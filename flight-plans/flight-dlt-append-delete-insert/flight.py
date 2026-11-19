"""
Postgres -> MotherDuck with dlt: append-only raw history, delete+insert current state

One Flight run does two things, in order:

1. Ingest (dlt). Copy TABLES from Postgres into <TARGET_DATABASE>.<RAW_SCHEMA>.
   - Tables in INCREMENTAL_TABLES are read incrementally on a cursor column (rows at or
     after the last seen cursor) and APPENDED, so raw keeps every version of every row.
     dlt keeps the cursor in <RAW_SCHEMA>._dlt_pipeline_state, so a fresh container
     resumes where the last run stopped.
   - Every other table is a full replace.
   dlt stamps each row with _dlt_load_id (one id per run, increasing).

2. Transform (SQL on MotherDuck). Build <TARGET_DATABASE>.<CURATED_SCHEMA>:
   - A replaced table is copied as-is: CREATE OR REPLACE TABLE curated.t AS SELECT ...
   - An incremental table keeps one current row per key:
       first run   CREATE TABLE AS the latest row per key from all of raw.
       later runs  take raw rows with _dlt_load_id above the max _dlt_load_id in curated,
                   keep the latest per key, drop any older than what curated holds, then
                   DELETE those keys from curated and INSERT the new rows, in one
                   transaction. Readers see the old rows or the new rows, never a gap.
     Curated keeps _dlt_load_id, so curated is its own watermark: no state table.
     DELETE + INSERT instead of MERGE INTO: DuckDB runs a whole-row UPDATE on its slow
     path, about 3x slower than DELETE + INSERT at 5M rows in our tests.
   Then it checks every incremental table has one row per key and every raw key.

Inputs
------
Secret (TYPE flights) -> Postgres connection, EITHER as one URL:
    DATABASE_URL = postgresql://user:password@host:5432/dbname?sslmode=require
OR as params named as the libpq env vars: PGHOST, PGDATABASE, PGUSER, PGPASSWORD,
and optionally PGPORT (5432) and PGSSLMODE (require).
A Flights secret named `pg` injects `pg_DATABASE_URL` (or `pg_PGHOST`, ...); bare names
also work, which is what a local run uses.

Config (non-secret env vars; an empty value means the default):
    TABLES             comma-separated source tables (required).
    INCREMENTAL_TABLES comma-separated table:cursor_column:key[|key2][:initial_value].
                       initial_value (ISO date/timestamp or integer) bounds the FIRST read.
    SOURCE_SCHEMA      Postgres schema (default: public).
    TARGET_DATABASE    MotherDuck database (default: pg_dlt), created if absent.
    RAW_SCHEMA         dlt dataset for the raw layer (default: raw).
    CURATED_SCHEMA     schema for the current-state tables (default: curated).
    DLT_BACKEND        sqlalchemy (default) or pyarrow (faster on wide tables).
    SECRET_NAME        Flights secret name (default: pg).
    PROBE_TIMEOUT_S    seconds to wait for Postgres to answer (default: 90).
"""

import os
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit


def env(name: str, default: str = "") -> str:
    """Config value, with an empty string meaning the default."""
    return (os.environ.get(name) or "").strip() or default


SOURCE_SCHEMA = env("SOURCE_SCHEMA", "public")
TABLES = [t.strip() for t in env("TABLES").split(",") if t.strip()]
INCREMENTAL_TABLES = env("INCREMENTAL_TABLES")
TARGET_DATABASE = env("TARGET_DATABASE", "pg_dlt")
RAW_SCHEMA = env("RAW_SCHEMA", "raw")
CURATED_SCHEMA = env("CURATED_SCHEMA", "curated")
DLT_BACKEND = env("DLT_BACKEND", "sqlalchemy")
SECRET_NAME = env("SECRET_NAME", "pg")
PROBE_TIMEOUT_S = int(env("PROBE_TIMEOUT_S", "90"))

# pandas and connectorx are also dlt backends, but their packages are not in
# requirements.txt. Add the package first to use one.
DLT_BACKENDS = {"sqlalchemy", "pyarrow"}
# Dropped from curated. _dlt_load_id is kept: it is curated's watermark.
BOOKKEEPING = ("_dlt_id",)
# Per-table scratch, qualified so no raw table can shadow it.
CHANGES = "temp.main._changes"

