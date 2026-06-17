---
title: Ingest Snowflake Tables into MotherDuck From a Flight
id: flight-snowflake-ingest
description: >-
  A reusable Flight that ingests Snowflake tables into MotherDuck in two phases:
  discover builds an editable inventory of source tables, and move copies the
  selected ones via Arrow. Use it for a code-driven, re-runnable Snowflake to
  MotherDuck ingest with a control table you can curate, for example as part of
  a migration off Snowflake.
type: template
category: ingestion
features: [flights]
tags: [snowflake, ingest, migrate]
prompt: >-
  I want a code-driven, re-runnable Flight that ingests Snowflake tables into MotherDuck
  in two phases (discover an editable inventory, then move the selected tables via
  Arrow), as part of migrating off Snowflake. Help me adapt the "Ingest Snowflake Tables
  into MotherDuck From a Flight" recipe to my own data and use case, using it as a
  guide:
  https://motherduck.com/docs/cookbook/flight-snowflake-ingest
published_date: 2026-06-09
---

# Ingest Snowflake Tables into MotherDuck From a Flight

A single-file Flight that ingests Snowflake tables into MotherDuck in two phases.
It is the Snowflake side of a migration onto MotherDuck: keep Snowflake as the
source of truth while you build out MotherDuck, and re-run the Flight to copy the
tables you select. It is deliberately just the ingest mechanics: no UI, no
per-table Flights, just `discover` and `move`.

- **DISCOVER** connects to Snowflake, enumerates the tables in scope from
  `INFORMATION_SCHEMA.TABLES`, and writes that inventory to a MotherDuck control
  table with a `selected` flag you can edit to choose what to move.
- **MOVE** reads the inventory and selection back from MotherDuck, pulls each
  selected table from Snowflake via Arrow, loads it into MotherDuck with
  `CREATE OR REPLACE TABLE ... AS SELECT`, and records a per-table ledger.

A `MODE` config picks the phase: `discover`, `move`, or `all` (discover then
move in one run). There is no first-class DuckDB `snowflake` extension, so the
path is `snowflake-connector-python` (Arrow fetch) into a DuckDB-registered Arrow
object, then a CTAS into `md:`.

## How it works

`flight.py` connects to MotherDuck (`md:`), creates `TARGET_DB`, the control
schema, and the target schema if missing, then runs one or both phases:

1. **DISCOVER.** Determine the databases in scope: just `SNOWFLAKE_DATABASE` when
   set, otherwise every database the connection can see (via `SHOW TERSE
   DATABASES`). For each, query its `INFORMATION_SCHEMA.TABLES` (optionally
   filtered to `SNOWFLAKE_SCHEMA`), listing `table_catalog, table_schema,
   table_name, row_count, bytes, table_type`, and skip any database the role
   cannot read. Write the combined result to the inventory control table with an
   added `selected BOOLEAN` (defaulted to `table_type = 'BASE TABLE'`) and a
   `discovered_at` timestamp. The write is a `CREATE OR REPLACE`, so re-discovery
   refreshes the inventory.
2. **MOVE.** Read the rows where `selected` is true. For each, run
   `SELECT * FROM <db>.<schema>.<table>` in Snowflake, fetch the result as one
   Arrow table with `cursor.fetch_arrow_all()`, register that Arrow object with
   the DuckDB connection, and `CREATE OR REPLACE TABLE
   <TARGET_DB>.<TARGET_SCHEMA>.<table> AS SELECT * FROM <arrow>`. Each table is
   idempotent (replace on re-run). A failure on one table is recorded and the run
   continues. Every move (including dry-run skips) writes one row to the ledger:
   source table, dest table, row count, status, detail, run timestamp, dry-run
   flag.

`MODE=all` runs DISCOVER then MOVE in a single run.

## Questions to answer

