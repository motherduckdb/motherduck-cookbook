---
title: Benchmark Query Performance With Read Scaling
id: motherduck-performance-benchmark
description: >-
  Run your own read queries against MotherDuck through the Postgres endpoint,
  serially and at several concurrency levels, then compare end-to-end time,
  per-query latency, Duckling spread and estimated price in a Dive. Use when a
  team wants to size read scaling or prove concurrent query performance on
  their own workload.
type: example
category: getting-started
features: [dives, pg_endpoint, shares]
tags: [python]
prompt: >-
  I want to benchmark how my own MotherDuck queries perform with read scaling,
  one at a time and with many running at once, and see the latency, Duckling
  spread and estimated price in a Dive. Help me adapt the "Benchmark Query
  Performance With Read Scaling" recipe to my own data and use case, using it
  as a guide: https://motherduck.com/docs/cookbook/motherduck-performance-benchmark
published_date: 2026-10-13
---

# Benchmark Query Performance With Read Scaling

A Python benchmark kit that replays a CSV of your own read-only queries against
MotherDuck, one at a time and with N queries in flight, then pulls the timings
from `md_information_schema.query_history` into a results database and shows
them in a **MotherDuck query benchmarking** Dive. You get the end-to-end time,
per-query latency, how queries spread across read scaling Ducklings, and the
estimated price for each run.