# dlt needs a writable HOME and data dir; a Flight has a writable /tmp.
os.environ.setdefault("HOME", "/tmp")
os.environ.setdefault("DLT_DATA_DIR", "/tmp/dlt")
os.environ.setdefault("RUNTIME__DLTHUB_TELEMETRY", "false")
os.environ.setdefault("RUNTIME__LOG_LEVEL", "WARNING")
# The transform's watermark is _dlt_load_id on every raw row. dlt adds it to row-based
# data (sqlalchemy) but not to Arrow data (pyarrow) unless asked. It is added at extract,
# so pyarrow's Parquet files still skip the normalize rewrite.
os.environ["NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID"] = "true"

import dlt
import duckdb
from dlt.sources.credentials import ConnectionStringCredentials
from dlt.sources.sql_database import sql_database
from sqlalchemy import create_engine, text


def log(step: str, message: str) -> None:
    print(f"[{step}] {message}", flush=True)


def redact(s: str) -> str:
    """Strip the password from anything that might carry a connection string."""
    return re.sub(r"://([^:/@]+):[^@]+@", r"://\1:<redacted>@", s)


def q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


# --------------------------------------------------------------------------- config


def parse_initial_value(raw: str):
    """'2026-11-05', '2026-11-05T08:00:00Z' or '1200' -> a value dlt can compare with
    the cursor. None if it is neither."""
    import pendulum  # a dlt dependency

    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    try:
        value = pendulum.parse(raw)
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, pendulum.DateTime) else None


def parse_incremental(raw: str) -> dict[str, tuple[str, list[str], object]]:
    """'orders:updated_at:id,lines:updated_at:order_id|line_no:2026-11-05'
    -> {table: (cursor, [keys], initial_value or None)}."""
    out = {}
    for entry in filter(None, (e.strip() for e in raw.split(","))):
        parts = entry.split(":", 3)
        initial = parse_initial_value(parts[3]) if len(parts) == 4 else None
        out[parts[0]] = (parts[1], parts[2].split("|"), initial)
    return out


def secret(param: str) -> str:
    """A secret param: `<SECRET_NAME>_<PARAM>` as Flights injects it, else bare <PARAM>."""
    return (os.environ.get(f"{SECRET_NAME}_{param}") or os.environ.get(param) or "").strip()


def validate_config() -> None:
    """Fail before any connection, with one message naming every bad setting."""
    problems = []
    if not TABLES:
        problems.append("TABLES is empty; expected a comma-separated list of source tables")
    if DLT_BACKEND not in DLT_BACKENDS:
        problems.append(f"DLT_BACKEND={DLT_BACKEND!r}; expected one of {sorted(DLT_BACKENDS)}")
    if RAW_SCHEMA == CURATED_SCHEMA:
        problems.append(f"RAW_SCHEMA and CURATED_SCHEMA are both {RAW_SCHEMA!r}")
    for entry in filter(None, (e.strip() for e in INCREMENTAL_TABLES.split(","))):
        parts = entry.split(":", 3)
        if len(parts) not in (3, 4) or not all(parts) or not all(parts[2].split("|")):
            problems.append(
                f"INCREMENTAL_TABLES entry {entry!r}; expected "
                "table:cursor_column:key[|key2][:initial_value]"
            )
            continue
        if len(parts) == 4 and parse_initial_value(parts[3]) is None:
            problems.append(
                f"INCREMENTAL_TABLES entry {entry!r}; initial_value {parts[3]!r} is not an "
                "ISO date/timestamp or an integer"
            )
        if parts[0] not in TABLES:
            problems.append(f"INCREMENTAL_TABLES names {parts[0]!r}, which is not in TABLES")
    if not secret("DATABASE_URL"):
        missing = [p for p in ("PGHOST", "PGUSER", "PGPASSWORD") if not secret(p)]
        if missing:
            problems.append(
                f"no DATABASE_URL and missing {missing}; attach a `{SECRET_NAME}` Flights "
                "secret with DATABASE_URL, or with PGHOST, PGUSER and PGPASSWORD"
            )
    if problems:
        raise ValueError("invalid config: " + "; ".join(problems))
    log(
        "config",
        f"tables={TABLES} incremental={INCREMENTAL_TABLES or '(none)'} "
        f"backend={DLT_BACKEND} target=md:{TARGET_DATABASE} raw={RAW_SCHEMA} "
        f"curated={CURATED_SCHEMA}",
    )


# ----------------------------------------------------------------------- postgres


