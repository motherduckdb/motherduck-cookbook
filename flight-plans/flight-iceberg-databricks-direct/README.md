---
title: Transform Databricks Iceberg on MotherDuck, Write Straight Back to Iceberg
id: flight-iceberg-databricks-direct
description: >-
  A reusable Flight that reads an Iceberg table from Databricks Unity Catalog,
  transforms it on MotherDuck's engine, and writes the result straight back to
  Iceberg with nothing persisted in MotherDuck. Use when you want MotherDuck as
  transform compute over your open lakehouse and the output only needs to live in
  Iceberg.
type: template
category: integrations
features: [flights]
tags: [databricks]
prompt: >-
  I keep my lakehouse in Databricks Unity Catalog and want a scheduled Flight that
  reads an Iceberg table, transforms it on MotherDuck, and writes the result straight
  back to Iceberg without staging a copy in MotherDuck. Help me adapt the "Transform
  Databricks Iceberg on MotherDuck, Write Straight Back to Iceberg" recipe to my own
  data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-iceberg-databricks-direct
published_date: 2026-07-20
---

# Transform Databricks Iceberg on MotherDuck, Write Straight Back to Iceberg

A single-file Flight that reads an Iceberg table in Databricks Unity Catalog (UC),
transforms it on MotherDuck's engine, and writes the result **straight back to
Iceberg** in the same catalog. Nothing is persisted in MotherDuck: MotherDuck is
the transform compute, and the open lake stays the system of record. The story is
"keep your Databricks lakehouse, run the transform compute on MotherDuck, write
back to the same open tables."

The default transform is a daily usage rollup (`usage_events_raw` to
`usage_daily_rollup`), so a deploy against that schema produces a successful run
you can then point at your own tables and transform.

> **Staging variant.** If you also want the result to live in a MotherDuck table
> (to query, share, or build Dives on) before it reaches Iceberg, use the
> companion [Transform Databricks Iceberg via a MotherDuck Staging Table](https://motherduck.com/docs/cookbook/flight-iceberg-databricks-stage)
> recipe instead. This one is the leaner, no-copy path.

## How it works

`flight.py` runs a fixed sequence; config values change its inputs:

1. Connect to MotherDuck (`md:`), `INSTALL`/`LOAD iceberg`, and idempotently
   attach the Databricks UC Iceberg REST catalog using a MotherDuck secret.
2. Recreate the target Iceberg table (`DROP` + `CREATE` with an explicit schema).
3. `INSERT ... SELECT` the transform, reading the source Iceberg table and writing
   directly into the target Iceberg table.
4. Log source and output row counts.

The transform is a plain `SELECT`, so adapt it by replacing the `SELECT` and the
matching `CREATE TABLE` column list (keep the two in sync).

## Questions to answer

- What is your UC Iceberg REST endpoint (`ICEBERG_ENDPOINT`) and warehouse (`ICEBERG_WAREHOUSE`)?
- Which secret holds the Databricks token (`ICEBERG_SECRET`)?
- Which schema and tables (`ICEBERG_SCHEMA`, `SOURCE_TABLE`, `TARGET_TABLE`)?
- What is the transform, and does the target's explicit column list match it?
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
- **`DROP`/`CREATE` on each run.** The target is fully replaced every run, which
  suits an idempotent rollup. For incremental or append semantics, change the write
  step.
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
| `TARGET_TABLE` | `usage_daily_rollup` | Target Iceberg table (fully replaced each run). Validated as an identifier. |
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

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT`, passing `source_code` from
[`flight.py`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-iceberg-databricks-direct/flight.py),
`requirements_txt` from [`requirements.txt`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-iceberg-databricks-direct/requirements.txt),
and the `config` keys you want to override. A MotherDuck token is attached
automatically and injected as `MOTHERDUCK_TOKEN`; no token argument is needed. Run
once manually with `MD_RUN_FLIGHT`, confirm it succeeds, then add a schedule with
`MD_UPDATE_FLIGHT` (schedule updates are metadata-only).

## Security

- **Identifier validation.** The catalog, secret name, schema, and table names flow
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
- Files in this template: [`flight.py`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-iceberg-databricks-direct/flight.py)
  and [`requirements.txt`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/flight-plans/flight-iceberg-databricks-direct/requirements.txt).
