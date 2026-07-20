import os
import re
from datetime import datetime, timezone

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code. The default points at the
    # sample workbook shipped alongside this template, served over HTTPS from
    # GitHub, so a fresh deploy produces a successful run you can then point at
    # your own S3 (or HTTPS) workbook.
    source_xlsx = env(
        "SOURCE_XLSX",
        "https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-excel-s3-ingest/sample_orders.xlsx",
    )
    sheet = env("SHEET", "orders", allow_empty=True)
    all_varchar = "true" if env_bool("ALL_VARCHAR", False) else "false"
    database = validate_identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "flights_demo"))
    schema = validate_identifier("DESTINATION_SCHEMA", env("DESTINATION_SCHEMA", "main"))
    table = validate_identifier("DESTINATION_TABLE", env("DESTINATION_TABLE", "excel_orders"))
    ledger_table = validate_identifier("RUN_LEDGER_TABLE", env("RUN_LEDGER_TABLE", "ingest_runs"))

    destination = f"{database}.{schema}.{table}"
    ledger = f"{database}.{schema}.{ledger_table}"

    con = duckdb.connect("md:")

    # read_xlsx comes from the excel extension and remote paths come from httpfs;
    # both autoload on first use, so no INSTALL/LOAD is needed. A single xlsx is
    # read per run: read_xlsx does not take a glob of multiple files today.
    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")

    # An Excel workbook is a full snapshot, not an append log, so each run fully
    # replaces the destination. The source path is a bound parameter; the sheet
    # name is a named table-function argument that must be a literal, so it is
    # escaped and inlined only after validation.
    sheet_clause = f", sheet = '{escape_literal(sheet)}'" if sheet else ""
    con.execute(
        f"""
        CREATE OR REPLACE TABLE {destination} AS
        SELECT *
        FROM read_xlsx(?{sheet_clause}, all_varchar = {all_varchar})
        """,
        [source_xlsx],
    )

    row_count = con.execute(f"SELECT count(*) FROM {destination}").fetchone()[0]

    # A lightweight audit trail of what each run loaded.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ledger} (
            run_at TIMESTAMPTZ,
            source_xlsx VARCHAR,
            sheet VARCHAR,
            destination_table VARCHAR,
            row_count BIGINT
        )
        """
    )
    con.execute(
        f"INSERT INTO {ledger} VALUES (current_timestamp, ?, ?, ?, ?)",
        [source_xlsx, sheet or "(first sheet)", destination, row_count],
    )
    print(f"loaded {destination} from {source_xlsx} sheet={sheet or '(first)'}: {row_count} rows")


def env(name: str, default: str, *, allow_empty: bool = False) -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value or allow_empty else default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def validate_identifier(name: str, value: str) -> str:
    # Database, schema, and table names flow into CREATE statements that cannot be
    # parameterized, so reject anything that is not a plain SQL identifier before
    # any SQL runs.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def escape_literal(value: str) -> str:
    # The sheet name is inlined as a single-quoted SQL string literal, so double
    # any single quotes to keep it a single literal.
    return value.replace("'", "''")


if __name__ == "__main__":
    main()
