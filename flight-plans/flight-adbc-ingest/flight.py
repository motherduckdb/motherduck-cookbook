"""Copy a Postgres table into MotherDuck with the DuckDB ADBC extension.

A demo Flight showing DuckDB's `adbc` community extension as a generic bridge
to any database with an ADBC driver — here, a PlanetScale Postgres database
through the standard PostgreSQL ADBC driver. Each run:

1. Installs the PostgreSQL ADBC driver with `dbc` (Columnar's ADBC driver
   manager, installed from requirements.txt).
2. Writes an ADBC connection profile pointing at the source database.
3. Loads the DuckDB `adbc` community extension and copies one table with
   `CREATE OR REPLACE TABLE ... AS SELECT * FROM read_adbc(...)`.

Config:
  PGHOST           Source Postgres host (required)
  PGUSER           Source Postgres user (required)
  PGPORT           Source Postgres port (default: 5432)
  PGDATABASE       Source Postgres database (default: postgres)
  SOURCE_TABLE     schema.table to copy (default: first user table found)
  TARGET_DATABASE  MotherDuck database to write to (default: adbc_demo)

Secrets:
  The `planetscale` flights secret provides the source password as
  `planetscale_cdc_password`.
"""

import os
import subprocess
import sys
from urllib.parse import quote

import duckdb

PROFILE = "planetscale"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name) or default
    if value is None:
        raise RuntimeError(f"Required env var {name} is not set")
    return value


def write_adbc_profile() -> None:
    """Write an ADBC connection profile for the source Postgres database."""
    uri = (
        f"postgresql://{quote(env('PGUSER'), safe='')}:"
        f"{quote(env('planetscale_cdc_password'), safe='')}"
        f"@{quote(env('PGHOST'), safe='')}:{int(env('PGPORT', '5432'))}"
        f"/{quote(env('PGDATABASE', 'postgres'), safe='')}?sslmode=require"
    )
    os.environ.setdefault("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    profile_dir = os.path.join(os.environ["XDG_CONFIG_HOME"], "adbc", "profiles")
    os.makedirs(profile_dir, exist_ok=True)
    profile_path = os.path.join(profile_dir, f"{PROFILE}.toml")
    # The profile holds the password, so keep it owner-only.
    with open(os.open(profile_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600), "w") as f:
        f.write(f'profile_version = 1\ndriver = "postgresql"\n\n[Options]\nuri = "{uri}"\n')


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def adbc_query(postgres_sql: str) -> str:
    """A DuckDB query that runs postgres_sql on the source through read_adbc()."""
    escaped = postgres_sql.replace("'", "''")
    return f"SELECT * FROM read_adbc('profile://{PROFILE}', '{escaped}')"


def main():
    subprocess.run(["dbc", "install", "postgresql"], check=True)
    # dbc installs into the active virtualenv; tell the extension's driver
    # manager where to look.
    os.environ["ADBC_DRIVER_PATH"] = os.path.join(sys.prefix, "etc", "adbc", "drivers")
    write_adbc_profile()

    con = duckdb.connect("md:")
    con.execute("INSTALL adbc FROM community; LOAD adbc;")

    if source_table := os.environ.get("SOURCE_TABLE"):
        schema, _, table = source_table.rpartition(".")
        schema = schema or "public"
    else:
        row = con.execute(
            adbc_query(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' "
                "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
                "ORDER BY 1, 2 LIMIT 1"
            )
        ).fetchone()
        if row is None:
            raise RuntimeError("No user tables found in the source database")
        schema, table = row

    # The copy lands in the target database's main schema.
    target_database = env("TARGET_DATABASE", "adbc_demo")
    quoted_target = f"{quote_ident(target_database)}.main.{quote_ident(table)}"
    con.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(target_database)}")
    rows = con.execute(
        f"CREATE OR REPLACE TABLE {quoted_target} AS "
        + adbc_query(f"SELECT * FROM {quote_ident(schema)}.{quote_ident(table)}")
    ).fetchone()[0]
    print(f"Copied {schema}.{table} -> {quoted_target} ({rows} rows)")


if __name__ == "__main__":
    main()
