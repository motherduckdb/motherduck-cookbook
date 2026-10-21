---
title: "Flight recipe: Heroku Postgres → MotherDuck full-refresh copy"
id: flight-heroku-postgres-ingest
description: >-
  A Flight that copies the base tables of a Heroku Postgres database into
  MotherDuck, one atomic full-refresh CREATE OR REPLACE per table, with
  Heroku-specific connection checks, schema/table filters, retries and an audit
  ledger. Use it to mirror Heroku Postgres into MotherDuck once or on a schedule.
type: template
category: ingestion
features: [flights]
tags: [postgres]
prompt: >-
  I want to copy my Heroku Postgres database into MotherDuck and keep it in sync.
  Help me adapt the "Flight recipe: Heroku Postgres → MotherDuck full-refresh
  copy" recipe to my own data and use case, using it as a guide. Only ask me what
  you need; I'll put my connection details in a MotherDuck secret, not in chat:
  https://motherduck.com/docs/cookbook/flight-heroku-postgres-ingest
published_date: 2026-10-20
---

# Flight recipe: Heroku Postgres → MotherDuck full-refresh copy

A single-file Flight that copies the base tables of a Heroku Postgres database into a
MotherDuck database, replacing any tables with the same names and keeping schema
names. It can run once or on a schedule, and every run is a full refresh. It is
the [flight-postgres-ingest](../flight-postgres-ingest) pattern with the
connection details, defaults and failure modes of Heroku Postgres built in, so the common
setup mistakes fail fast with a clear message.

## How it works

[`flight.py`](flight.py) runs a fixed sequence:

1. **Read the connection** from a Flights secret: a `DATABASE_URL` (the usual
   case), or `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` (`PGPORT`, `PGSSLMODE`
   optional). Namespaced keys (`<secret>_DATABASE_URL`) are preferred over bare
   ones. An `sslmode` in the URL is honoured. Provider checks run before connecting.
2. **Connect to MotherDuck** (`MOTHERDUCK_HOST` if set) and attach the Postgres
   source `READ_ONLY` through the DuckDB `postgres` extension. Credentials reach
   libpq through `PG*` environment variables, never SQL text.
3. **Discover base tables** with `postgres_query` on
   `information_schema.tables WHERE table_type = 'BASE TABLE'`, so views,
   materialized views and live stats views are never copied. Apply the
   `INCLUDED_*` / `EXCLUDED_*` filters (exclude wins). Unknown included names
   stop the run before anything is copied.
4. **Copy each table** with one `CREATE OR REPLACE TABLE ... AS SELECT *`, retried
   with jittered exponential backoff, then recount rows in Postgres and warn on
   drift. Each copy is recorded in `<TARGET_DB>.main.flight_tracker`.
5. **Fail the run at the end** if any table still failed; the others still copy.

The provider settings block at the top of `flight.py` (`PROVIDER`, `DEFAULT_SECRET`,
`DEFAULT_TARGET_DB`, `DEFAULT_SSLMODE`, system schemas, default exclusions) is the
only part that differs from the other `flight-*-postgres-ingest` templates.

## Questions to answer

- Is there already a MotherDuck Flights secret with the Heroku Postgres connection string?
  If not, create one named `heroku` with key `DATABASE_URL`. The value comes from
  `heroku config:get DATABASE_URL -a <app>`, or Dashboard → app → **Resources** → **Heroku Postgres** → **Settings** → **View Credentials** → **URI**. Enter it in the MotherDuck UI, never in chat, source or config.
- What to copy: all non-system schemas (the default), or specific schemas/tables?
- Which MotherDuck database receives the copy? Default `heroku_mirror`. If it already
  exists, tables with the same names are replaced.
- Once (on demand), or on a schedule (cron, UTC)?

## Heroku specifics

