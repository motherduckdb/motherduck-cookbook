"""
AWS S3 Tables (Iceberg) -> MotherDuck batch copy flight.

Copies one or more tables from an AWS S3 Tables Iceberg catalog into a MotherDuck database.
Each table is moved by a single streaming SQL statement:

    CREATE OR REPLACE TABLE <target>."<namespace>"."<table>" AS
        SELECT * FROM <catalog>."<namespace>"."<table>";

That statement is the entire load. It is:
    ATOMIC (CREATE OR REPLACE swaps in one step)
    IDEMPOTENT (re-running fully replaces)
    STREAMING (DuckDB pipelines the scan into the write, bounded memory)

Tables land at <target>.<namespace>.<table>, preserving source namespace names. Per-table
logging lands in <target>.main.flight_tracker.

Credentials 
-------------------------------------------------
It references a persistent MotherDuck S3 secret by name (SECRET_NAME below), created once out
of band. The keys need S3 Tables catalog access (s3tables:*) in the bucket's account, plus read
on the data:

    CREATE SECRET s3_tables_secret IN MOTHERDUCK (
        TYPE S3, KEY_ID '...', SECRET '...', REGION 'us-east-1'
    );

Config (non-secret env vars)
----------------------------
    TABLE_BUCKET_ARN   S3 Tables bucket ARN (arn:aws:s3tables:<region>:<acct>:bucket/<name>).
    TARGET_DATABASE    MotherDuck database to write into (default: iceberg_ingest).
    INCLUDED_SCHEMAS / EXCLUDED_SCHEMAS   comma-separated Iceberg namespaces.
    INCLUDED_TABLES  / EXCLUDED_TABLES    comma-separated table specs: `namespace.table` or a
                                          bare `table` (matches that table in any namespace).
    MAX_RETRIES (5)
    RETRY_BASE_SECONDS (2)

Selection is case-insensitive and format tolerant: each namespace/table entry may be given with
or without double quotes ("clickbench"."hits" == clickbench.hits) and with surrounding whitespace.

To open the catalog, the attach needs one namespace that exists in the bucket. It is taken from
INCLUDED_SCHEMAS, else the namespaces named in INCLUDED_TABLES, else the sample namespace. Set
INCLUDED_SCHEMAS (or INCLUDED_TABLES) when pointing at a non-sample bucket.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone

import duckdb
from tenacity import Retrying, stop_after_attempt, wait_exponential, wait_random

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("s3tables-iceberg-ingest")

# arn:aws:s3tables:<region>:<account-id>:bucket/<bucket-name>
ARN_RE = re.compile(r"arn:aws:s3tables:[a-z0-9-]+:\d+:bucket/[A-Za-z0-9][A-Za-z0-9_-]*")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
DEFAULT_ARN = "arn:aws:s3tables:us-east-1:114325331884:bucket/clickbench-iceberg"
SAMPLE_NAMESPACE = "clickbench"  # namespace in the default sample bucket

# Postgres catalog schemas that are never copied, should they surface via discovery.
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast"}

# Persistent MotherDuck S3 secret the catalog attach references. Change it to point at another
# secret (it must be a TYPE S3 secret created IN MOTHERDUCK).
SECRET_NAME = "s3_tables_secret"

# Local catalog name the S3 Tables bucket is attached as (TYPE ICEBERG). One source of truth.
ICEBERG_ALIAS = "iceberg_src"


# --------------------------------------------------------------------------- #
# Small SQL / env helpers
# --------------------------------------------------------------------------- #
def quote_ident(ident: str) -> str:
    """Double-quote a SQL identifier, escaping embedded quotes, so names with special
    characters or reserved words are handled correctly and safely."""
    return '"' + ident.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    """Single-quote a string literal, escaping embedded single quotes."""
    return "'" + value.replace("'", "''") + "'"


def qualified_name(*parts: str) -> str:
    """Join parts into a fully-qualified SQL name with every part quoted, so identifiers from
    config or catalog discovery cannot break out of their quotes (SQL-injection safe)."""
    return ".".join(quote_ident(part) for part in parts)


def validate_identifier(name: str, value: str) -> str:
    """Reject a config value that is not a plain SQL identifier before it is used as one
    (defense in depth alongside quote_ident)."""
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def _unquote(part: str) -> str:
    """Strip one layer of surrounding double quotes from an identifier and unescape "" -> "."""
    part = part.strip()
    if len(part) >= 2 and part[0] == '"' and part[-1] == '"':
        return part[1:-1].replace('""', '"')
    return part


