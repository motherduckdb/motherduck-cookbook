---
title: Ingest Shopify Orders and Products as a Flight
id: flight-shopify-ingest
description: >-
  A reusable Flight that loads Shopify orders, customers, and products into
  MotherDuck on a schedule with dlt's Shopify source, minting a short-lived Admin
  API token from Dev Dashboard client credentials on every run. Use when you want
  scheduled Shopify ingestion without storing a long-lived token.
type: template
category: ingestion
features: [flights]
tags: [dlt, shopify, ingest]
prompt: >-
  I want my Shopify orders, customers, and products loaded into MotherDuck on a
  schedule, without storing a long-lived Admin API token anywhere. Help me adapt
  the "Ingest Shopify Orders and Products as a Flight" recipe to my own data and
  use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-shopify-ingest
published_date: 2026-08-27
---

# Ingest Shopify Orders and Products as a Flight

A single-file Flight that loads Shopify into MotherDuck with
[dlt's Shopify source](https://dlthub.com/docs/dlt-ecosystem/verified-sources/shopify).
It shows two MotherDuck patterns worth stealing: running a dlt **verified source**
inside a Flight even though `source_code` is a single file, and holding only OAuth
client credentials in a secret while each run mints its own short-lived API token.

Everything is driven by Flight config, so you adapt it by setting config values
rather than editing code.

## How it works

`flight.py` runs a fixed sequence:

1. **Read config.** `SHOP_URL` is required; database, dataset, resources,
   `API_VERSION`, and `START_DATE` all have defaults.
2. **Create the destination database.** dlt runs `ATTACH IF NOT EXISTS` and
   `CREATE SCHEMA IF NOT EXISTS`, but never creates the database, so the Flight
   does that first. Skipping it fails the load with a catalog error.
3. **Disable dlt's external-scheduler requirement.** See Caveats: without this
   the source refuses to run outside Airflow.
4. **Mint an Admin API token.** A `client_credentials` grant against
   `{shop_url}/admin/oauth/access_token` returns a token valid for 24 hours. The
   granted scopes are logged; the token never is.
5. **Load with dlt.** `shopify_source(...)` with the selected resources, written
   through Parquet loader files. Each resource merges on `id` and loads
   incrementally on `updated_at`, so re-runs don't duplicate rows.
6. **Record the run.** One row per run in `RUN_LEDGER_TABLE` with the resources
   loaded and dlt's load package summary.

The verified source can't be pasted into a Flight, because `dlt init shopify_dlt`
scaffolds several modules while a Flight is one file. Instead `requirements.txt`
installs the `verified-sources` repository straight from a pinned GitHub archive,
which makes the connector importable as `sources.shopify_dlt`.

## Questions to answer

- What is the store's canonical `.myshopify.com` URL? A custom storefront domain
  does not serve the Admin API.
- Which resources are needed: `products`, `orders`, `customers`, or a subset?
  Grant only the matching read scopes.
- Are the app and the store in the **same** Shopify organization? The client
  credentials grant requires it.
- Which MotherDuck database and dataset should receive the tables?
- How far back should the first load reach, and does the app hold
  `read_all_orders` if that is more than 60 days?
- What schedule (cron, UTC) matches how often the store changes?

## Caveats

- **The source requires an external scheduler unless you turn that off.** Every
  `shopify_dlt` resource sets `allow_external_schedulers=True`, which tells dlt to
  take its load window from an orchestrator. Despite the name dlt treats it as a
  requirement, so outside Airflow, and with no `DLT_INTERVAL_START`/`DLT_INTERVAL_END`
  pair, the run fails with `ExternalSchedulerNotAvailable`. The Flight overrides
  `TimeIntervalContext` to switch it off for all resources at once. Re-declaring
  each resource's cursor also works but replaces it wholesale and silently drops
  the source's `end_value`, so a bounded backfill would lose its upper bound.
- **Orders are capped at 60 days without `read_all_orders`.** A Shopify scope
  restriction, not a dlt one, and it fails quietly by returning fewer rows.
- **The default `api_version` ages out.** The source itself defaults to `2023-10`
  and Shopify removes versions roughly a year after release, so this template
  pins `API_VERSION` and you should revisit it when you move the source pin.
- **Legacy custom apps are gone.** Shopify stopped allowing new admin-created
  custom apps on 2026-01-01, so there is no permanent `shpat_` token to paste for
  a new setup. Existing tokens still work: pass one through a secret param named
  `SOURCES__SHOPIFY_DLT__PRIVATE_APP_PASSWORD` and skip the exchange.
- **Only three resources exist.** Inventory, fulfillments, discounts, and payouts
  are not covered. Use dlt's REST API source for those.
- **Money fields arrive as strings.** Shopify returns decimal strings, so cast
  before doing arithmetic.
- **Pin the source archive.** The `verified-sources` repository is not on PyPI and
  its tags trail the default branch. A Flight reinstalls dependencies on every
  run, so an unpinned `master.tar.gz` could change the connector between runs of
  a Flight nobody touched.
- **The install is heavy.** The package depends on `dlt[bigquery, duckdb]`, so
  every run pulls the Google Cloud libraries too.

## What you'll adjust

No code edits are required. Everything is read from Flight config/env, plus a
MotherDuck Flights secret holding the OAuth client credentials.

| Knob | Default | Purpose |
|---|---|---|
| `SHOP_URL` | (required) | Canonical store URL, `https://<store>.myshopify.com`. |
| `RESOURCES` | `products,orders,customers` | Comma-separated subset to load. |
| `DESTINATION_DATABASE` | `shopify` | MotherDuck database, created if absent. |
| `DATASET_NAME` | `shopify_raw` | dlt dataset (schema) holding the tables. |
| `PIPELINE_NAME` | `flights_shopify_ingest` | dlt pipeline name, which owns incremental state. |
| `API_VERSION` | `2026-04` | Admin API version. Use a currently supported one. |
| `START_DATE` | `2024-01-01` | Lower bound for the first incremental load. |
| `RUN_LEDGER_TABLE` | `shopify_ingest_runs` | Ledger table in `<database>.main`; validated as an identifier. |
| `SHOPIFY_SECRET_NAME` | `shopify` | Name of the Flights secret to read credentials from. |
| `shopify` **secret** | (required) | `TYPE flights` secret with params `CLIENT_ID` and `CLIENT_SECRET`. |

Credentials are read from `<secret_name>_CLIENT_ID` and `<secret_name>_CLIENT_SECRET`,
falling back to the bare `CLIENT_ID` / `CLIENT_SECRET` aliases.

## Run it

You need a MotherDuck account and token, plus a Shopify Dev Dashboard app with
**Custom distribution** installed on your store. Create the app under **Apps** in
the [Dev Dashboard](https://dev.shopify.com/dashboard), add the read scopes you
need on an app version, release it, install it on the store, then copy the Client
ID and Secret from **App settings**.

Start with `products` only: it is the one resource that is not protected customer
data, so it isolates credential problems from scope problems.

```bash
export MOTHERDUCK_TOKEN=your_token_here
export CLIENT_ID=your_client_id
export CLIENT_SECRET=your_client_secret
SHOP_URL=https://your-store.myshopify.com \
RESOURCES=products \
DESTINATION_DATABASE=shopify_test \
  uv run --with-requirements requirements.txt flight.py
```

The run logs the granted scopes, then dlt's load summary. If a resource comes back
empty, check the logged scopes first.

### Deploy as a Flight

Store the client credentials as a **Flights secret** named `shopify` (UI:
[Settings > Secrets](https://app.motherduck.com/settings/secrets), type
**Flights**, params `CLIENT_ID` and `CLIENT_SECRET`). Or with SQL from a
write-enabled connection, since read-only connections reject `CREATE SECRET`:

```sql
CREATE SECRET shopify IN motherduck (
  TYPE flights,
  PARAMS MAP {
    'CLIENT_ID': 'your_client_id',
    'CLIENT_SECRET': 'your_client_secret'
  }
);
```

To keep the literal secret out of SQL and shell history, run that from the
**duckdb CLI** with the values in env vars, where `getenv()` resolves
client-side:

```sql
CREATE SECRET shopify IN motherduck (
  TYPE flights,
  PARAMS MAP {
    'CLIENT_ID': getenv('SHOPIFY_CLIENT_ID'),
    'CLIENT_SECRET': getenv('SHOPIFY_CLIENT_SECRET')
  }
);
```

Create the secret **before** the Flight: `MD_CREATE_FLIGHT` rejects an unknown
name with `user_secret not found`.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments), passing:

- `name`: a Flight name, for example `shopify-ingest`
- `source_code`: `flight.py`
- `requirements_txt`: `requirements.txt`
- `flight_secret_names`: `["shopify"]` so the client credentials reach the run
- `config`: at least `SHOP_URL`, plus any other knobs above

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create without a schedule, run once with `MD_RUN_FLIGHT(flight_id := ...)` (the id
is returned by `MD_CREATE_FLIGHT` and listed by `MD_FLIGHTS()`), and confirm the
dataset has tables and the ledger has a new row. Then add a schedule with
`MD_UPDATE_FLIGHT`. A daily cron suits most stores.

Verify a load, joining orders to their line items through dlt's parent/child keys:

```sql
SELECT
    items.title,
    sum(items.quantity) AS units,
    sum(items.quantity * items.price::DECIMAL(12, 2)) AS revenue
FROM shopify.shopify_raw.orders AS orders
JOIN shopify.shopify_raw.orders__line_items AS items
    ON items._dlt_parent_id = orders._dlt_id
WHERE orders.created_at >= current_date - INTERVAL 30 DAY
GROUP BY ALL
ORDER BY revenue DESC
LIMIT 20;
```

## Security

- **No long-lived API token anywhere.** The secret holds only the OAuth client ID
  and secret. Each run exchanges them for a token that expires in 24 hours, and
  the token is never written to config, a table, or the log.
- **Keep the literal secret out of history.** Prefer the duckdb-CLI `getenv()`
  form above, or the Settings UI, so the raw value is never typed into SQL text.
- **Least privilege.** Grant only the read scopes matching the resources you
  load. The run logs the scopes it received so you can confirm the app is not
  over-permissioned.
- **Rotate on exposure.** Shopify's App settings page has a **Rotate** action for
  the client secret; update the Flights secret afterwards.
- **Validated SQL identifiers.** `DESTINATION_DATABASE` and `RUN_LEDGER_TABLE`
  reach `CREATE`/`INSERT` statements that cannot be parameterized, so both are
  checked as plain identifiers before any SQL runs.
- **Protected customer data.** `orders` and `customers` are protected customer
  data. Custom apps have both access levels available without Shopify review,
  unlike public apps, which is a reason to prefer Custom distribution here.

## Learn more

- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
- MotherDuck's Shopify integration page, which covers the same credential flow
  with screenshots: <https://motherduck.com/docs/integrations/ingestion/shopify>
- dlt Shopify source reference:
  <https://dlthub.com/docs/dlt-ecosystem/verified-sources/shopify>
- Shopify client credentials grant:
  <https://shopify.dev/docs/apps/build/authentication-authorization/client-credentials-grant>
- Shopify Admin API versioning: <https://shopify.dev/docs/api/usage/versioning>
- Files: `flight.py` (the Flight source), `requirements.txt` (`duckdb`, `dlt`
  with the MotherDuck destination, `httpx` for the token exchange, and the pinned
  `verified-sources` archive that provides `sources.shopify_dlt`).
