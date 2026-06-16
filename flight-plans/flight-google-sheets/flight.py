"""Google Sheets <-> MotherDuck two-way sync flight.

Configure a list of Google Sheets to import to MotherDuck and/or
a list of queries to run and export to Google Sheets. 
This uses the GSheets DuckDB Community Extension. 

Import from Google Sheets with a single SQL statement:
    CREATE OR REPLACE TABLE <db>.<schema>.<table> AS SELECT * FROM read_gsheet('<url>');

Export to Google Sheets by running a MotherDuck query, exporting in-memory to Apache Arrow, 
then copying from Arrow to Google Sheets (still with the GSheets extension). 

Both import and export are a full-refresh operation. 
Each import and export is retried on errors.
Success or failure is logged in an audit log in <target>.main.gsheets_sync_log.

Config (env vars):
    Secret `GSHEETS_SECRET_NAME` (default gsheets) injects
    `<name>_SERVICE_ACCOUNT_JSON`: a Google service-account key (never expires;
    the extension mints short-lived OAuth tokens from it). Share each sheet with
    its client_email (Viewer for sources, Editor for destinations).
    TARGET_DATABASE - import destination database (default google_sheets)
    TARGET_SCHEMA - import destination schema (default main).
    SOURCE_SHEETS     - JSON array of objects: {"url": ..., "table": ...} plus optional
        sheet, range, header, all_varchar, database, schema.
    EXPORTS           - JSON array of objects: {"url": ...} plus EITHER "query" (exactly one
        SELECT) OR "database"+"table" (optional schema, default main, and limit);
        optional sheet and create_sheet.
    MAX_RETRIES (5) 
    RETRY_BASE_SECONDS (2)

Exit codes: 0 = all items succeeded; 1 = some failed after retries (everything
was attempted); 2 = bad config/credentials/setup (nothing attempted).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import partial

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
log = logging.getLogger("gsheets_sync")

# docs.google.com spreadsheet URL (incl. the /u/<n>/ account-switcher form) or a
# bare sheet ID (Google IDs are 44 chars).
SHEET_URL_RE = re.compile(
    r"^(https://docs\.google\.com/spreadsheets/(u/\d+/)?d/[A-Za-z0-9_-]{10,}([/?#].*)?"
    r"|[A-Za-z0-9_-]{40,})$"
)

# 10M = the Google Sheets per-spreadsheet CELL limit, i.e. the most rows any sheet
# could hold (1-column case). Bounds the Arrow materialization in run_export;
# multi-column results above 10M/column_count cells still fail at the Sheets API.
GSHEETS_MAX_ROWS = 10_000_000

ALLOWED_IMPORT_KEYS = {"url", "table", "sheet", "range", "header", "all_varchar", "database", "schema"}
ALLOWED_EXPORT_KEYS = {"url", "sheet", "query", "database", "schema", "table", "limit", "create_sheet"}

SYNC_LOG_COLUMNS = (
    "run_id", "direction", "item_key", "source_ref", "target_ref",
    "rows", "attempts", "status", "error", "started_at", "finished_at",
)


class ConfigError(ValueError):
    """Malformed SOURCE_SHEETS / EXPORTS config; aborts before any work."""


# ---- Small SQL / env helpers ----
def utcnow() -> datetime:
    """Naive UTC: a tz-aware datetime written to a naive TIMESTAMP column gets
    shifted by the client session's time zone, interleaving container/laptop runs."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def quote_ident(ident: str) -> str:
    """Double-quote a SQL identifier the way DuckDB expects."""
    return '"' + ident.replace('"', '""') + '"'


def sql_str(value: str) -> str:
    """Single-quoted DuckDB string literal for spots that can't be parameterized
    (read_gsheet args, COPY targets). Refuses non-strings rather than laundering them."""
    if not isinstance(value, str):
        raise TypeError(f"sql_str() requires a string, got {type(value).__name__}")
    return "'" + value.replace("'", "''") + "'"


# ---- Config parsing / validation ----
@dataclass(frozen=True)
class SheetImport:
    """One Google Sheet to import as a MotherDuck table."""
    url: str
    table: str
    sheet: str | None = None
    cell_range: str | None = None
    header: bool | None = None
    all_varchar: bool = False
    database: str | None = None  # resolved destination (TARGET_DATABASE / TARGET_SCHEMA
    schema: str | None = None    # default, or per-item override), set by parse_source_sheets

    @property
    def unique_id(self) -> str:
        # The resolved target table, which parse_source_sheets guarantees unique.
        return f"{self.database}.{self.schema}.{self.table}"


