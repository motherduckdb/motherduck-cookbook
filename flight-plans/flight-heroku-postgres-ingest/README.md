---
title: Mirror Heroku Postgres into MotherDuck With a Flight
id: flight-heroku-postgres-ingest
description: >-
  Deploy the shared Postgres ingest Flight against a Heroku Postgres database:
  a full-refresh copy of each base table into MotherDuck, with optional
  incremental tables, a read-only credential or follower as the source, retries,
  and an audit log. Use it to mirror Heroku Postgres into MotherDuck once or on a
  schedule.
type: template
category: ingestion
features: [flights]
tags: [postgres, ingest]
prompt: >-
  I want to ingest my Heroku Postgres data into MotherDuck and keep it in sync on
  a schedule. Help me adapt the "Mirror Heroku Postgres into MotherDuck With a
  Flight" recipe to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-heroku-postgres-ingest
published_date: 2026-10-20
---

# Mirror Heroku Postgres into MotherDuck With a Flight

Copy the tables of a Heroku Postgres database into a MotherDuck database, once
or on a schedule. This recipe deploys the shared
[Postgres ingest Flight](../flight-postgres-ingest) unchanged. This page covers
what's specific to Heroku: where the connection string comes from, which
credential and database to read from, and what breaks when Heroku rotates
credentials.

## How it works

The Flight attaches Heroku Postgres read-only through the DuckDB `postgres`
extension and copies each base table with one
`CREATE OR REPLACE TABLE ... AS SELECT *`. Tables you list in
`INCREMENTAL_TABLES` instead read only rows at or after the last watermark and
`MERGE` them on a key. Each table is retried with backoff and recorded in
`<TARGET_DATABASE>.main.flight_tracker`. See
[How it works](../flight-postgres-ingest#how-it-works) on the shared recipe for
the full sequence.

For Heroku, the Flight:

- Reads the `postgres://` URL Heroku hands out as-is, and requires TLS by default
  (`sslmode=require`), which Heroku enforces.
- Skips the `heroku_ext` extension schema. Views such as `pg_stat_statements` are
  never copied, because the Flight copies base tables only.

## Questions to answer

- Which credential should the Flight use? On Standard plans and up, create a
  read-only credential instead of using the default owner credential (see
  [Security](#security)). Essential plans have only the owner credential.
- Which database should it read? On Standard plans and up, point it at a
  [follower](https://devcenter.heroku.com/articles/heroku-postgres-follower-databases)
  so a large scan doesn't compete with application traffic on the leader.
- Where does the connection string go? Get it with
  `heroku pg:credentials:url DATABASE -a <app>`, or from the Dashboard: the
  database's **Credentials** tab, then the credential's URI. Store it in a
  MotherDuck Flights secret named `heroku` under the key `DATABASE_URL`. Never
  put it in chat, config, or code.
- What to copy: all non-system schemas (the default), or specific schemas and
  tables? Should any large, append-mostly tables be incremental, and on which
  watermark and key columns?
- Which MotherDuck database receives the copy? This recipe uses `heroku_mirror`.
  Tables with the same names in that database are replaced.
- Once, or on a schedule (cron, UTC)?

## Caveats

- **Settle the questions first.** An agent following this recipe should work
  through [Questions to answer](#questions-to-answer) before it opens the secret
  form (the MCP `create_secret` tool) or creates the Flight.
- **Credentials can change.** A failover or credential rotation updates
  `DATABASE_URL` on your Heroku apps, but not the copy in the Flight secret. When
  a run that used to work fails on authentication or the host name, fetch the
  current URL and update the secret.
- **Private and Shield databases aren't reachable from a Flight.** They only
  accept connections from inside their Private Space unless you set up one of the
  options in
  [Heroku Postgres and Private Spaces](https://devcenter.heroku.com/articles/heroku-postgres-and-private-spaces).
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
| `heroku` secret | (required) | `TYPE flights` secret with `DATABASE_URL`, the leader's or a follower's URL |
| `SECRET_NAME` | `heroku` | Makes the Flight read `heroku_DATABASE_URL` when it has several secrets |
| `TARGET_DATABASE` | `heroku_mirror` | MotherDuck database to write into, created if missing |
| `INCLUDED_SCHEMAS` | (all non-system) | Comma-separated schemas to copy, for example `public` |
| `EXCLUDED_TABLES` | (none) | Comma-separated `table` or `schema.table` to skip |
| `INCREMENTAL_TABLES` | (none) | `schema.table:watermark_column:key_column` for large tables, for example `public.events:created_at:id` |

## Run it

For a local run, set the same names the secret would inject. The Flight code
lives in the shared recipe's folder:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL="$(heroku config:get DATABASE_URL -a <app>)"
export TARGET_DATABASE=heroku_mirror
# optional: narrow scope, make large tables incremental
# export INCLUDED_SCHEMAS=public
# export INCREMENTAL_TABLES=public.events:created_at:id
cd ../flight-postgres-ingest
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Store the connection as a Flights secret named `heroku` with the key
`DATABASE_URL`: in the UI under
[Settings > Secrets](https://app.motherduck.com/settings/secrets) with type
**Flights**, or with SQL from a write-enabled connection:

```sql
CREATE SECRET heroku IN motherduck (
  TYPE flights,
  PARAMS MAP {'DATABASE_URL': 'postgres://<user>:<password>@<host>:5432/<database>'}
);
```

Then create the Flight with `MD_CREATE_FLIGHT`, passing:

- `name`: for example `heroku_postgres_mirror`
- `source_code`: the shared
  [`flight.py`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/flight.py)
- `requirements_txt`: the shared
  [`requirements.txt`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/requirements.txt)
- `flight_secret_names`: `['heroku']`
- `config`: `SECRET_NAME` = `heroku`, `TARGET_DATABASE` = `heroku_mirror`, plus
  any filters or `INCREMENTAL_TABLES`. Store keys you may override per run, set
  to `""` for the default, because `MD_RUN_FLIGHT` only overrides stored keys.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`. Run it once with `MD_RUN_FLIGHT(flight_id := ...)`,
check that `heroku_mirror.main.flight_tracker` has one row per table, then add a
schedule if one is wanted.

## Security

- **Use a read-only credential.** On Standard plans and up, create one and grant
  it `SELECT` from `heroku pg:psql`:

  ```bash
  heroku pg:credentials:create DATABASE --name motherduck_reader -a <app>
  ```

  ```sql
  GRANT USAGE ON SCHEMA public TO motherduck_reader;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO motherduck_reader;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO motherduck_reader;
  ```

- **The connection lives in a secret.** The URL reaches libpq through `PG*`
  environment variables, never a SQL statement or a log line.
- **Read-only source.** Postgres is attached `READ_ONLY`, so the Flight can't
  write back.

## Learn more

- Shared implementation and full settings: [flight-postgres-ingest](../flight-postgres-ingest).
- Heroku: [Connecting to Heroku Postgres](https://devcenter.heroku.com/articles/connecting-heroku-postgres)
  and [credentials](https://devcenter.heroku.com/articles/heroku-postgresql-credentials).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
