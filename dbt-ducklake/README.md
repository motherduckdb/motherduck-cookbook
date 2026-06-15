---
title: Run TPC-DS Models on DuckLake with dbt
id: dbt-ducklake
description: >-
  Loads the TPC-DS benchmark dataset into a DuckLake-backed database with dbt,
  then materializes the 99 TPC-DS analytical queries into a separate MotherDuck
  database. Use when you want a dbt project that lands raw tables in DuckLake
  storage and writes analytics models to native MotherDuck.
type: example
category: analytics
features: [ducklake]
tags: [dbt]
---

# Run TPC-DS Models on DuckLake with dbt

This dbt project loads TPC-DS Scale Factor 100 parquet from public S3 into a
DuckLake-backed database (`dbt_ducklake`), then runs the standard 99 TPC-DS
analytical queries and materializes their results into a second MotherDuck
database (`my_db`). It shows the MotherDuck pattern of splitting a project across
two storage layers: raw tables in DuckLake (so the parquet lives in your own
object store) and downstream analytics in native MotherDuck storage.

## Architecture

Two model layers, two storage backends:

| Layer | Path | Storage | Materialization | Schema/tag |
|---|---|---|---|---|
| Raw (`models/tpcds/raw/`) | reads S3 parquet via dbt sources | DuckLake database `dbt_ducklake` | `table` | schema `raw`, tag `raw` |
| Queries (`models/tpcds/queries/`) | references the raw tables | native MotherDuck database `my_db` | `table` | tag `queries` |

```
models/tpcds/
  raw/          # 25 TPC-DS base tables -> DuckLake (schema `raw`)
    _sources.yml
    customer.sql, store_sales.sql, catalog_sales.sql, web_sales.sql, ...
  queries/      # 99 TPC-DS analytical queries -> MotherDuck `my_db`
    query_1.sql ... query_99.sql
```

The 25 raw tables are: `call_center`, `catalog_page`, `catalog_returns`,
`catalog_sales`, `customer_address`, `customer_demographics`, `customer`,
`date_dim`, `household_demographics`, `income_band`, `inventory`, `item`,
`promotion`, `reason`, `ship_mode`, `store_returns`, `store_sales`, `store`,
`time_dim`, `warehouse`, `web_page`, `web_returns`, `web_sales`, `web_site`.

## How it works

The raw models are intentionally thin: each one just selects from a dbt source.

```sql
-- models/tpcds/raw/customer.sql
from {{ source("tpc-ds", "customer") }}
```

The source is defined once in `models/tpcds/raw/_sources.yml`, where
`meta.external_location` tells the dbt-duckdb adapter to read parquet straight
from S3, substituting each table name into `{name}`:

```yaml
sources:
  - name: tpc-ds
    meta:
      external_location: |-
        s3://devrel-test-data/tpcds/sf100/{name}.parquet
    tables:
      - name: customer
      - name: store_sales
      # ... 25 tables total
```

Because the `motherduck` target sets `is_ducklake: true`, those raw tables are
materialized into the DuckLake-backed `dbt_ducklake` database, so the underlying
parquet lands in your own `DATA_PATH` object store. The 99 query models then
reference the raw tables with `{{ ref(...) }}` and, via the
`tpcds.queries.+database: my_db` config in `dbt_project.yml`, write their results
into the separate native MotherDuck database `my_db`.

`profiles.yml` also ships a `local` target (`path: ducklake:local_dev.db`) for
developing against a local DuckLake file instead of MotherDuck; switch with
`dbt build --target local`.

## Questions to answer

- Source: keep the public `devrel-test-data` TPC-DS bucket, or point at your own parquet and scale factor?
- DuckLake database: which database name and `DATA_PATH` (S3/object-store prefix) should hold the raw tables?
- Analytics target: which native MotherDuck database should the query models land in (default `my_db`)?
- Scope: run all 99 queries plus 25 raw tables, or a subset via `--select` / `--exclude`?
- Full vs partial: rebuild raw tables every run, or only refresh the query layer?
- Credentials: MotherDuck token, and read access to the S3 source bucket.

## Caveats

- Create the DuckLake database before the first run. `CREATE DATABASE dbt_ducklake
  (TYPE ducklake, DATA_PATH '...')` and `my_db` must already exist; dbt does not
  create them. A missing `dbt_ducklake` fails the run, and a missing `my_db`
  fails the query layer.
- `is_ducklake: true` is required, not optional. Without it, the raw tables
  silently write to native MotherDuck storage instead of your `DATA_PATH` object
  store, defeating the point of this example. There is no error, just the wrong
  storage backend.
- `profiles.yml`'s profile name must match the `profile:` key in `dbt_project.yml`
  (`dbt_ducklake`). A mismatch makes dbt fail to find a profile.
