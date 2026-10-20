"""
Neon Postgres -> MotherDuck full-refresh copy (MotherDuck Flight).

Copies the base tables of a Postgres database into a MotherDuck database, one
atomic CREATE OR REPLACE per table. Re-runnable: every run is a full refresh.

Flight secret (type: flights), default name "neon", holding EITHER:
  DATABASE_URL                            the Neon connection string (direct endpoint)
OR
  PGHOST, PGDATABASE, PGUSER, PGPASSWORD  (PGPORT optional, default 5432;
                                           PGSSLMODE optional, default require)

Config (all optional, non-secret):
  SECRET_NAME, URL_KEY, INCLUDED_SCHEMAS, EXCLUDED_SCHEMAS, INCLUDED_TABLES,
  EXCLUDED_TABLES, TARGET_DB, TARGET_SCHEMA, MAX_RETRIES, MOTHERDUCK_HOST

requirements.txt:
  duckdb==1.5.6
"""
import os
import random
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, unquote, urlparse

import duckdb

# --- Provider settings: the only part that differs between the Postgres recipes ---
PROVIDER = "Neon"
SOURCE_NAME = "Neon Postgres"  # used in log and error messages
DEFAULT_SECRET = "neon"
DEFAULT_TARGET_DB = "neon_mirror"
DEFAULT_SSLMODE = "require"
# Schemas that are never copied, besides Postgres' own system schemas.
PROVIDER_SYSTEM_SCHEMAS = set()
# Tables skipped unless EXCLUDED_TABLES is set (live stats views some hosts install).
DEFAULT_EXCLUDED_TABLES = "pg_stat_statements,pg_stat_statements_info"
# --------------------------------------------------------------------------------

SRC = "pg_src"  # alias for the attached Postgres database
SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"} | PROVIDER_SYSTEM_SCHEMAS


def q(ident: str) -> str:
    """Quote a SQL identifier."""
    return '"' + ident.replace('"', '""') + '"'


def csv_env(name: str, default: str = "") -> set:
    """Comma-separated config value as a set; unset or empty falls back to the default."""
    return {x.strip() for x in (os.environ.get(name) or default).split(",") if x.strip()}


def check_host(host: str) -> None:
    """Catch the provider's most common wrong-URL mistake before connecting."""
    if PROVIDER == "Render" and "." not in host:
        raise RuntimeError(
            "PGHOST has no domain; this looks like Render's Internal Database URL, "
            "which only works inside Render. Store the External Database URL instead."
        )
    if PROVIDER == "Neon" and "-pooler" in host:
        print(
            "Warning: this is Neon's pooled (PgBouncer) endpoint. "
            "The direct endpoint is more reliable for bulk reads.",
            flush=True,
        )


def load_pg_env(secret: str, url_key: str) -> None:
    """Hand credentials to libpq via PG* env vars, so they never appear in SQL or logs.

    A flights secret is injected as <secret>_<KEY> and, when safe, as the bare <KEY>.
    The namespaced form is preferred; the bare form is the fallback.
    """

    def get(key):
        return os.environ.get(f"{secret}_{key}") or os.environ.get(key)

    url = (get(url_key) or "").strip()
    if url:
        u = urlparse(url)
        if u.scheme not in ("postgres", "postgresql"):
            raise RuntimeError(
                f"Secret '{secret}' key '{url_key}' is not a postgres:// URL (value not printed)"
            )
        params = {
            "PGHOST": u.hostname,
            "PGPORT": str(u.port or 5432),
            "PGUSER": unquote(u.username or ""),
            "PGPASSWORD": unquote(u.password or ""),
            "PGDATABASE": u.path.lstrip("/"),
        }
        sslmode = parse_qs(u.query).get("sslmode", [None])[0]
    else:
        keys = ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE")
        params = {k: get(k) for k in keys}
        params["PGPORT"] = params["PGPORT"] or "5432"
        sslmode = get("PGSSLMODE")

    missing = [k for k, v in params.items() if not v]
    if missing:
        present = sorted(k for k in os.environ if k.startswith(f"{secret}_"))
        raise RuntimeError(
            f"Missing {SOURCE_NAME} credentials {missing} in secret '{secret}'. Keys found: {present}"
        )

    check_host(params["PGHOST"])
    os.environ.update(params)
    os.environ["PGSSLMODE"] = sslmode or DEFAULT_SSLMODE


def connect_motherduck():
    md_host = os.environ.get("MOTHERDUCK_HOST")
    if not md_host:
        return duckdb.connect("md:")
    con = duckdb.connect()
    con.execute("INSTALL motherduck; LOAD motherduck;")
    con.execute(f"SET motherduck_host = '{md_host}'")
    con.execute("ATTACH 'md:'")
    print(f"Connected to MotherDuck at {md_host}", flush=True)
    return con


def discover_base_tables(con) -> list:
    """Base tables only: views, materialized views and foreign tables are skipped,
    so a live view like pg_stat_statements is never copied as a static table."""
    rows = con.execute(
        f"SELECT table_schema, table_name FROM postgres_query('{SRC}', "
        "'SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_type = ''BASE TABLE''') ORDER BY 1, 2"
    ).fetchall()
    return [
        (s, t)
        for s, t in rows
        if s not in SYSTEM_SCHEMAS and not s.startswith(("pg_temp", "pg_toast"))
    ]