- What is in scope: one Snowflake database (set `SNOWFLAKE_DATABASE`), or every visible database (leave it unset)? And optionally one schema?
- Which account, user, warehouse, and role should the Flight connect with, and are the credentials stored as a MotherDuck Flights secret? Discovery needs a warehouse.
- Where should the inventory, ledger, and moved tables live in MotherDuck (`TARGET_DB`, `TARGET_SCHEMA`, `CONTROL_SCHEMA`)?
- After discovery, which tables should actually move? Edit the `selected` column, or accept the default rule (base tables only).
- Are any in-scope tables large enough to need staging or a `MAX_ROWS_PER_TABLE` sample first?
- Which service account token can write to `TARGET_DB`?

## Caveats

- **No credential-free smoke test.** Unlike the `sample_data`-backed templates
  here, this Flight needs a real, reachable Snowflake account even to discover.
  There is no offline dry run of the Snowflake side; the closest thing is
  `DRY_RUN=true`, which still discovers but does not copy data.
- **Discovery needs an active warehouse.** `SHOW TERSE DATABASES` runs without
  one, but `INFORMATION_SCHEMA.TABLES` does not: with no warehouse every
  per-database scan fails with "No active warehouse selected" and is skipped, so
  the inventory comes back empty. Set `SNOWFLAKE_WAREHOUSE` (or give the connecting
  user a default warehouse). Verified live: an account-wide scan of 4 databases
  found 315 tables once a warehouse was set, and 0 before.
- **Account-wide scan includes shared and system databases.** Leaving
  `SNOWFLAKE_DATABASE` unset scans everything `SHOW TERSE DATABASES` returns,
  which can include `SNOWFLAKE` (account views) and `SNOWFLAKE_SAMPLE_DATA`. Only
  base tables are pre-`selected`, so views are inventoried but not moved by
  default. Curate `selected` before moving, and note that the move destination is
  keyed on table name alone, so same-named tables across databases or schemas
  would collide in `TARGET_DB`.
- **Re-discovery resets manual `selected` edits.** DISCOVER does a
  `CREATE OR REPLACE` of the inventory, so any hand edits to `selected` are lost
  on the next discover. To keep a curated selection, either stop re-discovering,
  apply a deterministic `UPDATE ... SET selected = (...)` rule after each
  discover, or maintain your selection in a separate table you join against.
- **Snowflake compute and egress cost money.** Every discover query and every
  `SELECT *` runs on a Snowflake warehouse and transfers data out. Use a small
  warehouse, scope tightly with `SNOWFLAKE_SCHEMA`, and consider
  `MAX_ROWS_PER_TABLE` for a sampled first pass.
- **`ACCOUNT_USAGE` lags.** The account-wide alternative,
  `SNOWFLAKE.ACCOUNT_USAGE.TABLES`, spans all databases but is delayed by up to
  ~90 minutes and lists dropped tables until purged. This template uses the live,
  exact `INFORMATION_SCHEMA.TABLES`, which is scoped to one database.
- **Very large tables use memory.** MOVE fetches each table's full result into
  one in-memory Arrow table. A Flight has a ~16GB RAM ceiling and ~150GB of local
  scratch on `/tmp`. For a table that will not fit in memory, stage it to local
  Parquet on `/tmp` first (`COPY ... TO '/tmp/...parquet'` from Snowflake or a
  paged Arrow write), then load from Parquet, instead of one big `fetch_arrow_all`.
- **`DRY_RUN` defaults to true.** MOVE writes real tables, so the first deploy
  logs the move plan and writes ledger rows without copying. Set `DRY_RUN=false`
  once you have reviewed the inventory.

## What you'll adjust

Every knob is read from Flight config/env, so you adapt this template by setting
config values rather than editing code. The Snowflake password (and, if you like,
the user) is the exception: it comes from a MotherDuck Flights secret, never from
plain config.