It demonstrates the MotherDuck pattern of **scaling a read workload with
[read scaling](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/)
tokens over the [Postgres endpoint](https://motherduck.com/docs/sql-reference/postgres-endpoint/)**,
and of **measuring it from MotherDuck's own
[`query_history`](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/md_information_schema/query_history/)**
rather than client-side timers.

## How it works

```
queries.csv ──► md_bench.py ──(read scaling token, PG endpoint)──► read scaling Ducklings
                    │
                    └──(read/write admin token)──► query_history ──► md_benchmarks ──► md_benchmarks_share ──► Dive
```

[`md_bench.py`](md_bench.py) uses `psycopg2` against the Postgres endpoint; it
never uses a local DuckDB client. Each invocation runs exactly one test and
saves it as its own run:

- `--concurrency 1` runs the queries one at a time, as a serial baseline.
- `--concurrency N` keeps exactly N queries running until the list is done.
  When one finishes, the next one starts.

1. **Tag and run.** Every query is prefixed with comment lines that survive into
   `query_history`, then run with the read scaling token:

   ```sql
   -- query_name: daily_revenue
   -- concurrency: 8 queries
   -- md_bench_run: <run id>
   SELECT ...
   ```

2. **Collect.** With the read/write token, the script waits for the run's
   queries to appear in `query_history` and copies them into the results
   database (`md_benchmarks`), one row per run, test and query. Run details are
   also saved locally in `runs/<run id>.json`.
3. **Visualize.** `--create-dive` creates `md_benchmarks_share` (automatic
   updates, see [`CREATE SHARE`](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/create-share/))
   and a Dive built from [`dive/benchmark_dive.tsx`](dive/benchmark_dive.tsx)
   that reads from the share. The script deploys it with
   [`MD_CREATE_DIVE`](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/dives/md-create-dive/). Every later run appears in the Dive's run list,
   so you can compare a serial baseline with concurrency 4, 8 and 16.

The numbers are calculated as follows:

- **E2E time** is the last query's end minus the first query's start, as
  recorded by MotherDuck.
- **Duckling** is the number after `.rs.` in `duckling_id`, for example `user.rs.2`.
- **Price** is E2E hours × Ducklings used × the hourly rate for the
  [Duckling size](https://motherduck.com/docs/about-motherduck/billing/duckling-sizes/), using the
  Business plan rate for the organization's region. The script reads the
  region from
  [`md_user_info()`](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/md-user-info/)
  and stores it with the run. Rates differ per
  [pricing tier](https://motherduck.com/docs/about-motherduck/cloud-regions/#pricing-tiers); see
  [compute pricing](https://motherduck.com/docs/about-motherduck/billing/pricing/#compute-pricing)
  for current values:

  | Tier | Regions | Standard | Jumbo | Mega | Giga |
  | --- | --- | --- | --- | --- | --- |
  | 1 | `us-east-1`, `us-west-2` | $2.40 | $4.80 | $12.00 | $24.00 |
  | 2 | `eu-central-1`, `eu-west-1` | $2.93 | $5.86 | $14.65 | $29.30 |
  | 3 | `ap-northeast-1`, `ap-southeast-2` | $3.10 | $6.19 | $15.48 | $30.96 |

## Questions to answer

- Which [region](https://motherduck.com/docs/about-motherduck/cloud-regions/) is the organization in?
  Run `SELECT region FROM md_user_info();` from the MotherDuck UI or a DuckDB
  1.5.3+ client, then pass it as `--region` on every command, for example
  `--region eu-west-1`. The Postgres endpoint hostname is regional,
  `pg.<region>-aws.motherduck.com`. Other regions' hostnames still accept the
  connection, but the round trip crosses regions and adds latency. `--check`
  flags the mismatch.
- Which database do the queries run against? Table names without a database
  resolve against `--database`.
- Which queries represent the workload, for example the queries behind a
  dashboard, an app endpoint or an embedded analytics page? Write them by hand,
  draft them from query history with `--suggest-queries`, or start with
  `queries.example.csv`.
- Should each end user's or tenant's queries stay on the same Duckling
  (user-to-Duckling affinity)? If so, add a `session_name` column and run with
  `--session-mode csv`.
- Which concurrency levels and how many rounds? A common plan is `1`, `4` and
  `8`, adding `16` for many simultaneous users.
- Who will view the Dive: people in the organization (default) or people
  outside it (`--share-access unrestricted`)?

## Caveats

- **Plan and tokens.** Read scaling needs a Business or Enterprise plan. The
  kit needs two tokens from the same organization: a
  [**Read Scaling** token](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/#creating-a-read-scaling-token)
  for the benchmark, and a
  [**Read/Write** token](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/authenticating-to-motherduck/#creating-an-access-token)
  that belongs to an **organization Admin**, because only Admins can read
  `query_history`.
- **Read-only queries only.** Read scaling tokens reject writes. Parameterized
  queries (`$1`, `?`), local file reads and AI functions such as `prompt()` do
  not belong in the CSV.
- **The price is an estimate** for the benchmark window only, at Business
  plan rates for the region. The actual bill depends on the plan and on how
  long the Ducklings stay up. If the script can't read the region, it assumes
  `us-east-1` rates and says so in the summary.
- **Eventual consistency.** Read scaling Ducklings are eventually consistent
  with the read/write Duckling. If the data changed recently, see
  [ensuring data freshness](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/#ensuring-data-freshness).
- **Cold cache.** The first queries on a cold Duckling are slower. Run the
  serial baseline twice, or add `--rounds 2`, for warm-cache numbers.
- **Pool size.** A read scaling pool has 4 Ducklings by default, with a soft
  limit of 16, so concurrency above the pool size shares Ducklings. See
  [pool sizing](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/#optimize-your-read-scaling-duckling-pool-size). Keep
  `queries × rounds` at least twice the highest concurrency level so the pool
  stays full.
- **Affinity is never implicit.** A `session_name` column alone does not turn
  affinity on. When the CSV has session names, the script refuses to run until
  you pass `--session-mode csv` or `--session-mode none` (or answer its prompt
  in a terminal).
- **Literal `\n` in the CSV.** If line breaks are the two characters `\n`
  instead of real newlines, any `--` comment comments out the rest of the query
  and every query fails with a syntax error.
- **`query_history` lag.** If a run times out waiting for its queries to appear,
  collect it later with `--collect <run id>`; nothing is lost.
- **Agent fallback for the Dive.** If `MD_CREATE_DIVE` is unavailable and the
  MotherDuck MCP server is connected, an agent can save
  `dive/benchmark_dive.tsx` with the `save_dive` tool instead; see
  [`CLAUDE.md`](CLAUDE.md) step 7.

## What you'll adjust

| Knob | Default | Purpose |
| --- | --- | --- |
| `--queries` | none | CSV with `query_name` and `query_text`, plus an optional `session_name` column. Each row is one read-only query. |
| `--database` | none | Database the queries run against. |
| `--concurrency` | none | `1` for the serial test, or N to keep N queries running at once. Run the script once per level. |
| `--rounds` | `1` | How many times the test works through the CSV. Use more rounds when the CSV has fewer queries than the concurrency level. |
| `--region` | `us-east-1` | The organization's AWS region: `us-east-1`, `us-west-2`, `eu-central-1`, `eu-west-1`, `ap-northeast-1` or `ap-southeast-2`. Sets the Postgres endpoint host. |
| `--host` | `pg.<region>-aws.motherduck.com` | Override the Postgres endpoint host. Rarely needed. |
| `--session-mode` | `none` | `none` spreads connections round robin; `csv` runs each query with its row's `session_name` (affinity); `per-worker` sends a distinct `session_name` per connection. |
| `--user-name` | none | Narrow the `query_history` lookup (and `--suggest-queries`) to one user, such as an app's service account. |
| `--results-db` | `md_benchmarks` | Where results are stored. The Dive reads this database through its share. |
| `--run-label` | start time | Label shown in the Dive's run selector. |
| `--history-timeout` | `600` | Seconds to wait for the run to appear in `query_history`. |
| `--share-access` | `organization` | Who can read `md_benchmarks_share`: `organization` or `unrestricted` (anyone with the link). |
| `--suggest-queries N` / `--since-days` / `--rank` | `7`, `frequency` | Draft a CSV from the N most-run (or `total_time`) read queries in the history window. |
| `MOTHERDUCK_READ_SCALING_TOKEN` | `.env` | Read Scaling token; or `--rs-token`. |
| `MOTHERDUCK_TOKEN` | `.env` | Admin Read/Write token for `query_history`, results and the Dive; or `--rw-token`. |

### Build the queries CSV

Pick queries that represent the read workload to scale. Three starting points:

- Write the CSV by hand from existing SQL. Use a CSV writer so multi-line SQL is
  quoted correctly.
- Draft one from query history. This takes the 20 most-run read queries from
  the last 7 days and leaves out failed queries, parameterized queries, local
  file reads and AI function calls:

  ```bash
  .venv/bin/python md_bench.py --suggest-queries 20 --since-days 7 --database <database> --out queries.csv
  ```

  Add `--rank total_time` to rank by total time spent instead of run count.
- Try the kit first with [`queries.example.csv`](queries.example.csv), which
  queries the `sample_data` share every account has.

### Optional: user-to-Duckling affinity

In a real application, each end user or tenant usually sends a stable
`session_name`, so their queries keep landing on the same Duckling and reuse its
warm cache ([session affinity](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/#session-affinity-with-session-name)).
To benchmark that pattern, add a `session_name` column and run with
`--session-mode csv`:

```csv
query_name,query_text,session_name
revenue_tenant_17,"SELECT ... WHERE tenant_id = 17",17
orders_tenant_17,"SELECT ... WHERE tenant_id = 17",17
revenue_tenant_42,"SELECT ... WHERE tenant_id = 42",42
```

Use the identifier the application would send, such as a user, tenant or
customer ID. Rows with an empty `session_name` are spread round robin. A useful
comparison is the same concurrency level with and without `--session-mode csv`.
The Dive then adds a **User-to-Duckling affinity** view that shows which
Duckling each session ran on and flags any session that moved.

## Run it

Prerequisites: Python 3.10 or later, the two tokens from **Settings >
Access Tokens** (see Caveats), and the organization's region. Add
`--region <region>` to each command below if it isn't `us-east-1`.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # then paste both tokens into .env in an editor

# Check tokens, connections, and query_history access
.venv/bin/python md_bench.py --check --database <database>

# Print the tagged SQL without running anything
.venv/bin/python md_bench.py --queries queries.csv --concurrency 1 --validate-only

# Serial baseline, then concurrent runs. Each one is saved as its own run.
.venv/bin/python md_bench.py --queries queries.csv --database <database> --concurrency 1
.venv/bin/python md_bench.py --queries queries.csv --database <database> --concurrency 4
.venv/bin/python md_bench.py --queries queries.csv --database <database> --concurrency 8

# Create the Dive once. It prints https://app.motherduck.com/dives/<dive id>.
.venv/bin/python md_bench.py --dive-only --create-dive --database <database>
```

Each run prints its end-to-end time, query count, errors, Ducklings used and
price. The Dive ID is stored in `.md_bench_dive_id`, so rerunning
`--create-dive` updates the same Dive instead of creating a second one. New runs
reach the share automatically. If a run times out waiting for `query_history`:

```bash
.venv/bin/python md_bench.py --collect <run id>
```

### Run it with a coding assistant

Open this folder in a coding assistant that can read files and run shell
commands, and paste:

```text
I want to benchmark how my MotherDuck queries perform with read scaling, both one at a time and with
several running at once. This folder has the MotherDuck query benchmarking kit. Read CLAUDE.md and
follow it: ask me one question at a time for anything you need, help me build my queries CSV, run the
benchmark, and save the results Dive to MotherDuck. Don't ask me to paste tokens into the chat.
```

[`CLAUDE.md`](CLAUDE.md) is the step-by-step procedure: setup, token checks,
building and validating the CSV, planning runs, and reporting results.

## Security

- **Tokens stay in `.env`.** Both tokens are read from `.env` or the
  environment and are never written to results or logs. `.env`, `runs/` and
  `.md_bench_dive_id` are git-ignored. Do not paste tokens into a chat; the
  assistant procedure checks only whether each token is set.
- **Least privilege for the workload.** Benchmark queries run with a Read
  Scaling token, which cannot write. Only collection, results and the Dive use
  the Admin Read/Write token.
- **TLS.** Connections to the Postgres endpoint use `sslmode=verify-full` with
  the `certifi` CA bundle.
- **Safe session names.** `session_name` values are restricted to letters,
  digits, `-`, `_`, `.` and `:` before they are passed as connection options.
- **The share exposes query text.** `md_benchmarks_share` defaults to
  organization access. With `--share-access unrestricted`, anyone with the link
  can read the results, including each benchmarked query's SQL. Change access
  later in the MotherDuck UI or by recreating the share.

## Learn more

- [`md_bench.py`](md_bench.py): the benchmark, collector, query suggester and
  Dive deployer (`MD_CREATE_DIVE` / `MD_UPDATE_DIVE_CONTENT`).
- [`dive/benchmark_dive.tsx`](dive/benchmark_dive.tsx): the results Dive.
- [`CLAUDE.md`](CLAUDE.md): the assistant procedure, including a troubleshooting
  table.
- [`queries.example.csv`](queries.example.csv) and [`.env.example`](.env.example).
- MotherDuck docs:
  - [Read scaling](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/)
  - [Postgres endpoint](https://motherduck.com/docs/sql-reference/postgres-endpoint/) and
    [connecting from Python](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/postgres-endpoint/python/)
  - [Session names](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/connecting-to-motherduck/#session-names)
  - [Cloud regions](https://motherduck.com/docs/about-motherduck/cloud-regions/) and
    [compute pricing](https://motherduck.com/docs/about-motherduck/billing/pricing/#compute-pricing)
  - [Duckling sizes](https://motherduck.com/docs/about-motherduck/billing/duckling-sizes/)
  - [`query_history`](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/md_information_schema/query_history/)
  - [Dives](https://motherduck.com/docs/key-tasks/dives/) and
    [`MD_CREATE_DIVE`](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/dives/md-create-dive/)
  - [Sharing within an organization](https://motherduck.com/docs/key-tasks/sharing-data/sharing-within-org/)
- For Dive mechanics use the MotherDuck MCP `get_dive_guide` tool; for anything
  else about read scaling or the Postgres endpoint, `ask_docs_question`.
