"""MotherDuck Flight: Azure Database for PostgreSQL Flexible Server -> MotherDuck.

Networking assumption
    The server uses "Public access" networking, and its firewall has a rule for
    each MotherDuck Flights outbound IP address in your MotherDuck region (see
    https://motherduck.com/docs/concepts/flights/). Private access (VNet) servers
    are not reachable from Flights.

What each run does
    1. Reads the connection from a Flights secret and runs Azure checks
       (direct port 5432, not PgBouncer on 6432; plain role name, not user@server).
    2. Connects to the source over TLS (sslmode=require unless the secret says otherwise).
    3. Resolves the table list: explicit TABLES, or every table in SCHEMAS.
       Unknown TABLES names stop the run before anything is copied.
    4. For each table, stages rows to local Parquet under /tmp (bounded memory),
       then loads them into MotherDuck one file-sized batch at a time:
         - full refresh (default): build a staging table, then replace the target
         - incremental (opt-in):  pull rows at/after the high-watermark, then upsert
    5. Writes one row per table to <TARGET_DB>._meta.load_log.
    6. Exits non-zero if any table failed, so the run is reported as FAILED.

Flights secret (TYPE flights; name set by SECRET_NAME, default "azure_pg"), holding EITHER
    DATABASE_URL     postgresql://user:password@<server>.postgres.database.azure.com:5432/db?sslmode=require
OR
    PGHOST, PGDATABASE, PGUSER, PGPASSWORD   (PGPORT optional, default 5432;
                                              PGSSLMODE optional, default require)
    Each key is injected as <secret>_<KEY> and usually also as the bare <KEY>;
    the namespaced form wins.

Config (non-secret; empty value = default; set TABLES or SCHEMAS)
    SECRET_NAME      Flights secret name                         default: azure_pg
    URL_KEY          key in the secret holding the URL           default: DATABASE_URL
    TARGET_DB        MotherDuck database to write into           default: azure_pg_raw
    TABLES           "public.orders,public.customers"            overrides SCHEMAS
    SCHEMAS          "public,sales"                              every table in these schemas
    EXCLUDE_TABLES   "public.audit_log,tmp_junk"                 schema.table or bare table name
    INCREMENTAL      "public.orders:updated_at:id,public.line_items:updated_at:order_id|line_no"
                     schema.table:watermark_column:key_column[|key_column...]
                     Tables not listed here get a full refresh.
    BATCH_FILE_SIZE  Parquet file size per load batch            default: 100MB
    FAIL_FAST        "true" stops at the first failing table     default: false
    MOTHERDUCK_HOST  MotherDuck API host override (e.g. staging) default: unset

Microsoft Entra ID (optional, not wired in)
    To use Entra ID instead of a password: add azure-identity to requirements.txt,
    store the service principal's tenant id, client id and client secret in the
    Flights secret, set PGUSER to the Entra role name, and use a fresh token
    (valid about an hour) as PGPASSWORD before attaching:

        from azure.identity import ClientSecretCredential
        cred = ClientSecretCredential(tenant_id, client_id, client_secret)
        os.environ["PGPASSWORD"] = cred.get_token(
            "https://ossrdbms-aad.database.windows.net/.default"
        ).token

requirements.txt
    duckdb==1.5.6
"""
import glob
import hashlib
import os
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import duckdb

STAGE_ROOT = "/tmp/pg_stage"
DUCKDB_TMP = "/tmp/duckdb_tmp"
META_SCHEMA = "_meta"
AZURE_SUFFIX = ".postgres.database.azure.com"


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


def split_qualified(name: str) -> tuple[str, str]:
    schema, _, table = name.partition(".")
    return (schema, table) if table else ("public", schema)


def parse_incremental(raw: str) -> dict[str, tuple[str, list[str]]]:
    """'schema.table:wm_col:key1|key2,...' -> {'schema.table': ('wm_col', ['key1', 'key2'])}"""
    spec = {}
    for entry in parse_list(raw):
        parts = [p.strip() for p in entry.split(":")]
        keys = [k.strip() for k in parts[2].split("|") if k.strip()] if len(parts) == 3 else []
        if len(parts) != 3 or not parts[0] or not parts[1] or not keys:
            raise ValueError(
                f"Bad INCREMENTAL entry {entry!r}; expected schema.table:watermark_column:key_column"
            )
        schema, table = split_qualified(parts[0])
        spec[f"{schema}.{table}".lower()] = (parts[1], keys)
    return spec


