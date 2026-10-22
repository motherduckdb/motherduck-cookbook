---
title: Mirror Supabase into MotherDuck With a Flight
id: flight-supabase-postgres-ingest
description: >-
  Deploy the shared Postgres ingest Flight against a Supabase project's
  Postgres database through the session pooler: a full-refresh copy of each
  base table into MotherDuck, with optional incremental tables, Supabase
  connection checks, Supabase-managed schemas skipped, retries, and an audit
  log. Use it to mirror Supabase into MotherDuck once or on a schedule.
type: template
category: ingestion
features: [flights]
tags: [postgres, ingest]
prompt: >-
  I want to ingest my Supabase data into MotherDuck and keep it in sync on a
  schedule. Help me adapt the "Mirror Supabase into MotherDuck With a Flight"
  recipe to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-supabase-postgres-ingest
published_date: 2026-10-20
---

# Mirror Supabase into MotherDuck With a Flight

Copy the tables of a Supabase project's Postgres database into a MotherDuck
database, once or on a schedule. This recipe deploys the shared
[Postgres ingest Flight](../flight-postgres-ingest) unchanged. This page covers
what's specific to Supabase: which connection string to use, which mistakes
the Flight catches before connecting, and what Row Level Security means for the
copy.

## How it works

The Flight attaches Supabase Postgres read-only through the DuckDB `postgres`
extension and copies each base table with one
`CREATE OR REPLACE TABLE ... AS SELECT *`. Tables you list in
`INCREMENTAL_TABLES` instead read only rows at or after the last watermark and
`MERGE` them on a key. Each table is retried with backoff and recorded in
`<TARGET_DATABASE>.main.flight_tracker`. See
[How it works](../flight-postgres-ingest#how-it-works) on the shared recipe for
the full sequence.

For Supabase, the Flight connects through the Supavisor session pooler. Before
connecting, it stops with a message that names the fix when:

- The URL still contains the `[YOUR-PASSWORD]` placeholder from the Connect
  dialog.
- A pooler host (`*.pooler.supabase.com`) has a user without the
  `.<project_ref>` suffix. Supavisor would answer `Tenant or user not found`.
- A pooler host uses port `6543`. That is the transaction pooler, which doesn't
  hold a session for the length of a bulk read. Use port `5432` on the same
  host.
- The direct host `db.<project_ref>.supabase.co` has no IPv4 address. The
  direct host is IPv6-only unless the project has the paid IPv4 add-on, and
  Flights connect over IPv4.

On any Supabase host, the Flight skips Supabase's own schemas: `auth`,
`storage`, `realtime`, `vault`, `pgsodium`, `extensions`, `graphql`,
`supabase_functions`, `net`, `cron`, and similar. This keeps `auth.users`,
which holds your users' emails and password hashes, out of MotherDuck. Name a
schema in `INCLUDED_SCHEMAS` to copy it anyway.

## Questions to answer

- Where does the connection string go? In the Supabase dashboard, open the
  project, click **Connect**, and pick **Session pooler**. Replace
  `[YOUR-PASSWORD]` with the database password. Store the result in a
  MotherDuck Flights secret named `supabase` under the key `DATABASE_URL`.
  Never put it in chat, config, or code.
- Which role should the Flight connect as? `postgres` bypasses Row Level
  Security and copies every row. A dedicated role sees only what you grant it
  (see [Security](#security)).
- What to copy? Most projects keep their data in `public`, so
  `INCLUDED_SCHEMAS=public` is the usual scope. Should any large,
  append-mostly tables be incremental, and on which watermark and key columns?
- Which MotherDuck database receives the copy? This recipe uses
  `supabase_mirror`. Tables with the same names in that database are replaced.
- Once, or on a schedule (cron, UTC)?

## Caveats

- **Settle the questions first.** An agent following this recipe should work
  through [Questions to answer](#questions-to-answer) before it opens the secret
  form (the MCP `create_secret` tool) or creates the Flight.
- **Copy the pooler host from the Connect dialog.** The session pooler string
  looks like
  `postgresql://postgres.<project_ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
  The `aws-0` part is a pooler cluster index and can differ, for example
  `aws-1`, so don't build the host by hand.
- **Row Level Security doesn't follow the data.** RLS policies protect the
  Supabase source, not the MotherDuck copy. Anyone who can read the copy sees
  every copied row.
- **Paused projects fail.** Free-plan projects pause after a period of
  inactivity. A scheduled run fails until you restore the project.
- **A reset password breaks the run.** After you change the database password
  in Supabase, update the secret. A run that fails with
  `password authentication failed` usually means this.
- **Network restrictions apply.** If the project restricts which IP addresses
  can connect, allow the Flights outbound addresses, or the connection times
  out.
- **Full refresh re-reads every table on every run.** Cost scales with table
  size, not change volume. Use `INCREMENTAL_TABLES` for large tables that mostly
  grow; incremental tables don't see deletes.
- **Not carried over:** primary keys, indexes, constraints, and RLS policies.
  **Not removed:** a table dropped in Postgres stays in MotherDuck until you
  drop it.
- **TLS.** The default `sslmode=require` encrypts the connection. A
  `verify-full` in the URL is passed through, but it needs Supabase's root
  certificate available to libpq, which this recipe doesn't set up.
- The shared recipe's [Caveats](../flight-postgres-ingest#caveats) apply too,
  including type mapping and the lack of a cross-table snapshot.

## What you'll adjust

No code edits are required. These are the settings this recipe sets; the full
list is in [What you'll adjust](../flight-postgres-ingest#what-youll-adjust) on
the shared recipe.

| Knob | This recipe | Purpose |
|---|---|---|
| `supabase` secret | (required) | `TYPE flights` secret with `DATABASE_URL`, the Session pooler string |
| `SECRET_NAME` | `supabase` | Makes the Flight read `supabase_DATABASE_URL` when it has several secrets |
| `TARGET_DATABASE` | `supabase_mirror` | MotherDuck database to write into, created if missing |
| `INCLUDED_SCHEMAS` | `public` | Comma-separated schemas to copy; Supabase-managed schemas are skipped unless listed here |
| `EXCLUDED_TABLES` | (none) | Comma-separated `table` or `schema.table` to skip |
| `INCREMENTAL_TABLES` | (none) | `schema.table:watermark_column:key_column` for large tables, for example `public.events:created_at:id` |

## Run it

For a local run, set the same names the secret would inject. The Flight code
lives in the shared recipe's folder:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://postgres.<project_ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres'
export TARGET_DATABASE=supabase_mirror
export INCLUDED_SCHEMAS=public
# optional: make large tables incremental
# export INCREMENTAL_TABLES=public.events:created_at:id
cd ../flight-postgres-ingest
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Store the connection as a Flights secret named `supabase` with the key
`DATABASE_URL`: in the UI under
[Settings > Secrets](https://app.motherduck.com/settings/secrets) with type
**Flights**, or with SQL from a write-enabled connection:

```sql
CREATE SECRET supabase IN motherduck (
  TYPE flights,
  PARAMS MAP {'DATABASE_URL': 'postgresql://postgres.<project_ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres'}
);
```

Then create the Flight with `MD_CREATE_FLIGHT`, passing:

- `name`: for example `supabase_postgres_mirror`
- `source_code`: the shared
  [`flight.py`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/flight.py)
- `requirements_txt`: the shared
  [`requirements.txt`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/requirements.txt)
- `flight_secret_names`: `['supabase']`
- `config`: `SECRET_NAME` = `supabase`, `TARGET_DATABASE` = `supabase_mirror`,
  `INCLUDED_SCHEMAS` = `public`, plus any filters or `INCREMENTAL_TABLES`.
  Store keys you may override per run, set to `""` for the default, because
  `MD_RUN_FLIGHT` only overrides stored keys.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`. Run it once with `MD_RUN_FLIGHT(flight_id := ...)`,
check that `supabase_mirror.main.flight_tracker` has one row per table, then add
a schedule if one is wanted.

## Security

- **`postgres` copies every row.** Supabase's `postgres` role has `BYPASSRLS`,
  so the Flight copies every row, and those rows land in MotherDuck without RLS
  protection. Control who can read `supabase_mirror` with MotherDuck access
  control.
- **Consider a dedicated role.** Grant it `SELECT` on only the tables you want
  in MotherDuck, from the Supabase SQL editor:

  ```sql
  CREATE ROLE motherduck_reader WITH LOGIN PASSWORD '...';
  GRANT USAGE ON SCHEMA public TO motherduck_reader;
  GRANT SELECT ON public.orders, public.customers TO motherduck_reader;
  ```

  Its pooler user is `motherduck_reader.<project_ref>`. A role without
  `BYPASSRLS` gets only the rows its policies allow, and an RLS-enabled table
  with no policy for it comes back empty, without an error. Decide whether the
  role needs `BYPASSRLS` or a policy.
- **The connection lives in a secret.** The URL reaches libpq through `PG*`
  environment variables, never a SQL statement or a log line.
- **Read-only source.** Postgres is attached `READ_ONLY`, so the Flight can't
  write back.

## Learn more

- Shared implementation and full settings: [flight-postgres-ingest](../flight-postgres-ingest).
- Supabase: [Connecting to Postgres](https://supabase.com/docs/guides/database/connecting-to-postgres),
  covering connection strings and poolers.
- Flights outbound IPs and networking: [Flights concepts](https://motherduck.com/docs/concepts/flights/).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
