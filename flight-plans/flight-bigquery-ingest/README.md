---
title: Incrementally Ingest BigQuery into MotherDuck
id: flight-bigquery-ingest
description: >-
  A reusable Flight that reads from BigQuery through the bigquery DuckDB
  community extension and loads the result into a MotherDuck table
  incrementally, using a date-partition watermark with idempotent per-partition
  DELETE plus INSERT. It defaults to bigquery_scan (Storage Read API: cheaper,
  no query job) and can switch to bigquery_query (Jobs API) when you need
  GoogleSQL. Use when you want scheduled, incremental BigQuery to MotherDuck
  ingestion as part of a migration off BigQuery.
type: template
features: [flights]
tags: [bigquery, ingest, migrate]
---

# Incrementally Ingest BigQuery into MotherDuck

A single-file Flight that pulls BigQuery data into a MotherDuck table, one date
partition at a time. It is the BigQuery side of a migration onto MotherDuck:
keep BigQuery as the source of truth while you build out MotherDuck, and let a
scheduled Flight keep a MotherDuck copy current.

The pattern is a date-partition watermark. Each run figures out which days are
missing or stale, reads just those days from BigQuery through the `bigquery`
community extension, and loads each day with a DELETE plus INSERT inside a
transaction so re-running a day replaces it instead of duplicating it. An
`OVERLAP_DAYS` window re-loads the last few partitions every run so
late-arriving rows get healed.

