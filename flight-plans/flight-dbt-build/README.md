---
title: Run a dbt Project as a Flight and Snapshot Run Results
id: flight-dbt-build
description: >-
  A reusable Flight that downloads a dbt project from a GitHub repo over HTTPS,
  runs dbt build on MotherDuck, and appends dbt's run_results.json to a snapshot
  table — one row per node per run. Use when you want one deployed Flight that
  builds a dbt project on a schedule and keeps a queryable history of build and
  test health.
type: template
category: automation
features: [flights]
tags: [dbt]
prompt: >-
  I want one deployed Flight that builds my dbt project on MotherDuck on a schedule
  and keeps a queryable history of build and test health — per-model status, timing,
  and test pass/fail over time — instead of just running dbt and losing the results.
  Help me adapt the "Run a dbt Project as a Flight and Snapshot Run Results" recipe to
  my own dbt repo and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-dbt-build
published_date: 2026-07-09
---

# Run a dbt Project as a Flight and Snapshot Run Results

A single-file Flight that runs a dbt project on MotherDuck and records what
happened — downloading the dbt project as a GitHub archive **over HTTPS at run
time** (no git binary needed) and appending dbt's own `run_results.json` to a
snapshot table, **one row per node per run**. Point `GIT_REPO`/`GIT_REF` at your
own dbt repo (public, or private via a Flights secret) and one deployed Flight
then builds that project on a schedule while keeping a queryable history of
build and test health — per-model status, timing, and test pass/fail over time.
This is the dbt-only sibling of [flight-dbt-metricflow](../flight-dbt-metricflow),
reusing the same HTTPS-archive fetch engine; where that template runs `mf query`
against a semantic model, this one runs plain `dbt build` and snapshots the
results.

## How it works

A Flight runs as a single `flight.py` in a fresh container that ships **no git
binary**. Embedding a copy of the dbt project would drift from the canonical
example, so `flight.py` **downloads the project over HTTPS at run time** (stdlib
only — no clone) and shells out to the `dbt` CLI against it:

1. Read config from the environment (Flight `config` keys arrive as env vars).
2. Connect to MotherDuck (`md:`) and `CREATE DATABASE IF NOT EXISTS` the
   snapshot database.
3. Download `GIT_REPO`@`GIT_REF` as a gzip archive into a temp dir and extract
   it. Public vs private is decided at run time: with no `GIT_TOKEN` secret it
   uses the public `…/archive/<ref>.tar.gz` endpoint; with one it uses the
   authenticated GitHub API tarball endpoint
   (`api.github.com/repos/<owner>/<repo>/tarball/<ref>`).
4. Discover the dbt project (`dbt_project.yml`) and its profile (`profiles.yml`)
   inside `REPO_SUBDIR` of the checkout, so any layout works.
5. Run dbt with `--target DBT_TARGET`. `RUN_MODE=build` runs `dbt seed` then
   `dbt build` (run + test); `RUN_MODE=test` runs `dbt test` only. The project's own
   `profiles.yml` is used as-is; its MotherDuck target reads the database name
   from an `env_var()`, which the Flight feeds via `DB_ENV_VAR`, so models land
   in `MODELS_DATABASE`.
6. Parse `target/run_results.json` and append one row per dbt node to the
   snapshot table, tagged with `run_at`, the git repo/ref, the dbt version, and
   the run mode. The full node is kept as a `JSON` column; typed columns
   (`status`, `execution_time`, `failures`, …) are pulled out for easy querying.

```
config (env) ── download archive ── dbt build/test ── run_results.json ── snapshot table
  override per run   GIT_REPO@GIT_REF   on MotherDuck      one row per node    append, JSON, run_at
```

### Build vs test mode

`RUN_MODE` picks what the Flight does with the fetched project:

- `RUN_MODE=build` (default) runs `dbt seed` then `dbt build` — priming the seed
  tables, then materializing and testing every model. This is the Flight that owns
  the build: it materializes the tables and records every node's result. (Seeding
  first is deliberate; see the cold-start caveat below.)
- `RUN_MODE=test` runs `dbt test` only, for when a **separate job already built
  the tables** (a dbt Cloud job, Airflow, another Flight). It touches no models,
  so it is read-only and any number of `test` runs can run concurrently without
  the write-write conflicts that shared-table rebuilds cause. Point `GIT_REPO`/
  `GIT_REF` at the same repo the build job uses so the tests match the tables.

