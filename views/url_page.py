from datetime import timedelta

import pandas as pd
import streamlit as st

from db import DETAIL_TABLE, fmt_int, materialize, pct_delta, run_query
from filters import compare_range, date_range, date_where, global_where, period_sums
from ui import compare_sorts, length_note, metric_cols, metric_config, page_header, paged_table, trend_chart

rng = date_range()
if rng is None:
    st.info("Pick an end date, then press Apply filters.")
    st.stop()
start, end = rng
span = (end - start).days + 1

cmp_rng = compare_range()
cmp = cmp_rng is not None
if cmp:
    cs, ce = cmp_rng
    compare_label = f"selected comparison period ({cs} to {ce})"
else:  # no comparison chosen: keep the automatic change vs the previous period of the same length
    cs, ce = start - timedelta(days=span), start - timedelta(days=1)
    compare_label = f"previous {span} day(s) ({cs} to {ce})"

page_header("GSC SEO Dashboard: URL performance",
            f"TrueMeds Growth Marketing &middot; Google Search Console impressions and clicks &middot; "
            f"{start} to {end}" + (f" vs {cs} to {ce}" if cmp else ""))

# One daily query covers the KPI cards, the change vs the comparison period and the trend charts. The date
# predicate is an OR of the two ranges, so Snowflake reads only those days, in a single pass.
params = {"s": start, "e": end, "cs": cs, "ce": ce}
daily = run_query(
    f"""SELECT data_date, SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(sum_position) AS sp
        FROM {DETAIL_TABLE}
        WHERE {date_where(True)} {global_where(params)}
        GROUP BY data_date ORDER BY data_date""",
    params,
)
daily["data_date"] = pd.to_datetime(daily["data_date"])
for col in ("clicks", "impressions", "sp"):
    daily[col] = pd.to_numeric(daily[col])
daily["avg_position"] = daily["sp"] / daily["impressions"].where(daily["impressions"] > 0) + 1
day = daily["data_date"].dt.date
cur = daily[(day >= start) & (day <= end)]
other = daily[(day >= cs) & (day <= ce)]


def totals(df):
    c, i = df["clicks"].sum(), df["impressions"].sum()
    return c, i, (c / i if i else None), (df["sp"].sum() / i + 1 if i else None)


c, i, ctr, pos = totals(cur)
pc, pi, pctr, ppos = totals(other)


def delta(a, b):
    d = pct_delta(a, b)
    return None if d is None else f"{d:+.1%}"


k1, k2, k3, k4 = st.columns(4)
k1.metric("Clicks", fmt_int(c), delta(c, pc), border=True)
k2.metric("Impressions", fmt_int(i), delta(i, pi), border=True)
k3.metric("CTR %", "-" if ctr is None else f"{ctr:.2%}", delta(ctr, pctr), border=True)
k4.metric("Avg Position", "-" if pos is None else f"{pos:.2f}", delta(pos, ppos), delta_color="inverse", border=True)
st.caption(f"Change vs {compare_label}")
if cmp:
    length_note(start, end, cs, ce)

with st.container(border=True):
    st.subheader("URL Level Data Table")
    tparams = {"s": start, "e": end, **({"cs": cs, "ce": ce} if cmp else {})}
    url_tbl = materialize(
        "URL",
        f"""SELECT url, bucket, {period_sums(cmp)}
            FROM {DETAIL_TABLE}
            WHERE {date_where(cmp)} {global_where(tparams)}
            GROUP BY url, bucket""",
        tparams,
    )
    paged_table(
        url_tbl,
        cols_sql=f"url, bucket, {metric_cols(cmp)}",
        sort_options={
            "Clicks (high to low)": "clicks DESC",
            "Impressions (high to low)": "impressions DESC",
            "CTR (high to low)": "ctr DESC NULLS LAST",
            "Avg Position (best first)": "avg_position ASC NULLS LAST",
            "URL (A to Z)": "url ASC",
            **compare_sorts(cmp),
        },
        tiebreak="url",
        col_config={"url": st.column_config.LinkColumn("URL", width="large"), "bucket": "Bucket",
                    **metric_config(cmp)},
        key="url",
        export_name="gsc_urls",
        height=560,
        cmp=cmp,
    )

for heading, y, title, zero in (("Clicks wrt Date", "clicks", "Clicks", True),
                                ("Impressions wrt Date", "impressions", "Impressions", True),
                                ("Avg Position wrt Date", "avg_position", "Avg Position", False)):
    with st.container(border=True):
        st.subheader(heading)
        trend_chart(cur, other if cmp else None, y, title, zero=zero, height=320, cur_start=start, other_start=cs)
