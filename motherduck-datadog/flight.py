"""Export MotherDuck operational metrics to Datadog.

A single-file MotherDuck Flight. Each run:

1. picks a time window (contiguous with the previous run, tracked in a small
   state table in MotherDuck);
2. aggregates `md_information_schema.query_history` for that window into
   per-minute query counts, error counts, latency percentiles, spill/transfer
   bytes, per-user activity and active ducklings;
3. snapshots point-in-time gauges: running queries, storage per database,
   access tokens, databases, Flights (and their recent runs), Dives, Guides,
   roles;
4. posts everything to the Datadog metrics API (`POST /api/v2/series`).

Every collector is isolated: if one fails (for example `storage_info` needs an
org admin), the run logs a warning, reports it through
`<prefix>.exporter.collector_ok`, and still ships the rest.

Configuration is read from environment variables so the same file runs locally
(`uv run --with-requirements requirements.txt flight.py`) and as a Flight
(where `config` keys and Flight secrets arrive as env vars). See the README
"What you'll adjust" table.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict

import duckdb
import httpx

# ---- Configuration (env vars; set them as Flight `config`) -----------------
# Metric name prefix. Every metric is `<prefix>.<name>`.
METRIC_PREFIX = os.environ.get("METRIC_PREFIX", "motherduck")
# Datadog site, e.g. datadoghq.com, datadoghq.eu, us3.datadoghq.com, us5.datadoghq.com, ap1.datadoghq.com.
DD_SITE = os.environ.get("DD_SITE", "datadoghq.com")
# Extra tags added to every series, comma-separated: "env:prod,team:data".
DD_TAGS = [t.strip() for t in os.environ.get("DD_TAGS", "").split(",") if t.strip()]
# Size of the window a run covers when there is no state yet. Match the cron
# interval (a `*/5 * * * *` schedule pairs with 5).
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "5"))
# query_history lags live traffic by one to two minutes (measured); the window
# ends this many seconds before "now" so every query in it has landed. Rows
# that land after the watermark has moved past their minute are never exported.
DELAY_SECONDS = int(os.environ.get("DELAY_SECONDS", "180"))
# Where the exporter remembers the end of the last exported window, as
# database.schema.table in a writable database. "" disables state (each run
# then exports the last WINDOW_MINUTES, which can double-count or skip a
# minute when a run starts late).
STATE_TABLE = os.environ.get("STATE_TABLE", "datadog_exporter.main.state")
# Per-user query metrics are capped to the busiest N users per window to keep
# Datadog custom-metric cardinality predictable. 0 disables per-user metrics.
TOP_USERS = int(os.environ.get("TOP_USERS", "20"))
# Per-Dive usage metrics (queries, viewers, errors) for the busiest N Dives per
# window, identified from the `md-dives/v1(<dive_id>)` tag in user_agent.
TOP_DIVES = int(os.environ.get("TOP_DIVES", "20"))
# Per-database storage gauges are emitted only for the N largest databases by
# active bytes (org totals are always emitted). Each database costs four
# Datadog custom-metric series; an org with 15k databases would otherwise emit
# 60k series per run. 0 disables per-database storage metrics.
STORAGE_TOP_DATABASES = int(os.environ.get("STORAGE_TOP_DATABASES", "25"))
# Which Flights get run metrics: "own" (the Flight owner's), "scheduled" (every
# scheduled Flight the token can see; org-wide for an admin), "all", or "none".
# Each Flight costs one MD_LIST_FLIGHT_RUNS call (~0.5 s), bounded by
# FLIGHT_RUNS_BUDGET_SEC per run.
FLIGHT_RUN_SCOPE = os.environ.get("FLIGHT_RUN_SCOPE", "own").lower()
FLIGHT_RUNS_BUDGET_SEC = int(os.environ.get("FLIGHT_RUNS_BUDGET_SEC", "60"))
# Print the series instead of posting them. Lets you smoke-test without a key.
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
# ---------------------------------------------------------------------------

COUNT, GAUGE = 1, 3  # Datadog MetricIntakeType
RUN_STARTED = time.monotonic()
RETRY_BUDGET_SEC = 90  # no more retries of a flaky view once the run is this old
MAX_POINT_AGE = dt.timedelta(minutes=55)  # Datadog rejects points older than 1h
BATCH_SIZE = 500  # series per POST; keeps payloads well under the 500 KB cap
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


class Series:
    """Accumulates Datadog series; one entry per (metric, tags) with many points."""

    def __init__(self, prefix: str, base_tags: list[str]) -> None:
        self.prefix = prefix
        self.base_tags = base_tags
        self._series: dict[tuple, dict] = {}

    def add(self, name: str, kind: int, ts: dt.datetime, value, tags: dict | None = None,
            unit: str | None = None) -> None:
        if value is None:
            return
        tag_list = sorted(self.base_tags + [f"{k}:{clean_tag(v)}" for k, v in (tags or {}).items()])
        key = (name, tuple(tag_list))
        entry = self._series.get(key)
        if entry is None:
            entry = {"metric": f"{self.prefix}.{name}", "type": kind, "tags": tag_list, "points": []}
            if kind == COUNT:
                entry["interval"] = 60
            if unit:
                entry["unit"] = unit
            self._series[key] = entry
        entry["points"].append({"timestamp": int(ts.timestamp()), "value": float(value)})

    def payload_batches(self) -> list[dict]:
        series = list(self._series.values())
        for entry in series:
            entry["points"].sort(key=lambda p: p["timestamp"])
        return [{"series": series[i:i + BATCH_SIZE]} for i in range(0, len(series), BATCH_SIZE)]

    def summary(self) -> Counter:
        counts: Counter = Counter()
        for entry in self._series.values():
            counts[entry["metric"]] += len(entry["points"])
        return counts

    def __len__(self) -> int:
        return len(self._series)


def main() -> None:
    started = time.monotonic()
    # Flight logs are captured from a pipe; line-buffer stdout so progress lines
    # carry real timestamps instead of appearing all at once at exit.
    sys.stdout.reconfigure(line_buffering=True)
    api_key = resolve_secret("DD_API_KEY")
    if not api_key and not DRY_RUN:
        raise SystemExit(
            "DD_API_KEY is not set. Attach a Flights secret with a DD_API_KEY param "
            "(or export it locally), or set DRY_RUN=true to print instead of post."
        )

    con = duckdb.connect("md:")
    # The Flight container has no timezone configured, which makes DuckDB
    # report 'Etc/Unknown' and fail to hand TIMESTAMPTZ values to Python.
    # Pin UTC so timestamps round-trip the same locally and in a Flight.
    con.execute("SET TimeZone = 'UTC'")
    base_tags = list(DD_TAGS)
    org = org_name(con)
    if org:
        base_tags.append(f"md_org:{clean_tag(org)}")
    series = Series(METRIC_PREFIX, base_tags)

    if another_run_is_active(con):
        print("A previous run of this Flight is still running; skipping so windows stay contiguous.")
        return

    now = dt.datetime.now(dt.timezone.utc)
    window_start, window_end = resolve_window(con, now)
    if window_start >= window_end:
        print(f"Window is empty ({window_start:%H:%M} >= {window_end:%H:%M}); run again later.")
        return
    print(f"Exporting window [{window_start:%Y-%m-%d %H:%M}, {window_end:%H:%M}) UTC")

    # recent_queries goes last: a failing read of that view can take close to a
    # minute to error out, and the other collectors should not wait behind it.
    collectors = [
        ("query_history", lambda: collect_query_history(con, series, window_start, window_end)),
        ("storage_info", lambda: collect_storage(con, series, window_end)),
        ("inventory", lambda: collect_inventory(con, series, window_end)),
        ("flight_runs", lambda: collect_flight_runs(con, series, window_start, window_end)),
        ("recent_queries", lambda: collect_running_queries(con, series, window_end)),
    ]
    status: dict[str, bool] = {}
    for name, run in collectors:
        t0 = time.monotonic()
        try:
            run()
            status[name] = True
        except Exception as exc:  # one broken source must not hide the others
            status[name] = False
            print(f"WARNING collector {name} failed: {first_line(exc)}", file=sys.stderr)
        elapsed = time.monotonic() - t0
        print(f"  collector {name}: {'ok' if status[name] else 'FAILED'} in {elapsed:.1f}s")
        series.add("exporter.collector_duration_sec", GAUGE, window_end, elapsed, {"collector": name}, unit="second")
    for name, ok in status.items():
        series.add("exporter.collector_ok", GAUGE, window_end, 1 if ok else 0, {"collector": name})

    series.add("exporter.heartbeat", GAUGE, window_end, 1)
    series.add("exporter.duration_sec", GAUGE, window_end, time.monotonic() - started, unit="second")
    series.add("exporter.series_count", GAUGE, window_end, len(series))

    for metric, points in sorted(series.summary().items()):
        print(f"  {metric}: {points} point(s)")

    if DRY_RUN:
        sample = series.payload_batches()[0]["series"][:3] if len(series) else []
        print(f"DRY_RUN: would post {len(series)} series; sample: {json.dumps(sample, indent=1)}")
    else:
        post_to_datadog(api_key, series)
        print(f"Posted {len(series)} series to Datadog ({DD_SITE}).")

    # Advance the watermark only after a successful post so a failed run is
    # retried over the same window next time.
    save_window_end(con, window_end)
    con.close()


# ---- Window / state --------------------------------------------------------

def resolve_window(con: duckdb.DuckDBPyConnection, now: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    window_end = (now - dt.timedelta(seconds=DELAY_SECONDS)).replace(second=0, microsecond=0)
    default_start = window_end - dt.timedelta(minutes=WINDOW_MINUTES)
    last_end = load_window_end(con)
    if last_end is None:
        return default_start, window_end
    # Contiguous with the previous run, but never further back than Datadog accepts.
    floor = window_end - MAX_POINT_AGE
    if last_end < floor:
        print(f"State is {now - last_end} old; catching up from {floor:%H:%M} (Datadog's 1h limit).")
        return floor, window_end
    return last_end, window_end


def state_parts() -> tuple[str, str, str] | None:
    if not STATE_TABLE.strip():
        return None
    parts = STATE_TABLE.strip().split(".")
    if len(parts) != 3 or not all(IDENTIFIER_RE.fullmatch(p) for p in parts):
        raise ValueError(f"STATE_TABLE must be database.schema.table, got {STATE_TABLE!r}")
    return parts[0], parts[1], parts[2]


def load_window_end(con: duckdb.DuckDBPyConnection) -> dt.datetime | None:
    parts = state_parts()
    if parts is None:
        return None
    database, schema, table = parts
    con.execute(f'CREATE DATABASE IF NOT EXISTS "{database}"')
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{database}"."{schema}"')
    con.execute(
        f'CREATE TABLE IF NOT EXISTS "{database}"."{schema}"."{table}" '
        "(exporter VARCHAR PRIMARY KEY, last_window_end TIMESTAMPTZ, updated_at TIMESTAMPTZ)"
    )
    row = con.execute(
        f'SELECT last_window_end FROM "{database}"."{schema}"."{table}" WHERE exporter = ?',
        [METRIC_PREFIX],
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return as_utc(row[0])


def save_window_end(con: duckdb.DuckDBPyConnection, window_end: dt.datetime) -> None:
    parts = state_parts()
    if parts is None:
        return
    database, schema, table = parts
    con.execute(
        f'INSERT OR REPLACE INTO "{database}"."{schema}"."{table}" VALUES (?, ?, now())',
        [METRIC_PREFIX, window_end],
    )


# ---- Collectors ------------------------------------------------------------

def collect_query_history(con, series: Series, start: dt.datetime, end: dt.datetime) -> None:
    """Per-minute aggregates of completed queries, bucketed by end_time."""
    # Bucketing by end_time means a query is counted once, in the minute it
    # finished, with its final latency and error status. Queries still running
    # are covered by collect_running_queries.
    rows = con.execute(
        """
        WITH q AS (
            SELECT date_trunc('minute', end_time) AS minute,
                   coalesce(query_type, 'UNKNOWN') AS query_type,
                   coalesce(instance_type, 'unknown') AS instance_type,
                   error_type,
                   epoch_ms(total_elapsed_time) AS total_ms,
                   epoch_ms(wait_time) AS wait_ms,
                   bytes_spilled_to_disk, bytes_uploaded, bytes_downloaded
            FROM md_information_schema.query_history
            WHERE end_time >= ? AND end_time < ?
        )
        SELECT minute, query_type, instance_type,
               CASE WHEN error_type IS NULL THEN 'ok' ELSE 'error' END AS status,
               count(*) AS n,
               count(*) FILTER (WHERE bytes_spilled_to_disk > 0) AS spilled_n,
               sum(bytes_spilled_to_disk) AS spilled_bytes,
               sum(bytes_uploaded) AS up, sum(bytes_downloaded) AS down,
               max(total_ms) AS max_ms, avg(total_ms) AS avg_ms, max(wait_ms) AS max_wait_ms
        FROM q GROUP BY ALL
        """,
        [start, end],
    ).fetchall()
    for minute, qtype, itype, status, n, spilled_n, spilled_bytes, up, down, max_ms, avg_ms, max_wait in rows:
        ts = as_utc(minute)
        tags = {"query_type": qtype, "instance_type": itype}
        series.add("queries.count", COUNT, ts, n, {**tags, "status": status})
        if status == "ok":
            series.add("queries.latency.max_ms", GAUGE, ts, max_ms, tags, unit="millisecond")
            series.add("queries.latency.avg_ms", GAUGE, ts, avg_ms, tags, unit="millisecond")
        series.add("queries.wait.max_ms", GAUGE, ts, max_wait, tags, unit="millisecond")
        series.add("queries.spilled.count", COUNT, ts, spilled_n, tags)
        series.add("queries.spilled.bytes", COUNT, ts, spilled_bytes, tags, unit="byte")
        series.add("queries.bytes_uploaded", COUNT, ts, up, tags, unit="byte")
        series.add("queries.bytes_downloaded", COUNT, ts, down, tags, unit="byte")

    # Percentiles are only meaningful over the whole population, so they are
    # emitted once per minute without instance/type tags.
    for minute, p50, p95, p99, wait_p95 in con.execute(
        """
        SELECT date_trunc('minute', end_time) AS minute,
               quantile_cont(epoch_ms(total_elapsed_time), 0.50),
               quantile_cont(epoch_ms(total_elapsed_time), 0.95),
               quantile_cont(epoch_ms(total_elapsed_time), 0.99),
               quantile_cont(epoch_ms(wait_time), 0.95)
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? AND error_type IS NULL
        GROUP BY ALL
        """,
        [start, end],
    ).fetchall():
        ts = as_utc(minute)
        series.add("queries.latency.p50_ms", GAUGE, ts, p50, unit="millisecond")
        series.add("queries.latency.p95_ms", GAUGE, ts, p95, unit="millisecond")
        series.add("queries.latency.p99_ms", GAUGE, ts, p99, unit="millisecond")
        series.add("queries.wait.p95_ms", GAUGE, ts, wait_p95, unit="millisecond")

    for minute, error_type, n in con.execute(
        """
        SELECT date_trunc('minute', end_time), error_type, count(*)
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? AND error_type IS NOT NULL
        GROUP BY ALL
        """,
        [start, end],
    ).fetchall():
        series.add("queries.errors", COUNT, as_utc(minute), n, {"error_type": error_type})

    # Who is busy: distinct users and ducklings in the window, plus per-user
    # counts for the busiest TOP_USERS users.
    users, ducklings = con.execute(
        """
        SELECT count(DISTINCT user_name), count(DISTINCT duckling_id)
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ?
        """,
        [start, end],
    ).fetchone()
    series.add("users.active", GAUGE, end, users)
    series.add("ducklings.active", GAUGE, end, ducklings)
    for itype, n in con.execute(
        """
        SELECT coalesce(instance_type, 'unknown'), count(DISTINCT duckling_id)
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? GROUP BY ALL
        """,
        [start, end],
    ).fetchall():
        series.add("ducklings.active_by_type", GAUGE, end, n, {"instance_type": itype})

    if TOP_USERS > 0:
        for minute, user, n, errors, max_ms, spilled in con.execute(
            """
            WITH top AS (
                SELECT user_name FROM md_information_schema.query_history
                WHERE end_time >= ? AND end_time < ?
                GROUP BY 1 ORDER BY count(*) DESC LIMIT ?
            )
            SELECT date_trunc('minute', end_time), user_name, count(*),
                   count(*) FILTER (WHERE error_type IS NOT NULL),
                   max(epoch_ms(total_elapsed_time)), sum(bytes_spilled_to_disk)
            FROM md_information_schema.query_history
            WHERE end_time >= ? AND end_time < ? AND user_name IN (SELECT user_name FROM top)
            GROUP BY ALL
            """,
            [start, end, TOP_USERS, start, end],
        ).fetchall():
            ts, tags = as_utc(minute), {"user_name": user}
            series.add("queries.by_user.count", COUNT, ts, n, tags)
            series.add("queries.by_user.errors", COUNT, ts, errors, tags)
            series.add("queries.by_user.latency.max_ms", GAUGE, ts, max_ms, tags, unit="millisecond")
            series.add("queries.by_user.spilled.bytes", COUNT, ts, spilled, tags, unit="byte")

    # Which clients drive the load: UI, Dives, Flights, MCP, dbt, Python, ...
    for minute, client, status, n in con.execute(
        f"""
        SELECT date_trunc('minute', end_time), {CLIENT_SQL} AS client,
               CASE WHEN error_type IS NULL THEN 'ok' ELSE 'error' END AS status, count(*)
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? GROUP BY ALL
        """,
        [start, end],
    ).fetchall():
        series.add("queries.by_client.count", COUNT, as_utc(minute), n, {"client": client, "status": status})

    # Dive usage: every query a Dive runs is tagged with the Dive id, so query
    # volume, distinct viewers and errors per Dive fall out of query_history.
    active_dives, dive_viewers = con.execute(
        f"""
        SELECT count(DISTINCT {DIVE_ID_SQL}), count(DISTINCT user_name)
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? AND user_agent LIKE '%md-dives/%'
        """,
        [start, end],
    ).fetchone()
    series.add("dives.active", GAUGE, end, active_dives)
    series.add("dives.viewers", GAUGE, end, dive_viewers)
    if TOP_DIVES > 0 and active_dives:
        dive_rows = con.execute(
            f"""
            WITH d AS (
                SELECT date_trunc('minute', end_time) AS minute, {DIVE_ID_SQL} AS dive_id,
                       user_name, error_type, epoch_ms(total_elapsed_time) AS total_ms
                FROM md_information_schema.query_history
                WHERE end_time >= ? AND end_time < ? AND user_agent LIKE '%md-dives/%'
            ), top AS (SELECT dive_id FROM d GROUP BY 1 ORDER BY count(*) DESC LIMIT ?)
            SELECT minute, dive_id, count(*), count(DISTINCT user_name),
                   count(*) FILTER (WHERE error_type IS NOT NULL), max(total_ms)
            FROM d WHERE dive_id IN (SELECT dive_id FROM top) GROUP BY ALL
            """,
            [start, end, TOP_DIVES],
        ).fetchall()
        titles = dive_titles(con)
        for minute, dive_id, n, viewers, errors, max_ms in dive_rows:
            ts = as_utc(minute)
            tags = {"dive_id": dive_id, "dive_title": titles.get(dive_id, "unknown")}
            series.add("dives.queries", COUNT, ts, n, tags)
            series.add("dives.query_errors", COUNT, ts, errors, tags)
            series.add("dives.viewers_by_dive", GAUGE, ts, viewers, tags)
            series.add("dives.latency.max_ms", GAUGE, ts, max_ms, tags, unit="millisecond")

    # Active users over 1/7/30 days. MotherDuck has no SQL or API that lists an
    # organization's members, so "users who ran a query" is the usable proxy.
    dau, wau, mau = con.execute(
        """
        SELECT count(DISTINCT user_name) FILTER (WHERE start_time >= ? - INTERVAL 24 HOUR),
               count(DISTINCT user_name) FILTER (WHERE start_time >= ? - INTERVAL 7 DAY),
               count(DISTINCT user_name)
        FROM md_information_schema.query_history
        WHERE start_time >= ? - INTERVAL 30 DAY
        """,
        [end, end, end],
    ).fetchone()
    series.add("users.active_24h", GAUGE, end, dau)
    series.add("users.active_7d", GAUGE, end, wau)
    series.add("users.active_30d", GAUGE, end, mau)

    # Log the slowest queries so a run's logs double as a quick triage view.
    slow = con.execute(
        """
        SELECT user_name, epoch_ms(total_elapsed_time), instance_type, left(regexp_replace(query_text, '\\s+', ' ', 'g'), 120)
        FROM md_information_schema.query_history
        WHERE end_time >= ? AND end_time < ? ORDER BY total_elapsed_time DESC LIMIT 5
        """,
        [start, end],
    ).fetchall()
    for user, ms, itype, text in slow:
        print(f"  slow: {ms:>9} ms  {itype:<9} {user:<24} {text}")


def collect_running_queries(con, series: Series, ts: dt.datetime) -> None:
    """Point-in-time gauges from recent_queries (updated every few seconds)."""
    # recent_queries is a live view; keep the SQL to plain aggregates. A
    # `WHERE end_time IS NULL` predicate or an unbounded row scan of it can
    # fail server-side, so "running" is derived as count(*) - count(end_time).
    # The view also fails intermittently with an internal error, so each read
    # is retried a few times before the collector gives up for this run.
    running, users, oldest = with_retry(lambda: con.execute(
        """
        SELECT count(*) - count(end_time),
               count(DISTINCT CASE WHEN end_time IS NULL THEN user_name END),
               min(start_time) FILTER (WHERE end_time IS NULL)
        FROM md_information_schema.recent_queries
        """
    ).fetchone(), label="recent_queries")
    series.add("queries.running", GAUGE, ts, running)
    series.add("queries.running.users", GAUGE, ts, users)
    age = (dt.datetime.now(dt.timezone.utc) - as_utc(oldest)).total_seconds() if oldest else 0.0
    series.add("queries.running.oldest_age_sec", GAUGE, ts, age, unit="second")
    for qtype, itype, n in with_retry(lambda: con.execute(
        """
        SELECT coalesce(query_type, 'UNKNOWN'), coalesce(instance_type, 'unknown'),
               count(*) - count(end_time)
        FROM md_information_schema.recent_queries GROUP BY ALL
        """
    ).fetchall(), label="recent_queries by type"):
        if n > 0:
            series.add("queries.running_by_type", GAUGE, ts, n, {"query_type": qtype, "instance_type": itype})


def collect_storage(con, series: Series, ts: dt.datetime) -> None:
    """Storage per database from storage_info (org admins only)."""
    rows = con.execute(
        """
        SELECT database_name, user_name, transient,
               active_bytes, historical_bytes, retained_for_clone_bytes, failsafe_bytes
        FROM md_information_schema.storage_info
        WHERE deleted_ts IS NULL
        """
    ).fetchall()
    kinds = ("active", "historical", "retained_for_clone", "failsafe")
    totals = defaultdict(float)
    owners: set[str] = set()
    for name, owner, transient, *bytes_by_kind in rows:
        owners.add(owner)
        for kind, value in zip(kinds, bytes_by_kind):
            totals[kind] += value or 0
    # Per-database series only for the largest databases, to bound cardinality.
    largest = sorted(rows, key=lambda r: r[3] or 0, reverse=True)[:max(STORAGE_TOP_DATABASES, 0)]
    for name, owner, transient, *bytes_by_kind in largest:
        for kind, value in zip(kinds, bytes_by_kind):
            series.add("storage.bytes", GAUGE, ts, value, {
                "database_name": name, "owner": owner, "kind": kind,
                "transient": str(bool(transient)).lower(),
            }, unit="byte")
    for kind in kinds:
        series.add("storage.total_bytes", GAUGE, ts, totals[kind], {"kind": kind}, unit="byte")
    series.add("storage.total_bytes", GAUGE, ts, sum(totals.values()), {"kind": "all"}, unit="byte")
    series.add("storage.databases", GAUGE, ts, len(rows))
    series.add("storage.owners", GAUGE, ts, len(owners))


def collect_inventory(con, series: Series, ts: dt.datetime) -> None:
    """Counts of the things an org accumulates: databases, tokens, Flights, Dives, Guides, roles."""
    for db_type, n in con.execute(
        "SELECT coalesce(type, 'unknown'), count(*) FROM md_information_schema.databases GROUP BY 1"
    ).fetchall():
        series.add("databases.count", GAUGE, ts, n, {"database_type": db_type})

    for token_type, n, expiring in con.execute(
        """
        SELECT coalesce(token_type, 'unknown'), count(*),
               count(*) FILTER (WHERE expire_at IS NOT NULL AND expire_at < now() + INTERVAL 7 DAY)
        FROM md_access_tokens() GROUP BY 1
        """
    ).fetchall():
        series.add("access_tokens.count", GAUGE, ts, n, {"token_type": token_type})
        series.add("access_tokens.expiring_7d", GAUGE, ts, expiring, {"token_type": token_type})

    for status, sched, n in con.execute(
        "SELECT coalesce(status, 'unknown'), coalesce(schedule_status, 'none'), count(*) "
        "FROM MD_LIST_FLIGHTS() GROUP BY ALL"
    ).fetchall():
        series.add("flights.count", GAUGE, ts, n, {"status": status, "schedule_status": sched})

    # Dives: the owner's plus every Dive shared with the organization.
    for status, n in con.execute(
        "SELECT coalesce(status, 'unknown'), count(*) "
        "FROM MD_LIST_DIVES(include_org_shares := true) GROUP BY 1"
    ).fetchall():
        series.add("dives.count", GAUGE, ts, n, {"status": status})
    owners, created_7d, updated_7d = con.execute(
        """
        SELECT count(DISTINCT owner_name),
               count(*) FILTER (WHERE created_at > now() - INTERVAL 7 DAY),
               count(*) FILTER (WHERE updated_at > now() - INTERVAL 7 DAY)
        FROM MD_LIST_DIVES(include_org_shares := true)
        """
    ).fetchone()
    series.add("dives.owners", GAUGE, ts, owners)
    series.add("dives.created_7d", GAUGE, ts, created_7d)
    series.add("dives.updated_7d", GAUGE, ts, updated_7d)

    # Guides: by access level and by top-level topic folder.
    for access, n in con.execute(
        "SELECT coalesce(access, 'unknown'), count(*) FROM MD_LIST_GUIDES() GROUP BY 1"
    ).fetchall():
        series.add("guides.count", GAUGE, ts, n, {"access": access})
    for topic, n in con.execute(
        "SELECT coalesce(split_part(topic, '/', 1), 'none'), count(*) FROM MD_LIST_GUIDES() GROUP BY 1"
    ).fetchall():
        series.add("guides.by_topic", GAUGE, ts, n, {"topic": topic or "none"})
    owners, created_7d, updated_7d = con.execute(
        """
        SELECT count(DISTINCT owner_name),
               count(*) FILTER (WHERE created_at > now() - INTERVAL 7 DAY),
               count(*) FILTER (WHERE updated_at > now() - INTERVAL 7 DAY)
        FROM MD_LIST_GUIDES()
        """
    ).fetchone()
    series.add("guides.owners", GAUGE, ts, owners)
    series.add("guides.created_7d", GAUGE, ts, created_7d)
    series.add("guides.updated_7d", GAUGE, ts, updated_7d)

    for role_type, n in con.execute(
        "SELECT coalesce(role_type, 'unknown'), count(*) FROM md_information_schema.roles GROUP BY 1"
    ).fetchall():
        series.add("roles.count", GAUGE, ts, n, {"role_type": role_type})


def collect_flight_runs(con, series: Series, start: dt.datetime, end: dt.datetime) -> None:
    """Runs that finished in the window, plus currently running/pending runs, per Flight."""
    if FLIGHT_RUN_SCOPE == "none":
        return
    if FLIGHT_RUN_SCOPE == "own":
        flights = con.execute(
            "SELECT flight_id, flight_name FROM MD_LIST_FLIGHTS(owner_only := true)").fetchall()
    elif FLIGHT_RUN_SCOPE == "scheduled":
        flights = con.execute(
            "SELECT flight_id, flight_name FROM MD_LIST_FLIGHTS() WHERE schedule_cron IS NOT NULL").fetchall()
    elif FLIGHT_RUN_SCOPE == "all":
        flights = con.execute("SELECT flight_id, flight_name FROM MD_LIST_FLIGHTS()").fetchall()
    else:
        raise ValueError(f"FLIGHT_RUN_SCOPE must be own, scheduled, all or none, got {FLIGHT_RUN_SCOPE!r}")
    # An org admin sees every Flight in the organization, which can be hundreds;
    # stop at the time budget rather than let this collector eat the interval.
    t0, tracked = time.monotonic(), 0
    for flight_id, flight_name in flights:
        if time.monotonic() - t0 > FLIGHT_RUNS_BUDGET_SEC:
            print(f"WARNING flight_runs: budget of {FLIGHT_RUNS_BUDGET_SEC}s spent after {tracked}/{len(flights)} "
                  "Flights; narrow FLIGHT_RUN_SCOPE or raise FLIGHT_RUNS_BUDGET_SEC", file=sys.stderr)
            break
        tracked += 1
        runs = con.execute(
            'SELECT status, started_at, ended_at FROM MD_LIST_FLIGHT_RUNS(flight_id := ?::UUID, "limit" := 20)',
            [str(flight_id)],
        ).fetchall()
        in_flight = 0
        for status, started_at, ended_at in runs:
            status = (status or "unknown").lower()
            if status in ("pending", "running"):
                in_flight += 1
                continue
            if ended_at is None:
                continue
            ended = as_utc(ended_at)
            if not (start <= ended < end):
                continue
            tags = {"flight_name": flight_name, "status": status}
            series.add("flights.runs", COUNT, ended.replace(second=0, microsecond=0), 1, tags)
            if started_at is not None:
                duration = (ended - as_utc(started_at)).total_seconds()
                series.add("flights.run_duration_sec", GAUGE, ended, duration,
                           {"flight_name": flight_name}, unit="second")
        series.add("flights.runs_in_flight", GAUGE, end, in_flight, {"flight_name": flight_name})
    series.add("flights.tracked", GAUGE, end, tracked)


# ---- Datadog ---------------------------------------------------------------

def post_to_datadog(api_key: str, series: Series) -> None:
    url = f"https://api.{DD_SITE}/api/v2/series"
    headers = {"DD-API-KEY": api_key, "Content-Type": "application/json", "Content-Encoding": "gzip"}
    with httpx.Client(timeout=30) as client:
        for batch in series.payload_batches():
            body = gzip.compress(json.dumps(batch).encode())
            for attempt in range(4):
                try:
                    response = client.post(url, content=body, headers=headers)
                except httpx.HTTPError as exc:
                    if attempt == 3:
                        raise
                    print(f"WARNING Datadog request failed ({exc}); retrying", file=sys.stderr)
                    time.sleep(2 ** attempt)
                    continue
                if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                if response.status_code >= 400:
                    # 403 means a bad or wrong-site key; surface the body, it is
                    # short and never echoes the key.
                    raise RuntimeError(f"Datadog rejected the batch: {response.status_code} {response.text[:300]}")
                break


# ---- Helpers ---------------------------------------------------------------

def resolve_secret(key: str) -> str:
    """Read a secret from env: bare `KEY`, or the namespaced `<secret>_KEY` a Flights secret injects."""
    if os.environ.get(key):
        return os.environ[key].strip()
    for name, value in os.environ.items():
        if name.endswith(f"_{key}") and value:
            return value.strip()
    return ""


def org_name(con) -> str:
    try:
        (name,) = con.execute("SELECT org_name FROM md_user_info()").fetchone()
        return name or ""
    except duckdb.Error:
        # Disabled in SaaS mode; the org tag is then simply omitted.
        return ""


def clean_tag(value) -> str:
    # Datadog tag values: lowercase, alphanumerics plus _-:./, max 200 chars.
    text = str(value).strip().lower()
    text = re.sub(r"[^a-z0-9_\-:./]", "_", text)
    return text[:200] or "unknown"


def as_utc(value) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def dive_titles(con) -> dict[str, str]:
    """Map Dive id -> title for every Dive the token can see, for tagging usage metrics."""
    try:
        rows = con.execute("SELECT id, title FROM MD_LIST_DIVES(include_org_shares := true)").fetchall()
    except duckdb.Error as exc:
        print(f"WARNING could not list Dives for titles: {first_line(exc)}", file=sys.stderr)
        return {}
    return {str(dive_id).lower(): (title or "untitled") for dive_id, title in rows}


def another_run_is_active(con) -> bool:
    """True when another run of this same Flight is still RUNNING (Flight runtime only)."""
    # The watermark is read at the start of a run and written at the end, so two
    # overlapping runs would export the same window twice. Only skip when this
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