def build_pg_url() -> str:
    """SQLAlchemy URL from the secret. A DATABASE_URL is used as given apart from the
    scheme, so sslmode and other options from a provider's Connect dialog pass through."""
    url = secret("DATABASE_URL")
    if url:
        return re.sub(r"^postgres(ql)?://", "postgresql+psycopg2://", url, count=1)
    from urllib.parse import quote

    return (
        f"postgresql+psycopg2://{quote(secret('PGUSER'), safe='')}:"
        f"{quote(secret('PGPASSWORD'), safe='')}@{secret('PGHOST')}:"
        f"{secret('PGPORT') or '5432'}/{secret('PGDATABASE') or 'postgres'}"
        f"?sslmode={secret('PGSSLMODE') or 'require'}"
    )


def check_provider(pg_url: str) -> None:
    """Stop on connection details that cannot work from a Flight, naming the fix."""
    u = urlsplit(pg_url)
    host, port, user = (u.hostname or "").lower(), u.port or 5432, u.username or ""
    problems = []
    if host.endswith(".pooler.supabase.com"):
        if "." not in user:
            problems.append("Supabase pooler user must be 'postgres.<project_ref>'")
        if port == 6543:
            problems.append("port 6543 is Supabase's transaction pooler; use 5432")
    elif host.endswith(".supabase.co") and host.startswith("db."):
        problems.append(
            "the Supabase direct host is IPv6-only and Flights connect over IPv4; "
            "use the Session pooler host"
        )
    if host.endswith(".postgres.database.azure.com"):
        if ".private." in host:
            problems.append("an Azure private-access host is not reachable from a Flight")
        if port == 6432:
            problems.append("port 6432 is Azure's PgBouncer; use 5432")
    if host.startswith("dpg-") and "." not in host:
        problems.append("a Render Internal Database URL; use the External Database URL")
    if "[YOUR-PASSWORD]" in pg_url:
        problems.append("DATABASE_URL still contains the [YOUR-PASSWORD] placeholder")
    if problems:
        raise ValueError("connection cannot work from a Flight: " + "; ".join(problems))
    if host.endswith(".neon.tech") and "-pooler" in host:
        log("probe", "WARNING: Neon -pooler host; the direct endpoint is better for bulk reads")
    if parse_qs(u.query).get("sslmode", ["require"])[0] == "disable":
        log("probe", "WARNING: sslmode=disable, the connection is not encrypted")
    log("probe", f"host={host or '(socket)'} port={port} db={u.path.lstrip('/')} user={user}")


def wait_for_postgres(pg_url: str) -> None:
    """Retry a real handshake, so a scaled-to-zero Postgres (Neon, Supabase) can wake up."""
    check_provider(pg_url)
    deadline = time.monotonic() + PROBE_TIMEOUT_S
    engine = create_engine(pg_url, connect_args={"connect_timeout": 10})
    attempt, last_error = 0, None
    while time.monotonic() < deadline:
        attempt += 1
        try:
            with engine.connect() as con:
                version = con.execute(text("SHOW server_version")).scalar()
            log("probe", f"Postgres {version} reachable after {attempt} attempt(s)")
            return
        except Exception as e:  # noqa: BLE001
            last_error = redact(str(e)).splitlines()[0]
            time.sleep(min(2 * attempt, 10))
    raise TimeoutError(f"Postgres not reachable after {PROBE_TIMEOUT_S}s: {last_error}")


# ------------------------------------------------------------------------- ingest


def relax_constraints(table) -> None:
    """Reflect every column as nullable. Raw carries no constraints, and a NOT NULL column
    added in Postgres later would make dlt emit ALTER TABLE ... ADD COLUMN ... NOT NULL,
    which DuckDB rejects."""
    for col in table.columns:
        col.nullable = True


def build_source(pg_url: str, incremental: dict):
    source = sql_database(
        credentials=ConnectionStringCredentials(pg_url),
        schema=SOURCE_SCHEMA,
        table_names=TABLES,
        backend=DLT_BACKEND,
        table_adapter_callback=relax_constraints,
    )
    for table in TABLES:
        resource = source.resources[table]
        if table in incremental:
            cursor, keys, initial = incremental[table]
            resource.apply_hints(
                write_disposition="append",
                incremental=dlt.sources.incremental(cursor, initial_value=initial),
            )
            since = f", first read from {initial}" if initial is not None else ""
            log("ingest", f"{table}: append on cursor {cursor}{since}, current row per {keys}")
        else:
            resource.apply_hints(write_disposition="replace")
            log("ingest", f"{table}: replace")
    return source


