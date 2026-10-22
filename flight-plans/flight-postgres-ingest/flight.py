"""
Postgres -> MotherDuck batch ELT flight

Mirrors PostgreSQL tables into a MotherDuck database using the DuckDB postgres
extension. By default each table is moved by a single streaming SQL statement:

    CREATE OR REPLACE TABLE <target>."<schema>"."<table>" AS SELECT * FROM pg."<schema>"."<table>";

That statement is the entire load. It swaps the table in one step, a re-run fully
replaces it, and DuckDB pipelines the scan into the write with bounded memory.

Tables listed in INCREMENTAL_TABLES instead pull only rows at or after the target's
high-watermark and MERGE them on a key, so a large append-mostly table isn't re-read
every run.

Per-table logging lands in  `<target>.main.flight_tracker`.

Inputs
------
Note that inputs are case sensitive (use uppercase).

Secret (TYPE flights) -> Postgres connection, EITHER as one URL:
    `DATABASE_URL` = postgres://user:password@host:5432/dbname?sslmode=require
    (the form Render, Heroku, Neon, Supabase, and Azure hand out; `sslmode`,
    `channel_binding`, `options`, `connect_timeout`, and `application_name` in the
    query string are passed to libpq)
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
    MOTHERDUCK_HOST    - staging host; exported as `motherduck_host` before connect.
    TARGET_DATABASE    - MotherDuck database to write into (default: postgres_ingest).
    TARGET_SCHEMA      - rename the target schema (only with a single source schema).
    INCLUDED_SCHEMAS / EXCLUDED_SCHEMAS  - comma-separated schema names.
    INCLUDED_TABLES  / EXCLUDED_TABLES   - comma-separated `schema.table` or bare `table`.
    INCREMENTAL_TABLES - comma-separated `schema.table:watermark_column:key[|key...]`.
    CHECK_ROW_COUNTS   - `true` to recount each table in Postgres after the copy.
    SECRET_NAME        - name of the flights secret, to prefer its namespaced params.
    URL_KEY            - secret param holding the URL (default: DATABASE_URL).
    MAX_RETRIES (5)
    RETRY_BASE_SECONDS (2)
"""

from __future__ import annotations

import logging
import os
import socket
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

# PostgreSQL system schemas, plus schemas managed hosts install extensions into
# (Heroku's `heroku_ext`), that are never mirrored.
SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "heroku_ext"}

# Schemas Supabase manages for its own services. `auth.users` holds your users'
# emails and password hashes, so these are skipped unless INCLUDED_SCHEMAS names them.
SUPABASE_SCHEMAS = {
    "auth", "storage", "realtime", "vault", "pgsodium", "pgsodium_masks",
    "supabase_functions", "supabase_migrations", "extensions", "graphql",
    "graphql_public", "net", "pgbouncer", "cron",
}

# Postgres connection params from the flight secret. Params named as libpq vars
# (PGHOST, ...) arrive bare and are libpq-ready; a legacy secret named `pg` with
# HOST/PORT/... params arrives namespaced as pg_HOST, pg_PORT, ...
PG_PARAMS = (
    ("PGHOST", "pg_HOST", None, True),
    ("PGPORT", "pg_PORT", "5432", False),
    ("PGDATABASE", "pg_DATABASE", None, True),
    ("PGUSER", "pg_USER", None, True),
    ("PGPASSWORD", "pg_PASSWORD", None, True),
    ("PGSSLMODE", "pg_SSLMODE", "require", False),
)

# Connection-URL query params that have a libpq env var. Anything else in the query
# string is ignored, and the run logs which keys it dropped.
URL_QUERY_ENV = {
    "sslmode": "PGSSLMODE",
    "channel_binding": "PGCHANNELBINDING",
    "options": "PGOPTIONS",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "application_name": "PGAPPNAME",
}

# Local DuckDB catalog name the source Postgres database is ATTACHed as. Referenced by
# attach_postgres(), discover_base_tables(), and the loaders -- one source of truth.
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


