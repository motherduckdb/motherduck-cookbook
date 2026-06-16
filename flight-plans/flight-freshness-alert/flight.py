import os
import re

import duckdb
import httpx


# ---- Configure your freshness checks here --------------------------------
# Each entry is a table, the timestamp column that records when its rows
# arrived, and warn/error age thresholds in hours (dbt-style source freshness):
# lag = now() - MAX(column); lag >= error_after_hours -> "error",
# lag >= warn_after_hours -> "warn", otherwise "pass".
#
# The defaults point at read-only `sample_data`, a frozen ~2022 snapshot, so a
# fresh deploy always reports "error" and fires a Slack message: a built-in test
# that your webhook is wired up. Replace these with your own tables before you
# schedule the Flight, or it will alert on every run.
CHECKS = [
    {
        "table": "sample_data.hn.hacker_news",
        "column": "timestamp",
        "warn_after_hours": 24,
        "error_after_hours": 48,
    },
    {
        "table": "sample_data.nyc.taxi",
        "column": "tpep_pickup_datetime",
        "warn_after_hours": 24,
        "error_after_hours": 48,
    },
]

# "warn" alerts on warn+error; "error" alerts only on error.
ALERT_LEVEL = "warn"

# Per-run audit ledger, written as database.schema.table. Must be a writable
# database (sample_data is read-only). Set to "" to skip the ledger.
RESULTS_TABLE = "flights_demo.main.freshness_check_runs"
# --------------------------------------------------------------------------


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Ordered so "warn" as the alert level also covers "error".
STATUS_RANK = {"pass": 0, "warn": 1, "error": 2}


def main() -> None:
    alert_level = ALERT_LEVEL.strip().lower()
    if alert_level not in {"warn", "error"}:
        raise ValueError(f"ALERT_LEVEL must be 'warn' or 'error', got {ALERT_LEVEL!r}")
    if not CHECKS:
        print("CHECKS is empty; nothing to check.")
        return

    con = duckdb.connect("md:")
    results = [evaluate(con, check) for check in CHECKS]

    for result in results:
        print(format_line(result))

    if RESULTS_TABLE.strip():
        write_ledger(con, RESULTS_TABLE.strip(), results)
    con.close()

    # Alert when any check reaches the configured level. Do this after the ledger
    # write so a failing webhook still leaves an audit trail.
    threshold = STATUS_RANK[alert_level]
    firing = [r for r in results if STATUS_RANK[r["status"]] >= threshold]
    webhook = resolve_webhook()

    if not firing:
        print(f"All checks within thresholds (alert level '{alert_level}'); no alert sent.")
        return
    if not webhook:
        print(
            f"{len(firing)} check(s) at/above '{alert_level}', "
            "but no SLACK_WEBHOOK_URL was found in the environment; skipping Slack."
        )
        return

    send_slack_alert(webhook, firing)
    print(f"Sent Slack alert for {len(firing)} check(s).")


def resolve_webhook() -> str:
    # A local run sets SLACK_WEBHOOK_URL directly (see "Run it"). Deployed as a
    # Flight, the webhook comes from a `TYPE flights` secret, and MotherDuck
    # injects each secret param under the env var `<secret_name>_<PARAM>`, NOT
    # the bare param name. So a secret named `freshness_slack` with a
    # `SLACK_WEBHOOK_URL` param arrives as `freshness_slack_SLACK_WEBHOOK_URL`.
    # Accept both: the exact name first (local), then any var ending in the
    # suffix (the secret, whatever you named it).
    direct = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if direct:
        return direct
    for key, value in os.environ.items():
        if key.endswith("_SLACK_WEBHOOK_URL") and value.strip():
            return value.strip()
    return ""


def evaluate(con: duckdb.DuckDBPyConnection, check: dict) -> dict:
    # Validate first: the table and column names flow into SQL that cannot be
    # parameterized, so reject anything that is not a plain SQL identifier.
    table = validate_table(check["table"])
    column = validate_identifier("column", check["column"])
    warn_after = float(check["warn_after_hours"])
    error_after = float(check["error_after_hours"])
    base = {
        "table": check["table"],
        "column": check["column"],
        "warn_after_hours": warn_after,
        "error_after_hours": error_after,
    }

    try:
        # Freshness is computed in SQL against the runtime clock (now()), so it
        # follows the Flight's timezone (UTC). A TIMESTAMPTZ column is compared
        # unambiguously; a naive TIMESTAMP column is read in the runtime timezone.
        # Reading max() back as a real timestamp needs pytz for TIMESTAMPTZ
        # columns (it is in requirements.txt). See the README "Time zones" section.
        max_ts, lag_hours = con.execute(
            f"SELECT max({column}) AS max_ts, "
            f"date_diff('hour', max({column}), now()) AS lag_hours "
            f"FROM {table}"
        ).fetchone()
    except duckdb.Error as exc:
        # A missing table/column should not blind the other checks: record this
        # one as an error with the reason and move on.
        return {**base, "max_ts": None, "lag_hours": None, "status": "error",
                "detail": str(exc).splitlines()[0]}

    if max_ts is None:
        return {**base, "max_ts": None, "lag_hours": None, "status": "error",
                "detail": "table is empty or column is all NULL"}

    lag = float(lag_hours)
    if lag >= error_after:
        status = "error"
    elif lag >= warn_after:
        status = "warn"
    else:
        status = "pass"
    return {**base, "max_ts": max_ts, "lag_hours": lag, "status": status, "detail": ""}


