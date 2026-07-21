import os
import re

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code. The Databricks credential
    # is the exception: it comes from a MotherDuck Iceberg-catalog secret,
    # referenced by name (ICEBERG_SECRET), never inlined here.
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

    ice = f"{catalog}.{schema}"
    source = f"{ice}.{source_table}"
    target = f"{ice}.{target_table}"

    con = duckdb.connect("md:")
    con.execute("INSTALL iceberg; LOAD iceberg;")
    attach_iceberg(con, catalog, secret, endpoint, warehouse, default_schema)

    # DIRECT publish: the transform reads the source Iceberg table and writes the
    # result straight back to an Iceberg table in the same catalog. MotherDuck is
    # the compute engine; nothing is persisted in MotherDuck. Replace the SELECT
    # (and the CREATE TABLE column list) with your own transform, keeping the two
    # in sync.
    con.execute(f"DROP TABLE IF EXISTS {target}")
    con.execute(
        f"CREATE TABLE {target} "
        "(customer_id INTEGER, day DATE, event_count BIGINT, query_count BIGINT)"
    )
    con.execute(
        f"INSERT INTO {target} "
        "SELECT customer_id, event_ts::DATE AS day, count(*) AS event_count, "
        "count(*) FILTER (WHERE event_type = 'query') AS query_count "
        f"FROM {source} GROUP BY 1, 2"
    )

    src = con.execute(f"SELECT count(*) FROM {source}").fetchone()[0]
    out = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
    print(f"[flight] source Iceberg rows ({source_table}): {src}")
    print(f"[flight] wrote Iceberg rollup rows ({target_table}): {out}")
    print("[flight] done: read Iceberg (Databricks UC) -> transform on MD -> wrote Iceberg directly")


def attach_iceberg(con, catalog, secret, endpoint, warehouse, default_schema) -> None:
    # Idempotent attach of the Databricks Unity Catalog Iceberg REST catalog.
    # catalog and secret are validated identifiers; endpoint, warehouse, and
    # default_schema are inlined as escaped string literals.
    attach_sql = (
        f"CREATE DATABASE {catalog} (\n"
        "    TYPE ICEBERG,\n"
        f"    \"secret\" {secret},\n"
        f"    endpoint '{escape_literal(endpoint)}',\n"
        f"    warehouse '{escape_literal(warehouse)}',\n"
        f"    default_schema '{escape_literal(default_schema)}',\n"
        "    read_only false\n"
        ")"
    )
    try:
        con.execute(attach_sql)
        print(f"[flight] attached Databricks Iceberg catalog {catalog}")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"[flight] Iceberg catalog {catalog} already attached")
        else:
            raise


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def validate_identifier(name: str, value: str) -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def escape_literal(value: str) -> str:
    return value.replace("'", "''")


if __name__ == "__main__":
    main()
