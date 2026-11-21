---
title: Keep Postgres Row History and a Current Table With a dlt Flight
id: flight-dlt-append-delete-insert
description: >-
  A reusable Flight that reads Postgres incrementally with dlt, appends every
  changed row to a raw history layer, and applies the new rows to a curated
  current-state table with a transactional DELETE + INSERT instead of MERGE.
  Use it when you need row history, or when MERGE on large, frequently updated
  tables is too slow.
type: template
category: ingestion
features: [flights]
tags: [postgres, dlt, ingest]
prompt: >-
  I want to sync Postgres tables into MotherDuck incrementally, keep every
  version of each row, and keep a current-state table without running MERGE.
  Help me adapt the "Keep Postgres Row History and a Current Table With a dlt
  Flight" recipe to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-dlt-append-delete-insert
published_date: 2026-11-19
---

# Keep Postgres Row History and a Current Table With a dlt Flight

A single-file Flight that syncs Postgres tables into MotherDuck in two layers:

- **raw** keeps every version of every row. Incremental tables are read on a
  cursor column such as `updated_at` and only appended to.
- **curated** keeps the current row per key. New rows from raw are applied with
  `DELETE` + `INSERT` inside one transaction, never with `MERGE INTO`.

[dlt](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck) does the
Postgres read, keeps the incremental cursor in MotherDuck, and handles schema
changes. The transform is plain SQL on MotherDuck. Both run in the same Flight
run, ingest first.

Pick this over [flight-postgres-ingest](../flight-postgres-ingest) when:

| Pick this one | Pick flight-postgres-ingest |
|---|---|
| You need the history of each row | A current copy is enough |
| Large tables with many updates, synced often | Mostly full refresh, or append-mostly tables |
| You want dlt's schema evolution and cursor state | You want one file with no dependencies beyond `duckdb` |

## How it works

`flight.py` runs a fixed sequence:

