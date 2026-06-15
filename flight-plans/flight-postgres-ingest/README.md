---
title: Mirror PostgreSQL Tables into MotherDuck With a Flight
id: flight-postgres-ingest
description: >-
  A reusable Flight that mirrors PostgreSQL base tables into MotherDuck through
  the DuckDB postgres extension, one streaming full-refresh CREATE OR REPLACE per
  table, with config-driven schema/table selection, idempotent re-runs, retries
  with backoff, and a per-table audit log. Use it for a config-driven, re-runnable
  Postgres to MotherDuck ingest.
type: template
features: [flights]
tags: [postgres, ingest, migrate]
---

# Mirror PostgreSQL Tables into MotherDuck With a Flight

A single-file Flight that copies PostgreSQL base tables into a MotherDuck
database. 

The pattern is a **full-refresh, atomic swap per table**. Each run attaches
Postgres read-only via the DuckDB `postgres` extension, discovers the base
tables in scope, and moves each with one statement:
`CREATE OR REPLACE TABLE <target>."<schema>"."<table>" AS SELECT * FROM pg."<schema>"."<table>"`.
That is the whole load — ATOMIC (swaps in one step), IDEMPOTENT (no watermark to
drift), and STREAMING (flat memory even on large tables). Python only
orchestrates discovery, retries, and logging. 
A logging table is created in the target database also.

Full refresh is the simplest correct choice for mutable tables; 
If you have very large tables (billions of rows), consider incremental/append/CDC
and use a different Flight template or modify this one heavily.

## How it works

`flight.py` runs a fixed sequence:

1. **Connect.** Set `motherduck_host` if `MOTHERDUCK_HOST` given, `duckdb.connect("md:")`.
2. **Attach Postgres read-only.** Export `pg_*` to libpq env vars, `INSTALL`/`LOAD postgres`, `ATTACH '' AS pg (TYPE postgres, READ_ONLY)`. `READ_ONLY` lets the extension parallelize reads; the empty connection string keeps the password in env, never in SQL.
3. **Ensure target.** `CREATE DATABASE IF NOT EXISTS` plus creating the `main.flight_tracker` audit table.
4. **Discover base tables** List base tables (`information_schema.tables WHERE table_type = 'BASE TABLE'` via `postgres_query`), keep those passing the gates.
5. **Load each table.** Pre-create schemas, then per table run `CREATE OR REPLACE ... AS SELECT *` under a tenacity retry (jittered exponential backoff, transient errors). Log a `flight_tracker` row on success; on failure after retries, log and continue (per-table isolation). Exit non-zero if anything failed.

## Questions to answer

