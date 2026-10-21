---
title: "Flight recipe: Azure Database for PostgreSQL → MotherDuck full-refresh and incremental copy"
id: flight-azure-postgres-ingest
description: >-
  A Flight that copies tables from Azure Database for PostgreSQL Flexible Server
  into MotherDuck: a full refresh per table by default, or an opt-in
  watermark-based incremental upsert per table, staged through local Parquet,
  with Azure-specific connection checks and a per-table load log. Use it to keep
  an Azure Postgres database copied into MotherDuck once or on a schedule.
type: template
category: ingestion
features: [flights]
tags: [postgres]
prompt: >-
  I want to copy my Azure Database for PostgreSQL into MotherDuck and keep it in
  sync. Help me adapt the "Flight recipe: Azure Database for PostgreSQL →
  MotherDuck full-refresh and incremental copy" recipe to my own data and use
  case, using it as a guide. Only ask me what you need; I'll put my connection
  details in a MotherDuck secret, not in chat:
  https://motherduck.com/docs/cookbook/flight-azure-postgres-ingest
published_date: 2026-10-20
---

# Flight recipe: Azure Database for PostgreSQL → MotherDuck full-refresh and incremental copy

A single-file Flight that copies tables from an Azure Database for PostgreSQL
Flexible Server into a MotherDuck database, keeping schema names. By default,
every table gets a full refresh on every run. Tables that have a "last changed"
column and a key can be switched to an incremental upsert, so each run pulls
only new and changed rows. Rows are staged as local Parquet files first, so
memory stays bounded on large tables. It follows the
[flight-postgres-ingest](../flight-postgres-ingest) pattern, with Azure's
connection details and common mistakes built in.

## How it works

[`flight.py`](flight.py) runs a fixed sequence:

1. **Read the connection** from a Flights secret: a `DATABASE_URL`, or
   `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` (`PGPORT` and `PGSSLMODE`
   optional). The namespaced `<secret>_<KEY>` form is preferred over the bare
   `<KEY>`. An `sslmode` in the URL is honoured; otherwise it is `require`.
