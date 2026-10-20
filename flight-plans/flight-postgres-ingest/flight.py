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

Secret (TYPE flights) -> Postgres connection, EITHER as one URL:
    `DATABASE_URL` = postgres://user:password@host:5432/dbname?sslmode=require
    (the form Render, Heroku, Neon, Supabase and most hosts hand out; a
    `sslmode` in the query string is honoured)
OR as params named as the libpq env vars:
    Required: `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`
    Optional: `PGPORT`, `PGSSLMODE`
    Example:
        CREATE SECRET pg IN motherduck (
            TYPE flights,
            PARAMS MAP {
                'PGHOST':     '<your-postgres-host>',
                'PGPORT':     '5432',
                'PGDATABASE': '<your_database>',
                'PGUSER':     '<YOUR_USER>',
                'PGPASSWORD': '<YOUR_PASSWORD>',
                'PGSSLMODE':  'require'
            }
        );
    (A legacy secret named `pg` with HOST/PORT/... params still works.)
Config (non-secret env vars; an empty value means the default):
    MOTHERDUCK_HOST  - staging host; exported as `motherduck_host` before connect.
    TARGET_DATABASE  - MotherDuck database to write into (default: postgres_ingest).
    TARGET_SCHEMA    - rename the target schema (only with a single source schema).
    INCLUDED_SCHEMAS / EXCLUDED_SCHEMAS  - comma-separated schema names.
    INCLUDED_TABLES  / EXCLUDED_TABLES   - comma-separated `schema.table` or bare `table`.
    SECRET_NAME      - name of the flights secret, to prefer its namespaced params.
    URL_KEY          - secret param holding the URL (default: DATABASE_URL).
    MAX_RETRIES (5)
    RETRY_BASE_SECONDS (2)
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

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

