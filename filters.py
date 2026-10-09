from datetime import timedelta

import streamlit as st

from db import AC_EXPR, DETAIL_TABLE, GB_EXPR, in_clause, run_query


def _default_compare(min_d, max_d):
    """Only a starting suggestion for the comparison period: the first week of data."""
    return (min_d, min(min_d + timedelta(days=6), max_d))


def render_global_filters() -> None:
    """Sidebar filters shared by every page. Rendered from app.py on each run so their state follows you from
    page to page. They sit in a form: nothing is queried until you press Apply, so picking several filters
    costs one refresh instead of one per click."""
    bounds = run_query(f"SELECT MIN(data_date) AS mn, MAX(data_date) AS mx FROM {DETAIL_TABLE}").iloc[0]
    min_d, max_d = bounds["mn"], bounds["mx"]
    opts = run_query(f"SELECT DISTINCT bucket, {AC_EXPR} AS ac, {GB_EXPR} AS gb FROM {DETAIL_TABLE}")
    with st.sidebar:
        st.header("Filters")
        with st.form("global_filters", border=False):
            st.date_input("Date", (min_d, max_d), min_value=min_d, max_value=max_d, key="g_date")
            st.checkbox("Compare with another period", key="g_cmp")
            st.date_input("Comparison period", _default_compare(min_d, max_d), min_value=min_d, max_value=max_d,
                          key="g_cmp_date", help="Used only when 'Compare with another period' is ticked.")
            st.multiselect("Bucket", sorted(opts["bucket"].dropna().unique()), placeholder="All buckets",
                           key="g_bucket")
            st.multiselect("Acute / Chronic", sorted(opts["ac"].unique()), placeholder="All", key="g_ac")
            st.multiselect("Generic / Branded medicines", sorted(opts["gb"].unique()), placeholder="All",
                           key="g_gb")
            st.text_input("URL contains", placeholder="e.g. paracetamol", key="g_url")
            st.form_submit_button("Apply filters", type="primary", width="stretch")
        st.caption(f"Data available {min_d} to {max_d}")


def date_range():
    """(start, end) from the global date filter, or None while the viewer is still picking the end date."""
    d = st.session_state.get("g_date")
    return tuple(d) if d and len(d) == 2 else None


def compare_range():
    """(start, end) of the comparison period, or None when comparison is off or its range is incomplete."""
    if not st.session_state.get("g_cmp"):
        return None
    d = st.session_state.get("g_cmp_date")
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


def date_where(cmp: bool, p: str = "") -> str:
    """Date predicate. With a comparison period it is an OR of the two ranges, so Snowflake reads only those
    days (not the gap between them). Needs params s, e (and cs, ce when cmp)."""
    cur = f"{p}data_date BETWEEN %(s)s AND %(e)s"
    return f"({cur} OR {p}data_date BETWEEN %(cs)s AND %(ce)s)" if cmp else cur


def period_sums(cmp: bool, p: str = "") -> str:
    """SUM expressions for the selected period and, with cmp, the comparison period (columns c_*), computed in
    the same single pass over the table."""
    cols = ("clicks", "impressions", "sum_position")
    if not cmp:
        return ", ".join(f"SUM({p}{c}) AS {c}" for c in cols)
    cur = f"{p}data_date BETWEEN %(s)s AND %(e)s"
    other = f"{p}data_date BETWEEN %(cs)s AND %(ce)s"
    return ", ".join(
        [f"SUM(IFF({cur}, {p}{c}, 0)) AS {c}" for c in cols] + [f"SUM(IFF({other}, {p}{c}, 0)) AS c_{c}" for c in cols]
    )
