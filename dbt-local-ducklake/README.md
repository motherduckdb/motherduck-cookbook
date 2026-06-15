---
title: Run dbt on a Local DuckLake Catalog
id: dbt-local-ducklake
description: >-
  A dbt-duckdb project that materializes TPC-H parquet into a DuckLake catalog,
  using local Postgres for metadata and a folder of parquet files for storage.
  Use when you want a lakehouse-style catalog (table versioning, snapshots, file
  compaction) for local dbt development that mirrors a managed DuckLake on
  MotherDuck.
type: example
features: [ducklake]
tags: [dbt, postgres, sqlite]
---

# Run dbt on a Local DuckLake Catalog

This project reads TPC-H parquet files as dbt sources and materializes them as tables in a DuckLake catalog. DuckLake is a DuckDB extension that adds Iceberg-style catalog management (snapshots, table versioning, file compaction) on top of plain parquet, with the metadata kept in a separate Postgres or SQLite database. The catalog is always aliased `catalog`, so the same `attach` pattern points at a fully managed DuckLake on MotherDuck by swapping one target: develop against a local catalog, then ship to `md:` with no model changes.

## Why DuckLake locally

Running DuckLake on your laptop gets you the catalog ergonomics of a lakehouse without standing one up:

- **Catalog management:** table versioning and snapshots without a full lakehouse stack.
- **Development parity:** mirror your production lakehouse patterns in local dev, then promote to MotherDuck-managed DuckLake by switching `--target`.
- **Flexible backends:** Postgres metadata for multi-user or shared-catalog scenarios, or SQLite for a zero-dependency single-file path.
- **Data/metadata separation:** metadata lives in Postgres/SQLite; data lives in parquet under `data_files/`, so each is managed independently.
- **Portable:** the whole analytical database is a metadata store plus a folder of parquet files you can commit or share.

## Connection details

`profiles.yml` ships two targets, both aliasing the catalog as `catalog` so models stay backend-agnostic.

The default `local` target keeps metadata in Postgres via a named secret and writes data files to `data_files/`:

```yaml
local:
  type: duckdb
  threads: 4
  extensions:
    - name: ducklake
      repo: core_nightly
    - postgres
  secrets:
    - name: ducklake_secret
      type: postgres
      host: localhost
      port: 5432
      database: ducklake_catalog
  attach:
    - path: "ducklake:postgres:"
      alias: catalog
      options:
        data_path: data_files
        meta_secret: ducklake_secret
```

The `motherduck` target attaches a managed DuckLake on MotherDuck instead. Note `is_ducklake: true` rather than the `ducklake:` connection-string prefix:

```yaml
motherduck:
  type: duckdb
  threads: 4
  attach:
    - path: "md:jdw_ducklake"
      is_ducklake: true
      alias: catalog
target: local
```

To run the zero-dependency SQLite path, add a target that keeps metadata in a single `.db` file and skips the Postgres secret entirely:

```yaml
local_sqlite:
  type: duckdb
  threads: 4
  extensions:
    - name: ducklake
      repo: core_nightly
    - sqlite
  attach:
    - path: "ducklake:sqlite:ducklake_sqlite.db"
      alias: catalog
      options:
        data_path: ducklake_files
```

## How it works

- `models/tpch/raw/_sources.yml`: `external_location: data/{name}.parquet` is the dbt-duckdb pattern for reading parquet directly as sources, with no upstream load step. The 8 TPC-H source tables map one-to-one to files in `data/`.
- `models/tpch/raw/*.sql`: each raw model is a thin `select * from {{ source('tpch', '<table>') }}` that materializes the parquet source into a DuckLake table in `catalog.raw`.
- `models/tpch/queries/q01.sql`...`q22.sql`: the 22 standard TPC-H analytical queries, materialized as tables in `catalog.prep`. They `{{ ref(...) }}` the raw models, so DuckLake snapshots the dependency graph end to end.
- `macros/schema.sql`: `generate_schema_name` is overridden to use schema names verbatim, so `+schema: raw` lands in `catalog.raw` rather than dbt's default `<target>_raw`. Without this override your tables would land in the wrong schema.
- `macros/ducklake_maintenance.sql`: `maintain_ducklake()` discovers the DuckLake alias by scanning `target.attach` for a path containing `ducklake`, falling back to `catalog`. It then runs three maintenance calls in order:

  ```sql
  CALL catalog.merge_adjacent_files();
  CALL ducklake_expire_snapshots('catalog', older_than => now() - INTERVAL '1 minute');
  CALL ducklake_cleanup_old_files('catalog', cleanup_all => true);
  ```

