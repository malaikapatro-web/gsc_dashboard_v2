import altair as alt
import pandas as pd
import streamlit as st

from db import export_csv, export_zip, run_query

# Streamlit Community Cloud apps have ~1 GB RAM, so keep in-memory downloads modest.
MAX_FULL_EXPORT_ROWS = 1_000_000
BATCH_SIZES = [50_000, 100_000, 250_000, 500_000]

TEXT_COLS = ("url", "query", "bucket")
PRIMARY = "#0F6E56"   # selected period
COMPARE = "#94A3B8"   # comparison period
SERIES = ("Selected period", "Comparison period")

CSS = """
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1600px;}
.gsc-hero {background: #0F6E56; color: #FFFFFF; border-radius: 14px; padding: 18px 26px; margin-bottom: 14px;}
.gsc-hero-title {font-size: 1.7rem; font-weight: 700; line-height: 1.25;}
.gsc-hero-sub {font-size: 0.92rem; opacity: 0.88; margin-top: 4px;}
div[data-testid="stMetricLabel"] p {font-size: 0.8rem; font-weight: 600; letter-spacing: 0.04em;
    text-transform: uppercase; color: #5B6B7B;}
div[data-testid="stMetricValue"] {font-size: 2rem; font-weight: 700; color: #0F3D33;}
h2, h3 {font-weight: 650;}
section[data-testid="stSidebar"] h2 {font-size: 1.1rem;}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def page_header(title: str, subtitle: str) -> None:
    st.markdown(f'<div class="gsc-hero"><div class="gsc-hero-title">{title}</div>'
                f'<div class="gsc-hero-sub">{subtitle}</div></div>', unsafe_allow_html=True)


def length_note(start, end, cs, ce) -> None:
    """Warn when the two periods have different lengths, because totals are then not like-for-like."""
    a, b = (end - start).days + 1, (ce - cs).days + 1
    if a != b:
        st.warning(f"The selected period is {a} days and the comparison period is {b} days, so total clicks and "
                   "impressions are not like-for-like. Pick periods of the same length for a fair comparison.")


def metric_cols(cmp: bool) -> str:
    """SELECT list of display metrics. With cmp the comparison period's numbers and the changes are added."""
    cur_pos = "DIV0NULL(sum_position, impressions) + 1"
    sql = (f"clicks, impressions, DIV0NULL(clicks, impressions) AS ctr, {cur_pos} AS avg_position")
    if cmp:
        cmp_pos = "DIV0NULL(c_sum_position, c_impressions) + 1"
        sql += (", c_clicks AS clicks_cmp, DIV0NULL(clicks - c_clicks, c_clicks) AS clicks_chg"
                ", c_impressions AS impressions_cmp, DIV0NULL(impressions - c_impressions, c_impressions) AS impressions_chg"
                f", DIV0NULL(c_clicks, c_impressions) AS ctr_cmp, {cmp_pos} AS avg_position_cmp"
                f", ({cur_pos}) - ({cmp_pos}) AS position_chg")
    return sql


def metric_config(cmp: bool) -> dict:
    cfg = {
        "clicks": st.column_config.NumberColumn("Clicks", format="%d"),
        "impressions": st.column_config.NumberColumn("Impressions", format="%d"),
        "ctr": st.column_config.NumberColumn("CTR", format="percent"),
        "avg_position": st.column_config.NumberColumn("Avg Position", format="%.2f"),
    }
    if cmp:
        cfg.update({
            "clicks_cmp": st.column_config.NumberColumn("Clicks (compare)", format="%d"),
            "clicks_chg": st.column_config.NumberColumn("Clicks change", format="percent",
                                                        help="(selected - comparison) / comparison"),
            "impressions_cmp": st.column_config.NumberColumn("Impressions (compare)", format="%d"),
            "impressions_chg": st.column_config.NumberColumn("Impressions change", format="percent",
                                                             help="(selected - comparison) / comparison"),
            "ctr_cmp": st.column_config.NumberColumn("CTR (compare)", format="percent"),
            "avg_position_cmp": st.column_config.NumberColumn("Avg Position (compare)", format="%.2f"),
            "position_chg": st.column_config.NumberColumn("Position change", format="%+.2f",
                                                          help="selected - comparison. Negative = ranking improved."),
        })
    return cfg


