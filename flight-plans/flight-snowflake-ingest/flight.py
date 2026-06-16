import os
import re

import duckdb
import snowflake.connector


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Default MotherDuck database for the control tables and the moved tables when
# TARGET_DB is not set in config/env. flights_demo is a writable demo database.
DEFAULT_TARGET_DB = "flights_demo"


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code. The Snowflake user and
    # password are the exception: they come from a MotherDuck secret (see
    # resolve_secret_param).
    mode = env("MODE", "all").strip().lower()
    if mode not in {"discover", "move", "all"}:
        raise ValueError(f"MODE must be 'discover', 'move', or 'all', got {mode!r}")

    target_db = validate_identifier("TARGET_DB", env("TARGET_DB", DEFAULT_TARGET_DB))
    target_schema = validate_identifier("TARGET_SCHEMA", env("TARGET_SCHEMA", "main"))
    control_schema = validate_identifier("CONTROL_SCHEMA", env("CONTROL_SCHEMA", "main"))
    inventory_table = validate_identifier("INVENTORY_TABLE", env("INVENTORY_TABLE", "snowflake_inventory"))
    ledger_table = validate_identifier("LEDGER_TABLE", env("LEDGER_TABLE", "snowflake_move_runs"))

    # Source scope in Snowflake. SNOWFLAKE_DATABASE is optional: set it to scan one
    # database, or leave it unset to scan every database the connection can see
    # (enumerated with SHOW TERSE DATABASES). SNOWFLAKE_SCHEMA is optional and, when
    # set, narrows discovery to that one schema.
    source_database_raw = env("SNOWFLAKE_DATABASE", "")
    source_database = (
        validate_identifier("SNOWFLAKE_DATABASE", source_database_raw)
        if source_database_raw else ""
    )
    source_schema_raw = env("SNOWFLAKE_SCHEMA", "")
    source_schema = validate_identifier("SNOWFLAKE_SCHEMA", source_schema_raw) if source_schema_raw else ""

    # MOVE is the destructive phase (it writes real tables), so it defaults to a
    # dry run: the first deploy logs the move plan without copying any data. Set
    # DRY_RUN=false to actually move once you have reviewed the inventory.
    dry_run = env_bool("DRY_RUN", True)

    # 0 means "no cap"; any positive value caps SELECT * with a LIMIT per table,
    # useful for a sampled first pass on very large tables.
    max_rows = int(env("MAX_ROWS_PER_TABLE", "0") or "0")

    control_fqn = f"{target_db}.{control_schema}"
    inventory_fqn = f"{control_fqn}.{inventory_table}"
    ledger_fqn = f"{control_fqn}.{ledger_table}"

    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {target_db}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {control_fqn}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {target_db}.{target_schema}")

    sf_conn_kwargs = snowflake_connect_kwargs(source_database, source_schema)

    if mode in {"discover", "all"}:
        discover(con, sf_conn_kwargs, source_database, source_schema, inventory_fqn)

    if mode in {"move", "all"}:
        move(
            con,
            sf_conn_kwargs,
            inventory_fqn,
            ledger_fqn,
            target_db,
            target_schema,
            dry_run=dry_run,
            max_rows=max_rows,
        )

    con.close()
    print(f"done (MODE={mode})")