## Questions to answer

- Which DuckLake backend: local Postgres, local SQLite, or a managed catalog on MotherDuck (`md:`)?
- What is the source data: keep TPC-H, or repoint `external_location` to your own parquet / object-storage path?
- What scale factor should the benchmark data use (full ~10GB vs a small dev sample)?
- Which target database and schema should models materialize into (defaults: `catalog.raw` and `catalog.prep`)?
- For the local Postgres target, what are the metadata store credentials (host, port, database)?
- Should DuckLake maintenance (compaction, snapshot expiry) run, and on what cadence?

## Caveats

- **Postgres database must exist first.** The `local` target attaches `ducklake:postgres:` against a `ducklake_catalog` database. dbt will not create it for you. Run `createdb ducklake_catalog` (or the equivalent) before `dbt build`, or the attach fails.
- **Secrets do not belong in `profiles.yml`.** The shipped Postgres secret has no password (local trust auth). For any non-local Postgres, supply credentials via environment variables / a dbt secret resolver, not by committing them here.
- **`core_nightly` is a moving target.** The DuckLake extension is pinned to the `core_nightly` repo, so behavior can change between builds. Pin to a released DuckLake version once one is available if you need reproducibility.
- **Snapshot expiry is aggressive.** `maintain_ducklake()` expires snapshots `older_than => now() - INTERVAL '1 minute'` even though its log line says "1 hour". Running it discards almost all time-travel history immediately. Widen the interval before relying on it in any environment where you want to keep snapshots.
- **`merge_adjacent_files` and friends are DuckLake-only.** These maintenance calls only resolve when the attached catalog is a DuckLake. Running the macro against a plain DuckDB attach will error.
- **The `motherduck` target uses a hardcoded database name.** `md:jdw_ducklake` is an example database. Point it at your own MotherDuck DuckLake database and set `motherduck_token` in the environment, or the attach fails silently to authenticate.
- **Scale factor 10 generates ~10GB.** Generating and materializing the full set is slow and disk-heavy. Use `--scale-factor 1` (or lower) for fast iteration; the same models work at any scale.
- **`data/`, `data_files/`, and `*.db` are gitignored.** The generated parquet, DuckLake data files, and SQLite metadata are intentionally not committed. A fresh clone has to regenerate data and rebuild before queries return rows.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `target` (profiles.yml) | Which DuckLake backend dbt runs against | `local` (Postgres metadata, ships as default) or `motherduck` (attaches `md:jdw_ducklake`); add a `local_sqlite` target for the zero-dependency SQLite path (see "Connection details") |
| `attach.path` (profiles.yml) | The DuckLake catalog connection string | `ducklake:postgres:`, `ducklake:sqlite:ducklake.db`, or `md:<your_ducklake_db>` with `is_ducklake: true` |
| `attach.options.data_path` | Where DuckLake writes its parquet data files (local targets) | `data_files` (default); any local or object-storage (`s3://`) path |
| `secrets.ducklake_secret` (profiles.yml) | Postgres metadata store for the `local` target | `host: localhost`, `port: 5432`, `database: ducklake_catalog` |
| `extensions.ducklake.repo` | DuckLake extension channel | `core_nightly` (current); pin to a release once one is published |
| `external_location` (models/tpch/raw/_sources.yml) | Where raw parquet sources are read from | `data/{name}.parquet`; repoint to your own files or an `s3://`/`https://` path |
| `tables` (models/tpch/raw/_sources.yml) | Which source tables dbt knows about | the 8 TPC-H tables; replace with your own source list |
| `models.dbt_local_ducklake.tpch` (dbt_project.yml) | Where models land in the catalog | `raw` -> `catalog.raw` (tables), `queries` -> `catalog.prep` (tables) |
| TPC-H scale factor | Size of the generated benchmark data | `--scale-factor 10` (~10GB); lower it (e.g. `1` for ~1GB) for a fast local run |
| `maintain_ducklake()` (macros/ducklake_maintenance.sql) | Compaction and snapshot cleanup cadence | merges adjacent files, then `ducklake_expire_snapshots(... older_than => now() - INTERVAL '1 minute')` and `ducklake_cleanup_old_files`; tune the interval |

