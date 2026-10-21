"""MotherDuck Flight: Supabase Postgres -> MotherDuck full-refresh copy.

Connect through the Supavisor SESSION pooler
    Supabase's direct host (db.<project_ref>.supabase.co) resolves to IPv6 only,
    unless the project has the paid IPv4 add-on. MotherDuck documents IPv4 egress
    addresses for Flights, not IPv6, so use the session pooler connection string
    (Supabase dashboard -> Connect -> Session pooler):
        postgresql://postgres.<project_ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
    Port 6543 is the transaction pooler; the Flight switches it to 5432.

What each run does
    1. Reads the connection from a Flights secret and runs Supabase checks
       (pooler user format, transaction-pooler port, IPv6-only direct host).
    2. Attaches Postgres READ_ONLY over TLS (sslmode=require unless the secret says
       otherwise). Credentials reach libpq as PG* environment variables, never SQL.
    3. Resolves the tables: every base table in SOURCE_SCHEMA (or the explicit
       TABLES list), minus EXCLUDE_TABLES. Views are never copied. Unknown names
       stop the run before anything is copied.
    4. Full-refreshes each table with one CREATE OR REPLACE TABLE ... AS SELECT,
       adding an _ingested_at column, retrying transient errors with backoff.
    5. Writes one row per table to <TARGET_DB>._meta.load_log.
    6. Exits non-zero if any table failed; the other tables still copy.

Flights secret (TYPE flights; name set by SECRET_NAME, default "supabase"), holding EITHER
    DATABASE_URL     the session pooler URI (see above)
OR
    PGHOST, PGDATABASE, PGUSER, PGPASSWORD   (PGPORT optional, default 5432;
                                              PGSSLMODE optional, default require)
    Each key is injected as <secret>_<KEY> and usually also as the bare <KEY>;
    the namespaced form wins.

Config (non-secret; empty value = default)
    SECRET_NAME      Flights secret name                          default: supabase
    URL_KEY          key in the secret holding the URL            default: DATABASE_URL
    SOURCE_SCHEMA    comma-separated schemas, or * for every
                     non-Supabase-managed schema                  default: public
    TABLES           "orders,billing.invoices"; bare names are
                     looked up in SOURCE_SCHEMA                   default: all base tables
    EXCLUDE_TABLES   "audit_log,public.tmp_import"                default: none
    TARGET_DB        MotherDuck database to write into            default: supabase_raw
    TARGET_SCHEMA    schema inside TARGET_DB                      default: main for one source
                                                                  schema, else the source name
    MAX_RETRIES      attempts per table on transient errors       default: 3
    MOTHERDUCK_HOST  MotherDuck API host override (e.g. staging)  default: unset

requirements.txt
    duckdb==1.5.6
"""
import os
import random
import re
import socket
import sys
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import duckdb

SRC = "supabase"  # alias of the attached Postgres database
META_SCHEMA = "_meta"
POOLER_SUFFIX = ".pooler.supabase.com"
DIRECT_HOST = re.compile(r"^db\.([a-z0-9]+)\.supabase\.co$")
PG_SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
# Schemas Supabase creates and manages. Skipped by SOURCE_SCHEMA=*; copied only when named.
SUPABASE_SCHEMAS = {
    "auth", "storage", "realtime", "_realtime", "extensions", "graphql", "graphql_public",
    "vault", "pgsodium", "pgsodium_masks", "supabase_functions", "supabase_migrations",
    "net", "cron", "pgbouncer", "_analytics", "_supavisor", "pgtle", "tiger", "topology",
}
# Errors that retrying cannot fix: fail the attach or the table immediately.
PERMANENT_ERRORS = (
    "password authentication failed", "tenant or user not found", "no pg_hba.conf entry",
    "does not exist", "permission denied", "circuit breaker", "authentication",
)


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------
def log(msg: str):
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z  {msg}", flush=True)


def cfg(name: str, default: str = "") -> str:
    """Config value; unset or empty falls back to the default."""
    return (os.environ.get(name) or default).strip()


def qi(name: str) -> str:
    """Quote an identifier (same rules in DuckDB and Postgres)."""
    return '"' + name.replace('"', '""') + '"'


def ql(value) -> str:
    """Quote a string literal (same rules in DuckDB and Postgres)."""
    return "'" + str(value).replace("'", "''") + "'"


