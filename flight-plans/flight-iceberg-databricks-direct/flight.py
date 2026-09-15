import os
import re

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def main() -> None:
    catalog = validate_identifier("ICEBERG_CATALOG", env("ICEBERG_CATALOG", "databricks_iceberg"))
    secret = validate_identifier("ICEBERG_SECRET", env("ICEBERG_SECRET", "databricks_token"))
    endpoint = env("ICEBERG_ENDPOINT", "")
    warehouse = env("ICEBERG_WAREHOUSE", "workspace")
    default_schema = env("ICEBERG_DEFAULT_SCHEMA", "default")
    schema = validate_identifier("ICEBERG_SCHEMA", env("ICEBERG_SCHEMA", "md_iceberg_demo"))
    source_table = validate_identifier("SOURCE_TABLE", env("SOURCE_TABLE", "usage_events_raw"))
    target_table = validate_identifier("TARGET_TABLE", env("TARGET_TABLE", "usage_daily_rollup"))

    if not endpoint:
        raise ValueError("ICEBERG_ENDPOINT is required (the Unity Catalog Iceberg REST endpoint)")
    if source_table.casefold() == target_table.casefold():
        raise ValueError("SOURCE_TABLE and TARGET_TABLE must be different")

    source = ".".join(quote_identifier(part) for part in (catalog, schema, source_table))
    target = ".".join(quote_identifier(part) for part in (catalog, schema, target_table))
    con = duckdb.connect("md:")
    try:
        con.execute("INSTALL httpfs; LOAD httpfs; INSTALL iceberg; LOAD iceberg;")
        # Date bucketing must agree in a local session and the Flight runtime.
        con.execute("SET TimeZone = 'UTC'")
        attach_iceberg(con, catalog, secret, endpoint, warehouse, default_schema)
        publish_rollup(con, target, rollup_sql(source))
        out = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
        print(f"[flight] wrote {out} Iceberg rollup rows to {target}")
    finally:
        con.close()


def rollup_sql(source: str) -> str:
    # Replace this SELECT and the publish schema together for another transform.
    return (
        "SELECT customer_id::BIGINT AS customer_id, event_ts::DATE AS day, "
        "count(*) AS event_count, "
        "count(*) FILTER (WHERE event_type = 'query') AS query_count "
        f"FROM {source} GROUP BY 1, 2"
    )


def publish_rollup(con, target: str, select_sql: str) -> None:
    # Iceberg does not support CREATE OR REPLACE. One transaction keeps the old
    # snapshot readable until the replacement is complete, including on errors.
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute(
            f"CREATE TABLE IF NOT EXISTS {target} "
            "(customer_id BIGINT, day DATE, event_count BIGINT, query_count BIGINT)"
        )
        con.execute(f"DELETE FROM {target}")
        con.execute(
            f"INSERT INTO {target} (customer_id, day, event_count, query_count) {select_sql}"
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def attach_iceberg(con, catalog, secret, endpoint, warehouse, default_schema) -> None:
    con.execute(
        f"CREATE DATABASE IF NOT EXISTS {quote_identifier(catalog)} ("
        f"TYPE ICEBERG, \"secret\" {quote_identifier(secret)}, "
        f"endpoint '{escape_literal(endpoint)}', "
        f"warehouse '{escape_literal(warehouse)}', "
        f"default_schema '{escape_literal(default_schema)}', read_only false)"
    )
    # Do not accidentally publish to a native database that happens to share
    # the configured name. Existing Iceberg aliases retain their stored config.
    row = con.execute("SELECT type FROM MD_DATABASES() WHERE lower(name) = lower(?)", [catalog]).fetchone()
    if row is None or row[0].lower() != "iceberg":
        raise ValueError("ICEBERG_CATALOG must name an Iceberg database")
    print(f"[flight] using Iceberg catalog {catalog}")


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def validate_identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def quote_identifier(value: str) -> str:
    return '"' + validate_identifier("identifier", value) + '"'


def escape_literal(value: str) -> str:
    return value.replace("'", "''")


if __name__ == "__main__":
    main()