def short_id(schema: str, table: str) -> str:
    """Collision-free, length-bounded name part for staging dirs and tables."""
    readable = "".join(c if c.isalnum() else "_" for c in table)[:40]
    digest = hashlib.sha1(f"{schema}.{table}".encode()).hexdigest()[:10]
    return f"{readable}_{digest}"


# --------------------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------------------
def get_pg_credentials(secret: str, url_key: str) -> dict:
    """Read the connection from the Flights secret. Error messages list key names only."""

    def get(key):
        return os.environ.get(f"{secret}_{key}") or os.environ.get(key)

    url = (get(url_key) or "").strip()
    if url:
        u = urlparse(url)
        if u.scheme not in ("postgres", "postgresql"):
            raise RuntimeError(
                f"Secret '{secret}' key '{url_key}' is not a postgresql:// URL (value not printed)"
            )
        try:
            port = str(u.port or 5432)
        except ValueError:
            raise RuntimeError(f"Secret '{secret}' key '{url_key}' has an invalid port") from None
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


def check_azure(creds: dict):
    """Fail fast or warn on the common Azure Flexible Server mistakes. Never logs secrets."""
    host, user = creds["PGHOST"].lower(), creds["PGUSER"]
    is_azure = host.endswith(AZURE_SUFFIX)
    if not is_azure:
        log(f"Warning: host does not end in {AZURE_SUFFIX}; Azure-specific checks skipped")
    if host.endswith(".private" + AZURE_SUFFIX):
        log("Warning: this is a private-access (VNet) host; Flights can only reach public endpoints")
    if creds["PGPORT"] == "6432":
        if is_azure:
            log("Warning: port 6432 is Azure's built-in PgBouncer; using the direct port 5432 instead")
            creds["PGPORT"] = "5432"
        else:
            log("Warning: port 6432 is usually a PgBouncer pooler; the direct port is more reliable")
    server = host.split(".")[0]
    if is_azure and "@" in user and user.lower().endswith("@" + server):
        raise RuntimeError(
            "PGUSER uses the legacy Single Server 'user@servername' format. Flexible Server "
            "expects the plain role name; drop the '@servername' suffix from the secret."
        )
    if creds["PGSSLMODE"] in ("disable", "allow", "prefer"):
        log(f"Warning: sslmode={creds['PGSSLMODE']}; Azure enforces TLS by default, use require")


# --------------------------------------------------------------------------------------
# Connections
# --------------------------------------------------------------------------------------
def configure_libpq(creds: dict):
    """DuckDB's postgres extension uses libpq, which reads the standard PG* variables.
    This avoids building a DSN string, so passwords containing quotes or spaces are safe."""
    os.environ.update(creds)
    os.environ["PGCONNECT_TIMEOUT"] = "20"
    os.environ["PGAPPNAME"] = "motherduck-flight"  # lets the DBA spot these sessions


def connect_motherduck() -> duckdb.DuckDBPyConnection:
    host = cfg("MOTHERDUCK_HOST")
    if host:
        os.environ["motherduck_host"] = host
        log(f"Using MotherDuck host {host}")
    os.makedirs(DUCKDB_TMP, exist_ok=True)
    con = duckdb.connect("md:")
    con.execute(f"SET temp_directory = {ql(DUCKDB_TMP)}")
    con.execute("SET preserve_insertion_order = false")  # lowers memory for large COPYs
    con.execute("SET TimeZone = 'UTC'")                   # stable watermark rendering
    con.execute("INSTALL postgres")
    con.execute("LOAD postgres")
    return con


