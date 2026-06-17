---
title: Build Hacker News Models From S3 With dbt
id: dbt-ingestion-s3
description: >-
  Queries a public Hacker News Parquet file in S3 and builds three dbt models on
  top of it, run locally against DuckDB or in the cloud against MotherDuck. Use
  when you want a dbt-on-MotherDuck recipe that reads Parquet/CSV directly from
  object storage without copying it first.
type: example
category: ingestion
features: []
tags: [dbt]
prompt: >-
  I want to build dbt models directly on Parquet/CSV in S3 without copying the data
  first, running locally against DuckDB or in the cloud against MotherDuck. Help me
  adapt the "Build Hacker News Models From S3 With dbt" recipe to my own data and use
  case, using it as a guide:
  https://motherduck.com/docs/cookbook/dbt-ingestion-s3
published_date: 2024-12-11
---

# Build Hacker News Models From S3 With dbt

A small dbt project that reads the public Hacker News Parquet file straight from
S3 as a dbt source, then builds three table models from it. It shows the
MotherDuck pattern of querying object storage in place (no copy step) and running
the same dbt project two ways: locally against a DuckDB file, or in the cloud
against MotherDuck.

## How it works

`models/sources.yml` defines the S3 file as a dbt source using DuckDB's external
location support, so dbt reads the Parquet in place instead of copying it. The
`{name}` placeholder is filled from the table name:

```yaml
sources:
  - name: hn_external
    meta:
      external_location: "s3://us-prd-motherduck-open-datasets/hacker_news/parquet/{name}.parquet"
    tables:
      - name: hacker_news_2024_2025
```

Models reference that source with `{{ source('hn_external', 'hacker_news_2024_2025') }}`,
so swapping the data source is a one-line change in `sources.yml`. The three
models are:

- `top_story_by_comments.sql`: top story per month by comment count, using a
  windowed `ROW_NUMBER()` partition over year/month.
- `duckdb_keyword_mentions.sql`: monthly count of stories mentioning `duckdb` in
  the title or text.
- `top_domains.sql`: top 20 story domains, extracted from the URL with
  `regexp_extract`.

The same project runs unchanged against a local DuckDB file or against
MotherDuck; only the dbt target changes.

## Questions to answer

- What is the source data: which S3/HTTPS path and file, and is it Parquet or CSV?
- Which models are actually needed (keep the three samples, replace them, or select a subset)?
- Target MotherDuck database and schema for the built tables.
- Local DuckDB run or MotherDuck run?
- Is there a MotherDuck account and access token available?

## Caveats

- **The target database must already exist.** dbt does not create it. The `prod`
  target connects to `md:hacker_news_stats`, which fails if the database is
  missing. Run `CREATE DATABASE IF NOT EXISTS ...` first.
- **No `dev` target exists.** `profiles.yml` defines `local` and `prod` only. dbt
  errors on `--target dev`.
- **Swapping to a private bucket needs a secret.** The default S3 dataset is
  public. Pointing `external_location` at a private bucket requires a DuckDB/
  MotherDuck `SECRET`; the project ships none.
- **Do not put a token in source or config.** The `local` and `prod` runs read
  `MOTHERDUCK_TOKEN` from the environment; keep it out of the repo.

## What you'll adjust

| Setting | Purpose | Options / example |
|---|---|---|
| `models/sources.yml` `external_location` | The object-storage path dbt reads as its source. | `s3://us-prd-motherduck-open-datasets/hacker_news/parquet/{name}.parquet`; swap for your own S3/HTTPS Parquet or CSV |
| Source table `name` | Which file under that location to query (`{name}` in the path). | `hacker_news_2024_2025` |
| `models/*.sql` | The three analytical models built from the source. | `top_story_by_comments`, `duckdb_keyword_mentions`, `top_domains`; add or replace your own |
| `dbt_project.yml` `models.+materialized` | How models are persisted. | `table` (default) or `view` |
| `profiles.yml` targets | Local vs cloud destination. | `local` (`local.db` DuckDB file) or `prod` (`md:hacker_news_stats`) |
| `profiles.yml` `prod` `path` | The MotherDuck database dbt builds into. | `md:hacker_news_stats`; create the database first |
| `MOTHERDUCK_TOKEN` (env) | Auth for MotherDuck runs. | a read/write token from your account |

## Run it

Prerequisites: a MotherDuck account and token for cloud runs, and `uv` for the
local Python runtime. The S3 dataset is public, so no AWS credentials are needed.

Local DuckDB run (writes to `local.db`):

```bash
uv run --with dbt-duckdb dbt run --target local
```

MotherDuck run (create the database once, then build):

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with dbt-duckdb dbt run --target prod
```

Create the `prod` database first if it does not exist. dbt does not create the
database for you, and a missing database makes the `prod` run fail:

```sql
CREATE DATABASE IF NOT EXISTS hacker_news_stats;
```

`profiles.yml` ships two targets: `local` (a `local.db` DuckDB file) and `prod`
(`md:hacker_news_stats`). The default target is `local`, so a bare `dbt run`
stays on disk. There is no `dev` target; use `local`.

## Files

- [`models/`](models/): the dbt project content. Three table models ([`top_story_by_comments.sql`](models/top_story_by_comments.sql), [`duckdb_keyword_mentions.sql`](models/duckdb_keyword_mentions.sql), [`top_domains.sql`](models/top_domains.sql)) plus [`sources.yml`](models/sources.yml), which declares the public Hacker News S3 Parquet as a dbt source via DuckDB's `external_location`.
- [`dbt_project.yml`](dbt_project.yml): dbt project config (profile name `dbt_ingestion_s3`, models materialized as `table`).
- [`profiles.yml`](profiles.yml): the two targets, `local` (a `local.db` DuckDB file) and `prod` (`md:hacker_news_stats`).
- [`pyproject.toml`](pyproject.toml): Python project metadata for local `uv run` (pins `dbt-duckdb==1.9.3`).
- [`uv.lock`](uv.lock): resolved lockfile for the local `uv` environment. [`.python-version`](.python-version) pins Python 3.12.
- [`analyses/`](analyses/), [`macros/`](macros/), [`seeds/`](seeds/), [`snapshots/`](snapshots/), [`tests/`](tests/): standard dbt scaffold directories, empty for now (each holds a `.gitkeep`).

## Learn more

- Deeper MotherDuck or DuckDB questions (querying S3, dbt-duckdb behavior): use the
  `ask_docs_question` MCP tool, or see the [dbt-duckdb adapter](https://github.com/duckdb/dbt-duckdb)
  and [MotherDuck docs](https://motherduck.com/docs).