@dataclass(frozen=True)
class SheetExport:
    """One MotherDuck SELECT to push out to a Google Sheet tab."""
    url: str
    sheet: str | None = None
    query: str | None = None
    database: str | None = None
    schema: str | None = None
    table: str | None = None
    limit: int | None = None
    create_sheet: bool = False

    @property
    def unique_id(self) -> str:
        # The destination tab, which parse_exports guarantees unique.
        return f"{self.url} -> sheet={self.sheet or '<first>'}"


def _str_field(item: dict, key: str, idx: int, kind: str, required: bool = False) -> str | None:
    """String field; a present non-string is a config typo that must fail fast (exit 2)."""
    value = item.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        msg = (f"required key {key!r} is missing or not a non-empty string" if required
               else f"{key!r} must be a non-empty string when present")
        raise ConfigError(f"{kind}[{idx}]: {msg}")
    return value.strip()


def _bool_field(item: dict, key: str, idx: int, kind: str, default: bool | None = None) -> bool | None:
    """Optional boolean. Absent -> `default`; a present non-bool (incl. JSON null) is
    a config typo that fails fast, uniformly across fields."""
    if key not in item:
        return default
    if not isinstance(item[key], bool):
        raise ConfigError(f"{kind}[{idx}]: {key!r} must be a boolean")
    return item[key]


def _parse_items(raw: str, kind: str, allowed: set[str], build) -> list:
    """Shared SOURCE_SHEETS/EXPORTS scaffolding: JSON list of objects, no unknown
    keys, valid sheet url; then build(item, url, idx) -> spec. Blank -> empty list,
    so an unused direction can be left blank."""
    raw = raw.strip() or "[]"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{kind} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ConfigError(f"{kind} must be a JSON list, got {type(data).__name__}")
    specs = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            raise ConfigError(f"{kind}[{idx}]: every entry must be a JSON object")
        if unknown := set(item) - allowed:
            raise ConfigError(f"{kind}[{idx}]: unknown key(s) {sorted(unknown)}; allowed: {sorted(allowed)}")
        url = _str_field(item, "url", idx, kind, required=True)
        if not SHEET_URL_RE.match(url):
            raise ConfigError(f"{kind}[{idx}]: {url!r} is not a docs.google.com spreadsheet URL or sheet ID")
        specs.append(build(item, url, idx))
    return specs


def parse_source_sheets(raw: str, default_db: str, default_schema: str) -> list[SheetImport]:
    """SOURCE_SHEETS -> SheetImport specs. Duplicate RESOLVED targets (same
    db.schema.table after defaults apply) are rejected so typos can't clobber a table."""
    def build(item: dict, url: str, idx: int) -> SheetImport:
        return SheetImport(
            url=url,
            table=_str_field(item, "table", idx, "SOURCE_SHEETS", required=True),
            sheet=_str_field(item, "sheet", idx, "SOURCE_SHEETS"),
            cell_range=_str_field(item, "range", idx, "SOURCE_SHEETS"),
            header=_bool_field(item, "header", idx, "SOURCE_SHEETS"),
            all_varchar=_bool_field(item, "all_varchar", idx, "SOURCE_SHEETS", default=False),
            database=_str_field(item, "database", idx, "SOURCE_SHEETS") or default_db,
            schema=_str_field(item, "schema", idx, "SOURCE_SHEETS") or default_schema,
        )

    specs = _parse_items(raw, "SOURCE_SHEETS", ALLOWED_IMPORT_KEYS, build)
    seen: set[tuple[str, str, str]] = set()
    for spec in specs:
        target = (spec.database, spec.schema, spec.table)
        if target in seen:
            raise ConfigError(f"SOURCE_SHEETS: duplicate target table {'.'.join(target)!r}")
        seen.add(target)
    return specs


def validate_select(sql: str, idx: int) -> str:
    """Exactly one SELECT, judged by DuckDB's own parser (prefix checks miss
    WITH-prefixed DML and reject legitimate parenthesized/comment-prefixed queries)."""
    try:
        stmts = duckdb.extract_statements(sql)
    except duckdb.Error as exc:
        raise ConfigError(f"EXPORTS[{idx}]: 'query' does not parse: {exc}") from exc
    if len(stmts) != 1 or stmts[0].type != duckdb.StatementType.SELECT:
        raise ConfigError(f"EXPORTS[{idx}]: 'query' must be exactly one SELECT statement")
    return sql.strip().rstrip(";").strip()