def ingest(pg_url: str, incremental: dict) -> None:
    """dlt extract -> normalize -> load into <TARGET_DATABASE>.<RAW_SCHEMA>."""
    con = duckdb.connect("md:")  # dlt needs the database to exist
    con.execute(f"CREATE DATABASE IF NOT EXISTS {q(TARGET_DATABASE)}")
    con.close()
    # MOTHERDUCK_TOKEN is read natively by the destination; only the database is set here.
    pipeline = dlt.pipeline(
        pipeline_name="pg_dlt_append",
        destination=dlt.destinations.motherduck(credentials=f"md:{TARGET_DATABASE}"),
        dataset_name=RAW_SCHEMA,
        pipelines_dir="/tmp/dlt/pipelines",
    )
    # sync_destination restores the incremental cursor from the raw schema, since the
    # Flight container starts empty every run.
    pipeline.sync_destination()
    pipeline.extract(build_source(pg_url, incremental))
    normalized = pipeline.normalize()
    info = pipeline.load()
    counts = normalized.row_counts if normalized else {}
    for table in TABLES:
        log("ingest", f"{table}: {counts.get(table, 0)} rows -> {RAW_SCHEMA}.{table}")
    for s in pipeline.last_trace.steps:
        log("ingest", f"{s.step} took {(s.finished_at - s.started_at).total_seconds():.1f}s")
    log("ingest", f"{len(info.loads_ids)} load package(s)")


# ---------------------------------------------------------------------- transform


def raw_dlt_columns(con, table: str) -> set[str]:
    rows = con.execute(
        "SELECT column_name FROM duckdb_columns() WHERE database_name = ? AND schema_name = ? "
        "AND table_name = ? AND column_name IN ('_dlt_id', '_dlt_load_id')",
        [TARGET_DATABASE, RAW_SCHEMA, table],
    ).fetchall()
    return {r[0] for r in rows}


def exclude_sql(con, table: str) -> str:
    """EXCLUDE clause for the bookkeeping columns this raw table actually has. A pyarrow
    load has no _dlt_id."""
    drop = [c for c in BOOKKEEPING if c in raw_dlt_columns(con, table)]
    return f" EXCLUDE ({', '.join(drop)})" if drop else ""


def latest_per_key(raw_rel: str, cursor: str, keys: list[str], exclude: str, where="TRUE") -> str:
    """Latest row per key: the newest cursor wins, the newest load breaks ties."""
    return (
        f"SELECT *{exclude} FROM ("
        f"  SELECT unnest(max_by(r, (r.{q(cursor)}, r._dlt_load_id))) "
        f"  FROM {raw_rel} AS r WHERE {where} GROUP BY {', '.join(q(k) for k in keys)})"
    )


def columns(con, schema: str, table: str) -> list[tuple[str, str]]:
    rows = con.execute(
        "SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = ? "
        "AND schema_name = ? AND table_name = ? ORDER BY column_index",
        [TARGET_DATABASE, schema, table],
    ).fetchall()
    return [(n, t) for n, t in rows if n not in BOOKKEEPING]


def add_new_columns(con, table: str) -> None:
    """Add to curated any column raw has gained (a column added in Postgres arrives
    through dlt as nullable), so INSERT BY NAME finds it. Nothing is ever dropped."""
    have = {n for n, _ in columns(con, CURATED_SCHEMA, table)}
    for name, dtype in columns(con, RAW_SCHEMA, table):
        if name not in have:
            con.execute(
                f"ALTER TABLE {q(TARGET_DATABASE)}.{q(CURATED_SCHEMA)}.{q(table)} "
                f"ADD COLUMN {q(name)} {dtype}"
            )
            log("transform", f"{table}: added column {name} {dtype} to curated")


def table_exists(con, schema: str, table: str) -> bool:
    return con.execute(
        "SELECT count(*) FROM duckdb_tables() WHERE database_name = ? AND schema_name = ? "
        "AND table_name = ?",
        [TARGET_DATABASE, schema, table],
    ).fetchone()[0] > 0