def discover(
    con: duckdb.DuckDBPyConnection,
    sf_conn_kwargs: dict,
    source_database: str,
    source_schema: str,
    inventory_fqn: str,
) -> None:
    # Build the inventory from Snowflake's per-database INFORMATION_SCHEMA.TABLES.
    # With SNOWFLAKE_DATABASE set, scan just that database; with it empty, scan
    # every database the connection can see. INFORMATION_SCHEMA is live and exact;
    # for a single account-wide query instead, SNOWFLAKE.ACCOUNT_USAGE.TABLES spans
    # all databases but lags up to ~90 minutes and needs IMPORTED PRIVILEGES on the
    # SNOWFLAKE database (see README).
    scope = source_database or "all visible databases"
    if source_schema:
        scope += f" (schema {source_schema})"
    print(f"discover: scanning {scope}")
    rows = fetch_inventory_rows(sf_conn_kwargs, source_database, source_schema)
    print(f"discover: found {len(rows)} table(s) in scope")

    # Default selection rule: move base tables, skip views and external tables, so
    # MODE=all works end to end. Re-running discovery is a full CREATE OR REPLACE,
    # which resets any manual `selected` edits; see the README "Caveats" for how
    # to preserve a selection (filter on table_name, or maintain a separate list).
    con.execute(f"CREATE OR REPLACE TABLE {inventory_fqn} (\n"
                "    table_catalog VARCHAR,\n"
                "    table_schema VARCHAR,\n"
                "    table_name VARCHAR,\n"
                "    row_count BIGINT,\n"
                "    bytes BIGINT,\n"
                "    table_type VARCHAR,\n"
                "    selected BOOLEAN,\n"
                "    discovered_at TIMESTAMPTZ\n"
                ")")

    if not rows:
        print(f"discover: wrote empty inventory to {inventory_fqn}")
        return

    row_sql = "(?, ?, ?, ?, ?, ?, ?, now())"
    insert_params: list = []
    for table_catalog, table_schema, table_name, row_count, num_bytes, table_type in rows:
        selected = str(table_type).upper() == "BASE TABLE"
        insert_params.extend([
            table_catalog, table_schema, table_name, row_count, num_bytes, table_type, selected,
        ])
    con.execute(
        f"INSERT INTO {inventory_fqn} "
        "(table_catalog, table_schema, table_name, row_count, bytes, table_type, selected, discovered_at) "
        "VALUES " + ", ".join([row_sql] * len(rows)),
        insert_params,
    )
    n_selected = sum(1 for r in rows if str(r[5]).upper() == "BASE TABLE")
    print(
        f"discover: wrote {len(rows)} row(s) to {inventory_fqn} "
        f"({n_selected} pre-selected as BASE TABLE). "
        "Edit the `selected` column to choose what MODE=move copies."
    )