1. **Check config and connect.** Validate every setting in one pass, then build
   the connection from the secret and stop on connection details that can't
   work from a Flight (see [Provider checks](#provider-checks)). The probe
   retries for `PROBE_TIMEOUT_S`, so a scaled-to-zero Postgres can wake up.
2. **Ingest with dlt** into `<TARGET_DATABASE>.<RAW_SCHEMA>`.
   - A table in `INCREMENTAL_TABLES` is read on its cursor column (rows at or
     after the last cursor seen) and appended.
   - Every other table is replaced.
   - dlt stamps each row with `_dlt_load_id`, one increasing id per run, and
     stores the cursors in `<RAW_SCHEMA>._dlt_pipeline_state`. A Flight starts
     with an empty container every run, and `sync_destination()` restores the
     cursors from MotherDuck.
3. **Transform** into `<TARGET_DATABASE>.<CURATED_SCHEMA>`.
   - A replaced table is copied: `CREATE OR REPLACE TABLE curated.t AS SELECT ...`.
   - An incremental table's first run builds curated from the latest row per
     key in all of raw.
   - Later runs take the raw rows whose `_dlt_load_id` is above the largest one
     in curated, keep the latest per key (`max_by(row, (cursor, _dlt_load_id))`),
     drop any older than curated's row, then run in one transaction:

     ```sql
     BEGIN TRANSACTION;
     CREATE TEMP TABLE _changes AS ...;          -- the new rows
     DELETE FROM curated.t USING _changes ...;    -- their keys
     INSERT INTO curated.t BY NAME SELECT * FROM _changes;
     COMMIT;
     ```

   - Curated keeps `_dlt_load_id`, so curated is its own watermark: there is no
     state table to drift out of sync.
4. **Verify** that every incremental table in curated has one row per key and
   every key raw has seen. Otherwise the run fails.

### Why DELETE + INSERT, not MERGE

- DuckDB runs a whole-row `UPDATE` on its slow path. `DELETE` + `INSERT` was
  about 3x faster than `MERGE INTO` at 5M rows in our tests.
- `INSERT ... BY NAME` matches columns by name, so column order never matters.
- The transaction makes the pair atomic. A reader, such as a Dive refreshing
  during the run, sees the old rows or the new rows, never the gap between the
  `DELETE` and the `INSERT`.

### How dlt runs the stages

`extract`, then `normalize`, then `load`, each for all tables before the next
starts. One run is one load package with one `_dlt_load_id`, so one slow table
holds up the small ones.

- **extract** reads one table at a time.
- **normalize** runs in the Flight's process.
- **load** runs up to 8 jobs at once. A job is a file, and dlt doesn't split a
  table into several files by default, so that is up to 8 tables at once.

Temp disk peaks during normalize, when both the extracted and the normalized
copy of everything read in the run are on disk. With the default `sqlalchemy`
backend, plan for about **twice** the size of the data read. The `pyarrow`
backend writes Parquet at extract, which normalize imports without rewriting,
so its peak is lower. Nothing builds up across runs.

### Provider checks

Before connecting, the Flight stops on connection details that can't work from
a Flight, with a message that names the fix:

| Host | Stops on |
|---|---|
| Render | An Internal Database URL (`dpg-...` with no domain) |
| Supabase pooler (`*.pooler.supabase.com`) | A user without the `.<project_ref>` suffix, or the transaction pooler port `6543` |
| Supabase direct (`db.<ref>.supabase.co`) | The direct host, which is IPv6-only, while Flights connect over IPv4 |
| Azure (`*.postgres.database.azure.com`) | A private-access (`.private.`) host, or the PgBouncer port `6432` |
| Any | A URL that still contains Supabase's `[YOUR-PASSWORD]` placeholder |

A Neon `-pooler` host and `sslmode=disable` log a warning instead.

## Questions to answer

- Postgres source: a connection URL (`DATABASE_URL`), or host, port,
  database, user, SSL mode, and password? Store it in a MotherDuck Flights
  secret, never in chat, config, or code. The same `pg` secret as
  [flight-postgres-ingest](../flight-postgres-ingest) works.
- Which tables to sync (`TABLES`), and from which schema?
- Which tables are incremental, and on which cursor and key columns? The cursor
  must be set on every write (`updated_at`, not `created_at`), and the key must
  be unique per row. Everything else is a full replace.
- Which `TARGET_DATABASE`, and which schema names for raw and curated?
- How large is each incremental table today? The first run reads all of it
  (see [Caveats](#caveats)). Is a copy already in MotherDuck to start from?
- What schedule (cron, UTC) matches how fresh curated needs to be? Keep it
  longer than a typical run.

## Caveats

- **Settle the questions first.** An agent following this recipe should work
  through [Questions to answer](#questions-to-answer) before it opens the
  secret form (the MCP `create_secret` tool) or creates the Flight.
- **Hard deletes are never seen.** A row deleted in Postgres stays in raw and
  curated. Use soft deletes (a `deleted_at` that moves `updated_at`), or
  replace the table now and then by dropping its raw and curated copies.
- **The cursor must move on every write.** A row updated without moving the
  cursor column forward is missed.
- **The first run reads every row** of each incremental table, so it must fit
  the Flight's temp disk at about twice the data size and the run-time limit
  (1 hour on Lite, 8 hours by default on Business). For a large table, start
  from an existing copy (below) or bound the first read with an
  `initial_value`.
- **raw grows forever.** It is append-only by design. Decide a retention rule,
  for example deleting raw rows older than a year whose keys have a newer row.
- **Tables are read one after another**, so a run doesn't capture one
  consistent moment across tables.
- **Overlapping runs.** A Flight doesn't skip a scheduled run while the last
  one is still going. Two overlapping transforms can conflict on the `DELETE`;
  the loser fails and the next run catches up.
- **Changing a table's cursor column** resets dlt's cursor for that table and
  reads it into raw once more. The transform's dedupe absorbs the repeat.
- **Columns added in Postgres** arrive in raw as nullable and are added to
  curated before the next apply. A column dropped in Postgres stays in curated
  as `NULL`.

### Starting a large table from an existing MotherDuck copy

If the table is already in MotherDuck, from an earlier sync, a one-off copy, or
a Parquet backfill, seed curated from it instead of reading all of Postgres:

1. Pick a cut-off a little before the copy's last refresh. Overlap is
   harmless (the newest row per key wins); a gap is not.
2. Seed curated in the target database:

   ```sql
   CREATE DATABASE IF NOT EXISTS pg_dlt;
   CREATE SCHEMA IF NOT EXISTS pg_dlt.curated;
   CREATE TABLE pg_dlt.curated.orders AS SELECT * FROM old_db.main.orders;
   ```

3. Add the cut-off as the fourth field:
   `INCREMENTAL_TABLES=orders:updated_at:id:2026-11-05T08:00:00Z`. dlt reads
   only rows at or after it on the first run and ignores it afterwards.
4. Run the Flight once and compare `curated.orders` with the source.

raw then holds history only from the cut-off on. Column types come from the
copy, and the first apply casts raw's values into them, failing loudly on an
impossible cast.

## What you'll adjust

No code edits are required. Everything is read from Flight config and a
MotherDuck Flights secret.

| Knob | Default | Purpose |
|---|---|---|
| `TABLES` | (required) | Comma-separated source tables. |
| `INCREMENTAL_TABLES` | (none) | Comma-separated `table:cursor_column:key[\|key2][:initial_value]`, for example `orders:updated_at:id,order_lines:updated_at:order_id\|line_no`. `initial_value` is an ISO date or timestamp, or an integer, and bounds only the first read. Tables not listed are replaced every run. |
| `SOURCE_SCHEMA` | `public` | Postgres schema to read. |
| `TARGET_DATABASE` | `pg_dlt` | MotherDuck database, created if absent. |
| `RAW_SCHEMA` | `raw` | Schema for the history layer and dlt's state. |
| `CURATED_SCHEMA` | `curated` | Schema for the current-state tables. |
| `DLT_BACKEND` | `sqlalchemy` | `pyarrow` is faster on wide tables and uses less temp disk. Column types then come from the Postgres schema, so pick it before the first run. |
| `SECRET_NAME` | `pg` | The Flights secret's name, used to read its namespaced params (`pg_DATABASE_URL`, ...). |
| `PROBE_TIMEOUT_S` | `90` | Seconds to wait for Postgres to answer. |
| Flights secret | (required) | `DATABASE_URL`, or `PGHOST`, `PGUSER`, `PGPASSWORD`, and optionally `PGDATABASE` (`postgres`), `PGPORT` (`5432`), `PGSSLMODE` (`require`). |

An empty config value means the default.

**Tables on different schedules.** Create one Flight per group of tables, each
with its own `RAW_SCHEMA`: dlt keeps each group's cursors there. The groups can
share `CURATED_SCHEMA`, since each Flight writes only its own tables.

## Run it

You need a MotherDuck token and a reachable Postgres. For a local run, set the
same names the secret would inject, bare:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://readonly_user:your_password@your-postgres-host:5432/your_database?sslmode=require'
export TABLES=customers,orders
export INCREMENTAL_TABLES=orders:updated_at:id
export TARGET_DATABASE=pg_dlt_test
uv run --python 3.12 --with-requirements requirements.txt flight.py
```

A local Postgres without TLS needs `sslmode=disable` in the URL. Run it again
after changing a row in Postgres: the log shows how many rows were applied,
replaced, and new.

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

- `name`: a Flight name, for example `pg_dlt_append`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `['pg']`
- `config`: at least `TABLES`, `INCREMENTAL_TABLES`, and `TARGET_DATABASE`.
  The connection stays in the secret.
- `instance_type`: `F16` or larger for a large first read; the instance size
  sets the temp disk.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`.

Create it without a schedule and run it once with
`MD_RUN_FLIGHT(flight_id := ...)`. The id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`. Check the run's log with
`MD_GET_FLIGHT_LOGS(flight_id := ..., run_number := ...)`, run it a second
time to see an incremental apply, then add a schedule.

If the Postgres host restricts inbound traffic by IP, allowlist the Flight
egress IPs for your MotherDuck region, listed under
[Connecting to external systems](https://motherduck.com/docs/concepts/flights/#connecting-to-external-systems).

## Security

- **The connection lives in a secret.** The password comes from a
  `TYPE flights` secret. Error messages and logs strip it from connection
  strings. Plain Flight `config` isn't treated as sensitive.
- **TLS by default.** `PGSSLMODE` defaults to `require`, and a URL with
  `sslmode=disable` logs a warning.
- **Read-only source.** The Flight only runs `SELECT` in Postgres. Give it a
  role with `SELECT` only:

  ```sql
  CREATE ROLE motherduck_flight LOGIN PASSWORD '<strong password>';
  GRANT CONNECT ON DATABASE your_database TO motherduck_flight;
  GRANT USAGE ON SCHEMA public TO motherduck_flight;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO motherduck_flight;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO motherduck_flight;
  ```

- **Quoted identifiers.** Database, schema, table, cursor, and key names are
  quoted in every statement the transform builds.

## Learn more

- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- dlt's MotherDuck destination and `sql_database` source:
  [dlthub.com/docs](https://dlthub.com/docs/dlt-ecosystem/verified-sources/sql_database).
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
- Files: [`flight.py`](flight.py) (the Flight source) and
  [`requirements.txt`](requirements.txt) (`dlt` with its MotherDuck and SQL
  database extras, the Postgres driver, and `duckdb` pinned to the runtime's
  version).