# Postgres connection params from the flight secret. Params named as libpq vars
# (PGHOST, ...) arrive bare and are libpq-ready; a legacy secret named `pg` with
# HOST/PORT/... params arrives namespaced as pg_HOST, pg_PORT, ...
PG_PARAMS = (
    ("PGHOST", "pg_HOST", None, True),
    ("PGPORT", "pg_PORT", "5432", False),
    ("PGDATABASE", "pg_DATABASE", None, True),
    ("PGUSER", "pg_USER", None, True),
    ("PGPASSWORD", "pg_PASSWORD", None, True),
    ("PGSSLMODE", "pg_SSLMODE", "prefer", False),
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


def env(name: str, default: str = "") -> str:
    """Read a config value, treating unset and empty the same, so a key stored on the
    Flight as "" (to allow per-run overrides) still means the default."""
    return (os.environ.get(name) or "").strip() or default


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
    if included_tables and fqtn not in included_tables and table not in included_tables:
        return False
    if fqtn in excluded_tables or table in excluded_tables:
        return False
    return True


def unknown_includes(
    tables: list[tuple[str, str]],
    included_schemas: frozenset[str],
    included_tables: frozenset[str],
) -> list[str]:
    """Names in INCLUDED_SCHEMAS / INCLUDED_TABLES that match no discovered base table.
    A typo (or a view, which is never copied) should stop the run, not silently copy
    less than intended."""
    schemas = {s for s, _ in tables}
    names = {f"{s}.{t}" for s, t in tables} | {t for _, t in tables}
    return sorted(included_schemas - schemas) + sorted(included_tables - names)


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


def resolve_pg_env() -> None:
    """Put the Postgres connection into libpq env vars, so the password never appears in
    SQL or logs.

    A flights secret injects each param as `<secret>_<PARAM>` and, when no other secret
    uses the same name, also as the bare `<PARAM>`. With SECRET_NAME set the namespaced
    form wins; otherwise the bare form is used, then the legacy `pg_*` form. A URL in
    `URL_KEY` (default DATABASE_URL) takes precedence over individual params."""
    secret = env("SECRET_NAME")

    def get(param: str) -> str | None:
        return (secret and os.environ.get(f"{secret}_{param}")) or os.environ.get(param)

    url = (get(env("URL_KEY", "DATABASE_URL")) or "").strip()
    if url:
        u = urlparse(url)
        if u.scheme not in ("postgres", "postgresql"):
            raise RuntimeError("The connection URL in the secret is not a postgres:// URL (value not printed)")
        os.environ.update({
            "PGHOST": u.hostname or "",
            "PGPORT": str(u.port or 5432),
            "PGDATABASE": u.path.lstrip("/"),
            "PGUSER": unquote(u.username or ""),
            "PGPASSWORD": unquote(u.password or ""),
        })
        sslmode = parse_qs(u.query).get("sslmode", [None])[0]
        if sslmode:
            os.environ["PGSSLMODE"] = sslmode

    missing = []
    for libpq_var, legacy_var, default, required in PG_PARAMS:
        value = os.environ.get(libpq_var) if url else None
        value = value or get(libpq_var) or os.environ.get(legacy_var) or default
        if not value:
            if required:
                missing.append(libpq_var)
            continue
        os.environ[libpq_var] = str(value)
    if missing:
        # Name the keys that *are* there (never their values) so a mismatch is obvious.
        found = sorted(k for k in os.environ if k.endswith(
            ("_DATABASE_URL", "_PGHOST", "_PGUSER", "_PGPASSWORD", "_PGDATABASE", "_HOST")))
        raise RuntimeError(
            f"Missing Postgres connection params {missing}: set DATABASE_URL or PGHOST/"
            f"PGDATABASE/PGUSER/PGPASSWORD in the flights secret. Secret keys found: {found}"
        )
    check_host(os.environ["PGHOST"])


def check_host(host: str) -> None:
    """Catch two common wrong-URL mistakes from managed Postgres hosts before connecting."""
    if host.startswith("dpg-") and "." not in host:
        raise RuntimeError(
            "PGHOST has no domain; this looks like Render's Internal Database URL, which only "
            "works inside Render. Store the External Database URL instead."
        )
    if "-pooler" in host:
        log.warning("PGHOST is a pooled (PgBouncer) endpoint; a direct endpoint is more "
                    "reliable for long bulk reads.")


def attach_postgres(con: duckdb.DuckDBPyConnection) -> None:
    """Wire up the read-only Postgres source so tables can be streamed out, keeping the
    password out of SQL by passing credentials through libpq env vars.
    ATTACHes READ_ONLY as `pg`."""
    resolve_pg_env()
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
        "  source_host          VARCHAR,"
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


def load_table(
    con: duckdb.DuckDBPyConnection, target_db: str, schema: str, table: str, tgt_schema: str
) -> int:
    """Perform the entire data movement for one table as a single atomic, idempotent,
    streaming CTAS. returns the row count the CTAS reports as inserted."""
    tgt_table = f"{quote_ident(target_db)}.{quote_ident(tgt_schema)}.{quote_ident(table)}"
    src_table = f"{PG_ALIAS}.{quote_ident(schema)}.{quote_ident(table)}"
    return con.execute(f"CREATE OR REPLACE TABLE {tgt_table} AS SELECT * FROM {src_table}").fetchone()[0]


def record_success(
    con: duckdb.DuckDBPyConnection, target_db: str, run_id: str,
    schema: str, table: str, tgt_schema: str, rows_loaded: int, attempts: int,
    started_at: datetime, finished_at: datetime, update_ts: datetime,
) -> None:
    """After success, append a row to the audit table"""
    con.execute(
        f"INSERT INTO {quote_ident(target_db)}.main.flight_tracker "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, os.environ.get("PGHOST", ""), schema, table, target_db, tgt_schema, table,
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
    TARGET_DB = env("TARGET_DATABASE", "postgres_ingest")
    TARGET_SCHEMA = env("TARGET_SCHEMA")
    MAX_RETRIES = max(1, int(env("MAX_RETRIES", "5")))
    RETRY_BASE_SECONDS = float(env("RETRY_BASE_SECONDS", "2"))
    INCLUDED_SCHEMAS = csv_set("INCLUDED_SCHEMAS")
    EXCLUDED_SCHEMAS = csv_set("EXCLUDED_SCHEMAS")
    INCLUDED_TABLES = csv_set("INCLUDED_TABLES")
    EXCLUDED_TABLES = csv_set("EXCLUDED_TABLES")

    log.info("Run %s -> target %r", RUN_ID, TARGET_DB)

    con = connect_motherduck()
    attach_postgres(con)

    all_tables = [
        (s, t) for (s, t) in discover_base_tables(con)
        if is_selected(s, t, frozenset(), frozenset(), frozenset(), frozenset())
    ]
    unknown = unknown_includes(all_tables, INCLUDED_SCHEMAS, INCLUDED_TABLES)
    if unknown:
        raise RuntimeError(f"Not found among Postgres base tables (views are never copied): {unknown}")
    selected = [
        (s, t) for (s, t) in all_tables
        if is_selected(s, t, INCLUDED_SCHEMAS, EXCLUDED_SCHEMAS, INCLUDED_TABLES, EXCLUDED_TABLES)
    ]
    log.info("Discovered %d base table(s); %d selected after filters", len(all_tables), len(selected))

    # A scheduled run that silently copies nothing hides a misconfiguration; fail instead.
    if not selected:
        raise RuntimeError("No tables to copy after applying the INCLUDED_* / EXCLUDED_* filters")
    if TARGET_SCHEMA and len({s for (s, _) in selected}) > 1:
        raise RuntimeError("TARGET_SCHEMA can only be used when copying a single source schema")

    ensure_target(con, TARGET_DB)

    # Pre-create the target schemas (mirroring source schema names) once.
    for sch in sorted({TARGET_SCHEMA or s for (s, _) in selected}):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_ident(TARGET_DB)}.{quote_ident(sch)}")

    started_all = datetime.now(timezone.utc)
    failed: list[str] = []
    drifted: list[str] = []
    succeeded = 0
    rows_total = 0

    for schema, table in selected:
        fqtn = f"{schema}.{table}"
        tgt_schema = TARGET_SCHEMA or schema
        started = datetime.now(timezone.utc)
        retryer = Retrying(
            stop=stop_after_attempt(MAX_RETRIES),
            wait=wait_exponential(multiplier=RETRY_BASE_SECONDS, max=60) + wait_random(0, 1),
            reraise=True,
        )
        try:
            rows = retryer(load_table, con, TARGET_DB, schema, table, tgt_schema)
            attempts = retryer.statistics.get("attempt_number", 1)
            finished = datetime.now(timezone.utc)
            record_success(con, TARGET_DB, RUN_ID, schema, table, tgt_schema, rows,
                           attempts, started, finished, datetime.now(timezone.utc))
            succeeded += 1
            rows_total += rows
            log.info("OK   %-50s %12d rows (attempts=%d)", fqtn, rows, attempts)
            # Recount in Postgres: a live source can change mid-copy. Warn, don't fail.
            try:
                src_rows = con.execute(
                    f"SELECT count(*) FROM {PG_ALIAS}.{quote_ident(schema)}.{quote_ident(table)}"
                ).fetchone()[0]
            except Exception as exc:  # noqa: BLE001 - the copy itself succeeded
                log.warning("     could not recount %s in Postgres: %s", fqtn, exc)
                src_rows = rows
            if src_rows != rows:
                drifted.append(fqtn)
                log.warning("     %s now has %d rows in Postgres (changed during the copy?)",
                            fqtn, src_rows)
        except Exception as exc:  # noqa: BLE001 - per-table isolation is intentional
            attempts = retryer.statistics.get("attempt_number", 1)
            failed.append(fqtn)
            log.error("FAIL %-50s (attempts=%d) %s: %s", fqtn, attempts, type(exc).__name__, exc)

    total_seconds = (datetime.now(timezone.utc) - started_all).total_seconds()
    log.info("Summary: %d succeeded, %d failed, %d rows in %.1fs (run %s)",
             succeeded, len(failed), rows_total, total_seconds, RUN_ID)

    if drifted:
        log.warning("Row-count drift (re-run when the source is quiet): %s", ", ".join(drifted))
    if failed:
        log.error("Failed tables: %s", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
