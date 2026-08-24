---
title: Ingest an Excel Workbook from S3 on a Schedule
id: flight-excel-s3-ingest
description: >-
  A reusable Flight that loads one sheet of an Excel (.xlsx) workbook from S3 or
  HTTPS into a MotherDuck table on a schedule, using read_xlsx over httpfs. Use
  when Excel files land in object storage and you want a scheduled, full-refresh
  load without a manual CLI step or a CSV conversion.
type: template
category: ingestion
features: [flights]
tags: [ingest, s3]
prompt: >-
  Excel workbooks land in my S3 bucket and I want a scheduled Flight that loads a
  sheet into a MotherDuck table with read_xlsx, full-refresh each run. Help me adapt
  the "Ingest an Excel Workbook from S3 on a Schedule" recipe to my own data and use
  case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-excel-s3-ingest
published_date: 2026-07-19
---

# Ingest an Excel Workbook from S3 on a Schedule

A single-file Flight that loads one worksheet of an Excel `.xlsx` workbook into a
MotherDuck table. DuckDB reads Excel natively with `read_xlsx` (from the `excel`
extension), and `httpfs` lets it read the workbook directly from S3 or HTTPS, so
there is no manual download, CLI step, or CSV conversion. This is the same path
the MotherDuck UI's **Add data** button does not yet cover, run on a schedule.

Everything is driven by Flight config, so you adapt it by setting config values,
not by editing `flight.py`. The default reads the `sample_orders.xlsx` workbook
shipped with this template (served over HTTPS from GitHub) and builds
`flights_demo.main.excel_orders` in your own account, so a fresh deploy produces
a successful run you can then point at your own workbook.

## How it works

`flight.py` runs a fixed sequence; the config values only change its inputs:

1. Connect to MotherDuck (`md:`) and `CREATE DATABASE`/`CREATE SCHEMA IF NOT EXISTS`
   for the destination, so the Flight owns everything it needs.
2. Fully replace the destination with `CREATE OR REPLACE TABLE ... AS SELECT * FROM read_xlsx(...)`.
   A workbook is a full snapshot rather than an append log, so a full refresh keeps
   the table in sync without tracking what changed.
3. Count the loaded rows and append one row to the run ledger.

The `excel` and `httpfs` extensions autoload on first use, so no `INSTALL`/`LOAD`
is needed. The default load is a `SELECT *` pass-through, so it works for any
single-sheet workbook with no code changes. To shape the data instead, replace
the `SELECT *` with your own projection or aggregation.

## Questions to answer

- Which workbook (`SOURCE_XLSX`), and is it on S3 (`s3://...`) or HTTPS (`https://...`)?
- Which worksheet should load (`SHEET`, default `orders`; leave empty for the first sheet)?
- Target MotherDuck database, schema, and table (`DESTINATION_*`); is letting the Flight create them acceptable?
- Is the source public, or does a private S3 bucket need a MotherDuck S3 secret first?
- Which service account token should the Flight use for a scheduled workload?
- What schedule (cron) should it run on?

## Caveats

- **One workbook per run.** `read_xlsx` reads a single file, not a glob of many
  (multi-file support is not available yet). Point `SOURCE_XLSX` at one workbook;
  to load several, run one Flight per file or combine them upstream.
- **One sheet per run.** A run loads a single worksheet. Set `SHEET` to the sheet
  you want, or leave it empty to take the first sheet. To land multiple sheets,
  deploy one Flight per sheet with different `DESTINATION_TABLE` values.
- **Full refresh, not incremental.** Each run replaces the whole table. That is
  the right model for a workbook that is republished in full, but it re-reads the
  entire file every run, so it is not suited to very large or append-only sources.
- **Numbers come in as `DOUBLE`.** `read_xlsx` infers types from the cells and
  reads every numeric cell as a double, so an integer-looking column like
  `order_id` loads as `1001.0`, not `1001`. Cast it downstream (for example
  `CAST(order_id AS BIGINT)`) if you need integers.
- **Type inference for messy columns.** For a column with mixed text and numbers,
  set `ALL_VARCHAR` to `true` to read everything as text, then cast the columns
  you need, or clean the sheet.