def attach_source(con: duckdb.DuckDBPyConnection, attempts: int = 4):
    """Attach the source read-only, retrying transient network hiccups (not auth errors)."""
    for attempt in range(1, attempts + 1):
        try:
            con.execute("ATTACH '' AS src (TYPE postgres, READ_ONLY)")
            version = con.execute(
                "SELECT * FROM postgres_query('src', 'SELECT version()')"
            ).fetchone()[0]
            log(f"Connected to source (sslmode={os.environ['PGSSLMODE']}): {version.split(',')[0]}")
            return
        except duckdb.Error as exc:
            msg = str(exc)
            permanent = any(
                s in msg
                for s in ("password authentication failed", "no pg_hba.conf entry", "does not exist")
            )
            if attempt == attempts or permanent:
                if "timeout" in msg.lower() or "timed out" in msg.lower():
                    log("Hint: a connect timeout usually means the Azure firewall has no rule "
                        "for the Flights outbound IPs of your MotherDuck region")
                raise
            wait = 5 * 2**attempt
            log(f"Source connection failed ({attempt}/{attempts}): {exc}. Retrying in {wait}s")
            try:
                con.execute("DETACH DATABASE IF EXISTS src")
            except duckdb.Error:
                pass
            time.sleep(wait)


# --------------------------------------------------------------------------------------
# Table discovery
# --------------------------------------------------------------------------------------
def list_source_tables(con, schemas: list[str] | None) -> list[tuple[str, str]]:
    # Regular and partitioned tables, skipping individual partitions
    # (the parent table already returns their rows).
    where = "c.relkind IN ('r', 'p') AND NOT c.relispartition"
    if schemas:
        where += f" AND n.nspname IN ({', '.join(ql(s) for s in schemas)})"
    else:
        where += " AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%'"
    pg_sql = (
        "SELECT n.nspname, c.relname "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE {where} ORDER BY 1, 2"
    )
    return [tuple(r) for r in con.execute(f"SELECT * FROM postgres_query('src', {ql(pg_sql)})").fetchall()]


def resolve_tables(con: duckdb.DuckDBPyConnection) -> list[tuple[str, str]]:
    exclude = {t.lower() for t in parse_list(cfg("EXCLUDE_TABLES"))}
    explicit = parse_list(cfg("TABLES"))

    if explicit:
        wanted = [split_qualified(t) for t in explicit]
        found = set(list_source_tables(con, sorted({s for s, _ in wanted})))
        unknown = [f"{s}.{t}" for s, t in wanted if (s, t) not in found]
        if unknown:
            raise RuntimeError(f"TABLES not found as tables in the source: {unknown}")
        tables = wanted
    else:
        schemas = parse_list(cfg("SCHEMAS"))
        if not schemas:
            raise RuntimeError("Set TABLES or SCHEMAS in the flight config")
        tables = list_source_tables(con, schemas)
        empty = sorted(set(schemas) - {s for s, _ in tables})
        if empty:
            log(f"Warning: no tables found in schema(s) {empty}")

    return [
        (s, t) for s, t in tables
        if f"{s}.{t}".lower() not in exclude and t.lower() not in exclude
    ]


# --------------------------------------------------------------------------------------
# Staging and loading
# --------------------------------------------------------------------------------------
def stage_to_parquet(con, pg_sql: str, stage_dir: str, file_size: str) -> list[str]:
    """Stream the source query to local Parquet files of roughly file_size each.
    COPY ... TO <dir> with FILE_SIZE_BYTES writes a directory of data_N.parquet files
    (one empty file when the query returns no rows)."""
    shutil.rmtree(stage_dir, ignore_errors=True)
    os.makedirs(os.path.dirname(stage_dir), exist_ok=True)  # COPY creates only the leaf dir
    con.execute(
        f"COPY (SELECT * FROM postgres_query('src', {ql(pg_sql)})) "
        f"TO {ql(stage_dir)} (FORMAT parquet, FILE_SIZE_BYTES {ql(file_size)})"
    )
    files = glob.glob(os.path.join(stage_dir, "**", "*.parquet"), recursive=True)
    # data_10 must sort after data_9.
    return sorted(files, key=lambda p: (len(p), p))


def count_parquet(con, files: list[str]) -> int:
    if not files:
        return 0
    file_list = "[" + ", ".join(ql(f) for f in files) + "]"
    return con.execute(f"SELECT count(*) FROM read_parquet({file_list})").fetchone()[0]


