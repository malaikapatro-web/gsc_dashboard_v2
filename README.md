# GSC SEO Dashboard v2 (Streamlit, reads GSC_DB_TABLE only)

Streamlit rebuild of the "GSC SEO Dashboard" Power BI report. Every number comes straight from the detail table
`STG_SILVER_DB.ANALYTICS.GSC_DB_TABLE`; the pre-aggregated `GSC_URL_DAILY_AGG` / `GSC_QUERY_DAILY_AGG` are not used.

- **Global filters (both pages)**: Date, Bucket, Acute / Chronic, Generic / Branded medicines, URL contains.
- **URL page**: KPIs (absolute numbers), URL table, clicks / impressions / avg position trends.
- **Query page**: one row per query and URL with clicks, impressions, CTR and avg position. Tables: Query Wise
  Bucketing, BRAND, NON-BRAND, GENERIC, BRANDED, OTHER; branded / non-branded and generic / branded trends.
- Every table ends with a bold TOTAL row that sums all rows matching the filters.
- Tables are paged server-side and can be downloaded in batches (CSV) or, up to 1M rows, as one zip. Downloads
  contain data rows only (no TOTAL row).

## Run locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill in; this file is git-ignored
streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. share.streamlit.io > Create app > pick this repo, branch `main`, main file `app.py`.
2. Advanced settings: Python 3.12; paste your filled-in secrets (see `.streamlit/secrets.toml.example`) into **Secrets**.
3. After deploy, open the app's **Settings > Sharing** and restrict viewers to specific people. The data is internal.

Credentials live only in the Community Cloud Secrets box. They are never stored in this repo.

## Password gate

Before anything loads, viewers must enter the password stored in Secrets as `app_password` (top-level key, above
the `[snowflake]` table). Until then the app runs no Snowflake query and shows no data. If `app_password` is missing the
app stays locked instead of opening. After 5 wrong guesses a browser session is locked for 5 minutes. To change the
password, edit it in the app's Secrets and save; everyone is asked again on their next visit. The password is checked
inside the app, so it protects the data but the login page itself is still reachable by anyone with the link.

## Authentication: Snowflake key pair

The app logs in with an RSA key pair. The public key must be registered on the Snowflake user
(`ALTER USER <user> SET RSA_PUBLIC_KEY='...'`). Paste the private key (PEM text) into the app's Secrets as
`private_key` (add `private_key_passphrase` if it is encrypted). Locally you can use `private_key_path` instead.

- The key never expires on its own, so there is nothing to rotate daily.
- If you use a Snowflake network policy, Community Cloud has no fixed IPs, so the allow-list must cover it.

## Snowflake requirements

Use a dedicated service user and read-only role rather than a personal login. The role needs:

```sql
GRANT USAGE ON DATABASE STG_SILVER_DB TO ROLE GSC_DASH_RO;
GRANT USAGE ON SCHEMA STG_SILVER_DB.ANALYTICS TO ROLE GSC_DASH_RO;
GRANT SELECT ON ALL TABLES IN SCHEMA STG_SILVER_DB.ANALYTICS TO ROLE GSC_DASH_RO;
GRANT USAGE ON WAREHOUSE <warehouse> TO ROLE GSC_DASH_RO;
-- scratch schema for session-temporary tables (they vanish when the session ends)
GRANT USAGE ON DATABASE <DB> TO ROLE GSC_DASH_RO;
GRANT USAGE, CREATE TABLE ON SCHEMA <DB>.<SCHEMA> TO ROLE GSC_DASH_RO;
```

## Notes

- Free-tier apps have about 1 GB of RAM, hence the 1M-row cap on single-file downloads and the batch sizes.
- Query results are cached for 6 hours (the source refreshes daily).
