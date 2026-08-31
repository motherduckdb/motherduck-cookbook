---
title: Copy a Postgres Table into MotherDuck With the DuckDB ADBC Extension
id: flight-adbc-ingest
description: >-
  A demo Flight that copies one Postgres table into MotherDuck through the
  DuckDB adbc community extension and the PostgreSQL ADBC driver installed with
  dbc. Use it as a starting point for reading any database that has an ADBC
  driver from a Flight.
type: template
category: ingestion
features: [flights]
tags: [adbc, planetscale, postgres, ingest]
prompt: >-
  I want a simple Flight that copies a table from a Postgres database into
  MotherDuck through the DuckDB ADBC extension and an ADBC driver installed
  with dbc. Help me adapt the "Copy a Postgres Table into MotherDuck With the
  DuckDB ADBC Extension" recipe to my own data and use case, using it as a
  guide: https://motherduck.com/docs/cookbook/flight-adbc-ingest
published_date: 2026-08-31
---

# Copy a Postgres Table into MotherDuck With the DuckDB ADBC Extension

A single-file demo Flight that copies one Postgres table into MotherDuck. The
interesting part is the transport: instead of the DuckDB `postgres` extension,
it uses the [`adbc` community extension](https://duckdb.org/community_extensions/extensions/adbc.html)
with the standard PostgreSQL ADBC driver. Swap the driver name and connection
URI and the same pattern reads Snowflake, MySQL, SQLite, or anything else with
an [ADBC](https://arrow.apache.org/adbc/) driver. The tested source is a
PlanetScale Postgres database.

## How it works

`flight.py` runs three steps:

1. Install the PostgreSQL ADBC driver with `dbc`, Columnar's ADBC driver
   manager. `dbc` is a pip package from `requirements.txt`, so the driver
   install is one subprocess call. The code then points `ADBC_DRIVER_PATH` at
   the virtualenv directory where `dbc` places drivers.
2. Write an ADBC connection profile, a small TOML file holding the source
   connection URI. The password comes from a MotherDuck Flights secret and is
   percent-encoded into the URI.
3. Connect to MotherDuck, load the `adbc` extension, and copy the table with
   one statement:
   `CREATE OR REPLACE TABLE <target> AS SELECT * FROM read_adbc('profile://planetscale', 'SELECT * FROM <schema>.<table>')`.
   With no `SOURCE_TABLE` configured, it first asks the source's
   `information_schema` for the first user table.

`read_adbc()` sends the query text to the source database, which returns the
result as Arrow data. The copy is one statement with no intermediate files.

## Questions to answer

- Source connection: host, port, database, user, and which password? The
  password goes in a MotherDuck Flights secret; the rest is plain config.
- Which `schema.table` to copy, or rely on discovery of the first user table?
- Which `TARGET_DATABASE` should receive the copy?
- Is a full refresh of one table enough? This is a demo. For a config-driven,
  multi-table Postgres mirror with retries and an audit log, use the
  `flight-postgres-ingest` template instead.

## Caveats

- **Demo scope.** One table, full refresh, no retries, no audit log.
- **For Postgres specifically, the `postgres` extension is the direct route.**
  ADBC earns its place when the source has an ADBC driver but no DuckDB
  extension, or when you want one connection pattern across several systems.
- **Community extension.** `adbc` is built by Columnar and published in the
  DuckDB community extension repository for DuckDB 1.5.4 and later.
- **The extension also supports `ATTACH ... (TYPE adbc)`.** This Flight uses
  `read_adbc()` because it hands the query text to the source, so filters run
  there instead of after transfer.

## What you'll adjust

| Knob | Default | Purpose |
|---|---|---|
| `PGHOST` | (required) | Source Postgres host. |
| `PGUSER` | (required) | Source Postgres user. |
| `PGPORT` | `5432` | Source Postgres port. |
| `PGDATABASE` | `postgres` | Source Postgres database. |
| `SOURCE_TABLE` | (first user table) | `schema.table` to copy; a bare table name means `public.<table>`. |
| `TARGET_DATABASE` | `adbc_demo` | MotherDuck database for the copy (created if absent). The table lands in its `main` schema. |
| `planetscale` **secret** | (required) | Flights secret with one param, `cdc_password`, injected as env var `planetscale_cdc_password`. Using a different secret or param name means updating that env var name in `flight.py`. |

## Run it

You need a MotherDuck account and token plus a reachable Postgres source.

```bash
export MOTHERDUCK_TOKEN=your_token_here
export PGHOST=your-postgres-host
export PGUSER=your_user
export PGDATABASE=your_database
export planetscale_cdc_password=your_password
# optional: export SOURCE_TABLE=public.orders TARGET_DATABASE=adbc_demo
uv run --with-requirements requirements.txt flight.py
```

The run prints one line, for example
`Copied bench.events -> "adbc_demo".main."events" (51001 rows)`.

### Deploy as a Flight

First store the password as a Flights secret named `planetscale` (UI:
[Settings > Secrets](https://app.motherduck.com/settings/secrets), type
**Flights**, one param named `cdc_password`). Then create the Flight with the
`MD_CREATE_FLIGHT` SQL function, passing:

- `name`: a Flight name, for example `adbc_ingest`
- `source_code`: `flight.py`
- `requirements_txt`: `requirements.txt`
- `flight_secret_names`: `['planetscale']`
- `config`: at least `PGHOST`, `PGUSER`, and `PGDATABASE`, plus `SOURCE_TABLE`
  and `TARGET_DATABASE` if the defaults don't fit

Create it without a schedule, run it once with `MD_RUN_FLIGHT(flight_id := ...)`
(the id is returned by `MD_CREATE_FLIGHT` and listed by `MD_FLIGHTS()`), and
check the target table. Add a cron schedule later if the copy should repeat.

## Security

- **Password in a secret, never in config or SQL.** It reaches the driver
  percent-encoded inside the profile URI. The profile file is written with
  owner-only permissions (0600) in the run's container.
- **Quoted identifiers.** Schema, table, and database names are quoted before
  they land in SQL, and query text sent through `read_adbc()` is
  single-quote-escaped.

## Learn more

- DuckDB `adbc` extension: [community extension page](https://duckdb.org/community_extensions/extensions/adbc.html) and [source](https://github.com/columnar-tech/duckdb-adbc-client).
- `dbc` driver manager: [docs.columnar.tech/dbc](https://docs.columnar.tech/dbc/).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Files: [`flight.py`](flight.py) (the Flight source), [`requirements.txt`](requirements.txt) (`duckdb` pinned to a MotherDuck-supported version, plus `dbc`).