def parse_exports(raw: str) -> list[SheetExport]:
    """EXPORTS -> SheetExport specs: destination url plus exactly one of `query` or
    `database`/`table`. Duplicate url+tab destinations would silently clobber each
    other, so they're rejected."""
    def build(item: dict, url: str, idx: int) -> SheetExport:
        query, table, database, schema = item.get("query"), item.get("table"), None, None
        if (query is None) == (table is None):
            raise ConfigError(f"EXPORTS[{idx}]: provide exactly one of 'query' or 'table'")
        if query is not None:
            query = validate_select(query, idx)
            if {"database", "schema", "limit"} & item.keys():
                raise ConfigError(
                    f"EXPORTS[{idx}]: 'database'/'schema'/'limit' only apply in 'table' mode; "
                    "qualify names and LIMIT inside the query instead"
                )
        else:
            table = _str_field(item, "table", idx, "EXPORTS", required=True)
            database = _str_field(item, "database", idx, "EXPORTS", required=True)
            schema = _str_field(item, "schema", idx, "EXPORTS")
        limit = item.get("limit")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0):
            raise ConfigError(f"EXPORTS[{idx}]: 'limit' must be a positive integer")
        return SheetExport(
            url=url,
            sheet=_str_field(item, "sheet", idx, "EXPORTS"),
            query=query, database=database, schema=schema, table=table, limit=limit,
            create_sheet=_bool_field(item, "create_sheet", idx, "EXPORTS", default=False),
        )

    specs = _parse_items(raw, "EXPORTS", ALLOWED_EXPORT_KEYS, build)
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        dest = (spec.url, spec.sheet or "<first>")
        if dest in seen:
            raise ConfigError(f"EXPORTS: duplicate destination {dest[0]} sheet={dest[1]}")
        seen.add(dest)
    return specs


# ---- SQL builders (pure functions -> unit-testable) ----
def build_read_gsheet_sql(spec: SheetImport) -> str:
    """read_gsheet() call for one import; it can't take bound parameters, so
    everything is escaped into the SQL text."""
    args = [sql_str(spec.url)]
    if spec.sheet:
        args.append(f"sheet={sql_str(spec.sheet)}")
    if spec.cell_range:
        args.append(f"range={sql_str(spec.cell_range)}")
    if spec.header is not None:
        args.append(f"header={'true' if spec.header else 'false'}")
    if spec.all_varchar:
        args.append("all_varchar=true")
    return f"SELECT * FROM read_gsheet({', '.join(args)})"


def build_export_query(spec: SheetExport) -> str:
    """The SELECT run against MotherDuck: the user's validated query verbatim, or
    SELECT * over the configured table with an optional LIMIT."""
    if spec.query is not None:
        return spec.query
    fqtn = ".".join(quote_ident(part) for part in (spec.database, spec.schema or "main", spec.table))
    sql = f"SELECT * FROM {fqtn}"
    if spec.limit:
        sql += f" LIMIT {spec.limit}"
    return sql


def build_copy_sql(spec: SheetExport, source_sql: str) -> str:
    """COPY ... TO (FORMAT gsheet); OVERWRITE_SHEET keeps re-runs idempotent."""
    opts = ["FORMAT gsheet"]
    if spec.sheet:
        opts.append(f"SHEET {sql_str(spec.sheet)}")
    if spec.create_sheet:
        opts.append("CREATE_IF_NOT_EXISTS TRUE")
    opts.append("OVERWRITE_SHEET TRUE")
    return f"COPY (SELECT * FROM {source_sql}) TO {sql_str(spec.url)} ({', '.join(opts)})"