def build_staging_table(con, stg: str, files: list[str], empty_pg_sql: str):
    """Create the MotherDuck staging table from Parquet files, one batch per file."""
    if files:
        con.execute(f"CREATE OR REPLACE TABLE {stg} AS SELECT * FROM read_parquet({ql(files[0])})")
        for path in files[1:]:
            con.execute(f"INSERT INTO {stg} SELECT * FROM read_parquet({ql(path)})")
    else:
        # Defensive: no files at all. Still create the table with the source's column types.
        con.execute(
            f"CREATE OR REPLACE TABLE {stg} AS "
            f"SELECT * FROM postgres_query('src', {ql(empty_pg_sql)})"
        )


def target_columns(con, target_db: str, schema: str, table: str) -> set[str]:
    rows = con.execute(
        "SELECT column_name FROM duckdb_columns() "
        "WHERE database_name = ? AND schema_name = ? AND table_name = ?",
        [target_db, schema, table],
    ).fetchall()
    return {r[0] for r in rows}


def staging_name(target_db: str, schema: str, table: str, run_tag: str) -> str:
    return f"{qi(target_db)}.{qi(META_SCHEMA)}.{qi(f'stg_{short_id(schema, table)}_{run_tag}')}"


def load_full(con, target_db, schema, table, stage_dir, file_size, run_tag) -> tuple[int, int]:
    tgt = f"{qi(target_db)}.{qi(schema)}.{qi(table)}"
    stg = staging_name(target_db, schema, table, run_tag)
    src_sql = f"SELECT * FROM {qi(schema)}.{qi(table)}"

    files = stage_to_parquet(con, src_sql, stage_dir, file_size)
    staged = count_parquet(con, files)
    try:
        build_staging_table(con, stg, files, f"SELECT * FROM ({src_sql}) q LIMIT 0")
        # Readers keep seeing the old table until this single statement completes.
        con.execute(f"CREATE OR REPLACE TABLE {tgt} AS SELECT * FROM {stg}")
    finally:
        con.execute(f"DROP TABLE IF EXISTS {stg}")

    total = con.execute(f"SELECT count(*) FROM {tgt}").fetchone()[0]
    return staged, total


