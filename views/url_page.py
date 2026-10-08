from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

from db import DETAIL_TABLE, fmt_int, materialize, pct_delta, run_query
from filters import date_range, global_where
from ui import paged_table

st.title("GSC SEO Dashboard - URL")

rng = date_range()
if rng is None:
    st.info("Pick an end date.")
    st.stop()
start, end = rng
span = (end - start).days + 1
prev_start, prev_end = start - timedelta(days=span), start - timedelta(days=1)

# One daily query covers the KPI cards, the previous-period deltas and the trend charts.
params = {"ps": prev_start, "e": end}
daily = run_query(
    f"""SELECT data_date, SUM(clicks) AS clicks, SUM(impressions) AS impressions, SUM(sum_position) AS sp
        FROM {DETAIL_TABLE}
        WHERE data_date BETWEEN %(ps)s AND %(e)s {global_where(params)}
        GROUP BY data_date ORDER BY data_date""",
    params,
)
daily["data_date"] = pd.to_datetime(daily["data_date"])
for col in ("clicks", "impressions", "sp"):
    daily[col] = pd.to_numeric(daily[col])
daily["avg_position"] = daily["sp"] / daily["impressions"].where(daily["impressions"] > 0) + 1
cur = daily[daily["data_date"].dt.date >= start]
prv = daily[daily["data_date"].dt.date <= prev_end]


def totals(df):
    c, i = df["clicks"].sum(), df["impressions"].sum()
    return c, i, (c / i if i else None), (df["sp"].sum() / i + 1 if i else None)


c, i, ctr, pos = totals(cur)
pc, pi, pctr, ppos = totals(prv)


def delta(a, b):
    d = pct_delta(a, b)
    return None if d is None else f"{d:+.1%}"


k1, k2, k3, k4 = st.columns(4)
k1.metric("Clicks", fmt_int(c), delta(c, pc))
k2.metric("Impressions", fmt_int(i), delta(i, pi))
k3.metric("CTR %", "-" if ctr is None else f"{ctr:.2%}", delta(ctr, pctr))
k4.metric("Avg Position", "-" if pos is None else f"{pos:.2f}", delta(pos, ppos), delta_color="inverse")
st.caption(f"Change vs previous {span} day(s): {prev_start} to {prev_end}")

st.subheader("URL Level Data Table")
tparams = {"s": start, "e": end}
url_tbl = materialize(
    "URL",
    f"""SELECT url, bucket, SUM(clicks) AS clicks, SUM(impressions) AS impressions,
               SUM(sum_position) AS sum_position
        FROM {DETAIL_TABLE}
        WHERE data_date BETWEEN %(s)s AND %(e)s {global_where(tparams)}
        GROUP BY url, bucket""",
    tparams,
)
paged_table(
    url_tbl,
    cols_sql="""url, bucket, clicks, impressions,
                DIV0NULL(clicks, impressions) AS ctr,
                DIV0NULL(sum_position, impressions) + 1 AS avg_position""",
    sort_options={
        "Clicks (high to low)": "clicks DESC",
        "Impressions (high to low)": "impressions DESC",
        "CTR (high to low)": "ctr DESC NULLS LAST",
        "Avg Position (best first)": "avg_position ASC NULLS LAST",
        "URL (A to Z)": "url ASC",
    },
    tiebreak="url",
    col_config={
        "url": st.column_config.LinkColumn("URL", width="large"),
        "bucket": "Bucket",
        "clicks": st.column_config.NumberColumn("Clicks", format="%d"),
        "impressions": st.column_config.NumberColumn("Impressions", format="%d"),
        "ctr": st.column_config.NumberColumn("CTR", format="percent"),
        "avg_position": st.column_config.NumberColumn("Avg Position", format="%.2f"),
    },
    key="url",
    export_name="gsc_urls",
    height=560,
)


def trend(df, y, title, zero=True):
    fmt = ",.2f" if y == "avg_position" else ",d"
    chart = (
        alt.Chart(df)
        .mark_line(point=True)
        .encode(
            x=alt.X("data_date:T", title="Date"),
            y=alt.Y(f"{y}:Q", title=title, scale=alt.Scale(zero=zero), axis=alt.Axis(format=fmt)),
            tooltip=["data_date:T", alt.Tooltip(f"{y}:Q", format=fmt)],
        )
        .properties(height=320)
    )
    st.altair_chart(chart, width="stretch")


st.subheader("Clicks wrt Date")
trend(cur, "clicks", "Clicks")
st.subheader("Impressions wrt Date")
trend(cur, "impressions", "Impressions")
st.subheader("Avg Position wrt Date")
trend(cur, "avg_position", "Avg Position", zero=False)
