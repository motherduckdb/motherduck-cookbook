---
title: "Flight recipe: Custom tool or API → MotherDuck (start from scratch)"
id: flight-http-api-ingest
description: >-
  A starter Flight for any tool or API without its own recipe: it pulls JSON
  records over HTTP (optional key, page or Link-header pagination), lands them in
  a MotherDuck table, adds new fields as columns and upserts on an id. Use it when
  the source is a custom tool or SaaS API and you need to work out how to connect,
  sync and schedule it.
type: template
category: ingestion
features: [flights]
tags: [python]
prompt: >-
  I want to bring data from another tool or API into MotherDuck. Help me adapt the
  "Flight recipe: Custom tool or API → MotherDuck (start from scratch)" recipe to
  my own data and use case, using it as a guide. Ask me a few simple questions
  about what it is and how to connect to it, then tell me your plan before you
  build anything. If it needs a key, I'll put it in a MotherDuck secret, not in
  chat: https://motherduck.com/docs/cookbook/flight-http-api-ingest
published_date: 2026-10-20
---

# Flight recipe: Custom tool or API → MotherDuck (start from scratch)

For anything without its own recipe: an internal tool, a SaaS API, a file drop.
First work out what the thing is and how to reach it, then build a Flight that
pulls its data into MotherDuck and keeps it current on a schedule. The starter
[`flight.py`](flight.py) covers the common case, a JSON-over-HTTP API with an
optional key, pagination and an upsert key, using only the Python standard
library plus `duckdb`. Adapt `fetch_records()` when an API needs more.

## How it works

1. **Fetch** every page from `API_URL` (up to `MAX_PAGES`), attaching the key from
   the Flights secret the way the API expects (`Authorization: Bearer`, a named
   header, or a query parameter). Requests retry on `429` and `5xx`, honouring
   `Retry-After`. A key in the query string is masked in every log line.
2. **Find the records** at `RECORDS_PATH` (a dot path such as `data.items`; empty
   means the response is the list).
3. **Land** them in `/tmp` as JSON lines and bulk-load with `read_json_auto`, so
   nested JSON becomes `STRUCT`s and lists.
4. **Evolve the table:** fields the API adds become new columns automatically.
5. **Upsert or append:** with `PRIMARY_KEY`, rows with the same key are replaced
   (delete, then `INSERT ... BY NAME`); without it, every run appends.

## Questions to answer

Work these out in plain language, one short message at a time, skipping anything
already known. The goal is to learn enough to build the Flight, not a quiz.

- **What is it?** Which tool or system, and what should happen with its data? If
  it has its own recipe (Postgres, Render, Heroku, Neon, Snowflake, BigQuery, S3,
  dbt), use that one instead.
- **How is it reached?** An API, a database connection, an export (CSV, JSON,
  Parquet), or webhooks? Find its docs if there are any.
- **How does it sign in?** If it needs an API key or token, store it in a
  MotherDuck Flights secret (key `API_KEY`), never in chat, source or config.
  Check how the API expects it: a bearer header, a named header, or a query
  parameter.
- **What data?** Which endpoint or objects, roughly how much, and whether records
  have an id (for updates) or only ever get added.
- **How fresh?** Once, or on a schedule (how often)?

