---
title: Query dbt MetricFlow Metrics as a Flight
id: flight-dbt-metricflow
description: >-
  A reusable Flight that downloads a dbt + MetricFlow project from a GitHub repo
  over HTTPS, builds it on MotherDuck, and runs mf query for a metric chosen per
  run through Flight config.
  Each run appends the result to a snapshot table. Use when your dbt semantic model
  lives in a repo and you want one deployed Flight that answers many metric
  questions and builds a metric time series on a schedule.
type: template
category: analytics
features: [flights]
tags: [dbt, metricflow]
---

# Query dbt MetricFlow Metrics as a Flight

A single-file Flight that builds a [dbt MetricFlow](https://docs.getdbt.com/docs/build/about-metricflow)
semantic model on MotherDuck and queries it — downloading the dbt project as a
GitHub archive **over HTTPS at run time** (no git binary needed) and choosing the
metric, grouping, and date window **per run through Flight config**. Point
`GIT_REPO`/`GIT_REF` at your own dbt repo to query your own model (public, or
private via a Flights secret); one deployed Flight then answers many metric
questions by overriding config
when you trigger a run, no redeploy. Each run appends its result to a snapshot
table, so a scheduled Flight builds a queryable time series of metric values.

This is the Flight counterpart to the local [dbt-metricflow](../../dbt-metricflow)
example. There, `mf query` prints to your terminal; here the same semantic model
runs in a scheduled, torn-down container and its output has to land somewhere
durable.

## How it works

A Flight runs as a single `flight.py` in a fresh container that ships **no git
binary**. Embedding a copy of the dbt project would drift from the canonical
example, so `flight.py` **downloads the project over HTTPS at run time** (stdlib
only — no clone) and shells out to the `dbt` and `mf` CLIs against it:

1. Read config from the environment (Flight `config` keys arrive as env vars).
2. Connect to MotherDuck (`md:`) and `CREATE DATABASE IF NOT EXISTS` the target.
3. Download `GIT_REPO`@`GIT_REF` as a gzip archive into a temp dir and extract it.
   Public vs private is decided at run time: with no `GIT_TOKEN` secret it uses the
   public `…/archive/<ref>.tar.gz` endpoint; with one it uses the authenticated
   GitHub API tarball endpoint (`api.github.com/repos/<owner>/<repo>/tarball/<ref>`).
4. Discover the dbt project (`dbt_project.yml`) and the profile (`profiles.yml`)
   in the checkout by globbing, so any layout works.
5. `dbt seed` and `dbt run --target motherduck` to build the models. The fetched
   project's own `profiles.yml` is used as-is; its MotherDuck path reads the
   database name from `MD_DATABASE` via dbt's `env_var()`, so nothing is written
   from scratch.
6. `mf query --metrics … --group-by … --start-time … --end-time …`, writing a CSV.
7. Append each result row to the snapshot table, tagged with `run_at` and config.

```
config (env) ── download archive ── dbt seed/run ── mf query ── snapshot table
  override per run   GIT_REPO@GIT_REF    build on MotherDuck     append, JSON, run_at
```

### The config-override pattern

This is the reason the Flight exists. A Flight's `config` is a `MAP(VARCHAR, VARCHAR)`
of non-secret values, injected as environment variables. You override it **per
run** without editing or re-versioning the Flight:

```sql
-- create once with default config
FROM MD_CREATE_FLIGHT(
  name := 'dbt_metricflow',
  source_code := '...flight.py...',
  requirements_txt := '...requirements.txt...',
  config := MAP {
    'METRICS': 'revenue,orders,customers',
    'GROUP_BY': 'metric_time__month',
    'START_DATE': '2024-01-01',
    'END_DATE': '2024-12-31',
    'MD_DATABASE': 'ecommerce_metrics_flight'
  }
);

-- run with a one-off override: a different metric and window, same Flight
FROM MD_RUN_FLIGHT(
  flight_id := '…',
  config := MAP {'METRICS': 'revenue_per_customer', 'START_DATE': '2024-02-01', 'END_DATE': '2024-02-29'}
);
```

The override is merged over the stored config — provided keys win, omitted keys
keep the Flight default. **Keys must already exist on the Flight**; a per-run
override changes values, it cannot introduce a new key.

### The snapshot table

`mf query` output columns change with the metric and grouping you ask for, so a
fixed-column table would break the first time someone overrides `METRICS`. Each
result row is therefore stored as a `JSON` column, keeping one table usable across
every run:

```sql
SELECT run_at, metrics, group_by, result
FROM ecommerce_metrics_flight.metric_snapshots
ORDER BY run_at DESC;

-- pull a field back out of the JSON
SELECT run_at, result->>'revenue' AS revenue
FROM ecommerce_metrics_flight.metric_snapshots
WHERE metrics = 'revenue,orders,customers';
```

Verified across two runs: a `revenue,orders,customers` run and a
`revenue_per_customer` override produce different JSON shapes yet coexist in the
one table, each tagged with the config that produced it.

### Querying your own model

The default `GIT_REPO`/`GIT_REF`/`REPO_SUBDIR` point at this cookbook's
[dbt-metricflow](../../dbt-metricflow) example so a fresh deploy runs end to end.
To query your own metrics, point these at your dbt repo. Your project needs a
`profiles.yml` with a `motherduck` target whose path resolves the database — the
example uses `path: "md:{{ env_var('MD_DATABASE', 'ecommerce_test_db') }}"`, which
the Flight feeds through `MD_DATABASE`. No code in `flight.py` changes.

### Private repositories

A public repo needs no credentials. For a **private** GitHub repo, store a
personal access token (fine-grained, **Contents: Read-only** on that repo) in a
MotherDuck `TYPE flights` secret with a `GIT_TOKEN` param:

```sql
CREATE SECRET git_auth IN motherduck (
  TYPE flights,
  GIT_TOKEN 'github_pat_...'
);
```

`resolve_secret('GIT_TOKEN')` in `flight.py` reads it at run time — the Flight
injects each secret param as `<secret_name>_GIT_TOKEN`, and the helper accepts
either that or a bare `GIT_TOKEN` env var (handy locally). When a token is present,
the Flight switches to the authenticated GitHub API archive endpoint and sends the
token in an `Authorization` header (never in the URL, so it stays out of the logs).

## Questions to answer

- Which git repo, ref, and subdirectory hold the dbt project (your fork, or the
  default example)?
- Which metrics matter, and what dimension and date window should each run query?
- Which target database should hold the built models and the snapshot table?
- Will runs vary the metric per trigger (config override), run on a fixed
  schedule, or both?
- Does the project's time spine range cover your requested dates?

## Caveats

- **Run-time network, no git.** The Flight downloads the project archive over
  HTTPS at run time (stdlib only — the container has no `git`), so it needs egress
  to GitHub. The archive endpoints are **GitHub-specific**; a non-GitHub host
  (GitLab, Bitbucket, self-hosted) would need a different fetch.
- **A private repo needs a `GIT_TOKEN` secret.** Without one, the public
  `…/archive/<ref>.tar.gz` URL 404s on a private repo. Store a token in a `TYPE
  flights` secret (see [Private repositories](#private-repositories)); the Flight
  then uses the authenticated API endpoint.
- **`GIT_REF` accepts a branch, tag, or commit SHA.** Both the public and
  authenticated archive endpoints resolve any of the three. Pin to a tag or SHA
  for reproducible runs.
- **Override changes values, not keys.** A per-run `config` override only sets new
  values for keys that already exist on the Flight. The defaults include every key
  below; set the value you want.
- **Time-dimension queries are bounded by the spine.** The example spine generates
  `2024-01-01` to `2025-12-31`. `START_DATE`/`END_DATE` outside that window, or
  grouping by `metric_time__*` beyond it, returns no rows. Widen the spine in your
  project.
- **Build runs every time.** Each run does `dbt seed` + `dbt run` against a fresh
  checkout, so a large project makes every run slower.
- **Derived metrics reference metric names, not measures.** `revenue_per_customer`
  is `revenue / customers`, both metrics. A raw measure name in a derived `expr`
  will not resolve.
- **Keep the token out of config.** The runtime attaches a MotherDuck token and
  injects it as `MOTHERDUCK_TOKEN`; never place a token in `config`.

## What you'll adjust

Every knob is a config/env value read by `read_config()` at the top of
`flight.py`; set them as Flight config rather than by editing code. The semantic
model itself lives in the git repo you point the Flight at, not in `flight.py`.

| Config key | Default | Purpose |
|---|---|---|
| `GIT_REPO` | `…/motherduck-cookbook.git` | GitHub repo holding the dbt project. Point at your fork. |
| `GIT_REF` | `main` | Branch, tag, or commit SHA to download as an archive. |
| `REPO_SUBDIR` | `dbt-metricflow` | Path within the repo to run from (extracted from the archive). |
| `METRICS` | `revenue,orders,customers` | Comma-separated metric(s) `mf query` computes. Override per run. |
| `GROUP_BY` | `metric_time__month` | Dimension(s) to slice by, e.g. `metric_time__day`, `order_id__status`. |
| `START_DATE` | `2024-01-01` | `mf query --start-time`; must fall inside the time spine. |
| `END_DATE` | `2024-12-31` | `mf query --end-time`; must fall inside the time spine. |
| `MD_DATABASE` | `ecommerce_metrics_flight` | Target database; fed to the project's `profiles.yml` `env_var()`. Created if missing. |
| `SNAPSHOT_TABLE` | `metric_snapshots` | Append-only table of results (`run_at`, config, `result` JSON). |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Never put it in config. |

`GIT_TOKEN` is **not** a config key — it is a secret. Store it in a `TYPE flights`
secret (see [Private repositories](#private-repositories)) so it never lands in the
Flight's `config` MAP or the logs. Public repos need no token at all.

## Run it

You need a MotherDuck account and an access token. The default repo/ref make a
fresh deploy produce a successful run with no other credentials.

Smoke-test locally before deploying (this downloads the project over HTTPS, builds
it in your account, and appends one batch to `metric_snapshots`):

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with-requirements requirements.txt flight.py
```

Override any default inline, for example a different metric and your own repo. For
a private repo, set `GIT_TOKEN` as a bare env var locally (deployed, it comes from
the Flights secret instead):

```bash
METRICS=revenue_per_customer GROUP_BY=metric_time__month \
GIT_REPO=https://github.com/you/your-dbt-repo.git GIT_REF=main REPO_SUBDIR=analytics \
GIT_TOKEN=github_pat_... \
  uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT` (no deploy SQL is checked in; adapt the
arguments), passing:

- `name`: a Flight name, for example `dbt_metricflow`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: `GIT_REPO`/`GIT_REF`/`REPO_SUBDIR` for your project, plus `METRICS`,
  `GROUP_BY`, `START_DATE`, `END_DATE`, `MD_DATABASE`

A MotherDuck token is attached automatically and injected at run time as
`MOTHERDUCK_TOKEN`; no token argument is needed. For a private dbt repo, also
`CREATE SECRET … (TYPE flights, GIT_TOKEN '…')` first (see
[Private repositories](#private-repositories)) — the Flight injects it at run time.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := …)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm the database and a `metric_snapshots` row
appear. Trigger a second run with a per-run `config` override to confirm the
override reaches the query (a different metric or window shows up in the new
snapshot row). Once green, add a schedule (`0 6 * * *`, 06:00 UTC daily, is a
reasonable default) by updating `schedule_cron` with `MD_UPDATE_FLIGHT`; schedule
updates are metadata-only and do not create a new version.

## Security

- **Identifier safety.** `MD_DATABASE` and `SNAPSHOT_TABLE` flow into `CREATE`
  statements that cannot be parameterized, so each is double-quote-escaped (`_ident`)
  before any SQL runs.
- **Parameterized data.** The snapshot row (metrics, grouping, dates, and the
  result JSON) is written with bound parameters, never string-formatted into SQL.
- **Fetch a trusted ref.** The Flight runs whatever code the fetched ref contains.
  Point `GIT_REPO`/`GIT_REF` at a repo and branch/tag you control; pin a tag for
  reproducibility.
- **Token in a secret, in the header.** A private repo's `GIT_TOKEN` belongs in a
  `TYPE flights` secret, never in `config` (which is logged and stored on the
  Flight). At run time the token is sent in the `Authorization` header of the
  GitHub API request, not in the URL, so it does not reach the Flight logs.

## Learn more

- Flight mechanics (creating, running, scheduling, secrets): the MotherDuck MCP
  `get_flight_guide` tool.
- The local, terminal-driven version of this project, with an `mf query`
  cookbook: [dbt-metricflow](../../dbt-metricflow) and its
  [EXAMPLES.md](../../dbt-metricflow/EXAMPLES.md).
- MetricFlow CLI reference: [dbt MetricFlow commands](https://docs.getdbt.com/docs/build/metricflow-commands).
- Deeper MotherDuck or DuckDB questions: the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight that
  downloads the project over HTTPS) and [`requirements.txt`](requirements.txt)
  (`dbt-core`, `dbt-duckdb`, `dbt-metricflow`, `duckdb`).
