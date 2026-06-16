"""
Postgres -> MotherDuck batch ELT flight

Mirrors PostgreSQL base tables into a MotherDuck database using the DuckDB postgres
extension. Each table is moved by a single streaming SQL statement:

    CREATE OR REPLACE TABLE <target>."<schema>"."<table>" AS SELECT * FROM pg."<schema>"."<table>";

That statement is the entire load. It is:
    ATOMIC (CREATE OR REPLACE swaps in one step)
    IDEMPOTENT (re-running fully replaces)
    STREAMING (DuckDB pipelines the scan into the write, bounded memory)

Per-table logging lands in  `<target>.main.flight_tracker`.

Inputs
------
Note that inputs are case sensitive (use uppercase).

Secret `pg` (TYPE flights) -> Postgres connection params: 
    Required: `HOST`, `DATABASE`, `USER`, `PASSWORD`
    Optional: `PORT`, `SSLMODE`
    Example:
        CREATE SECRET pg IN motherduck (
            TYPE flights,
            PARAMS MAP {
                'HOST':     '<your-postgres-host>',
                'PORT':     '5432',
                'DATABASE': '<your_database>',
                'USER':     '<YOUR_USER>',
                'PASSWORD': '<YOUR_PASSWORD>',
                'SSLMODE':  'require'
            }
        );
Config (non-secret env vars):
    MOTHERDUCK_HOST  - staging host; exported as `motherduck_host` before connect.
    TARGET_DATABASE  - MotherDuck database to write into (default: postgres_ingest).
    INCLUDED_SCHEMAS / EXCLUDED_SCHEMAS  - comma-separated schema names.
    INCLUDED_TABLES  / EXCLUDED_TABLES   - comma-separated, fully qualified schema.table.
    MAX_RETRIES (5) 
    RETRY_BASE_SECONDS (2)
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone

import duckdb
from tenacity import (
    Retrying,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("pg2md")

# PostgreSQL system schemas that are never mirrored. 
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast"}

# Postgres connection params from the flight secret. 
# The secret injects each as `<secret_name>_<KEY>`
PG_PARAMS = (
    ("HOST", "PGHOST", None, True),
    ("PORT", "PGPORT", "5432", False),
    ("DATABASE", "PGDATABASE", None, True),
    ("USER", "PGUSER", None, True),
    ("PASSWORD", "PGPASSWORD", None, True),
    ("SSLMODE", "PGSSLMODE", "prefer", False),
)

# Local DuckDB catalog name the source Postgres database is ATTACHed as. Referenced by
# attach_postgres(), discover_base_tables(), and load_table() -- one source of truth.
PG_ALIAS = "pg"


# --------------------------------------------------------------------------- #
# Small SQL / env helpers
# --------------------------------------------------------------------------- #
def quote_ident(ident: str) -> str:
    """Build safe SQL by quoting an identifier the way DuckDB/Postgres expect, so
    names with special characters or reserved words are handled correctly. Pass any
    identifier string and use the returned double-quoted, escaped form in SQL text."""
    return '"' + ident.replace('"', '""') + '"'


def csv_set(name: str) -> frozenset[str]:
    """Turn a comma-separated env var into a clean set for membership filtering, which
    the schema/table selection gates rely on. Pass the env var name and receive a set
    of trimmed, non-empty values (empty set if unset)."""
    raw = os.environ.get(name, "") or ""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


# --------------------------------------------------------------------------- #
# Table selection
# --------------------------------------------------------------------------- #
def is_selected(
    schema: str,
    table: str,
    included_schemas: frozenset[str],
    excluded_schemas: frozenset[str],
    included_tables: frozenset[str],
    excluded_tables: frozenset[str],
) -> bool:
    """Decide whether a discovered base table is mirrored, applying the two include/exclude
    gates where exclude always wins and system schemas are excluded."""
    fqtn = f"{schema}.{table}"
    if schema in SYSTEM_SCHEMAS or schema.startswith("pg_temp") or schema.startswith("pg_toast"):
        return False
    if included_schemas and schema not in included_schemas:
        return False
    if schema in excluded_schemas:
        return False
    if included_tables and fqtn not in included_tables:
        return False
    if fqtn in excluded_tables:
        return False
    return True


# --------------------------------------------------------------------------- #
# Connection + setup
# --------------------------------------------------------------------------- #
def connect_motherduck() -> duckdb.DuckDBPyConnection:
    """Open the MotherDuck connection that backs the whole flight, targeting the configured
    host when one is set."""
    host = os.environ.get("MOTHERDUCK_HOST")
    if host:
        os.environ["motherduck_host"] = host
        log.info("Targeting MotherDuck host: %s", host)
    else:
        log.info("MOTHERDUCK_HOST not set; using runtime default MotherDuck host")
    return duckdb.connect("md:")


def attach_postgres(con: duckdb.DuckDBPyConnection, secret_name: str) -> None:
    """Wire up the read-only Postgres source so tables can be streamed out, keeping the
    password out of SQL by passing credentials through libpq env vars. 
    ATTACHes READ_ONLY as `pg`."""
    for key, libpq_var, default, required in PG_PARAMS:
        env_var = f"{secret_name}_{key}"
        value = os.environ.get(env_var, default)
        if value is None:
            if required:
                raise RuntimeError(f"Required Postgres secret env var {env_var!r} is not set")
            continue
        os.environ[libpq_var] = str(value)

    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    con.execute(f"ATTACH '' AS {PG_ALIAS} (TYPE postgres, READ_ONLY)")
    log.info(
        "Attached Postgres %s:%s/%s (read-only, sslmode=%s)",
        os.environ["PGHOST"], os.environ["PGPORT"],
        os.environ["PGDATABASE"], os.environ["PGSSLMODE"],
    )


def ensure_target(con: duckdb.DuckDBPyConnection, target_db: str) -> None:
    """Create the target database and the audit logging table up front."""
    target = quote_ident(target_db)
    con.execute(f"CREATE DATABASE IF NOT EXISTS {target}")
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {target}.main.flight_tracker ("
        "  run_id               VARCHAR,"
        "  flight_secret_name   VARCHAR,"
        "  source_schema        VARCHAR,"
        "  source_table         VARCHAR,"
        "  destination_database VARCHAR,"
        "  destination_schema   VARCHAR,"
        "  destination_table    VARCHAR,"
        "  rows_loaded          BIGINT,"
        "  attempts             INTEGER,"
        "  started_at           TIMESTAMP,"
        "  finished_at          TIMESTAMP,"
        "  update_ts            TIMESTAMP"
        ")"
    )


# --------------------------------------------------------------------------- #
# Discovery + per-table load
# --------------------------------------------------------------------------- #
def discover_base_tables(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """List the candidate source tables to iterate on"""
    rows = con.execute(
        f"SELECT table_schema, table_name FROM postgres_query('{PG_ALIAS}', "
        "'SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_type = ''BASE TABLE''') "
        "ORDER BY table_schema, table_name"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def load_table(con: duckdb.DuckDBPyConnection, target_db: str, schema: str, table: str) -> int:
    """Perform the entire data movement for one table as a single atomic, idempotent,
    streaming CTAS. returns the row count the CTAS reports as inserted."""
    tgt_table = f"{quote_ident(target_db)}.{quote_ident(schema)}.{quote_ident(table)}"
    src_table = f"{PG_ALIAS}.{quote_ident(schema)}.{quote_ident(table)}"
    return con.execute(f"CREATE OR REPLACE TABLE {tgt_table} AS SELECT * FROM {src_table}").fetchone()[0]


def record_success(
    con: duckdb.DuckDBPyConnection, target_db: str, run_id: str, secret_name: str,
    schema: str, table: str, rows_loaded: int, attempts: int,
    started_at: datetime, finished_at: datetime, update_ts: datetime,
) -> None:
    """After success, append a row to the audit table"""
    con.execute(
        f"INSERT INTO {quote_ident(target_db)}.main.flight_tracker "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, secret_name, schema, table, target_db, schema, table,
         rows_loaded, attempts, started_at, finished_at, update_ts],
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    """Orchestrate the full-refresh ELT: connect, attach, discover, then load each
    table sequentially with per-table retries/isolation and record results."""
    # Run config, read once from the environment and referenced as needed below.
    RUN_ID = str(uuid.uuid4())
    TARGET_DB = os.environ.get("TARGET_DATABASE", "postgres_ingest")
    MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))
    RETRY_BASE_SECONDS = float(os.environ.get("RETRY_BASE_SECONDS", "2"))
    INCLUDED_SCHEMAS = csv_set("INCLUDED_SCHEMAS")
    EXCLUDED_SCHEMAS = csv_set("EXCLUDED_SCHEMAS")
    INCLUDED_TABLES = csv_set("INCLUDED_TABLES")
    EXCLUDED_TABLES = csv_set("EXCLUDED_TABLES")
    # MotherDuck Flights secret holding the Postgres connection; its params arrive as
    # <SECRET_NAME>_HOST, <SECRET_NAME>_PORT, ... Change it to point at another secret.
    SECRET_NAME = "pg"

    log.info("Run %s -> target %r", RUN_ID, TARGET_DB)

    con = connect_motherduck()
    attach_postgres(con, SECRET_NAME)
    ensure_target(con, TARGET_DB)

    all_tables = discover_base_tables(con)
    selected = [
        (s, t) for (s, t) in all_tables
        if is_selected(s, t, INCLUDED_SCHEMAS, EXCLUDED_SCHEMAS, INCLUDED_TABLES, EXCLUDED_TABLES)
    ]
    log.info("Discovered %d base table(s); %d selected after filters", len(all_tables), len(selected))

    if not selected:
        log.warning("No tables selected - nothing to do.")
        return

    # Pre-create the target schemas (mirroring source schema names) once.
    for sch in sorted({s for (s, _) in selected}):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(TARGET_DB)}.{quote_ident(sch)}")

    started_all = datetime.now(timezone.utc)
    failed: list[str] = []
    succeeded = 0
    rows_total = 0

    for schema, table in selected:
        fqtn = f"{schema}.{table}"
        started = datetime.now(timezone.utc)
        retryer = Retrying(
            stop=stop_after_attempt(MAX_RETRIES),
            wait=wait_exponential(multiplier=RETRY_BASE_SECONDS, max=60) + wait_random(0, 1),
            reraise=True,
        )
        try:
            rows = retryer(load_table, con, TARGET_DB, schema, table)
            attempts = retryer.statistics.get("attempt_number", 1)
            finished = datetime.now(timezone.utc)
            record_success(con, TARGET_DB, RUN_ID, SECRET_NAME, schema, table, rows,
                           attempts, started, finished, datetime.now(timezone.utc))
            succeeded += 1
            rows_total += rows
            log.info("OK   %-50s %12d rows (attempts=%d)", fqtn, rows, attempts)
        except Exception as exc:  # noqa: BLE001 - per-table isolation is intentional
            attempts = retryer.statistics.get("attempt_number", 1)
            failed.append(fqtn)
            log.error("FAIL %-50s (attempts=%d) %s: %s", fqtn, attempts, type(exc).__name__, exc)

    total_seconds = (datetime.now(timezone.utc) - started_all).total_seconds()
    log.info("Summary: %d succeeded, %d failed, %d rows in %.1fs (run %s)",
             succeeded, len(failed), rows_total, total_seconds, RUN_ID)

    if failed:
        log.error("Failed tables: %s", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
