---
title: Copy AWS S3 Tables (Iceberg) into MotherDuck With a Flight
id: flight-s3tables-iceberg-ingest
description: >-
  A reusable Flight that copies tables from an Apache Iceberg (AWS S3 Tables) catalog
  into MotherDuck, one streaming full-refresh CREATE OR REPLACE per table, with
  config-driven namespace/table selection, retries with backoff, and a per-table
  audit log. Use it for a config-driven, re-runnable S3 Tables to MotherDuck ingest.
type: template
category: ingestion
features: [flights]
tags: [ingest, s3]
prompt: >-
  I have tables in an AWS S3 Tables Iceberg bucket and I want to copy them into
  MotherDuck as native tables, on a schedule, so my queries stop re-scanning Iceberg
  every time. I need to choose which namespaces and tables get copied, and I want a
  re-run to just replace the data. Help me adapt the "Copy AWS S3 Tables (Iceberg)
  into MotherDuck With a Flight" recipe to my own bucket and use case, using it as a
  starting point.
published_date: 2026-07-17
---

# Copy Apache Iceberg (AWS S3 Tables) into MotherDuck With a Flight

Copies one or more tables from an AWS S3 Tables Iceberg Lakehouse into a MotherDuck database, so
queries read native tables instead of scanning Iceberg. Point it at a bucket, pick
which namespaces and tables to copy, and re-run it on a schedule to keep the copies current.
Everything is driven by config, so you can reuse it without editing the code.

Each run attaches the S3 Tables catalog as a MotherDuck database (`TYPE ICEBERG`), finds the tables
in scope, and copies each one with a single `CREATE OR REPLACE TABLE ... AS SELECT *`. 
It swaps the table in one step, a re-run just replaces it, and DuckDB
streams the read straight into the write so memory stays flat on large tables. Copied tables land at
`<target>.<namespace>.<table>`, and each run writes one row per table to `<target>.main.flight_tracker`.

## Prerequisite: an S3 secret

The Flight holds no AWS keys. It references a MotherDuck S3 secret by name (`SECRET_NAME` in
`flight.py`, default `s3_tables_secret`), so create that secret once. The keys need S3 Tables
catalog access (`s3tables:*`) in the bucket's account, plus read on the data. Run this through the
DuckDB CLI so `getenv()` reads the keys from your shell instead of writing them into the statement
(drop `SESSION_TOKEN` for long-lived IAM keys):

```bash
motherduck_token="$YOUR_TOKEN" duckdb "md:" <<'SQL'
CREATE OR REPLACE SECRET s3_tables_secret IN MOTHERDUCK (
    TYPE S3,
    KEY_ID getenv('AWS_ACCESS_KEY_ID'),
    SECRET getenv('AWS_SECRET_ACCESS_KEY'),
    REGION 'us-east-1'
);
SQL
```

## What you'll adjust

No code edits are required. Everything is read from Flight config and the MotherDuck secret.

| Knob | Default | Purpose |
|---|---|---|
| `TABLE_BUCKET_ARN` | AWS Bucket ARN | S3 Tables bucket to copy from (`arn:aws:s3tables:…:bucket/…`). |
| `TARGET_DATABASE` | `iceberg_ingest` | MotherDuck database for the copy (created if absent). Tables land at `<target>.<namespace>.<table>`. |
| `INCLUDED_SCHEMAS` | (all) | Comma-separated namespaces to include. Empty = all. |
| `EXCLUDED_SCHEMAS` | (none) | Comma-separated namespaces to drop. Exclude wins. |
| `INCLUDED_TABLES` | (all) | Comma-separated `namespace.table`, or a bare `table` (matches that table in any namespace). Empty = all in selected namespaces. |
| `EXCLUDED_TABLES` | (none) | Comma-separated `namespace.table` or bare `table` to drop. Exclude wins. |
| `MAX_RETRIES` | `5` | Per-table retry attempts on transient errors. |
| `RETRY_BASE_SECONDS` | `2` | Exponential-backoff multiplier (seconds). |
| `s3_tables_secret` **secret** | (required) | MotherDuck `TYPE S3` secret with the AWS keys. Rename it only if you also change `SECRET_NAME` in `flight.py`. |

Selection precedence: a table is copied only if its namespace passes the namespace gate and its
`namespace.table` passes the table gate; excludes always win.

Selection is forgiving about formats. Entries can be quoted or unquoted (`"clickbench"."hits"` is
the same as `clickbench.hits`), fully qualified or a bare table name, and have surrounding
whitespace. Matching is case-insensitive.

To open the catalog, the attach needs one namespace that exists in the bucket. It comes from
`INCLUDED_SCHEMAS`, else the namespaces named in `INCLUDED_TABLES`, else the sample namespace. Set
`INCLUDED_SCHEMAS` (or `INCLUDED_TABLES`) when pointing at a non-sample bucket.

## Run it Locally

With the secret in place, the Flight needs only a MotherDuck token; it reads no AWS env vars.

```bash
export MOTHERDUCK_TOKEN=your_token_here
# scope it so a first run is cheap (the sample `hits` table is ~100M rows):
export INCLUDED_TABLES=clickbench.probe,clickbench.ptest
uv run --with-requirements requirements.txt flight.py
```

This copies the selected tables into `iceberg_ingest`, one full-refresh `CREATE OR REPLACE` each,
and writes one `flight_tracker` row per table. One log line per table plus a summary; it exits
non-zero if any table failed after retries.

### Deploy as a Flight

Create it with `MD_CREATE_FLIGHT`, passing `name`, `source_code` ([`flight.py`](flight.py)),
`requirements_txt` ([`requirements.txt`](requirements.txt)), and a `config` with at least
`TABLE_BUCKET_ARN` plus any `INCLUDED_*`/`EXCLUDED_*` scoping and `TARGET_DATABASE`. No secret
arguments are needed: the Flight reads a stored `IN MOTHERDUCK` secret, and a MotherDuck token is
attached for you. Run it once with `MD_RUN_FLIGHT`, confirm `flight_tracker` has one row per table,
then add a schedule with `MD_UPDATE_FLIGHT`. Use long-lived IAM keys for scheduled runs.

## Caveats

- Every run copies each selected table in full, so cost tracks table size, not how much changed.
  The sample `hits` table is about 100M rows, so scope runs with `INCLUDED_TABLES` or `INCLUDED_SCHEMAS`.
- A table dropped at the source is not dropped from the target; remove it yourself.

## Learn more

- Flights: the `get_flight_guide` MCP tool. S3 Tables, Iceberg, or secrets: `ask_docs_question`.
- Files here: [`flight.py`](flight.py) and [`requirements.txt`](requirements.txt).