def apply_changes(con, table: str, cursor: str, keys: list[str]) -> None:
    """Apply the raw rows from loads newer than curated's watermark."""
    db, raw, cur = q(TARGET_DATABASE), q(RAW_SCHEMA), q(CURATED_SCHEMA)
    raw_rel, cur_rel = f"{db}.{raw}.{q(table)}", f"{db}.{cur}.{q(table)}"
    exclude = exclude_sql(con, table)
    if not table_exists(con, CURATED_SCHEMA, table):
        con.execute(f"CREATE TABLE {cur_rel} AS {latest_per_key(raw_rel, cursor, keys, exclude)}")
        n = con.execute(f"SELECT count(*) FROM {cur_rel}").fetchone()[0]
        log("transform", f"{table}: first run, {n} rows from all of raw")
        return
    add_new_columns(con, table)
    watermark = con.execute(f"SELECT coalesce(max(_dlt_load_id), '') FROM {cur_rel}").fetchone()[0]
    new_rows = latest_per_key(raw_rel, cursor, keys, exclude, where=f"r._dlt_load_id > {watermark!r}")
    on = " AND ".join(f"s.{q(k)} = c.{q(k)}" for k in keys)
    on_delete = " AND ".join(f"s.{q(k)} = {q(table)}.{q(k)}" for k in keys)
    con.execute("BEGIN TRANSACTION")
    try:
        # Keep a row only if it is a new key or not older than curated's row. A NULL
        # cursor in curated (a cursor column added later) counts as oldest.
        con.execute(
            f"CREATE TEMP TABLE {CHANGES} AS SELECT s.* FROM ({new_rows}) AS s "
            f"LEFT JOIN {cur_rel} AS c ON {on} "
            f"WHERE c.{q(keys[0])} IS NULL OR c.{q(cursor)} IS NULL "
            f"OR s.{q(cursor)} >= c.{q(cursor)}"
        )
        deleted = con.execute(f"DELETE FROM {cur_rel} USING {CHANGES} AS s WHERE {on_delete}").fetchone()[0]
        inserted = con.execute(f"INSERT INTO {cur_rel} BY NAME SELECT * FROM {CHANGES}").fetchone()[0]
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute(f"DROP TABLE IF EXISTS {CHANGES}")
    log(
        "transform",
        f"{table}: loads after {watermark or '(none)'}: {inserted} rows applied "
        f"({deleted} replaced, {inserted - deleted} new)",
    )


def verify(con, table: str, keys: list[str]) -> bool:
    """Curated holds one row per key and every key raw has seen. It can hold more keys
    than raw when it was seeded from an existing table."""
    db, raw, cur = q(TARGET_DATABASE), q(RAW_SCHEMA), q(CURATED_SCHEMA)
    key_expr = ", ".join(q(k) for k in keys)
    on = " AND ".join(f"c.{q(k)} = r.{q(k)}" for k in keys)
    missing = con.execute(
        f"SELECT count(DISTINCT ({key_expr})) FROM {db}.{raw}.{q(table)} AS r "
        f"WHERE NOT EXISTS (SELECT 1 FROM {db}.{cur}.{q(table)} AS c WHERE {on})"
    ).fetchone()[0]
    rows, distinct = con.execute(
        f"SELECT count(*), count(DISTINCT ({key_expr})) FROM {db}.{cur}.{q(table)}"
    ).fetchone()
    log("verify", f"{table}: curated rows={rows} keys={distinct}, raw keys missing={missing}")
    return rows == distinct and missing == 0


def transform(incremental: dict) -> bool:
    db, raw, cur = q(TARGET_DATABASE), q(RAW_SCHEMA), q(CURATED_SCHEMA)
    con = duckdb.connect("md:")
    # duckdb.connect("md:") returns the instance ingest() opened earlier in this process,
    # and its catalog predates the tables dlt just wrote through its own connection.
    con.execute("REFRESH DATABASES")
    missing = [t for t in TABLES if not table_exists(con, RAW_SCHEMA, t)]
    if missing:
        raise RuntimeError(f"tables not in {TARGET_DATABASE}.{RAW_SCHEMA}: {missing}")
    no_load_id = [t for t in incremental if "_dlt_load_id" not in raw_dlt_columns(con, t)]
    if no_load_id:
        raise RuntimeError(
            f"raw tables without _dlt_load_id: {no_load_id}; drop them and run again"
        )
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {db}.{cur}")
    for t in TABLES:
        if t in incremental:
            cursor, keys, _ = incremental[t]
            apply_changes(con, t, cursor, keys)
        else:
            con.execute(
                f"CREATE OR REPLACE TABLE {db}.{cur}.{q(t)} AS "
                f"SELECT *{exclude_sql(con, t)} FROM {db}.{raw}.{q(t)}"
            )
            n = con.execute(f"SELECT count(*) FROM {db}.{cur}.{q(t)}").fetchone()[0]
            log("transform", f"{t}: replaced, {n} rows")
    ok = all(verify(con, t, incremental[t][1]) for t in incremental)
    con.close()
    return ok


def main() -> int:
    validate_config()
    incremental = parse_incremental(INCREMENTAL_TABLES)
    pg_url = build_pg_url()
    wait_for_postgres(pg_url)
    try:
        ingest(pg_url, incremental)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(redact(str(e))) from None
    if not transform(incremental):
        log("verify", "FAILED: curated has duplicate keys or is missing raw keys")
        return 1
    log("done", f"curated tables in md:{TARGET_DATABASE}.{CURATED_SCHEMA}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
