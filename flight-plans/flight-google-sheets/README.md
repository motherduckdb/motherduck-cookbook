---
title: Sync Google Sheets and MotherDuck With a Flight
id: flight-google-sheets
description: >-
  A reusable Flight that syncs data both ways between Google Sheets and
  MotherDuck through the DuckDB gsheets community extension. Import a list of
  sheets into MotherDuck tables, and export query results back to sheet tabs as
  reverse ETL. Both directions are full-refresh and idempotent, with per-item
  retries and an audit log. Use it to pull business data out of spreadsheets and
  push curated data back into them for business process automation.
type: template
category: ingestion
features: [flights]
tags: [google-sheets, ingest, export]
---

# Sync Google Sheets and MotherDuck With a Flight

Spreadsheets are where a lot of real business data actually lives. 
This Flight brings that data into MotherDuck so it can join your warehouse tables, 
and pushes results back out to Google Sheets so the people who live in spreadsheets keep working
where they already are.

The export direction is **reverse ETL**: you run a query in MotherDuck
and land the result on a Google Sheet tab. That is what turns a warehouse into
**business process automation** — a scheduled Flight can refresh a "top accounts
to call today" tab, a per-region inventory sheet, or a finance reconciliation
report every morning, with no one exporting CSVs by hand. The same Flight can do
the inbound direction, the outbound direction, or both, so it fits a plain
ingest, a pure reverse-ETL publish, or a full round trip.

At a high level you give the Flight two lists: which sheets to **import** (each
becomes a MotherDuck table) and which queries to **export** (each result
overwrites a sheet tab).

## How it works

`flight.py` is a single file driven entirely by config; no code edits are needed
for the common cases. One run does:

1. **Validate config and credentials, then connect.** The Google service-account
   key is read from a MotherDuck **Flights secret** and registered with the
   `gsheets` extension **in memory**.
2. **Import each configured sheet** with one atomic statement:
   `CREATE OR REPLACE TABLE <db>.<schema>.<table> AS SELECT * FROM read_gsheet('<url>')`.
3. **Export each configured query** by running the SELECT on MotherDuck,
   materializing the (small sheet-sized) result as an Arrow table, and copying it to
   the destination tab with `COPY ... TO '<url>' (FORMAT gsheet, OVERWRITE_SHEET TRUE)`.
   `OVERWRITE_SHEET` keeps re-runs idempotent.
4. **Isolate, retry, and audit every item.** Each import/export is retried with
   jittered exponential backoff; one failing item never stops the rest. Every
   item — success or failure — is recorded in
   `<TARGET_DATABASE>.main.gsheets_sync_log`, and the run exits non-zero if
   anything failed after retries.

## Questions to answer

- Which Google Sheets should become MotherDuck tables, and what table name does
  each map to? (Optionally a specific tab, cell range, header handling, and a
  per-item destination database/schema.)
- Which queries should be published back to Sheets, and to which spreadsheet URL
  and tab? (Each export is either a `query` or a `database`/`table` reference.)
- Are you doing import-only, export-only, or both? Leave the unused list blank.
- Which `TARGET_DATABASE` / `TARGET_SCHEMA` should imported tables land in?
- What schedule (cron, UTC) matches how often the source data changes and how
  fresh the published sheets need to be?