def parse_list(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def is_permanent(exc: Exception) -> bool:
    if isinstance(exc, (duckdb.CatalogException, duckdb.BinderException, duckdb.ParserException,
                        duckdb.ConversionException, duckdb.NotImplementedException)):
        return True
    return any(s in str(exc).lower() for s in PERMANENT_ERRORS)


def redact(text: str) -> str:
    """Defence in depth: libpq doesn't echo passwords, but never let one reach a log."""
    pw = os.environ.get("PGPASSWORD")
    return text.replace(pw, "***") if pw and len(pw) >= 4 else text


# --------------------------------------------------------------------------------------
# Credentials and Supabase checks
# --------------------------------------------------------------------------------------
def get_pg_credentials(secret: str, url_key: str) -> dict:
    """Read the connection from the Flights secret. Error messages list key names only."""

    def get(key):
        return os.environ.get(f"{secret}_{key}") or os.environ.get(key)

    url = (get(url_key) or "").strip()
    if url:
        # Supabase shows the URI with a [YOUR-PASSWORD] placeholder; a common slip is to
        # save it unchanged, or to keep the square brackets around the real password.
        if "[YOUR-PASSWORD]" in url.upper():
            raise RuntimeError(
                f"Secret '{secret}' key '{url_key}' still contains Supabase's [YOUR-PASSWORD] "
                "placeholder. Replace it, square brackets included, with the database password."
            )
        try:
            u = urlparse(url)
            port = str(u.port or 5432)
        except ValueError:
            raise RuntimeError(
                f"Secret '{secret}' key '{url_key}' is not a valid connection URL (value not "
                "printed). Remove any square brackets around the password, and URL-encode "
                "special characters in it (for example @ as %40), or store PGHOST/PGPORT/"
                "PGDATABASE/PGUSER/PGPASSWORD as separate keys instead."
            ) from None
        if u.scheme not in ("postgres", "postgresql"):
            raise RuntimeError(
                f"Secret '{secret}' key '{url_key}' is not a postgresql:// URL (value not printed)"
            )
        if (u.password or "").startswith("[") and (u.password or "").endswith("]"):
            raise RuntimeError(
                f"Secret '{secret}' key '{url_key}': the password is wrapped in square brackets. "
                "Supabase's [YOUR-PASSWORD] brackets are part of the placeholder; remove them."
            )
        creds = {
            "PGHOST": u.hostname,
            "PGPORT": port,
            "PGUSER": unquote(u.username or ""),
            "PGPASSWORD": unquote(u.password or ""),
            "PGDATABASE": unquote(u.path.lstrip("/")),
        }
        sslmode = parse_qs(u.query).get("sslmode", [None])[0]
        source = f"key '{url_key}'"
    else:
        creds = {k: get(k) for k in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE")}
        creds["PGPORT"] = creds["PGPORT"] or "5432"
        sslmode = get("PGSSLMODE")
        source = "keys PGHOST/PGDATABASE/PGUSER/PGPASSWORD"

    missing = [k for k, v in creds.items() if not v]
    if missing:
        present = sorted(k for k in os.environ if k.startswith(f"{secret}_"))
        raise RuntimeError(
            f"Missing Postgres connection fields {missing} (read from {source} of secret "
            f"'{secret}'). Secret keys visible to this run: {present}. Set SECRET_NAME / "
            f"URL_KEY to match, or store {url_key} or PGHOST/PGDATABASE/PGUSER/PGPASSWORD."
        )
    creds["PGSSLMODE"] = sslmode or "require"
    return creds


def ipv6_only_and_unreachable(host: str, port: int) -> bool:
    """True when the host has no IPv4 address and this machine can't reach it over IPv6."""
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except OSError:
        return False  # DNS trouble: let the attach (and its retries) report it
    if not infos or any(i[0] == socket.AF_INET for i in infos):
        return False
    for family, kind, proto, _, addr in infos:
        try:
            with socket.socket(family, kind, proto) as s:
                s.settimeout(5)
                s.connect(addr)
                return False
        except OSError:
            continue
    return True


def check_supabase(creds: dict) -> str:
    """Fix or reject the common Supabase connection mistakes. Returns the host kind.
    Never logs credentials; host names and ports only."""
    host, user = creds["PGHOST"].lower(), creds["PGUSER"]
    direct = DIRECT_HOST.match(host)

    if host.endswith(POOLER_SUFFIX):
        kind = "supabase-pooler"
        if "." not in user:
            raise RuntimeError(
                "The Supabase pooler needs the user name '<role>.<project_ref>' (for example "
                "'postgres.abcdefghijklmnop'), but the secret's user has no '.<project_ref>' "
                "suffix. Copy the Session pooler connection string from Supabase -> Connect."
            )
        if creds["PGPORT"] == "6543":
            log("Warning: port 6543 is Supabase's transaction pooler, which suits short "
                "queries, not long bulk reads; using the session pooler port 5432 instead")
            creds["PGPORT"] = "5432"
    elif direct:
        kind = "supabase-direct"
        log("Note: this is Supabase's direct host. It is IPv6-only unless the project has the "
            "IPv4 add-on; Flights connect over IPv4, so prefer the Session pooler string.")
        if creds["PGPORT"] == "6543":
            log("Warning: port 6543 on the direct host is the dedicated transaction pooler; "
                "using port 5432 instead")
            creds["PGPORT"] = "5432"
        if "." in user:
            log("Warning: the direct host expects the plain role name (usually 'postgres'); "
                "the '<role>.<project_ref>' form is only for the pooler")
        if ipv6_only_and_unreachable(host, int(creds["PGPORT"])):
            raise RuntimeError(
                f"{host} resolves to IPv6 only and this runtime has no IPv6 route. Store the "
                "Session pooler connection string instead (Supabase -> Connect -> Session "
                "pooler: aws-0-<region>.pooler.supabase.com:5432, user postgres.<project_ref>), "
                "or enable the project's IPv4 add-on."
            )
    else:
        kind = "other"
        log("Warning: host is not a Supabase host (*.pooler.supabase.com or "
            "db.<ref>.supabase.co); Supabase-specific checks skipped")
        if creds["PGPORT"] == "6543":
            log("Warning: port 6543 is usually a transaction pooler; a session or direct "
                "connection is more reliable for bulk reads")

    if creds["PGSSLMODE"] in ("disable", "allow", "prefer"):
        log(f"Warning: sslmode={creds['PGSSLMODE']}; use require (Supabase recommends it)")
    return kind


# --------------------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------------------
def configure_libpq(creds: dict):
    """DuckDB's postgres extension uses libpq, which reads the standard PG* variables.
    The password never appears in SQL text, so it can't be echoed by a SQL error."""
    os.environ.update(creds)
    os.environ["PGCONNECT_TIMEOUT"] = "20"
    os.environ["PGAPPNAME"] = "motherduck-flight"  # lets the DBA spot these sessions


def connect_motherduck() -> duckdb.DuckDBPyConnection:
    host = cfg("MOTHERDUCK_HOST")
    if host:
        os.environ["motherduck_host"] = host
        log(f"Using MotherDuck host {host}")
    con = duckdb.connect("md:")
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    try:
        # Keep the number of pooler connections small.
        con.execute("SET pg_connection_limit = 4")
    except duckdb.Error as exc:
        log(f"(pg_connection_limit not set: {exc})")
    return con


def attach_source(con: duckdb.DuckDBPyConnection, kind: str, attempts: int = 4):
    """Attach the source read-only, retrying transient network errors (not auth errors)."""
    for attempt in range(1, attempts + 1):
        try:
            con.execute(f"ATTACH '' AS {SRC} (TYPE postgres, READ_ONLY)")
            version = con.execute(
                f"SELECT * FROM postgres_query('{SRC}', 'SELECT version()')"
            ).fetchone()[0]
            log(f"Connected to source (sslmode={os.environ['PGSSLMODE']}): {version.split(',')[0]}")
            return
        except duckdb.Error as exc:
            msg = redact(str(exc))
            if attempt == attempts or is_permanent(exc):
                if "tenant or user not found" in msg.lower():
                    log("Hint: the pooler user must be '<role>.<project_ref>' and the host "
                        "must be the one shown in Supabase -> Connect")
                elif kind == "supabase-direct":
                    log("Hint: the direct host is IPv6-only without the IPv4 add-on; use the "
                        "Session pooler connection string")
                raise RuntimeError(f"Could not connect to the source: {msg}") from None
            wait = min(60, 5 * 2**attempt) + random.random()
            log(f"Source connection failed ({attempt}/{attempts}): {msg}. Retrying in {wait:.0f}s")
            try:
                con.execute(f"DETACH DATABASE IF EXISTS {SRC}")
            except duckdb.Error:
                pass
            time.sleep(wait)


# --------------------------------------------------------------------------------------
# Table discovery
# --------------------------------------------------------------------------------------
def pg_query(con, sql: str) -> list:
    return con.execute(f"SELECT * FROM postgres_query('{SRC}', {ql(sql)})").fetchall()


def list_base_tables(con) -> list[tuple[str, str]]:
    """Regular and partitioned tables only (relkind r/p). Views, materialized views and
    foreign tables are skipped, and so are individual partitions (the parent returns
    their rows). duckdb_tables() on an attached Postgres lists views too, so it isn't used."""
    excluded = ", ".join(ql(s) for s in sorted(PG_SYSTEM_SCHEMAS))
    return [
        tuple(r) for r in pg_query(
            con,
            "SELECT n.nspname, c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition "
            f"AND n.nspname NOT IN ({excluded}) AND n.nspname NOT LIKE 'pg\\_%' "
            "ORDER BY 1, 2",
        )
    ]


def resolve_tables(con, schemas_raw: str, tables_raw: str, exclude_raw: str) -> list[tuple[str, str]]:
    found = list_base_tables(con)
    found_set = set(found)
    all_schemas = {s for s, _ in found}

    if schemas_raw.strip() == "*":
        schemas = sorted(all_schemas - SUPABASE_SCHEMAS)
    else:
        schemas = parse_list(schemas_raw)
        unknown = [s for s in schemas if s not in all_schemas]
        if unknown:
            raise RuntimeError(f"SOURCE_SCHEMA has no base tables (or doesn't exist): {unknown}")
    managed = sorted(set(schemas) & SUPABASE_SCHEMAS)
    if managed:
        log(f"Warning: copying Supabase-managed schema(s) {managed}; they can hold user "
            "emails, password hashes or secrets")

    explicit = parse_list(tables_raw)
    if explicit:
        tables, unknown = [], []
        for name in explicit:
            schema, _, table = name.rpartition(".")
            matches = [(schema, table)] if schema else [(s, name) for s in schemas]
            matches = [m for m in matches if m in found_set]
            if not matches:
                unknown.append(name)
            tables += [m for m in matches if m not in tables]
        if unknown:
            raise RuntimeError(
                f"TABLES not found as base tables in {schemas}: {unknown} "
                "(views are not copied; use schema.table for other schemas)"
            )
    else:
        tables = [(s, t) for s, t in found if s in schemas]

    exclude = {e.lower() for e in parse_list(exclude_raw)}
    return [(s, t) for s, t in tables if t.lower() not in exclude and f"{s}.{t}".lower() not in exclude]


def plan_targets(tables, target_schema: str) -> list[tuple[str, str, str]]:
    """(source_schema, table, target_schema). Fails before copying on name collisions,
    including names that differ only by case (DuckDB identifiers are case-insensitive)."""
    single = len({s for s, _ in tables}) == 1
    plan = [(s, t, target_schema or ("main" if single else s)) for s, t in tables]
    if any(ts.lower() == META_SCHEMA for _, _, ts in plan):
        raise RuntimeError(f"TARGET_SCHEMA '{META_SCHEMA}' is reserved for the load log")
    seen, clashes = {}, []
    for s, t, ts in plan:
        key = (ts.lower(), t.lower())
        if key in seen:
            clashes.append(f"{seen[key]} and {s}.{t} -> {ts}.{t}")
        seen[key] = f"{s}.{t}"
    if clashes:
        raise RuntimeError(
            f"Several source tables would land on the same target table: {clashes}. "
            "Leave TARGET_SCHEMA empty to keep the source schema names, or exclude one."
        )
    return plan


def rls_warning(con, tables):
    """Rows hidden by Row Level Security are silently missing for roles without BYPASSRLS.
    Supabase's postgres role has BYPASSRLS; a custom read-only role usually doesn't."""
    try:
        bypass = pg_query(con, "SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")
        if bypass and bypass[0][0]:
            return
        rls = {tuple(r) for r in pg_query(
            con,
            "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relrowsecurity",
        )}
        hit = [f"{s}.{t}" for s, t in tables if (s, t) in rls]
        if hit:
            log(f"Warning: RLS is enabled on {hit} and this role has no BYPASSRLS; rows the "
                "policies hide will be missing from the copy")
    except duckdb.Error as exc:
        log(f"(RLS check skipped: {exc})")


# --------------------------------------------------------------------------------------
# Copy and load log
# --------------------------------------------------------------------------------------
def copy_table(con, target_db: str, schema: str, table: str, target_schema: str) -> tuple[int, int]:
    src = f"{SRC}.{qi(schema)}.{qi(table)}"
    dst = f"{qi(target_db)}.{qi(target_schema)}.{qi(table)}"
    cols = {r[0].lower() for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()}
    if "_ingested_at" in cols:
        raise RuntimeError("source table already has an _ingested_at column; exclude or rename it")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {qi(target_db)}.{qi(target_schema)}")
    # One statement: readers see the old copy until the new one is complete.
    con.execute(f"CREATE OR REPLACE TABLE {dst} AS SELECT *, now() AS _ingested_at FROM {src}")
    in_target = con.execute(f"SELECT count(*) FROM {dst}").fetchone()[0]
    in_source = pg_query(con, f"SELECT count(*) FROM {qi(schema)}.{qi(table)}")[0][0]
    return in_target, in_source


def ensure_meta(con, target_db: str):
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {qi(target_db)}.{qi(META_SCHEMA)}")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {qi(target_db)}.{qi(META_SCHEMA)}.load_log (
            flight_id      VARCHAR,
            run_id         VARCHAR,
            source_kind    VARCHAR,
            source_table   VARCHAR,
            target_table   VARCHAR,
            status         VARCHAR,
            rows_in_target BIGINT,
            rows_in_source BIGINT,
            attempts       INTEGER,
            started_at     TIMESTAMPTZ,
            finished_at    TIMESTAMPTZ,
            error          VARCHAR
        )
        """
    )


def write_log(con, target_db: str, row: list):
    try:
        con.execute(
            f"INSERT INTO {qi(target_db)}.{qi(META_SCHEMA)}.load_log "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    except Exception as exc:  # never let logging mask the real result
        log(f"  (could not write load_log row: {exc})")


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main():
    secret = cfg("SECRET_NAME", "supabase")
    url_key = cfg("URL_KEY", "DATABASE_URL")
    target_db = cfg("TARGET_DB", "supabase_raw")
    max_retries = max(1, int(cfg("MAX_RETRIES", "3")))
    # Injected by the Flights runtime; "local" when run outside a Flight.
    flight_id = os.environ.get("MOTHERDUCK_FLIGHT_ID") or "local"
    run_id = os.environ.get("MOTHERDUCK_FLIGHT_RUN_ID") or f"local-{os.getpid()}"

    creds = get_pg_credentials(secret, url_key)
    kind = check_supabase(creds)
    configure_libpq(creds)
    con = connect_motherduck()
    attach_source(con, kind)

    tables = resolve_tables(con, cfg("SOURCE_SCHEMA", "public"), cfg("TABLES"), cfg("EXCLUDE_TABLES"))
    if not tables:
        raise RuntimeError("No tables to copy after applying SOURCE_SCHEMA / TABLES / EXCLUDE_TABLES")
    plan = plan_targets(tables, cfg("TARGET_SCHEMA"))
    rls_warning(con, tables)

    con.execute(f"CREATE DATABASE IF NOT EXISTS {qi(target_db)}")
    ensure_meta(con, target_db)
    log(f"Copying {len(plan)} table(s) into {target_db}")

    failures = []
    for schema, table, target_schema in plan:
        started = datetime.now(timezone.utc)
        in_target = in_source = error = None
        attempt = 0
        while True:
            attempt += 1
            try:
                in_target, in_source = copy_table(con, target_db, schema, table, target_schema)
                status = "succeeded"
                break
            except Exception as exc:
                msg = redact(f"{type(exc).__name__}: {exc}")
                if attempt >= max_retries or is_permanent(exc):
                    status, error = "failed", msg
                    break
                wait = min(60, 2**attempt) + random.random()
                log(f"  {schema}.{table}: attempt {attempt}/{max_retries} failed ({msg}); retrying in {wait:.0f}s")
                time.sleep(wait)

        secs = (datetime.now(timezone.utc) - started).total_seconds()
        if status == "succeeded":
            drift = "" if in_source == in_target else f"  WARNING: source now has {in_source:,} rows (changed during copy?)"
            log(f"  ok    {schema}.{table} -> {target_schema}.{table}: {in_target:,} rows in {secs:.1f}s"
                f" (attempts={attempt}){drift}")
        else:
            failures.append(f"{schema}.{table}")
            log(f"  FAIL  {schema}.{table}: {error}")
        write_log(con, target_db, [
            flight_id, run_id, kind, f"{schema}.{table}", f"{target_schema}.{table}", status,
            in_target, in_source, attempt, started, datetime.now(timezone.utc), error,
        ])

    con.execute(f"DETACH {SRC}")
    con.close()
    log(f"Done: {len(plan) - len(failures)} succeeded, {len(failures)} failed")
    if failures:
        log("Failed tables: " + ", ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        print(redact(traceback.format_exc()), flush=True)
        log(f"FAILED: {redact(f'{type(exc).__name__}: {exc}')}")
        sys.exit(1)
