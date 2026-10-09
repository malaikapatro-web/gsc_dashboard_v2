import pandas as pd
import streamlit as st

from db import DETAIL_TABLE, GB_EXPR, materialize, run_query
from filters import compare_range, date_range, date_where, global_where, period_sums
from ui import compare_sorts, length_note, metric_cols, metric_config, page_header, paged_table, trend_chart

rng = date_range()
if rng is None:
    st.info("Pick an end date, then press Apply filters.")
    st.stop()
start, end = rng

cmp_rng = compare_range()
cmp = cmp_rng is not None
cs, ce = cmp_rng if cmp else (None, None)

page_header("GSC SEO Dashboard: Query performance",
            f"TrueMeds Growth Marketing &middot; Google Search Console impressions and clicks &middot; "
            f"{start} to {end}" + (f" vs {cs} to {ce}" if cmp else ""))

if cmp:
    length_note(start, end, cs, ce)

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


def sum_cols() -> str:
    cols = "SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(sum_position) AS sum_position"
    return cols + (", SUM(c_clicks) AS c_clicks, SUM(c_impressions) AS c_impressions, "
                   "SUM(c_sum_position) AS c_sum_position" if cmp else "")


def plain_cols() -> str:
    cols = "clicks, impressions, sum_position"
    return cols + (", c_clicks, c_impressions, c_sum_position" if cmp else "")


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

# Base: one row per (query, URL). bucket and brand follow from the URL / query text. Anonymized queries (no text
# in GSC) are kept as one labelled row per URL. With a comparison period both periods are summed in one pass.
bp = {"s": start, "e": end, **({"cs": cs, "ce": ce} if cmp else {})}
base = materialize(
    "QBASE",
    f"""SELECT {QUERY_EXPR} AS query, d.url AS url, d.bucket AS bucket, d.brand_non_brand AS brand_non_brand,
               {period_sums(cmp, "d.")}
        FROM {SOURCE}
        WHERE {date_where(cmp, "d.")} {where(bp)}
        GROUP BY 1, 2, 3, 4""",
    bp,
)

CFG = {
    "query": st.column_config.TextColumn("Query", width="large"),
    "url": st.column_config.LinkColumn("URL", width="large"),
    "bucket": "Bucket",
    **metric_config(cmp),
}
SORTS = {
    "Clicks (high to low)": "clicks DESC",
    "Impressions (high to low)": "impressions DESC",
    "CTR (high to low)": "ctr DESC NULLS LAST",
    "Avg Position (best first)": "avg_position ASC NULLS LAST",
    "Query (A to Z)": "query ASC",
    "URL (A to Z)": "url ASC",
    **compare_sorts(cmp),
}
METRICS = metric_cols(cmp)


def view_table(prefix: str, condition: str) -> str:
    """Rows of `base` that match condition, one per (query, URL)."""
    if group_tail:  # long-tail labels can repeat per URL (brand and non-brand), so add them up
        return materialize(
            prefix, f"SELECT query, url, {sum_cols()} FROM {base} WHERE {condition} GROUP BY query, url")
    return materialize(prefix, f"SELECT query, url, {plain_cols()} FROM {base} WHERE {condition}")


# BRAND / NON-BRAND = does the search query mention TrueMeds. The medicine type (Generic / Branded) is the
# global filter in the left sidebar, so it is not repeated here.
with st.container(border=True):
    view = st.radio("Table", ["Query Wise Bucketing", "BRAND", "NON-BRAND"], horizontal=True, key="q_view")
    st.subheader(view)
    if view == "Query Wise Bucketing":
        cond = "LEN(TRIM(query)) >= 4" if hide_short else "TRUE"
        if group_tail:
            tbl = materialize(
                "QBUCKET",
                f"SELECT query, url, bucket, {sum_cols()} FROM {base} WHERE {cond} GROUP BY query, url, bucket",
            )
        elif hide_short:
            tbl = materialize("QSHORT", f"SELECT * FROM {base} WHERE {cond}")
        else:
            tbl = base
        paged_table(
            tbl,
            cols_sql=f"query, url, bucket, {METRICS}",
            sort_options=SORTS, tiebreak="query, url", col_config=CFG,
            key="qb", export_name="gsc_queries_bucketing", cmp=cmp,
        )
    else:
        cond = f"brand_non_brand = '{view}'"
        paged_table(
            view_table("QVIEW", cond),
            cols_sql=f"query, url, {METRICS}",
            sort_options=SORTS, tiebreak="query, url", col_config=CFG,
            key=f"qbr_{view}", export_name="gsc_queries_" + view.split()[0].lower(), cmp=cmp,
        )

# Trends always come straight from the detail table, with the same filters as the tables above.
dp = {"s": start, "e": end, **({"cs": cs, "ce": ce} if cmp else {})}
daily = run_query(
    f"""SELECT data_date, brand_non_brand, {GB_EXPR} AS gb, SUM(clicks) AS clicks,
               SUM(impressions) AS impressions, SUM(sum_position) AS sp
        FROM {DETAIL_TABLE}
        WHERE {date_where(cmp)} {where(dp, "")}
        GROUP BY data_date, brand_non_brand, {GB_EXPR} ORDER BY data_date""",
    dp,
)
daily["data_date"] = pd.to_datetime(daily["data_date"])
for c in ("clicks", "impressions", "sp"):
    daily[c] = pd.to_numeric(daily[c])


def series(col, label):
    """Daily totals for one group, as (selected period, comparison period or None)."""
    d = daily[daily[col] == label]
    day = d["data_date"].dt.date

    def agg(mask):
        g = d[mask].groupby("data_date", as_index=False)[["clicks", "impressions", "sp"]].sum()
        g["avg_position"] = g["sp"] / g["impressions"].where(g["impressions"] > 0) + 1  # same formula as the tables
        return g

    return agg((day >= start) & (day <= end)), (agg((day >= cs) & (day <= ce)) if cmp else None)


def trend_section(heading, col, left, right):
    """left/right are (value in `col`, label shown in chart titles)."""
    with st.container(border=True):
        st.header(heading)
        for column, (value, name) in zip(st.columns(2), (left, right)):
            cur, other = series(col, value)
            with column:
                for y, title, zero, label in (("clicks", "Clicks", True, "Clicks"),
                                              ("impressions", "Impressions", True, "Impressions"),
                                              ("avg_position", "Avg Position", False, "Avg Position")):
                    st.subheader(f"{label} wrt Date ({name})")
                    trend_chart(cur, other, y, title, zero=zero, cur_start=start, other_start=cs)


trend_section("Brand vs Non-Brand queries", "brand_non_brand", ("BRAND", "Branded"), ("NON-BRAND", "Non-Branded"))
trend_section("Generic vs Branded medicines", "gb", ("GENERIC", "Generic"), ("BRANDED", "Branded medicines"))