- TPC-DS Scale Factor 100 is large. A full `dbt build` materializes 25 raw tables
  plus 99 query tables and can take a while and consume meaningful memory. For
  iteration, scope with `--select tag:raw` / `--select tag:queries` or a single
  model (`--select query_1`).
- The S3 source bucket needs read access. The public `devrel-test-data` bucket is
  readable without credentials; if you swap in your own bucket, configure object
  storage credentials (a MotherDuck/DuckDB secret) or the raw layer fails.

## What you'll adjust

| Setting | Purpose | Options / example |
|---|---|---|
| `profiles.yml` `path` (target `motherduck`) | DuckLake database dbt builds raw tables into | `md:dbt_ducklake`; create it first as a DuckLake DB with your own `DATA_PATH` |
| `profiles.yml` `is_ducklake` | Marks the target as DuckLake-backed | `true` (required; see Caveats) |
| `profiles.yml` `schema` / `threads` | Default schema and parallelism | `test` / `4` |
| `dbt_project.yml` `models.dbt_ducklake.tpcds.queries.+database` | Where the 99 query models land | `my_db` (a separate native MotherDuck database) |
| `dbt_project.yml` raw/queries `+materialized`, `+schema`, `+tags` | Materialization and tagging per layer | raw: `table` in schema `raw`; queries: `table` |
| `models/tpcds/raw/_sources.yml` `meta.external_location` | S3 location of the TPC-DS parquet | `s3://devrel-test-data/tpcds/sf100/{name}.parquet`; swap for your own bucket or scale factor |
| dbt selector (`--select` / `--exclude`) | Limit which models run | e.g. `tag:raw`, `tag:queries`, a single `query_1` |

## Run it

Prerequisites: a MotherDuck account and token, plus a `dbt_ducklake` database
created as a DuckLake database with your own `DATA_PATH`, and a `my_db` database
for the analytics models. Create the DuckLake database first, for example:

```sql
CREATE DATABASE dbt_ducklake (TYPE ducklake, DATA_PATH 's3://your-bucket/dbt_ducklake/');
CREATE DATABASE IF NOT EXISTS my_db;
```

Then build:

```bash
uv sync
uv run dbt build
```

This creates the 25 TPC-DS raw tables in DuckLake from S3 parquet and runs the
99 analytical query models into `my_db`. Your browser prompts for MotherDuck
authentication unless a token is configured (set `MOTHERDUCK_TOKEN` in the
environment to run non-interactively).

TPC-DS Scale Factor 100 is heavy. For iteration, scope the run with
`--select tag:raw`, `--select tag:queries`, or a single model (`--select query_1`).

## Files

- [`dbt_project.yml`](dbt_project.yml) - dbt project config: profile name `dbt_ducklake`, plus the per-layer settings that send raw models to schema `raw` (tag `raw`) and the query models to database `my_db` (tag `queries`).
- [`profiles.yml`](profiles.yml) - the dbt profile: a `motherduck` target (`md:dbt_ducklake`, `is_ducklake: true`) and a `local` target (`ducklake:local_dev.db`) for offline development.
- [`models/tpcds/raw/`](models/tpcds/raw/) - the 25 thin raw models (one `select` per TPC-DS base table) materialized into DuckLake, plus `_sources.yml` which points the dbt-duckdb adapter at the S3 parquet via `meta.external_location`.
- [`models/tpcds/queries/`](models/tpcds/queries/) - the 99 standard TPC-DS analytical query models (`query_1.sql` through `query_99.sql`) that `ref` the raw tables and land in `my_db`.
- [`pyproject.toml`](pyproject.toml) - local project deps for `uv sync` / `uv run dbt` (dbt-core, dbt-duckdb, duckdb), Python 3.12+. [`uv.lock`](uv.lock) pins the resolved versions.
- [`.python-version`](.python-version) - pins the local Python version (3.12) for uv.
- [`.user.yml`](.user.yml) / [`.gitignore`](.gitignore) - dbt anonymous-usage user id, and ignore rules for `target/`, `dbt_packages/`, `logs/`, and `*.db`.
- [`analyses/`](analyses/), [`macros/`](macros/), [`seeds/`](seeds/), [`snapshots/`](snapshots/), [`tests/`](tests/) - empty standard dbt scaffold directories (each holds a `.gitkeep`).

## Learn more

- `dbt_project.yml` splits the two storage layers; `profiles.yml` holds the
  `motherduck` and `local` targets; `models/tpcds/raw/_sources.yml` defines the S3
  source location.
- DuckLake setup, `DATA_PATH`, and deeper MotherDuck/DuckDB questions: use the
  `ask_docs_question` MCP tool.