def compare_sorts(cmp: bool) -> dict:
    if not cmp:
        return {}
    return {"Clicks change (high to low)": "clicks_chg DESC NULLS LAST",
            "Impressions change (high to low)": "impressions_chg DESC NULLS LAST"}


def trend_chart(cur, other, y, title, zero=True, height=300, cur_start=None, other_start=None) -> None:
    """Line chart of the selected period; with `other` the comparison period is overlaid by day of period."""
    fmt = ",.2f" if y == "avg_position" else ",d"
    yenc = alt.Y(f"{y}:Q", title=title, scale=alt.Scale(zero=zero), axis=alt.Axis(format=fmt))
    if other is None:
        chart = (alt.Chart(cur[["data_date", y]])
                 .mark_line(point=alt.OverlayMarkDef(color=PRIMARY, filled=True, size=36), color=PRIMARY,
                            strokeWidth=2)
                 .encode(x=alt.X("data_date:T", title="Date"), y=yenc,
                         tooltip=[alt.Tooltip("data_date:T", title="Date"), alt.Tooltip(f"{y}:Q", format=fmt)]))
    else:
        def tag(df, name, first_day):
            d = df[["data_date", y]].copy()
            d["series"] = name
            d["day"] = (pd.to_datetime(d["data_date"]) - pd.Timestamp(first_day)).dt.days + 1
            return d
        data = pd.concat([tag(cur, SERIES[0], cur_start), tag(other, SERIES[1], other_start)])
        chart = (alt.Chart(data)
                 .mark_line(point=True, strokeWidth=2)
                 .encode(x=alt.X("day:Q", title="Day of period", axis=alt.Axis(tickMinStep=1, format="d")),
                         y=yenc,
                         color=alt.Color("series:N", scale=alt.Scale(domain=list(SERIES), range=[PRIMARY, COMPARE]),
                                         legend=alt.Legend(orient="top", title=None)),
                         strokeDash=alt.StrokeDash("series:N", legend=None,
                                                   scale=alt.Scale(domain=list(SERIES), range=[[1, 0], [5, 4]])),
                         tooltip=[alt.Tooltip("series:N", title="Period"), alt.Tooltip("data_date:T", title="Date"),
                                  alt.Tooltip(f"{y}:Q", format=fmt)]))
    st.altair_chart(chart.properties(height=height), width="stretch")


def with_total_row(df, table, total_rows, cmp=False):
    """Append a bold TOTAL row that sums the whole filtered table (needs clicks, impressions, sum_position)."""
    sel = "SUM(clicks) AS c, SUM(impressions) AS i, SUM(sum_position) AS p"
    if cmp:
        sel += ", SUM(c_clicks) AS cc, SUM(c_impressions) AS ci, SUM(c_sum_position) AS cp"
    t = run_query(f"SELECT {sel} FROM {table}").iloc[0]
    num = {k: float(t[k] or 0) for k in t.index}
    clicks, impr, pos = num["c"], num["i"], num["p"]
    cc, ci, cp = num.get("cc", 0), num.get("ci", 0), num.get("cp", 0)
    avg = pos / impr + 1 if impr else None
    avg_cmp = cp / ci + 1 if ci else None
    text_cols = [c for c in df.columns if c in TEXT_COLS]
    values = {
        "clicks": clicks, "impressions": impr, "ctr": clicks / impr if impr else None, "avg_position": avg,
        "clicks_cmp": cc, "impressions_cmp": ci, "ctr_cmp": cc / ci if ci else None, "avg_position_cmp": avg_cmp,
        "clicks_chg": (clicks - cc) / cc if cc else None, "impressions_chg": (impr - ci) / ci if ci else None,
        "position_chg": avg - avg_cmp if avg is not None and avg_cmp is not None else None,
    }
    row = {}
    for c in df.columns:
        if c == text_cols[0]:
            row[c] = f"TOTAL ({total_rows:,} rows)"
        elif c in text_cols:
            row[c] = ""
        else:
            row[c] = values.get(c)
    shown = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    last = len(shown) - 1
    return shown.style.apply(lambda r: ["font-weight: bold" if r.name == last else ""] * len(r), axis=1)


