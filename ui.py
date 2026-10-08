import pandas as pd
import streamlit as st

from db import export_csv, export_zip, run_query

# Streamlit Community Cloud apps have ~1 GB RAM, so keep in-memory downloads modest.
MAX_FULL_EXPORT_ROWS = 1_000_000
BATCH_SIZES = [50_000, 100_000, 250_000, 500_000]


TEXT_COLS = ("url", "query", "bucket")


def with_total_row(df, table, total_rows):
    """Append a bold TOTAL row that sums the whole filtered table (needs clicks, impressions, sum_position)."""
    t = run_query(f"SELECT SUM(clicks) AS c, SUM(impressions) AS i, SUM(sum_position) AS p FROM {table}").iloc[0]
    clicks, impr, pos = (float(t[k] or 0) for k in ("c", "i", "p"))
    text_cols = [c for c in df.columns if c in TEXT_COLS]
    row = {}
    for c in df.columns:
        if c == text_cols[0]:
            row[c] = f"TOTAL ({total_rows:,} rows)"
        elif c in text_cols:
            row[c] = ""
        elif c == "clicks":
            row[c] = clicks
        elif c == "impressions":
            row[c] = impr
        elif c == "ctr":
            row[c] = clicks / impr if impr else None
        elif c == "avg_position":
            row[c] = pos / impr + 1 if impr else None
    shown = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    last = len(shown) - 1
    return shown.style.apply(lambda r: ["font-weight: bold" if r.name == last else ""] * len(r), axis=1)


def paged_table(table, cols_sql, sort_options, tiebreak, col_config, key, export_name, height=520):
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
        if c not in ("url", "query", "bucket"):
            df[c] = pd.to_numeric(df[c])

    first, last = (page - 1) * size + 1, min(page * size, total)
    st.caption(f"{total:,} rows match the filters. Showing {first:,} to {last:,}. "
               "The TOTAL row at the bottom sums every matching row, not just this page.")
    st.dataframe(with_total_row(df, table, total), width="stretch", height=height, hide_index=True,
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
                "text/csv", key=f"{key}_bdl",
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
            key=f"{key}_dl",
        )