| Knob | Where | Default | Purpose |
|---|---|---|---|
| `MODE` | config / env | `all` | Which phase to run: `discover`, `move`, or `all`. |
| `SNOWFLAKE_ACCOUNT` | config / env | (required) | Snowflake account identifier, for example `ab12345.eu-west-1`. |
| `SNOWFLAKE_USER` | config / env or Flight secret | (required) | Snowflake login user. Can sit in plain config, or alongside the password in a Flights secret (arrives as `<secret_name>_SNOWFLAKE_USER`); `flight.py` resolves either. |
| `SNOWFLAKE_WAREHOUSE` | config / env | (unset) | Warehouse for the discovery and move queries. Effectively required: querying `INFORMATION_SCHEMA.TABLES` needs an active warehouse, so without one discovery finds 0 tables. (`SHOW DATABASES` itself does not need a warehouse.) |
| `SNOWFLAKE_ROLE` | config / env | (unset) | Role to assume. Optional. |
| `SNOWFLAKE_DATABASE` | config / env | (unset) | Source database to scan. Leave it unset to scan every database the connection can see (enumerated with `SHOW TERSE DATABASES`), or set it to scope to one database. Validated as a SQL identifier when set. |
| `SNOWFLAKE_SCHEMA` | config / env | (unset) | Optional single schema name to narrow discovery to, applied in each database scanned. Validated as a SQL identifier. |
| `TARGET_DB` | config / env | `flights_demo` | MotherDuck database that receives the control tables and moved tables. |
| `TARGET_SCHEMA` | config / env | `main` | Schema in `TARGET_DB` where moved tables land. |
| `CONTROL_SCHEMA` | config / env | `main` | Schema in `TARGET_DB` that holds the inventory and ledger tables. |
| `INVENTORY_TABLE` | config / env | `snowflake_inventory` | Control table name: the inventory of source tables with a `selected` flag. |
| `LEDGER_TABLE` | config / env | `snowflake_move_runs` | Per-table move ledger name. |
| `MAX_ROWS_PER_TABLE` | config / env | `0` | Optional `LIMIT` per table during move (`0` means no cap). Useful for a sampled first pass. |
| `DRY_RUN` | config / env | `true` | When true, MOVE logs the plan and writes ledger rows without copying data. Set `false` to copy for real. |
| `SNOWFLAKE_PASSWORD` | Flight secret / env var | (required) | The Snowflake credential. Add it through a MotherDuck Flights secret (in the UI, see below), never in code or config. As a Flight the secret arrives as `<secret_name>_SNOWFLAKE_PASSWORD`; `flight.py` resolves either name. |
| `MOTHERDUCK_TOKEN` | Flight-injected | (Flight-injected) | Auth for MotherDuck. Select a token on the Flight; never hard-code it. |

## Run it