# ---- Connections + auth ----
def validate_service_account_json(secret_name: str) -> tuple[str, str]:
    """Pre-flight credential check, run BEFORE any connection so a missing/garbled
    secret exits 2 with a clean error. Returns (client_email, private_key)."""
    env_var = f"{secret_name}_SERVICE_ACCOUNT_JSON"
    sa_json = os.environ.get(env_var)
    if not sa_json:
        raise RuntimeError(
            f"Env var {env_var} is not set. Provide a `{secret_name}` flights secret "
            "with a SERVICE_ACCOUNT_JSON param holding a Google service-account key."
        )
    try:
        parsed = json.loads(sa_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{env_var} is not valid JSON (check the flight secret)") from exc
    missing = {"client_email", "private_key"} - set(parsed)
    if missing:
        raise RuntimeError(
            f"{env_var} is missing key(s) {sorted(missing)}; "
            "expected a Google service-account key file"
        )
    return parsed["client_email"], parsed["private_key"]


def create_gsheet_secret(con: duckdb.DuckDBPyConnection, email: str, private_key: str) -> None:
    """Register the service-account key with the gsheets extension, in memory only:
    the key is passed inline (EMAIL/SECRET), never written to disk."""
    con.execute(
        f"CREATE OR REPLACE SECRET gsheet_auth "
        f"(TYPE gsheet, PROVIDER key_file, EMAIL {sql_str(email)}, SECRET {sql_str(private_key)})"
    )


# ---- Target setup + audit log ----
def ensure_sync_log(md: duckdb.DuckDBPyConnection, database: str) -> None:
    """Create the audit table and verify its schema up front: a pre-existing table
    with drifted columns would silently kill every audit INSERT, so die loudly
    (exit 2, before any work)."""
    md.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(database)}")
    log_table = f"{quote_ident(database)}.main.gsheets_sync_log"
    md.execute(
        f"CREATE TABLE IF NOT EXISTS {log_table} ("
        "run_id VARCHAR, direction VARCHAR, item_key VARCHAR, source_ref VARCHAR, "
        "target_ref VARCHAR, rows BIGINT, attempts INTEGER, status VARCHAR, "
        "error VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP)"
    )
    actual = tuple(d[0] for d in md.execute(f"SELECT * FROM {log_table} LIMIT 0").description)
    if actual != SYNC_LOG_COLUMNS:
        raise RuntimeError(
            f"{log_table} exists with unexpected columns {list(actual)}; "
            f"expected {list(SYNC_LOG_COLUMNS)}. Rename or migrate it."
        )


def record_result(md: duckdb.DuckDBPyConnection, log_db: str, row: list) -> None:
    """Append one audit row (ordered as SYNC_LOG_COLUMNS). Audit failures are
    logged but never abort the run - observability, not control."""
    try:
        md.execute(
            f"INSERT INTO {quote_ident(log_db)}.main.gsheets_sync_log "
            f"({', '.join(SYNC_LOG_COLUMNS)}) VALUES ({', '.join('?' * len(SYNC_LOG_COLUMNS))})",
            row,
        )
    except Exception as exc:  # noqa: BLE001 - audit must not kill the job
        log.warning("Could not write gsheets_sync_log row for %s: %s", row[2], exc)


# ---- Per-item work (each call is retried as a unit; both are idempotent) ----
def run_import(md: duckdb.DuckDBPyConnection, spec: SheetImport) -> tuple[str, int]:
    """One sheet -> MotherDuck table as a single atomic CTAS. Database/schema
    creation lives here (not main) so a bad per-item override fails THIS item only.
    Returns (fully-qualified target, row count); safe to retry: the swap is atomic."""
    db, schema = quote_ident(spec.database), quote_ident(spec.schema)
    md.execute(f"CREATE DATABASE IF NOT EXISTS {db}")
    md.execute(f"CREATE SCHEMA IF NOT EXISTS {db}.{schema}")
    target = f"{db}.{schema}.{quote_ident(spec.table)}"
    rows = md.execute(
        f"CREATE OR REPLACE TABLE {target} AS {build_read_gsheet_sql(spec)}"
    ).fetchone()[0]
    return spec.unique_id, rows


def run_export(
    md: duckdb.DuckDBPyConnection, local: duckdb.DuckDBPyConnection, spec: SheetExport,
) -> tuple[str, int]:
    """One MotherDuck SELECT -> sheet tab via an in-memory Arrow bridge.
    to_arrow_table(), not arrow(): the latter is a single-use RecordBatchReader in
    duckdb 1.5+. Returns (destination, rows); safe to retry: the tab is overwritten."""
    # .sql().limit() composes a LIMIT onto the user's query, bounding the Arrow
    # materialization at the Sheets cell limit so a runaway query can't eat memory.
    arrow_tbl = md.sql(build_export_query(spec)).limit(GSHEETS_MAX_ROWS).to_arrow_table()
    if arrow_tbl.num_rows == GSHEETS_MAX_ROWS:
        log.warning("Export %s hit the %d-row cap (Google Sheets cell limit); "
                    "result may be truncated", spec.unique_id, GSHEETS_MAX_ROWS)
    local.register("_gsheets_out", arrow_tbl)
    try:
        local.execute(build_copy_sql(spec, "_gsheets_out"))
    finally:
        local.unregister("_gsheets_out")
    return f"{spec.url} (sheet={spec.sheet or '<first>'})", arrow_tbl.num_rows


