---
title: Run a dlt Ingest Pipeline as a Flight
id: flight-dlt-ingest
description: >-
  A reusable Flight that runs a dlt pipeline into MotherDuck on a schedule, with
  Parquet loader files, schema evolution, and a run ledger. Use when you want
  Python ingestion that handles API calls, schema drift, state, and merge
  behavior without hand-writing every INSERT.
type: template
category: ingestion
features: [flights]
tags: [dlt, ingest]
---

# Run a dlt Ingest Pipeline as a Flight

A single-file Flight that runs a [dlt](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck)
pipeline into MotherDuck. It shows the MotherDuck pattern for Python ingestion
that handles API calls, schema drift, state, and load packages without
hand-writing every `INSERT`: dlt manages the schema and load, and the Flight
schedules it and records each run.

Everything is driven by Flight config, so you adapt it by setting config values,
not by editing `flight.py`. The defaults fetch public GitHub repository metadata
and load it into `flights_demo_dlt.github_repo_stats` in your own account, so a
fresh deploy produces a successful run. You adapt it by replacing the demo source
function and pointing the destination at your data.

## How it works

`flight.py` runs a fixed sequence; the config values only change its inputs:

1. Set `HOME=/tmp` (dlt writes working files under `HOME`, and a Flight has a
   writable `/tmp`) and point the dlt MotherDuck destination at
   `DESTINATION_DATABASE` through an environment variable, so no token is written
   anywhere.
2. Connect to MotherDuck (`md:`) and `CREATE DATABASE IF NOT EXISTS` the
   destination, because dlt creates the dataset and tables but not the database.
3. Build a dlt pipeline and `run()` the source with `loader_file_format="parquet"`
   and the configured write disposition and primary key.
4. Append one row to the run ledger capturing the dlt load package summary.

## Why this dlt setup

The important default is the load format. For MotherDuck, prefer Parquet loader
files over row-wise `insert_values`, so larger sources stay on a bulk-loading
path. The Flight makes that choice explicit with `loader_file_format="parquet"`.

Use this dlt pattern when you want schema evolution, state tracking, merge
behavior, or a ready-made source connector. If you already have clean Parquet
files in S3, the [flight-scheduled-s3-ingest](../flight-scheduled-s3-ingest)
template is simpler. If you only have a few hundred rows of control metadata,
direct inserts are fine.

## Adapt the pattern

- Replace `repo_rows()` with a dlt source for your API, database, or filesystem.
- Keep `DESTINATION_DATABASE` pointed at the database where dlt should create the
  dataset.
- Use `WRITE_DISPOSITION=merge` with a `PRIMARY_KEY` for entity tables, and
  `append` for event streams.
- Keep `loader_file_format="parquet"` unless you have measured a reason to change
  it.
- Lower dlt load workers if a source or network path is unreliable. See the
  [dlt MotherDuck destination docs](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck).

## Questions to answer

- What is the real source: which dlt source, API, database, or filesystem replaces `repo_rows()`?
- Target MotherDuck database and dataset (`DESTINATION_DATABASE`, `DATASET_NAME`); is letting the Flight create the database acceptable?
- Load behavior: `merge` with a `PRIMARY_KEY` for entity tables, or `append` for event streams?
- Which service account token, and how are any source credentials kept out of config?
- What schedule (cron) should it run on?

## Caveats

- **dlt does not create the database.** It creates the dataset (schema) and tables,
  so the Flight pre-creates `DESTINATION_DATABASE` with `CREATE DATABASE IF NOT EXISTS`.
- **`merge` needs a primary key.** With `WRITE_DISPOSITION=merge`, set `PRIMARY_KEY`
  to the column that identifies a row; otherwise use `append` or `replace`.
- **Keep source credentials out of config.** Flight config is for non-secret
  values. Add a private source's credentials as a MotherDuck **Flights secret**
  (the simplest way is the MotherDuck UI at
  [Settings > Secrets](https://app.motherduck.com/settings/secrets), or
  `CREATE SECRET ... (TYPE flights, ...)` from the DuckDB client), which the
  runtime injects as env vars you read with `os.environ`.
- **Keep the token out of config.** The runtime attaches a MotherDuck token and
  injects it as `MOTHERDUCK_TOKEN`; never place a token in `config`.

## What you'll adjust

Every knob is a config/env value read at the top of `flight.py`. Set them as
Flight config, not by editing code. The demo source itself lives in the
`repo_rows()` function, which you replace with your own dlt source.

| Config key | Default | Purpose |
|---|---|---|
| `DESTINATION_DATABASE` | `flights_demo` | MotherDuck database dlt loads into. Created if missing. Validated as a SQL identifier. |
| `DATASET_NAME` | `flights_demo_dlt` | dlt dataset (schema) that holds the loaded tables. |
| `TABLE_NAME` | `github_repo_stats` | dlt table name for the loaded rows. |
| `WRITE_DISPOSITION` | `merge` | `merge` (needs a primary key), `append` (event streams), or `replace`. |
| `PRIMARY_KEY` | `repo` | Merge key used when `WRITE_DISPOSITION` is `merge`. |
| `PIPELINE_NAME` | `flights_dlt_ingest` | dlt pipeline name (also used for dlt state). |
| `GITHUB_REPOS` | `duckdb/duckdb,motherduckdb/motherduck-docs,dlt-hub/dlt` | Comma-separated repos the demo source fetches. |
| `RUN_LEDGER_TABLE` | `dlt_ingest_runs` | Audit table in the database's `main` schema. Validated as a SQL identifier. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Select a token on the Flight; never put it in config. |

## Run it

You need a MotherDuck account and an access token. The default source is a public
GitHub API call, so no extra credentials are needed. A private source should read
its credentials from a MotherDuck secret or another short-lived source, never from
Flight config.

To smoke-test the pipeline locally before deploying, run the file directly against
your account:

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with-requirements requirements.txt flight.py
```

That single run creates the `flights_demo` database, loads the demo repos into
`flights_demo_dlt.github_repo_stats`, and writes one ledger row. Override any
default inline, for example `GITHUB_REPOS=duckdb/duckdb uv run --with-requirements requirements.txt flight.py`.

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `dlt_ingest`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the keys from [What you'll adjust](#what-youll-adjust) you want to
  override (omit any you are keeping at default)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm it succeeds and the dlt tables and ledger
row appear. Once the manual run is green, add a daily schedule (`15 7 * * *`,
07:15 UTC, is a reasonable default) by updating the Flight's `schedule_cron` with
`MD_UPDATE_FLIGHT`. Schedule updates are metadata-only and do not create a new
Flight version.

## Security

- **Identifier validation.** `DESTINATION_DATABASE` and `RUN_LEDGER_TABLE` flow
  into `CREATE`/`INSERT` statements that cannot be parameterized, so each is
  checked against `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL runs.
- **Parameterized data.** The ledger row (pipeline name, dataset, table, and load
  summary) is written with bound parameters, never string-formatted into SQL.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- dlt sources, write dispositions, and the MotherDuck destination:
  [dlt MotherDuck destination docs](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck).
- Deeper MotherDuck or DuckDB questions: use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (`duckdb`, `dlt[motherduck]`, `httpx`).
