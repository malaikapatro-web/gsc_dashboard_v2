import altair as alt
import pandas as pd
import streamlit as st

from db import DETAIL_TABLE, GB_EXPR, materialize, run_query
from filters import date_range, global_where
from ui import paged_table

st.title("GSC SEO Dashboard - Query")

rng = date_range()
if rng is None:
    st.info("Pick an end date.")
    st.stop()
start, end = rng

with st.sidebar:
    st.subheader("Query page filters")
    search = st.text_input("Query contains", placeholder="e.g. dolo 650", key="q_search")
    hide_short = st.checkbox("Hide queries shorter than 4 characters", value=False,
                             help="The original Power BI table hid these. Off here so no query is dropped.")
    group_tail = st.checkbox(
        "Group queries under 10 impressions as Long-tail", value=False, key="q_tail",
        help="Same rule as the Power BI report: a query with fewer than 10 total impressions across all loaded "
             "days is shown as 'Long-tail (low volume)'. Totals do not change.",
    )


def where(params: dict, p: str = "d.") -> str:
    """Global filters + the Query page's own search. p is a table alias prefix."""
    sql = global_where(params, p)
    if search:
        params["q"] = f"%{search.strip()}%"
        sql += f" AND {p}query ILIKE %(q)s"
    return sql


TAIL_LABEL = "Long-tail (low volume)"
if group_tail:
    # Same rule as the pipeline: total impressions per query over every loaded day, threshold 10.
    qvol = materialize(
        "QVOL",
        f"""SELECT query AS vq, SUM(impressions) AS total_impressions
            FROM {DETAIL_TABLE} WHERE query IS NOT NULL GROUP BY query""",
    )
    QUERY_EXPR = (f"CASE WHEN d.query IS NULL THEN '(anonymized queries)' "
                  f"WHEN v.total_impressions >= 10 THEN d.query ELSE '{TAIL_LABEL}' END")
    SOURCE = f"{DETAIL_TABLE} d LEFT JOIN {qvol} v ON d.query = v.vq"
else:
    QUERY_EXPR = "COALESCE(d.query, '(anonymized queries)')"
    SOURCE = f"{DETAIL_TABLE} d"

# Base: one row per (query, URL). bucket, brand and medicine type follow from the URL / query text.
# Anonymized queries (no text in GSC) are kept as one labelled row per URL.
bp = {"s": start, "e": end}
base = materialize(
    "QBASE",
    f"""SELECT {QUERY_EXPR} AS query, d.url AS url, d.bucket AS bucket, d.brand_non_brand AS brand_non_brand,
               {GB_EXPR} AS gb, SUM(d.clicks) AS clicks, SUM(d.impressions) AS impressions,
               SUM(d.sum_position) AS sum_position
        FROM {SOURCE}
        WHERE d.data_date BETWEEN %(s)s AND %(e)s {where(bp)}
        GROUP BY 1, 2, 3, 4, 5""",
    bp,
)

CFG = {
    "query": st.column_config.TextColumn("Query", width="large"),
    "url": st.column_config.LinkColumn("URL", width="large"),
    "bucket": "Bucket",
    "clicks": st.column_config.NumberColumn("Clicks", format="%d"),
    "impressions": st.column_config.NumberColumn("Impressions", format="%d"),
    "ctr": st.column_config.NumberColumn("CTR", format="percent"),
    "avg_position": st.column_config.NumberColumn("Avg Position", format="%.2f"),
}
SORTS = {
    "Clicks (high to low)": "clicks DESC",
    "Impressions (high to low)": "impressions DESC",
    "CTR (high to low)": "ctr DESC NULLS LAST",
    "Avg Position (best first)": "avg_position ASC NULLS LAST",
    "Query (A to Z)": "query ASC",
    "URL (A to Z)": "url ASC",
}
METRICS = """clicks, impressions, DIV0NULL(clicks, impressions) AS ctr,
             DIV0NULL(sum_position, impressions) + 1 AS avg_position"""


def view_table(prefix: str, condition: str) -> str:
    """Rows of `base` that match condition, one per (query, URL)."""
    if group_tail:  # long-tail labels can repeat per URL (brand and non-brand), so add them up
        return materialize(
            prefix,
            f"""SELECT query, url, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
                       SUM(sum_position) AS sum_position
                FROM {base} WHERE {condition} GROUP BY query, url""",
        )
    return materialize(prefix, f"SELECT query, url, clicks, impressions, sum_position FROM {base} WHERE {condition}")