def write_ledger(con: duckdb.DuckDBPyConnection, target: str, results: list[dict]) -> None:
    database, schema, _table = split_results_table(target)
    # The ledger lives in a writable database, so create it (and its schema) on
    # first run; sample_data is read-only and only ever read from.
    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {target} (
            run_at TIMESTAMPTZ,
            table_name VARCHAR,
            timestamp_column VARCHAR,
            max_timestamp TIMESTAMPTZ,
            lag_hours DOUBLE,
            warn_after_hours DOUBLE,
            error_after_hours DOUBLE,
            status VARCHAR,
            detail VARCHAR
        )
        """
    )
    # One bulk multi-row INSERT (not executemany / row-by-row) with bound
    # parameters. run_at uses now() so every row in a run shares one timestamp.
    row_sql = "(now(), ?, ?, ?, ?, ?, ?, ?, ?)"
    params: list = []
    for r in results:
        params.extend([
            r["table"], r["column"], r["max_ts"], r["lag_hours"],
            r["warn_after_hours"], r["error_after_hours"], r["status"], r["detail"],
        ])
    con.execute(
        f"INSERT INTO {target} "
        "(run_at, table_name, timestamp_column, max_timestamp, lag_hours, "
        "warn_after_hours, error_after_hours, status, detail) VALUES "
        + ", ".join([row_sql] * len(results)),
        params,
    )


def send_slack_alert(webhook: str, firing: list[dict]) -> None:
    lines = []
    for r in firing:
        emoji = ":red_circle:" if r["status"] == "error" else ":large_yellow_circle:"
        if r["lag_hours"] is None:
            detail = r["detail"] or "no data"
        else:
            detail = (
                f"{humanize_hours(r['lag_hours'])} old "
                f"(warn after {humanize_hours(r['warn_after_hours'])}, "
                f"error after {humanize_hours(r['error_after_hours'])})"
            )
        lines.append(f"{emoji} *{r['table']}* — {r['status'].upper()}: {detail}")

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "Data freshness alert", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"{len(firing)} table(s) past their freshness threshold"}
        ]},
    ]
    response = httpx.post(
        webhook,
        json={"text": f"Data freshness alert: {len(firing)} table(s) stale", "blocks": blocks},
        timeout=30,
    )
    # A configured-but-failing webhook should surface as a FAILED run so you
    # notice that alerting itself is broken; the ledger is already written.
    response.raise_for_status()


def format_line(r: dict) -> str:
    if r["lag_hours"] is None:
        return f"[{r['status'].upper():5}] {r['table']} ({r['column']}): {r['detail']}"
    return (
        f"[{r['status'].upper():5}] {r['table']} ({r['column']}): "
        f"{humanize_hours(r['lag_hours'])} old "
        f"(warn after {humanize_hours(r['warn_after_hours'])}, "
        f"error after {humanize_hours(r['error_after_hours'])}); "
        f"max {r['max_ts']}"
    )


def humanize_hours(hours: float) -> str:
    # Turn an age in hours into the largest natural unit (hours, days, months,
    # years), so an alert reads "3 years, 7 months" instead of "31203h". The
    # ledger still stores the exact numeric lag_hours; this is display only.
    h = float(hours)
    if h < 1:
        return "under 1 hour"
    if round(h) < 24:
        return _plural(round(h), "hour")
    days = h / 24
    if round(days) < 60:
        return _plural(round(days), "day")
    months = days / 30.44  # average days per month
    if round(months) < 12:
        return _plural(round(months), "month")
    years = days / 365.25
    whole_years = int(years)
    extra_months = round((years - whole_years) * 12)
    if extra_months >= 12:  # rounding can push remainder to a full year
        whole_years += 1
        extra_months -= 12
    if extra_months:
        return f"{_plural(whole_years, 'year')}, {_plural(extra_months, 'month')}"
    return _plural(whole_years, "year")


def _plural(n: int, unit: str) -> str:
    return f"{n} {unit}" if n == 1 else f"{n} {unit}s"


def validate_identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def validate_table(value: str) -> str:
    # Accept database.schema.table or schema.table; validate each part so the
    # name is safe to interpolate into SQL that cannot be parameterized.
    parts = value.split(".")
    if len(parts) not in (2, 3):
        raise ValueError(
            f"table must be 'database.schema.table' or 'schema.table', got {value!r}"
        )
    for part in parts:
        validate_identifier("table part", part)
    return value


def split_results_table(value: str) -> tuple[str, str, str]:
    parts = value.split(".")
    if len(parts) != 3:
        raise ValueError(f"RESULTS_TABLE must be 'database.schema.table', got {value!r}")
    for part in parts:
        validate_identifier("RESULTS_TABLE part", part)
    return parts[0], parts[1], parts[2]


if __name__ == "__main__":
    main()