2. **Azure checks** before connecting:
   - port `6432` (Azure's built-in PgBouncer) is switched to the direct port `5432`, with a warning;
   - a legacy Single Server user name (`user@servername`) stops the run with a clear message;
   - a host that doesn't end in `.postgres.database.azure.com` gets a warning, and a private-access (`.private.`) host gets a warning that Flights can't reach it.
3. **Connect** to MotherDuck (`MOTHERDUCK_HOST` if set) and attach Postgres
   `READ_ONLY` through the DuckDB `postgres` extension. Credentials reach libpq
   through `PG*` environment variables, never SQL text.
4. **Resolve tables:** the explicit `TABLES` list, or every regular and
   partitioned table in `SCHEMAS` (individual partitions are skipped, because
   the parent returns their rows). Then remove `EXCLUDE_TABLES`. Names in
   `TABLES` that don't exist stop the run before anything is copied.
5. **Load each table** by streaming it to local Parquet under `/tmp`
   (`BATCH_FILE_SIZE` per file), then building a MotherDuck staging table one
   file at a time:
   - **Full refresh** (default): `CREATE OR REPLACE` the target from the staging table, so readers see the old copy until the swap.
   - **Incremental** (tables listed in `INCREMENTAL`): pull rows where the watermark column is `>=` the target's current maximum, then in one transaction `DELETE ... USING` matching keys and `INSERT BY NAME`. Using `>=` re-pulls rows that share the last timestamp, and the upsert makes that harmless. If the target table doesn't exist yet, the first run is a full load.
6. **Log** one row per table to `<TARGET_DB>._meta.load_log` (flight id, run id,
   mode, status, rows staged, rows in target, timing, error). The run exits
   non-zero if any table failed; the other tables still load unless
   `FAIL_FAST=true`.

## Questions to answer

- Is there a MotherDuck Flights secret with the connection yet? If not, create
  one (for example `azure_pg`) with key `DATABASE_URL`, or with
  `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD`. Use the plain role name (not
  `user@server`) and a read-only role. Enter the values in the MotherDuck UI,
  never in chat, source or config.
- Is the server set to **Public access**, and does its firewall allow the
  Flights outbound IPs for your MotherDuck region? (See [Azure specifics](#azure-specifics).)
- What to copy: which schemas (`SCHEMAS`), or which exact tables (`TABLES`)? Anything to exclude?
- Which tables, if any, should be incremental? Each needs a watermark column
  that increases whenever a row is inserted or updated (such as `updated_at`),
  plus a key that identifies a row.
- Which MotherDuck database receives the copy? The default is `azure_pg_raw`.
  Tables with the same names are replaced.
- Once (on demand), or on a schedule (cron, UTC)?

## Azure specifics

- **Networking.** Flights connect from a fixed set of outbound IP addresses,
  two per MotherDuck region, listed under "Connecting to external systems" in
  the [Flights concepts docs](https://motherduck.com/docs/concepts/flights/).
  Find your region with `SELECT region FROM md_user_info();`. In the Azure
  portal, open the server's **Networking** page, keep **Public access** on, and
  add a firewall rule for each of those addresses. Servers that use
  **Private access (VNet integration)** or only a private endpoint can't be
  reached from Flights. A connect timeout usually means a missing firewall rule.
- **Port 5432, not 6432.** Port 6432 is Flexible Server's built-in PgBouncer.
  Long bulk reads are more reliable on the direct port, so the Flight switches
  `6432` to `5432` on Azure hosts.
- **User name format.** Flexible Server uses the plain role name. The
  `user@servername` form belongs to the retired Single Server, and the Flight
  stops with a clear message if it sees it. Microsoft Entra user names such as
  `alice@contoso.com` are let through.
- **TLS.** Flexible Server enforces TLS by default, so the default
  `sslmode=require` works. `verify-full` in the URL is honoured. That needs
  the Azure root certificate to be available to libpq, which this recipe
  doesn't set up.
- **Microsoft Entra ID instead of a password.** This isn't wired in. The
  `flight.py` docstring shows the change: add `azure-identity`, store the service
  principal in the secret, and pass a fresh access token as `PGPASSWORD`
  (tokens last about an hour, so fetch one per run).

## Caveats

- **Full refresh re-reads the whole table on every run.** Cost scales with
  table size, not change volume. Use `INCREMENTAL` for large tables that have a
  reliable watermark column.
- **Incremental does not see deletes.** A row deleted in Postgres stays in
  MotherDuck. It also misses updates that don't bump the watermark column, and
  rows whose key columns are `NULL` can't be matched. Run an occasional full
  refresh (clear `INCREMENTAL` for one run) to resync.
- **Incremental needs a stable schema.** `INSERT BY NAME` fails if the source
  gains a column the target doesn't have. Run one full refresh after a schema change.
- **Not carried over:** primary keys, indexes and constraints. **Not removed:**
  a table dropped in Postgres stays in MotherDuck until you drop it.
- **Column types** come from DuckDB's Postgres type mapping, then a Parquet
  round trip. Integers, decimals, timestamps with and without time zone, UUID,
  JSON, intervals and arrays keep their types, but Postgres enums arrive as
  `VARCHAR`. Exotic types (ranges, `hstore`, composite types) may arrive as
  `VARCHAR` or fail; exclude such tables.
- **Per-run overrides need stored keys.** `MD_RUN_FLIGHT` (or the MCP
  `run_flight` tool) can only override config keys that are already stored on the
  Flight. Create the Flight with every key you may change later, set to its
  default (an empty value means "use the default").

### Troubleshooting

| Symptom | Fix |
|---|---|
| `Missing Postgres connection fields [...] Secret keys visible to this run: [...]` | Set `SECRET_NAME` / `URL_KEY` to match the key names shown (values are never printed) |
| `PGUSER uses the legacy Single Server 'user@servername' format` | Store the plain role name in the secret |
| Connect timeout | Add Azure firewall rules for your region's Flights outbound IPs; check that Public access is on |
| `password authentication failed` | Wrong password, or an expired Entra token used as the password |
| `TABLES not found as tables in the source: [...]` | A typo, a missing schema prefix (bare names mean `public`), or a view (views are not copied) |
| `INCREMENTAL columns not in <table>: [...]` | Fix the watermark or key column name in `INCREMENTAL` |
| `Bad INCREMENTAL entry` | Use the `schema.table:watermark_column:key_column` format (composite keys below the table) |
| `config key ... is not defined on this Flight` | Add the key to the Flight's stored config first |

## What you'll adjust

No code edits are required. Everything is read from Flight config and the Flights secret.

| Knob | Default | Purpose |
|---|---|---|
| `SECRET_NAME` | `azure_pg` | Flights secret name |
| `URL_KEY` | `DATABASE_URL` | Key in the secret that holds the connection URL |
| `TARGET_DB` | `azure_pg_raw` | MotherDuck database to write into (created if missing) |
| `SCHEMAS` | (none) | Comma-separated schemas; every table in them is copied |
| `TABLES` | (none) | Comma-separated `schema.table` (bare names mean `public`); overrides `SCHEMAS` |
| `EXCLUDE_TABLES` | (none) | Comma-separated `schema.table` or bare table names to skip |
| `INCREMENTAL` | (none) | Comma-separated `schema.table:watermark_column:key_column` entries; other tables get a full refresh |
| `BATCH_FILE_SIZE` | `100MB` | Parquet file size per load batch |
| `FAIL_FAST` | `false` | `true` stops at the first failing table |
| `MOTHERDUCK_HOST` | unset | Override the MotherDuck host (for example a non-production one). Leave unset normally. |
| Flights **secret** | (required) | `DATABASE_URL`, or `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` (+ optional `PGPORT`, `PGSSLMODE`) |

Set either `SCHEMAS` or `TABLES`. Empty config values fall back to the defaults.

For a composite key, join the key columns with `|`, for example
`public.orders:updated_at:id,public.line_items:updated_at:order_id|line_no`.

## Run it

For a local run, set the same names the secret would inject:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://reader:password@myserver.postgres.database.azure.com:5432/appdb?sslmode=require'
export SCHEMAS=public
# optional:
# export TARGET_DB=azure_pg_raw
# export INCREMENTAL='public.orders:updated_at:id'
uv run --with-requirements requirements.txt flight.py
```

Your laptop's IP must also be allowed by the Azure firewall for a local run.

### Deploy as a Flight

First store the connection as a **Flights secret**, for example named
`azure_pg` with key `DATABASE_URL` (UI:
[Settings > Secrets](https://app.motherduck.com/settings/secrets), type
**Flights**). The prefilled link
`https://app.motherduck.com/settings/secrets?action=create&type=flights&name=azure_pg&params=DATABASE_URL`
carries only the key name. Or use `CREATE SECRET azure_pg IN motherduck (TYPE flights, PARAMS MAP {...})`
from a write-enabled connection.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function, passing:

- `name`: for example `azure_postgres_copy`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["azure_pg"]`
- `config`: `SCHEMAS` or `TABLES`, `TARGET_DB`, plus every other knob you may
  override per run (`TABLES`, `EXCLUDE_TABLES`, `INCREMENTAL`, `FAIL_FAST`, ...),
  set to its default or `""`

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`. Run once with `MD_RUN_FLIGHT(flight_id := ...)` and
check that the tables exist. Each table should have a `succeeded` row in
`<TARGET_DB>._meta.load_log` with `rows_staged` equal to `rows_in_target` on a
full refresh. Then add a schedule if one is wanted.

## Security

- **Connection in a secret, never config or SQL.** The URL or password comes
  from a `TYPE flights` secret and reaches libpq through `PG*` environment
  variables. It never appears in a SQL statement or a log line, and error
  messages list key names only.
- **Read-only source.** Postgres is attached `READ_ONLY`; use a read-only role as well.
- **Narrow firewall rules.** Allow only the Flights outbound IPs for your
  region, not `0.0.0.0` to `255.255.255.255` or "Allow public access from any Azure service".
- **Quoted identifiers and literals.** Schema, table and column names and the
  watermark value are quoted before they reach SQL. Config is input from
  whoever runs the Flight, so keep it non-secret.

## Status

Tested on MotherDuck staging (Oct 20, 2026), with a Render Postgres 18 database
standing in for Azure because both use the same libpq and `DATABASE_URL` code
path. The `demo_shop` schema has 4 tables and 18,187 rows. Results:

- A full refresh matched the source row counts, and the load log was written.
- A rerun was idempotent.
- An incremental run (a `timestamptz` watermark with microseconds, an integer
  watermark, and a composite key) staged only the rows at the watermark and left
  no duplicates.
- The `TABLES` and `EXCLUDE_TABLES` filters, an unknown table name, and a wrong
  secret key all behaved as described.
- The Azure host check warned without blocking.

Not tested: a real Azure server, so not Azure firewall rules, Flights' egress
from Azure's side, TLS against Azure, or Entra ID tokens.

## Learn more

- Generic Postgres version: [flight-postgres-ingest](../flight-postgres-ingest).
- Flights outbound IPs and networking: [Flights concepts](https://motherduck.com/docs/concepts/flights/).
- Azure Flexible Server networking: [learn.microsoft.com](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-networking-public).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
- DuckDB `postgres` extension: [duckdb.org/docs](https://duckdb.org/docs/stable/core_extensions/postgres).
- Files: [`flight.py`](flight.py), [`requirements.txt`](requirements.txt) (`duckdb` only; the `postgres` extension is a core DuckDB extension loaded at run time).
