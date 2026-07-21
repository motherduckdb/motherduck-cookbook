---
title: Transform Databricks Iceberg via a MotherDuck Staging Table
id: flight-iceberg-databricks-stage
description: >-
  A reusable Flight that reads an Iceberg table from Databricks Unity Catalog,
  transforms and materializes the result in a MotherDuck table, then publishes
  that table back to Iceberg. Use when you want the curated result to live in
  MotherDuck (to query, share, or build Dives on) as well as in the open lake.
type: template
category: integrations
features: [flights]
tags: [databricks]
prompt: >-
  I keep my lakehouse in Databricks Unity Catalog and want a scheduled Flight that
  reads an Iceberg table, transforms it into a MotherDuck table I can query and share,
  and then publishes that table back to Iceberg. Help me adapt the "Transform Databricks
  Iceberg via a MotherDuck Staging Table" recipe to my own data and use case, using it
  as a guide:
  https://motherduck.com/docs/cookbook/flight-iceberg-databricks-stage
published_date: 2026-07-20
---

# Transform Databricks Iceberg via a MotherDuck Staging Table

A single-file Flight that reads an Iceberg table in Databricks Unity Catalog (UC),
transforms and **materializes the result in a MotherDuck table**, then publishes
that table back to Iceberg. You get the curated result in two places: a MotherDuck
table you can query, share, and build Dives on, and the open Iceberg lake as the
system of record.

