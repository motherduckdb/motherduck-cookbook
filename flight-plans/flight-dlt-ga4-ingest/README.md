---
title: Ingest Google Analytics 4 into MotherDuck with dlt as a Flight
id: flight-dlt-ga4-ingest
description: >-
  A reusable Flight that runs a dlt pipeline pulling Google Analytics 4 (GA4)
  report data into MotherDuck on a schedule, with Parquet loader files, schema
  evolution, and a run ledger. Use when you want scheduled GA4 reporting data
  (sessions, users, pageviews by dimension) in MotherDuck without hand-writing
  the API calls or INSERTs.
type: template
category: ingestion
features: [flights]
tags: [dlt, ingest, ga4, google-analytics]
---

# Ingest Google Analytics 4 into MotherDuck with dlt as a Flight

A single-file Flight that runs a [dlt](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck)
pipeline pulling **Google Analytics 4 (GA4)** report data into MotherDuck. It is
the GA4 adaptation of [flight-dlt-ingest](../flight-dlt-ingest): the demo
`repo_rows()` source is replaced with a `ga4_rows()` source that calls the GA4
Data API, and the rest of the pattern — dlt-managed schema, Parquet loader files,
a run ledger, and Flight scheduling — is unchanged.

Everything is driven by Flight config, so you adapt it by setting config values,
not by editing `flight.py`. The defaults pull the last 7 days of
sessions/users/pageviews by default channel group from the GA4 property you set
in `GA4_PROPERTY_ID`, and load it into `ga4_ingest.ga4.ga4_report` in your own
account.

## Aggregated reports, not raw events

The GA4 Data API (`runReport`) returns **pre-aggregated report rows** — metrics
like `sessions` and `totalUsers` broken down by dimensions like `date` and
`sessionDefaultChannelGroup`. It **cannot** return raw, event-level data
(individual `event_name` rows, event params, user properties).

- Want **dashboards / KPI reporting**? This template is the right fit.
- Want **raw GA4 events**? Use the native **GA4 → BigQuery export** and ingest
  with [flight-bigquery-ingest](../flight-bigquery-ingest) instead.

## How it works

`flight.py` runs a fixed sequence; the config values only change its inputs:

1. Set `HOME=/tmp` (dlt writes working files under `HOME`, and a Flight has a
   writable `/tmp`) and point the dlt MotherDuck destination at
   `DESTINATION_DATABASE` through an environment variable, so no token is written
   anywhere.
2. Connect to MotherDuck (`md:`) and `CREATE DATABASE IF NOT EXISTS` the
   destination, because dlt creates the dataset and tables but not the database.
3. Build a dlt pipeline and `run()` the `ga4_rows()` source with
   `loader_file_format="parquet"` and the configured write disposition and
   primary key.
4. Append one row to the run ledger capturing the dlt load package summary.

`ga4_rows()` authenticates with a Google service account, calls the GA4 Data API
`runReport` for the configured property, dimensions, metrics, and date range, and
yields one dict per report row. It pages through results in 10k-row pages (the
GA4 per-request cap) until the full `row_count` is read.

## Why this dlt setup

The important default is the load format. For MotherDuck, prefer Parquet loader
files over row-wise `insert_values`, so larger sources stay on a bulk-loading
path. The Flight makes that choice explicit with `loader_file_format="parquet"`.

The second important default is **`merge` on the dimension columns over a moving
lookback window**. GA4 keeps revising recent days as attribution and conversion
windows settle (often for 48 hours to two weeks). Re-pulling `7daysAgo`→`yesterday`
on every run and merging on the dimension columns means a recent day's row gets
*corrected* on later runs instead of frozen wrong (`append` would double-count,
`replace` would discard history).

## Adapt the pattern

- Set `GA4_PROPERTY_ID` to your numeric GA4 property id.
- Choose your grain with `GA4_DIMENSIONS` and `GA4_METRICS`. The dimension list
  is effectively the table's grain and the default merge key, so plan it up front
  — adding a dimension later changes the key.
- Tune the lookback with `GA4_START_DATE` / `GA4_END_DATE`. GA4 accepts relative
  strings (`7daysAgo`, `yesterday`, `today`) as well as `YYYY-MM-DD`.
- Use `WRITE_DISPOSITION=merge` with the dimension columns as `PRIMARY_KEY`
  (default) for self-healing reports; `replace` if you re-pull the full range
  each run; `append` only if you truly want an immutable log of pulls.
- Keep `loader_file_format="parquet"` unless you have measured a reason to change
  it. See the [dlt MotherDuck destination docs](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck).

## Questions to answer

- Which GA4 **property** (numeric id), and which **dimensions/metrics** (the grain)?
- What **lookback window** matches how late your GA4 data settles?
- Target MotherDuck database and dataset (`DESTINATION_DATABASE`, `DATASET_NAME`);
  is letting the Flight create the database acceptable?
- Load behavior: `merge` on the dimensions (default), `replace`, or `append`?
- Which service account token, and is the GA4 service-account key stored as a
  Flights secret (not config)?
- What schedule (cron) should it run on?

## Prerequisites (Google side)

1. **Enable the Google Analytics Data API** in a GCP project.
2. **Create a service account** and download its JSON key.
3. In **GA4 Admin → Property Access Management**, add the service account's email
   as a **Viewer** on the property. (API enabled but service account not granted
   on the property is the most common cause of a `403`.)
4. Note the **numeric GA4 Property ID** (Admin → Property Settings), e.g.
   `123456789` — not the `G-XXXXXXX` measurement id.

## Caveats