@st.fragment  # table controls (sort, page, downloads) rerun only this table, not the KPIs and charts around it
def paged_table(table, cols_sql, sort_options, tiebreak, col_config, key, export_name, height=520, cmp=False):
    """Server-side paged table: every row is reachable, and 'Prepare full download' exports all of them."""
    total = int(run_query(f"SELECT COUNT(*) AS n FROM {table}")["n"].iloc[0])
    scope = table.rsplit("_", 1)[-1]  # filter hash: resets the page number when filters change

    c1, c2, c3 = st.columns([2, 1, 1])
    sort = c1.selectbox("Sort by", list(sort_options), key=f"{key}_sort")
    size = c2.selectbox("Rows per page", [500, 1000, 5000, 10000], index=1, key=f"{key}_size")
    pages = max(1, -(-total // size))
    page = c3.number_input(f"Page (of {pages:,})", 1, pages, 1, key=f"{key}_page_{scope}")

    order = f"ORDER BY {sort_options[sort]}, {tiebreak}"
    df = run_query(f"SELECT {cols_sql} FROM {table} {order} LIMIT {int(size)} OFFSET {(page - 1) * size}")
    for c in df.columns:
        if c not in TEXT_COLS:
            df[c] = pd.to_numeric(df[c])

    first, last = (page - 1) * size + 1, min(page * size, total)
    st.caption(f"{total:,} rows match the filters. Showing {first:,} to {last:,}. "
               "The TOTAL row at the bottom sums every matching row, not just this page.")
    st.dataframe(with_total_row(df, table, total, cmp), width="stretch", height=height, hide_index=True,
                 column_config=col_config)

    with st.expander("Download in batches"):
        b1, b2 = st.columns(2)
        bsize = b1.selectbox("Batch size (rows)", BATCH_SIZES, index=1,
                             format_func=lambda n: f"{n:,}", key=f"{key}_bsize")
        n_batches = max(1, -(-total // bsize))
        labels = [f"Batch {i + 1} of {n_batches}: rows {i * bsize + 1:,} to {min((i + 1) * bsize, total):,}"
                  for i in range(n_batches)]
        pick = b2.selectbox("Batch", range(n_batches), format_func=lambda i: labels[i],
                            key=f"{key}_bpick_{scope}_{bsize}")
        st.caption(f"Batches follow the current sort ({sort}), so the same batch number always gives the same rows.")
        bkey = f"batch_{key}"
        sig = (table, sort, bsize, pick)
        if st.button(f"Prepare {labels[pick].split(':')[0]}", key=f"{key}_bprep"):
            with st.spinner("Preparing batch..."):
                sql = f"SELECT {cols_sql} FROM {table} {order} LIMIT {bsize} OFFSET {pick * bsize}"
                st.session_state[bkey] = (sig, export_csv(sql))
        got = st.session_state.get(bkey)
        if got and got[0] == sig:
            data, n = got[1]
            st.download_button(
                f"Download batch {pick + 1} ({n:,} rows, CSV)", data, f"{export_name}_batch{pick + 1:03d}.csv",
                "text/csv", key=f"{key}_bdl", on_click="ignore",
            )

    if total > MAX_FULL_EXPORT_ROWS:
        st.caption(
            f"A single-file full download is limited to {MAX_FULL_EXPORT_ROWS:,} rows to protect the hosted app's "
            f"memory. This table has {total:,} rows: use 'Download in batches' above to get every row."
        )
        return
    ekey = f"export_{key}"
    if st.button(f"Prepare full download (all {total:,} rows)", key=f"{key}_prep"):
        with st.spinner(f"Exporting {total:,} rows..."):
            st.session_state[ekey] = (table, export_zip(f"SELECT {cols_sql} FROM {table} {order}", export_name))
    ready = st.session_state.get(ekey)
    if ready and ready[0] == table:
        data, n, parts = ready[1]
        note = f" in {parts} files (Excel holds max 1,048,576 rows per sheet)" if parts > 1 else ""
        st.download_button(
            f"Download all {n:,} rows (.zip){note}", data, f"{export_name}.zip", "application/zip",
            key=f"{key}_dl", on_click="ignore",
        )
