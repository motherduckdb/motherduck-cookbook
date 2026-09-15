import os
import re

import dlt
import duckdb
import httpx
from dlt.common.configuration.container import Container
from dlt.extract.incremental.context import TimeIntervalContext

from sources.shopify_dlt import shopify_source


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SHOPIFY_RESOURCES = ("products", "orders", "customers")


def get_access_token(shop_url: str, client_id: str, client_secret: str) -> str:
    # A Dev Dashboard app exposes a client ID and secret, not a permanent Admin
    # API token. The client credentials grant trades them for a token that lives
    # 24 hours, which suits a Flight: each run mints its own and finishes well
    # inside that window, so no token is ever stored.
    response = httpx.post(
        f"{shop_url}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    if response.status_code != 200:
        # Surface Shopify's own message. `shop_not_permitted` means the app and
        # the store sit in different Shopify organizations, which this grant
        # does not support; use the authorization code grant for that case.
        raise RuntimeError(
            f"Shopify token exchange failed: HTTP {response.status_code}: {response.text[:400]}"
        )
    payload = response.json()
    # Log the granted scopes, never the token. A short load that returns no rows
    # is usually a missing scope rather than an empty store.
    print(f"granted scopes: {payload.get('scope')!r}, expires_in={payload.get('expires_in')}")
    return payload["access_token"]


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code. Only SHOP_URL and the
    # credential secret are required.
    shop_url = env("SHOP_URL", "").rstrip("/")
    if not shop_url:
        raise ValueError(
            "SHOP_URL is required, for example https://your-store.myshopify.com. "
            "Use the canonical .myshopify.com host, not a custom storefront domain."
        )
    database = validate_identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "shopify"))
    dataset_name = env("DATASET_NAME", "shopify_raw")
    pipeline_name = env("PIPELINE_NAME", "flights_shopify_ingest")
    api_version = env("API_VERSION", "2026-04")
    start_date = env("START_DATE", "2024-01-01")
    ledger_table = validate_identifier("RUN_LEDGER_TABLE", env("RUN_LEDGER_TABLE", "shopify_ingest_runs"))
    resources = [
        name.strip()
        for name in env("RESOURCES", ",".join(SHOPIFY_RESOURCES)).split(",")
        if name.strip()
    ]
    unknown = sorted(set(resources) - set(SHOPIFY_RESOURCES))
    if unknown:
        raise ValueError(
            f"RESOURCES contains {unknown}; the Shopify source only provides {list(SHOPIFY_RESOURCES)}"
        )

    # dlt writes working files under HOME; a Flight has a writable /tmp.
    os.environ.setdefault("HOME", "/tmp")
    # Point the dlt MotherDuck destination at our database. The injected
    # MOTHERDUCK_TOKEN supplies the credential, so no token appears here.
    os.environ["DESTINATION__MOTHERDUCK__CREDENTIALS__DATABASE"] = database

    # Create the destination database so dlt has a catalog to build the dataset
    # in; dlt creates the dataset (schema) and tables, but not the database.
    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")

    # Every shopify_dlt resource declares allow_external_schedulers=True, which
    # tells dlt to take its load window from an orchestrator rather than its own
    # state. Despite the name dlt treats it as a requirement: outside Airflow,
    # and without a DLT_INTERVAL_START/DLT_INTERVAL_END pair, the run fails with
    # ExternalSchedulerNotAvailable. This override switches it off for every
    # resource at once and leaves dlt's incremental state in charge. Prefer it
    # over re-declaring each cursor, which would drop the source's end_value.
    Container()[TimeIntervalContext] = TimeIntervalContext(allow_external_schedulers=False)

    access_token = get_access_token(
        shop_url,
        credential("CLIENT_ID"),
        credential("CLIENT_SECRET"),
    )

    pipeline = dlt.pipeline(
        pipeline_name=pipeline_name,
        destination="motherduck",
        dataset_name=dataset_name,
    )
    source = shopify_source(
        private_app_password=access_token,
        shop_url=shop_url,
        start_date=start_date,
        api_version=api_version,
    ).with_resources(*resources)

    load_info = pipeline.run(
        source,
        # Prefer Parquet loader files over row-wise insert_values so larger
        # stores stay on a bulk-loading path. Keep this unless you have measured
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
            resources VARCHAR,
            load_summary VARCHAR
        )
        """
    )
    con.execute(
        f"INSERT INTO {database}.main.{ledger_table} VALUES (current_timestamp, ?, ?, ?, ?)",
        [pipeline_name, dataset_name, ",".join(resources), str(load_info)],
    )
    con.close()
    print(load_info)


def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def credential(key: str) -> str:
    # A Flight secret injects each key as `<secret_name>_<KEY>` and, when safe,
    # as the bare `<KEY>`. Prefer the namespaced form so two secrets defining the
    # same key stay distinct, then fall back to the bare alias.
    secret_name = env("SHOPIFY_SECRET_NAME", "shopify")
    for candidate in (f"{secret_name}_{key}", key):
        value = os.environ.get(candidate, "").strip()
        if value:
            return value
    raise KeyError(
        f"Missing Shopify {key}. Expected {secret_name}_{key} or {key} from a "
        f"TYPE flights secret named {secret_name!r}."
    )


def validate_identifier(name: str, value: str) -> str:
    # The database and ledger table names flow into CREATE/INSERT statements that
    # cannot be parameterized, so reject anything that is not a plain SQL
    # identifier before any SQL runs.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


if __name__ == "__main__":
    main()
