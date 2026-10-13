#!/usr/bin/env python3
"""
md_bench.py: benchmark query performance on MotherDuck with read scaling.

Everything runs over MotherDuck's Postgres-compatible (PG) endpoint with psycopg2.
The local DuckDB client is never used.

Each invocation runs exactly one test and saves it as its own run:
  --concurrency 1   serial test: one query at a time
  --concurrency N   concurrency test: keep exactly N queries in flight until the queue drains

Flow:
  1. Run the benchmark queries with a READ SCALING token. Every query is tagged with comments:
       -- query_name: <name>
       -- concurrency: 1 query | N queries
       -- md_bench_run: <run_id>
  2. With a READ/WRITE token, read md_information_schema.query_history for this run's
     queries and copy them into a results database (default: md_benchmarks).
  3. Optionally create or update the "MotherDuck query benchmarking" Dive that visualizes the results.

Usage:
  python md_bench.py --queries queries.csv --database my_db --concurrency 1   # serial
  python md_bench.py --queries queries.csv --database my_db --concurrency 5   # 5 at a time

Tokens come from the flags, then the environment variables MOTHERDUCK_READ_SCALING_TOKEN
and MOTHERDUCK_TOKEN, then an interactive prompt.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import queue
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import certifi
    import psycopg2
except ImportError:  # pragma: no cover
    sys.exit("Missing dependencies. Run: pip install -r requirements.txt")

APP_NAME = "md_bench"
DEFAULT_HOST = "pg.us-east-1-aws.motherduck.com"
DIVE_TITLE = "MotherDuck query benchmarking"
DIVE_SOURCE = Path(__file__).parent / "dive" / "benchmark_dive.tsx"
DIVE_ID_FILE = Path(__file__).parent / ".md_bench_dive_id"   # remembers the Dive this folder created
ENV_FILE = Path(__file__).parent / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Read KEY=VALUE lines from a local .env into the environment (existing variables win).

    Lets users keep tokens in a file they edit themselves, so they never paste a token into a chat.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value

# $/hour per read-scaling duckling, by instance type.
PRICE_CASE_SQL = """
    CASE instance_type
        WHEN 'Standard' THEN 2.40
        WHEN 'Jumbo'    THEN 4.80
        WHEN 'Mega'     THEN 12.00
        WHEN 'Giga'     THEN 24.00
        ELSE NULL
    END
