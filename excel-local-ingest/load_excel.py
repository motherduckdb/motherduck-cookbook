import os
import re

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def main() -> None:
    # Config comes from the environment so you adapt this by setting values, not
    # by editing code. The default reads the sample_orders.xlsx workbook next to
    # this script and builds excel_demo.main.excel_orders in your account.
    source_xlsx = env("SOURCE_XLSX", os.path.join(os.path.dirname(__file__), "sample_orders.xlsx"))
    sheet = env("SHEET", "orders")
    all_varchar = "true" if env_bool("ALL_VARCHAR", False) else "false"
    database = validate_identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "excel_demo"))
    schema = validate_identifier("DESTINATION_SCHEMA", env("DESTINATION_SCHEMA", "main"))
    table = validate_identifier("DESTINATION_TABLE", env("DESTINATION_TABLE", "excel_orders"))

    destination = f"{database}.{schema}.{table}"

    if not os.path.exists(source_xlsx):
        raise FileNotFoundError(f"SOURCE_XLSX not found: {source_xlsx}")

    # Connect to MotherDuck. duckdb.connect("md:") reads the token from the
    # motherduck_token / MOTHERDUCK_TOKEN environment variable.
    con = duckdb.connect("md:")

    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")

    # read_xlsx (excel extension) autoloads on first use. The local file is read
    # by the local DuckDB process and the resulting rows are written to the
    # MotherDuck table. The path is a bound parameter; the sheet name is a named
    # table-function argument that must be a literal, so it is escaped and inlined.
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
    print(f"loaded {destination} from {source_xlsx} sheet={sheet or '(first)'}: {row_count} rows")
    preview = con.execute(f"SELECT * FROM {destination} LIMIT 5")
    columns = [d[0] for d in preview.description]
    print(" | ".join(columns))
    for row in preview.fetchall():
        print(" | ".join("" if v is None else str(v) for v in row))


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def validate_identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def escape_literal(value: str) -> str:
    return value.replace("'", "''")


if __name__ == "__main__":
    main()
