---
title: Ingest API Data into MotherDuck with Python
id: python-ingestion
description: >-
  Fetches JSON from an HTTP API and loads it into a MotherDuck table using the
  native DuckDB Python client, with a simple pandas path and a typed, chunked
  PyArrow path. Use when you need a standalone Python ingestion script that pulls
  from an API or in-memory dataframe and writes to MotherDuck.
type: example
category: ingestion
features: []
tags: [python, pyarrow, pandas]
---

# Ingest API Data into MotherDuck with Python

This example pulls contributor stats from the GitHub API and loads them into a
MotherDuck database from a plain Python process. It connects with the native
DuckDB Python client (`duckdb.connect()` then `ATTACH 'md:'`) and shows two
ingestion patterns: a quick pandas `CREATE TABLE AS SELECT` for small payloads,
and a typed PyArrow buffer that inserts in chunks for larger loads while avoiding
type-inference surprises. See the MotherDuck docs on
[loading data with Python](https://motherduck.com/docs/key-tasks/loading-data-into-motherduck/loading-data-md-python/)
for more background.

## How it works

Both scripts share the same shape: `fetch_github_data()` pulls JSON over HTTP,
`process_data()` reshapes it into rows, and `main()` opens a DuckDB connection,
attaches MotherDuck, and writes the table.

### Connecting to MotherDuck

The connection is the native DuckDB Python client, not a Postgres driver. A
local in-process DuckDB connection attaches your MotherDuck account:

```python
con = duckdb.connect()
con.sql("ATTACH 'md:'")
con.sql("CREATE DATABASE IF NOT EXISTS github")
```

`ATTACH 'md:'` reads the `motherduck_token` environment variable. `load_dotenv()`
runs at import time, so the token must be present in `.env` (or already exported)
before the script connects. After attaching, MotherDuck databases are addressed
as `<database>.<schema>.<table>`, defaulting to the `main` schema.

### Small payload: pandas CTAS

`load_to_motherduck_small.py` builds a pandas DataFrame and lets DuckDB scan it
directly by name with `CREATE TABLE AS SELECT`. This is the smallest viable path,
and column types are inferred from the DataFrame:

```python
df = process_data(data)  # pandas DataFrame with login, total_commits
con.sql("CREATE TABLE IF NOT EXISTS github.github_commits AS SELECT * FROM df")
```

### Larger payload: typed PyArrow buffer

`load_to_motherduck_large.py` defines `ArrowTableLoadingBuffer`. It pins an
explicit PyArrow schema, creates the target table with matching DuckDB types, then
slices the Arrow table into `chunk_size` batches. Each chunk is registered as a
view and inserted with `INSERT INTO ... SELECT * FROM buffer_table`:

```python
schema = pa.schema([("login", pa.string()), ("total_commits", pa.int64())])
table = pa.Table.from_pylist(records, schema=schema)

con.execute("""
    CREATE TABLE IF NOT EXISTS github.github_commits_large (
        login VARCHAR,
        total_commits BIGINT
    )
""")

buffer = ArrowTableLoadingBuffer(
    conn=con,
    pyarrow_schema=table.schema,
    table_name="github.github_commits_large",
    chunk_size=10,  # small for the demo; use ~100000 in production
)
buffer.insert(table)
```

Reach for this path when the source is too large for a single load, or when you
want explicit column types instead of inference. Pinning both the Arrow schema
and the DuckDB column definitions keeps the two in lockstep and avoids surprises
where inference picks a wider or narrower type than you want.

`tests/test_load_to_motherduck_large.py` exercises the buffer chunking against an
in-memory DuckDB database (no MotherDuck or network needed), covering a single
insert, multiple inserts, and a 100-row table that spans many chunks.

## Questions to answer

- What is the source: an HTTP API, a local file, or an in-memory dataframe?
- Which MotherDuck database and table should the data land in?
- Small payload (pandas, one shot) or larger payload (typed PyArrow, chunked)?
- Full refresh (create/replace) or append to an existing table?
- How often should it run, and where will the `motherduck_token` come from?

## Caveats

- **`CREATE TABLE IF NOT EXISTS` does not refresh.** Both scripts only create the
  table on the first run; later runs are silent no-ops and the data goes stale.
  For a full refresh use `CREATE OR REPLACE TABLE`; to accumulate rows, create the
  table once and `INSERT` on subsequent runs (the large script's pattern).
- **Missing token fails at attach, not at startup.** If `.env` is absent, holds
  the placeholder `mytoken`, or you run from a directory where `load_dotenv()`
  cannot find `.env`, the script fails when it reaches `ATTACH 'md:'`. Confirm the
  token is real and that you run from this folder.
- **GitHub `stats/contributors` can return `202` with an empty body.** GitHub
  computes contributor statistics asynchronously and returns `202 Accepted` with
  no data on the first request for a repo. The script does not retry, so a cold
  cache yields an empty load. Re-run after a moment, or handle `202` explicitly
  for your own source.
- **Unauthenticated GitHub API requests are rate limited** to roughly 60 per hour
  per IP. For anything beyond a demo, send an authentication header, and do not
  hardcode that token in the script or commit it.
- **Keep secrets out of source and config.** The token belongs in `.env` (which
  should be gitignored) or a real environment variable, never in `flight.py`-style
  committed code or in the `.env.template`.
- **The pandas path infers types.** For wide or sparse payloads, inference can
  pick types you did not intend (for example everything as `VARCHAR`, or integers
  promoted to floats by nulls). Prefer the typed PyArrow path when types matter.
- **`chunk_size=10` is a demo value.** It is set small so the example logs several
  chunks. Tiny chunks mean many round trips; raise it (around `100000`) for real
  loads.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `motherduck_token` (in `.env`) | Authenticates the DuckDB client to MotherDuck. Copy `.env.template` to `.env` and set it. | A MotherDuck access token |
| `url` in `fetch_github_data()` | The API endpoint to ingest. Swap for your own source. | `https://api.github.com/repos/duckdb/duckdb/stats/contributors` |
| `process_data()` body | Maps the API JSON into rows. Rewrite for your payload's shape. | builds `login`, `total_commits` records |
| `CREATE DATABASE IF NOT EXISTS github` | Target MotherDuck database. | any database name |
| Target table name | Where rows land: `github.github_commits` (small) / `github.github_commits_large` (large). | `<database>.<table>` |
| Explicit schema (large script) | The PyArrow + DuckDB column types for typed ingestion. | `[("login", pa.string()), ("total_commits", pa.int64())]` |
| `chunk_size` (large script) | Rows per INSERT batch. Set to 10 in the demo; use a larger value in production. | `100000` is a good default |

## Run it

Prerequisites: a MotherDuck account and access token, Python 3.12, and `uv`.
Docker is optional (only if you run it inside a devcontainer). Copy
`.env.template` to `.env` and set `motherduck_token`.

```bash
# from this folder
make load-md-small   # pandas path: load_to_motherduck_small.py
make load-md-large   # typed PyArrow chunked path: load_to_motherduck_large.py
make test            # run the PyArrow buffer tests
```

The same scripts run directly via `uv`:

```bash
uv run python -m python_ingestion.load_to_motherduck_small
uv run python -m python_ingestion.load_to_motherduck_large
uv run pytest tests
```

## Files

- [`python_ingestion/load_to_motherduck_small.py`](python_ingestion/load_to_motherduck_small.py) - the pandas path: fetches GitHub contributor stats, reshapes them, and writes `github.github_commits` with `CREATE TABLE AS SELECT`.
- [`python_ingestion/load_to_motherduck_large.py`](python_ingestion/load_to_motherduck_large.py) - the typed PyArrow path: defines `ArrowTableLoadingBuffer` and inserts `github.github_commits_large` in `chunk_size` batches against an explicit schema.
- [`python_ingestion/__init__.py`](python_ingestion/__init__.py) - marks `python_ingestion` as an importable package.
- [`tests/`](tests/) - pytest suite: `test_load_to_motherduck_small.py` mocks the GitHub fetch and checks `process_data`, `test_load_to_motherduck_large.py` exercises the buffer chunking against an in-memory DuckDB.
- [`Makefile`](Makefile) - `make` targets for the two load scripts and the tests (`load-md-small`, `load-md-large`, `test`).
- [`pyproject.toml`](pyproject.toml) - project metadata and dependencies (duckdb, pandas, pyarrow, python-dotenv, requests; pytest and ruff for dev).
- [`.env.template`](.env.template) - copy to `.env` and set `motherduck_token`; the scripts read it via `load_dotenv()`.
- [`.python-version`](.python-version) - pins the interpreter to Python 3.12.
- [`uv.lock`](uv.lock) - the pinned `uv` dependency lockfile.

## Learn more

For loading-data options beyond this script (object storage, secrets management,
`CREATE TABLE AS` vs `INSERT` tradeoffs, bulk loading) and deeper MotherDuck or
DuckDB questions, use the `ask_docs_question` MCP tool or see the
[loading data documentation](https://motherduck.com/docs/key-tasks/loading-data-into-motherduck/).
