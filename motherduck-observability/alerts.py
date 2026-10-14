"""Evaluate alert rules over the MotherDuck observability tables and notify Slack.

A single-file MotherDuck Flight, scheduled a couple of minutes after the
collector (`flight.py`). Each run:

1. evaluates every rule in `CHECKS` with one SQL query against the metrics
   tables in `TARGET_DATABASE`; a rule returns one row per group (for example
   one per Flight, or per instance type) with a numeric `value`;
2. classifies each (rule, group) as `ok`, `warning` or `critical` from the
   rule's thresholds, and compares that with the status stored in
   `alert_state` from the previous run;
3. posts one Slack message (Incoming Webhook) covering every transition into
   `warning`/`critical`, every recovery back to `ok` (if `NOTIFY_ON_RESOLVE`),
   and every alert that is still firing past its `renotify_minutes`. A firing
   alert carries up to `DETAIL_ROWS` example queries (query id, duckling id,
   user, the offending value, the query text) each linked to that query in the
   observability Dive, plus a link to the Dive view for the rule. A recovery
   says how long the episode lasted, the value now, and the same examples;
4. writes the new state to `alert_state` and one row per transition or
   notification to `alert_events`, the ledger the Dive's Alerts tab reads.

The rules mirror the nine monitors of the Datadog version of this recipe, as
SQL over the typed tables instead of metric queries. Adapt them by editing the
`CHECKS` list: a rule is a name, a title, a SQL query returning
`(group_key, value)`, warning/critical thresholds, a runbook line, and
optionally a `details` query (example queries to show) and a `dive` state (the
Dive view to link).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import re
import sys

import duckdb
import httpx

# ---- Configuration (env vars; set them as Flight `config`) -----------------
# Database and schema the collector Flight writes to.
TARGET_DATABASE = os.environ.get("TARGET_DATABASE", "md_observability")
TARGET_SCHEMA = os.environ.get("TARGET_SCHEMA", "main")
# Lowest status that triggers a Slack message: "warning" or "critical".
ALERT_LEVEL = os.environ.get("ALERT_LEVEL", "warning").lower()
# Post a recovery message when an alert goes back to ok.
NOTIFY_ON_RESOLVE = os.environ.get("NOTIFY_ON_RESOLVE", "true").lower() == "true"
# Link added to every message (the observability Dive, usually).
DIVE_URL = os.environ.get("DIVE_URL", "")
# Label added to every message, e.g. the organization or environment.
ALERT_SOURCE = os.environ.get("ALERT_SOURCE", "MotherDuck")
# Example queries attached to a firing alert (0 disables them): query id,
# duckling id, user, the offending value and a link to the query in the Dive.
DETAIL_ROWS = int(os.environ.get("DETAIL_ROWS", "3"))
# Evaluate and print, but do not post to Slack or write state.
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
# ---------------------------------------------------------------------------

# ---- Alert rules ------------------------------------------------------------
# Each rule's `sql` runs against the metrics tables; `{t}` expands to the
# qualified schema prefix (e.g. "md_observability"."main"). It must return
# (group_key VARCHAR, value DOUBLE). Return no rows (or NULL value) when there
# is nothing to report; a group that stops appearing is resolved.
# `op` is ">" or "<"; the value is compared against `critical` first, then
# `warning`. `renotify_minutes` re-posts an alert that keeps firing; 0 posts it
# once per episode.
# `details` (optional) is a query returning (query_id, duckling_id, user_name,
# detail VARCHAR, query_text) for the example queries attached to the Slack
# message; `{group}` expands to the firing group, `{n}` to DETAIL_ROWS and
# `{warning}`/`{critical}` to the thresholds.
# `dive` (optional) is the useDiveState bag the "open in the Dive" link opens
# with: the tab, and filters such as {"instance": "{group}"}.
CHECKS: list[dict] = [
    {
        "name": "query_error_rate",
        "title": "Query error rate above threshold",
        "sql": """
            SELECT 'all' AS group_key,
                   100.0 * sum(queries) FILTER (WHERE status = 'error') / nullif(sum(queries), 0) AS value
            FROM {t}."query_minutes" WHERE minute >= now() - INTERVAL 15 MINUTE""",
        "op": ">", "warning": 2.0, "critical": 5.0, "unit": "%", "renotify_minutes": 60,
        "runbook": "Check the Errors tab for the error types and users affected, then "
                   "`SELECT error_type, error_message, user_name, query_text FROM md_information_schema.query_history "
                   "WHERE error_type IS NOT NULL ORDER BY start_time DESC LIMIT 50`.",
        "dive": {"tab": "log", "filters": {"status": "error"}},
        "details": """
            SELECT query_id::VARCHAR, duckling_id, user_name,
                   error_type || ': ' || left(regexp_replace(coalesce(error_message, ''), '\\s+', ' ', 'g'), 80) AS detail,
                   query_text
            FROM md_information_schema.query_history
            WHERE error_type IS NOT NULL AND end_time >= now() - INTERVAL 15 MINUTE
            ORDER BY end_time DESC LIMIT {n}""",
    },
    {
        "name": "p95_latency",
        "title": "p95 query latency above threshold (15 min average)",
        "sql": """
            SELECT 'all' AS group_key, avg(p95_ms) AS value
            FROM {t}."query_latency_minutes" WHERE minute >= now() - INTERVAL 15 MINUTE""",
        "op": ">", "warning": 10_000.0, "critical": 30_000.0, "unit": "ms", "renotify_minutes": 60,
        "runbook": "Look at the slowest queries per instance type and per user on the Queries tab; a single "
                   "Mega/Giga duckling saturated by one user is the usual cause.",
        "dive": {"tab": "log", "logsort": "slowest"},
        "details": """
            SELECT query_id::VARCHAR, duckling_id, user_name,
                   format('{:.1f} s on {}', epoch_ms(total_elapsed_time) / 1000.0, coalesce(instance_type, 'unknown')) AS detail,
                   query_text
            FROM md_information_schema.query_history
            WHERE end_time >= now() - INTERVAL 15 MINUTE
            ORDER BY total_elapsed_time DESC LIMIT {n}""",
    },
    {
        "name": "long_running_query",
        "title": "A query has been running for too long",
        "sql": """
            SELECT 'all' AS group_key, oldest_age_sec AS value
            FROM {t}."running_queries" ORDER BY captured_at DESC LIMIT 1""",
        "op": ">", "warning": 900.0, "critical": 1800.0, "unit": "s", "renotify_minutes": 60,
        "runbook": "Find it with `SELECT user_name, start_time, query_text FROM md_information_schema.recent_queries` "
                   "(rows with a NULL end_time are still running). The Dive link below shows the query once it finishes.",
        "dive": {"tab": "users"},
        # The value is the collector's snapshot, up to five minutes old, so the
        # query may have finished by now: show it either way.
        "details": """
            SELECT query_id::VARCHAR, duckling_id, user_name, detail, query_text FROM (
                SELECT query_id, duckling_id, user_name, start_time, query_text,
                       format('running for {:.0f} s on {}', epoch(now() - start_time), coalesce(instance_type, 'unknown')) AS detail
                FROM md_information_schema.recent_queries
                WHERE end_time IS NULL AND epoch(now() - start_time) > {warning}
                UNION ALL
                SELECT query_id, duckling_id, user_name, start_time, query_text,
                       format('ran for {:.0f} s on {}, finished {}', epoch_ms(total_elapsed_time) / 1000.0,
                              coalesce(instance_type, 'unknown'), strftime(end_time, '%H:%M UTC')) AS detail
                FROM md_information_schema.query_history
                WHERE end_time >= now() - INTERVAL 15 MINUTE AND epoch_ms(total_elapsed_time) / 1000.0 > {warning}
            ) ORDER BY start_time LIMIT {n}""",
    },
    {
        "name": "spill_to_disk",
        "title": "Heavy spill to disk (30 min, per instance type)",
        "sql": """
            SELECT instance_type AS group_key, sum(spilled_bytes) / 1e9 AS value
            FROM {t}."query_minutes" WHERE minute >= now() - INTERVAL 30 MINUTE
            GROUP BY 1 HAVING sum(spilled_bytes) > 0""",
        "op": ">", "warning": 25.0, "critical": 100.0, "unit": "GB", "renotify_minutes": 0,
        "runbook": "Spilling means the working set no longer fits in memory: consider a larger instance size, "
                   "narrower projections, or splitting the job. See top users by bytes spilled on the Users tab.",
        "dive": {"tab": "log", "logsort": "spilled", "filters": {"instance": "{group}"}},
        "details": """
            SELECT query_id::VARCHAR, duckling_id, user_name,
                   format('{:.1f} GB spilled', bytes_spilled_to_disk / 1e9) AS detail, query_text
            FROM md_information_schema.query_history
            WHERE coalesce(instance_type, 'unknown') = '{group}' AND bytes_spilled_to_disk > 0
              AND end_time >= now() - INTERVAL 30 MINUTE
            ORDER BY bytes_spilled_to_disk DESC LIMIT {n}""",
    },
    {
        "name": "flight_run_failed",
        "title": "Flight run failed (last 30 min)",
        "sql": """
            SELECT flight_name AS group_key, count(*)::DOUBLE AS value
            FROM {t}."flight_runs" WHERE status = 'failed' AND ended_at >= now() - INTERVAL 30 MINUTE
            GROUP BY 1""",
        "op": ">", "warning": None, "critical": 0.0, "unit": "runs", "renotify_minutes": 0,
        "runbook": "Read its logs in the MotherDuck UI (Flights > the Flight) or with `MD_GET_FLIGHT_LOGS`.",
        "dive": {"tab": "flights"},
    },
    {
        "name": "token_expiring",
        "title": "Access token expires within 7 days",
        "sql": """
            SELECT label AS group_key, count::DOUBLE AS value
            FROM {t}."inventory_counts"
            WHERE entity = 'access_token' AND dimension = 'expiring_7d' AND count > 0
              AND captured_at = (SELECT max(captured_at) FROM {t}."inventory_counts")""",
        "op": ">", "warning": None, "critical": 0.0, "unit": "tokens", "renotify_minutes": 1440,
        "runbook": "Rotate them before the services using them lose access: "
                   "`SELECT token_name, expire_at FROM md_access_tokens() WHERE expire_at < now() + INTERVAL 7 DAY`.",
        "dive": {"tab": "inventory"},
    },
    {
        "name": "storage_growth",
        "title": "Active storage grew too fast in a day",
        "sql": """
            WITH latest AS (SELECT active_bytes FROM {t}."storage_totals" ORDER BY captured_at DESC LIMIT 1),
                 before AS (SELECT active_bytes FROM {t}."storage_totals"
                            WHERE captured_at <= now() - INTERVAL 1 DAY ORDER BY captured_at DESC LIMIT 1)
            SELECT 'all' AS group_key,
                   100.0 * (latest.active_bytes - before.active_bytes) / nullif(before.active_bytes, 0) AS value
            FROM latest, before""",
        "op": ">", "warning": 10.0, "critical": 25.0, "unit": "%", "renotify_minutes": 0,
        "runbook": "See the largest databases on the Storage tab to find the database responsible.",
        "dive": {"tab": "storage"},
    },
    {
        "name": "collector_not_reporting",
        "title": "Collector Flight is not reporting",
        "sql": """
            SELECT 'all' AS group_key,
                   coalesce(date_diff('minute', max(run_at), now()), 1e9) AS value
            FROM {t}."exporter_runs"
            """,
        "op": ">", "warning": None, "critical": 15.0, "unit": "min since last run", "renotify_minutes": 60,
        "runbook": "Every other rule is blind. Check the collector Flight in MotherDuck: is its schedule active, "
                   "did the last run fail?",
        "dive": {"tab": "health"},
    },
    {
        "name": "collector_failing",
        "title": "A collector has failed on every run for 15 minutes",
        "sql": """
            SELECT collector AS group_key, count(*)::DOUBLE AS value
            FROM {t}."collector_runs" WHERE run_at >= now() - INTERVAL 15 MINUTE
            GROUP BY 1 HAVING bool_and(NOT ok)""",
        "op": ">", "warning": None, "critical": 0.0, "unit": "failed runs", "renotify_minutes": 240,
        "runbook": "`storage_info` failing usually means the Flight's token is not an organization admin. "
                   "Otherwise read the collector_runs.error column or the Flight run logs.",
        "dive": {"tab": "health"},
    },
]
# ---------------------------------------------------------------------------

STATUS_RANK = {"ok": 0, "warning": 1, "critical": 2}
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
EMOJI = {"ok": ":large_green_circle:", "warning": ":large_yellow_circle:", "critical": ":red_circle:"}


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)
    validate_config()
    con = duckdb.connect("md:")
    con.execute("SET TimeZone = 'UTC'")
    ensure_tables(con)
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    previous = load_state(con)
    results = evaluate_all(con)
    decisions = decide(results, previous, now)
    for d in decisions:
        if d["notify"]:
            d["details"] = fetch_details(con, d["check"], d["group"])

    for d in decisions:
        print(format_line(d))
        for row in d.get("details", []):
            print("         " + detail_line(row))
    to_notify = [d for d in decisions if d["notify"]]
    print(f"{len(results)} result(s), {len(decisions)} decision(s), {len(to_notify)} to notify.")

    if DRY_RUN:
        print("DRY_RUN: not posting to Slack, not writing state.")
        con.close()
        return

    # Notify before persisting: if Slack fails the run is FAILED and the next
    # run re-evaluates with the old state, so the alert is not lost.
    webhook = resolve_secret("SLACK_WEBHOOK_URL")
    if to_notify:
        if webhook:
            still_firing = sum(1 for d in decisions if d["new_status"] != "ok" and not d["notify"])
            post_slack(webhook, to_notify, now, still_firing)
            print(f"Posted {len(to_notify)} alert(s) to Slack.")
        else:
            print(f"{len(to_notify)} alert(s) to notify but no SLACK_WEBHOOK_URL in the environment; "
                  "attach a Flights secret named `slack` with that param.", file=sys.stderr)
    write_state(con, decisions, now, notified=bool(webhook))
    con.close()


# ---- Evaluation --------------------------------------------------------------

def evaluate_all(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """Run every rule; a rule that errors is reported as `error` rather than aborting the run."""
    results = []
    for check in CHECKS:
        sql = check["sql"].replace("{t}", prefix())
        try:
            rows = con.execute(sql).fetchall()
        except duckdb.Error as exc:
            print(f"WARNING rule {check['name']} failed: {first_line(exc)}", file=sys.stderr)
            results.append({"check": check, "group": "all", "value": None, "status": "error",
                            "error": first_line(exc)})
            continue
        for group_key, value in rows:
            if value is None:
                continue
            results.append({"check": check, "group": str(group_key or "all"), "value": float(value),
                            "status": classify(check, float(value)), "error": ""})
    return results


def fetch_details(con: duckdb.DuckDBPyConnection, check: dict, group: str) -> list[dict]:
    """Example queries behind a firing alert, from the live metadata views. A failure is logged, not fatal."""
    sql = check.get("details")
    if not sql or DETAIL_ROWS <= 0:
        return []
    sql = sql.replace("{t}", prefix()).replace("{group}", group.replace("'", "''")).replace("{n}", str(DETAIL_ROWS))
    for key in ("warning", "critical"):
        if check.get(key) is not None:
            sql = sql.replace("{" + key + "}", repr(float(check[key])))
    try:
        rows = con.execute(sql).fetchall()
    except duckdb.Error as exc:
        print(f"WARNING details for {check['name']}[{group}] failed: {first_line(exc)}", file=sys.stderr)
        return []
    return [{"query_id": str(query_id or ""), "duckling_id": str(duckling or "-"), "user_name": str(user or "-"),
             "detail": str(detail or ""), "query_text": squash(text)}
            for query_id, duckling, user, detail, text in rows]


def classify(check: dict, value: float) -> str:
    def beyond(threshold) -> bool:
        if threshold is None:
            return False
        return value > threshold if check["op"] == ">" else value < threshold
    if beyond(check.get("critical")):
        return "critical"
    if beyond(check.get("warning")):
        return "warning"
    return "ok"


def decide(results: list[dict], previous: dict[tuple[str, str], dict], now: dt.datetime) -> list[dict]:
    """Compare each (rule, group) with its stored state and decide what to record and notify."""
    by_check = {c["name"]: c for c in CHECKS}
    decisions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    min_rank = STATUS_RANK[ALERT_LEVEL]

    for r in results:
        if r["status"] == "error":
            continue  # a broken rule is logged; it neither fires nor resolves anything
        key = (r["check"]["name"], r["group"])
        seen.add(key)
        prev = previous.get(key)
        prev_status = prev["status"] if prev else "ok"
        new_status = r["status"]
        notify, kind = False, "steady"
        if new_status != prev_status:
            kind = "resolved" if new_status == "ok" else ("escalated" if STATUS_RANK[new_status] > STATUS_RANK[prev_status] else "downgraded")
            if new_status == "ok":
                notify = NOTIFY_ON_RESOLVE and STATUS_RANK[prev_status] >= min_rank
            else:
                notify = STATUS_RANK[new_status] >= min_rank
        elif new_status != "ok":
            renotify = int(r["check"].get("renotify_minutes") or 0)
            last = prev.get("last_notified_at") if prev else None
            if renotify > 0 and STATUS_RANK[new_status] >= min_rank and (
                    last is None or now - as_utc(last) >= dt.timedelta(minutes=renotify)):
                notify, kind = True, "renotify"
        decisions.append({
            "check": r["check"], "group": r["group"], "value": r["value"],
            "old_status": prev_status, "new_status": new_status, "kind": kind, "notify": notify,
            "since": as_utc(prev["since"]) if prev and prev_status == new_status else now,
            "last_notified_at": as_utc(prev["last_notified_at"]) if prev and prev.get("last_notified_at") else None,
            "episode_start": as_utc(prev["since"]) if kind == "resolved" and prev and prev.get("since") else None,
        })

    # A (rule, group) that was alerting and no longer appears in the results
    # (for example a Flight with no failed run in the last 30 minutes) is resolved.
    for key, prev in previous.items():
        if key in seen or prev["status"] == "ok":
            continue
        check = by_check.get(key[0])
        if check is None:
            continue  # rule removed from CHECKS; its state row is dropped below
        decisions.append({
            "check": check, "group": key[1], "value": None, "old_status": prev["status"], "new_status": "ok",
            "kind": "resolved", "notify": NOTIFY_ON_RESOLVE and STATUS_RANK[prev["status"]] >= min_rank,
            "since": now, "last_notified_at": None,
            "episode_start": as_utc(prev["since"]) if prev.get("since") else None,
        })
    return decisions


# ---- Persistence -------------------------------------------------------------

def prefix() -> str:
    return f'"{TARGET_DATABASE}"."{TARGET_SCHEMA}"'


def validate_config() -> None:
    for name, value in (("TARGET_DATABASE", TARGET_DATABASE), ("TARGET_SCHEMA", TARGET_SCHEMA)):
        if not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"{name} must be a plain SQL identifier, got {value!r}")
    if ALERT_LEVEL not in ("warning", "critical"):
        raise ValueError(f"ALERT_LEVEL must be warning or critical, got {ALERT_LEVEL!r}")
    names = [c["name"] for c in CHECKS]
    if len(set(names)) != len(names):
        raise ValueError("CHECKS names must be unique")


def ensure_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f'CREATE DATABASE IF NOT EXISTS "{TARGET_DATABASE}"')
    con.execute(f'CREATE SCHEMA IF NOT EXISTS {prefix()}')
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {prefix()}."alert_state" (
            check_name VARCHAR, group_key VARCHAR, status VARCHAR, value DOUBLE,
            since TIMESTAMPTZ, last_notified_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
            PRIMARY KEY (check_name, group_key))""")
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {prefix()}."alert_events" (
            evaluated_at TIMESTAMPTZ, check_name VARCHAR, title VARCHAR, group_key VARCHAR,
            old_status VARCHAR, new_status VARCHAR, kind VARCHAR, value DOUBLE, unit VARCHAR,
            warning DOUBLE, critical DOUBLE, notified BOOLEAN, message VARCHAR)""")


def load_state(con: duckdb.DuckDBPyConnection) -> dict[tuple[str, str], dict]:
    rows = con.execute(
        f'SELECT check_name, group_key, status, value, since, last_notified_at FROM {prefix()}."alert_state"'
    ).fetchall()
    return {(name, group): {"status": status, "value": value, "since": since, "last_notified_at": last}
            for name, group, status, value, since, last in rows}


def write_state(con: duckdb.DuckDBPyConnection, decisions: list[dict], now: dt.datetime, notified: bool) -> None:
    valid = {c["name"] for c in CHECKS}
    con.execute(f'DELETE FROM {prefix()}."alert_state" WHERE check_name NOT IN (SELECT unnest(?::VARCHAR[]))',
                [sorted(valid)])
    state_rows, event_rows = [], []
    for d in decisions:
        last = now if (d["notify"] and notified) else d["last_notified_at"]
        state_rows.append((d["check"]["name"], d["group"], d["new_status"], d["value"], d["since"], last, now))
        if d["kind"] != "steady":
            c = d["check"]
            message = "\n".join([format_line(d)] + [detail_line(r) for r in d.get("details", [])])
            event_rows.append((now, c["name"], c["title"], d["group"], d["old_status"], d["new_status"], d["kind"],
                               d["value"], c["unit"], c.get("warning"), c.get("critical"),
                               d["notify"] and notified, message))
    insert_rows(con, "alert_state",
                ["check_name", "group_key", "status", "value", "since", "last_notified_at", "updated_at"],
                state_rows, replace=True)
    insert_rows(con, "alert_events",
                ["evaluated_at", "check_name", "title", "group_key", "old_status", "new_status", "kind",
                 "value", "unit", "warning", "critical", "notified", "message"], event_rows)


def insert_rows(con, table: str, columns: list[str], rows: list[tuple], replace: bool = False) -> None:
    if not rows:
        return
    verb = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
    placeholders = "(" + ", ".join("?" * len(columns)) + ")"
    chunk = max(1, 400 // len(columns))
    for i in range(0, len(rows), chunk):
        batch = rows[i:i + chunk]
        con.execute(f'{verb} {prefix()}."{table}" ({", ".join(columns)}) VALUES '
                    + ", ".join([placeholders] * len(batch)), [v for r in batch for v in r])


# ---- Slack -------------------------------------------------------------------

def post_slack(webhook: str, decisions: list[dict], now: dt.datetime, still_firing: int = 0) -> None:
    worst = max((d["new_status"] for d in decisions), key=lambda s: STATUS_RANK[s])
    blocks: list[dict] = [{"type": "header", "text": {"type": "plain_text", "text": headline(decisions, still_firing), "emoji": True}}]
    for d in decisions:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": slack_text(d, now)[:3000]}})
    context = f"Evaluated {now:%Y-%m-%d %H:%M} UTC · worst status: {worst}"
    if DIVE_URL:
        context += f" · <{DIVE_URL}|Open the observability Dive>"
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": context}]})
    response = httpx.post(webhook, json={"text": headline(decisions, still_firing), "blocks": blocks[:50]}, timeout=30)
    # A configured-but-failing webhook surfaces as a FAILED run, so you notice
    # that alerting itself is broken; state is written only after this succeeds.
    response.raise_for_status()


def headline(decisions: list[dict], still_firing: int = 0) -> str:
    """What this message is about, and whether other rules are still firing (so "all clear" means the org)."""
    firing = sum(1 for d in decisions if d["new_status"] != "ok")
    resolved = len(decisions) - firing
    if firing:
        text = f"{ALERT_SOURCE}: {firing} alert(s) firing"
        if resolved:
            text += f", {resolved} resolved"
        if still_firing:
            text += f", {still_firing} other(s) still firing"
        return text
    text = f"{ALERT_SOURCE}: {resolved} alert(s) resolved"
    return text + (f", {still_firing} still firing" if still_firing else ", all clear")


def slack_text(d: dict, now: dt.datetime | None = None) -> str:
    c = d["check"]
    now = now or dt.datetime.now(dt.timezone.utc)
    group = f" `{d['group']}`" if d["group"] != "all" else ""
    link = check_link(c, d["group"])
    if d["new_status"] == "ok":
        head = f"{EMOJI['ok']} *Resolved:* {c['title']}{group}"
        head += f" — now {fmt(d['value'])} {c['unit']}" if d["value"] is not None else " — no longer reported"
        if d.get("episode_start"):
            head += f", after {duration(now - d['episode_start'])} {d['old_status']}"
        lines = [head + (f" · <{link}|open in the Dive>" if link else "")]
    else:
        threshold = c["critical"] if d["new_status"] == "critical" else c["warning"]
        label = {"escalated": "", "downgraded": "Downgraded to ", "renotify": "Still "}[d["kind"]]
        head = (f"{EMOJI[d['new_status']]} *{label}{d['new_status'].upper()}:* {c['title']}{group} — "
                f"{fmt(d['value'])} {c['unit']} (threshold {fmt(threshold)} {c['unit']})")
        lines = [head + (f" · <{link}|open in the Dive>" if link else ""), f"_{c['runbook']}_"]
    for row in d.get("details", []):
        link = query_link(row["query_id"], now)
        ident = f"<{link}|{row['query_id'][:8]}>" if link else row["query_id"][:8]
        lines.append(f"• {ident} · duckling `{slack_escape(row['duckling_id'])}` · user `{slack_escape(row['user_name'])}` · "
                     f"{slack_escape(row['detail'])}\n    `{slack_escape(row['query_text'])}`")
    return "\n".join(lines)


def slack_escape(text: str) -> str:
    """Slack mrkdwn control characters; backticks would end the inline code span."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("`", "'")


def duration(delta: dt.timedelta) -> str:
    minutes = max(0, int(delta.total_seconds() // 60))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def detail_line(row: dict) -> str:
    return (f"- {row['query_id'][:8]} duckling={row['duckling_id']} user={row['user_name']} {row['detail']}: "
            f"{row['query_text'][:100]}")


# ---- Dive links ----------------------------------------------------------------
# The Dive keeps its tab, time scope and filters in the URL fragment as
# `#state=<base64url JSON>`, one key per useDiveState call, so a link can open
# it on a given view. The bags below match the keys in dive/dive.tsx.

