---
title: Mirror Neon Postgres into MotherDuck With a Flight
id: flight-neon-postgres-ingest
description: >-
  Deploy the shared Postgres ingest Flight against a Neon Postgres database: a
  full-refresh copy of each base table into MotherDuck, with optional
  incremental tables, a read replica or branch as the source, retries, and an
  audit log. Use it to mirror Neon Postgres into MotherDuck once or on a
  schedule.
type: template
category: ingestion
features: [flights]
tags: [postgres, ingest]
prompt: >-
  I want to ingest my Neon Postgres data into MotherDuck and keep it in sync on
  a schedule. Help me adapt the "Mirror Neon Postgres into MotherDuck With a
  Flight" recipe to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-neon-postgres-ingest
published_date: 2026-10-20
---

# Mirror Neon Postgres into MotherDuck With a Flight

Copy the tables of a Neon Postgres database into a MotherDuck database, once or
on a schedule. This recipe deploys the shared
[Postgres ingest Flight](../flight-postgres-ingest) unchanged. This page covers
what's specific to Neon: which connection string to copy, which endpoint to
read from, and how scale-to-zero affects a run.

## How it works

The Flight attaches Neon Postgres read-only through the DuckDB `postgres`
extension and copies each base table with one
`CREATE OR REPLACE TABLE ... AS SELECT *`. Tables you list in
`INCREMENTAL_TABLES` instead read only rows at or after the last watermark and
`MERGE` them on a key. Each table is retried with backoff and recorded in
`<TARGET_DATABASE>.main.flight_tracker`. See
[How it works](../flight-postgres-ingest#how-it-works) on the shared recipe for
the full sequence.

For Neon, the Flight:

- Requires TLS by default (`sslmode=require`), which Neon enforces. The
  `sslmode` and `channel_binding=require` parameters in Neon's URL are passed to
  libpq as-is.
- Logs a warning, and keeps going, when the host contains `-pooler`. A pooled
  (PgBouncer) endpoint is less reliable for long bulk reads than the direct one.
- Copies base tables only, so views such as `pg_stat_statements` are never
  copied.

## Questions to answer

- Where does the connection string come from? In the Neon Console, open the
  project and click **Connect**. Pick the branch and database, and turn
  **Connection pooling** off so the URL uses the direct endpoint (its host has no
  `-pooler`). Store it in a MotherDuck Flights secret named `neon` under the key
  `DATABASE_URL`. Never put it in chat, config, or code.
- Which endpoint should it read? A read replica endpoint or a separate branch
  keeps a large scan off the primary compute. Both have their own connection
  string, so switching is a change to the secret. If you read a branch, make
  sure it's the one you want (usually `main` or production), not a dev branch.
- What to copy: all non-system schemas (the default), or specific schemas and
  tables? Should any large, append-mostly tables be incremental, and on which
  watermark and key columns?
- Which MotherDuck database receives the copy? This recipe uses `neon_mirror`.
  Tables with the same names in that database are replaced.
- Once, or on a schedule (cron, UTC)?

## Caveats

- **Settle the questions first.** An agent following this recipe should work
  through [Questions to answer](#questions-to-answer) before it opens the secret
  form (the MCP `create_secret` tool) or creates the Flight.
- **A suspended compute wakes on the first connection.** A compute that scaled
  to zero takes a few seconds to start, which adds to the start of a run. The
  per-table retries cover a slow wake.
- **A password reset breaks the secret.** If you reset the role's password in
  the Neon Console, runs fail on authentication until you update the secret.
- **Full refresh re-reads every table on every run.** Cost scales with table
  size, not change volume. Use `INCREMENTAL_TABLES` for large tables that mostly
  grow; incremental tables don't see deletes.
- **Not carried over:** primary keys, indexes, and constraints. **Not removed:**
  a table dropped in Postgres stays in MotherDuck until you drop it.
- The shared recipe's [Caveats](../flight-postgres-ingest#caveats) apply too,
  including type mapping and the lack of a cross-table snapshot.

## What you'll adjust

No code edits are required. These are the settings this recipe sets; the full
list is in [What you'll adjust](../flight-postgres-ingest#what-youll-adjust) on
the shared recipe.

| Knob | This recipe | Purpose |
|---|---|---|
| `neon` secret | (required) | `TYPE flights` secret with `DATABASE_URL`, the direct URL of the primary, a read replica, or a branch |
| `SECRET_NAME` | `neon` | Makes the Flight read `neon_DATABASE_URL` when it has several secrets |
| `TARGET_DATABASE` | `neon_mirror` | MotherDuck database to write into, created if missing |
| `INCLUDED_SCHEMAS` | (all non-system) | Comma-separated schemas to copy, for example `public` |
| `EXCLUDED_TABLES` | (none) | Comma-separated `table` or `schema.table` to skip |
| `INCREMENTAL_TABLES` | (none) | `schema.table:watermark_column:key_column` for large tables, for example `public.events:created_at:id` |

## Run it

For a local run, set the same names the secret would inject. The Flight code
lives in the shared recipe's folder:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://<user>:<password>@<host>/<database>?sslmode=require&channel_binding=require'
export TARGET_DATABASE=neon_mirror
# optional: narrow scope, make large tables incremental
# export INCLUDED_SCHEMAS=public
# export INCREMENTAL_TABLES=public.events:created_at:id
cd ../flight-postgres-ingest
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Store the connection as a Flights secret named `neon` with the key
`DATABASE_URL`: in the UI under
[Settings > Secrets](https://app.motherduck.com/settings/secrets) with type
**Flights**, or with SQL from a write-enabled connection:

```sql
CREATE SECRET neon IN motherduck (
  TYPE flights,
  PARAMS MAP {'DATABASE_URL': 'postgresql://<user>:<password>@<host>/<database>?sslmode=require&channel_binding=require'}
);
```

Then create the Flight with `MD_CREATE_FLIGHT`, passing:

- `name`: for example `neon_postgres_mirror`
- `source_code`: the shared
  [`flight.py`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/flight.py)
- `requirements_txt`: the shared
  [`requirements.txt`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/requirements.txt)
- `flight_secret_names`: `['neon']`
- `config`: `SECRET_NAME` = `neon`, `TARGET_DATABASE` = `neon_mirror`, plus any
  filters or `INCREMENTAL_TABLES`. Store keys you may override per run, set to
  `""` for the default, because `MD_RUN_FLIGHT` only overrides stored keys.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`. Run it once with `MD_RUN_FLIGHT(flight_id := ...)`,
check the log for the pooler warning and that
`neon_mirror.main.flight_tracker` has one row per table, then add a schedule if
one is wanted.

## Security

- **The connection lives in a secret.** The URL reaches libpq through `PG*`
  environment variables, never a SQL statement or a log line.
- **Read-only source.** Postgres is attached `READ_ONLY`, so the Flight can't
  write back.
- **Quoted identifiers.** Discovered schema and table names and
  `TARGET_DATABASE` are quoted before they reach SQL.

## Learn more

- Shared implementation and full settings: [flight-postgres-ingest](../flight-postgres-ingest).
- DuckDB `postgres` extension: [duckdb.org/docs](https://duckdb.org/docs/stable/core_extensions/postgres).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
