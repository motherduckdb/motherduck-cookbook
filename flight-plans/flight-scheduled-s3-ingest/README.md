---
title: Ingest Partitioned S3 Parquet on a Schedule
id: flight-scheduled-s3-ingest
description: >-
  A reusable Flight that refreshes a MotherDuck table from Hive-partitioned
  Parquet in S3 on a schedule, reading only the partition that changes. Use when
  files already land in partitioned object storage and you want a scheduled,
  incremental warehouse refresh without re-reading every partition each run.
type: template
category: ingestion
features: [flights]
tags: [ingest, s3]
prompt: >-
  My data already lands as Hive-partitioned Parquet in S3, and I want a scheduled Flight
  that incrementally refreshes a MotherDuck table by reading only the partition that
  changes. Help me adapt the "Ingest Partitioned S3 Parquet on a Schedule" recipe to my
  own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-scheduled-s3-ingest
published_date: 2026-06-08
---

# Ingest Partitioned S3 Parquet on a Schedule

A single-file Flight that refreshes a MotherDuck table from Hive-partitioned
Parquet in S3. It shows the MotherDuck pattern for scheduled, incremental
ingestion: filter on the partition column so DuckDB prunes to the matching
folder, then replace just that one partition in the destination. Older
partitions never change, so re-reading them every run wastes work.

Everything is driven by Flight config, so you adapt it by setting config values,
not by editing `flight.py`. The defaults read the public DuckDB PyPI download
stats (partitioned by `year`) and build `flights_demo.main.duckdb_pypi_downloads`
in your own account, so a fresh deploy produces a successful run you can then
point at your own data.

## How it works

`flight.py` runs a fixed sequence; the config values only change its inputs:

1. Connect to MotherDuck (`md:`) and `CREATE DATABASE`/`CREATE SCHEMA IF NOT EXISTS`
   for the destination, so the Flight owns everything it needs.
2. Create the destination once with `CREATE TABLE IF NOT EXISTS ... AS SELECT * ... LIMIT 0`,
   which infers the destination columns from the source without reading rows.
3. `DELETE` the target partition, then `INSERT` it back by reading the source with
   `hive_partitioning = true` and `WHERE PARTITION_COLUMN = LOAD_PARTITION`. The
   filter on the partition column is what lets DuckDB prune to a single folder.
4. Count the refreshed rows and append one row to the run ledger.

The default load is a `SELECT *` pass-through, so it works for any partitioned
Parquet with no code changes. To shape the data instead, replace the marked
`SELECT *` in the `INSERT` with your own projection or aggregation, keeping the
partition column so the incremental replace still lines up.

## Questions to answer

- Which partitioned source, and what is its partition key (`SOURCE_GLOB`, `PARTITION_COLUMN`)?
- Which partition should each scheduled run refresh (`LOAD_PARTITION`, default current year)?
- Target MotherDuck database, schema, and table (`DESTINATION_*`); is letting the Flight create them acceptable?
- Is the source public, or does a private bucket need a MotherDuck S3 secret first?
- Which service account token should the Flight use for a scheduled workload?
- What schedule (cron) should it run on?

## Caveats

- **Pruning depends on a direct partition filter.** The speedup comes from
  comparing the raw `PARTITION_COLUMN` to `LOAD_PARTITION`. Wrapping the column in
  a function (for example `CAST(year AS VARCHAR)`) can defeat pruning and read
  every folder.
- **The destination schema is inferred on the first run.** If you later change the
  source columns, they may not match the existing table. Drop and recreate the
  destination, or migrate it, when the shape changes.
- **Numeric partition values are integers.** A digit-only `LOAD_PARTITION` is bound
  as an integer so it matches a numeric Hive column. Pass a non-numeric value for
  string partitions (for example a region code).
- **Private buckets need a secret.** The default dataset is public. Point
  `SOURCE_GLOB` at a private bucket only after adding a MotherDuck **S3 secret**
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
| `SOURCE_GLOB` | public DuckDB PyPI stats glob | Partitioned Parquet to read. Swap for your own S3/HTTPS glob. |
| `PARTITION_COLUMN` | `year` | Hive partition key the Flight prunes and replaces on. Validated as a SQL identifier. |
| `LOAD_PARTITION` | current UTC year | Which partition each run refreshes. Set it to backfill a year, or to target a date or region. Digit-only values are treated as integers. |
| `DESTINATION_DATABASE` | `flights_demo` | MotherDuck database to build into. Created if missing. Validated as a SQL identifier. |
| `DESTINATION_SCHEMA` | `main` | Schema for the destination and ledger tables. Validated as a SQL identifier. |
| `DESTINATION_TABLE` | `duckdb_pypi_downloads` | Destination table name. Validated as a SQL identifier. |
| `HIVE_PARTITIONING` | `true` | Turn `key=value` folder names into columns. |
| `RUN_LEDGER_TABLE` | `ingest_runs` | Audit table that records one row per run. Validated as a SQL identifier. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Select a token on the Flight; never put it in config. |

## Run it

You need a MotherDuck account and an access token. The default source is a public
S3 dataset, so no AWS credentials are needed; a private bucket needs a MotherDuck
S3 secret available to the token behind the Flight.

To smoke-test the source logic locally before deploying, run the file directly
against your account:

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with duckdb==1.5.2 flight.py
```

That single run creates `flights_demo.main.duckdb_pypi_downloads`, loads the
current year's partition, and writes one ledger row. Override any default inline,
for example `LOAD_PARTITION=2024 uv run --with duckdb==1.5.2 flight.py` to
backfill a year.

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `scheduled_s3_ingest`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the keys from [What you'll adjust](#what-youll-adjust) you want to
  override (omit any you are keeping at default)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm it succeeds. Each run reads only
`LOAD_PARTITION`, so the live partition stays fresh without touching the
historical files. Once the manual run is green, add a daily schedule (the source
updates daily; `30 6 * * *`, 06:30 UTC, is a reasonable default) by updating the
Flight's `schedule_cron` with `MD_UPDATE_FLIGHT`. Schedule updates are
metadata-only and do not create a new Flight version.

## Security

Two patterns keep the dynamic SQL safe; preserve both when you adapt the Flight:

- **Identifier validation.** `PARTITION_COLUMN`, `DESTINATION_DATABASE`,
  `DESTINATION_SCHEMA`, `DESTINATION_TABLE`, and `RUN_LEDGER_TABLE` flow into
  statements that cannot be parameterized, so each is checked against
  `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL runs.
- **Parameterized data.** The source glob and the partition value are passed as
  bound parameters to `read_parquet`, the `DELETE`/`INSERT`, and the ledger insert,
  never string-formatted into SQL.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions (Hive partitioning, partition pruning,
  S3 secrets): use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (its one dependency, `duckdb`).
