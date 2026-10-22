---
title: Mirror PostgreSQL Tables into MotherDuck With a Flight
id: flight-postgres-ingest
description: >-
  A reusable Flight that mirrors PostgreSQL base tables into MotherDuck through
  the DuckDB postgres extension, one streaming full-refresh CREATE OR REPLACE per
  table or a watermark MERGE for large tables, with config-driven schema/table
  selection, retries with backoff, and a per-table audit log. Use it for a
  config-driven, re-runnable Postgres to MotherDuck ingest.
type: template
category: ingestion
features: [flights]
tags: [postgres, ingest, migrate]
prompt: >-
  I want a config-driven, re-runnable Flight that mirrors selected PostgreSQL tables
  into MotherDuck with one streaming full-refresh per table, retries, and an audit log.
  Help me adapt the "Mirror PostgreSQL Tables into MotherDuck With a Flight" recipe to
  my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-postgres-ingest
published_date: 2026-06-09
---

# Mirror PostgreSQL Tables into MotherDuck With a Flight

A single-file Flight that copies PostgreSQL base tables into a MotherDuck
database. It's the shared implementation behind the provider recipes for
[Render](../flight-render-postgres-ingest), [Heroku](../flight-heroku-postgres-ingest),
[Neon](../flight-neon-postgres-ingest), [Supabase](../flight-supabase-postgres-ingest),
and [Azure](../flight-azure-postgres-ingest), which deploy this `flight.py` with
provider-specific settings.

By default each table is a full refresh with one statement:
`CREATE OR REPLACE TABLE <target>."<schema>"."<table>" AS SELECT * FROM pg."<schema>"."<table>"`.
The swap happens in one step, a re-run replaces the table instead of appending,
and DuckDB streams the scan into the write, so memory stays flat on large
tables. Python only handles discovery, retries, and logging.

Full refresh is the simplest correct choice for mutable tables. For a large table
that mostly grows, list it in `INCREMENTAL_TABLES`: the Flight then reads only
rows at or after the target's high-watermark and merges them on a key.

## How it works

`flight.py` runs a fixed sequence:

