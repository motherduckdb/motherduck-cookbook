"""Collect MotherDuck operational metrics into MotherDuck tables.

A single-file MotherDuck Flight. Each run:

1. picks a time window contiguous with the previous run (the watermark lives
   in a one-row `state` table in the target database);
2. aggregates `md_information_schema.query_history` for that window into
   per-minute tables: org-wide query counts and latency percentiles, a
   detailed table at the (user, client, DuckDB version, query type,
   instance type, status, error type) grain that the Dive filters on, and
   per-Dive activity. The aggregation runs server-side as
   `INSERT ... SELECT`, so no query rows leave MotherDuck;
3. snapshots point-in-time gauges into timestamped tables: running queries,
   storage per database, databases, access tokens, Flights (and their recent
   runs), Dives, Guides, roles, active users;
4. records its own health in `exporter_runs` and `collector_runs`, prunes rows
   older than `RETENTION_DAYS`, and advances the watermark.

Every collector is isolated: if one fails (for example `storage_info` needs an
org admin), the run logs a warning, records `ok = false` for that collector in
`collector_runs`, and still writes the rest.

Every write is idempotent for its window: a table's rows for the window are
deleted before they are inserted again, so a re-run of a window (after a
failed run, or a manual backfill) never double-counts.

Configuration is read from environment variables so the same file runs locally
(`uv run --with-requirements requirements.txt flight.py`) and as a Flight
(where `config` keys arrive as env vars). See the README "What you'll adjust".
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys
import time

import duckdb

# ---- Configuration (env vars; set them as Flight `config`) -----------------
# Database and schema that hold every metrics table. Created on first run.
TARGET_DATABASE = os.environ.get("TARGET_DATABASE", "md_observability")
TARGET_SCHEMA = os.environ.get("TARGET_SCHEMA", "main")
# Size of the window a run covers when there is no state yet and BACKFILL_HOURS
# is 0. Match the cron interval (a `*/5 * * * *` schedule pairs with 5).
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "5"))
# query_history lags live traffic by one to two minutes (measured); the window
# ends this many seconds before "now" so every query in it has landed.
DELAY_SECONDS = int(os.environ.get("DELAY_SECONDS", "180"))
# On the very first run (no watermark yet) start this many hours back, so the
# Dive has history as soon as the Flight is deployed. query_history keeps
# months of history, so this can be large; a day of a busy org aggregates in
# seconds because the work happens inside MotherDuck.
BACKFILL_HOURS = int(os.environ.get("BACKFILL_HOURS", "24"))
# Upper bound on the window one run may cover when catching up after a gap
# (schedule paused, failed runs). A longer gap is covered a chunk per run.
MAX_CATCHUP_HOURS = int(os.environ.get("MAX_CATCHUP_HOURS", "24"))
# Rows older than this are deleted from every time-indexed table at the end of
# a run. 0 keeps everything.
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "90"))
# Per-minute rows at the (user, client, DuckDB version, query type, instance
# type, status, error type) grain, for every user. This is what the Dive's
# filters run on; roughly 2-5x the rows of query_minutes. "false" disables it.
DETAIL_MINUTES = os.environ.get("DETAIL_MINUTES", "true").lower() == "true"
# Per-Dive activity (queries, viewers, errors) for the busiest N Dives per
# window, identified from the `md-dives/v1(<dive_id>)` tag in user_agent.
TOP_DIVES = int(os.environ.get("TOP_DIVES", "20"))
# Per-database storage rows are kept for the N largest databases by active
# bytes (org totals are always written). 0 disables per-database rows.
STORAGE_TOP_DATABASES = int(os.environ.get("STORAGE_TOP_DATABASES", "25"))
# The slowest N queries of each window are stored with a truncated query text
# for triage. 0 disables the table.
SLOW_QUERIES = int(os.environ.get("SLOW_QUERIES", "5"))
# Which Flights get run metrics: "own" (the token's), "scheduled" (every
# scheduled Flight the token can see; org-wide for an admin), "all", or "none".
# Each Flight costs one MD_LIST_FLIGHT_RUNS call (~0.5 s), bounded by
# FLIGHT_RUNS_BUDGET_SEC per run.
FLIGHT_RUN_SCOPE = os.environ.get("FLIGHT_RUN_SCOPE", "own").lower()
FLIGHT_RUNS_BUDGET_SEC = int(os.environ.get("FLIGHT_RUNS_BUDGET_SEC", "60"))
# Compute and log everything but roll back instead of committing, and leave the
# watermark alone. Lets you smoke-test against a real org without writing.
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
# ---------------------------------------------------------------------------

EXPORTER = "md_observability"
RUN_STARTED = time.monotonic()
RETRY_BUDGET_SEC = 90  # no more retries of a flaky view once the run is this old
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Which client issued a query, derived from query_history.user_agent. MotherDuck
# appends a tag per surface (md-dives/v1(<id>), md-flights(<id>,run_id=..),
# mcp-server-motherduck-remote, motherduck-wasm for the UI) after the duckdb
# version; tools such as dbt, dlt and Airflow add their own. Runtime tags win
# over tool tags, so a dbt build inside a Flight counts as "flight".
CLIENT_SQL = """
    CASE
        WHEN user_agent LIKE '%md-flights(%' THEN 'flight'
        WHEN user_agent LIKE '%md-dives/%' THEN 'dive'
        WHEN user_agent LIKE '%mcp-server-motherduck%' THEN 'mcp'
        WHEN user_agent LIKE '%motherduck-wasm%' THEN 'ui'
        WHEN user_agent LIKE '% dbt/%' OR user_agent LIKE '%dbt-duckdb%' THEN 'dbt'
        WHEN user_agent LIKE '%dlt/%' THEN 'dlt'
        WHEN user_agent LIKE '%airflow%' THEN 'airflow'
        WHEN user_agent LIKE '%pgendpoint%' THEN 'pg_endpoint'
        WHEN user_agent LIKE '%node-neo-api%' OR user_agent LIKE '%nodejs%' THEN 'node'
        WHEN user_agent LIKE '%jdbc%' THEN 'jdbc'
        WHEN user_agent LIKE '% python/%' THEN 'python'
        WHEN user_agent LIKE '% go%' THEN 'go'
        ELSE 'other'
    END"""
DIVE_ID_SQL = "regexp_extract(user_agent, 'md-dives/v[0-9]+\\(([0-9a-f-]{36})\\)', 1)"

# Every table the Flight owns, with the column that orders it in time (used for
# retention). Tables keyed by `minute` hold per-minute aggregates for a window;
# tables keyed by `captured_at` hold one snapshot per run.
TABLES: dict[str, tuple[str, str]] = {
    "state": ("", "exporter VARCHAR PRIMARY KEY, last_window_end TIMESTAMPTZ, updated_at TIMESTAMPTZ"),
    "exporter_runs": ("run_at", """
        run_at TIMESTAMPTZ, window_start TIMESTAMPTZ, window_end TIMESTAMPTZ,
        duration_sec DOUBLE, ok BOOLEAN, collectors_ok INTEGER, collectors_failed INTEGER,
        rows_written BIGINT, flight_run_id VARCHAR"""),
    "collector_runs": ("run_at", """
        run_at TIMESTAMPTZ, collector VARCHAR, ok BOOLEAN, duration_sec DOUBLE,
        rows_written BIGINT, error VARCHAR"""),
    "query_minutes": ("minute", """
        minute TIMESTAMPTZ, query_type VARCHAR, instance_type VARCHAR, status VARCHAR,
        queries BIGINT, spilled_queries BIGINT, spilled_bytes BIGINT,
        bytes_uploaded BIGINT, bytes_downloaded BIGINT,
        max_ms DOUBLE, avg_ms DOUBLE, max_wait_ms DOUBLE"""),
    "query_latency_minutes": ("minute", """
        minute TIMESTAMPTZ, ok_queries BIGINT,
        p50_ms DOUBLE, p95_ms DOUBLE, p99_ms DOUBLE, wait_p95_ms DOUBLE"""),
    "query_error_minutes": ("minute", "minute TIMESTAMPTZ, error_type VARCHAR, errors BIGINT"),
    "query_detail_minutes": ("minute", """
        minute TIMESTAMPTZ, user_name VARCHAR, client VARCHAR, duckdb_version VARCHAR,
        query_type VARCHAR, instance_type VARCHAR, status VARCHAR, error_type VARCHAR,
        queries BIGINT, spilled_queries BIGINT, spilled_bytes BIGINT,
        bytes_uploaded BIGINT, bytes_downloaded BIGINT,
        max_ms DOUBLE, avg_ms DOUBLE, max_wait_ms DOUBLE"""),
    "dive_minutes": ("minute", """
        minute TIMESTAMPTZ, dive_id VARCHAR, dive_title VARCHAR,
        queries BIGINT, viewers BIGINT, errors BIGINT, max_ms DOUBLE"""),
    "slow_queries": ("window_end", """
        window_end TIMESTAMPTZ, end_time TIMESTAMPTZ, query_id VARCHAR, user_name VARCHAR, client VARCHAR,
        duckdb_version VARCHAR, instance_type VARCHAR, query_type VARCHAR, status VARCHAR, error_type VARCHAR,
        total_ms DOUBLE, wait_ms DOUBLE, spilled_bytes BIGINT, query_text VARCHAR"""),
    "activity_snapshots": ("captured_at", """
        captured_at TIMESTAMPTZ, window_start TIMESTAMPTZ, window_end TIMESTAMPTZ,
        active_users BIGINT, active_ducklings BIGINT,
        users_24h BIGINT, users_7d BIGINT, users_30d BIGINT,
        dives_active BIGINT, dive_viewers BIGINT"""),
    "duckling_snapshots": ("captured_at", "captured_at TIMESTAMPTZ, instance_type VARCHAR, ducklings BIGINT"),
    "running_queries": ("captured_at", """
        captured_at TIMESTAMPTZ, running BIGINT, running_users BIGINT, oldest_age_sec DOUBLE"""),
    "running_queries_by_type": ("captured_at", """
        captured_at TIMESTAMPTZ, query_type VARCHAR, instance_type VARCHAR, running BIGINT"""),
    "storage_snapshots": ("captured_at", """
        captured_at TIMESTAMPTZ, database_name VARCHAR, owner VARCHAR, transient BOOLEAN,
        active_bytes BIGINT, historical_bytes BIGINT, retained_for_clone_bytes BIGINT, failsafe_bytes BIGINT"""),
    "storage_totals": ("captured_at", """
        captured_at TIMESTAMPTZ, databases BIGINT, owners BIGINT,
        active_bytes BIGINT, historical_bytes BIGINT, retained_for_clone_bytes BIGINT,
        failsafe_bytes BIGINT, total_bytes BIGINT"""),
    "inventory_counts": ("captured_at", """
        captured_at TIMESTAMPTZ, entity VARCHAR, dimension VARCHAR, label VARCHAR, count BIGINT"""),
    "catalog_snapshots": ("captured_at", """
        captured_at TIMESTAMPTZ, entity VARCHAR, total BIGINT, owners BIGINT,
        created_7d BIGINT, updated_7d BIGINT"""),
    "flight_runs": ("ended_at", """
        flight_id VARCHAR, flight_name VARCHAR, run_id VARCHAR, run_number BIGINT, status VARCHAR,
        is_scheduled BOOLEAN, started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ, duration_sec DOUBLE"""),
    "flight_run_snapshots": ("captured_at", """
        captured_at TIMESTAMPTZ, flight_id VARCHAR, flight_name VARCHAR, in_flight BIGINT"""),
}


class Run:
    """State shared by the collectors of one run."""

    def __init__(self, con: duckdb.DuckDBPyConnection, window_start: dt.datetime,
                 window_end: dt.datetime, captured_at: dt.datetime) -> None:
        self.con = con
        self.start = window_start
        self.end = window_end
        self.captured_at = captured_at
        self.rows_written = 0
        self.status: dict[str, tuple[bool, float, int, str]] = {}

    def run(self, name: str, fn) -> None:
        t0 = time.monotonic()
        rows_before = self.rows_written
        try:
            fn()
            ok, error = True, ""
        except Exception as exc:  # one broken source must not hide the others
            ok, error = False, first_line(exc)
            print(f"WARNING collector {name} failed: {error}", file=sys.stderr)
        elapsed = time.monotonic() - t0
        rows = self.rows_written - rows_before
        self.status[name] = (ok, elapsed, rows, error)
        print(f"  collector {name}: {'ok' if ok else 'FAILED'} in {elapsed:.1f}s, {rows} row(s)")

    # -- write helpers ------------------------------------------------------
    def replace_window(self, table: str, time_column: str) -> None:
        """Delete the rows this run is about to rewrite, so a re-run never double-counts."""
        self.con.execute(
            f"DELETE FROM {qualified(table)} WHERE {time_column} >= ? AND {time_column} < ?",
            [self.start, self.end],
        )

    def insert_select(self, table: str, select_sql: str, params: list) -> int:
        """INSERT ... SELECT inside MotherDuck; returns the number of rows written."""
        row = self.con.execute(f"INSERT INTO {qualified(table)} {select_sql}", params).fetchone()
        n = int(row[0]) if row else 0
        self.rows_written += n
        return n

    def insert_rows(self, table: str, columns: list[str], rows: list[tuple]) -> int:
        """One multi-row INSERT with bound parameters (never executemany), chunked."""
        if not rows:
            return 0
        placeholders = "(" + ", ".join("?" * len(columns)) + ")"
        chunk = max(1, 400 // len(columns))
        for i in range(0, len(rows), chunk):
            batch = rows[i:i + chunk]
            params = [v for r in batch for v in r]
            self.con.execute(
                f"INSERT INTO {qualified(table)} ({', '.join(columns)}) VALUES "
                + ", ".join([placeholders] * len(batch)),
                params,
            )
        self.rows_written += len(rows)
        return len(rows)


def main() -> None:
    started = time.monotonic()
    # Flight logs are captured from a pipe; line-buffer stdout so progress lines
    # carry real timestamps instead of appearing all at once at exit.
    sys.stdout.reconfigure(line_buffering=True)
    validate_config()

    con = duckdb.connect("md:")
    # The Flight container has no timezone configured, which makes DuckDB
    # report 'Etc/Unknown' and fail to hand TIMESTAMPTZ values to Python.
    # Pin UTC so timestamps round-trip the same locally and in a Flight.
    con.execute("SET TimeZone = 'UTC'")
    ensure_tables(con)

    if another_run_is_active(con):
        print("A previous run of this Flight is still running; skipping so windows stay contiguous.")
        return

    now = dt.datetime.now(dt.timezone.utc)
    window_start, window_end = resolve_window(con, now)
    if window_start >= window_end:
        print(f"Window is empty ({window_start:%H:%M} >= {window_end:%H:%M}); run again later.")
        return
    print(f"Collecting window [{window_start:%Y-%m-%d %H:%M}, {window_end:%Y-%m-%d %H:%M}) UTC "
          f"into {TARGET_DATABASE}.{TARGET_SCHEMA}")
    run = Run(con, window_start, window_end, now.replace(microsecond=0))

    if DRY_RUN:
        con.execute("BEGIN TRANSACTION")

    # recent_queries goes last: a failing read of that view can take close to a
    # minute to error out, and the other collectors should not wait behind it.
    collectors = [
        ("query_history", lambda: collect_query_history(run)),
        ("storage_info", lambda: collect_storage(run)),
        ("inventory", lambda: collect_inventory(run)),
        ("flight_runs", lambda: collect_flight_runs(run)),
        ("recent_queries", lambda: collect_running_queries(run)),
    ]
    for name, fn in collectors:
        run.run(name, fn)

    failed = [name for name, (ok, *_rest) in run.status.items() if not ok]
    run.insert_rows("collector_runs", ["run_at", "collector", "ok", "duration_sec", "rows_written", "error"],
                    [(run.captured_at, name, ok, elapsed, rows, error)
                     for name, (ok, elapsed, rows, error) in run.status.items()])
    run.insert_rows("exporter_runs",
                    ["run_at", "window_start", "window_end", "duration_sec", "ok", "collectors_ok",
                     "collectors_failed", "rows_written", "flight_run_id"],
                    [(run.captured_at, window_start, window_end, time.monotonic() - started, not failed,
                      len(run.status) - len(failed), len(failed), run.rows_written,
                      os.environ.get("MOTHERDUCK_FLIGHT_RUN_ID", ""))])
    if RETENTION_DAYS > 0:
        prune(con, now - dt.timedelta(days=RETENTION_DAYS))

    if DRY_RUN:
        con.execute("ROLLBACK")
        print(f"DRY_RUN: rolled back {run.rows_written} row(s); watermark not advanced.")
    else:
        # Advance the watermark only after every write succeeded, so a failed
        # run is retried over the same window next time (writes are idempotent).
        save_window_end(con, window_end)
        print(f"Wrote {run.rows_written} row(s) in {time.monotonic() - started:.1f}s; "
              f"watermark -> {window_end:%Y-%m-%d %H:%M} UTC.")
    con.close()


# ---- Schema ----------------------------------------------------------------

def qualified(table: str) -> str:
    return f'"{TARGET_DATABASE}"."{TARGET_SCHEMA}"."{table}"'


def validate_config() -> None:
    for name, value in (("TARGET_DATABASE", TARGET_DATABASE), ("TARGET_SCHEMA", TARGET_SCHEMA)):
        if not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"{name} must be a plain SQL identifier, got {value!r}")
    if FLIGHT_RUN_SCOPE not in ("own", "scheduled", "all", "none"):
        raise ValueError(f"FLIGHT_RUN_SCOPE must be own, scheduled, all or none, got {FLIGHT_RUN_SCOPE!r}")


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f'CREATE DATABASE IF NOT EXISTS "{TARGET_DATABASE}"')
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{TARGET_DATABASE}"."{TARGET_SCHEMA}"')
    for table, (_time_column, columns) in TABLES.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {qualified(table)} ({columns})")


def prune(con: duckdb.DuckDBPyConnection, older_than: dt.datetime) -> None:
    for table, (time_column, _columns) in TABLES.items():
        if time_column:
            con.execute(f"DELETE FROM {qualified(table)} WHERE {time_column} < ?", [older_than])


# ---- Window / state --------------------------------------------------------

def resolve_window(con: duckdb.DuckDBPyConnection, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    window_end = (now - dt.timedelta(seconds=DELAY_SECONDS)).replace(second=0, microsecond=0)
    last_end = load_window_end(con)
    if last_end is None:
        hours_back = BACKFILL_HOURS if BACKFILL_HOURS > 0 else WINDOW_MINUTES / 60
        start = window_end - dt.timedelta(hours=hours_back)
        if BACKFILL_HOURS > 0:
            print(f"No watermark yet; backfilling {BACKFILL_HOURS}h from {start:%Y-%m-%d %H:%M} UTC.")
        return start, window_end
    # Contiguous with the previous run, but at most MAX_CATCHUP_HOURS per run;
    # a longer gap is covered one chunk per run until the Flight has caught up.
    cap = dt.timedelta(hours=MAX_CATCHUP_HOURS)
    if window_end - last_end > cap:
        capped_end = last_end + cap
        print(f"State is {now - last_end} old; catching up [{last_end:%Y-%m-%d %H:%M}, "
              f"{capped_end:%Y-%m-%d %H:%M}) this run (MAX_CATCHUP_HOURS={MAX_CATCHUP_HOURS}).")
        return last_end, capped_end
    return last_end, window_end


def load_window_end(con: duckdb.DuckDBPyConnection) -> dt.datetime | None:
    row = con.execute(
        f"SELECT last_window_end FROM {qualified('state')} WHERE exporter = ?", [EXPORTER]
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return as_utc(row[0])


def save_window_end(con: duckdb.DuckDBPyConnection, window_end: dt.datetime) -> None:
    con.execute(f"INSERT OR REPLACE INTO {qualified('state')} VALUES (?, ?, now())", [EXPORTER, window_end])


# ---- Collectors ------------------------------------------------------------

def collect_query_history(run: Run) -> None:
    """Per-minute aggregates of completed queries, bucketed by end_time, computed in MotherDuck."""
    # Bucketing by end_time means a query is counted once, in the minute it
    # finished, with its final latency and error status. Queries still running
    # are covered by collect_running_queries.
    w = [run.start, run.end]
    run.replace_window("query_minutes", "minute")
    run.insert_select("query_minutes", """
        SELECT date_trunc('minute', end_time) AS minute,
               coalesce(query_type, 'UNKNOWN') AS query_type,
               coalesce(instance_type, 'unknown') AS instance_type,
               CASE WHEN error_type IS NULL THEN 'ok' ELSE 'error' END AS status,
               count(*) AS queries,
               count(*) FILTER (WHERE bytes_spilled_to_disk > 0) AS spilled_queries,
               coalesce(sum(bytes_spilled_to_disk), 0)::BIGINT AS spilled_bytes,
               coalesce(sum(bytes_uploaded), 0)::BIGINT AS bytes_uploaded,
               coalesce(sum(bytes_downloaded), 0)::BIGINT AS bytes_downloaded,
               max(epoch_ms(total_elapsed_time)) AS max_ms,
               avg(epoch_ms(total_elapsed_time)) AS avg_ms,
               max(epoch_ms(wait_time)) AS max_wait_ms
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ?
        GROUP BY ALL
        """, w)

    # Percentiles are only meaningful over the whole population, so they are
    # computed once per minute over successful queries.
    run.replace_window("query_latency_minutes", "minute")
    run.insert_select("query_latency_minutes", """
        SELECT date_trunc('minute', end_time) AS minute, count(*) AS ok_queries,
               quantile_cont(epoch_ms(total_elapsed_time), 0.50) AS p50_ms,
               quantile_cont(epoch_ms(total_elapsed_time), 0.95) AS p95_ms,
               quantile_cont(epoch_ms(total_elapsed_time), 0.99) AS p99_ms,
               quantile_cont(epoch_ms(wait_time), 0.95) AS wait_p95_ms
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? AND error_type IS NULL
        GROUP BY ALL
        """, w)

    run.replace_window("query_error_minutes", "minute")
    run.insert_select("query_error_minutes", """
        SELECT date_trunc('minute', end_time) AS minute, error_type, count(*) AS errors
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? AND error_type IS NOT NULL
        GROUP BY ALL
        """, w)

    # The detailed grain the Dive filters on: who ran what, from which client
    # and DuckDB version, on which instance type, and how it ended. Every
    # other query table is a roll-up of this one, kept because it is cheaper
    # to read when no filter is active.
    run.replace_window("query_detail_minutes", "minute")
    if DETAIL_MINUTES:
        run.insert_select("query_detail_minutes", f"""
            SELECT date_trunc('minute', end_time) AS minute, user_name, {CLIENT_SQL} AS client,
                   coalesce(duckdb_version, 'unknown') AS duckdb_version,
                   coalesce(query_type, 'UNKNOWN') AS query_type,
                   coalesce(instance_type, 'unknown') AS instance_type,
                   CASE WHEN error_type IS NULL THEN 'ok' ELSE 'error' END AS status, error_type,
                   count(*) AS queries,
                   count(*) FILTER (WHERE bytes_spilled_to_disk > 0) AS spilled_queries,
                   coalesce(sum(bytes_spilled_to_disk), 0)::BIGINT AS spilled_bytes,
                   coalesce(sum(bytes_uploaded), 0)::BIGINT AS bytes_uploaded,
                   coalesce(sum(bytes_downloaded), 0)::BIGINT AS bytes_downloaded,
                   max(epoch_ms(total_elapsed_time)) AS max_ms,
                   avg(epoch_ms(total_elapsed_time)) AS avg_ms,
                   max(epoch_ms(wait_time)) AS max_wait_ms
            FROM md_information_schema.query_history
            WHERE end_time >= ? AND end_time < ?
            GROUP BY ALL
            """, w)

    # Dive usage: every query a Dive runs is tagged with the Dive id, so query
    # volume, distinct viewers and errors per Dive fall out of query_history.
    # Titles come from MD_LIST_DIVES; a Dive the token cannot see keeps its id.
    run.replace_window("dive_minutes", "minute")
    if TOP_DIVES > 0:
        run.insert_select("dive_minutes", f"""
            WITH d AS (
                SELECT date_trunc('minute', end_time) AS minute, {DIVE_ID_SQL} AS dive_id,
                       user_name, error_type, epoch_ms(total_elapsed_time) AS total_ms
                FROM md_information_schema.query_history
                WHERE end_time >= ? AND end_time < ? AND user_agent LIKE '%md-dives/%'
            ), top AS (SELECT dive_id FROM d GROUP BY 1 ORDER BY count(*) DESC LIMIT ?),
            titles AS (SELECT lower(id::VARCHAR) AS dive_id, title FROM MD_LIST_DIVES(include_org_shares := true))
            SELECT d.minute, d.dive_id, coalesce(any_value(t.title), 'unknown') AS dive_title,
                   count(*) AS queries, count(DISTINCT d.user_name) AS viewers,
                   count(*) FILTER (WHERE d.error_type IS NOT NULL) AS errors, max(d.total_ms) AS max_ms
            FROM d LEFT JOIN titles t ON t.dive_id = lower(d.dive_id)
            WHERE d.dive_id IN (SELECT dive_id FROM top)
            GROUP BY d.minute, d.dive_id
            """, [run.start, run.end, TOP_DIVES])

    # The slowest queries of the window, with a truncated text, for triage.
    run.replace_window("slow_queries", "window_end")
    if SLOW_QUERIES > 0:
        run.insert_select("slow_queries", f"""
            SELECT ? AS window_end, end_time, query_id::VARCHAR, user_name, {CLIENT_SQL} AS client,
                   coalesce(duckdb_version, 'unknown'), coalesce(instance_type, 'unknown'),
                   coalesce(query_type, 'UNKNOWN'),
                   CASE WHEN error_type IS NULL THEN 'ok' ELSE 'error' END AS status, error_type,
                   epoch_ms(total_elapsed_time) AS total_ms, epoch_ms(wait_time) AS wait_ms,
                   coalesce(bytes_spilled_to_disk, 0)::BIGINT AS spilled_bytes,
                   left(regexp_replace(query_text, '\\s+', ' ', 'g'), 300) AS query_text
            FROM md_information_schema.query_history
            WHERE end_time >= ? AND end_time < ?
            ORDER BY total_elapsed_time DESC LIMIT ?
            """, [run.end, run.start, run.end, SLOW_QUERIES])

    # Window-level gauges: distinct users and ducklings in the window, Dives
    # queried, and active users over 1/7/30 days. MotherDuck has no SQL or API
    # that lists an organization's members, so "users who ran a query" is the
    # usable proxy.
    run.insert_select("activity_snapshots", f"""
        SELECT ? AS captured_at, ? AS window_start, ? AS window_end,
               count(DISTINCT user_name) FILTER (WHERE end_time >= ? AND end_time < ?) AS active_users,
               count(DISTINCT duckling_id) FILTER (WHERE end_time >= ? AND end_time < ?) AS active_ducklings,
               count(DISTINCT user_name) FILTER (WHERE start_time >= ? - INTERVAL 24 HOUR) AS users_24h,
               count(DISTINCT user_name) FILTER (WHERE start_time >= ? - INTERVAL 7 DAY) AS users_7d,
               count(DISTINCT user_name) AS users_30d,
               count(DISTINCT {DIVE_ID_SQL}) FILTER (WHERE end_time >= ? AND end_time < ? AND user_agent LIKE '%md-dives/%') AS dives_active,
               count(DISTINCT user_name) FILTER (WHERE end_time >= ? AND end_time < ? AND user_agent LIKE '%md-dives/%') AS dive_viewers
        FROM md_information_schema.query_history
        WHERE start_time >= ? - INTERVAL 30 DAY
        """, [run.captured_at, run.start, run.end, run.start, run.end, run.start, run.end,
              run.end, run.end, run.start, run.end, run.start, run.end, run.end])
    run.insert_select("duckling_snapshots", """
        SELECT ? AS captured_at, coalesce(instance_type, 'unknown') AS instance_type,
               count(DISTINCT duckling_id) AS ducklings
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? GROUP BY ALL
        """, [run.captured_at, run.start, run.end])


def collect_running_queries(run: Run) -> None:
    """Point-in-time gauges from recent_queries (updated every few seconds)."""
    # recent_queries is a live view; keep the SQL to plain aggregates. A
    # `WHERE end_time IS NULL` predicate or an unbounded row scan of it can
    # fail server-side, so "running" is derived as count(*) - count(end_time).
    # The view also fails intermittently with an internal error, so each read
    # is retried a few times before the collector gives up for this run.
    con = run.con
    running, users, oldest = with_retry(lambda: con.execute(
        """
        SELECT count(*) - count(end_time),
               count(DISTINCT CASE WHEN end_time IS NULL THEN user_name END),
               min(start_time) FILTER (WHERE end_time IS NULL)
        FROM md_information_schema.recent_queries
        """
    ).fetchone(), label="recent_queries")
    age = (dt.datetime.now(dt.timezone.utc) - as_utc(oldest)).total_seconds() if oldest else 0.0
    run.insert_rows("running_queries", ["captured_at", "running", "running_users", "oldest_age_sec"],
                    [(run.captured_at, running, users, age)])
    by_type = with_retry(lambda: con.execute(
        """
        SELECT coalesce(query_type, 'UNKNOWN'), coalesce(instance_type, 'unknown'),
               count(*) - count(end_time)
        FROM md_information_schema.recent_queries GROUP BY ALL
        """
    ).fetchall(), label="recent_queries by type")
    run.insert_rows("running_queries_by_type", ["captured_at", "query_type", "instance_type", "running"],
                    [(run.captured_at, qtype, itype, n) for qtype, itype, n in by_type if n > 0])


def collect_storage(run: Run) -> None:
    """Storage per database from storage_info (org admins only)."""
    run.insert_select("storage_totals", """
        SELECT ? AS captured_at, count(*) AS databases, count(DISTINCT user_name) AS owners,
               coalesce(sum(active_bytes), 0), coalesce(sum(historical_bytes), 0),
               coalesce(sum(retained_for_clone_bytes), 0), coalesce(sum(failsafe_bytes), 0),
               coalesce(sum(active_bytes + historical_bytes + retained_for_clone_bytes + failsafe_bytes), 0)
        FROM md_information_schema.storage_info WHERE deleted_ts IS NULL
        """, [run.captured_at])
    # Per-database rows only for the largest databases, to bound table growth
    # (an org with 15,000 databases would otherwise add 15,000 rows per run).
    if STORAGE_TOP_DATABASES > 0:
        run.insert_select("storage_snapshots", """
            SELECT ? AS captured_at, database_name, user_name AS owner, transient,
                   active_bytes, historical_bytes, retained_for_clone_bytes, failsafe_bytes
            FROM md_information_schema.storage_info WHERE deleted_ts IS NULL
            ORDER BY active_bytes DESC NULLS LAST LIMIT ?
            """, [run.captured_at, STORAGE_TOP_DATABASES])


def collect_inventory(run: Run) -> None:
    """Counts of the things an org accumulates: databases, tokens, Flights, Dives, Guides, roles."""
    con = run.con
    counts: list[tuple] = []

    def add(entity: str, dimension: str, rows: list[tuple]) -> None:
        counts.extend((run.captured_at, entity, dimension, str(label), int(n)) for label, n in rows)

    add("database", "type", con.execute(
        "SELECT coalesce(type, 'unknown'), count(*) FROM md_information_schema.databases GROUP BY 1").fetchall())
    tokens = con.execute(
        """
        SELECT coalesce(token_type, 'unknown'), count(*),
               count(*) FILTER (WHERE expire_at IS NOT NULL AND expire_at < now() + INTERVAL 7 DAY)
        FROM md_access_tokens() GROUP BY 1
        """).fetchall()
    add("access_token", "token_type", [(t, n) for t, n, _e in tokens])
    add("access_token", "expiring_7d", [(t, e) for t, _n, e in tokens])
    add("flight", "status", con.execute(
        "SELECT coalesce(status, 'unknown'), count(*) FROM MD_LIST_FLIGHTS() GROUP BY 1").fetchall())
    add("flight", "schedule_status", con.execute(
        "SELECT coalesce(schedule_status, 'none'), count(*) FROM MD_LIST_FLIGHTS() GROUP BY 1").fetchall())
    add("dive", "status", con.execute(
        "SELECT coalesce(status, 'unknown'), count(*) FROM MD_LIST_DIVES(include_org_shares := true) GROUP BY 1"
    ).fetchall())
    add("guide", "access", con.execute(
        "SELECT coalesce(access, 'unknown'), count(*) FROM MD_LIST_GUIDES() GROUP BY 1").fetchall())
    add("guide", "topic", con.execute(
        "SELECT coalesce(nullif(split_part(topic, '/', 1), ''), 'none'), count(*) FROM MD_LIST_GUIDES() GROUP BY 1"
    ).fetchall())
    add("role", "role_type", con.execute(
        "SELECT coalesce(role_type, 'unknown'), count(*) FROM md_information_schema.roles GROUP BY 1").fetchall())
    run.insert_rows("inventory_counts", ["captured_at", "entity", "dimension", "label", "count"], counts)

    # Dives (the token's plus every Dive shared with the organization) and
    # Guides: totals, distinct owners, and 7-day create/update counts.
    catalog = []
    for entity, source in (("dive", "MD_LIST_DIVES(include_org_shares := true)"), ("guide", "MD_LIST_GUIDES()")):
        total, owners, created_7d, updated_7d = con.execute(
            f"""
            SELECT count(*), count(DISTINCT owner_name),
                   count(*) FILTER (WHERE created_at > now() - INTERVAL 7 DAY),
                   count(*) FILTER (WHERE updated_at > now() - INTERVAL 7 DAY)
            FROM {source}
            """).fetchone()
        catalog.append((run.captured_at, entity, total, owners, created_7d, updated_7d))
    run.insert_rows("catalog_snapshots", ["captured_at", "entity", "total", "owners", "created_7d", "updated_7d"],
                    catalog)


def collect_flight_runs(run: Run) -> None:
    """Runs that finished in the window, plus currently running/pending runs, per Flight."""
    if FLIGHT_RUN_SCOPE == "none":
        return
    con = run.con
    if FLIGHT_RUN_SCOPE == "own":
        flights = con.execute("SELECT flight_id, flight_name FROM MD_LIST_FLIGHTS(owner_only := true)").fetchall()
    elif FLIGHT_RUN_SCOPE == "scheduled":
        flights = con.execute(
            "SELECT flight_id, flight_name FROM MD_LIST_FLIGHTS() WHERE schedule_cron IS NOT NULL").fetchall()
    else:
        flights = con.execute("SELECT flight_id, flight_name FROM MD_LIST_FLIGHTS()").fetchall()
    # An org admin sees every Flight in the organization, which can be hundreds;
    # stop at the time budget rather than let this collector eat the interval.
    t0, tracked = time.monotonic(), 0
    finished: list[tuple] = []
    snapshots: list[tuple] = []
    for flight_id, flight_name in flights:
        if time.monotonic() - t0 > FLIGHT_RUNS_BUDGET_SEC:
            print(f"WARNING flight_runs: budget of {FLIGHT_RUNS_BUDGET_SEC}s spent after {tracked}/{len(flights)} "
                  "Flights; narrow FLIGHT_RUN_SCOPE or raise FLIGHT_RUNS_BUDGET_SEC", file=sys.stderr)
            break
        tracked += 1
        runs = con.execute(
            'SELECT run_id, run_number, status, is_scheduled, started_at, ended_at '
            'FROM MD_LIST_FLIGHT_RUNS(flight_id := ?::UUID, "limit" := 20)',
            [str(flight_id)],
        ).fetchall()
        in_flight = 0
        for run_id, run_number, status, is_scheduled, started_at, ended_at in runs:
            status = (status or "unknown").lower()
            if status in ("pending", "running"):
                in_flight += 1
                continue
            if ended_at is None:
                continue
            ended = as_utc(ended_at)
            if not (run.start <= ended < run.end):
                continue
            duration = (ended - as_utc(started_at)).total_seconds() if started_at is not None else None
            finished.append((str(flight_id), flight_name, str(run_id), int(run_number), status,
                             bool(is_scheduled), started_at, ended, duration))
        snapshots.append((run.captured_at, str(flight_id), flight_name, in_flight))
    run.replace_window("flight_runs", "ended_at")
    run.insert_rows("flight_runs", ["flight_id", "flight_name", "run_id", "run_number", "status",
                                    "is_scheduled", "started_at", "ended_at", "duration_sec"], finished)
    run.insert_rows("flight_run_snapshots", ["captured_at", "flight_id", "flight_name", "in_flight"], snapshots)


# ---- Helpers ---------------------------------------------------------------

def as_utc(value) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def another_run_is_active(con) -> bool:
    """True when another run of this same Flight is still RUNNING (Flight runtime only)."""
    # The watermark is read at the start of a run and written at the end, so two
    # overlapping runs would write the same window twice (idempotently, but the
    # second would also delete rows the first just wrote). Only skip when this
    # run can positively identify itself in the RUNNING list, so a surprise in
    # the id format can never make every run skip.
    flight_id = os.environ.get("MOTHERDUCK_FLIGHT_ID", "")
    run_id = os.environ.get("MOTHERDUCK_FLIGHT_RUN_ID", "").lower()
    if not flight_id or not run_id:
        return False
    try:
        rows = con.execute(
            'SELECT run_id FROM MD_LIST_FLIGHT_RUNS(flight_id := ?::UUID, "limit" := 10) '
            "WHERE status = 'RUNNING'", [flight_id]).fetchall()
    except duckdb.Error as exc:
        print(f"WARNING could not check for concurrent runs: {first_line(exc)}", file=sys.stderr)
        return False
    running = {str(r[0]).lower() for r in rows}
    return run_id in running and len(running) > 1


def with_retry(fn, label: str, attempts: int = 3):
    """Call fn(), retrying a MotherDuck error a few times with a short backoff."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except duckdb.Error as exc:
            # A failing read can take most of a minute to come back, so stop
            # retrying once the run is old enough that another attempt would
            # push it past the schedule interval.
            if attempt == attempts or time.monotonic() - RUN_STARTED > RETRY_BUDGET_SEC:
                raise
            print(f"WARNING {label} attempt {attempt} failed ({first_line(exc)}); retrying", file=sys.stderr)
            time.sleep(2 * attempt)


def first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


if __name__ == "__main__":
    main()
