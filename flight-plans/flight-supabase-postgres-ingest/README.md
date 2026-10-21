---
title: "Flight recipe: Supabase Postgres → MotherDuck full-refresh copy"
id: flight-supabase-postgres-ingest
description: >-
  A Flight that copies the base tables of a Supabase Postgres database into
  MotherDuck through the Supavisor session pooler, one full-refresh CREATE OR
  REPLACE per table with an _ingested_at column, with Supabase-specific
  connection checks, schema/table filters, retries and a per-table load log.
  Use it to copy Supabase data into MotherDuck once or on a schedule.
type: template
category: ingestion
features: [flights]
tags: [postgres]
prompt: >-
  I want to copy my Supabase Postgres database into MotherDuck and keep it in
  sync. Help me adapt the "Flight recipe: Supabase Postgres → MotherDuck
  full-refresh copy" recipe to my own data and use case, using it as a guide.
  Only ask me what you need; I'll put my connection details in a MotherDuck
  secret, not in chat:
  https://motherduck.com/docs/cookbook/flight-supabase-postgres-ingest
published_date: 2026-10-20
---

# Flight recipe: Supabase Postgres → MotherDuck full-refresh copy

A single-file Flight that copies the base tables of a Supabase project's
Postgres database into a MotherDuck database. Every run is a full refresh: each
table is rebuilt with one `CREATE OR REPLACE TABLE ... AS SELECT`, plus an
`_ingested_at` timestamp. It connects through Supabase's Supavisor **session
pooler**, because the direct database host is IPv6-only and Flights connect
over IPv4. It follows the [flight-postgres-ingest](../flight-postgres-ingest)
pattern, with Supabase's connection details and common mistakes built in.

## How it works

[`flight.py`](flight.py) runs a fixed sequence:

1. **Read the connection** from a Flights secret: a `DATABASE_URL`, or
   `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` (`PGPORT` and `PGSSLMODE`
   optional). The namespaced `<secret>_<KEY>` form is preferred over the bare
   `<KEY>`. An `sslmode` in the URL is honoured; otherwise it is `require`.
2. **Supabase checks** before connecting:
   - a pooler host (`*.pooler.supabase.com`) on port `6543`, the transaction pooler, is switched to the session pooler port `5432`, with a warning;
   - a pooler user without the `.<project_ref>` suffix stops the run with a clear message;
   - a direct host (`db.<project_ref>.supabase.co`) gets a note to use the pooler, and stops the run if it resolves to IPv6 only and the runtime can't reach it;
   - any other host gets a warning and the Supabase checks are skipped.
3. **Connect** to MotherDuck (`MOTHERDUCK_HOST` if set) and attach Postgres
   `READ_ONLY` through the DuckDB `postgres` extension. Credentials reach libpq
   through `PG*` environment variables, never SQL text.
4. **Resolve tables** from `pg_class`: regular and partitioned tables in
   `SOURCE_SCHEMA` (or the explicit `TABLES` list), minus `EXCLUDE_TABLES`.
   Views, materialized views, foreign tables and individual partitions are
   skipped. Unknown schema or table names, and two tables that would land on
   the same target name, stop the run before anything is copied. A warning
   lists tables whose Row Level Security would hide rows from the connecting role.
5. **Copy each table** with `CREATE OR REPLACE TABLE ... AS SELECT *, now() AS _ingested_at`,
   retrying transient errors with jittered backoff (auth and catalog errors
   fail at once). The row count in MotherDuck is compared with a fresh count in
   Postgres; a difference is a warning.
6. **Log** one row per table to `<TARGET_DB>._meta.load_log` (flight id, run
   id, host kind, source and target table, status, rows in target and source,
   attempts, timing, error). The run exits non-zero if any table failed; the
   other tables still copy.

## Questions to answer

- Is there a MotherDuck Flights secret with the connection yet? If not, create
  one named `supabase` with key `DATABASE_URL`. The value is the **Session
  pooler** connection string from the Supabase dashboard: open the project,
  click **Connect**, and pick **Session pooler**. Replace `[YOUR-PASSWORD]`
  with the database password. Enter it in the MotherDuck UI, never in chat,
  source or config.