def parse_incremental(raw: str) -> dict[str, tuple[str, list[str]]]:
    """Parse INCREMENTAL_TABLES, 'schema.table:watermark:key1|key2,...', into
    {'schema.table': ('watermark', ['key1', 'key2'])}. A bare table means `public`."""
    spec: dict[str, tuple[str, list[str]]] = {}
    for entry in (e.strip() for e in raw.split(",") if e.strip()):
        parts = [p.strip() for p in entry.split(":")]
        keys = [k.strip() for k in parts[2].split("|") if k.strip()] if len(parts) == 3 else []
        if len(parts) != 3 or not parts[0] or not parts[1] or not keys:
            raise RuntimeError(
                f"Bad INCREMENTAL_TABLES entry {entry!r}; expected "
                "schema.table:watermark_column:key_column[|key_column...]"
            )
        fqtn = parts[0] if "." in parts[0] else f"public.{parts[0]}"
        spec[fqtn] = (parts[1], keys)
    return spec


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


def apply_url(url: str) -> None:
    """Export a postgres:// URL as libpq env vars. Errors never include the URL, because
    a malformed URL usually means the password has an unencoded character in it."""
    if "[YOUR-PASSWORD]" in url.upper():
        raise RuntimeError(
            "The connection URL still contains the [YOUR-PASSWORD] placeholder (Supabase "
            "shows the URL this way). Replace it, brackets included, with the password."
        )
    u = urlparse(url)
    if u.scheme not in ("postgres", "postgresql"):
        raise RuntimeError("The connection URL in the secret is not a postgres:// URL (value not printed)")
    try:
        port = u.port
    except ValueError:
        raise RuntimeError(
            "Could not parse the connection URL (value not printed). Percent-encode special "
            "characters in the password, for example '/' as %2F and '@' as %40."
        ) from None
    os.environ.update({
        "PGHOST": u.hostname or "",
        "PGPORT": str(port or 5432),
        "PGDATABASE": unquote(u.path.lstrip("/")),
        "PGUSER": unquote(u.username or ""),
        "PGPASSWORD": unquote(u.password or ""),
    })
    query = parse_qs(u.query)
    for key, values in query.items():
        if key in URL_QUERY_ENV:
            os.environ[URL_QUERY_ENV[key]] = values[-1]
    ignored = sorted(set(query) - set(URL_QUERY_ENV))
    if ignored:
        log.warning("Ignoring connection URL params with no libpq env var: %s", ignored)


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
        apply_url(url)

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
    check_host(os.environ["PGHOST"].lower(), os.environ["PGPORT"], os.environ["PGUSER"])


def has_ipv4(host: str, port: str) -> bool:
    try:
        return bool(socket.getaddrinfo(host, int(port), socket.AF_INET))
    except socket.gaierror:
        return False


