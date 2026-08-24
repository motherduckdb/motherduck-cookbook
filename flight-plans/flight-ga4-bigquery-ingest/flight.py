"""Incrementally ingest GA4 BigQuery daily exports into MotherDuck."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
GA4_TABLE_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_]+\.events_\*$")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip() or default


def identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def flight_secret_value(name: str) -> str:
    """Read a local value or the prefixed value a Flight secret injects."""
    if value := os.environ.get(name, "").strip():
        return value
    suffix = f"_{name}"
    return next(
        (value.strip() for key, value in os.environ.items() if key.endswith(suffix) and value.strip()),
        "",
    )


def configure_gcp_credentials() -> Path | None:
    """Use local ADC, or materialize a Flight-secret JSON key privately."""
    if env("GOOGLE_APPLICATION_CREDENTIALS"):
        return None
    if (Path.home() / ".config/gcloud/application_default_credentials.json").exists():
        print("Using local Google Application Default Credentials.")
        return None
    service_account_json = flight_secret_value("GOOGLE_APPLICATION_CREDENTIALS_JSON")
    if not service_account_json:
        raise RuntimeError(
            "Set GOOGLE_APPLICATION_CREDENTIALS or local Application Default Credentials, "
            "or attach a TYPE FLIGHTS secret with GOOGLE_APPLICATION_CREDENTIALS_JSON."
        )
    json.loads(service_account_json)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="ga4_gcp_", delete=False
    ) as handle:
        handle.write(service_account_json)
    path = Path(handle.name)
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
    return path


def build_ga4_query(pattern: str, start_date: dt.date, end_date: dt.date) -> str:
    return f"""
        SELECT
            PARSE_DATE('%Y%m%d', event_date) AS event_date,
            event_timestamp,
            event_name,
            user_pseudo_id,
            platform,
            geo.country AS country,
            traffic_source.source AS traffic_source,
            traffic_source.medium AS traffic_medium
        FROM `{pattern}`
        WHERE _TABLE_SUFFIX BETWEEN '{start_date:%Y%m%d}' AND '{end_date:%Y%m%d}'
    """


def resolve_window(connection: duckdb.DuckDBPyConnection, destination: str) -> tuple[dt.date, dt.date]:
    start = env("START_DATE")
    end = env("END_DATE")
    if bool(start) != bool(end):
        raise ValueError("Set both START_DATE and END_DATE for a backfill, or neither.")
    if start:
        return dt.date.fromisoformat(start), dt.date.fromisoformat(end)

    previous_max = connection.execute(f"SELECT max(event_date) FROM {destination}").fetchone()[0]
    if previous_max is None:
        cold_start = dt.date.fromisoformat(env("COLD_START_DATE"))
        return cold_start, cold_start
    if isinstance(previous_max, dt.datetime):
        previous_max = previous_max.date()
    if not isinstance(previous_max, dt.date):
        previous_max = dt.date.fromisoformat(str(previous_max))
    # GA4 can update a daily export for up to three days after its event date.
    return previous_max - dt.timedelta(days=2), dt.date.today() - dt.timedelta(days=1)


def main() -> None:
    billing_project = env("GCP_PROJECT_ID")
    source_pattern = env("GA4_SOURCE_TABLE_PATTERN")
    if not billing_project:
        raise ValueError("GCP_PROJECT_ID is required.")
    if not GA4_TABLE_RE.fullmatch(source_pattern):
        raise ValueError(
            "GA4_SOURCE_TABLE_PATTERN must be project.dataset.events_*, got "
            f"{source_pattern!r}"
        )
    database = identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "ga4"))
    schema = identifier("DESTINATION_SCHEMA", env("DESTINATION_SCHEMA", "raw"))
    table = identifier("DESTINATION_TABLE", env("DESTINATION_TABLE", "events"))
    if not env("COLD_START_DATE"):
        raise ValueError("COLD_START_DATE is required (YYYY-MM-DD).")

    temporary_credentials = configure_gcp_credentials()
    connection = duckdb.connect(":memory:", config={"allow_community_extensions": True})
    destination = ".".join(map(quote_identifier, (database, schema, table)))
    try:
        connection.execute("INSTALL bigquery FROM community")
        connection.execute("LOAD bigquery")
        connection.execute("LOAD motherduck")
        connection.execute("ATTACH 'md:'")
        connection.execute("SET preserve_insertion_order = false")
        connection.execute(f"CREATE DATABASE IF NOT EXISTS {quote_identifier(database)}")
        connection.execute(
            f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(database)}.{quote_identifier(schema)}"
        )
        bootstrap = build_ga4_query(source_pattern, dt.date(1970, 1, 1), dt.date(1970, 1, 1))
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {destination} AS "
            "SELECT * FROM bigquery_query(?, ?) LIMIT 0",
            [billing_project, bootstrap],
        )

        start_date, end_date = resolve_window(connection, destination)
        if start_date > end_date:
            print(f"Nothing to load: {start_date} is after {end_date}.")
            return
        query = build_ga4_query(source_pattern, start_date, end_date)
        connection.execute("BEGIN")
        try:
            connection.execute(
                f"DELETE FROM {destination} WHERE event_date BETWEEN ? AND ?",
                [start_date, end_date],
            )
            connection.execute(
                f"INSERT INTO {destination} SELECT * FROM bigquery_query(?, ?)",
                [billing_project, query],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

        rows_loaded = connection.execute(
            f"SELECT count(*) FROM {destination} WHERE event_date BETWEEN ? AND ?",
            [start_date, end_date],
        ).fetchone()[0]
        audit_table = f"{quote_identifier(database)}.main.ga4_ingest_runs"
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {audit_table} "
            "(run_at TIMESTAMPTZ, start_date DATE, end_date DATE, rows_loaded BIGINT)"
        )
        connection.execute(
            f"INSERT INTO {audit_table} VALUES (current_timestamp, ?, ?, ?)",
            [start_date, end_date, rows_loaded],
        )
        print(f"Loaded {rows_loaded} GA4 event rows for {start_date} through {end_date}.")
    finally:
        connection.close()
        if temporary_credentials:
            temporary_credentials.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