## Run it

Prerequisites: `uv`, and the metadata backend for your chosen target. The default `local` target needs a reachable Postgres with a `ducklake_catalog` database (create it first: `createdb ducklake_catalog`). The SQLite path needs nothing extra. The `motherduck` target needs a MotherDuck account and `motherduck_token` set in the environment.

```bash
# Install dbt-core, dbt-duckdb, duckdb, and tpchgen-cli into a managed venv
uv sync

# Generate TPC-H source parquet into data/ (lower --scale-factor for a smaller set)
uv run tpchgen-cli --scale-factor 10 --output-dir data --format=parquet

# Build the raw tables and queries into the DuckLake catalog
uv run dbt build

# Run against MotherDuck-managed DuckLake instead of the local backend
uv run dbt build --target motherduck

# Build a single model
uv run dbt run --select customer
```

Run the maintenance macro on demand to compact files and expire old snapshots:

```bash
uv run dbt run-operation maintain_ducklake
```

You can also inspect the catalog directly from the DuckDB CLI with the same attach pattern dbt uses:

```bash
duckdb -c "INSTALL ducklake; LOAD ducklake; \
  ATTACH 'ducklake:postgres:dbname=ducklake_catalog host=localhost' AS catalog; \
  USE catalog; SELECT * FROM raw.customer LIMIT 10;"
```

## Files

- [`dbt_project.yml`](dbt_project.yml) - dbt project config: points `tpch.raw` models at `catalog.raw` (table) and `tpch.queries` at `catalog.prep` (table), all materialized into the DuckLake catalog.
- [`profiles.yml`](profiles.yml) - the dbt-duckdb connection profile with the `local` (Postgres metadata) and `motherduck` targets, both aliasing the DuckLake catalog as `catalog`.
- [`pyproject.toml`](pyproject.toml) - Python dependencies for `uv sync`: dbt-core, dbt-duckdb, duckdb, and tpchgen-cli.
- [`uv.lock`](uv.lock) - pinned lockfile for the uv-managed environment.
- [`models/tpch/raw/_sources.yml`](models/tpch/raw/_sources.yml) - declares the 8 TPC-H sources, read directly from `data/{name}.parquet` via dbt-duckdb's `external_location`.
- [`models/tpch/raw/`](models/tpch/raw/) - the 8 raw models (customer, lineitem, nation, orders, part, partsupp, region, supplier), each a thin `select *` from its parquet source into `catalog.raw`.
- [`models/tpch/queries/`](models/tpch/queries/) - the 22 standard TPC-H analytical queries (`q01.sql`...`q22.sql`), materialized as tables in `catalog.prep`.
- [`macros/schema.sql`](macros/schema.sql) - overrides `generate_schema_name` to use schema names verbatim, so `+schema: raw` lands in `catalog.raw` instead of dbt's default `<target>_raw`.
- [`macros/ducklake_maintenance.sql`](macros/ducklake_maintenance.sql) - the `maintain_ducklake()` operation: merges adjacent files, expires old snapshots, and cleans up orphaned data files.
- [`.user.yml`](.user.yml) - dbt's per-user identifier file (anonymous usage tracking).
- `analyses/`, `seeds/`, `snapshots/`, `tests/`, `macros/` - the standard dbt project directories, kept as empty placeholders except for the macros above.

## Learn more

- For when a managed DuckLake on MotherDuck is the right call (BYOB storage, own-compute access, data inlining, file-aware maintenance), see the `motherduck-ducklake` skill.
- For Postgres-endpoint vs DuckDB-client connection tradeoffs to MotherDuck, see the `motherduck-connect` skill.
- For deeper DuckLake or DuckDB SQL questions, use the `ask_docs_question` MCP tool rather than duplicating docs here.