def _split_outside_quotes(text: str, sep: str) -> list[str]:
    """Split text on sep, ignoring separators that fall inside double quotes."""
    parts, buf, in_quotes = [], [], False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
            buf.append(ch)
        elif ch == sep and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def _split_ref(text: str) -> list[str]:
    """Parse a dotted identifier reference into its unquoted parts, honoring double quotes (a
    '.' inside quotes is literal). Empty parts (stray dots/whitespace) are dropped."""
    return [u for p in _split_outside_quotes(text, ".") if (u := _unquote(p))]


def csv_schemas(name: str) -> frozenset[str]:
    """Parse a comma-separated namespace list into a set of unquoted names, tolerant of optional
    double quotes and whitespace. Case is preserved here; matching casefolds (see is_selected)."""
    out = set()
    for entry in _split_outside_quotes(os.environ.get(name, "") or "", ","):
        parts = _split_ref(entry)
        if parts:
            out.add(".".join(parts))
    return frozenset(out)


def csv_tables(name: str) -> frozenset[tuple[str | None, str]]:
    """Parse a comma-separated table list into a set of (namespace|None, table) specs. Accepts a
    fully-qualified `namespace.table` or a bare `table`, each part optionally double-quoted, with
    surrounding whitespace. Case is preserved here; matching casefolds (see is_selected)."""
    out = set()
    for entry in _split_outside_quotes(os.environ.get(name, "") or "", ","):
        parts = _split_ref(entry)
        if not parts:
            continue
        if len(parts) == 1:
            out.add((None, parts[0]))
        else:
            out.add((".".join(parts[:-1]), parts[-1]))
    return frozenset(out)


def validate_arn(value: str) -> str:
    if not ARN_RE.fullmatch(value):
        raise ValueError(f"TABLE_BUCKET_ARN is not a valid S3 Tables bucket ARN: {value!r}")
    return value


# --------------------------------------------------------------------------- #
# Table selection
# --------------------------------------------------------------------------- #
def is_selected(
    schema: str,
    table: str,
    included_schemas: frozenset[str],
    excluded_schemas: frozenset[str],
    included_tables: frozenset[tuple[str | None, str]],
    excluded_tables: frozenset[tuple[str | None, str]],
) -> bool:
    """Decide whether a discovered table is copied. Matching is case-insensitive and format
    tolerant: schema specs are plain names and table specs are (namespace|None, table), already
    unquoted by csv_schemas/csv_tables. A bare-table spec matches that table in any namespace.
    Exclude always wins; system schemas are always dropped."""
    s = schema.casefold()
    t = table.casefold()
    inc_s = {x.casefold() for x in included_schemas}
    exc_s = {x.casefold() for x in excluded_schemas}

    if s in SYSTEM_SCHEMAS:
        return False
    if inc_s and s not in inc_s:
        return False
    if s in exc_s:
        return False

    def matches(specs: frozenset[tuple[str | None, str]]) -> bool:
        for spec_schema, spec_table in specs:
            if spec_table.casefold() == t and (spec_schema is None or spec_schema.casefold() == s):
                return True
        return False

    if included_tables and not matches(included_tables):
        return False
    if matches(excluded_tables):
        return False
    return True


def pick_attach_namespace() -> str:
    """Choose one namespace to open the catalog with (Iceberg requires a default_schema that
    exists). Prefer INCLUDED_SCHEMAS, then the namespaces named in INCLUDED_TABLES, then the
    sample. Uses the name as typed (quotes stripped), preserving case for the catalog."""
    for entry in _split_outside_quotes(os.environ.get("INCLUDED_SCHEMAS", "") or "", ","):
        parts = _split_ref(entry)
        if parts:
            return ".".join(parts)
    for entry in _split_outside_quotes(os.environ.get("INCLUDED_TABLES", "") or "", ","):
        parts = _split_ref(entry)
        if len(parts) >= 2:
            return ".".join(parts[:-1])
    return SAMPLE_NAMESPACE