# BRAND / NON-BRAND = does the search query mention TrueMeds. Generic / Branded = the medicine type of the
# page that earned the impression (from MEDICINE_MASTER); "Other pages" are URLs with no medicine match.
GB_VIEWS = {"GENERIC medicines": "GENERIC", "BRANDED medicines": "BRANDED", "OTHER pages (no medicine)": "(BLANK)"}
view = st.radio("Table", ["Query Wise Bucketing", "BRAND", "NON-BRAND", *GB_VIEWS], horizontal=True, key="q_view")
st.subheader(view)

if view == "Query Wise Bucketing":
    cond = "LEN(TRIM(query)) >= 4" if hide_short else "TRUE"
    if group_tail:
        tbl = materialize(
            "QBUCKET",
            f"""SELECT query, url, bucket, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
                       SUM(sum_position) AS sum_position
                FROM {base} WHERE {cond} GROUP BY query, url, bucket""",
        )
    elif hide_short:
        tbl = materialize("QSHORT", f"SELECT * FROM {base} WHERE {cond}")
    else:
        tbl = base
    paged_table(
        tbl,
        cols_sql=f"query, url, bucket, {METRICS}",
        sort_options=SORTS, tiebreak="query, url", col_config=CFG,
        key="qb", export_name="gsc_queries_bucketing",
    )
else:
    cond = f"gb = '{GB_VIEWS[view]}'" if view in GB_VIEWS else f"brand_non_brand = '{view}'"
    paged_table(
        view_table("QVIEW", cond),
        cols_sql=f"query, url, {METRICS}",
        sort_options=SORTS, tiebreak="query, url", col_config=CFG,
        key=f"qbr_{view}", export_name="gsc_queries_" + view.split()[0].lower(),
    )

# Trends always come straight from the detail table, with the same filters as the tables above.
dp = {"s": start, "e": end}
daily = run_query(
    f"""SELECT data_date, brand_non_brand, {GB_EXPR} AS gb, SUM(clicks) AS clicks,
               SUM(impressions) AS impressions, SUM(sum_position) AS sp
        FROM {DETAIL_TABLE}
        WHERE data_date BETWEEN %(s)s AND %(e)s {where(dp, "")}
        GROUP BY data_date, brand_non_brand, {GB_EXPR} ORDER BY data_date""",
    dp,
)
daily["data_date"] = pd.to_datetime(daily["data_date"])
for c in ("clicks", "impressions", "sp"):
    daily[c] = pd.to_numeric(daily[c])


def series(col, label):
    d = daily[daily[col] == label].groupby("data_date", as_index=False)[["clicks", "impressions", "sp"]].sum()
    d["avg_position"] = d["sp"] / d["impressions"].where(d["impressions"] > 0) + 1  # same formula as the tables
    return d


def trend(d, y, title, zero=True):
    fmt = ",.2f" if y == "avg_position" else ",d"
    chart = (
        alt.Chart(d)
        .mark_line(point=True)
        .encode(
            x=alt.X("data_date:T", title="Date"),
            y=alt.Y(f"{y}:Q", title=title, scale=alt.Scale(zero=zero), axis=alt.Axis(format=fmt)),
            tooltip=["data_date:T", alt.Tooltip(f"{y}:Q", format=fmt)],
        )
        .properties(height=300)
    )
    st.altair_chart(chart, width="stretch")


def trend_section(heading, col, left, right):
    """left/right are (value in `col`, label shown in chart titles)."""
    st.header(heading)
    for column, (value, name) in zip(st.columns(2), (left, right)):
        d = series(col, value)
        with column:
            st.subheader(f"Clicks wrt Date ({name})")
            trend(d, "clicks", "Clicks")
            st.subheader(f"Impressions wrt Date ({name})")
            trend(d, "impressions", "Impressions")
            st.subheader(f"Avg Position wrt Date ({name})")
            trend(d, "avg_position", "Avg Position", zero=False)


trend_section("Brand vs Non-Brand queries", "brand_non_brand", ("BRAND", "Branded"), ("NON-BRAND", "Non-Branded"))
trend_section("Generic vs Branded medicines", "gb", ("GENERIC", "Generic"), ("BRANDED", "Branded medicines"))
