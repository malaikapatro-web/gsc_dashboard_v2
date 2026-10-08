import streamlit as st

from db import AC_EXPR, DETAIL_TABLE, GB_EXPR, in_clause, run_query


def render_global_filters() -> None:
    """Sidebar filters shared by every page. Rendered from app.py on each run so their state follows you
    from page to page (a widget keeps its value only if it is drawn on every run)."""
    bounds = run_query(f"SELECT MIN(data_date) AS mn, MAX(data_date) AS mx FROM {DETAIL_TABLE}").iloc[0]
    min_d, max_d = bounds["mn"], bounds["mx"]
    opts = run_query(
        f"SELECT DISTINCT bucket, {AC_EXPR} AS ac, {GB_EXPR} AS gb FROM {DETAIL_TABLE}"
    )
    with st.sidebar:
        st.header("Filters")
        st.date_input("Date", (min_d, max_d), min_value=min_d, max_value=max_d, key="g_date")
        st.multiselect("Bucket", sorted(opts["bucket"].dropna().unique()), placeholder="All buckets", key="g_bucket")
        st.multiselect("Acute / Chronic", sorted(opts["ac"].unique()), placeholder="All", key="g_ac")
        st.multiselect("Generic / Branded medicines", sorted(opts["gb"].unique()), placeholder="All", key="g_gb")
        st.text_input("URL contains", placeholder="e.g. paracetamol", key="g_url")
        st.caption(f"Data available {min_d} to {max_d}")


def date_range():
    """(start, end) from the global date filter, or None while the viewer is still picking the end date."""
    d = st.session_state.get("g_date")
    return tuple(d) if d and len(d) == 2 else None


def global_where(params: dict, p: str = "") -> str:
    """AND-clauses for the global filters. p is a table alias prefix such as 'd.'; params is filled in place."""
    ss, sql = st.session_state, ""
    if ss.get("g_bucket"):
        sql += " AND " + in_clause(f"{p}bucket", ss["g_bucket"], params, "gb_")
    if ss.get("g_ac"):
        sql += " AND " + in_clause(AC_EXPR, ss["g_ac"], params, "ga_")
    if ss.get("g_gb"):
        sql += " AND " + in_clause(GB_EXPR, ss["g_gb"], params, "gg_")
    if (ss.get("g_url") or "").strip():
        params["g_url"] = f"%{ss['g_url'].strip()}%"
        sql += f" AND {p}url ILIKE %(g_url)s"
    return sql
