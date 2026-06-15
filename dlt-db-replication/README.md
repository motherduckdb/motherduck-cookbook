---
title: Replicate PostgreSQL Tables to MotherDuck with dlt
id: dlt-db-replication
description: >-
  A dlt pipeline that extracts a list of PostgreSQL tables in parallel and loads
  them into MotherDuck, with per-run timing metrics. Use when you need to copy or
  refresh tables from a source SQL database into MotherDuck and want tunable
  extract, normalize, and load parallelism.
type: example
features: []
tags: [dlt, postgres, connectorx]
---

# Replicate PostgreSQL Tables to MotherDuck with dlt

This example uses [dlt](https://dlthub.com/) to replicate a configured set of PostgreSQL tables into MotherDuck. It reads the source connection and the table list from `.dlt/` config, extracts tables in parallel through dlt's `sql_database` source (ConnectorX backend, Parquet interim storage), and loads them into a MotherDuck dataset with `write_disposition="replace"` (full refresh: each table is dropped and recreated every run). The MotherDuck pattern it shows is bulk loading from an external relational database via dlt's MotherDuck destination, plus a helper that logs extract, normalize, and load timings per run.

## How it works

`sql_database_pipeline.py` is the entry point. It builds a MotherDuck pipeline, reads the table list from config, validates it, then constructs a parallelized ConnectorX source restricted to those tables and runs it as a full refresh:

```python
pipeline = dlt.pipeline(
    pipeline_name="pg2md", destination="motherduck", dataset_name="pg2md_data"
)

tables = dlt.config.get("sources.sql_database.tables")
if not tables:
    raise ValueError(
        "No tables configured in .dlt/config.toml under [sources.sql_database.tables]"
    )

source = sql_database(backend="connectorx").parallelize().with_resources(*tables)
pipeline.run(source, write_disposition="replace")
```

`.with_resources(*tables)` is what scopes the source to the configured list; without it dlt would reflect and load the entire schema. `write_disposition="replace"` drops and recreates each target table on every run, so it is idempotent but not incremental. For incremental loads switch to `merge` (needs a primary key) or `append` (needs a cursor field); see the dlt incremental loading guide linked below.

`timing_logs.py` reads the dlt trace after the run. `print_pipeline_metrics()` pulls durations and row counts for the overall run and the extract, normalize, and load stages from `pipeline.last_trace`, and `configure_logger()` sets up a dedicated `pipeline_metrics` logger.

## Configuration notes

`.dlt/config.toml` holds every non-secret knob. A few sections deserve attention:

- `[sources.sql_database] workers` and `[postgres] pool_size` should stay equal. They are both `6`. The pool must be large enough for the extraction workers, or connections will queue and stall.
- `[extract] / [normalize] / [load] workers` (`8` / `4` / `4`) tune each pipeline stage independently. These are separate from the source `workers` above.
- `[destination.motherduck] batch_size = 1000000` trades memory for throughput. Large batches load faster but hold more in memory.
- `[data_writer] format = "parquet"` is the interim format dlt writes before loading. Parquet gives good compression and load performance.

## Questions to answer

- Source database: which PostgreSQL host, database, and schema?
- Which tables to replicate, and is the list stable or changing often?
- Load strategy: full refresh (`replace`, current default) or incremental (`merge`/`append` with a cursor/primary key)?
- Target MotherDuck database and dataset (schema) name?
- Expected data volume, so extract/normalize/load workers and `batch_size` can be tuned?
- Credentials: PostgreSQL username/password and a MotherDuck access token, and where they should live (`secrets.toml` vs environment).
- How often should this run, and from where (local, CI, an orchestrator)?

## Caveats

- **`secrets.toml` is required and gitignored.** It is not committed and does not exist until you create it. A missing or partial file fails the run; do not put the MotherDuck token or PostgreSQL password in `config.toml`, which is committed.
- **Full refresh by default.** `write_disposition="replace"` drops and recreates every listed table on each run. It does not preserve history or do change data capture. Switch to `merge`/`append` for incremental loads.
- **No tables configured raises early.** If `[sources.sql_database.tables]` is empty or missing, the pipeline raises `ValueError` before connecting. This is intentional, so the table list must be set in `config.toml`.
- **A stale `table` key is in `config.toml`.** Alongside the real `tables` list there is a leftover singular `table = ["call_center"]` entry marked deprecated. The code reads `tables` (plural) only; ignore or delete the `table` key so you don't edit the wrong one.
- **ConnectorX is version-pinned.** `connectorx<0.4.2` is a hard upper bound. Loosening it can break extraction.
- **Metrics ignore `[runtime] log_level`.** `print_pipeline_metrics` logs through its own `pipeline_metrics` logger at `INFO` with `propagate=False`, so the metrics summary always prints even though `config.toml` sets the dlt runtime `log_level` to `WARNING`. The two log levels are independent; raising or lowering `[runtime] log_level` will not silence or surface the metrics block.
- **Worker/pool mismatch stalls extraction.** Setting `[sources.sql_database] workers` higher than `[postgres] pool_size` exhausts the connection pool. Keep them equal.
- **Memory pressure under heavy load.** Large `batch_size` plus high worker counts can cause out-of-memory errors on big tables. Reduce `batch_size` or worker counts if you hit OOM.
- **Connection failures.** If extraction cannot reach the source, verify the PostgreSQL credentials in `secrets.toml`, the `host`/`port`, and network reachability from where the pipeline runs.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `[sources.sql_database.credentials]` in `.dlt/secrets.toml` | Source PostgreSQL connection | `drivername` (`postgresql`), `database`, `host`, `port` (5432), `username`, `password` |
| `[destination.motherduck.credentials] token` in `.dlt/secrets.toml` | MotherDuck auth token for the destination | your MotherDuck access token |
| `[sources.sql_database] schema` in `.dlt/config.toml` | Source schema to read tables from | e.g. `my_pg`, `public` |
| `[sources.sql_database] tables` in `.dlt/config.toml` | Which tables to replicate (read by `dlt.config.get("sources.sql_database.tables")` in `sql_database_pipeline.py`) | list of table names, e.g. `["customer", "store_sales"]` |
| `pipeline_name` / `dataset_name` in `sql_database_pipeline.py` | Pipeline id and target MotherDuck dataset (schema) | `pg2md` / `pg2md_data` |
| `write_disposition` in `sql_database_pipeline.py` `pipeline.run(...)` | Load strategy | `replace` (full refresh, current), `append`, or `merge` (incremental) |
| `[sources.sql_database] workers` and `[postgres] pool_size` in `.dlt/config.toml` | Source extraction parallelism and matching connection pool | both `6` by default; keep them equal |
| `[extract] / [normalize] / [load] workers` in `.dlt/config.toml` | Per-stage parallelism | `8` / `4` / `4` |
| `[destination.motherduck] batch_size` in `.dlt/config.toml` | Rows per load batch (memory vs throughput) | `1000000` |
| `[data_writer] format` in `.dlt/config.toml` | Interim file format | `parquet` |
| `[runtime] log_level` in `.dlt/config.toml` | dlt log verbosity (does NOT control the metrics output) | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

## Run it

Prerequisites: Python 3.11+, a reachable PostgreSQL source, and a MotherDuck account plus access token.

Dependencies (declared in `pyproject.toml`, resolved by `uv`):

- `dlt[motherduck]>=1.7.0`: dlt core plus the MotherDuck destination.
- `connectorx<0.4.2`: fast extraction backend for PostgreSQL. The upper bound is deliberate; newer ConnectorX releases have broken behavior here, so do not relax it without testing.
- `psycopg2-binary>=2.9.10`: PostgreSQL adapter used by SQLAlchemy reflection.
- `sqlalchemy>=2.0.38`: reflects the source schema so dlt can discover columns and types.
- `humanize>=4.12.1`: formats the per-stage durations in the metrics output.

Create `.dlt/secrets.toml` with both the PostgreSQL credentials and the MotherDuck token before running. The file does not exist by default and is gitignored, so a missing or incomplete `secrets.toml` is the most common first-run failure.

```toml
# .dlt/secrets.toml
[sources.sql_database.credentials]
drivername = "postgresql"
database   = "your_database_name"
host       = "your_postgres_host"
port       = 5432
username   = "your_postgres_username"
password   = "your_postgres_password"

[destination.motherduck.credentials]
token = "your_motherduck_token"
```

Then run the pipeline. `uv run` creates the env, installs deps from `pyproject.toml`, and runs in one step:

```bash
uv run sql_database_pipeline.py
```

Or sync first, then run:

```bash
uv sync
uv run python sql_database_pipeline.py
```

The run connects to PostgreSQL, extracts the configured tables in parallel, normalizes them to Parquet, loads them into the MotherDuck dataset, and then logs per-stage timing and row counts via `timing_logs.py`.

## Files

- [`sql_database_pipeline.py`](sql_database_pipeline.py) - the entry point: builds the MotherDuck pipeline, reads the table list from config, runs the ConnectorX `sql_database` source as a full refresh, then prints metrics.
- [`timing_logs.py`](timing_logs.py) - helper that reads the dlt trace after a run: `print_pipeline_metrics()` logs overall, extract, normalize, and load durations and row counts, `configure_logger()` sets up the dedicated `pipeline_metrics` logger.
- [`.dlt/config.toml`](.dlt/config.toml) - all non-secret knobs: source schema and table list, source/pool/stage worker counts, MotherDuck batch size, interim Parquet format, and dlt runtime log level.
- `.dlt/secrets.toml` - PostgreSQL credentials and the MotherDuck token. Not committed (gitignored) and must be created by hand before running, see the template in "Run it".
- [`pyproject.toml`](pyproject.toml) - project metadata and dependencies (`dlt[motherduck]`, version-pinned `connectorx`, `psycopg2-binary`, `sqlalchemy`, `humanize`), resolved by `uv`.
- [`uv.lock`](uv.lock) - pinned dependency lockfile for reproducible `uv` installs.
- [`.gitignore`](.gitignore) - excludes `secrets.toml`, `.env`, Python build artifacts, and local `*.duckdb` files.

## Learn more

- `sql_database_pipeline.py`: pipeline definition, table-list lookup, ConnectorX source, full-refresh run.
- `timing_logs.py`: `print_pipeline_metrics()` and `configure_logger()`, which extract stage timings and row counts from the dlt trace.
- `.dlt/config.toml`: all non-secret knobs (schema, table list, workers, batch size, format, log level).
- dlt write dispositions and incremental loading: https://dlthub.com/docs/general-usage/incremental-loading
- For deeper MotherDuck or DuckDB questions (destination behavior, dataset/schema layout, tuning loads), run the `ask_docs_question` MCP tool or see the MotherDuck docs.
