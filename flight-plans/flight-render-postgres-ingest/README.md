---
title: Mirror Render Postgres into MotherDuck With a Flight
id: flight-render-postgres-ingest
description: >-
  Deploy the shared Postgres ingest Flight against a Render Postgres database:
  a full-refresh copy of each base table into MotherDuck, with optional
  incremental tables, a read replica as the source, retries, and an audit log.
  Use it to mirror Render Postgres into MotherDuck once or on a schedule.
type: template
category: ingestion
features: [flights]
tags: [postgres, ingest]
prompt: >-
  I want to ingest my Render Postgres data into MotherDuck and keep it in sync on
  a schedule. Help me adapt the "Mirror Render Postgres into MotherDuck With a
  Flight" recipe to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-render-postgres-ingest
published_date: 2026-10-20
---

# Mirror Render Postgres into MotherDuck With a Flight

Copy the tables of a Render Postgres database into a MotherDuck database, once
or on a schedule. This recipe deploys the shared
[Postgres ingest Flight](../flight-postgres-ingest) unchanged. This page covers
what's specific to Render: which connection string to use, how the IP allow list
and read replicas affect the Flight, and why a Free database can't back a
schedule.

## How it works

The Flight attaches Render Postgres read-only through the DuckDB `postgres`
extension and copies each base table with one
`CREATE OR REPLACE TABLE ... AS SELECT *`. Tables you list in
`INCREMENTAL_TABLES` instead read only rows at or after the last watermark and
`MERGE` them on a key. Each table is retried with backoff and recorded in
`<TARGET_DATABASE>.main.flight_tracker`. See
[How it works](../flight-postgres-ingest#how-it-works) on the shared recipe for
the full sequence.

For Render, the Flight:

- Stops before connecting if the host is a `dpg-...` name with no domain. That
  is the Internal Database URL, which only resolves inside Render's network.
- Requires TLS by default (`sslmode=require`). Render requires SSL on external
  connections.
- Copies base tables only, so views such as `pg_stat_statements` are never
  copied.

## Questions to answer

- Where does the connection string go? In the Render Dashboard, open the
  database, then **Connect** > **External** > **External Database URL**. Store it
  in a MotherDuck Flights secret named `render` under the key `DATABASE_URL`.
  Never put it in chat, config, or code.
- Which database should it read? A read replica keeps a large scan off the
  primary. Each replica has its own External Database URL. Render offers
  replicas only on databases with at least 10 GB of storage and 0.5 CPU.
- Has the database's IP allow list been narrowed from the default, which allows
  all addresses? If so, add the Flight egress IPs for your organization's region
  (see [Caveats](#caveats)).
- Is the database on the Free plan? Free databases expire 30 days after they're
  created. Take a one-time copy if you like, but don't schedule against one. The
  plan is shown on the database's **Info** page in Render.
- What to copy: all non-system schemas (the default), or specific schemas and
  tables? Should any large, append-mostly tables be incremental, and on which
  watermark and key columns?
- Which MotherDuck database receives the copy? This recipe uses `render_mirror`.
  Tables with the same names in that database are replaced.
- Once, or on a schedule (cron, UTC)?

## Caveats

- **Settle the questions first.** An agent following this recipe should work
  through [Questions to answer](#questions-to-answer) before it opens the secret
  form (the MCP `create_secret` tool) or creates the Flight.
- **Use the External Database URL.** Render shows two connection strings for
  each database:

  | | Internal Database URL | External Database URL |
  |---|---|---|
  | Host format | `dpg-<id>-a`, a bare host with no domain | `dpg-<id>-a.<region>-postgres.render.com`, for example `...virginia-postgres.render.com` |
  | Reachable from | Only Render services in the same account and region | Anywhere, including MotherDuck Flights and your laptop |

  Copy the External URL exactly as Render shows it. Don't add a region suffix by
  hand: Render's regional endpoints can route a hand-built host, which hides
  mistakes.
- **A restricted IP allow list blocks the Flight.** If connections time out or
  are refused, add the Flight egress IPs listed in
  [Connecting to external systems](https://motherduck.com/docs/concepts/flights/#connecting-to-external-systems)
  for your organization's region. Find the region with
  `SELECT region FROM md_user_info();`.
- **Free databases expire.** A Free Postgres database expires 30 days after it's
  created, and every Flight run against it fails from then on. Render gives a
  14-day grace period to upgrade, then deletes the database and all its data.
  A copy already in MotherDuck survives the deletion.
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
| `render` secret | (required) | `TYPE flights` secret with `DATABASE_URL`, the primary's or a read replica's External Database URL |
| `SECRET_NAME` | `render` | Makes the Flight read `render_DATABASE_URL` when it has several secrets |
| `TARGET_DATABASE` | `render_mirror` | MotherDuck database to write into, created if missing |
| `INCLUDED_SCHEMAS` | (all non-system) | Comma-separated schemas to copy, for example `public` |
| `EXCLUDED_TABLES` | (none) | Comma-separated `table` or `schema.table` to skip |
| `INCREMENTAL_TABLES` | (none) | `schema.table:watermark_column:key_column` for large tables, for example `public.events:created_at:id` |

## Run it

For a local run, set the same names the secret would inject. The Flight code
lives in the shared recipe's folder:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://<user>:<password>@dpg-<id>-a.<region>-postgres.render.com/<database>'
export TARGET_DATABASE=render_mirror
# optional: narrow scope, make large tables incremental
# export INCLUDED_SCHEMAS=public
# export INCREMENTAL_TABLES=public.events:created_at:id
cd ../flight-postgres-ingest
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Store the connection as a Flights secret named `render` with the key
`DATABASE_URL`: in the UI under
[Settings > Secrets](https://app.motherduck.com/settings/secrets) with type
**Flights**, or with SQL from a write-enabled connection:

```sql
CREATE SECRET render IN motherduck (
  TYPE flights,
  PARAMS MAP {'DATABASE_URL': 'postgresql://<user>:<password>@dpg-<id>-a.<region>-postgres.render.com/<database>'}
);
```

Then create the Flight with `MD_CREATE_FLIGHT`, passing:

- `name`: for example `render_postgres_mirror`
- `source_code`: the shared
  [`flight.py`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/flight.py)
- `requirements_txt`: the shared
  [`requirements.txt`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/requirements.txt)
- `flight_secret_names`: `['render']`
- `config`: `SECRET_NAME` = `render`, `TARGET_DATABASE` = `render_mirror`, plus
  any filters or `INCREMENTAL_TABLES`. Store keys you may override per run, set
  to `""` for the default, because `MD_RUN_FLIGHT` only overrides stored keys.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`. Run it once with `MD_RUN_FLIGHT(flight_id := ...)`,
check that `render_mirror.main.flight_tracker` has one row per table, then add a
schedule if one is wanted.

## Security

- **The connection lives in a secret.** The URL reaches libpq through `PG*`
  environment variables, never a SQL statement or a log line.
- **Read-only source.** Postgres is attached `READ_ONLY`, so the Flight can't
  write back.
- **TLS on the wire.** The Flight connects with `sslmode=require` unless the URL
  sets a different `sslmode`.

## Learn more

- Shared implementation and full settings: [flight-postgres-ingest](../flight-postgres-ingest).
- Render: [Free plan limits](https://render.com/docs/free).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
