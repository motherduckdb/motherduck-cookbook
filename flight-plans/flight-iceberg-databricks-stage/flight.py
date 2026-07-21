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

    # Where the curated rollup is staged inside MotherDuck before it is published
    # to Iceberg. This is the difference from the direct template: the result
    # lands in a real MotherDuck table you can query, share, and build Dives on,
    # and only then gets published to the open lake.
    md_database = validate_identifier("MD_DATABASE", env("MD_DATABASE", "flights_demo"))
    md_schema = validate_identifier("MD_SCHEMA", env("MD_SCHEMA", "main"))
    md_table = validate_identifier("MD_TABLE", env("MD_TABLE", "usage_daily_rollup"))

    # PUBLISH is the step that writes to Iceberg. It defaults on, but set
    # PUBLISH_TO_ICEBERG=false to stage in MotherDuck only (for example to inspect
    # the rollup before it reaches the shared lake).
    publish = env_bool("PUBLISH_TO_ICEBERG", True)

    if not endpoint:
        raise ValueError("ICEBERG_ENDPOINT is required (the Unity Catalog Iceberg REST endpoint)")

    ice = f"{catalog}.{schema}"
    source = f"{ice}.{source_table}"
    target = f"{ice}.{target_table}"
    md_fqn = f"{md_database}.{md_schema}.{md_table}"

    con = duckdb.connect("md:")
    con.execute("INSTALL iceberg; LOAD iceberg;")
    attach_iceberg(con, catalog, secret, endpoint, warehouse, default_schema)

    con.execute(f"CREATE DATABASE IF NOT EXISTS {md_database}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {md_database}.{md_schema}")

    # STAGE: read the source Iceberg table, transform on MotherDuck's engine, and
    # materialize the result into a MotherDuck table. Replace the SELECT with your
    # own transform.
    con.execute(
        f"CREATE OR REPLACE TABLE {md_fqn} AS "
        "SELECT customer_id, event_ts::DATE AS day, count(*) AS event_count, "
        "count(*) FILTER (WHERE event_type = 'query') AS query_count "
        f"FROM {source} GROUP BY 1, 2"
    )
    staged = con.execute(f"SELECT count(*) FROM {md_fqn}").fetchone()[0]
    print(f"[flight] staged {staged} rows in MotherDuck: {md_fqn}")

    if not publish:
        print("[flight] PUBLISH_TO_ICEBERG=false, staged in MotherDuck only; skipping Iceberg write")
        return

    # PUBLISH: write the staged MotherDuck table back to Iceberg. The Iceberg
    # target schema is declared explicitly and must match the staged columns.
    con.execute(f"DROP TABLE IF EXISTS {target}")
    con.execute(
        f"CREATE TABLE {target} "
        "(customer_id INTEGER, day DATE, event_count BIGINT, query_count BIGINT)"
    )
    con.execute(f"INSERT INTO {target} SELECT * FROM {md_fqn}")

    out = con.execute(f"SELECT count(*) FROM {target}").fetchone()[0]
    print(f"[flight] published Iceberg rollup rows ({target_table}): {out}")
    print("[flight] done: read Iceberg -> stage in MotherDuck -> publish MotherDuck table to Iceberg")


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