Then state in one or two sentences what the Flight will do (for example: "Every
hour, pull new tickets from the Help Desk API into `helpdesk.main.tickets`,
updating changed ones by id") and get a go-ahead before building it.

## Caveats

- **Try the API before writing the Flight.** Fetch one page (a tiny test Flight,
  or `read_json_auto('https://...')` for a public endpoint) to learn where the
  records sit, the id field and how pagination works.
- **Beyond JSON-over-HTTP** (cursor pagination, POST queries, OAuth, a database
  driver, file exports): edit `fetch_records()` or write a new Flight with the
  same shape: fetch, land in `/tmp` as JSON or Parquet, bulk-load, upsert or
  replace. Pin any client library in `requirements.txt`.
- **Webhooks** (the tool pushes instead of being polled) aren't a Flight's job:
  poll the tool's API instead, or point the webhook at a service that writes to
  MotherDuck.
- **Big histories:** for APIs with an "updated since" filter, put it in `API_URL`
  for scheduled runs so each run fetches only recent changes; the upsert keeps
  the table correct.
- **Per-run overrides need stored keys.** `MD_RUN_FLIGHT` (or the MCP
  `run_flight` tool) can only override config keys already stored on the Flight.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `HTTP 401` / `403` | Wrong or missing key, or the wrong `AUTH` style for this API |
| `RECORDS_PATH ... does not point at a list of records` | Look at one response and set the dot path to the list |
| `stopped at MAX_PAGES` | Raise `MAX_PAGES`, or narrow `API_URL` (e.g. an updated-since filter) |
| Duplicate rows | Set `PRIMARY_KEY` to the API's id field |
| `Binder Error` on `INSERT ... BY NAME` | A field changed type between runs; cast it in a custom `fetch_records()` or recreate the table |

## What you'll adjust

| Knob | Default | Purpose |
|---|---|---|
| `API_URL` | (required) | Endpoint returning JSON |
| `RECORDS_PATH` | `""` | Dot path to the list of records (`""` = the response is the list) |
| `AUTH` | `bearer` if a key is set, else `none` | `none`, `bearer`, `header:<Header-Name>` or `query:<param>` |
| `PAGINATION` | `none` | `page` (`?<PAGE_PARAM>=1,2,…` until an empty page) or `link` (follow `Link: rel="next"`) |
| `PAGE_PARAM` / `MAX_PAGES` | `page` / `100` | Page parameter name and a safety cap |
| `PRIMARY_KEY` | `""` | Column to upsert on; empty = append every run |
| `TARGET_DB` / `TARGET_TABLE` | `api_sync` / `records` | Destination (database created if missing) |
| `SECRET_NAME` | `api` | Flights secret holding `API_KEY` |
| `MOTHERDUCK_HOST` | unset | Override the MotherDuck host (e.g. non-prod). Leave unset for default. |

## Run it

```bash
export MOTHERDUCK_TOKEN=your_token_here
export API_URL='https://api.github.com/repos/duckdb/duckdb/releases?per_page=30'
export PAGINATION=link PRIMARY_KEY=id TARGET_DB=api_sync TARGET_TABLE=duckdb_releases
# export API_KEY=...   # only if the API needs one
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

If the API needs a key, first store it as a **Flights secret** named `api` with
key `API_KEY` (UI: [Settings > Secrets](https://app.motherduck.com/settings/secrets),
type **Flights**; the prefilled link
`https://app.motherduck.com/settings/secrets?action=create&type=flights&name=api&params=API_KEY`
carries only the key name).

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function, passing:

- `name`: for example `helpdesk_tickets_sync`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["api"]` if a key is needed
- `config`: `API_URL`, `RECORDS_PATH`, `AUTH`, `PAGINATION`, `PRIMARY_KEY`, `TARGET_DB`, `TARGET_TABLE`

A MotherDuck token is attached automatically and injected as `MOTHERDUCK_TOKEN`.
Run once with `MD_RUN_FLIGHT(flight_id := ...)`, read the log (one line per page
and a row count), check `SELECT * FROM <TARGET_DB>.main.<TARGET_TABLE> LIMIT 10`,
then add a schedule if one is wanted.

## Security

- **Keys in a secret, never config or code.** `API_KEY` is read from a
  `TYPE flights` secret (namespaced `<secret>_API_KEY` first, then bare).
- **No key in logs.** With `AUTH=query:<param>`, the parameter is masked in every
  logged URL; header keys are never logged.
- **Quoted identifiers.** `TARGET_DB`, `TARGET_TABLE`, `PRIMARY_KEY` and new
  column names are quoted before they reach SQL.

## Status

Tested on MotherDuck staging (Oct 20, 2026) against a public API (GitHub releases
for duckdb/duckdb, `PAGINATION=link`, `PRIMARY_KEY=id`): 60 records over 2 pages
loaded with nested fields as typed `STRUCT`s, and a second run upserted the same
60 rows without duplicates. The key-in-header and key-in-query paths and the log
redaction were checked locally, not against a real authenticated API.

## Learn more

- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- DuckDB JSON loading: [duckdb.org/docs](https://duckdb.org/docs/stable/data/json/overview).
- Files: [`flight.py`](flight.py), [`requirements.txt`](requirements.txt) (`duckdb` only).
