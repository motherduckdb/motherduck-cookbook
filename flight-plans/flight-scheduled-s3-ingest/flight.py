import os
import re
from datetime import datetime, timezone

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code. Defaults point at the
    # public DuckDB PyPI download stats, partitioned in S3 by year.
    source_glob = env(
        "SOURCE_GLOB",
        "s3://us-prd-motherduck-open-datasets/pypi/duckdb/pypi_daily_stats/**/*.parquet",
    )
    partition_column = validate_identifier("PARTITION_COLUMN", env("PARTITION_COLUMN", "year"))
    database = validate_identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "flights_demo"))
    schema = validate_identifier("DESTINATION_SCHEMA", env("DESTINATION_SCHEMA", "main"))
    table = validate_identifier("DESTINATION_TABLE", env("DESTINATION_TABLE", "duckdb_pypi_downloads"))
    ledger_table = validate_identifier("RUN_LEDGER_TABLE", env("RUN_LEDGER_TABLE", "ingest_runs"))
    hive_partitioning = "true" if env_bool("HIVE_PARTITIONING", True) else "false"
    load_partition = resolve_partition(env("LOAD_PARTITION", ""))

    destination = f"{database}.{schema}.{table}"
    ledger = f"{database}.{schema}.{ledger_table}"

    con = duckdb.connect("md:")

    # The Flight creates its own destination, so it runs on the first deploy
    # without depending on a database or schema that already exists.
    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")

    # Create the destination once by inferring its columns from the source.
    # LIMIT 0 reads no rows, so this is cheap and keeps the destination's types
    # aligned with the source Parquet (including the partition column).
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {destination} AS
        SELECT *
        FROM read_parquet(?, hive_partitioning = {hive_partitioning})
        WHERE {partition_column} = ?
        LIMIT 0
        """,
        [source_glob, load_partition],
    )

    # Replace exactly one partition. Filtering on the partition column lets DuckDB
    # prune every other partition folder, so the scan cost stays flat as more
    # partitions land. To transform instead of copying through, replace this
    # SELECT * with your own projection or aggregation (keep the partition column).
    con.execute(f"DELETE FROM {destination} WHERE {partition_column} = ?", [load_partition])
    con.execute(
        f"""
        INSERT INTO {destination}
        SELECT *
        FROM read_parquet(?, hive_partitioning = {hive_partitioning})
        WHERE {partition_column} = ?
        """,
        [source_glob, load_partition],
    )

    row_count = con.execute(
        f"SELECT count(*) FROM {destination} WHERE {partition_column} = ?",
        [load_partition],
    ).fetchone()[0]

    # A lightweight audit trail of which partition each run refreshed.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ledger} (
            run_at TIMESTAMPTZ,
            source_glob VARCHAR,
            destination_table VARCHAR,
            partition_column VARCHAR,
            load_partition VARCHAR,
            row_count BIGINT
        )
        """
    )
    con.execute(
        f"INSERT INTO {ledger} VALUES (current_timestamp, ?, ?, ?, ?, ?)",
        [source_glob, destination, partition_column, str(load_partition), row_count],
    )
    print(f"refreshed {destination} partition {partition_column}={load_partition}: {row_count} rows")


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def validate_identifier(name: str, value: str) -> str:
    # Database, schema, table, and column names flow into CREATE/DELETE/INSERT
    # statements that cannot be parameterized, so reject anything that is not a
    # plain SQL identifier before any SQL runs.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def resolve_partition(raw: str) -> int | str:
    # Default to the current UTC year's partition. Set LOAD_PARTITION in config to
    # target another partition: a different year, a date string, a region code, and
    # so on. Digit-only values are treated as integers so they match a numeric
    # partition column (such as a Hive year) and still prune cleanly.
    if not raw:
        return datetime.now(timezone.utc).year
    return int(raw) if raw.lstrip("-").isdigit() else raw


if __name__ == "__main__":
    main()