- Which Google service account will the sheets be shared with? (See
  [Caveats](#caveats) — service accounts cannot own sheets.)

## Caveats

- **Service accounts have zero Drive storage quota** (Google policy since 2025),
  so the service account cannot create or own spreadsheets. Every source and
  destination sheet must be owned by a person (or a Shared Drive) and shared
  with the service account's `client_email`: **Viewer** is enough for sources,
  **Editor** for destinations.
- **Full refresh, both directions.** Imports replace the whole table; exports
  overwrite the whole tab. 
- **Destination sheets must exist** unless the export sets `create_sheet: true`,
  which creates the tab if missing.
- **Sheet size limits.** A Google spreadsheet holds at most 10M cells; exports
  are capped at 10,000,000 rows to bound memory, and wider results can still hit
  the cell cap at the Sheets API. The extension also writes ~2,048 rows per API
  call with no rate-limit retry, so very large exports can hit Google's
  per-minute write quota.
- **Export queries must be a single read-only `SELECT`** (enforced by DuckDB's
  parser); `database`/`schema`/`limit` apply only in table mode.
- **Old tables/tabs are not removed.** Dropping a sheet from `SOURCE_SHEETS` does
  not drop the table it created; clean those up yourself.

## What you'll adjust

No code edits are required. Everything is read from Flight config/env plus one
MotherDuck **Flights secret** holding the Google service-account key.

| Knob | Default | Purpose |
|---|---|---|
| `SOURCE_SHEETS` | `[]` | JSON array of sheets to import. Each: `{"url", "table"}` plus optional `sheet`, `range`, `header`, `all_varchar`, `database`, `schema`. Leave blank for export-only. |
| `EXPORTS` | `[]` | JSON array of queries to publish. Each: `{"url"}` plus EITHER `query` (one SELECT) OR `database`+`table` (optional `schema`, `limit`); optional `sheet`, `create_sheet`. Leave blank for import-only. |
| `TARGET_DATABASE` | `google_sheets` | MotherDuck database imported tables land in (created if absent). Also holds the `gsheets_sync_log` audit table. |
| `TARGET_SCHEMA` | `main` | Schema imported tables land in. |
| `GSHEETS_SECRET_NAME` | `gsheets` | Name of the `TYPE flights` secret holding `SERVICE_ACCOUNT_JSON`. |
| `MAX_RETRIES` | `5` | Per-item retry attempts. |
| `RETRY_BASE_SECONDS` | `2` | Exponential-backoff multiplier (seconds). |
| `MOTHERDUCK_HOST` | (unset) | Override MotherDuck host (non-prod). Leave unset for default. |
| `gsheets` **secret** | (required) | `TYPE flights` secret with one param, `SERVICE_ACCOUNT_JSON`, holding the full service-account key JSON. |

Example config values:

```json
SOURCE_SHEETS = [
  {"url": "https://docs.google.com/spreadsheets/d/<id>/edit", "table": "target_accounts"},
  {"url": "https://docs.google.com/spreadsheets/d/<id>/edit", "table": "price_overrides", "sheet": "Q3"}
]
EXPORTS = [
  {"url": "https://docs.google.com/spreadsheets/d/<id>/edit", "sheet": "calls_today", "create_sheet": true,
   "query": "SELECT account, owner, score FROM crm.scored_accounts ORDER BY score DESC LIMIT 200"}
]
```

## Run it

You need a MotherDuck account and token, and a Google service-account key whose
`client_email` has been shared on the sheets you reference. 

To create and set up a Google service-account and key, ask your agent! Or use these references:
* [DuckDB GSheets extension docs for getting a token](https://duckdb-gsheets.com/#getting-a-google-api-access-token)
* [Docs for creating a Google Service Account](https://docs.cloud.google.com/iam/docs/service-accounts-create)

For a local run, inject the key the same way the Flights secret would:

```bash
export MOTHERDUCK_TOKEN=your_token_here
# the service-account key JSON, exactly as the `gsheets` Flights secret injects it:
export gsheets_SERVICE_ACCOUNT_JSON="$(cat path/to/service-account.json)"
# pick a direction (either, or both):
export SOURCE_SHEETS='[{"url":"https://docs.google.com/spreadsheets/d/<id>/edit","table":"target_accounts"}]'
export EXPORTS='[{"url":"https://docs.google.com/spreadsheets/d/<id>/edit","sheet":"calls_today","create_sheet":true,"query":"SELECT 1 AS demo"}]'
# optional: destination database/schema
# export TARGET_DATABASE=google_sheets
uv run --with-requirements requirements.txt flight.py
```

This validates config and the key, connects to MotherDuck, loads the `gsheets`
extension, creates `TARGET_DATABASE` and the `main.gsheets_sync_log` audit table,
then imports each sheet and exports each query with per-item retries. One log
line per item plus a summary; exits non-zero if any item failed after retries.

### Deploy as a Flight

First store the Google service-account key as a **Flights secret** named
`gsheets` (UI: [Settings > Secrets](https://app.motherduck.com/settings/secrets),
type **Flights**). Or via SQL from a write-enabled connection (read-only
connections reject `CREATE SECRET`):

```sql
CREATE SECRET gsheets IN motherduck (
  TYPE flights,
  PARAMS MAP { 'SERVICE_ACCOUNT_JSON': '<paste the full service-account key JSON>' }
);
```

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `google_sheets_sync`
- `source_code`: [`flight.py`](flight.py) (no edits for the common cases)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["gsheets"]` so the service-account key is injected
- `config`: your `SOURCE_SHEETS` and/or `EXPORTS` JSON, plus `TARGET_DATABASE`/`TARGET_SCHEMA` if non-default. Credentials stay in the `gsheets` secret, never config.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create without a schedule, run once with `MD_RUN_FLIGHT(flight_id := ...)` (the
id is returned by `MD_CREATE_FLIGHT` and listed by `MD_FLIGHTS()`), and confirm
`<TARGET_DATABASE>.main.gsheets_sync_log` has one row per item. Decide with the
user whether a schedule is desired and what cadence fits the data.

## Security

- **Key in a secret, in memory only.** The service-account key comes from a `TYPE
  flights` secret and is registered with the extension inline (`EMAIL`/`SECRET`),
  never written to disk and never logged.
- **Quoted identifiers and escaped literals.** Config-supplied database/schema/
  table names flow into SQL via `quote_ident()`, and sheet URLs/options via a
  single-quote-escaping `sql_str()` — preventing SQL injection.
- **Read-only export queries.** Each export `query` is validated with DuckDB's
  parser to be exactly one `SELECT`, so an export can never mutate the warehouse.

## Learn more

- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- DuckDB `gsheets` extension: [github.com/evidence-dev/duckdb_gsheets](https://github.com/evidence-dev/duckdb_gsheets).
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
- Files: [`flight.py`](flight.py) (the Flight source), [`requirements.txt`](requirements.txt) (`duckdb` plus `pyarrow` for the export Arrow bridge and `tenacity` for retry/backoff; the `gsheets` extension is a runtime community extension, not a pip package).