def move(
    con: duckdb.DuckDBPyConnection,
    sf_conn_kwargs: dict,
    inventory_fqn: str,
    ledger_fqn: str,
    target_db: str,
    target_schema: str,
    *,
    dry_run: bool,
    max_rows: int,
) -> None:
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {ledger_fqn} (
            run_at TIMESTAMPTZ,
            source_table VARCHAR,
            dest_table VARCHAR,
            row_count BIGINT,
            status VARCHAR,
            detail VARCHAR,
            dry_run BOOLEAN
        )
        """
    )

    selected = con.execute(
        f"SELECT table_catalog, table_schema, table_name "
        f"FROM {inventory_fqn} WHERE selected ORDER BY table_schema, table_name"
    ).fetchall()

    label = "dry-run" if dry_run else "live"
    print(f"move: {len(selected)} selected table(s) in {label} mode")

    for table_catalog, table_schema, table_name in selected:
        source_table = f"{table_catalog}.{table_schema}.{table_name}"
        dest_table = f"{target_db}.{target_schema}.{table_name}"

        if dry_run:
            print(f"[dry-run] would copy {source_table} -> {dest_table}")
            record_move(con, ledger_fqn, source_table, dest_table, None, "skipped", "dry-run", dry_run=True)
            continue

        try:
            # The Snowflake-side names come from the inventory, which is built
            # from validated INFORMATION_SCHEMA rows, but quote each part anyway
            # before interpolating into the Snowflake SELECT.
            src_sql = ".".join(ident(part) for part in (table_catalog, table_schema, table_name))
            select_sql = f"SELECT * FROM {src_sql}"
            if max_rows > 0:
                select_sql += f" LIMIT {int(max_rows)}"

            # Bulk columnar pull: fetch the whole result as one Arrow table rather
            # than row-by-row. For very large tables this materializes in memory
            # (~16GB ceiling on a Flight); the README "Caveats" covers staging to
            # local Parquet on /tmp (~150GB scratch) as the alternative.
            arrow_table = snowflake_fetch_arrow(sf_conn_kwargs, select_sql)
            row_count = arrow_table.num_rows

            # Register the Arrow table with DuckDB and CTAS into MotherDuck. CREATE
            # OR REPLACE makes each table move idempotent: a re-run overwrites.
            con.register("sf_arrow_obj", arrow_table)
            try:
                con.execute(
                    f"CREATE OR REPLACE TABLE {ident(target_db)}.{ident(target_schema)}.{ident(table_name)} "
                    "AS SELECT * FROM sf_arrow_obj"
                )
            finally:
                con.unregister("sf_arrow_obj")

            print(f"moved {source_table} -> {dest_table} ({row_count} rows)")
            record_move(con, ledger_fqn, source_table, dest_table, row_count, "moved", "", dry_run=False)
        except Exception as exc:
            # One bad table should not abort the rest of the move. Record the
            # reason in the ledger and continue.
            detail = str(exc).splitlines()[0]
            print(f"failed {source_table}: {detail}")
            record_move(con, ledger_fqn, source_table, dest_table, None, "error", detail, dry_run=False)

    print(f"move: complete ({label})")


def record_move(
    con: duckdb.DuckDBPyConnection,
    ledger_fqn: str,
    source_table: str,
    dest_table: str,
    row_count: int | None,
    status: str,
    detail: str,
    *,
    dry_run: bool,
) -> None:
    # All data values are bound parameters, never string-formatted into SQL.
    con.execute(
        f"INSERT INTO {ledger_fqn} "
        "(run_at, source_table, dest_table, row_count, status, detail, dry_run) "
        "VALUES (now(), ?, ?, ?, ?, ?, ?)",
        [source_table, dest_table, row_count, status, detail, dry_run],
    )


def snowflake_connect_kwargs(source_database: str, source_schema: str) -> dict:
    # Non-secret connection params come from config/env; the user and password come
    # from a MotherDuck secret (see resolve_secret_param). account and user are required.
    account = env("SNOWFLAKE_ACCOUNT", "")
    # user and password can both live in one TYPE flights secret, so resolve each
    # from the bare env var (local) or the secret-injected `<secret_name>_<PARAM>`
    # form (deployed). account is not a credential, so it stays in plain config.
    user = resolve_secret_param("SNOWFLAKE_USER")
    if not account or not user:
        raise ValueError(
            "SNOWFLAKE_ACCOUNT (config) and SNOWFLAKE_USER (config or a TYPE flights "
            "secret param) are both required"
        )

    password = resolve_secret_param("SNOWFLAKE_PASSWORD")
    if not password:
        raise ValueError(
            "No Snowflake password found. Set SNOWFLAKE_PASSWORD locally, or deploy "
            "a TYPE flights secret whose param arrives as <secret_name>_SNOWFLAKE_PASSWORD."
        )

    kwargs: dict = {
        "account": account,
        "user": user,
        "password": password,
    }
    # Only pin a default database when scanning a single one; an account-wide scan
    # (empty source_database) lists databases with SHOW instead.
    if source_database:
        kwargs["database"] = source_database
    warehouse = env("SNOWFLAKE_WAREHOUSE", "")
    if warehouse:
        kwargs["warehouse"] = warehouse
    role = env("SNOWFLAKE_ROLE", "")
    if role:
        kwargs["role"] = role
    if source_schema:
        kwargs["schema"] = source_schema
    return kwargs


def resolve_secret_param(param: str) -> str:
    # Resolve a connection param that may come from a MotherDuck `TYPE flights`
    # secret. A local run can set the bare env var (e.g. SNOWFLAKE_PASSWORD).
    # Deployed as a Flight, the secret injects each param under the env var
    # `<secret_name>_<PARAM>`, NOT the bare name: the (lowercased) secret name
    # becomes a prefix, so a secret `snowflake_migration_test` with a
    # SNOWFLAKE_PASSWORD param arrives as
    # `snowflake_migration_test_SNOWFLAKE_PASSWORD`. Accept both: the exact name
    # first (local), then any var ending in `_<PARAM>` (the secret, whatever you
    # named it). Both SNOWFLAKE_USER and SNOWFLAKE_PASSWORD can be stored this way.
    # Mirrors resolve_webhook() in flight-freshness-alert.
    direct = os.environ.get(param, "").strip()
    if direct:
        return direct
    suffix = f"_{param}"
    for key, value in os.environ.items():
        if key.endswith(suffix) and value.strip():
            return value.strip()
    return ""


def fetch_inventory_rows(sf_conn_kwargs: dict, source_database: str, source_schema: str) -> list:
    # Return (catalog, schema, table, row_count, bytes, table_type) rows. With
    # source_database set, scan just that database's INFORMATION_SCHEMA; with it
    # empty, enumerate every database the connection can see (SHOW TERSE DATABASES)
    # and scan each, skipping any the role cannot read. One connection for the whole
    # pass. Names from SHOW are quoted with ident() (they carry their stored case);
    # a config-supplied single database is a validated identifier left unquoted so
    # Snowflake resolves it case-insensitively.
    conn = snowflake.connector.connect(**sf_conn_kwargs)
    try:
        cur = conn.cursor()
        try:
            from_show = not source_database
            if from_show:
                cur.execute("SHOW TERSE DATABASES")
                name_idx = column_index(cur.description, "name")
                databases = [r[name_idx] for r in cur.fetchall()]
                print(f"discover: {len(databases)} database(s) visible: {', '.join(databases)}")
            else:
                databases = [source_database]

            rows: list = []
            for db in databases:
                db_ref = ident(db) if from_show else db
                where = "table_schema NOT IN ('INFORMATION_SCHEMA')"
                params: list = []
                if source_schema:
                    where += " AND table_schema = %s"
                    params.append(source_schema.upper())
                query = (
                    "SELECT table_catalog, table_schema, table_name, row_count, bytes, table_type "
                    f"FROM {db_ref}.INFORMATION_SCHEMA.TABLES "
                    f"WHERE {where} "
                    "ORDER BY table_schema, table_name"
                )
                try:
                    cur.execute(query, params or None)
                    db_rows = cur.fetchall()
                    rows.extend(db_rows)
                    if from_show:
                        print(f"discover: {db}: {len(db_rows)} table(s)")
                except snowflake.connector.errors.Error as exc:
                    print(f"discover: skip database {db}: {str(exc).splitlines()[0]}")
            return rows
        finally:
            cur.close()
    finally:
        conn.close()


def column_index(description, name: str) -> int:
    # Find a column position in a Snowflake cursor description by name (SHOW output
    # column order is not guaranteed across versions).
    target = name.upper()
    for i, col in enumerate(description):
        col_name = col.name if hasattr(col, "name") else col[0]
        if str(col_name).upper() == target:
            return i
    raise ValueError(f"column {name!r} not found in result columns")


def snowflake_fetch_arrow(sf_conn_kwargs: dict, query: str):
    # snowflake-connector-python[pandas] ships pyarrow and exposes
    # cursor.fetch_arrow_all(), which returns a pyarrow.Table built from the
    # result's native Arrow batches: the recommended columnar path into DuckDB.
    conn = snowflake.connector.connect(**sf_conn_kwargs)
    try:
        cur = conn.cursor()
        try:
            cur.execute(query)
            # force_return_table=True returns an empty pyarrow.Table (with the
            # result schema) instead of None when there are no rows, so an empty
            # source table still moves as an empty destination table, with a row
            # count of 0, rather than raising on `.num_rows`.
            return cur.fetch_arrow_all(force_return_table=True)
        finally:
            cur.close()
    finally:
        conn.close()


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def validate_identifier(name: str, value: str) -> str:
    # Config-supplied database, schema, and table names flow into SQL that cannot
    # be parameterized, so reject anything that is not a plain SQL identifier
    # before any SQL runs. Per-table identifiers read from the inventory at
    # runtime are quoted with ident() instead.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def ident(value: str) -> str:
    # Quote a dynamic SQL identifier by escaping embedded double quotes.
    return '"' + value.replace('"', '""') + '"'


if __name__ == "__main__":
    main()