`SELECT` scopes either mode to a subset of nodes (for example `tag:nightly`);
empty means the whole project.

### The failure policy

dbt exits non-zero both when a model fails to build and when a test assertion
fails. Those are different events, and the Flight treats them differently:

- A **node error** — a model, seed, or snapshot that failed to build, or a test
  that errored (status `error`) — fails the Flight run. Something is broken.
- A **test failure** — a test whose assertion did not hold (status `fail`) — is
  **recorded in the snapshot table but does not fail the run**. Scheduled
  quality drift is a trend to watch, not a page in the middle of the night.

So the Flight runs dbt tolerating its non-zero exit, always writes the snapshot,
and only then re-raises if any node actually errored. The one hard failure with
nothing to record is dbt not producing `run_results.json` at all (a parse or
compile error before any node ran).

### The config-override pattern

A Flight's `config` is a `MAP(VARCHAR, VARCHAR)` of non-secret values, injected
as environment variables. You override it **per run** without editing or
re-versioning the Flight — for example, run the same deployed Flight in
test-only mode against a feature branch:

```sql
-- create once with default config (RUN_MODE=build, the default repo)
FROM MD_CREATE_FLIGHT(
  name := 'dbt_build',
  source_code := '...flight.py...',
  requirements_txt := '...requirements.txt...',
  config := MAP {
    'GIT_REPO': 'https://github.com/you/your-dbt-repo.git',
    'GIT_REF': 'main',
    'REPO_SUBDIR': 'analytics',
    'RUN_MODE': 'build',
    'DBT_TARGET': 'prod',
    'DB_ENV_VAR': 'MOTHERDUCK_DATABASE',
    'MODELS_DATABASE': 'analytics_flight',
    'SNAPSHOT_DATABASE': 'analytics_flight',
    'SNAPSHOT_TABLE': 'dbt_run_results'
  }
);

-- run with a one-off override: test-only, against a feature branch, same Flight
FROM MD_RUN_FLIGHT(
  flight_id := '…',
  config := MAP {'RUN_MODE': 'test', 'GIT_REF': 'feature/new-tests'}
);
```

The override is merged over the stored config — provided keys win, omitted keys
keep the Flight default. **Keys must already exist on the Flight**; a per-run
override changes values, it cannot introduce a new key.

### The snapshot table

Every run appends one row per dbt node, so the table is a time series of build
and test health. The full node is stored as `JSON`, so the schema survives any
project or node selection, while typed columns make the common queries easy. To
find tests that are currently failing across the most recent runs:

```sql
SELECT run_at, unique_id, status, failures
FROM dbt_churn_flight.dbt_run_results
WHERE resource_type = 'test' AND status = 'fail'
ORDER BY run_at DESC;
```

`resource_type` (`model`, `seed`, `test`, `snapshot`, …), `execution_time`, and
`rows_affected` are all columns too, so the same table answers "which model is
getting slower?" and "what did last night's build touch?".

## Questions to answer

- Which git repo, ref, and subdirectory hold the dbt project (your fork, or the
  default `dbt-churn-prediction` example)?
- Should each run **build** the project (`RUN_MODE=build`) or only **test** an
  already-built one (`RUN_MODE=test`)?
- What target does the project's `profiles.yml` define (`DBT_TARGET`), and which
  env var does that target read the database name from (`DB_ENV_VAR`)?
- Which database and table should hold the models (`MODELS_DATABASE`) and the
  `run_results` history (`SNAPSHOT_DATABASE`/`SNAPSHOT_TABLE`)?
- Should the build run on a schedule, and at what cadence?

## Caveats

- **Run-time network, no git.** The Flight downloads the project archive over
  HTTPS at run time (stdlib only — the container has no `git`), so it needs
  egress to GitHub. The archive endpoints are **GitHub-specific**; a non-GitHub
  host (GitLab, Bitbucket, self-hosted) would need a different fetch.