- **SSL is required.** The Flight defaults to `sslmode=require`.
- **`pg_stat_statements` is installed by default** in `public`. It's a live query-stats view, not app data. Because only base tables are copied it's skipped automatically (it's also in the default `EXCLUDED_TABLES`). The `heroku_ext` extension schema is always skipped.
- **Credentials can rotate** during Heroku maintenance or plan changes. If a run that used to work fails with an auth or host error, refresh the secret from `heroku config:get DATABASE_URL`.
- **Large or production databases:** point the secret at a follower (read replica) URL to keep the load off the primary. On Standard plans and above, a read-only credential (`heroku pg:credentials:create`) is safer than the default owner credential.
- **There's no free Heroku Postgres plan.** The cheapest is Essential-0 (`heroku addons:create heroku-postgresql:essential-0 -a <app>`).

## Caveats

- **Ask before setting up.** Agents following this recipe should work through
  [Questions to answer](#questions-to-answer) with the user first, and only then open the
  secret form (the MCP `create_secret` tool) or create the Flight.
- **Full refresh re-reads every table on every run.** Cost scales with table size,
  not change volume. For very large tables (hundreds of millions of rows and up),
  consider an incremental pattern.
- **Not carried over:** primary keys, indexes and constraints. **Not removed:** a
  table dropped in Postgres stays in MotherDuck until you drop it.
- **Column types** come from DuckDB's Postgres type mapping. Exotic types (custom
  enums, ranges, `hstore`, composite arrays) may arrive as `VARCHAR` or fail;
  exclude such tables or project their columns.
- **Row-count drift is a warning, not a failure**, because a live source can
  change mid-copy.
- **Per-run overrides need stored keys.** `MD_RUN_FLIGHT` (or the MCP
  `run_flight` tool) can only override config keys already stored on the Flight,
  so create the Flight with every key you may change later, set to its default.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing Heroku Postgres credentials ... Keys found: [...]` | Set `SECRET_NAME` / `URL_KEY` to match the keys shown |
| `Not found among Heroku Postgres base tables: [...]` | A typo in `INCLUDED_SCHEMAS` / `INCLUDED_TABLES`, or the object is a view (views are skipped) |
| `No tables to copy after applying the INCLUDED_* / EXCLUDED_* filters` | The filters exclude everything; loosen them |
| Row-count warning on a table | The source changed during the copy; re-run when it's quiet, or exclude the table |
| A table fails after retries with a type error | An exotic column type; add the table to `EXCLUDED_TABLES` |
| Auth failed / host not found | Credentials rotated; refresh the secret |
| `config key ... is not defined on this Flight` when running | A per-run `config` override only works for keys stored on the Flight; update the Flight's config map first |

## What you'll adjust

No code edits are required. Everything is read from Flight config and the Flights secret.

| Knob | Default | Purpose |
|---|---|---|
| `SECRET_NAME` | `heroku` | Flights secret name |
| `URL_KEY` | `DATABASE_URL` | Key in the secret that holds the URL |
| `INCLUDED_SCHEMAS` | all non-system | Comma-separated Postgres schemas to copy |
| `EXCLUDED_SCHEMAS` | none | Comma-separated schemas to skip (exclude wins) |
| `INCLUDED_TABLES` | all | Comma-separated `table` or `schema.table` |
| `EXCLUDED_TABLES` | `pg_stat_statements,pg_stat_statements_info` | Tables to skip (exclude wins) |
| `TARGET_DB` | `heroku_mirror` | MotherDuck database to write into (created if missing) |
| `TARGET_SCHEMA` | same as source | Rename the target schema (only with a single source schema) |
| `MAX_RETRIES` | `3` | Attempts per table on transient errors (jittered backoff: 2s, 4s, 8s…) |
| `MOTHERDUCK_HOST` | unset | Override the MotherDuck host (e.g. non-prod). Leave unset for default. |
| `heroku` **secret** | (required) | `TYPE flights` secret with `DATABASE_URL`, or `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` |

Empty config values fall back to the defaults.

## Run it

For a local run, set the same names the secret would inject:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://user:password@host:5432/dbname?sslmode=require'
# optional: narrow scope / pick a destination
# export INCLUDED_SCHEMAS=public
# export TARGET_DB=heroku_mirror
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

First store the connection as a **Flights secret** named `heroku` with key
`DATABASE_URL` (UI: [Settings > Secrets](https://app.motherduck.com/settings/secrets),
type **Flights**; the prefilled link
`https://app.motherduck.com/settings/secrets?action=create&type=flights&name=heroku&params=DATABASE_URL`
carries only the key name).

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function, passing:

- `name`: for example `heroku_postgres_copy`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["heroku"]`
- `config`: `TARGET_DB` plus any filters you might override later (set to their defaults)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`. Run once with `MD_RUN_FLIGHT(flight_id := ...)`, check
that the tables exist with row counts matching the log and that
`<TARGET_DB>.main.flight_tracker` has one row per table, then add a schedule if
one is wanted.

## Security

- **Connection in a secret, never config or SQL.** The URL or password comes from
  a `TYPE flights` secret and reaches libpq through `PG*` environment variables,
  never a SQL statement or log line.
- **Read-only source.** Postgres is attached `READ_ONLY`, so the Flight can never
  write back.
- **Quoted identifiers.** Discovered schema and table names and `TARGET_DB` are
  quoted before they reach SQL.

## Status

Tested on MotherDuck staging (Oct 20, 2026): a full copy of a real Heroku Postgres database (`demo_shop` schema: 4 tables, 18,187 rows) succeeded with row counts matching for every table. Discovery returned only the 4 base tables, so views such as `pg_stat_statements` (whose row count changes while it's read) are never copied.

## Learn more

- Generic Postgres version: [flight-postgres-ingest](../flight-postgres-ingest).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- DuckDB `postgres` extension: [duckdb.org/docs](https://duckdb.org/docs/stable/core_extensions/postgres).
- Files: [`flight.py`](flight.py), [`requirements.txt`](requirements.txt) (`duckdb` only; the `postgres` extension is a core DuckDB extension loaded at run time).