- Should the Flight connect as `postgres` (bypasses Row Level Security, sees
  everything) or as a dedicated read-only role? A read-only role is safer but
  only sees rows its RLS policies allow. See [Supabase specifics](#supabase-specifics).
- What to copy: `public` (the default), other schemas, every schema you own
  (`SOURCE_SCHEMA=*`), or specific tables? Anything to exclude?
- Which MotherDuck database and schema receive the copy? The default is
  `supabase_raw`, schema `main`. Tables with the same names are replaced.
- Once (on demand), or on a schedule (cron, UTC)?

## Supabase specifics

- **Use the session pooler, not the direct host.** Supabase's direct
  connection `db.<project_ref>.supabase.co` maps to an IPv6 address unless the
  project has the paid IPv4 add-on. MotherDuck documents only IPv4 outbound
  addresses for Flights. The session pooler is IPv4 and looks like
  `postgresql://postgres.<project_ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`.
  The `aws-0` part is a pooler cluster index and can differ (for example
  `aws-1`), so copy the host from the Connect dialog rather than building it.
- **Port 5432, not 6543.** On the pooler host, port `6543` is transaction mode,
  meant for short serverless queries, with no prepared statements or session
  state. The Flight switches it to `5432` (session mode). On the direct host,
  `6543` is the dedicated pooler and is also switched to `5432`.
- **User name format.** Through the pooler the user is `<role>.<project_ref>`,
  for example `postgres.abcdefghijklmnop`. Without the suffix Supavisor answers
  `Tenant or user not found`; the Flight catches this before connecting.
- **Row Level Security.** Supabase's `postgres` role has `BYPASSRLS`, so it
  copies every row. A custom role without `BYPASSRLS` gets only the rows its
  policies allow, and an RLS-enabled table with no policy for it comes back
  empty, without an error. The Flight logs a warning that lists those tables.
  To use a read-only role, create it with `create role reader with login password '...'`,
  grant `usage` on the schemas and `select` on their tables, and decide
  whether it needs `bypassrls`. Its pooler user is `reader.<project_ref>`.
- **Supabase-managed schemas.** `auth`, `storage`, `realtime`, `extensions`,
  `graphql`, `graphql_public`, `vault`, `pgsodium`, `supabase_functions`,
  `net`, `cron` and similar schemas are skipped by `SOURCE_SCHEMA=*`. Name one
  explicitly to copy it; the Flight warns, because `auth.users` holds emails
  and password hashes.
- **TLS.** The default `sslmode=require` encrypts the connection. `verify-full`
  in the URL is honoured, but it needs Supabase's root certificate to be
  available to libpq, which this recipe doesn't set up.
- **Paused projects.** Free-plan projects pause after a period of inactivity,
  and a scheduled Flight will fail until the project is restored.

## Caveats

- **Full refresh re-reads every table on every run.** Cost scales with table
  size, not change volume. For very large tables, consider an incremental
  (watermark) pattern instead.
- **Not carried over:** primary keys, indexes, constraints and RLS policies.
  Anyone who can read the MotherDuck copy sees every copied row. **Not
  removed:** a table dropped in Postgres stays in MotherDuck until you drop it.
- **`_ingested_at` is reserved.** A source table that already has a column with
  that name fails with a clear message; exclude or rename it.
- **Column types** come from DuckDB's Postgres type mapping. Exotic types
  (ranges, composite types, some extension types such as PostGIS geometry) may
  arrive as `VARCHAR` or fail; exclude such tables.
- **Row-count drift is a warning, not a failure**, because a live source can
  change mid-copy.
- **Per-run overrides need stored keys.** `MD_RUN_FLIGHT` (or the MCP
  `run_flight` tool) can only override config keys already stored on the
  Flight, so create the Flight with every key you may change later, set to its
  default (an empty value means "use the default").

### Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing Postgres connection fields [...] Secret keys visible to this run: [...]` | Set `SECRET_NAME` / `URL_KEY` to match the key names shown (values are never printed) |
| `... resolves to IPv6 only and this runtime has no IPv6 route` | Store the Session pooler connection string instead of the direct one |
| `The Supabase pooler needs the user name '<role>.<project_ref>'` or `Tenant or user not found` | Copy the user and host exactly from Supabase → Connect → Session pooler |
| `password authentication failed` | Wrong or reset database password (Database settings in Supabase): update the secret |
| Connect timeout | The project is paused, or network restrictions in Supabase block the Flights outbound IPs |
| `TABLES not found as base tables in [...]` | A typo, a missing schema prefix, or a view (views are not copied) |
| `SOURCE_SCHEMA has no base tables (or doesn't exist)` | Fix the schema name, or use `*` |
| `Several source tables would land on the same target table` | Leave `TARGET_SCHEMA` empty so each schema keeps its name, or exclude one table |
| RLS warning, or a table arrives empty | The role doesn't bypass RLS: use `postgres`, grant `bypassrls`, or add a policy for the role |
| `config key ... is not defined on this Flight` | Add the key to the Flight's stored config first |

## What you'll adjust

No code edits are required. Everything is read from Flight config and the Flights secret.

| Knob | Default | Purpose |
|---|---|---|
| `SECRET_NAME` | `supabase` | Flights secret name |
| `URL_KEY` | `DATABASE_URL` | Key in the secret that holds the connection URL |
| `SOURCE_SCHEMA` | `public` | Comma-separated Postgres schemas, or `*` for every schema except Supabase-managed ones |
| `TABLES` | (all base tables) | Comma-separated `table` (looked up in `SOURCE_SCHEMA`) or `schema.table` |
| `EXCLUDE_TABLES` | (none) | Comma-separated `table` or `schema.table` names to skip |
| `TARGET_DB` | `supabase_raw` | MotherDuck database to write into (created if missing) |
| `TARGET_SCHEMA` | `main` for one source schema, else the source schema's name | Schema inside `TARGET_DB` |
| `MAX_RETRIES` | `3` | Attempts per table on transient errors |
| `MOTHERDUCK_HOST` | unset | Override the MotherDuck host (for example a non-production one). Leave unset normally. |
| Flights **secret** | (required) | `DATABASE_URL`, or `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` (+ optional `PGPORT`, `PGSSLMODE`) |

Empty config values fall back to the defaults.

## Run it

For a local run, set the same names the secret would inject:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://postgres.your_project_ref:password@aws-0-us-east-1.pooler.supabase.com:5432/postgres'
# optional:
# export SOURCE_SCHEMA=public,billing
# export TARGET_DB=supabase_raw
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

First store the connection as a **Flights secret** named `supabase` with key
`DATABASE_URL` (UI: [Settings > Secrets](https://app.motherduck.com/settings/secrets),
type **Flights**). The prefilled link
`https://app.motherduck.com/settings/secrets?action=create&type=flights&name=supabase&params=DATABASE_URL`
carries only the key name. Or use `CREATE SECRET supabase IN motherduck (TYPE flights, PARAMS MAP {...})`
from a write-enabled connection.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function, passing:

- `name`: for example `supabase_postgres_copy`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["supabase"]`
- `config`: `SOURCE_SCHEMA`, `TARGET_DB`, plus every other knob you may
  override per run (`TABLES`, `EXCLUDE_TABLES`, `TARGET_SCHEMA`, ...), set to
  its default or `""`

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`. Run once with `MD_RUN_FLIGHT(flight_id := ...)` and
check that the tables exist. Each table should have a `succeeded` row in
`<TARGET_DB>._meta.load_log` with `rows_in_target` equal to `rows_in_source`.
Then add a schedule if one is wanted.

## Security

- **Connection in a secret, never config or SQL.** The URL or password comes
  from a `TYPE flights` secret and reaches libpq through `PG*` environment
  variables. It never appears in a SQL statement or a log line, and error
  messages list key names only. The recipe deliberately doesn't inline the
  password in a DuckDB `CREATE SECRET` statement: DuckDB echoes the statement
  text in some parser errors, so the password could end up in the run log.
- **Read-only source.** Postgres is attached `READ_ONLY`; a read-only role adds
  a second layer.
- **RLS doesn't follow the data.** Supabase's RLS policies protect the source,
  not the MotherDuck copy. Copy only the schemas and tables that the MotherDuck
  readers may see, and keep `auth` and `vault` out unless you need them.
- **Quoted identifiers and literals.** Schema and table names are quoted before
  they reach SQL. Config is input from whoever runs the Flight, so keep it non-secret.

## Status

Tested on MotherDuck staging (Oct 20, 2026), with a Render Postgres 18 database
standing in for Supabase because it uses the same `DATABASE_URL` → libpq code
path. The `demo_shop` schema has 4 tables and 18,187 rows. Results:

- A full refresh matched the source row counts, added `_ingested_at`, and wrote the load log.
- A rerun was idempotent.
- The `TABLES` and `EXCLUDE_TABLES` filters, an unknown table name, and a wrong
  secret key all behaved as described.
- The non-Supabase host warned without blocking.
- A probe run in the Flights runtime found no IPv6 route, and reached the
  Supabase session and transaction pooler hosts over IPv4.

The Supabase host checks were tested locally with fake URLs. Not tested: a real
Supabase project, so not the session pooler itself, the IPv4 add-on, RLS with a
custom role, or `verify-full` with the Supabase root certificate.

## Learn more

- Generic Postgres version: [flight-postgres-ingest](../flight-postgres-ingest).
- Supabase connection strings and poolers: [supabase.com/docs](https://supabase.com/docs/guides/database/connecting-to-postgres).
- Flights outbound IPs and networking: [Flights concepts](https://motherduck.com/docs/concepts/flights/).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
- DuckDB `postgres` extension: [duckdb.org/docs](https://duckdb.org/docs/stable/core_extensions/postgres).
- Files: [`flight.py`](flight.py), [`requirements.txt`](requirements.txt) (`duckdb` only; the `postgres` extension is a core DuckDB extension loaded at run time).