This is the staging counterpart to the [direct](https://motherduck.com/docs/cookbook/flight-iceberg-databricks-direct)
recipe. Use this one when the result should be usable inside MotherDuck (fast
reads, sharing, Dives, BI) or when you want to inspect or validate the rollup
before it reaches the shared lake; use the direct one when the output only needs
to live in Iceberg and you would rather not keep a copy.

The default transform is a daily usage rollup (`usage_events_raw` to
`usage_daily_rollup`), so a deploy against that schema produces a successful run
you can then point at your own tables and transform.

## How it works

`flight.py` runs a fixed sequence; config values change its inputs:

1. Connect to MotherDuck (`md:`), `INSTALL`/`LOAD iceberg`, and idempotently
   attach the Databricks UC Iceberg REST catalog using a MotherDuck secret.
2. **Stage:** `CREATE OR REPLACE TABLE` the transform result into a MotherDuck
   table (reading the source Iceberg table, transforming on MotherDuck's engine).
3. **Publish:** recreate the target Iceberg table and `INSERT ... SELECT * FROM`
   the MotherDuck staging table into it. Set `PUBLISH_TO_ICEBERG=false` to stage
   in MotherDuck only.
4. Log staged and published row counts.

The transform is a plain `SELECT`, so adapt it by replacing the `SELECT` in the
stage step and the matching `CREATE TABLE` column list in the publish step.

## Questions to answer

- What is your UC Iceberg REST endpoint (`ICEBERG_ENDPOINT`) and warehouse (`ICEBERG_WAREHOUSE`)?
- Which secret holds the Databricks token (`ICEBERG_SECRET`)?
- Which schema and tables (`ICEBERG_SCHEMA`, `SOURCE_TABLE`, `TARGET_TABLE`)?
- Where should the MotherDuck copy live (`MD_DATABASE`, `MD_SCHEMA`, `MD_TABLE`)?
- What is the transform, and does the Iceberg target's explicit column list match it?
- What schedule (cron) should it run on?

## Caveats

These are the setup gotchas that make Databricks UC Iceberg writes work from an
external engine. They cost hours the first time; get them right before deploying.

- **The token needs `all-apis` scope.** A scope-limited OAuth token 403s at
  `/v1/config`. A classic Databricks PAT with `all-apis` works. Store it as a
  MotherDuck **Iceberg-catalog secret** and pass the secret name via `ICEBERG_SECRET`.
- **Writes need credential vending, and the managed catalog does not vend.** The
  default managed `workspace` catalog will not vend credentials to external engines.
  You need a UC **storage credential + external location** (an S3 bucket with a
  Databricks-provisioned IAM role via the AWS delegation flow), `EXTERNAL USE SCHEMA`,
  and external data access enabled. Create **native Iceberg tables** in a schema on
  that external location: those vend. Pre-existing Delta-as-Iceberg tables are
  read-only and will not vend, so you cannot write to them.
- **The AWS "60-minute temporary access" delegation screen provisions a persistent
  IAM role.** Vending keeps working after the 60 minutes; that screen is a one-time
  setup step, not a time limit on the integration.
- **Pin DuckDB.** Server-side Iceberg needs DuckDB >= 1.5.2, and the Flights runtime
  otherwise pulls the latest. This template pins `duckdb==1.5.4`.
- **The staged MotherDuck table is fully replaced each run** (`CREATE OR REPLACE`),
  as is the Iceberg target. That suits an idempotent rollup; change both writes for
  incremental or append semantics.
- **Reads only? Staging still needs vending.** Reading data files from UC also needs
  vending, so the same external-location setup applies even if you set
  `PUBLISH_TO_ICEBERG=false`.
- **Keep the token out of config.** The Databricks credential lives in the secret;
  a MotherDuck token is injected as `MOTHERDUCK_TOKEN` at runtime. Never put either
  in `config`.

## What you'll adjust

| Config key | Default | Purpose |
|---|---|---|
| `ICEBERG_ENDPOINT` | (required) | UC Iceberg REST endpoint, e.g. `https://<host>/api/2.1/unity-catalog/iceberg-rest`. |
| `ICEBERG_WAREHOUSE` | `workspace` | UC catalog/warehouse name. |
| `ICEBERG_SECRET` | `databricks_token` | Name of the MotherDuck secret holding the `all-apis` token. Validated as an identifier. |
| `ICEBERG_CATALOG` | `databricks_iceberg` | Local attach name for the catalog. Validated as an identifier. |
| `ICEBERG_DEFAULT_SCHEMA` | `default` | `default_schema` for the attach. |
| `ICEBERG_SCHEMA` | `md_iceberg_demo` | Working schema in the catalog. Validated as an identifier. |
| `SOURCE_TABLE` | `usage_events_raw` | Source Iceberg table. Validated as an identifier. |
| `TARGET_TABLE` | `usage_daily_rollup` | Target Iceberg table (fully replaced when publishing). Validated as an identifier. |
| `MD_DATABASE` | `flights_demo` | MotherDuck database for the staged table. Created if missing. Validated as an identifier. |
| `MD_SCHEMA` | `main` | MotherDuck schema for the staged table. Validated as an identifier. |
| `MD_TABLE` | `usage_daily_rollup` | MotherDuck staging table name. Validated as an identifier. |
| `PUBLISH_TO_ICEBERG` | `true` | Set `false` to stage in MotherDuck only and skip the Iceberg write. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Select a token on the Flight; never put it in config. |

## Run it

You need a MotherDuck account and token, a Databricks UC catalog reachable over the
Iceberg REST endpoint, and a MotherDuck Iceberg-catalog secret with an `all-apis`
token. To smoke-test before deploying:

```bash
export MOTHERDUCK_TOKEN=your_token_here
ICEBERG_ENDPOINT='https://<host>/api/2.1/unity-catalog/iceberg-rest' \
  uv run --with duckdb==1.5.4 flight.py
```

Set `PUBLISH_TO_ICEBERG=false` on the first run to build the MotherDuck staging
table and inspect it before writing anything to Iceberg.

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT`, passing `source_code` from
[`flight.py`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-iceberg-databricks-stage/flight.py),
`requirements_txt` from [`requirements.txt`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-iceberg-databricks-stage/requirements.txt),
and the `config` keys you want to override. A MotherDuck token is attached
automatically and injected as `MOTHERDUCK_TOKEN`; no token argument is needed. Run
once manually with `MD_RUN_FLIGHT`, confirm it succeeds, then add a schedule with
`MD_UPDATE_FLIGHT` (schedule updates are metadata-only).

## Security

- **Identifier validation.** The catalog, secret name, schemas, and table names flow
  into `ATTACH`/`CREATE`/`INSERT` statements that cannot be parameterized, so each is
  checked against `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL runs. The endpoint,
  warehouse, and default schema are inlined as escaped string literals.
- **Credential in a secret.** The Databricks token never appears in code or config;
  it is referenced by secret name and read by the engine at attach time.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions (server-side Iceberg, UC credential vending):
  use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-iceberg-databricks-stage/flight.py)
  and [`requirements.txt`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-iceberg-databricks-stage/requirements.txt).