You need a MotherDuck account and an access token, a reachable Snowflake account,
and the Snowflake password. There is no credential-free smoke test: the discover
phase has to reach a real Snowflake account (see [Caveats](#caveats)).

```bash
export MOTHERDUCK_TOKEN=your_token_here
export SNOWFLAKE_ACCOUNT=ab12345.eu-west-1
export SNOWFLAKE_USER=your_user
export SNOWFLAKE_WAREHOUSE=your_wh        # required for discovery: INFORMATION_SCHEMA needs an active warehouse
# export SNOWFLAKE_DATABASE=SOURCE_DB     # omit to scan EVERY visible database
# export SNOWFLAKE_SCHEMA=PUBLIC          # optional: narrow to one schema
export SNOWFLAKE_PASSWORD=your_password   # local only; use a secret for a Flight

# Phase 1: build the inventory in MotherDuck, then edit `selected` as needed.
MODE=discover uv run --with-requirements requirements.txt flight.py

# Phase 2: copy the selected tables. DRY_RUN defaults to true (plan only);
# set DRY_RUN=false to copy for real once you have reviewed the inventory.
MODE=move DRY_RUN=false uv run --with-requirements requirements.txt flight.py
```

After `MODE=discover`, inspect and curate the inventory in MotherDuck, for
example:

```sql
SELECT table_schema, table_name, table_type, row_count, bytes, selected
FROM flights_demo.main.snowflake_inventory
ORDER BY bytes DESC;

-- Move only one schema's base tables:
UPDATE flights_demo.main.snowflake_inventory
SET selected = (table_schema = 'PUBLIC' AND table_type = 'BASE TABLE');
```

`MODE=all` runs discover then move in one go (still honoring `DRY_RUN`), which is
handy once your selection rule is stable.

### Deploy as a Flight

Store the Snowflake credentials as a MotherDuck **Flights secret**. The simplest
way is the MotherDuck UI: open
[Settings > Secrets](https://app.motherduck.com/settings/secrets), add a secret of
type **Flights**, and give it `SNOWFLAKE_USER` and `SNOWFLAKE_PASSWORD` parameters.
If you would rather use SQL, you can create the same secret from the DuckDB client
or any write-enabled SQL connection (read-only connections reject `CREATE SECRET`):

```sql
CREATE SECRET snowflake_creds IN motherduck (
  TYPE flights,
  PARAMS MAP {
    'SNOWFLAKE_USER': 'your_user',
    'SNOWFLAKE_PASSWORD': 'your_password'
  }
);
```

A `TYPE flights` secret injects each param under the env var
`<secret_name>_<PARAM>`, not the bare param name: the params above arrive as
`snowflake_creds_SNOWFLAKE_USER` and `snowflake_creds_SNOWFLAKE_PASSWORD`. (DuckDB
lowercases the unquoted secret name into the prefix.) `flight.py` handles this: it
reads the bare `SNOWFLAKE_USER` / `SNOWFLAKE_PASSWORD` for local runs and otherwise
picks up any env var ending in `_SNOWFLAKE_USER` / `_SNOWFLAKE_PASSWORD`, so the
secret name you choose does not matter.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL
is checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `snowflake_ingest`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the non-secret knobs, for example
  `{"MODE": "discover", "SNOWFLAKE_ACCOUNT": "ab12345.eu-west-1", "SNOWFLAKE_WAREHOUSE": "your_wh", "SNOWFLAKE_DATABASE": "SOURCE_DB", "TARGET_DB": "flights_demo", "DRY_RUN": "true"}`
- `flight_secret_names`: `["snowflake_creds"]` so the user and password are
  injected (as `snowflake_creds_SNOWFLAKE_USER` / `snowflake_creds_SNOWFLAKE_PASSWORD`;
  `flight.py` resolves them)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create the Flight with `MODE=discover`, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm the inventory lands in MotherDuck. Curate `selected`, then run
`MODE=move` with `DRY_RUN=false` (a config change, not a new Flight version) to
copy. Schedule it only if you want a recurring refresh.

## Security

- **Credentials in a secret, not config.** `SNOWFLAKE_USER` and
  `SNOWFLAKE_PASSWORD` are read from a MotherDuck `TYPE flights` secret (or local
  env vars) at runtime, never hard-coded or placed in Flight `config`. The same
  resolver supports a private-key credential if you swap the connection params.
- **Identifier validation.** Every config-supplied database, schema, and table
  name (`SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `TARGET_DB`, `TARGET_SCHEMA`,
  `CONTROL_SCHEMA`, `INVENTORY_TABLE`, `LEDGER_TABLE`) is checked against
  `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL runs. Per-table names read from the
  inventory at runtime are quoted with `ident()` before interpolation.
- **Parameterized data.** Inventory rows and ledger rows (table names, row
  counts, status, detail) are written with bound parameters, and the optional
  schema filter in discovery is a bound parameter, never string-formatted SQL.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Snowflake connector and Arrow fetch:
  [snowflake-connector-python](https://docs.snowflake.com/en/developer-guide/python-connector/python-connector)
  its `fetch_arrow_all()` Arrow fetch path.
- Snowflake metadata sources:
  [`INFORMATION_SCHEMA.TABLES`](https://docs.snowflake.com/en/sql-reference/info-schema/tables)
  and [`ACCOUNT_USAGE.TABLES`](https://docs.snowflake.com/en/sql-reference/account-usage/tables).
- Deeper MotherDuck or DuckDB questions: use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (`duckdb`,
  `snowflake-connector-python[pandas]`, `pyarrow`).
