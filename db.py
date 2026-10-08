import csv
import hashlib
import io
import tempfile
import time
import zipfile
from pathlib import Path

import pandas as pd
import snowflake.connector
import streamlit as st
from cryptography.hazmat.primitives import serialization

SCHEMA = "STG_SILVER_DB.ANALYTICS"
# Every page reads the detail table directly (no pre-aggregated GSC_URL_DAILY_AGG / GSC_QUERY_DAILY_AGG).
DETAIL_TABLE = f"{SCHEMA}.GSC_DB_TABLE"

# acute_chronic has mixed casing in the source (ACUTE/acute/Acute) and NULLs
AC_EXPR = "COALESCE(UPPER(TRIM(acute_chronic)), '(BLANK)')"
# generic_branded comes from MEDICINE_MASTER (Branded / Generic); NULL for pages that are not a medicine
GB_EXPR = "COALESCE(UPPER(TRIM(generic_branded)), '(BLANK)')"


def _config() -> dict:
    """Credentials come only from Streamlit secrets ([snowflake] table); nothing is stored in the repo."""
    return dict(st.secrets["snowflake"])


def _private_key_der(cfg: dict) -> bytes:
    pem = cfg.get("private_key")  # Community Cloud: PEM text pasted into Secrets
    data = pem.encode() if pem else Path(cfg["private_key_path"]).read_bytes()  # local: key file path
    pw = cfg.get("private_key_passphrase")
    key = serialization.load_pem_private_key(data, password=pw.encode() if pw else None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@st.cache_resource
def _connection():
    cfg = _config()
    return snowflake.connector.connect(
        account=cfg["account"],
        user=cfg["user"],
        private_key=_private_key_der(cfg),
        role=cfg.get("role"),
        warehouse=cfg.get("warehouse"),
        client_session_keep_alive=True,
    )


def require_config() -> None:
    """Call once at startup: fail with a readable message (not a stack trace) if secrets or login are wrong."""
    try:
        cfg = _config()
        cfg["account"], cfg["user"]
        if not (cfg.get("private_key") or cfg.get("private_key_path")):
            raise KeyError("private_key")
    except Exception:
        st.error("Snowflake credentials are not configured. Add a [snowflake] section with account, user and "
                 "private_key to the app's Secrets (see .streamlit/secrets.toml.example and the README).")
        st.stop()
    try:
        _connection()
    except Exception as e:  # failed connects are not cached, so a reload retries
        st.error(f"Could not connect to Snowflake: {e}\n\nCheck the key pair is registered on the user "
                 "(RSA_PUBLIC_KEY) and the Secrets values are correct, then reload.")
        st.stop()


def _execute(sql: str, params: dict) -> pd.DataFrame:
    cur = _connection().cursor()
    try:
        cur.execute(sql, params)
        cols = [c[0].lower() for c in cur.description or []]
        return pd.DataFrame(cur.fetchall(), columns=cols or None)
    finally:
        cur.close()


@st.cache_data(ttl=6 * 3600, max_entries=200, show_spinner=False)  # max_entries keeps memory bounded on Community Cloud
def run_query(sql: str, params: dict | None = None) -> pd.DataFrame:
    params = params or {}
    try:
        return _execute(sql, params)
    except snowflake.connector.errors.Error:
        _connection.clear()  # session may have expired; reconnect once
        return _execute(sql, params)


SCRATCH = "MARKETING_BRONZE_DB.STAGING"  # default; override with scratch_schema in secrets. Temp tables only.
PART_ROWS = 1_000_000  # Excel's row limit is 1,048,576, so exports are split into parts
MAX_TEMP_TABLES = 12


@st.cache_resource
def _scratch_registry() -> dict:
    return {"conn": None, "names": []}


def materialize(prefix: str, select_sql: str, params: dict | None = None) -> str:
    """Create (once) a session-temporary Snowflake table holding select_sql; return its name."""
    params = params or {}
    fresh = int(time.time() // (6 * 3600))  # rebuild after the daily refresh window
    key = hashlib.md5(f"{select_sql}{sorted(params.items())}{fresh}".encode()).hexdigest()[:12]
    name = f"{_config().get('scratch_schema', SCRATCH)}.TMP_{prefix}_{key}"
    reg, conn = _scratch_registry(), _connection()
    if reg["conn"] is not conn:  # new Snowflake session -> old temp tables are gone
        reg["conn"], reg["names"] = conn, []
    if name not in reg["names"]:
        _execute(f"CREATE TEMPORARY TABLE IF NOT EXISTS {name} AS {select_sql}", params)
        reg["names"].append(name)
        while len(reg["names"]) > MAX_TEMP_TABLES:
            _execute(f"DROP TABLE IF EXISTS {reg['names'].pop(0)}", {})
    return name


def export_zip(select_sql: str, base_name: str) -> tuple[bytes, int, int]:
    """Stream every row of select_sql into a zip of CSV parts (<= PART_ROWS rows each)."""
    cur = _connection().cursor()
    try:
        cur.execute(select_sql)
        cols = [c[0].lower() for c in cur.description]
        tmp = tempfile.TemporaryFile()
        total, parts, in_part, text, writer = 0, 0, PART_ROWS, None, None
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            while True:
                rows = cur.fetchmany(50_000)
                if not rows and parts:
                    break
                for r in rows:
                    if in_part >= PART_ROWS:
                        if text:
                            text.close()
                        parts += 1
                        in_part = 0
                        text = io.TextIOWrapper(
                            zf.open(f"{base_name}_part{parts}.csv", "w", force_zip64=True),
                            encoding="utf-8-sig", newline="",
                        )
                        writer = csv.writer(text)
                        writer.writerow(cols)
                    writer.writerow(r)
                    in_part += 1
                    total += 1
                if not rows:  # empty result: still emit a header-only file
                    parts += 1
                    zf.writestr(f"{base_name}_part1.csv", ",".join(cols) + "\n")
                    break
            if text:
                text.close()
        tmp.seek(0)
        return tmp.read(), total, parts
    finally:
        cur.close()


def export_csv(select_sql: str) -> tuple[bytes, int]:
    """Run select_sql (already LIMIT/OFFSET-ed to one batch) and return a single CSV (Excel-friendly BOM)."""
    cur = _connection().cursor()
    try:
        cur.execute(select_sql)
        cols = [c[0].lower() for c in cur.description]
        raw = io.BytesIO()
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")  # writes straight to bytes: one copy
        writer = csv.writer(text)
        writer.writerow(cols)
        n = 0
        while rows := cur.fetchmany(50_000):
            writer.writerows(rows)
            n += len(rows)
        text.flush()
        return raw.getvalue(), n
    finally:
        cur.close()


def in_clause(expr: str, values, params: dict, key: str) -> str:
    placeholders = []
    for i, v in enumerate(values):
        params[f"{key}{i}"] = v
        placeholders.append(f"%({key}{i})s")
    return f"{expr} IN ({', '.join(placeholders)})"


def fmt_int(n) -> str:
    """Absolute number with thousands separators (never K / M / B)."""
    if n is None or pd.isna(n):
        return "-"
    return f"{float(n):,.0f}"


def pct_delta(cur, prev):
    if prev in (None, 0) or pd.isna(prev) or cur is None or pd.isna(cur):
        return None
    return (cur - prev) / prev