- **Aggregated, not raw.** See [Aggregated reports, not raw events](#aggregated-reports-not-raw-events).
- **dlt does not create the database.** It creates the dataset (schema) and
  tables, so the Flight pre-creates `DESTINATION_DATABASE` with
  `CREATE DATABASE IF NOT EXISTS`.
- **`merge` needs a primary key.** With `WRITE_DISPOSITION=merge`, `PRIMARY_KEY`
  defaults to the dimension columns; keep it aligned with your grain or switch to
  `append`/`replace`.
- **Keep source credentials out of config.** The GA4 service-account key is a
  secret. Add it as a MotherDuck **Flights secret** named `GA4_SERVICE_ACCOUNT_JSON`
  (the simplest way is the MotherDuck UI at
  [Settings > Secrets](https://app.motherduck.com/settings/secrets), or
  `CREATE SECRET ... (TYPE flights, ...)` from the DuckDB client), which the
  runtime injects as an env var you read with `os.environ`.
- **Keep the token out of config.** The runtime attaches a MotherDuck token and
  injects it as `MOTHERDUCK_TOKEN`; never place a token in `config`.

## What you'll adjust

Every knob is a config/env value read at the top of `flight.py`. Set them as
Flight config, not by editing code. The source itself lives in the `ga4_rows()`
function.

| Config key | Default | Purpose |
|---|---|---|
| `GA4_PROPERTY_ID` | (required) | Numeric GA4 property id, e.g. `123456789`. Validated as digits. |
| `GA4_DIMENSIONS` | `date,sessionDefaultChannelGroup` | Comma-separated GA4 dimension API names. Defines the grain and default merge key. |
| `GA4_METRICS` | `sessions,totalUsers,screenPageViews` | Comma-separated GA4 metric API names. Loaded as numbers. |
| `GA4_START_DATE` | `7daysAgo` | Report start date. Relative (`NdaysAgo`, `yesterday`, `today`) or `YYYY-MM-DD`. |
| `GA4_END_DATE` | `yesterday` | Report end date. Same formats as `GA4_START_DATE`. |
| `DESTINATION_DATABASE` | `ga4_ingest` | MotherDuck database dlt loads into. Created if missing. Validated as a SQL identifier. |
| `DATASET_NAME` | `ga4` | dlt dataset (schema) that holds the loaded tables. |
| `TABLE_NAME` | `ga4_report` | dlt table name for the loaded rows. |
| `WRITE_DISPOSITION` | `merge` | `merge` (default; merges on `PRIMARY_KEY`), `append`, or `replace`. |
| `PRIMARY_KEY` | `GA4_DIMENSIONS` | Merge key when `WRITE_DISPOSITION=merge`. Defaults to the dimension columns. |
| `PIPELINE_NAME` | `ga4_dlt_ingest` | dlt pipeline name (also used for dlt state). |
| `RUN_LEDGER_TABLE` | `dlt_ingest_runs` | Audit table in the database's `main` schema. Validated as a SQL identifier. |
| `GA4_SERVICE_ACCOUNT_JSON` | (Flights secret) | Service-account key JSON. Store as a Flights **secret**, never in config. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Select a token on the Flight; never put it in config. |

## Run it

You need a MotherDuck account and access token, a GA4 property, and a service
account with Viewer access on that property (see
[Prerequisites](#prerequisites-google-side)).

To smoke-test the pipeline locally before deploying, run the file directly
against your account, supplying the GA4 key inline:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export GA4_SERVICE_ACCOUNT_JSON="$(cat service-account.json)"
GA4_PROPERTY_ID=123456789 uv run --with-requirements requirements.txt flight.py
```

That single run creates the `ga4_ingest` database, loads the last 7 days of the
default report into `ga4.ga4_report`, and writes one ledger row. Override any
default inline, for example:

```bash
GA4_PROPERTY_ID=123456789 \
GA4_DIMENSIONS=date,country,deviceCategory \
GA4_METRICS=sessions,engagedSessions,conversions \
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `ga4_dlt_ingest`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the keys from [What you'll adjust](#what-youll-adjust) you want to
  override (at minimum `GA4_PROPERTY_ID`; omit any you are keeping at default)

Before the first run, add the `GA4_SERVICE_ACCOUNT_JSON` Flights secret (UI:
[Settings > Secrets](https://app.motherduck.com/settings/secrets), or
`CREATE SECRET ... (TYPE flights, ...)`), and select a MotherDuck token on the
Flight (injected at run time as `MOTHERDUCK_TOKEN`).

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm it succeeds and the GA4 tables and ledger
row appear. Once the manual run is green, add a daily schedule (`15 7 * * *`,
07:15 UTC, is a reasonable default) by updating the Flight's `schedule_cron` with
`MD_UPDATE_FLIGHT`. Schedule updates are metadata-only and do not create a new
Flight version.

## Security

- **Identifier validation.** `DESTINATION_DATABASE` and `RUN_LEDGER_TABLE` flow
  into `CREATE`/`INSERT` statements that cannot be parameterized, so each is
  checked against `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL runs.
  `GA4_PROPERTY_ID` is validated as digits.
- **Parameterized data.** The ledger row (pipeline name, dataset, table, and load
  summary) is written with bound parameters, never string-formatted into SQL.
- **Secret handling.** The GA4 service-account key is read from the
  `GA4_SERVICE_ACCOUNT_JSON` env var supplied by a Flights secret; it is never
  written to disk by the Flight or placed in config.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- dlt sources, write dispositions, and the MotherDuck destination:
  [dlt MotherDuck destination docs](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck).
- GA4 Data API dimensions and metrics:
  [GA4 Dimensions & Metrics reference](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema).
- The base template this adapts: [flight-dlt-ingest](../flight-dlt-ingest).
- For raw event-level GA4 data: [flight-bigquery-ingest](../flight-bigquery-ingest)
  via the GA4 → BigQuery export.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (`duckdb`, `dlt[motherduck]`,
  `google-analytics-data`).