def setup_connections(secret_name: str, target_db: str) -> tuple:
    """Validate credentials, open both connections, register gsheet auth, and ensure
    the audit table. Raises on any failure so main() can exit 2 (nothing attempted).
    Returns (md, local, client_email)."""
    client_email, private_key = validate_service_account_json(secret_name)
    md = duckdb.connect("md:")
    local = duckdb.connect()  # sheet writes only: COPY (FORMAT gsheet) is
    # unresolvable on any connection with the motherduck extension loaded.
    for con in (md, local):
        con.execute("INSTALL gsheets FROM community")
        con.execute("LOAD gsheets")
        create_gsheet_secret(con, client_email, private_key)
    ensure_sync_log(md, target_db)
    return md, local, client_email


# ---- main ----
def main() -> None:
    """Parse config and validate credentials,
    set up connections and the audit table, 
    then run each import and export item with retries and isolation."""
    RUN_ID = str(uuid.uuid4())
    # `or default` so a blank env var falls back to the default, not an empty string.
    TARGET_DB = os.environ.get("TARGET_DATABASE") or "google_sheets"
    TARGET_SCHEMA = os.environ.get("TARGET_SCHEMA") or "main"
    SECRET_NAME = os.environ.get("GSHEETS_SECRET_NAME") or "gsheets"

    # ValueError (bad MAX_RETRIES/RETRY_BASE_SECONDS) is config-class -> exit 2, not a
    # runtime traceback; ConfigError subclasses ValueError, so one except covers both.
    try:
        MAX_RETRIES = int(os.environ.get("MAX_RETRIES") or "5")
        RETRY_BASE_SECONDS = float(os.environ.get("RETRY_BASE_SECONDS") or "2")
        imports = parse_source_sheets(os.environ.get("SOURCE_SHEETS", "[]"), TARGET_DB, TARGET_SCHEMA)
        exports = parse_exports(os.environ.get("EXPORTS", "[]"))
    except ValueError as exc:
        log.error("Configuration error: %s", exc)
        sys.exit(2)

    log.info("Run %s: %d import(s), %d export(s) -> target %s.%s",
             RUN_ID, len(imports), len(exports), TARGET_DB, TARGET_SCHEMA)

    if not imports and not exports:
        log.warning("SOURCE_SHEETS and EXPORTS are both empty - nothing to do.")
        return

    try:
        md, local, client_email = setup_connections(SECRET_NAME, TARGET_DB)
    except Exception as exc:  # noqa: BLE001 - setup failure = nothing attempted = exit 2
        log.error("Setup failed before any item was attempted: %s", exc)
        sys.exit(2)
    log.info("Google Sheets auth ready as %s", client_email)

    started_all = utcnow()
    failed: list[str] = []
    succeeded = 0
    rows_total = 0

    work = [("import", s, partial(run_import, md, s)) for s in imports] \
        + [("export", s, partial(run_export, md, local, s)) for s in exports]
    for direction, spec, job in work:
        # source_ref computed up front so success and failure audit rows match.
        source_ref = spec.url if direction == "import" else build_export_query(spec)
        started = utcnow()
        # Jittered exponential backoff capped at 60s, retrying on any error.
        retryer = Retrying(
            stop=stop_after_attempt(MAX_RETRIES),
            wait=wait_exponential(multiplier=RETRY_BASE_SECONDS, max=60) + wait_random(0, 1),
            reraise=True,
        )
        try:
            target_ref, rows = retryer(job)
            status, error = "OK", None
            succeeded += 1
            rows_total += rows
        except Exception as exc:  # noqa: BLE001 - per-item isolation is intentional
            target_ref, rows, status = "", None, "FAILED"
            error = f"{type(exc).__name__}: {exc}"
            failed.append(f"{direction}:{spec.unique_id}")
        attempts = retryer.statistics.get("attempt_number", 1)
        record_result(md, TARGET_DB, [RUN_ID, direction, spec.unique_id, source_ref, target_ref,
                                      rows, attempts, status, error and error[:4000],
                                      started, utcnow()])
        if status == "OK":
            log.info("OK   %-6s %-60s %10d rows (attempts=%d)", direction, spec.unique_id, rows, attempts)
        else:
            log.error("FAIL %-6s %-60s (attempts=%d) %s", direction, spec.unique_id, attempts, error)

    total_seconds = (utcnow() - started_all).total_seconds()
    log.info("Summary: %d succeeded, %d failed, %d rows in %.1fs (run %s)",
             succeeded, len(failed), rows_total, total_seconds, RUN_ID)

    if failed:
        log.error("Failed items: %s", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