def dive_link(state: dict) -> str:
    if not DIVE_URL:
        return ""
    payload = base64.urlsafe_b64encode(json.dumps(state, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{DIVE_URL.split('#', 1)[0]}#state={payload}"


def check_link(check: dict, group: str) -> str:
    """The Dive view for a rule, with `{group}` filled in (e.g. the instance type that is spilling)."""
    state = check.get("dive")
    return dive_link(fill_group(state, group)) if state else ""


def query_link(query_id: str, now: dt.datetime) -> str:
    """The Query log filtered to one query id, on a window wide enough to include it."""
    if not query_id:
        return ""
    window = {"from": (now - dt.timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M"),
              "to": (now + dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")}
    return dive_link({"tab": "log", "window": window, "filters": {"query": query_id}})


def fill_group(value, group: str):
    if isinstance(value, dict):
        return {k: fill_group(v, group) for k, v in value.items()}
    if isinstance(value, str):
        return value.replace("{group}", group)
    return value


def format_line(d: dict) -> str:
    group = f"[{d['group']}]" if d["group"] != "all" else ""
    value = f"{fmt(d['value'])} {d['check']['unit']}" if d["value"] is not None else "no data"
    flag = " NOTIFY" if d["notify"] else ""
    return (f"{d['new_status'].upper():8} {d['check']['name']}{group}: {value} "
            f"({d['old_status']} -> {d['new_status']}, {d['kind']}){flag}")


def fmt(value) -> str:
    if value is None:
        return "-"
    value = float(value)
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:.1f}".rstrip("0").rstrip(".")


# ---- Helpers -----------------------------------------------------------------

def resolve_secret(key: str) -> str:
    """Read a secret from env: bare `KEY`, or the namespaced `<secret>_KEY` a Flights secret injects."""
    if os.environ.get(key):
        return os.environ[key].strip()
    for name, value in os.environ.items():
        if name.endswith(f"_{key}") and value:
            return value.strip()
    return ""


def as_utc(value) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def squash(text) -> str:
    """One line, 120 characters, for a query text."""
    return re.sub(r"\s+", " ", str(text or "")).strip()[:120]


def first_line(exc: Exception) -> str:
    return str(exc).splitlines()[0] if str(exc) else type(exc).__name__


if __name__ == "__main__":
    main()