def ensure_target(con, tgt_db: str) -> None:
    """Create the target database and the per-table audit ledger."""
    con.execute(f"CREATE DATABASE IF NOT EXISTS {q(tgt_db)}")
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {q(tgt_db)}.main.flight_tracker ("
        "run_id VARCHAR, source_host VARCHAR, source_schema VARCHAR, source_table VARCHAR, "
        "target_schema VARCHAR, target_table VARCHAR, rows_loaded BIGINT, attempts INTEGER, "
        "started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ)"
    )


def with_retries(fn, attempts: int):
    """Retry transient failures with jittered exponential backoff (2s, 4s, 8s, ... max 60s)."""
    for attempt in range(1, attempts + 1):
        try:
            return fn(), attempt
        except Exception as exc:
            if attempt == attempts:
                raise
            delay = min(60, 2**attempt) + random.random()
            print(f"    retry {attempt}/{attempts - 1} in {delay:.0f}s: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(delay)


def main():
    run_id = str(uuid.uuid4())
    secret = os.environ.get("SECRET_NAME") or DEFAULT_SECRET
    url_key = os.environ.get("URL_KEY") or "DATABASE_URL"
    included_schemas = csv_env("INCLUDED_SCHEMAS")
    excluded_schemas = csv_env("EXCLUDED_SCHEMAS")
    included_tables = csv_env("INCLUDED_TABLES")
    excluded_tables = csv_env("EXCLUDED_TABLES", DEFAULT_EXCLUDED_TABLES)
    tgt_db = os.environ.get("TARGET_DB") or DEFAULT_TARGET_DB
    tgt_schema_override = os.environ.get("TARGET_SCHEMA", "").strip()
    max_retries = max(1, int(os.environ.get("MAX_RETRIES") or "3"))

    load_pg_env(secret, url_key)

    con = connect_motherduck()
    con.execute("INSTALL postgres; LOAD postgres;")
    # Empty conninfo -> libpq reads PGHOST/PGUSER/... from the environment.
    con.execute(f"ATTACH '' AS {SRC} (TYPE postgres, READ_ONLY)")
    print(f"Connected to {SOURCE_NAME} (sslmode={os.environ['PGSSLMODE']})", flush=True)

    found = discover_base_tables(con)
    found_schemas = {s for s, _ in found}
    found_names = {f"{s}.{t}" for s, t in found} | {t for _, t in found}
    unknown = sorted(included_schemas - found_schemas) + sorted(included_tables - found_names)
    if unknown:
        raise RuntimeError(f"Not found among {SOURCE_NAME} base tables: {unknown}")

    # A table is copied only if it passes the schema and table gates; exclude always wins.
    tables = [
        (s, t)
        for s, t in found
        if (not included_schemas or s in included_schemas)
        and s not in excluded_schemas
        and (not included_tables or t in included_tables or f"{s}.{t}" in included_tables)
        and t not in excluded_tables
        and f"{s}.{t}" not in excluded_tables
    ]
    if not tables:
        raise RuntimeError("No tables to copy after applying the INCLUDED_* / EXCLUDED_* filters")
    if tgt_schema_override and len({s for s, _ in tables}) > 1:
        raise RuntimeError("TARGET_SCHEMA can only be used when copying a single source schema")

    ensure_target(con, tgt_db)
    print(f"Copying {len(tables)} of {len(found)} base table(s) into {tgt_db}", flush=True)

    failures, drift, total = [], [], 0
    for s, t in tables:
        ts = tgt_schema_override or s
        source = f"{SRC}.{q(s)}.{q(t)}"
        target = f"{q(tgt_db)}.{q(ts)}.{q(t)}"
        started = datetime.now(timezone.utc)

        def copy():
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {q(tgt_db)}.{q(ts)}")
            # CREATE OR REPLACE swaps atomically: readers see the old copy until done.
            con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM {source}")
            return con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]

        try:
            n_dst, attempts = with_retries(copy, max_retries)
            n_src = con.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
            total += n_dst
            con.execute(
                f"INSERT INTO {q(tgt_db)}.main.flight_tracker VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [run_id, os.environ["PGHOST"], s, t, ts, t, n_dst, attempts, started, datetime.now(timezone.utc)],
            )
            note = ""
            if n_src != n_dst:
                drift.append(f"{s}.{t}")
                note = f"  WARNING: Postgres now has {n_src:,} rows (changed during copy?)"
            secs = (datetime.now(timezone.utc) - started).total_seconds()
            print(f"  {s}.{t} -> {ts}.{t}: {n_dst:,} rows in {secs:.1f}s (attempts={attempts}){note}", flush=True)
        except Exception as exc:  # keep going, report at the end
            failures.append(f"{s}.{t}")
            print(f"  {s}.{t}: FAILED - {type(exc).__name__}: {exc}", flush=True)

    con.execute(f"DETACH {SRC}")

    print(f"Total: {total:,} rows in {len(tables) - len(failures)} table(s); run {run_id}", flush=True)
    if drift:
        print(f"Row-count drift (re-run when the source is quiet): {drift}", flush=True)
    if failures:
        raise SystemExit(f"{len(failures)} table(s) failed: {failures}")
    print("Done.", flush=True)


if __name__ == "__main__":
    main()