def check_host(host: str, port: str, user: str) -> None:
    """Stop on connection details that are known not to work from a Flight, and warn on
    ones that work badly. Each rule matches one managed host's documented URL forms."""
    # Render: the Internal Database URL host is `dpg-...` with no domain.
    if host.startswith("dpg-") and "." not in host:
        raise RuntimeError(
            "PGHOST has no domain; this looks like Render's Internal Database URL, which only "
            "works inside Render. Store the External Database URL instead."
        )
    # Neon: pooled endpoints carry `-pooler` in the endpoint name.
    if "-pooler" in host:
        log.warning("PGHOST is a pooled (PgBouncer) endpoint; a direct endpoint is more "
                    "reliable for long bulk reads.")
    # Supabase: the shared pooler serves sessions on 5432 and transactions on 6543.
    if host.endswith(".pooler.supabase.com"):
        if "." not in user:
            raise RuntimeError(
                "The Supabase pooler needs the user name '<role>.<project_ref>', for example "
                "'postgres.abcdefghijklmnop'. Copy the Session pooler string from Supabase > Connect."
            )
        if port == "6543":
            raise RuntimeError(
                "Port 6543 is Supabase's transaction pooler, which doesn't hold a session for "
                "the length of a bulk read. Use the session pooler: same host, port 5432."
            )
    # Supabase: the direct host is IPv6-only unless the project bought the IPv4 add-on,
    # and Flights connect out over IPv4.
    if host.startswith("db.") and host.endswith(".supabase.co") and not has_ipv4(host, port):
        raise RuntimeError(
            f"{host} has no IPv4 address, and Flights connect over IPv4. Store the Session "
            "pooler string from Supabase > Connect (host *.pooler.supabase.com, port 5432)."
        )
    # Azure Database for PostgreSQL flexible server.
    if host.endswith(".postgres.database.azure.com"):
        if host.endswith(".private.postgres.database.azure.com"):
            raise RuntimeError(
                "This is an Azure private-access (VNet) host, which isn't reachable from a "
                "Flight. Use a server with public access and a firewall rule for the Flight egress IPs."
            )
        if port == "6432":
            raise RuntimeError(
                "Port 6432 is Azure's built-in PgBouncer. Connect to the server directly on "
                "port 5432 instead."
            )
        server = host.split(".")[0]
        if user.lower().endswith("@" + server):
            raise RuntimeError(
                "PGUSER uses the retired Single Server 'user@servername' form. Flexible server "
                "expects the plain role name; drop '@servername' from the secret."
            )


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
    """List the candidate source tables to iterate on. A partitioned table is listed once,
    as its parent; its partitions are skipped, since reading the parent reads them all."""
    rows = con.execute(
        f"SELECT table_schema, table_name FROM postgres_query('{PG_ALIAS}', "
        "'SELECT n.nspname AS table_schema, c.relname AS table_name "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN (''r'', ''p'') AND NOT c.relispartition') "
        "ORDER BY table_schema, table_name"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def table_exists(con: duckdb.DuckDBPyConnection, target_db: str, schema: str, table: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM duckdb_tables() "
        "WHERE database_name = ? AND schema_name = ? AND table_name = ?",
        [target_db, schema, table],
    ).fetchone()[0] > 0


def load_table(
    con: duckdb.DuckDBPyConnection, target_db: str, schema: str, table: str, tgt_schema: str
) -> int:
    """Perform the entire data movement for one table as a single atomic, idempotent,
    streaming CTAS. returns the row count the CTAS reports as inserted."""
    tgt_table = f"{quote_ident(target_db)}.{quote_ident(tgt_schema)}.{quote_ident(table)}"
    src_table = f"{PG_ALIAS}.{quote_ident(schema)}.{quote_ident(table)}"
    return con.execute(f"CREATE OR REPLACE TABLE {tgt_table} AS SELECT * FROM {src_table}").fetchone()[0]


def load_incremental(
    con: duckdb.DuckDBPyConnection, target_db: str, schema: str, table: str, tgt_schema: str,
    watermark_col: str, keys: list[str],
) -> int:
    """Copy rows at or after the target's high-watermark and MERGE them on `keys`.

    The first run, or a run after the target was dropped, is a full CTAS. `>=` rather
    than `>` re-reads the rows that share the last watermark value, so a row committed
    with that same value after the previous run isn't skipped; the MERGE makes the
    re-read harmless. Returns the number of rows read from Postgres."""
    if not table_exists(con, target_db, tgt_schema, table):
        return load_table(con, target_db, schema, table, tgt_schema)

    tgt_table = f"{quote_ident(target_db)}.{quote_ident(tgt_schema)}.{quote_ident(table)}"
    src_table = f"{PG_ALIAS}.{quote_ident(schema)}.{quote_ident(table)}"
    wm = quote_ident(watermark_col)
    # The watermark travels as text plus its DuckDB type, so a TIMESTAMPTZ doesn't need
    # a Python timezone library and the cast back happens in the same session time zone.
    watermark, wm_type = con.execute(
        f"SELECT CAST(max({wm}) AS VARCHAR), typeof(max({wm})) FROM {tgt_table}"
    ).fetchone()

    # Stage the batch locally first, so Postgres is read once and the MERGE is one
    # atomic statement. A bound watermark is pushed down to Postgres as a WHERE clause.
    con.execute("DROP TABLE IF EXISTS temp.main.pg2md_batch")
    if watermark is None:
        con.execute(f"CREATE TEMP TABLE pg2md_batch AS SELECT * FROM {src_table}")
    else:
        con.execute(
            f"CREATE TEMP TABLE pg2md_batch AS SELECT * FROM {src_table} "
            f"WHERE {wm} >= CAST(? AS {wm_type})",
            [watermark],
        )
    rows = con.execute("SELECT count(*) FROM temp.main.pg2md_batch").fetchone()[0]
    on = " AND ".join(f"t.{quote_ident(k)} = b.{quote_ident(k)}" for k in keys)
    con.execute(
        f"MERGE INTO {tgt_table} AS t USING temp.main.pg2md_batch AS b ON ({on}) "
        "WHEN MATCHED THEN UPDATE WHEN NOT MATCHED THEN INSERT"
    )
    con.execute("DROP TABLE temp.main.pg2md_batch")
    log.info("     %s.%s: %d row(s) at or after %s = %s", schema, table, rows, watermark_col, watermark)
    return rows


def check_incremental_columns(
    con: duckdb.DuckDBPyConnection, schema: str, table: str, watermark_col: str, keys: list[str]
) -> None:
    """Fail the table before any copy when INCREMENTAL_TABLES names a column it lacks."""
    cols = {
        r[0] for r in con.execute(
            "SELECT column_name FROM duckdb_columns() "
            "WHERE database_name = ? AND schema_name = ? AND table_name = ?",
            [PG_ALIAS, schema, table],
        ).fetchall()
    }
    missing = [c for c in [watermark_col, *keys] if c not in cols]
    if missing:
        raise RuntimeError(f"INCREMENTAL_TABLES columns not in {schema}.{table}: {missing}")


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
    """Orchestrate the ELT: connect, attach, discover, then load each table sequentially
    with per-table retries/isolation and record results."""
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
    INCREMENTAL = parse_incremental(env("INCREMENTAL_TABLES"))
    CHECK_ROW_COUNTS = env("CHECK_ROW_COUNTS").lower() in ("1", "true", "yes")

    log.info("Run %s -> target %r", RUN_ID, TARGET_DB)

    con = connect_motherduck()
    attach_postgres(con)

    if os.environ["PGHOST"].lower().endswith((".supabase.com", ".supabase.co")):
        EXCLUDED_SCHEMAS = EXCLUDED_SCHEMAS | (SUPABASE_SCHEMAS - INCLUDED_SCHEMAS)

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
    unused = sorted(set(INCREMENTAL) - {f"{s}.{t}" for s, t in selected})
    if unused:
        raise RuntimeError(f"INCREMENTAL_TABLES names tables that aren't selected: {unused}")

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
            if fqtn in INCREMENTAL:
                watermark_col, keys = INCREMENTAL[fqtn]
                check_incremental_columns(con, schema, table, watermark_col, keys)
                rows = retryer(load_incremental, con, TARGET_DB, schema, table, tgt_schema,
                               watermark_col, keys)
            else:
                rows = retryer(load_table, con, TARGET_DB, schema, table, tgt_schema)
            attempts = retryer.statistics.get("attempt_number", 1)
            finished = datetime.now(timezone.utc)
            record_success(con, TARGET_DB, RUN_ID, schema, table, tgt_schema, rows,
                           attempts, started, finished, datetime.now(timezone.utc))
            succeeded += 1
            rows_total += rows
            log.info("OK   %-50s %12d rows (attempts=%d)", fqtn, rows, attempts)
        except Exception as exc:  # noqa: BLE001 - per-table isolation is intentional
            attempts = retryer.statistics.get("attempt_number", 1)
            failed.append(fqtn)
            log.error("FAIL %-50s (attempts=%d) %s: %s", fqtn, attempts, type(exc).__name__, exc)
            continue

        # A live source can change mid-copy. Recounting re-reads the table in Postgres,
        # so it's opt-in, and a mismatch warns rather than fails.
        if CHECK_ROW_COUNTS and fqtn not in INCREMENTAL:
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