- Postgres source: host, port, database, user, SSL mode — and which password? Enter as a MotherDuck secret.
- Which schemas/tables to mirror: everything non-system, one schema, or an explicit allow/deny list?
- Which `TARGET_DATABASE` should receive the mirror?
- Is a full refresh per run acceptable given table sizes? (See [Caveats](#caveats).)
- What schedule (cron, UTC) matches source change rate and freshness needs?
- Any exotic Postgres column types that the DuckDB Postgres extension can't map that should be excluded?

## Caveats

- **Full refresh re-reads the whole table every run.** Cost scales with table
  size, not change volume. Updates and deletes are reflected, but a table
  **dropped** from the source is NOT dropped from the target — remove it yourself
  or recreate the target database. For very large/slowly-changing tables, an
  incremental pattern is cheaper.
- **The upload is single threaded and sequential by design.** Testing a ~90M-row database showed
  no improvement when parallelizing the load of multiple large tables.
  Testing also showed that adjusting DuckDB `threads`, `pg_pages_per_task`,
  `pg_connection_limit`, `pg_pool_max_connections`, and using multiple Python threads all
  leave total time unchanged - hence the simple sequential loop.
  If performance is critical, consider the added dependency of an AWS S3 bucket in your MotherDuck region and
  staging Postgres data in Parquet in S3 and ingest server-side
  (`read_parquet('s3://…')` runs in the MotherDuck duckling).
  This Flight avoids the dependency on an S3 bucket to keep things simpler.
- **`SELECT *` relies on the extension's type mapping.** Exotic Postgres types
  (custom enums, ranges, `hstore`, composite arrays) may surface as `VARCHAR` or
  error — exclude such tables or fork `load_table` to project columns.
- **Base tables only.** Discovery filters `table_type = 'BASE TABLE'`; views,
  materialized views, and foreign tables are skipped by design.
- **Client-side extension.** The Postgres scan runs in the Flight container and rows upload to MotherDuck from there.
- **Old tables are not dropped.** The target database is not cleared out at the start of the run,
  so old tables can persist. A separate command would be required to clear out the target database.

## What you'll adjust

No code edits are required (code edits are optional). 
Everything is read from Flight config/env and a MotherDuck flights secret. 
The MotherDuck **Flights secret** named `pg` contains the Postgres connection information
which includes a password, so it must be in a secret. If a different secret name is desired, 
update the SECRET_NAME variable in the code.

| Knob | Default | Purpose |
|---|---|---|
| `TARGET_DATABASE` | `postgres_ingest` | MotherDuck database for the mirror (created if absent). Tables land at `<target>.<schema>.<table>`, preserving source schema names. |
| `INCLUDED_SCHEMAS` | (all non-system) | Comma-separated source schemas to include. Empty = all. |
| `EXCLUDED_SCHEMAS` | (none) | Comma-separated schemas to drop. Exclude wins. |
| `INCLUDED_TABLES` | (all) | Comma-separated `schema.table` to include. Empty = all in selected schemas. |
| `EXCLUDED_TABLES` | (none) | Comma-separated `schema.table` to drop. Exclude wins. |
| `MAX_RETRIES` | `5` | Per-table retry attempts on transient errors. |
| `RETRY_BASE_SECONDS` | `2` | Exponential-backoff multiplier (seconds). |
| `MOTHERDUCK_HOST` | (unset) | Override MotherDuck host (e.g. non-prod). Leave unset for default. |
| `pg` **secret** | (required) | Postgres connection. `TYPE flights` secret named `pg` with params `HOST`, `PORT`, `DATABASE`, `USER`, `PASSWORD`, `SSLMODE`. |

Selection precedence: a table is mirrored only if its schema passes the schema
gate **and** its `schema.table` passes the table gate; excludes are `AND NOT` at
every level, so exclude always wins (including a table whose schema is excluded).
System schemas (`information_schema`, `pg_catalog`, `pg_toast`, `pg_temp*`) are
always excluded.

Two gotchas with the `pg` secret:
- **KEYS must be UPPERCASE.** The secret injects each param as `pg_<KEY>` (e.g.
  `pg_HOST`), which `flight.py` reads via `PG_PARAMS`.
- **Code edits are required to use a name other than `pg`.** DuckDB lowercases the secret name into the prefix.
  Rename the secret only if you also change `SECRET_NAME` in `flight.py`.

## Run it

You need a MotherDuck account and token, plus a reachable Postgres source. For a
local run, set the same `pg_*` names the secret would inject (no credential-free
smoke test — a reachable Postgres is required).

```bash
export MOTHERDUCK_TOKEN=your_token_here
# Postgres connection (same names the `pg` Flights secret injects):
export pg_HOST=your-postgres-host
export pg_PORT=5432
export pg_DATABASE=your_database
export pg_USER=readonly_user
export pg_PASSWORD=your_password
export pg_SSLMODE=require
# optional: narrow scope / pick a destination
# export TARGET_DATABASE=postgres_ingest
# export INCLUDED_SCHEMAS=public
# export EXCLUDED_TABLES=public.huge_audit_log
uv run --with-requirements requirements.txt flight.py
```

This connects to MotherDuck, loads the `postgres` extension, ATTACHes the source
`READ_ONLY`, creates `TARGET_DATABASE` and the `main.flight_tracker` audit table,
discovers base tables, applies the gates, and mirrors each selected table with a
full-refresh `CREATE OR REPLACE`. One log line per table plus a summary; exits
non-zero if any table failed after retries.

### Deploy as a Flight

First store the connection as a **Flights secret** named `pg` (UI:
[Settings > Secrets](https://app.motherduck.com/settings/secrets), type
**Flights**). Or via SQL from a write-enabled connection (read-only connections
reject `CREATE SECRET`):

```sql
CREATE SECRET pg IN motherduck (
  TYPE flights,
  PARAMS MAP {
    'HOST': 'your-postgres-host',
    'PORT': '5432',
    'DATABASE': 'your_database',
    'USER': 'readonly_user',
    'PASSWORD': 'your_password',
    'SSLMODE': 'require'
  }
);
```

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL
is checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `postgres_ingest`
- `source_code`: [`flight.py`](flight.py) (no edits for the default "mirror everything non-system")
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["pg"]` so the Postgres connection is injected
- `config`: at least `TARGET_DATABASE`, plus any `INCLUDED_*`/`EXCLUDED_*` scoping and `MOTHERDUCK_HOST` if non-default. The connection stays in the `pg` secret, never config.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create without a schedule, run once with `MD_RUN_FLIGHT(flight_id := ...)` (the
id is returned by `MD_CREATE_FLIGHT` and listed by `MD_FLIGHTS()`), and confirm
`<TARGET_DATABASE>.main.flight_tracker` has one row per table. 
Get feedback from the user about whether or not a schedule is desired and what it should be.

## Security

- **Connection in a secret, never config or SQL.** The password comes from a
  `TYPE flights` secret and reaches the extension via libpq env vars
  (`PGPASSWORD`, …) — never in a SQL statement or log. Plain Flight `config` is
  not treated as sensitive.
- **Read-only source.** Attached `READ_ONLY`, so the Flight can never write back.
- **Quoted identifiers.** `TARGET_DATABASE` and discovered schema/table names flow
  into `CREATE`/`SELECT` (not parameterizable) via `quote_ident()`. This prevents SQL injection.

## Learn more

- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- DuckDB `postgres` extension: [duckdb.org/docs](https://duckdb.org/docs/stable/core_extensions/postgres).
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
- Files: [`flight.py`](flight.py) (the Flight source), [`requirements.txt`](requirements.txt) (`duckdb` plus `tenacity` for retry/backoff; the `postgres` extension is a runtime core extension, not a pip package).
