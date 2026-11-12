---
title: "Flight recipe: Custom tool or API → MotherDuck with dlt"
id: flight-http-api-ingest
description: >-
  A starter Flight for any tool or API without its own recipe, built on dlt: use
  a dlt verified source if one exists, otherwise dlt's declarative rest_api source
  (auth, pagination, incremental cursor, merge on a primary key), and load through
  dlt's motherduck destination with schema evolution and retries. Use it when the
  source is a custom tool or SaaS API and you need to work out how to connect,
  sync and schedule it.
type: template
category: ingestion
features: [flights]
tags: [dlt, python]
prompt: >-
  I want to bring data from another tool or API into MotherDuck. Help me adapt the
  "Flight recipe: Custom tool or API → MotherDuck with dlt" recipe to my own data
  and use case, using it as a guide. Ask me a few simple questions about what it
  is and how to connect to it, check whether dlt already has a source for it, then
  tell me your plan before you build anything. If it needs a key, I'll put it in a
  MotherDuck secret, not in chat:
  https://motherduck.com/docs/cookbook/flight-http-api-ingest
published_date: 2026-10-20
---

# Flight recipe: Custom tool or API → MotherDuck with dlt

For anything without its own recipe: an internal tool, a SaaS API, a niche
service. First work out what the thing is and how to reach it, then build a
Flight that pulls its data into MotherDuck and keeps it current on a schedule.
[dlt](https://dlthub.com/docs) is the default engine: it already handles auth,
pagination, retries, incremental state, schema evolution and upserts, so the
Flight stays a short config-driven file. The starter [`flight.py`](flight.py)
runs dlt's declarative `rest_api` source into dlt's `motherduck` destination.

## Pick the source, in this order

1. **A dlt verified source exists for the tool.** Check the
   [verified sources list](https://dlthub.com/docs/dlt-ecosystem/verified-sources/)
   (GitHub, HubSpot, Jira, Zendesk, Stripe, Notion, Salesforce, ...). It already
   knows the endpoints, keys and incremental fields, so use it: pin the
   [`verified-sources`](https://github.com/dlt-hub/verified-sources) archive in
   `requirements.txt` and return its source from `build_source()` (the commented
   option (a) in `flight.py`). [flight-shopify-ingest](../flight-shopify-ingest)
   is a worked example of this pattern.
2. **Otherwise, a JSON-over-HTTP API: use [`rest_api`](https://dlthub.com/docs/dlt-ecosystem/verified-sources/rest_api/basic)**
   with a declarative config. This is what `flight.py` does by default: set
   `BASE_URL`, `RESOURCE`, `AUTH`, `PAGINATION`, `PRIMARY_KEY` and optionally an
   incremental cursor, no code changes. For things the config knobs don't cover
   (offset or cursor pagination, POST bodies, several related endpoints), extend
   the dict in `build_source()` with any `rest_api` option.
3. **Neither fits** (non-HTTP protocol, a client SDK, exotic auth or signing):
   write a custom [`@dlt.resource`](https://dlthub.com/docs/general-usage/resource)
   or `@dlt.source` that yields dicts (option (c) in `flight.py`). Keep the dlt
   pipeline and `motherduck` destination so you still get merge, schema evolution
   and state.

## How it works

1. **Secrets in, from env vars.** `API_KEY` comes from a Flights secret
   (namespaced `<secret>_API_KEY` first, then bare). Any secret param named in
   dlt's convention (`SOURCES__...`, `DESTINATION__...`) is exposed under its
   bare name so dlt resolves it, which is how a verified source gets its token.
2. **Destination.** The Flight runtime injects `MOTHERDUCK_TOKEN`, which dlt's
   `motherduck` destination reads directly. `flight.py` only sets
   `DESTINATION__MOTHERDUCK__CREDENTIALS__DATABASE` and runs
   `CREATE DATABASE IF NOT EXISTS`, because dlt creates the dataset and tables
   but not the database.
3. **Extract.** `rest_api` fetches every page from `BASE_URL/RESOURCE`, attaching
   the key as a bearer token, a named header or a query parameter. dlt's HTTP
   client retries `429`, `5xx` and connection errors with backoff, honouring
   `Retry-After`.
4. **Incremental.** With `INCREMENTAL_CURSOR`, dlt stores the highest cursor value
   in the destination (`_dlt_pipeline_state`). Each run, even on a fresh Flight
   machine, restores it and sends it as `INCREMENTAL_PARAM`, so only changes are
   fetched.
5. **Load.** dlt infers the schema, adds new fields as columns, writes Parquet
   loader files and loads into `<DESTINATION_DATABASE>.<DATASET_NAME>.<TABLE_NAME>`.
   With `PRIMARY_KEY` the write disposition is `merge`, so reruns upsert instead
   of duplicating; without it, every run appends.
6. **Report.** The log ends with rows loaded per table and dlt's load summary.
   Any failure exits non-zero with the API key masked out of the message.

## Questions to answer

Work these out in plain language, one short message at a time, skipping anything
already known. The goal is to learn enough to build the Flight, not a quiz.

- **What is it?** Which tool or system, and what should happen with its data? If
  it has its own recipe (Postgres, Shopify, HubSpot, Snowflake, BigQuery, S3,
  dbt), use that one instead.
- **Does dlt have a source for it?** Check the verified sources list before
  anything else ([Pick the source](#pick-the-source-in-this-order)).
- **How is it reached?** An HTTP API (find its docs), a database connection, an
  export (CSV, JSON, Parquet), or webhooks?
- **How does it sign in?** If it needs an API key or token, store it in a
  MotherDuck Flights secret, never in chat, source or config. Check how the API
  expects it: a bearer header, a named header, or a query parameter.
- **What data?** Which endpoint, where the records sit in the response, how it
  pages, roughly how much, whether records have an id (for upserts) and an
  "updated since" field or filter (for incremental runs).
- **How fresh?** Once, or on a schedule (how often)?

Then state in one or two sentences what the Flight will do (for example: "Every
hour, pull tickets changed since the last run from the Help Desk API with dlt's
rest_api source into `helpdesk.raw.tickets`, merging on `id`") and get a go-ahead
before building it.

## Caveats

- **Ask before setting up.** Agents following this recipe should work through
  [Questions to answer](#questions-to-answer) with the user first, and only then
  open the secret form (the MCP `create_secret` tool) or create the Flight.
- **Try the API before writing the Flight.** Fetch one page (a tiny test Flight,
  a local run with `MAX_PAGES=1`, or `read_json_auto('https://...')` for a public
  endpoint) to learn where the records sit, the id field and how pagination works.
- **Nested JSON becomes child tables.** dlt flattens nested objects into
  `parent__child` columns and lists into child tables such as `releases__assets`,
  linked by `_dlt_id` / `_dlt_parent_id`. To keep nested data as JSON columns,
  pass `max_table_nesting=0` on the resource or source.
- **`auto` pagination guesses.** It detects common patterns and logs what it
  chose. Set `PAGINATION` explicitly once you know the API. `page_number` stops at
  the first empty page; `MAX_PAGES` is a safety cap for APIs that ignore the page
  parameter.
- **Incremental without `INCREMENTAL_PARAM`** still fetches everything and only
  filters client-side; use the API's "updated since" filter when it has one.
- **Untyped empty columns are skipped.** Fields that are `null` on every record
  are not materialized (dlt warns) until a value arrives; that's expected.
- **Changing the pipeline shape** (table, primary key, dataset) keeps the old dlt
  state. Use a new `PIPELINE_NAME`, or drop the tables and the `_dlt_*` state
  tables, to start fresh.
- **Webhooks** (the tool pushes instead of being polled) aren't a Flight's job:
  poll the tool's API instead, or point the webhook at a service that writes to
  MotherDuck.
- **Per-run overrides need stored keys.** `MD_RUN_FLIGHT` (or the MCP
  `run_flight` tool) can only override config keys already stored on the Flight.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `401` / `403 Client Error` | Wrong or missing key, or the wrong `AUTH` style for this API |
| Records nested under a wrapper end up as one odd row | Set `RECORDS_PATH` (JSONPath, e.g. `data.items`) |
| Only the first page loads | Set `PAGINATION` explicitly (`header_link`, `page_number`, `json_link`) |
| Duplicate rows | Set `PRIMARY_KEY` to the API's id field (switches to `merge`) |
| Second run loads 0 rows | Working as intended with an incremental cursor; nothing changed |
| `Set config BASE_URL` / `RESOURCE` | Both are required |

## What you'll adjust

| Knob | Default | Purpose |
|---|---|---|
| `BASE_URL` | (required) | API root, e.g. `https://api.example.com/v1` |
| `RESOURCE` | (required) | Endpoint path under `BASE_URL`, e.g. `tickets` |
| `PARAMS` | `""` | Query string sent on every request, e.g. `per_page=100&state=all` |
| `RECORDS_PATH` | auto-detect | JSONPath to the records list, e.g. `data.items` |
| `AUTH` | `bearer` if a key is set, else `none` | `none`, `bearer`, `header:<Header-Name>` or `query:<param>` |
| `PAGINATION` | `auto` | `auto`, `single_page`, `page_number`, `header_link` (follow `Link: rel="next"`), `json_link` |
| `PAGE_PARAM` / `BASE_PAGE` | `page` / `1` | `page_number` only |
| `NEXT_URL_PATH` | `next` | `json_link` only: JSONPath to the next-page URL |
| `MAX_PAGES` | `1000` | Safety cap on pages per run (`0` = no cap) |
| `PRIMARY_KEY` | `""` | Upsert key; set = `merge`, empty = `append` |
| `INCREMENTAL_CURSOR` | `""` | Field that grows on change, e.g. `updated_at` |
| `INCREMENTAL_PARAM` / `INCREMENTAL_START` | `""` | Query param that receives the last cursor (e.g. `since`) and the first-run value |
| `DESTINATION_DATABASE` / `DATASET_NAME` / `TABLE_NAME` | `api_sync` / `raw` / last `RESOURCE` segment | Destination (database created if missing) |
| `PIPELINE_NAME` | `api_sync_<TABLE_NAME>` | dlt pipeline and state name; one per Flight |
| `SECRET_NAME` | `api` | Flights secret holding `API_KEY` (and any `SOURCES__...` params) |
| `MOTHERDUCK_HOST` | unset | Override the MotherDuck host (e.g. non-prod). Leave unset for default. |

`build_source()` in [`flight.py`](flight.py) is the one place to edit for a
verified source, a custom resource, or `rest_api` options beyond these knobs.

## Run it

```bash
export MOTHERDUCK_TOKEN=your_token_here
export BASE_URL=https://api.github.com RESOURCE=repos/duckdb/duckdb/releases PARAMS=per_page=30
export PAGINATION=header_link PRIMARY_KEY=id
# export API_KEY=...   # only if the API needs one
uv run --with-requirements requirements.txt flight.py
```

That loads `api_sync.raw.releases` (plus `releases__assets`). Run it again and the
row count stays the same: `merge` on `id` upserts. For an incremental sync, add
for example `INCREMENTAL_CURSOR=updated_at INCREMENTAL_PARAM=since
INCREMENTAL_START=2024-01-01T00:00:00Z`.

dlt's own env-var config conventions work too, for example
`DESTINATION__MOTHERDUCK__CREDENTIALS="md:?motherduck_token=..."` instead of
`MOTHERDUCK_TOKEN`, or `SOURCES__GITHUB__ACCESS_TOKEN` for the GitHub verified
source (see [dlt credentials setup](https://dlthub.com/docs/general-usage/credentials/setup)).
Keep secrets in env vars, not in code or `.dlt/secrets.toml` checked into git.

### Deploy as a Flight

If the API needs a key, first store it as a **Flights secret** named `api` with
key `API_KEY` (UI: [Settings > Secrets](https://app.motherduck.com/settings/secrets),
type **Flights**; the prefilled link
`https://app.motherduck.com/settings/secrets?action=create&type=flights&name=api&params=API_KEY`
carries only the key name). For a verified source, name the params the way dlt
expects them instead, for example `SOURCES__GITHUB__ACCESS_TOKEN`.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function, passing:

- `name`: for example `helpdesk_tickets_sync`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["api"]` if a key is needed
- `config`: `BASE_URL`, `RESOURCE`, `PARAMS`, `AUTH`, `PAGINATION`, `PRIMARY_KEY`,
  any `INCREMENTAL_*` keys, `DESTINATION_DATABASE`, `TABLE_NAME`

A MotherDuck token is attached automatically and injected as `MOTHERDUCK_TOKEN`.
Run once with `MD_RUN_FLIGHT(flight_id := ...)`, read the log (rows per table and
the dlt load summary), check
`SELECT * FROM <DESTINATION_DATABASE>.<DATASET_NAME>.<TABLE_NAME> LIMIT 10`, then
add a schedule if one is wanted.

## Security

- **Keys in a secret, never config or code.** `API_KEY` and any `SOURCES__...`
  credentials are read from a `TYPE flights` secret through env vars.
- **No key in logs.** dlt sanitizes request URLs in its logs, and `flight.py`
  masks the key in any error message before it is printed, including a key sent
  as a query parameter.
- **Identifiers.** `DESTINATION_DATABASE` is validated as a plain SQL identifier
  before `CREATE DATABASE`; dlt quotes dataset, table and column names itself.

## Status

Checked locally (Oct 26, 2026, dlt 1.31.0, duckdb 1.5.6) against the public
GitHub API with a local DuckDB destination standing in for MotherDuck:
`header_link` pagination loaded 60 releases and a rerun kept 60 distinct ids;
`page_number` with `INCREMENTAL_CURSOR=updated_at` / `INCREMENTAL_PARAM=since`
loaded 150 issues, then 0 on the next run; a failing request with
`AUTH=query:api_key` exited non-zero with the key masked. Not yet re-run on
MotherDuck after the switch to dlt.

## Learn more

- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- dlt: [verified sources](https://dlthub.com/docs/dlt-ecosystem/verified-sources/),
  [`rest_api` source](https://dlthub.com/docs/dlt-ecosystem/verified-sources/rest_api/basic),
  [resources](https://dlthub.com/docs/general-usage/resource),
  [incremental loading](https://dlthub.com/docs/general-usage/incremental-loading),
  [MotherDuck destination](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck).
- Related templates: [flight-dlt-ingest](../flight-dlt-ingest) (generic dlt
  pipeline with a run ledger), [flight-shopify-ingest](../flight-shopify-ingest)
  (a pinned verified source).
- Files: [`flight.py`](flight.py), [`requirements.txt`](requirements.txt)
  (`duckdb`, `dlt[motherduck]`).