1. **Connect.** `duckdb.connect("md:")`.
2. **Attach Postgres read-only.** Read the connection from the secret, either a
   `DATABASE_URL` or `PG*` params, and export it as libpq env vars. Check the host
   against known-bad forms (see [Provider checks](#provider-checks)), then
   `ATTACH '' AS pg (TYPE postgres, READ_ONLY)`. The empty connection string keeps
   the password in env, never in SQL.
3. **Discover tables.** List ordinary and partitioned tables from `pg_class`. A
   partitioned table is copied once, as its parent. A name in
   `INCLUDED_SCHEMAS`/`INCLUDED_TABLES` that matches no table stops the run before
   anything is copied, and so does a filter set that selects nothing.
4. **Ensure target.** `CREATE DATABASE IF NOT EXISTS` plus the
   `main.flight_tracker` audit table.
5. **Load each table** under a tenacity retry with jittered exponential backoff.
   - A full-refresh table runs the `CREATE OR REPLACE ... AS SELECT *`.
   - An incremental table reads `max(<watermark>)` from the target, copies rows
     with `<watermark> >= ` that value into a temp table (the filter runs in
     Postgres), and runs one `MERGE` on the key columns. The first run, when the
     target doesn't exist yet, is a full copy.

   Each success appends a `flight_tracker` row. A table that still fails after
   retries is logged and skipped, and the run exits non-zero at the end.

### Provider checks

Before connecting, the Flight stops on connection details that can't work from
a Flight, with a message that names the fix:

| Host | Stops on |
|---|---|
| Render | An Internal Database URL (`dpg-...` with no domain) |
| Supabase pooler (`*.pooler.supabase.com`) | A user without the `.<project_ref>` suffix, or the transaction pooler port `6543` |
| Supabase direct (`db.<ref>.supabase.co`) | A host with no IPv4 address, since Flights connect over IPv4 |
| Azure (`*.postgres.database.azure.com`) | A private-access (`.private.`) host, the PgBouncer port `6432`, or a Single Server `user@server` user name |
| Any | A URL that still contains Supabase's `[YOUR-PASSWORD]` placeholder |

A Neon `-pooler` host logs a warning instead. On a Supabase host, Supabase's
own schemas (`auth`, `storage`, `realtime`, `vault`, and others) are skipped
unless `INCLUDED_SCHEMAS` names them; `auth.users` holds your users' emails and
password hashes.

## Questions to answer

- Postgres source: a connection URL (`DATABASE_URL`) or host, port, database,
  user, SSL mode, and password? Store it as a MotherDuck secret, never in chat,
  config, or code. For a managed host, start from its provider recipe.
- Which schemas and tables to mirror: everything non-system, one schema, or an
  explicit allow/deny list?
- Which `TARGET_DATABASE` should receive the mirror?
- Is a full refresh per run acceptable given table sizes, or which large tables
  should be incremental, on which watermark and key columns? (See
  [Caveats](#caveats).)
- What schedule (cron, UTC) matches the source's change rate and freshness needs?
- Are there exotic Postgres column types the DuckDB Postgres extension can't map,
  so those tables should be excluded?

## Caveats

- **Settle the questions first.** An agent following this recipe should work
  through [Questions to answer](#questions-to-answer) before it opens the secret
  form (the MCP `create_secret` tool) or creates the Flight.
- **Full refresh re-reads the whole table every run.** Cost scales with table
  size, not change volume. Updates and deletes are reflected.
- **Incremental tables don't see deletes.** A row deleted in Postgres stays in
  MotherDuck, and a row updated without moving its watermark column forward is
  missed. Use a watermark the application sets on every write, such as
  `updated_at`, and run a full refresh now and then by dropping the target table.
- **Incremental tables need a stable schema.** A column added in Postgres makes
  the `MERGE` fail for that table until you drop the target table so the next
  run recreates it.
- **Dropped tables persist.** A table dropped from the source, or removed from
  the filters, stays in the target database. Drop it yourself.
- **No cross-table snapshot.** Tables are copied one after another, each in its
  own read, so a run doesn't capture one consistent moment across tables.
- **The load is sequential by design.** Testing a ~90M-row database showed no
  improvement from loading several large tables in parallel, or from adjusting
  DuckDB `threads`, `pg_pages_per_task`, `pg_connection_limit`, or
  `pg_pool_max_connections`. If speed matters more than simplicity, stage
  Postgres data as Parquet in an S3 bucket in your MotherDuck region and read it
  server-side with `read_parquet('s3://...')`.
- **`SELECT *` relies on the extension's type mapping.** Bare `numeric` columns
  without a precision arrive as `DOUBLE`, and `json`/`jsonb` arrive as `VARCHAR`.
  Exotic types (ranges, `hstore`, composite arrays) may surface as `VARCHAR` or
  error; exclude those tables or fork `load_table` to project columns.
- **Base tables only.** Views, materialized views, and foreign tables are
  skipped.
- **The scan runs in the Flight.** The Postgres extension runs in the Flight's
  DuckDB client, and rows upload to MotherDuck from there.

## What you'll adjust

No code edits are required. Everything is read from Flight config and a
MotherDuck Flights secret. The secret holds the Postgres connection, including
the password.

| Knob | Default | Purpose |
|---|---|---|
| `TARGET_DATABASE` | `postgres_ingest` | MotherDuck database for the mirror, created if absent. Tables land at `<target>.<schema>.<table>`. |
| `TARGET_SCHEMA` | (source schema) | Rename the target schema. Only with a single source schema. |
| `INCLUDED_SCHEMAS` | (all non-system) | Comma-separated source schemas to include. |
| `EXCLUDED_SCHEMAS` | (none) | Comma-separated schemas to skip. Exclude wins. |
| `INCLUDED_TABLES` | (all) | Comma-separated `schema.table` or bare `table` to include. A bare name matches that table in every selected schema. Unknown names stop the run. |
| `EXCLUDED_TABLES` | (none) | Comma-separated `schema.table` or bare `table` to skip. Exclude wins. |
| `INCREMENTAL_TABLES` | (none) | Comma-separated `schema.table:watermark_column:key_column`, with `\|` between multiple keys, for example `public.orders:updated_at:id,public.lines:updated_at:order_id\|line_no`. A bare table name means `public`. |
| `CHECK_ROW_COUNTS` | `false` | `true` recounts each full-refresh table in Postgres after the copy and warns when the count changed. It reads every table a second time. |
| `MAX_RETRIES` | `5` | Per-table attempts. |
| `RETRY_BASE_SECONDS` | `2` | Exponential-backoff multiplier, in seconds. |
| `SECRET_NAME` | (unset) | The Flights secret's name. When set, its namespaced params (`<name>_DATABASE_URL`, `<name>_PGHOST`, ...) win over bare ones, which matters when a Flight has several secrets. |
| `URL_KEY` | `DATABASE_URL` | Secret param that holds the connection URL. |
| Flights secret | (required) | `DATABASE_URL`, or params `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, and `PGSSLMODE` (default `require`). |

An empty config value means the default, so you can store every key on the
Flight as `""` and override it per run. `MD_RUN_FLIGHT` can only override keys
already stored on the Flight.

A table is mirrored only if its schema passes the schema gate and its
`schema.table` passes the table gate. Exclude always wins, including for a
table whose schema is excluded. System schemas (`information_schema`,
`pg_catalog`, `pg_toast`, `pg_temp*`) and Heroku's `heroku_ext` are always
skipped.

The secret's params are the libpq env vars themselves, so libpq picks them up
with no mapping and the secret's name is yours to choose. In a `DATABASE_URL`,
`sslmode`, `channel_binding`, `options`, `connect_timeout`, and
`application_name` are passed to libpq; other query params are ignored and
named in the log. A secret named `pg` with `HOST`/`PORT`/... params, injected as
`pg_HOST`, `pg_PORT`, ..., also works.

## Run it

You need a MotherDuck token and a reachable Postgres. For a local run, set the
same names the secret would inject:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://readonly_user:your_password@your-postgres-host:5432/your_database?sslmode=require'
# optional: narrow scope, pick a destination, make large tables incremental
# export TARGET_DATABASE=postgres_ingest
# export INCLUDED_SCHEMAS=public
# export INCREMENTAL_TABLES=public.events:created_at:id
uv run --with-requirements requirements.txt flight.py
```

A local Postgres without TLS needs `sslmode=disable` in the URL. The run logs
one line per table plus a summary, and exits non-zero if any table failed after
retries.

### Deploy as a Flight

Store the connection as a Flights secret: in the UI under
[Settings > Secrets](https://app.motherduck.com/settings/secrets) with type
**Flights**, or with SQL from a write-enabled connection:

```sql
CREATE SECRET pg IN motherduck (
  TYPE flights,
  PARAMS MAP {'DATABASE_URL': 'postgresql://readonly_user:your_password@your-postgres-host:5432/your_database?sslmode=require'}
);
```

Then create the Flight with `MD_CREATE_FLIGHT`, passing:

- `name`: a Flight name, for example `postgres_ingest`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `['pg']`
- `config`: at least `TARGET_DATABASE`, plus any `INCLUDED_*`, `EXCLUDED_*`, or
  `INCREMENTAL_TABLES` settings. The connection stays in the secret.
- `max_runtime_sec`: an optional cap on a run's duration (`0` = no cap)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`.

Create it without a schedule and run it once with
`MD_RUN_FLIGHT(flight_id := ...)`. The id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`; inspect a run with
`MD_GET_FLIGHT_RUN(flight_id := ..., run_number := ...)`. Confirm that
`<TARGET_DATABASE>.main.flight_tracker` has one row per table, then decide on a
schedule and add it.

If a host restricts inbound traffic by IP, allowlist the Flight egress IPs for
your MotherDuck region, listed under
[Connecting to external systems](https://motherduck.com/docs/concepts/flights/#connecting-to-external-systems).

## Security

- **The connection lives in a secret.** The password comes from a `TYPE flights`
  secret and reaches the extension through libpq env vars, never a SQL statement
  or a log line. Errors about a malformed URL never print it. Plain Flight
  `config` isn't treated as sensitive.
- **TLS by default.** `PGSSLMODE` defaults to `require`.
- **Read-only source.** Postgres is attached `READ_ONLY`, so the Flight can't
  write back. Give it a role with `SELECT` only.
- **Quoted identifiers.** `TARGET_DATABASE`, key and watermark columns, and
  discovered schema and table names go through `quote_ident()`, and the
  watermark value is a bound parameter.

## Learn more

- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- DuckDB `postgres` extension: [duckdb.org/docs](https://duckdb.org/docs/stable/core_extensions/postgres).
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
- Files: [`flight.py`](flight.py) (the Flight source) and
  [`requirements.txt`](requirements.txt) (`duckdb` plus `tenacity` for retries;
  the `postgres` extension is a DuckDB core extension, not a pip package).