- **A private repo needs a `GIT_TOKEN` secret.** Without one, the public
  `…/archive/<ref>.tar.gz` URL 404s on a private repo. Store a token in a `TYPE
  flights` secret (see [Deploy as a Flight](#deploy-as-a-flight)); the Flight
  then uses the authenticated API endpoint.
- **`GIT_REF` accepts a branch, tag, or commit SHA.** Both the public and
  authenticated archive endpoints resolve any of the three. Pin to a tag or SHA
  for reproducible runs.
- **The build runs every time, and concurrent builds conflict.** With
  `RUN_MODE=build`, each run does `dbt build` against the same database, so a
  large project is slower and **parallel `build` runs collide** on the shared
  tables (MotherDuck rejects the losers with a write-write conflict). For fan-out
  or read-only runs, use `RUN_MODE=test` — it touches no models and is
  parallel-safe.
- **Build mode seeds before it builds.** The default project's staging models
  read the seed tables as dbt `source()`s, and dbt does not sequence a model
  after a seed it only reads as a source. So `RUN_MODE=build` runs `dbt seed`
  first and then `dbt build`, which is what makes a cold first run on a brand-new
  `MODELS_DATABASE` succeed rather than error on models that ran before the seeds.
  `RUN_MODE=test` assumes the tables already exist and does no seeding.
- **Test failures are recorded but do not fail the Flight — model errors do.** A
  failing test assertion (status `fail`) lands in the snapshot table and the run
  stays green; a model, seed, or snapshot that errors (status `error`) fails the
  run. Watch quality drift by querying the table, not by watching for red runs.
- **The project's profile must match your config.** Its `profiles.yml` must
  expose the target database through `env_var()` matching `DB_ENV_VAR`, and it
  must define the target you pass as `DBT_TARGET`. The default
  `dbt-churn-prediction` reads `MOTHERDUCK_DATABASE` and has a `prod` target,
  which is why `DB_ENV_VAR`/`DBT_TARGET` default to those values.
- **Keep the token out of config.** The runtime attaches a MotherDuck token and
  injects it as `MOTHERDUCK_TOKEN`; never place a token in `config`.

## What you'll adjust

Every knob is a config/env value read by `read_config()` at the top of
`flight.py`; set them as Flight config rather than by editing code. The dbt
project itself lives in the git repo you point the Flight at, not in `flight.py`.

| Config key | Default | Purpose |
|---|---|---|
| `GIT_REPO` | `…/motherduck-cookbook.git` | GitHub repo holding the dbt project. Point at your fork. |
| `GIT_REF` | `main` | Branch, tag, or commit SHA to download as an archive. |
| `REPO_SUBDIR` | `dbt-churn-prediction` | Path within the repo that holds the dbt project. |
| `RUN_MODE` | `build` | `build`: `dbt build` (seed + run + test). `test`: `dbt test` only (read-only, parallel-safe) when a separate job owns the build. |
| `DBT_TARGET` | `prod` | The target in the project's `profiles.yml` to run against (`dbt --target`). |
| `DB_ENV_VAR` | `MOTHERDUCK_DATABASE` | The env var the profile's target reads its database name from, via `env_var()`. |
| `MODELS_DATABASE` | `dbt_churn_flight` | Database the models build into; fed to the profile through `DB_ENV_VAR`. |
| `SELECT` | (empty) | Optional dbt node selection, e.g. `tag:nightly`. Empty runs the whole project. |
| `SNAPSHOT_DATABASE` | `dbt_churn_flight` | Database holding the `run_results` history. Created if missing. |
| `SNAPSHOT_TABLE` | `dbt_run_results` | Append-only table of node results (`run_at`, config, typed columns, `node` JSON). |

`GIT_TOKEN` is **not** a config key — it is a secret. Store it in a `TYPE
flights` secret (see [Deploy as a Flight](#deploy-as-a-flight)) so it never lands
in the Flight's `config` MAP or the logs. Public repos need no token at all.
`MOTHERDUCK_TOKEN` is likewise not config: the runtime injects it automatically
at run time.

## Run it

You need a MotherDuck account and an access token. The default repo/ref make a
fresh deploy produce a successful run with no other credentials.

Smoke-test locally before deploying (this downloads the project over HTTPS,
builds it in your account, and appends one batch of rows to `dbt_run_results`):

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with-requirements requirements.txt flight.py
```

Override any default inline, for example test-only mode against your own repo.
For a private repo, set `GIT_TOKEN` as a bare env var locally (deployed, it comes
from the Flights secret instead):

```bash
RUN_MODE=test \
GIT_REPO=https://github.com/you/your-dbt-repo.git GIT_REF=main REPO_SUBDIR=analytics \
GIT_TOKEN=github_pat_... \
  uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT` (no deploy SQL is checked in; adapt the
arguments), passing:

- `name`: a Flight name, for example `dbt_build`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: a `MAP` of the knobs above — `GIT_REPO`/`GIT_REF`/`REPO_SUBDIR` for
  your project, `RUN_MODE`, `DBT_TARGET`/`DB_ENV_VAR`, `MODELS_DATABASE`, and
  `SNAPSHOT_DATABASE`/`SNAPSHOT_TABLE`
- `flight_secret_names` (private repos only): the `TYPE flights` secrets to inject,
  e.g. `['git_auth']`. **Required for a private repo** — see below.

A MotherDuck token is attached automatically and injected at run time as
`MOTHERDUCK_TOKEN`; no token argument is needed. For a **private** dbt repo, two
steps are both required — create the secret **and** attach it to the Flight.

First create a `TYPE flights` secret holding a GitHub token with **Contents:
Read-only** on that repo. Flights-secret params go inside a `PARAMS MAP` (a bare
`GIT_TOKEN '…'` property is rejected with `Unknown parameter 'git_token'`):

```sql
CREATE SECRET git_auth IN motherduck (
  TYPE flights,
  PARAMS MAP {'GIT_TOKEN': 'github_pat_...'}
);
```

Then **attach it** by passing `flight_secret_names := ['git_auth']` to
`MD_CREATE_FLIGHT`. At run time the runtime injects each param as
`<secret_name>_<PARAM>` (here `git_auth_GIT_TOKEN`); `resolve_secret` in
`flight.py` matches any env var ending in `_GIT_TOKEN`, so the secret name is yours
to choose. Only when the secret is **both created and attached** does the Flight
take the authenticated GitHub API archive endpoint — miss either step and it falls
back to the public endpoint, which 404s on a private repo. If the repo lives in a
SAML-protected org, the token must also be **SSO-authorized** for that org (an
unauthorized token 403s).

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := …)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_LIST_FLIGHTS()`), and confirm the snapshot database and a batch of
`dbt_run_results` rows appear. Once green, add a schedule by updating
`schedule_cron` with `MD_UPDATE_FLIGHT` (`0 6 * * *`, 06:00 UTC daily, is a
reasonable default); schedule updates are metadata-only and do not create a new
version.

## Security

- **Identifier safety.** `SNAPSHOT_DATABASE` and `SNAPSHOT_TABLE` flow into
  `CREATE`/`INSERT` statements that cannot be parameterized, so each is
  double-quote-escaped (`_ident`) before any SQL runs.
- **Parameterized data.** Every value written into the snapshot row (the git
  repo/ref, the run mode, and the node results parsed from `run_results.json`) is
  passed as a bound parameter, never string-formatted into SQL.
- **Fetch a trusted ref.** The Flight runs whatever code the fetched ref
  contains. Point `GIT_REPO`/`GIT_REF` at a repo and branch/tag you control; pin
  a tag or SHA for reproducibility.
- **Token in a secret, in the header.** A private repo's `GIT_TOKEN` belongs in a
  `TYPE flights` secret, never in `config` (which is stored on the Flight and
  logged). At run time the token is sent in the `Authorization` header of the
  GitHub API request, not in the URL, so it does not reach the Flight logs.

## Learn more

- Flight mechanics (creating, running, scheduling, secrets): the MotherDuck MCP
  `get_flight_guide` tool.
- The local, terminal-driven dbt example this Flight builds by default:
  [dbt-churn-prediction](../../dbt-churn-prediction).
- The MetricFlow sibling of this template, which builds the same fetch engine
  around `mf query`: [flight-dbt-metricflow](../flight-dbt-metricflow).
- Deeper MotherDuck or DuckDB questions: the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight that
  downloads the project over HTTPS, runs dbt, and snapshots `run_results.json`)
  and [`requirements.txt`](requirements.txt) (`dbt-core`, `dbt-duckdb`,
  `duckdb`).