By default it reads with `bigquery_scan` (the BigQuery Storage Read API), which
is the cheaper and lower-latency path for a plain partitioned-table read. When
you genuinely need GoogleSQL — joins, aggregations, functions, derived columns,
or a view — flip `READ_MODE` to `"query"` and it uses `bigquery_query` instead.
See [Which read mode?](#which-read-mode-bigquery_scan-vs-bigquery_query) before
you reach for query mode.

Like [flight-freshness-alert](../flight-freshness-alert), the thing you edit is a
small set of constants at the top of `flight.py` (your `QUERY`, the destination,
the partition column), not a config blob. The generic engine below those
constants stays untouched.

## Which read mode? bigquery_scan vs bigquery_query

`READ_MODE` decides how rows leave BigQuery. **Default to `"scan"`.** Both modes
produce the same rows in the same destination; they differ in cost, speed, and
capability, not in the result.

- **`"scan"` → `bigquery_scan` (the default).** Reads a table directly through
  the BigQuery **Storage Read API**. It is billed on the cheaper storage-read
  meter (not the query bytes-scanned meter), skips the query-job step so it
  starts streaming sooner, and pushes column projection and the row filter down
  so you only transfer the columns and partitions you load. This is the right
  choice for the common case: a plain read of a date-partitioned table.

- **`"query"` → `bigquery_query`.** Runs arbitrary GoogleSQL through the **Jobs
  API**, billed by bytes scanned. More capable, but a query job runs before any
  bytes stream, and the analysis meter is ~5–6× the per-byte rate of the
  storage-read meter.

**Stay on `"scan"` unless you answer "yes" to one of these — *do you need to…*:**

- …**join or aggregate server-side** in BigQuery before the data lands?
- …**use a GoogleSQL function or a derived/computed column** (e.g.
  `DATE(event_timestamp) AS event_date`) that the source table does not already
  store? (`bigquery_scan` reads stored columns only.)
- …**read a view or an external table**? (The Storage Read API cannot, so
  `bigquery_scan` cannot either.)

If all three are "no", use `"scan"` — it is cheaper and faster for the same
result. If you flip to `"query"`, push the date filter into the query's `WHERE`
on a partitioned column so BigQuery still prunes partitions; a query that scans
the whole table every run gets expensive fast.

## How it works

`flight.py` runs a fixed sequence; only the USER-EDIT BLOCKS change its inputs:

1. Materialize GCP credentials. If `GOOGLE_APPLICATION_CREDENTIALS` already
   points at a file (local run), use it. Otherwise read the SA JSON from the
   resolved secret env var, validate it parses as JSON, write it to a private
   temp file, and set `GOOGLE_APPLICATION_CREDENTIALS` to that path.
2. Connect DuckDB in memory with community extensions enabled, `INSTALL`/`LOAD`
   the `bigquery` extension, `LOAD motherduck`, `ATTACH 'md:'`, and set
   `preserve_insertion_order = FALSE`.
3. Ensure the destination exists. Create the database and schema, then bootstrap
   the table by running the source read (`bigquery_scan` or `bigquery_query`,
   per `READ_MODE`) over a 1970-01-01 window (BigQuery prunes the partitioned
   scan to nothing, so the probe is nearly free) with
   `CREATE TABLE IF NOT EXISTS ... AS SELECT ... LIMIT 0`, which takes the result
   schema without rows.
4. Resolve the partition window. Priority: explicit `start_dt`+`end_dt`
   (inclusive backfill range), then `target_dt` (single day), otherwise watermark
   mode (`prev_max = MAX(partition column)`; load
   `[prev_max + 1 - OVERLAP_DAYS, prev_max + 1]`; cold start at `COLD_START_DT`
   when empty).
5. For each day in the window, run one transaction: `DELETE` rows matching
   `MD_DELETE_PREDICATE` for that day, then `INSERT` the day's source read
   (`bigquery_scan(SCAN_TABLE, filter=...)` in scan mode, or
   `bigquery_query(GCP_PROJECT_ID, QUERY)` in query mode). Commit on success,
   roll back on error. Per-phase timing (DELETE, then BigQuery read plus
   MotherDuck insert) is logged to stdout, which Flight logs capture.

The DELETE plus INSERT per partition is what makes a re-run safe: re-loading a
day clears its rows first, so you never get duplicates, and a failed day rolls
back cleanly rather than leaving a half-loaded partition.

## Questions to answer

- Do you actually need GoogleSQL (a join, aggregation, function, derived column, or a view)? If not, keep `READ_MODE = "scan"`. If so, switch to `"query"`. See [Which read mode?](#which-read-mode-bigquery_scan-vs-bigquery_query).
- Which BigQuery table do you want to load (scan mode: `SCAN_TABLE` + `SCAN_COLUMNS`; query mode: the `FROM` in `QUERY`), and which result column is the partition date?
- Is the source partitioned on that date column so the `{start_dt}`/`{end_dt}` filter actually prunes scans?
- What is the destination `database.schema.table` in MotherDuck?
- What is your GCP billing project (the project charged for the read), and is it the right one for cost attribution?
- How far back should a cold start go (`COLD_START_DT`), and how many days of overlap heal late-arriving data (`OVERLAP_DAYS`)?
- Do the active read filter (`SCAN_FILTER` or `QUERY`) and `MD_DELETE_PREDICATE` filter on exactly the same predicates, so the DELETE never clears rows you are not re-loading?
- Which service account can run the read, and how is its JSON key stored (a file locally, a `TYPE flights` secret when deployed)?
- What schedule (cron, UTC) matches how often the source data lands?

## Caveats

- **No credential-free smoke test.** Unlike the `sample_data` templates here,
  this Flight needs a real BigQuery source: a GCP billing project and a
  service-account key. There is no public dataset path that exercises the whole
  flow without credentials.
- **BigQuery costs money — scan is the cheaper default.** In scan mode,
  `bigquery_scan` reads through the Storage Read API, billed on the storage-read
  meter; the `SCAN_FILTER` row restriction on a partitioned column prunes
  partitions so you only read the days you load. In query mode, `bigquery_query`
  runs through the Jobs API, billed by bytes scanned (~5–6× the per-byte rate of
  the storage-read meter) — push the date filter into `QUERY`'s WHERE on a
  partitioned column so BigQuery still prunes. Either way, a read that scans the
  full table every run gets expensive fast. Prefer scan unless you need GoogleSQL
  (see [Which read mode?](#which-read-mode-bigquery_scan-vs-bigquery_query)).
- **Predicate mirroring is a footgun.** `MD_DELETE_PREDICATE` must select exactly
  the rows one partition's read produces. If the active read filter (`SCAN_FILTER`
  or `QUERY`) filters on `event_source` but the DELETE does not, the DELETE clears
  rows you are not re-loading and you lose data. Keep them in lockstep, and note
  the quoting difference: the BigQuery-side `SCAN_FILTER`/`QUERY` use double quotes
  for string literals, the DuckDB-side `MD_DELETE_PREDICATE` uses single quotes.
- **Watermark and time zones.** The watermark is computed from
  `MAX(PARTITION_DATE_COLUMN)`, a date, so it is timezone-free as long as the
  column is a true partition DATE. Scan mode reads the stored DATE column
  directly. In query mode, if your partition date is derived from a timestamp,
  derive it the same way in `QUERY` every run (for example `DATE(event_timestamp)`
  in a consistent zone), or the day boundaries will drift. A deployed Flight runs
  in UTC; a local `uv run` uses your machine's timezone.
- **`OVERLAP_DAYS` trades cost for freshness.** A larger overlap heals
  later-arriving data but re-scans and re-loads more partitions every run. Set it
  to the longest delay you expect for late rows, no more.
- **Cold start can be large.** A watermark cold start loads a single day
  (`COLD_START_DT`). To backfill history, run once with `start_dt`/`end_dt`
  spanning the range before you rely on the watermark.

## What you'll adjust

The read mode, source, and destination live in USER-EDIT BLOCKS at the top of
`flight.py`. A few inputs come from outside the code: the MotherDuck token, the
GCP billing project, and the service-account credentials.

| Knob | Where | Default | Purpose |
|---|---|---|---|
| `DEFAULT_DB` | top of `flight.py` | `bigquery_ingest` | Destination database in MotherDuck. Validated as a SQL identifier. Override per run with `BIGQUERY_DEST_DB`. |
| `SCHEMA` | top of `flight.py` | `main` | Destination schema. Validated as a SQL identifier. |
| `TABLE` | top of `flight.py` | `events` | Destination table. Validated as a SQL identifier. |
| `PARTITION_DATE_COLUMN` | top of `flight.py` | `event_date` | Column in the source read's result holding the partition date. Drives the watermark. Must be a DATE and must exist in the result. |
| `COLD_START_DT` | top of `flight.py` | `2024-01-01` | First day to load when the destination table is empty. A `datetime.date`. |
| `OVERLAP_DAYS` | top of `flight.py` | `3` | Watermark re-load window in days. Each run re-loads the last `OVERLAP_DAYS` partitions to heal late data. `0` = forward-only. |
| `READ_MODE` | top of `flight.py` | `scan` | `"scan"` (`bigquery_scan`, Storage Read API — default, cheaper) or `"query"` (`bigquery_query`, Jobs API — only when you need GoogleSQL). See [Which read mode?](#which-read-mode-bigquery_scan-vs-bigquery_query). |
| `SCAN_TABLE` | top of `flight.py` | `my_project.analytics.events` | scan mode: the fully-qualified `project.dataset.table` to read. Validated as a table reference before interpolation. |
| `SCAN_COLUMNS` | top of `flight.py` | sample column list | scan mode: the projected SELECT list (`*` for everything). Only these columns are read. Must include `PARTITION_DATE_COLUMN`. |
| `SCAN_FILTER` | top of `flight.py` | sample row filter | scan mode: a BigQuery Storage Read API `row_restriction` on `{start_dt}`/`{end_dt}` so BigQuery prunes partitions. BigQuery-side, so literals use double quotes. |
| `QUERY` | top of `flight.py` | sample events query | query mode only: the BigQuery GoogleSQL string. Must return `PARTITION_DATE_COLUMN` and filter on `{start_dt}`/`{end_dt}` so BigQuery prunes partitions. |
| `MD_DELETE_PREDICATE` | top of `flight.py` | mirrors the source filter | A DuckDB WHERE clause that clears one partition before re-insert. Must mirror the active read's predicates (`SCAN_FILTER` or `QUERY`). |
| `build_filters()` | top of `flight.py` | `start_dt`, `end_dt`, `event_source` | Returns the `{placeholder}` values for `SCAN_FILTER`/`QUERY` and `MD_DELETE_PREDICATE`. Reads per-run overrides (e.g. `EVENT_SOURCE`) from the environment. |
| `GCP_PROJECT_ID` | Flight config / env var | (required) | The GCP billing project charged for the read. In scan mode it is interpolated into `bigquery_scan` (named args can't be bound); in query mode it is bound as a parameter. Validated as a project id. |
| `GOOGLE_APPLICATION_CREDENTIALS` | env var (local) | (unset) | Path to a service-account JSON file on disk. Used for a local run. |
| `GOOGLE_APPLICATION_CREDENTIALS_JSON` | Flight secret | (unset) | The service-account JSON itself, provided through a `TYPE flights` secret. Deployed, it arrives as `<secret_name>_GOOGLE_APPLICATION_CREDENTIALS_JSON`; `flight.py` resolves either name. |
| `start_dt` / `end_dt` / `target_dt` | env var (optional) | (unset) | Override the window: explicit inclusive backfill range, or a single day. Unset = watermark mode. |
| `BIGQUERY_DEST_DB` | Flight config / env var (optional) | `DEFAULT_DB` | Override the destination database without editing code. |
| `MOTHERDUCK_TOKEN` | Flight-injected | (Flight-injected) | Auth. Select a token on the Flight; never hard-code it. |

## Run it

You need a MotherDuck account and access token, a GCP project to bill the
read, and a service-account JSON key with permission to read from BigQuery
(the BigQuery Storage Read API for scan mode, or run a query in query mode).
Unlike the `sample_data` templates here, there is no credential-free smoke
test: BigQuery access is required (see [Caveats](#caveats)).

```bash
export MOTHERDUCK_TOKEN=your_token_here
export GCP_PROJECT_ID=your-gcp-billing-project
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
# optional: a one-day load or an explicit inclusive backfill range
# export target_dt=2024-03-01
# export start_dt=2024-03-01
# export end_dt=2024-03-07
uv run --with-requirements requirements.txt flight.py
```

With the constants edited to your source and destination, this connects to
MotherDuck, installs and loads the `bigquery` extension, bootstraps the
destination table from the source read's schema, resolves the partition window,
and loads each day. With no `target_dt`/`start_dt`/`end_dt` set it runs in
watermark mode: a cold start loads `COLD_START_DT`, and later runs load the days
after the destination's current maximum partition date (plus the `OVERLAP_DAYS`
overlap).

### Deploy as a Flight

The service-account JSON is a secret, so store it as a MotherDuck **Flights
secret**. The simplest way is the MotherDuck UI: open
[Settings > Secrets](https://app.motherduck.com/settings/secrets), add a secret of
type **Flights**, and give it a `GOOGLE_APPLICATION_CREDENTIALS_JSON` parameter
whose value is the entire JSON key as one string. If you would rather use SQL, you
can create the same secret from the DuckDB client or any write-enabled SQL
connection (read-only connections reject `CREATE SECRET`):

```sql
CREATE SECRET gcp_creds IN motherduck (
  TYPE flights,
  PARAMS MAP { 'GOOGLE_APPLICATION_CREDENTIALS_JSON': '{"type":"service_account", ...}' }
);
```

A `TYPE flights` secret injects each param under the env var
`<secret_name>_<PARAM>`, not the bare param name: the param above arrives as
`gcp_creds_GOOGLE_APPLICATION_CREDENTIALS_JSON`, not
`GOOGLE_APPLICATION_CREDENTIALS_JSON`. (DuckDB lowercases the unquoted secret name
into the prefix.) `flight.py` handles this: it reads the bare
`GOOGLE_APPLICATION_CREDENTIALS_JSON` for local runs and otherwise picks up any
env var ending in `_GOOGLE_APPLICATION_CREDENTIALS_JSON`, then writes the JSON to
a temp file and points `GOOGLE_APPLICATION_CREDENTIALS` at it. The secret name you
choose does not matter.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL
is checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `bigquery_ingest`
- `source_code`: the contents of [`flight.py`](flight.py), with the USER-EDIT
  BLOCKS edited to your read mode, source, and destination
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["gcp_creds"]` so the SA JSON is injected (as
  `gcp_creds_GOOGLE_APPLICATION_CREDENTIALS_JSON`; `flight.py` resolves it)
- `config`: `GCP_PROJECT_ID` (and optionally `BIGQUERY_DEST_DB`, `EVENT_SOURCE`).
  The billing project is not a secret, so it belongs in config. The SA JSON does
  NOT: keep it in the secret above.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm it loads the cold-start partition. Then
add a schedule (for example `0 6 * * *`, daily at 06:00 UTC) by updating the
Flight's `schedule_cron` with `MD_UPDATE_FLIGHT`. Schedule updates are
metadata-only and do not create a new Flight version. For a one-off backfill, set `start_dt`/`end_dt` (or `target_dt`)
in the run config.

## Security

- **SA JSON in a secret, not config.** The service-account JSON is a credential,
  so it must come from a MotherDuck `TYPE flights` secret, never from plaintext
  Flight `config`. Putting it in config (as the original internal template did) is
  a hack: config is not treated as sensitive. The team decision is to move the SA
  JSON to a secret, so this template reads it from the secret-injected env var and
  only ever writes it to a private temp file in the Flight's ephemeral runtime.
- **Identifier validation.** `DEFAULT_DB` (and its `BIGQUERY_DEST_DB` override),
  `SCHEMA`, `TABLE`, and `PARTITION_DATE_COLUMN` are checked against
  `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL runs, because they flow into
  `CREATE`/`USE`/`SELECT`/`DELETE`/`INSERT` statements that cannot be
  parameterized, and are quoted with `ident()`. `GCP_PROJECT_ID` is validated as a
  GCP project id, and `SCAN_TABLE` is validated as a `project.dataset.table`
  reference.
- **How read values reach the SQL.** In query mode, the billing project and the
  rendered GoogleSQL are bound as parameters to `bigquery_query(?, ?)`, never
  f-string'd into the statement. In scan mode, `bigquery_scan`'s table,
  `billing_project`, and `filter` are named function arguments that cannot be
  bound as parameters, so they are interpolated — but only after the table and
  project are validated and the filter is rendered from `build_filters()` values
  with single quotes escaped (`sql_str`). As with `QUERY`/`MD_DELETE_PREDICATE`,
  the `{placeholder}` values are ones you control here, not untrusted input.

## Learn more

- Flight mechanics (creating, running, scheduling, secrets): use the MotherDuck
  MCP `get_flight_guide` tool.
- `bigquery_scan('table', filter=...)` (Storage Read API, the cheaper default)
  vs `bigquery_query` (Jobs API, billed by bytes scanned, for server-side joins,
  views, or GoogleSQL functions): the
  [duckdb-bigquery community extension](https://github.com/hafenkran/duckdb-bigquery)
  documents both. This template defaults to `bigquery_scan` and switches to
  `bigquery_query` when `READ_MODE = "query"`. See
  [Which read mode?](#which-read-mode-bigquery_scan-vs-bigquery_query).
- Deeper MotherDuck or DuckDB questions: use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (just `duckdb`; the `bigquery`
  extension is a runtime community extension, not a pip package).