# --------------------------------------------------------------------------- #
# Connection + setup
# --------------------------------------------------------------------------- #
def attach_iceberg(con: duckdb.DuckDBPyConnection, arn: str, attach_namespace: str) -> None:
    """Attach the S3 Tables bucket as a MotherDuck database of TYPE ICEBERG, using the
    persistent S3 secret. CREATE OR REPLACE makes it idempotent. Raises with a clear hint if
    the attach comes up as an error catalog (usually a missing/underprivileged secret or a
    default_schema namespace that does not exist)."""
    con.execute(
        f"CREATE OR REPLACE DATABASE {quote_ident(ICEBERG_ALIAS)} ("
        f"TYPE ICEBERG, "
        f"WAREHOUSE {quote_literal(arn)}, "
        f"ENDPOINT_TYPE 's3_tables', "
        f'"secret" {quote_ident(SECRET_NAME)}, '
        f"default_schema {quote_literal(attach_namespace)})"
    )
    is_error, message = con.execute(
        "SELECT is_error_catalog, error_message FROM MD_ATTACHED_DATABASES() WHERE alias = ?",
        [ICEBERG_ALIAS],
    ).fetchone()
    if is_error:
        raise RuntimeError(
            f"Iceberg catalog attached as an ERROR catalog: {message}\n"
            f"Check that secret {SECRET_NAME!r} exists with S3 Tables catalog (s3tables:*) "
            f"permissions in the bucket's account, and that namespace {attach_namespace!r} exists."
        )
    log.info("Attached %s as %s (TYPE ICEBERG) via secret %s", arn, ICEBERG_ALIAS, SECRET_NAME)


def ensure_target(con: duckdb.DuckDBPyConnection, target_db: str) -> None:
    """Create the target database and the audit logging table up front."""
    con.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(target_db)}")
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {qualified_name(target_db, 'main', 'flight_tracker')} ("
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
def discover_tables(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    """List the candidate source tables across every namespace in the attached catalog."""
    rows = con.execute(
        "SELECT schema_name, table_name FROM duckdb_tables() "
        "WHERE database_name = ? ORDER BY schema_name, table_name",
        [ICEBERG_ALIAS],
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def load_table(con: duckdb.DuckDBPyConnection, target_db: str, schema: str, table: str) -> int:
    """Move one table as a single atomic, idempotent, streaming CTAS. Returns the row count
    the CTAS reports as inserted."""
    tgt = qualified_name(target_db, schema, table)
    src = qualified_name(ICEBERG_ALIAS, schema, table)
    return con.execute(f"CREATE OR REPLACE TABLE {tgt} AS SELECT * FROM {src}").fetchone()[0]


def record_success(
    con: duckdb.DuckDBPyConnection, target_db: str, run_id: str,
    schema: str, table: str, rows_loaded: int, attempts: int,
    started_at: datetime, finished_at: datetime, update_ts: datetime,
) -> None:
    """After success, append a row to the audit table."""
    con.execute(
        f"INSERT INTO {qualified_name(target_db, 'main', 'flight_tracker')} "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [run_id, SECRET_NAME, schema, table, target_db, schema, table,
         rows_loaded, attempts, started_at, finished_at, update_ts],
    )


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    """Orchestrate the copy: connect, attach, discover, then load each selected table
    sequentially with per-table retries/isolation and record results."""
    RUN_ID = str(uuid.uuid4())
    ARN = validate_arn(os.environ.get("TABLE_BUCKET_ARN", DEFAULT_ARN).strip())
    TARGET_DB = validate_identifier(
        "TARGET_DATABASE",
        os.environ.get("TARGET_DATABASE", "iceberg_ingest").strip() or "iceberg_ingest",
    )
    MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))
    RETRY_BASE_SECONDS = float(os.environ.get("RETRY_BASE_SECONDS", "2"))
    INCLUDED_SCHEMAS = csv_schemas("INCLUDED_SCHEMAS")
    EXCLUDED_SCHEMAS = csv_schemas("EXCLUDED_SCHEMAS")
    INCLUDED_TABLES = csv_tables("INCLUDED_TABLES")
    EXCLUDED_TABLES = csv_tables("EXCLUDED_TABLES")

    log.info("Run %s -> target %r", RUN_ID, TARGET_DB)

    con = duckdb.connect("md:")
    attach_iceberg(con, ARN, pick_attach_namespace())
    ensure_target(con, TARGET_DB)

    all_tables = discover_tables(con)
    selected = [
        (s, t) for (s, t) in all_tables
        if is_selected(s, t, INCLUDED_SCHEMAS, EXCLUDED_SCHEMAS, INCLUDED_TABLES, EXCLUDED_TABLES)
    ]
    log.info("Discovered %d table(s); %d selected after filters", len(all_tables), len(selected))

    if not selected:
        log.warning("No tables selected - nothing to do.")
        return

    # Pre-create the target schemas (mirroring source namespace names) once.
    for sch in sorted({s for (s, _) in selected}):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {qualified_name(TARGET_DB, sch)}")

    started_all = datetime.now(timezone.utc)
    failed: list[str] = []
    succeeded = 0
    rows_total = 0

    for schema, table in selected:
        fqtn = f"{schema}.{table}"  # logging only; SQL names go through qualified_name()
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
            record_success(con, TARGET_DB, RUN_ID, schema, table, rows,
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