"""


# ─────────────────────────────────────────────────────────────────────────────
# Inputs
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BenchQuery:
    name: str
    text: str
    session: str | None = None   # optional session_name for user-to-Duckling affinity


def load_queries(path: str) -> list[BenchQuery]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = [h.strip().lower() for h in (reader.fieldnames or [])]
        if "query_name" not in headers or "query_text" not in headers:
            sys.exit(f"{path}: expected columns query_name,query_text (got {reader.fieldnames})")
        out: list[BenchQuery] = []
        for i, row in enumerate(reader, start=2):
            row = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
            name, text = row["query_name"].strip(), row["query_text"].strip()
            if not name or not text:
                sys.exit(f"{path}:{i}: query_name and query_text must both be non-empty")
            if "\n" in name or "\r" in name:
                sys.exit(f"{path}:{i}: query_name must be a single line")
            session = row.get("session_name", "").strip() or None
            if session and not all(ch.isalnum() or ch in "-_.:" for ch in session):
                sys.exit(f"{path}:{i}: session_name {session!r} may only use letters, digits, '-', '_', '.', and ':'")
            out.append(BenchQuery(name, text.rstrip().rstrip(";"), session))
    if not out:
        sys.exit(f"{path}: no queries found")
    return out


def tag_sql(q: BenchQuery, concurrency: int, run_id: str, with_session: bool = False) -> str:
    label = "1 query" if concurrency == 1 else f"{concurrency} queries"
    session = f"-- session_name: {q.session}\n" if with_session and q.session else ""
    return (
        f"-- query_name: {q.name}\n"
        f"-- concurrency: {label}\n"
        f"-- md_bench_run: {run_id}\n"
        f"{session}"
        f"{q.text}"
    )


def resolve_secret(value: str | None, env: str, prompt: str) -> str:
    value = value or os.environ.get(env) or os.environ.get(env.lower())
    if not value:
        if not sys.stdin.isatty():
            sys.exit(f"{prompt} is required (flag or ${env})")
        value = getpass.getpass(f"{prompt}: ")
    value = value.strip()
    if not value:
        sys.exit(f"{prompt} is required")
    return value


def resolve_plain(value: str | None, prompt: str) -> str:
    if not value:
        if not sys.stdin.isatty():
            sys.exit(f"{prompt} is required")
        value = input(f"{prompt}: ")
    value = value.strip()
    if not value:
        sys.exit(f"{prompt} is required")
    return value


def parse_concurrency(raw: str) -> int:
    try:
        c = int(raw.strip())
    except ValueError:
        sys.exit(f"--concurrency must be a single whole number, such as 1 or 5 (got {raw!r}). "
                 "Run md_bench.py once per concurrency level.")
    if c < 1:
        sys.exit("--concurrency must be 1 or more (1 runs the serial test)")
    return c


# ─────────────────────────────────────────────────────────────────────────────
# Connections
# ─────────────────────────────────────────────────────────────────────────────

def connect(host: str, database: str, token: str, session_name: str | None = None):
    kwargs = dict(
        host=host,
        port=5432,
        dbname=database,
        user="postgres",
        password=token,
        sslmode="verify-full",
        sslrootcert=certifi.where(),
        application_name=APP_NAME,
        connect_timeout=30,
    )
    if session_name:
        # Session affinity for read scaling: the same session_name stays on the same replica.
        kwargs["options"] = f"--session_name={session_name}"
    conn = psycopg2.connect(**kwargs)
    conn.autocommit = True
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Benchmark execution
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    test_name: str
    concurrency: int
    sent: int = 0
    errors: int = 0
    client_wall_ms: float = 0.0
    error_samples: list[str] = field(default_factory=list)


def _run_one(cur, sql: str) -> str | None:
    try:
        cur.execute(sql)  # no params, so psycopg2 leaves '%' in the SQL alone
        if cur.description is not None:
            cur.fetchall()  # include result transfer in the timing
        return None
    except psycopg2.Error as e:
        return str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__


class SessionPool:
    """Connections keyed by session_name, for --session-mode csv.

    MotherDuck routes a connection by its session_name, so a query that borrows a connection opened with its own
    session_name goes to the same read scaling Duckling as every other query for that session. Queries without a
    session_name share connections opened without one, which MotherDuck spreads round robin.
    """

    def __init__(self, factory):
        self.factory = factory
        self.idle: dict[str | None, list] = {}
        self.opened: list = []
        self.lock = threading.Lock()

    def prewarm(self, sessions) -> None:
        for s in sessions:
            c = self.factory(s)
            self.idle.setdefault(s, []).append(c)
            self.opened.append(c)

    def acquire(self, session: str | None):
        with self.lock:
            free = self.idle.get(session)
            if free:
                return free.pop()
        c = self.factory(session)   # another connection for a busy session lands on the same Duckling
        with self.lock:
            self.opened.append(c)
        return c

    def release(self, session: str | None, conn) -> None:
        if conn.closed:
            return
        with self.lock:
            self.idle.setdefault(session, []).append(conn)

    def close_all(self) -> None:
        for c in self.opened:
            try:
                c.close()
            except Exception:
                pass


def _run_with_pool(pool: SessionPool, q: BenchQuery, sql: str) -> str | None:
    conn = pool.acquire(q.session)
    try:
        return _run_one(conn.cursor(), sql)
    finally:
        pool.release(q.session, conn)


def run_serial(queries, rounds, run_id, conn_factory, session_mode="none") -> TestResult:
    res = TestResult("serial", 1)
    work = [q for _ in range(rounds) for q in queries]
    use_sessions = session_mode == "csv"
    pool = None
    if use_sessions:
        pool = SessionPool(conn_factory)
        pool.prewarm({q.session for q in queries})
        conn = None
    else:
        conn = conn_factory(None)
        cur = conn.cursor()
    print(f"\n▶ serial test: {len(work)} queries, one at a time"
          + (f", {len({q.session for q in queries if q.session})} session names" if use_sessions else ""))
    t0 = time.perf_counter()
    for i, q in enumerate(work, 1):
        qs = time.perf_counter()
        sql = tag_sql(q, 1, run_id, use_sessions)
        if pool:
            err = _run_with_pool(pool, q, sql)
        else:
            err = _run_one(cur, sql)
            if err and conn.closed:
                conn = conn_factory(None)
                cur = conn.cursor()
        res.sent += 1
        if err:
            res.errors += 1
            res.error_samples.append(f"{q.name}: {err}")
        print(f"  [{i}/{len(work)}] {q.name:<30} {(time.perf_counter() - qs) * 1000:9.0f} ms"
              f"{'  ERROR' if err else ''}")
    res.client_wall_ms = (time.perf_counter() - t0) * 1000
    if pool:
        pool.close_all()
    else:
        conn.close()
    return res


def run_concurrent(queries, rounds, concurrency, run_id, conn_factory, session_mode) -> TestResult:
    res = TestResult(f"concurrency_{concurrency}", concurrency)
    work: queue.Queue = queue.Queue()
    for _ in range(rounds):
        for q in queries:
            work.put(q)
    total = work.qsize()
    if total < concurrency:
        print(f"  ⚠ only {total} queries queued for concurrency {concurrency}; "
              f"fewer than {concurrency} will ever be in flight. Increase --rounds.")

    use_sessions = session_mode == "csv"
    print(f"\n▶ concurrency test: {total} queries, {concurrency} in flight at all times"
          + (f", {len({q.session for q in queries if q.session})} session names" if use_sessions else ""))

    # Open connections before the clock starts so connection setup isn't measured.
    def session_for(i: int) -> str | None:
        return f"mdbench-{run_id[:8]}-c{concurrency}-w{i}" if session_mode == "per-worker" else None

    pool = None
    conns: list = []
    if use_sessions:
        pool = SessionPool(conn_factory)
        pool.prewarm({q.session for q in queries})
    else:
        conns = [conn_factory(session_for(i)) for i in range(concurrency)]
    lock = threading.Lock()
    done = 0
    start_gate = threading.Barrier(concurrency + 1)

    def worker(i: int):
        nonlocal done
        conn = conns[i] if not pool else None
        cur = conn.cursor() if conn else None
        start_gate.wait()
        while True:
            try:
                q = work.get_nowait()
            except queue.Empty:
                return
            sql = tag_sql(q, concurrency, run_id, use_sessions)
            err = _run_with_pool(pool, q, sql) if pool else _run_one(cur, sql)
            with lock:
                res.sent += 1
                done += 1
                if err:
                    res.errors += 1
                    if len(res.error_samples) < 20:
                        res.error_samples.append(f"{q.name}: {err}")
            if not pool and err and conn.closed:
                conn = conn_factory(session_for(i))
                conns[i] = conn
                cur = conn.cursor()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(concurrency)]
    for t in threads:
        t.start()
    start_gate.wait()
    t0 = time.perf_counter()
    while any(t.is_alive() for t in threads):
        time.sleep(1)
        with lock:
            d = done
        print(f"  {d}/{total} done, {time.perf_counter() - t0:6.1f}s elapsed", end="\r", flush=True)
    for t in threads:
        t.join()
    res.client_wall_ms = (time.perf_counter() - t0) * 1000
    print(f"  {total}/{total} done, {res.client_wall_ms / 1000:6.1f}s elapsed")
    if pool:
        pool.close_all()
    for c in conns:
        try:
            c.close()
        except Exception:
            pass
    return res


# ─────────────────────────────────────────────────────────────────────────────
# Results: query_history → results database
# ─────────────────────────────────────────────────────────────────────────────

def ddl(db: str) -> list[str]:
    return [
        f"CREATE DATABASE IF NOT EXISTS {db}",
        f"""CREATE TABLE IF NOT EXISTS {db}.main.runs (
            run_id VARCHAR, run_label VARCHAR, started_at_utc TIMESTAMP, finished_at_utc TIMESTAMP,
            database_name VARCHAR, user_name VARCHAR, host VARCHAR, rounds INTEGER,
            concurrency_levels VARCHAR, csv_file VARCHAR, query_count INTEGER,
            session_mode VARCHAR, is_read_scaling BOOLEAN)""",
        f"""CREATE TABLE IF NOT EXISTS {db}.main.tests (
            run_id VARCHAR, test_name VARCHAR, concurrency INTEGER, client_wall_ms DOUBLE,
            server_first_start_utc TIMESTAMP, server_last_end_utc TIMESTAMP, e2e_ms DOUBLE,
            query_count INTEGER, error_count INTEGER, replicas_used INTEGER, instance_type VARCHAR,
            hourly_rate_usd DOUBLE, price_usd DOUBLE)""",
        f"""CREATE TABLE IF NOT EXISTS {db}.main.queries (
            run_id VARCHAR, test_name VARCHAR, concurrency INTEGER, query_name VARCHAR,
            query_id UUID, query_text VARCHAR, start_utc TIMESTAMP, end_utc TIMESTAMP,
            duration_ms DOUBLE, exec_ms DOUBLE, wait_ms DOUBLE, duckling_id VARCHAR,
            replica_num INTEGER, instance_type VARCHAR, session_name VARCHAR, user_agent VARCHAR,
            error_message VARCHAR)""",
    ]


def history_filter(run_id: str) -> str:
    # The run_id pattern is assembled with || so this collection query never matches itself.
    return f"""
        (%(user_name)s IS NULL OR user_name = %(user_name)s)
        AND start_time >= %(since)s
        AND query_text ILIKE '%%-- query_name:%%'
        AND query_text ILIKE '%%-- concurrency:%%'
        AND query_text ILIKE '%%' || 'md_bench_run: ' || '{run_id}' || '%%'
    """


def wait_for_history(cur, run_id, params, expected, timeout_s) -> int:
    sql = (f"SELECT count(*) FROM md_information_schema.main.query_history "
           f"WHERE {history_filter(run_id)}")
    deadline = time.time() + timeout_s
    print(f"\n▶ waiting for {expected} queries to appear in md_information_schema.query_history")
    while True:
        cur.execute(sql, params)
        n = cur.fetchone()[0]
        print(f"  found {n}/{expected}", end="\r", flush=True)
        if n >= expected:
            print()
            return n
        if time.time() > deadline:
            print(f"\n  ⚠ timed out after {timeout_s}s with {n}/{expected} queries; continuing with what's there")
            return n
        time.sleep(15)


def persist(cur, db, run_id, params, tests: list[TestResult]):
    cur.execute(f"DELETE FROM {db}.main.queries WHERE run_id = %(run_id)s", {"run_id": run_id})
    cur.execute(f"DELETE FROM {db}.main.tests WHERE run_id = %(run_id)s", {"run_id": run_id})
    cur.execute(f"""
        INSERT INTO {db}.main.queries
        SELECT
            %(run_id)s AS run_id,
            CASE WHEN c = 1 THEN 'serial' ELSE 'concurrency_' || c END AS test_name,
            c AS concurrency,
            regexp_extract(query_text, '-- query_name: ([^\\n]+)', 1) AS query_name,
            query_id,
            query_text,
            (start_time AT TIME ZONE 'UTC') AS start_utc,
            (end_time AT TIME ZONE 'UTC') AS end_utc,
            (epoch_us(end_time) - epoch_us(start_time)) / 1000.0 AS duration_ms,
            date_part('epoch', execution_time) * 1000 AS exec_ms,
            date_part('epoch', wait_time) * 1000 AS wait_ms,
            duckling_id,
            TRY_CAST(NULLIF(regexp_extract(duckling_id, '\\.rs\\.(\\d+)', 1), '') AS INTEGER) AS replica_num,
            instance_type,
            COALESCE(NULLIF(session_name, ''),
                     NULLIF(regexp_extract(query_text, '-- session_name: ([^\\n]+)', 1), '')) AS session_name,
            user_agent,
            error_message
        FROM (
            SELECT *, CAST(regexp_extract(query_text, '-- concurrency: (\\d+)', 1) AS INTEGER) AS c
            FROM md_information_schema.main.query_history
            WHERE {history_filter(run_id)}
        )
    """, params)

    values = ", ".join(
        cur.mogrify("(%s, %s)", (t.test_name, t.client_wall_ms)).decode() for t in tests
    )
    cur.execute(f"""
        INSERT INTO {db}.main.tests
        SELECT
            %(run_id)s, a.test_name, a.concurrency, c.client_wall_ms,
            a.first_start, a.last_end,
            (epoch_us(a.last_end) - epoch_us(a.first_start)) / 1000.0 AS e2e_ms,
            a.query_count, a.error_count, a.replicas_used, a.instance_type,
            {PRICE_CASE_SQL.replace('instance_type', 'a.instance_type')} AS hourly_rate_usd,
            {PRICE_CASE_SQL.replace('instance_type', 'a.instance_type')}
                * ((epoch_us(a.last_end) - epoch_us(a.first_start)) / 1e6 / 3600.0)
                * a.replicas_used AS price_usd
        FROM (
            SELECT
                test_name,
                any_value(concurrency) AS concurrency,
                min(start_utc) AS first_start,
                max(end_utc) AS last_end,
                count(*) AS query_count,
                count(error_message) AS error_count,
                count(DISTINCT duckling_id) AS replicas_used,
                mode(instance_type) AS instance_type
            FROM {db}.main.queries
            WHERE run_id = %(run_id)s
            GROUP BY test_name
        ) a
        LEFT JOIN (VALUES {values}) c(test_name, client_wall_ms) USING (test_name)
    """, {"run_id": run_id})


def print_summary(cur, db, run_id):
    cur.execute(f"""
        SELECT test_name, concurrency, e2e_ms, client_wall_ms, query_count, error_count,
               replicas_used, instance_type, price_usd
        FROM {db}.main.tests WHERE run_id = %(run_id)s ORDER BY concurrency
    """, {"run_id": run_id})
    rows = cur.fetchall()
    print("\n" + "─" * 90)
    print(f"{'TEST':<18}{'E2E (s)':>10}{'CLIENT (s)':>12}{'QUERIES':>9}{'ERRORS':>8}"
          f"{'REPLICAS':>10}{'INSTANCE':>11}{'PRICE ($)':>12}")
    for name, _c, e2e, wall, n, errs, reps, inst, price in rows:
        print(f"{name:<18}{(e2e or 0) / 1000:>10.2f}{(wall or 0) / 1000:>12.2f}{n:>9}{errs:>8}"
              f"{reps:>10}{(inst or '-'):>11}{('%.4f' % price) if price is not None else 'n/a':>12}")
    print("─" * 90)
    print("Price = E2E hours × read-scaling ducklings used × hourly rate "
          "(Standard $2.40, Jumbo $4.80, Mega $12.00, Giga $24.00)")


# ─────────────────────────────────────────────────────────────────────────────
# Setup helpers: --check and --suggest-queries
# ─────────────────────────────────────────────────────────────────────────────

def token_type(token: str) -> str | None:
    """Read the tokenType claim from a MotherDuck token without verifying or printing it."""
    import base64
    import json
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("tokenType")
    except Exception:
        return None


def run_check(host: str, database: str, rs_token: str, rw_token: str) -> bool:
    ok = True
    def line(status: bool, msg: str):
        nonlocal ok
        ok = ok and status
        print(f"  {'✓' if status else '✗'} {msg}")

    print(f"Checking setup against {host}/{database}")
    rs_type, rw_type = token_type(rs_token), token_type(rw_token)
    line(rs_token != rw_token, "the two tokens are different")
    line(rs_type in (None, "read_scaling"), f"read scaling token type: {rs_type or 'unknown'}")
    line(rw_type in (None, "read_write"), f"read/write token type: {rw_type or 'unknown'}")
    try:
        c = connect(host, database, rs_token)
        c.cursor().execute("SELECT 1")
        c.close()
        line(True, "read scaling token connects through the Postgres endpoint")
    except psycopg2.Error as e:
        line(False, f"read scaling connection failed: {str(e).strip().splitlines()[-1]}")
    try:
        rw = connect(host, database, rw_token)
        cur = rw.cursor()
        cur.execute("SELECT 1")
        line(True, "read/write token connects through the Postgres endpoint")
    except psycopg2.Error as e:
        line(False, f"read/write connection failed: {str(e).strip().splitlines()[-1]}")
        return False
    try:
        cur.execute("SELECT count(*) FROM md_information_schema.main.query_history "
                    "WHERE start_time >= now() - INTERVAL 1 DAY")
        line(True, "read/write token can read md_information_schema.query_history")
    except psycopg2.Error:
        line(False, "read/write token can't read md_information_schema.query_history (it needs an organization Admin)")
    try:
        cur.execute("SELECT database_name, type FROM duckdb_databases() WHERE NOT internal ORDER BY 1")
        names = [f"{r[0]}" for r in cur.fetchall() if r[0] not in ("md_information_schema",)]
        print(f"  databases you can query: {', '.join(names) if names else '(none found)'}")
    except psycopg2.Error:
        pass
    rw.close()
    print("Setup looks good." if ok else "Fix the items marked ✗, then run --check again.")
    return ok


def suggest_queries(host: str, database: str, rw_token: str, n: int, days: int, user: str | None, out: Path,
                    rank: str = "frequency") -> None:
    """Write the top N successful read queries from query_history to a CSV.

    rank="frequency" picks the most-run queries; rank="total_time" picks the ones that used the most time overall.
    """
    if out.exists():
        sys.exit(f"{out} already exists. Choose another --out path or move the file first.")
    conn = connect(host, database, rw_token)
    cur = conn.cursor()
    cur.execute(f"""
        SELECT query_text, count(*) AS runs,
               median(date_part('epoch', execution_time) * 1000) AS median_ms
        FROM md_information_schema.main.query_history
        WHERE start_time >= now() - INTERVAL {int(days)} DAY
          AND error_message IS NULL
          AND (%(user)s IS NULL OR user_name = %(user)s)
          AND regexp_matches(lower(query_text), '^\\s*(/\\*.*?\\*/\\s*|--[^\\n]*\\n\\s*)*(select|with|from)\\b')
          AND NOT regexp_matches(query_text, '\\$[0-9]+|\\?')
          AND query_text NOT ILIKE '%%md_bench_run:%%'
          AND query_text NOT ILIKE '%%md_information_schema%%'
          AND query_text NOT ILIKE '%%duckdb_%%'
          AND query_text NOT ILIKE '%%md_live_duckling_size%%'
          AND NOT regexp_matches(lower(query_text), 'read_(csv|json|parquet|text|blob)[a-z_]*\\s*\\(\\s*''(/|\\./|~|[a-z]:)')
          AND NOT regexp_matches(lower(query_text), '\\b(prompt|embedding|prompt_struct)\\s*\\(')
          AND query_text NOT ILIKE '%%MD UI%%'
        GROUP BY query_text
        ORDER BY {"runs * median_ms DESC" if rank == "total_time" else "runs DESC, median_ms DESC"}
        LIMIT {int(n)}
    """, {"user": user})
    rows = cur.fetchall()
    conn.close()
    if not rows:
        sys.exit("No matching read queries found in query_history for that window.")
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["query_name", "query_text"])
        for i, (text, runs, med) in enumerate(rows, 1):
            w.writerow([f"q{i:02d}", text.strip().rstrip(";")])
    print(f"Wrote {len(rows)} queries to {out}. Rename query_name values to something meaningful before you run.")
    for i, (text, runs, med) in enumerate(rows, 1):
        first = " ".join(text.split())[:90]
        print(f"  q{i:02d}  runs={runs:<6} median={(med or 0):>9.0f} ms  {first}")


# ─────────────────────────────────────────────────────────────────────────────
# Dive
# ─────────────────────────────────────────────────────────────────────────────

def resolve_dive_id(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if DIVE_ID_FILE.exists():
        return DIVE_ID_FILE.read_text(encoding="utf-8").strip() or None
    return None


def ensure_share(cur, db: str, access: str) -> str:
    """Return the URL of the results database's share, creating it if needed.

    The Dive reads the share rather than the personal database, so anyone the Dive is sent to can see the data.
    UPDATE AUTOMATIC keeps the share current as new runs are saved.
    """
    share = f"{db}_share"
    cur.execute("SELECT url, access, \"update\" FROM MD_INFORMATION_SCHEMA.OWNED_SHARES "
                "WHERE name = %(name)s AND source_db_name = %(db)s", {"name": share, "db": db})
    row = cur.fetchone()
    if row:
        print(f"Using share {share} ({row[1]} access, {row[2]} updates): {row[0]}")
        return str(row[0])
    visibility = "DISCOVERABLE" if access == "ORGANIZATION" else "HIDDEN"
    cur.execute(f"CREATE SHARE IF NOT EXISTS {share} FROM {db} "
                f"(ACCESS {access}, VISIBILITY {visibility}, UPDATE AUTOMATIC)")
    cur.execute("SELECT url FROM MD_INFORMATION_SCHEMA.OWNED_SHARES "
                "WHERE name = %(name)s AND source_db_name = %(db)s", {"name": share, "db": db})
    row = cur.fetchone()
    if not row:
        sys.exit(f"Created share {share}, but couldn't read its URL from MD_INFORMATION_SCHEMA.OWNED_SHARES.")
    url = str(row[0])
    print(f"Created share {share} ({access.lower()} access, automatic updates): {url}")
    return url


DIVE_DEFAULT_DB = "md_benchmarks"   # the database name written into dive/benchmark_dive.tsx
DIVE_APP_URL = "https://app.motherduck.com/dives/{id}"   # same address in every region


def dive_url(dive_id: str) -> str:
    return DIVE_APP_URL.format(id=dive_id)


def dive_content(db: str, share_url: str) -> str:
    """Return the Dive source pointed at the results share for database `db`.

    The source names the default database in three forms: the alias and SQL catalog ("md_benchmarks"), the
    REQUIRED_DATABASES path ("md:md_benchmarks"), and the on-page text (>md_benchmarks<). All three follow --results-db.
    """
    content = DIVE_SOURCE.read_text(encoding="utf-8")
    declared = f'{{ type: "database", path: "md:{DIVE_DEFAULT_DB}", alias: "{DIVE_DEFAULT_DB}" }}'
    if content.count(declared) != 1:
        sys.exit(f"Couldn't find the REQUIRED_DATABASES entry for {DIVE_DEFAULT_DB} in {DIVE_SOURCE}")
    # Point the Dive at the share, keeping the alias so its fully qualified SQL is unchanged.
    content = content.replace(declared, f'{{ type: "share", path: "{share_url}", alias: "{db}" }}')
    content = (content.replace(f'"md:{DIVE_DEFAULT_DB}"', f'"md:{db}"')
                      .replace(f'"{DIVE_DEFAULT_DB}"', f'"{db}"')
                      .replace(f'>{DIVE_DEFAULT_DB}<', f'>{db}<'))
    if "$mdbench$" in content:
        sys.exit("Dive source contains the $mdbench$ quote tag")
    return content


def publish_dive(cur, db: str, dive_id: str | None, share_access: str = "ORGANIZATION") -> str:
    share_url = ensure_share(cur, db, share_access)
    content = dive_content(db, share_url)
    resources = f"[{{'url': '{share_url}', 'alias': '{db}'}}]"
    if dive_id:
        cur.execute(f"""
            SELECT id FROM MD_UPDATE_DIVE_CONTENT(
                id := '{dive_id}', content := $mdbench${content}$mdbench$,
                description := 'md_bench.py update', required_resources := {resources})
        """)
        cur.fetchall()
        return dive_id
    cur.execute(f"""
        SELECT id FROM MD_CREATE_DIVE(
            title := '{DIVE_TITLE}',
            description := 'Serial and concurrent query benchmarks on MotherDuck read scaling',
            content := $mdbench${content}$mdbench$,
            required_resources := {resources})
    """)
    new_id = str(cur.fetchone()[0])
    DIVE_ID_FILE.write_text(new_id + "\n", encoding="utf-8")
    return new_id


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Benchmark MotherDuck read scaling over the PG endpoint.")
    p.add_argument("--queries", help="CSV with columns query_name,query_text")
    p.add_argument("--database", help="MotherDuck database the queries run against")
    p.add_argument("--rs-token", help="Read scaling token (or $MOTHERDUCK_READ_SCALING_TOKEN)")
    p.add_argument("--rw-token", help="Read/write token for query_history + results (or $MOTHERDUCK_TOKEN / $motherduck_token)")
    p.add_argument("--user-name", help="Optional: MotherDuck user_name to narrow the query_history lookup")
    p.add_argument("--concurrency", help="Queries in flight: 1 runs the serial test, N keeps N queries running")
    p.add_argument("--rounds", type=int, default=1, help="Times the test works through the CSV (default 1)")
    p.add_argument("--host", default=DEFAULT_HOST, help=f"PG endpoint host (default {DEFAULT_HOST})")
    p.add_argument("--session-mode", choices=["none", "csv", "per-worker"], default=None,
                   help="none (default) = let MotherDuck spread connections round robin; "
                        "csv = run each query with the session_name from the CSV (user-to-Duckling affinity); "
                        "per-worker = send a distinct session_name per worker connection")
    p.add_argument("--results-db", default="md_benchmarks", help="Database to store results (default md_benchmarks)")
    p.add_argument("--run-label", default="", help="Optional label to show in the Dive's run selector")
    p.add_argument("--history-timeout", type=int, default=600, help="Seconds to wait for query_history (default 600)")
    p.add_argument("--create-dive", action="store_true",
                   help="Create the results Dive (or update the one this folder already created)")
    p.add_argument("--dive-id", help="Update this existing Dive's content instead")
    p.add_argument("--share-access", choices=["organization", "unrestricted"], default="organization",
                   help="Who can read the results share the Dive uses: everyone in your organization (default), "
                        "or any MotherDuck user with the link")
    p.add_argument("--dive-only", action="store_true", help="Only create/update the Dive, don't benchmark")
    p.add_argument("--validate-only", action="store_true", help="Parse the CSV and print tagged SQL, then exit")
    p.add_argument("--collect", metavar="RUN_ID",
                   help="Collect a finished run from query_history into the results database, then exit")
    p.add_argument("--check", action="store_true", help="Check tokens, connections, and query_history access, then exit")
    p.add_argument("--suggest-queries", type=int, metavar="N",
                   help="Write the N most-run read queries from query_history to --out, then exit")
    p.add_argument("--since-days", type=int, default=7, help="Window for --suggest-queries (default 7)")
    p.add_argument("--rank", choices=["frequency", "total_time"], default="frequency",
                   help="How --suggest-queries ranks queries (default frequency)")
    p.add_argument("--out", default="queries.csv", help="Output path for --suggest-queries (default queries.csv)")
    args = p.parse_args()
    load_env_file()

    if not args.results_db.replace("_", "").isalnum():
        sys.exit("--results-db must be a simple identifier")

    if args.collect:
        rw_token = resolve_secret(args.rw_token, "MOTHERDUCK_TOKEN", "MotherDuck read/write token")
        m = load_manifest(args.collect)
        collect_and_save(m, rw_token, args.history_timeout)
        finish_dive_step(args, m)
        return

    if args.check:
        rs_token = resolve_secret(args.rs_token, "MOTHERDUCK_READ_SCALING_TOKEN", "MotherDuck READ SCALING token")
        rw_token = resolve_secret(args.rw_token, "MOTHERDUCK_TOKEN", "MotherDuck read/write token")
        sys.exit(0 if run_check(args.host, args.database or "my_db", rs_token, rw_token) else 1)

    if args.suggest_queries:
        rw_token = resolve_secret(args.rw_token, "MOTHERDUCK_TOKEN", "MotherDuck read/write token")
        suggest_queries(args.host, args.database or "my_db", rw_token, args.suggest_queries, args.since_days,
                        args.user_name, Path(args.out), args.rank)
        return

    if args.dive_only:
        rw_token = resolve_secret(args.rw_token, "MOTHERDUCK_TOKEN", "MotherDuck read/write token")
        database = resolve_plain(args.database, "Database to connect to (any you can read)")
        conn = connect(args.host, database, rw_token)
        cur = conn.cursor()
        for stmt in ddl(args.results_db):
            cur.execute(stmt)
        existing = resolve_dive_id(args.dive_id)
        dive = publish_dive(cur, args.results_db, existing, args.share_access.upper())
        conn.close()
        print(f"Dive {'updated' if existing else 'created'}: {dive_url(dive)}")
        return

    queries_path = resolve_plain(args.queries, "Path to queries CSV")
    queries = load_queries(queries_path)
    concurrency = parse_concurrency(resolve_plain(args.concurrency, "Concurrency (1 = serial, N = N queries at a time)"))
    if args.rounds < 1:
        sys.exit("--rounds must be >= 1")
    sessions = {q.session for q in queries if q.session}
    if args.session_mode == "csv" and not sessions:
        sys.exit("--session-mode csv needs a session_name column with values in the queries CSV.")
    if args.session_mode is None:
        if sessions and not args.validate_only:
            # A session_name column alone never turns on affinity: the user has to choose it.
            question = (f"The CSV has {len(sessions)} session_name values. Use them for user-to-Duckling affinity, "
                        "so each session's queries stay on one Duckling? [y/N] ")
            if not sys.stdin.isatty():
                sys.exit(f"The CSV has {len(sessions)} session_name values. Confirm with the user whether to use "
                         "user-to-Duckling affinity, then rerun with --session-mode csv (use them) or "
                         "--session-mode none (ignore them and route round robin).")
            args.session_mode = "csv" if input(question).strip().lower() in ("y", "yes") else "none"
        else:
            args.session_mode = "none"
    if sessions and args.session_mode != "csv":
        print(f"Note: ignoring the CSV's {len(sessions)} session_name values; connections are routed round robin.")
    if args.session_mode == "csv":
        print(f"User-to-Duckling affinity is on: {len(sessions)} session names from the CSV.")
    run_id = str(uuid.uuid4())

    if args.validate_only:
        print(f"{len(queries)} queries loaded. Example tagged SQL:\n")
        print(tag_sql(queries[0], concurrency, run_id, args.session_mode == "csv"))
        return

    database = resolve_plain(args.database, "MotherDuck database to benchmark")
    # Optional: each run's tag is unique, so query_history can be matched without a user name.
    user_name = (args.user_name or os.environ.get("MOTHERDUCK_USER_NAME") or "").strip() or None
    rs_token = resolve_secret(args.rs_token, "MOTHERDUCK_READ_SCALING_TOKEN", "MotherDuck READ SCALING token")
    rw_token = resolve_secret(args.rw_token, "MOTHERDUCK_TOKEN", "MotherDuck read/write token")
    if rs_token == rw_token:
        sys.exit("The read scaling token and the read/write token are identical. "
                 "Create a separate Read Scaling token in MotherDuck (Settings → Access Tokens).")

    def rs_conn(session_name):
        return connect(args.host, database, rs_token, session_name)

    # Verify both connections up front.
    print(f"Connecting to {args.host}/{database} …")
    probe = rs_conn(None)
    probe.cursor().execute("SELECT 1 AS connected")
    probe.close()
    rw = connect(args.host, database, rw_token)
    rw_cur = rw.cursor()
    rw_cur.execute("SELECT 1 AS connected")
    print("  read scaling + read/write connections OK")

    started = datetime.now(timezone.utc)
    test_label = "serial" if concurrency == 1 else f"concurrency {concurrency}"
    print(f"run_id {run_id}, {len(queries)} queries × {args.rounds} round(s), {test_label}")

    if concurrency == 1:
        results = [run_serial(queries, args.rounds, run_id, rs_conn, args.session_mode)]
    else:
        results = [run_concurrent(queries, args.rounds, concurrency, run_id, rs_conn, args.session_mode)]
    finished = datetime.now(timezone.utc)
    rw.close()

    for r in results:
        for s in r.error_samples[:5]:
            print(f"  ✗ [{r.test_name}] {s}")

    # Save the run locally first, so a slow query_history never loses it: --collect can finish the job later.
    manifest = {
        "run_id": run_id,
        "label": args.run_label or started.strftime("%Y-%m-%d %H:%M UTC"),
        "started": started.isoformat(), "finished": finished.isoformat(),
        "database": database, "user_name": user_name, "host": args.host, "rounds": args.rounds,
        "concurrency": concurrency, "csv": os.path.basename(queries_path), "query_count": len(queries),
        "session_mode": args.session_mode, "results_db": args.results_db,
        "tests": [{"test_name": r.test_name, "concurrency": r.concurrency, "sent": r.sent,
                   "errors": r.errors, "client_wall_ms": r.client_wall_ms} for r in results],
    }
    path = save_manifest(manifest)
    print(f"\nRun details saved to {path}")

    collect_and_save(manifest, rw_token, args.history_timeout)
    finish_dive_step(args, manifest)


# ─────────────────────────────────────────────────────────────────────────────
# Collection: query_history → results database
# ─────────────────────────────────────────────────────────────────────────────

RUNS_DIR = Path(__file__).parent / "runs"


def save_manifest(m: dict) -> Path:
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{m['run_id']}.json"
    path.write_text(json.dumps(m, indent=2), encoding="utf-8")
    return path


def load_manifest(run_id: str) -> dict:
    path = RUNS_DIR / f"{run_id}.json"
    if not path.exists():
        known = sorted(p.stem for p in RUNS_DIR.glob("*.json")) if RUNS_DIR.exists() else []
        sys.exit(f"No saved run {run_id} in {RUNS_DIR}." + (f" Saved runs: {', '.join(known)}" if known else ""))
    return json.loads(path.read_text(encoding="utf-8"))


def collect_and_save(m: dict, rw_token: str, timeout_s: int) -> None:
    """Wait for the run's queries in query_history, then copy them into the results database.

    Safe to repeat: every write for a run_id replaces what was there.
    """
    db = m["results_db"]
    run_id = m["run_id"]
    results = [TestResult(t["test_name"], t["concurrency"], t["sent"], t["errors"], t["client_wall_ms"])
               for t in m["tests"]]
    started = datetime.fromisoformat(m["started"])
    finished = datetime.fromisoformat(m["finished"])

    rw = connect(m["host"], m["database"], rw_token)
    cur = rw.cursor()
    cur.execute("SET TimeZone = 'UTC'")
    params = {
        "user_name": m.get("user_name"),
        "since": (started - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S+00"),
        "run_id": run_id,
    }
    expected = sum(r.sent for r in results)
    found = wait_for_history(cur, run_id, params, expected, timeout_s)
    if found == 0:
        rw.close()
        sys.exit("\nNone of this run's queries are in md_information_schema.query_history yet. "
                 "Collect the run later with:\n"
                 f"  python md_bench.py --collect {run_id}\n"
                 "If it never appears, check that the read/write token belongs to an organization Admin in the same "
                 "organization as the read scaling token.")
    if found < expected:
        print(f"  ⚠ saving {found} of {expected} queries. Run --collect {run_id} later to pick up the rest.")

    user_name = m.get("user_name")
    if not user_name:
        cur.execute(f"SELECT any_value(user_name) FROM md_information_schema.main.query_history "
                    f"WHERE {history_filter(run_id)}", params)
        user_name = cur.fetchone()[0]

    for stmt in ddl(db):
        cur.execute(stmt)
    persist(cur, db, run_id, params, results)

    cur.execute(f"""SELECT count(*) FILTER (WHERE replica_num IS NOT NULL), count(*)
                    FROM {db}.main.queries WHERE run_id = %(run_id)s""", params)
    rs_rows, all_rows = cur.fetchone()
    is_rs = rs_rows > 0
    if not is_rs:
        print("\n✗ None of the benchmark queries ran on a read scaling Duckling (duckling_id has no '.rs.'). "
              "The read scaling token doesn't appear to be a read scaling token. Results are saved with is_read_scaling=false.")
    elif rs_rows < all_rows:
        print(f"\n⚠ {all_rows - rs_rows} of {all_rows} queries did not run on a read scaling Duckling.")

    cur.execute(f"DELETE FROM {db}.main.runs WHERE run_id = %(run_id)s", params)
    cur.execute(f"""
        INSERT INTO {db}.main.runs VALUES
        (%(run_id)s, %(label)s, %(started)s, %(finished)s, %(db)s, %(user)s, %(host)s, %(rounds)s,
         %(levels)s, %(csv)s, %(nq)s, %(mode)s, %(is_rs)s)
    """, {
        "run_id": run_id, "label": m["label"],
        "started": started.replace(tzinfo=None), "finished": finished.replace(tzinfo=None),
        "db": m["database"], "user": user_name, "host": m["host"], "rounds": m["rounds"],
        "levels": str(m["concurrency"]), "csv": m["csv"], "nq": m["query_count"], "mode": m["session_mode"],
        "is_rs": is_rs,
    })
    print_summary(cur, db, run_id)
    print(f"\nResults saved to {db} (run_id {run_id}).")
    rw.close()


def finish_dive_step(args, m: dict) -> None:
    if args.create_dive or args.dive_id:
        rw_token = resolve_secret(args.rw_token, "MOTHERDUCK_TOKEN", "MotherDuck read/write token")
        rw = connect(m["host"], m["database"], rw_token)
        existing = resolve_dive_id(args.dive_id)
        dive = publish_dive(rw.cursor(), m["results_db"], existing, args.share_access.upper())
        rw.close()
        print(f"Dive {'updated' if existing else 'created'}: {dive_url(dive)}")
    elif resolve_dive_id(None):
        print(f"See this run in the Dive: {dive_url(resolve_dive_id(None))}")
    else:
        print("To see the results, create the Dive once: python md_bench.py --dive-only --create-dive --database <db>")


if __name__ == "__main__":
    main()