- **Private buckets need a secret.** The default source is public. Point
  `SOURCE_XLSX` at a private `s3://` bucket only after adding a MotherDuck **S3 secret**
  for it: the simplest way is the MotherDuck UI at
  [Settings > Secrets](https://app.motherduck.com/settings/secrets), or
  `CREATE SECRET ... (TYPE S3, ...)` from the DuckDB client. It must be available
  to the Flight's token. (This is an S3 secret on the account, not a Flights
  secret: it is read by the engine, not injected as an env var.)
- **Keep the token out of config.** Select a token on the Flight so
  `MOTHERDUCK_TOKEN` is injected at runtime; do not place it in `config`.

## What you'll adjust

Every knob is a config/env value read at the top of `flight.py`. Set them as
Flight config, not by editing code.

| Config key | Default | Purpose |
|---|---|---|
| `SOURCE_XLSX` | bundled `sample_orders.xlsx` (HTTPS) | The workbook to read. Swap for your own `s3://` or `https://` path. |
| `SHEET` | `orders` | Worksheet to load. Leave empty to take the first sheet. |
| `ALL_VARCHAR` | `false` | Read every cell as text instead of inferring types. Useful for messy sheets. |
| `DESTINATION_DATABASE` | `flights_demo` | MotherDuck database to build into. Created if missing. Validated as a SQL identifier. |
| `DESTINATION_SCHEMA` | `main` | Schema for the destination and ledger tables. Validated as a SQL identifier. |
| `DESTINATION_TABLE` | `excel_orders` | Destination table name. Validated as a SQL identifier. |
| `RUN_LEDGER_TABLE` | `ingest_runs` | Audit table that records one row per run. Validated as a SQL identifier. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Select a token on the Flight; never put it in config. |

## Run it

You need a MotherDuck account and an access token. The default source is a public
HTTPS workbook, so no AWS credentials are needed; a private S3 bucket needs a
MotherDuck S3 secret available to the token behind the Flight.

To smoke-test the source logic locally before deploying, run the file directly
against your account:

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with duckdb==1.5.4 flight.py
```

That single run creates `flights_demo.main.excel_orders`, loads the `orders`
sheet, and writes one ledger row. Override any default inline, for example
`SHEET=regions DESTINATION_TABLE=excel_regions uv run --with duckdb==1.5.4 flight.py`
to load the other sheet.

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `excel_s3_ingest`
- `source_code`: the contents of [`flight.py`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-excel-s3-ingest/flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-excel-s3-ingest/requirements.txt)
- `max_runtime_sec`: optional cap on a run's duration in seconds (`0` = no cap)
- `config`: the keys from [What you'll adjust](#what-youll-adjust) you want to
  override (omit any you are keeping at default)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`; inspect a specific run with
`MD_GET_FLIGHT_RUN(flight_id := ..., run_number := ...)`), and confirm it
succeeds. Once the manual run is green, add a schedule that matches how often
the workbook is republished (for example `0 7 * * *`, 07:00 UTC daily) by
updating the Flight's `schedule_cron` with `MD_UPDATE_FLIGHT`. Schedule updates
are metadata-only and do not create a new Flight version.

## Security

Two patterns keep the dynamic SQL safe; preserve both when you adapt the Flight:

- **Identifier validation.** `DESTINATION_DATABASE`, `DESTINATION_SCHEMA`,
  `DESTINATION_TABLE`, and `RUN_LEDGER_TABLE` flow into statements that cannot be
  parameterized, so each is checked against `^[A-Za-z_][A-Za-z0-9_]*$` before any
  SQL runs.
- **Parameterized and escaped inputs.** The workbook path is passed as a bound
  parameter to `read_xlsx`. The `SHEET` name is a named table-function argument
  that must be a literal, so single quotes in it are doubled before it is inlined.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions (`read_xlsx` options, sheets, S3 secrets):
  use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-excel-s3-ingest/flight.py)
  (the single-file Flight source), [`requirements.txt`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-excel-s3-ingest/requirements.txt)
  (its one dependency, `duckdb`), and [`sample_orders.xlsx`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-excel-s3-ingest/sample_orders.xlsx)
  (the default sample workbook, with `orders` and `regions` sheets).
