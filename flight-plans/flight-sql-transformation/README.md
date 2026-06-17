---
title: Run SQL Transformations in Order
id: flight-sql-transformation
description: >-
  A reusable Flight that takes a set of CREATE TABLE AS / CREATE VIEW AS / CREATE MACRO AS statements and runs them in dependency order (in a DAG).
  Every statement waits for its upstreams,
  independent statements run concurrently up to a pool size limit,
  and each retries with exponential backoff. 
  Use for a set of SQL transformations inside one Flight.
type: template
category: analytics
features: [flights]
tags: []
prompt: >-
  I have a set of CREATE TABLE AS / VIEW / MACRO statements I want to run in dependency
  order as a DAG inside one Flight, with independent statements running concurrently and
  automatic retries. Help me adapt the "Run SQL Transformations in Order" recipe to my
  own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-sql-transformation
published_date: 2026-06-15
---

# Run SQL Transformations in Order and in Parallel

Run a set of SQL transformations on MotherDuck! 
This is a Flight that turns a list of `CREATE TABLE | VIEW | MACRO ... AS ...`
statements into a dependency graph and executes it concurrently. Just replace the contents of the `sql_statements` function with your own SQL queries in any order. 
Then [sqlglot](https://github.com/tobymao/sqlglot) parses each
statement to find the object it produces and its upstream dependencies and the resulting DAG runs as parallel as possible.
If any errors arise, each statement will retry up to a limit, then downstream queries are cancelled.

The example statements live in `sql_statements()` — the example is one chain covering a
table, a view, a scalar macro, a diamond, and a dependency on a table this
Flight does not create. Replace them with your own; the engine stays untouched.

## How it works

1. Parse each statement with sqlglot to get its produced object and its table
   and macro references.
2. Build the DAG: a reference matching another statement's output becomes an
   edge. Table/view references resolve against table/view producers, macro calls
   against macros. Duplicate targets, ambiguous references, and cycles are
   rejected before anything runs.
3. Execute on a `ThreadPoolExecutor`: launch every node whose upstreams have all
   succeeded, retry each with exponential backoff, and on a permanent failure
   skip its downstream while independent branches finish. The report logs each
   statement's status, attempts, and duration.

## Questions to answer

- What `CREATE` statements make up your pipeline?
- What is the destination database?
- How many statements can safely run at once?
- On what schedule (cron, UTC) should it run?

## Caveats

- **External references are not dependencies.** A reference no statement produces
  (`read_csv(...)`, `sample_data.*`, a pre-existing table) creates no edge and is
  treated as an existing input.
- **Ambiguous references fail fast.** A reference matching two produced objects
  (e.g. bare `t` when both `a.t` and `b.t` exist) raises rather than guess.
  Qualify the name to disambiguate.
- **Statements must be `CREATE ... AS <query>`.** A non-`CREATE` statement, or a
  `CREATE TABLE` with only a column list and no `AS`, is rejected.

## What you'll adjust

| Knob | Where | Default | Purpose |
|---|---|---|---|
| `sql_statements()` | `flight.py` | one chain over `sample_data.nyc.taxi` | Your `CREATE` statements. Seed the first from a readable source; the rest reference earlier outputs by name. |
| `TARGET_DATABASE` | Flight config / env | `sql_dag_sqlglot` | Destination database, created if absent. |
| `MAX_WORKERS` | Flight config / env | `4` | Thread-pool size — independent statements run at once. |
| `MAX_ATTEMPTS` | Flight config / env | `4` | Retries per statement before skipping downstream statements. |
| `RETRY_BASE_DELAY` | Flight config / env | `1.0` | First delay before retry (doubles each retry, capped at 30s). |

## Run it

You need a MotherDuck account and access token. The example reads the public
`sample_data.nyc.taxi`, so it runs as-is with no other credentials.

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with-requirements requirements.txt flight.py
```

It builds the DAG, logs the execution plan by level, and runs it. A non-zero
exit means at least one statement failed.

### Deploy as a Flight

Deploy through the Flight SQL surface (`MD_CREATE_FLIGHT`, then
`MD_RUN_FLIGHT`) with:

- `source_code`: [`flight.py`](flight.py), with `sql_statements()` edited to your statements
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `config`: `TARGET_DATABASE`, `MAX_WORKERS` as needed

The Flight runtime injects `MOTHERDUCK_TOKEN`; make sure it can write the
destination database. Create the Flight without a schedule, trigger one run with
`MD_RUN_FLIGHT` to confirm it loads, then add a `schedule_cron` using cron
syntax based on user input.

## Learn more

- Flight mechanics (creating, running, scheduling, secrets): the MotherDuck MCP
  `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions: the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight) and
  [`requirements.txt`](requirements.txt) (`duckdb` plus `sqlglot` for parsing).
