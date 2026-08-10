import json
import os
import re
from collections.abc import Iterator

import dlt
import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
WRITE_DISPOSITIONS = {"append", "merge", "replace"}


def ga4_rows(
    property_id: str,
    dimensions: list[str],
    metrics: list[str],
    start_date: str,
    end_date: str,
) -> Iterator[dict]:
    # GA4 source: aggregated report rows from the GA4 Data API (runReport). Each
    # yielded dict is one dimension-combination with its metric values, and dlt
    # infers and evolves the schema from these dicts.
    #
    # This is the aggregated reporting surface (sessions, users, pageviews, etc.
    # by dimension) -- NOT raw events. If you need event-level GA4 data, use the
    # native GA4 -> BigQuery export with the flight-bigquery-ingest template
    # instead; the Data API cannot return raw events.
    #
    # Credentials come from the GA4_SERVICE_ACCOUNT_JSON Flights secret (the full
    # service-account key JSON), read from the environment -- never from config.
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        RunReportRequest,
    )
    from google.oauth2 import service_account

    sa_info = json.loads(os.environ["GA4_SERVICE_ACCOUNT_JSON"])
    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )
    client = BetaAnalyticsDataClient(credentials=credentials)

    # GA4 caps a single report page at 10k rows; page through with offset until
    # we have read row_count rows.
    page_size, offset = 10000, 0
    while True:
        response = client.run_report(
            RunReportRequest(
                property=f"properties/{property_id}",
                dimensions=[Dimension(name=name) for name in dimensions],
                metrics=[Metric(name=name) for name in metrics],
                date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                limit=page_size,
                offset=offset,
            )
        )
        for row in response.rows:
            record = {
                dim: row.dimension_values[i].value
                for i, dim in enumerate(dimensions)
            }
            for i, metric in enumerate(metrics):
                raw = row.metric_values[i].value
                record[metric] = float(raw) if raw not in (None, "") else None
            yield record

        offset += page_size
        if offset >= response.row_count:
            break


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code. Defaults pull the last 7
    # days of sessions/users/pageviews by channel from the GA4 property set in
    # GA4_PROPERTY_ID into ga4_ingest.ga4.ga4_report in your own account.
    database = validate_identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "ga4_ingest"))
    dataset_name = env("DATASET_NAME", "ga4")
    table_name = env("TABLE_NAME", "ga4_report")
    pipeline_name = env("PIPELINE_NAME", "ga4_dlt_ingest")
    write_disposition = env("WRITE_DISPOSITION", "merge")
    if write_disposition not in WRITE_DISPOSITIONS:
        raise ValueError(
            f"WRITE_DISPOSITION must be one of {sorted(WRITE_DISPOSITIONS)}, got {write_disposition!r}"
        )
    ledger_table = validate_identifier("RUN_LEDGER_TABLE", env("RUN_LEDGER_TABLE", "dlt_ingest_runs"))

    # GA4 source inputs.
    property_id = env("GA4_PROPERTY_ID", "")
    if not property_id.isdigit():
        raise ValueError(
            f"GA4_PROPERTY_ID must be the numeric GA4 property id, got {property_id!r}"
        )
    dimensions = [
        dim.strip()
        for dim in env("GA4_DIMENSIONS", "date,sessionDefaultChannelGroup").split(",")
        if dim.strip()
    ]
    metrics = [
        metric.strip()
        for metric in env("GA4_METRICS", "sessions,totalUsers,screenPageViews").split(",")
        if metric.strip()
    ]
    start_date = env("GA4_START_DATE", "7daysAgo")
    end_date = env("GA4_END_DATE", "yesterday")
    # Merge key defaults to the dimension columns, so re-pulling a lookback window
    # heals GA4's late-arriving data instead of duplicating or freezing it.
    primary_key = [
        key.strip()
        for key in env("PRIMARY_KEY", ",".join(dimensions)).split(",")
        if key.strip()
    ]

    # dlt writes working files under HOME; a Flight has a writable /tmp.
    os.environ.setdefault("HOME", "/tmp")
    # Point the dlt MotherDuck destination at our database. The injected
    # MOTHERDUCK_TOKEN supplies the credential, so no token appears here.
    os.environ["DESTINATION__MOTHERDUCK__CREDENTIALS__DATABASE"] = database

    # Create the destination database so dlt has a catalog to build the dataset in;
    # dlt creates the dataset (schema) and tables, but not the database itself.
    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")

    pipeline = dlt.pipeline(
        pipeline_name=pipeline_name,
        destination="motherduck",
        dataset_name=dataset_name,
    )
    load_info = pipeline.run(
        ga4_rows(property_id, dimensions, metrics, start_date, end_date),
        table_name=table_name,
        write_disposition=write_disposition,
        primary_key=primary_key,
        # Prefer Parquet loader files over row-wise insert_values so larger
        # sources stay on a bulk-loading path. Keep this unless you have measured
        # a reason to change it.
        loader_file_format="parquet",
    )

    # Record the dlt load package summary so each run leaves an audit trail. The
    # ledger lives in the database's main schema, separate from the dlt dataset.
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.main")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {database}.main.{ledger_table} (
            run_at TIMESTAMPTZ,
            pipeline_name VARCHAR,
            destination_dataset VARCHAR,
            destination_table VARCHAR,
            load_summary VARCHAR
        )
        """
    )
    con.execute(
        f"INSERT INTO {database}.main.{ledger_table} VALUES (current_timestamp, ?, ?, ?, ?)",
        [pipeline_name, dataset_name, table_name, str(load_info)],
    )
    con.close()
    print(load_info)


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def validate_identifier(name: str, value: str) -> str:
    # The database and ledger table names flow into CREATE/INSERT statements that
    # cannot be parameterized, so reject anything that is not a plain SQL
    # identifier before any SQL runs.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


if __name__ == "__main__":
    main()