def load_incremental(
    con, target_db, schema, table, wm_col, keys, stage_dir, file_size, run_tag
) -> tuple[int, int]:
    tgt = f"{qi(target_db)}.{qi(schema)}.{qi(table)}"
    stg = staging_name(target_db, schema, table, run_tag)
    src_sql = f"SELECT * FROM {qi(schema)}.{qi(table)}"

    # First run (no target yet) is a full load.
    cols = target_columns(con, target_db, schema, table)
    if not cols:
        log(f"  {schema}.{table}: target missing, doing initial full load")
        return load_full(con, target_db, schema, table, stage_dir, file_size, run_tag)
    missing = [c for c in [wm_col, *keys] if c not in cols]
    if missing:
        raise RuntimeError(f"INCREMENTAL columns not in {schema}.{table}: {missing}")

    # TimeZone is UTC, so a timestamptz renders with an explicit +00 offset and
    # Postgres compares it as the same instant; timestamp/date/int render as plain literals.
    watermark = con.execute(f"SELECT CAST(max({qi(wm_col)}) AS VARCHAR) FROM {tgt}").fetchone()[0]
    if watermark is not None:
        # >= rather than > so rows sharing the last timestamp aren't missed;
        # the upsert below makes re-pulled rows harmless.
        src_sql += f" WHERE {qi(wm_col)} >= {ql(watermark)}"
    log(f"  {schema}.{table}: watermark {wm_col} >= {watermark}")

    files = stage_to_parquet(con, src_sql, stage_dir, file_size)
    staged = count_parquet(con, files)
    if staged == 0:
        total = con.execute(f"SELECT count(*) FROM {tgt}").fetchone()[0]
        return 0, total

    key_match = " AND ".join(f"t.{qi(k)} = s.{qi(k)}" for k in keys)
    try:
        build_staging_table(con, stg, files, f"SELECT * FROM ({src_sql}) q LIMIT 0")
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(f"DELETE FROM {tgt} AS t USING {stg} AS s WHERE {key_match}")
            con.execute(f"INSERT INTO {tgt} BY NAME SELECT * FROM {stg}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.execute(f"DROP TABLE IF EXISTS {stg}")

    total = con.execute(f"SELECT count(*) FROM {tgt}").fetchone()[0]
    return staged, total


# --------------------------------------------------------------------------------------
# Load log
# --------------------------------------------------------------------------------------
def ensure_meta(con, target_db: str):
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {qi(target_db)}.{qi(META_SCHEMA)}")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {qi(target_db)}.{qi(META_SCHEMA)}.load_log (
            flight_id      VARCHAR,
            run_id         VARCHAR,
            source_table   VARCHAR,
            mode           VARCHAR,
            status         VARCHAR,
            rows_staged    BIGINT,
            rows_in_target BIGINT,
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
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    except Exception as exc:  # never let logging mask the real result
        log(f"  (could not write load_log row: {exc})")


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main():
    secret = cfg("SECRET_NAME", "azure_pg")
    url_key = cfg("URL_KEY", "DATABASE_URL")
    target_db = cfg("TARGET_DB", "azure_pg_raw")
    file_size = cfg("BATCH_FILE_SIZE", "100MB")
    fail_fast = cfg("FAIL_FAST", "false").lower() == "true"
    incremental = parse_incremental(cfg("INCREMENTAL"))
    # Injected by the Flights runtime; "local" when run outside a Flight.
    flight_id = os.environ.get("MOTHERDUCK_FLIGHT_ID") or "local"
    run_id = os.environ.get("MOTHERDUCK_FLIGHT_RUN_ID") or f"local-{os.getpid()}"
    run_tag = hashlib.sha1(run_id.encode()).hexdigest()[:8]

    creds = get_pg_credentials(secret, url_key)
    check_azure(creds)
    configure_libpq(creds)
    con = connect_motherduck()
    attach_source(con)

    con.execute(f"CREATE DATABASE IF NOT EXISTS {qi(target_db)}")
    ensure_meta(con, target_db)

    tables = resolve_tables(con)
    if not tables:
        raise RuntimeError("No tables matched TABLES/SCHEMAS after EXCLUDE_TABLES")
    selected = {f"{s}.{t}".lower() for s, t in tables}
    unused = sorted(set(incremental) - selected)
    if unused:
        log(f"Warning: INCREMENTAL entries not in the selected tables: {unused}")
    log(f"Loading {len(tables)} table(s) into {target_db}")

    failures, processed = [], 0
    try:
        for schema, table in tables:
            processed += 1
            key = f"{schema}.{table}".lower()
            mode = "incremental" if key in incremental else "full"
            stage_dir = os.path.join(STAGE_ROOT, short_id(schema, table))
            started = datetime.now(timezone.utc)
            staged = total = error = None
            log(f"{schema}.{table}: starting ({mode})")

            try:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {qi(target_db)}.{qi(schema)}")
                if mode == "full":
                    staged, total = load_full(
                        con, target_db, schema, table, stage_dir, file_size, run_tag
                    )
                else:
                    wm_col, keys = incremental[key]
                    staged, total = load_incremental(
                        con, target_db, schema, table, wm_col, keys, stage_dir, file_size, run_tag
                    )
                status = "succeeded"
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                log(f"{schema}.{table}: {staged:,} rows staged, {total:,} in target ({elapsed:.1f}s)")
            except Exception as exc:
                status, error = "failed", f"{type(exc).__name__}: {exc}"
                failures.append(f"{schema}.{table}")
                log(f"{schema}.{table}: FAILED - {error}")
                traceback.print_exc()
            finally:
                shutil.rmtree(stage_dir, ignore_errors=True)

            write_log(
                con,
                target_db,
                [flight_id, run_id, f"{schema}.{table}", mode, status,
                 staged, total, started, datetime.now(timezone.utc), error],
            )
            if failures and fail_fast:
                log("FAIL_FAST is set; stopping")
                break
    finally:
        shutil.rmtree(STAGE_ROOT, ignore_errors=True)
        shutil.rmtree(DUCKDB_TMP, ignore_errors=True)

    succeeded = processed - len(failures)
    skipped = len(tables) - processed
    log(f"Done: {succeeded} succeeded, {len(failures)} failed" + (f", {skipped} skipped" if skipped else ""))
    if failures:
        log("Failed tables: " + ", ".join(failures))
        sys.exit(1)


if __name__ == "__main__":
    main()
