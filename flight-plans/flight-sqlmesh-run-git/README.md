---
title: Run a SQLMesh Project as a Flight via git clone
id: flight-sqlmesh-run-git
description: >-
  A reusable Flight that checks a SQLMesh project out with git at run time,
  optionally runs its dlt loader, then applies `sqlmesh plan` and `sqlmesh run`
  against MotherDuck and appends one row per run, tagged with the exact commit
  SHA, to a history table. Use when you want a scheduled SQLMesh deploy fetched
  by git clone from any git host, with SQLMesh state kept in MotherDuck.
type: template
category: automation
features: [flights]
tags: [sqlmesh, dlt]
prompt: >-
  I want one deployed Flight that git-clones my SQLMesh project at run time,
  applies plans and runs due models on MotherDuck on a schedule, and keeps a
  queryable history of each run tagged with the exact commit SHA it built. Help
  me adapt the "Run a SQLMesh Project as a Flight via git clone" recipe to my
  own SQLMesh repo and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-sqlmesh-run-git
published_date: 2026-09-03
---

# Run a SQLMesh Project as a Flight via git clone

A single-file Flight that runs a [SQLMesh](https://sqlmesh.readthedocs.io/en/stable/)
project on MotherDuck the way a scheduler would, and records what happened. It
checks the project out **with `git` at run time** (the Flights runtime
preinstalls the binary), optionally runs a load script from the same checkout
so the raw tables exist, then drives SQLMesh's two production commands:
`sqlmesh plan --auto-apply` applies whatever model changes the commit carries
and backfills them, and `sqlmesh run` evaluates every model whose `cron` is due.
One row per run, tagged with the exact commit SHA built, lands in a history
table.

By default it builds this cookbook's [sqlmesh-demo](../../sqlmesh-demo): a dlt
loader pulls Yahoo Finance stock data into `stock_data.*`, and SQLMesh builds
interim (`INCREMENTAL_BY_TIME_RANGE` + `SCD_TYPE_2_BY_TIME`), conformed, and
mart layers on top. Scheduling that pair is what makes the demo's SCD type 2
models meaningful: each run is one more dlt load *and* one more snapshot in
time, so company-info and option-chain history accumulates run over run.

Three properties of the design:

- **SQLMesh state lives in MotherDuck, not in the Flight.** SQLMesh stores its
  snapshots, intervals, and environments in the database it targets, so the
  Flight is stateless and a redeploy or a new version loses nothing. The Flight
  never touches that state; it only appends its own run row.
- **Any git host works** and **every run row records the exact commit SHA**
  (`git_sha`), so "which commit broke the nightly run?" is one query. A private
  repo authenticates the way git does: the token reaches git via `GIT_ASKPASS`,
  never a URL or command line.
- **The project's config is not edited.** The Flight points the project's
  gateway at `MODELS_DATABASE` through SQLMesh's own environment-variable
  overrides (`SQLMESH__GATEWAYS__<GATEWAY>__CONNECTION__DATABASE`), and any
  other `SQLMESH__*` key you put in the Flight config passes straight through.

## How it works

1. Read config from the environment (Flight `config` keys arrive as env vars).
2. Connect to MotherDuck (`md:`) and `CREATE DATABASE IF NOT EXISTS` the
   snapshot and models databases.
3. Check out `GIT_REPO`@`GIT_REF` into a temp dir: `git init` + `git fetch
   --depth 1 origin <ref>` + `git checkout FETCH_HEAD`. A fetch refspec accepts
   a **branch, tag, or full commit SHA** uniformly, which is why the Flight
   fetches into `FETCH_HEAD` instead of using `git clone --branch` (that flag
   rejects SHAs). The resolved `git rev-parse HEAD` is captured for the row.
4. If a `GIT_TOKEN` secret resolves, the remote URL gets `GIT_USERNAME` (a host
   convention: `x-access-token` for GitHub, `oauth2` for GitLab) and git reads
   the token through a one-line `GIT_ASKPASS` script. No token: public clone.
5. Locate the SQLMesh project at `REPO_SUBDIR/SQLMESH_PROJECT` (it must hold a
   `config.yaml`, `config.yml`, or `config.py`).
6. If `LOAD_SCRIPT` is set, run it with `python` from its own directory, with
   dlt's MotherDuck destination pointed at `MODELS_DATABASE` via
   `DESTINATION__MOTHERDUCK__CREDENTIALS__DATABASE`. The default is the demo's
   dlt loader; a non-zero exit stops the run here.
7. In `RUN_MODE=plan_run`, run `sqlmesh -p <project> --gateway <gw> plan <env>
   --auto-apply --no-prompts`. Non-interactive on purpose: a Flight has no
   terminal, so an uncategorized change fails the plan instead of waiting.
8. Run `sqlmesh -p <project> --gateway <gw> run <env>`. Models whose `cron`
   interval is not yet due are skipped, so scheduling the Flight more often
   than the most frequent model cron is safe.
9. Append one row to the history table: `run_at`, repo, ref, **`git_sha`**,
   SQLMesh version, environment, run mode, per-step status (`load`, `plan`,
   `run`: `success`/`audit_failed`/`error`/`skipped`), duration, and the tail
   of each step's output. Then apply the failure policy: an execution error
   fails the Flight; failed audits are recorded as `audit_failed` and keep the
   run green, the same policy as the cookbook's dbt Flight plans.

```
config (env) ── git fetch+checkout ── LOAD_SCRIPT ── sqlmesh plan ── sqlmesh run ── history row
 override per run  GIT_REPO@GIT_REF    dlt → raw      --auto-apply    due crons     append, git_sha
```

With `git_sha` and per-step status on every row, regressions map straight to
commits:

```sql
SELECT run_at, git_sha, status, load_status, plan_status, run_status, right(plan_output, 400) AS tail
FROM sqlmesh_flight_git.sqlmesh_flight_runs
WHERE status <> 'success'          -- 'error' (red run) or 'audit_failed' (green run, quality drift)
ORDER BY run_at DESC;
```

## Questions to answer

- Which git repo, ref, and subdirectory hold the SQLMesh project? Is the host
  GitHub (default `GIT_USERNAME` works) or another git host (set the username
  convention it expects)?
- Is the repo private (needs a `GIT_TOKEN` flights secret) or public?
- Does the Flight also **load** the raw data (`LOAD_SCRIPT` set) or only
  transform it (`LOAD_SCRIPT` empty, because another job or Flight such as
  [flight-dlt-ingest](../flight-dlt-ingest) owns ingestion)?
- Should each run **plan and run** (`RUN_MODE=plan_run`, the Flight owns the
  deploy) or only **run** (`RUN_MODE=run`, because CI applies plans)?
- Which gateway in the project's config should it use (`SQLMESH_GATEWAY`), and
  which SQLMesh environment (`SQLMESH_ENVIRONMENT`, normally `prod`)?
- Which MotherDuck database holds the raw tables, models, and SQLMesh state
  (`MODELS_DATABASE`), and which holds the run history
  (`SNAPSHOT_DATABASE`/`SNAPSHOT_TABLE`)?
- How often should it run? At least as often as the most frequent model `cron`
  in the project (the demo's models are all `@daily`).

## Caveats

- **Run-time network to the git host, and to whatever the loader reads.** The
  Flight clones at run time and needs HTTPS egress to `GIT_REPO`. Only HTTPS
  remotes are supported: there is no SSH key material in a Flight, so `git@…`
  URLs won't work. The default loader also calls Yahoo Finance through
  `yfinance`, an unofficial, rate-limited client; it skips symbols that fail and
  prints rather than raising, so check the `load_output` column if a ticker is
  missing downstream.
- **A private repo needs a `GIT_TOKEN` secret, created and attached.** Without
  one the clone runs unauthenticated and fails on a private repo. See
  [Deploy as a Flight](#deploy-as-a-flight).
- **`GIT_USERNAME` must match the host's convention.** `x-access-token` (the
  default) is for GitHub PATs; GitLab expects `oauth2`. A wrong username reads
  as a 401/403 on fetch even with a valid token.
- **SQLMesh state is stored in MotherDuck.** SQLMesh warns that it does not
  recommend the motherduck engine for production state, because state writes
  are small transactions and concurrent writers can corrupt state. It works,
  and it is the zero-extra-infrastructure choice for a Flight, but do **not**
  run two instances of this Flight (or a laptop `sqlmesh plan`) against the same
  `MODELS_DATABASE` at the same time. To move state to Postgres, add
  `SQLMESH__GATEWAYS__<GATEWAY>__STATE_CONNECTION__*` keys to the Flight config;
  they pass straight through to SQLMesh.
- **Audit failures are recorded but do not fail the Flight; execution errors
  do.** This matches the cookbook's dbt Flight plans: quality drift is a trend
  to query, not a page. The Flight tells the two apart from SQLMesh's output
  (an audit failure reads `'<audit>' audit error: N rows failed`; an execution
  error leads with an exception class such as `BinderException:`), records
  `audit_failed` on the step and the run, and exits green. Anything it cannot
  classify is treated as an error, the fail-safe direction. SQLMesh's audits
  remain blocking on their side: the failing model is not promoted, and a plan
  that fails audits leaves the environment unfinalized, so `run` is skipped for
  that run and the next scheduled run re-plans. Query
  `WHERE status = 'audit_failed'` to watch for it, or mark an audit
  `blocking false` in the model to let the data through.
- **`--no-prompts` fails on uncategorized changes.** SQLMesh auto-categorizes
  most changes as breaking or non-breaking. When it cannot, an interactive plan
  would ask; the Flight fails instead, with the reason in `plan_output`.
  Categorize the change explicitly (or plan it once from a terminal) and re-run.
- **Dropping a column from an SCD type 2 or other forward-only model fails the
  plan.** SQLMesh treats `SCD_TYPE_2_*` models as forward-only and refuses
  destructive schema changes by default (`Plan requires a destructive change to
  a forward-only model`). When you trim such a model, set
  `on_destructive_change allow` on it or plan once from a terminal with
  `--allow-destructive-model`; the Flight deliberately does not pass that flag
  for every run.
- **A failed plan leaves the environment unfinalized, and `sqlmesh run` waits
  for it.** In `plan_run` mode the Flight skips `run` after a failed plan, so
  the run fails fast. In `RUN_MODE=run`, a `run` against an environment whose
  last plan failed elsewhere retries every 30 seconds until the Flight's
  timeout; fix the model and apply a successful plan before the next run.
- **Changing `MODELS_DATABASE` leaves the demo's external models pointing at
  the old name.** `sqlmesh-demo/transform/external_models.yaml` declares the raw
  tables as `"dlt_test_db"."stock_data".*`. The models still resolve (they
  reference `stock_data.*`, which SQLMesh qualifies with the gateway's
  database), but SQLMesh lists the declared externals as separate models and
  loses their column metadata. Regenerate the file with
  `sqlmesh create_external_models` against the new database when that matters.
- **The demo loader is a full reload each run.** Its dlt resources use
  `write_disposition="replace"`, and `stock_history` pulls only the trailing 360
  days. SQLMesh's SCD type 2 history comes from comparing loads over time, so
  the models only accumulate history if this Flight runs on a schedule.
- **Keep tokens out of config.** The runtime injects `MOTHERDUCK_TOKEN`
  automatically, and the git token belongs in a flights secret; `config` is
  stored unencrypted on the Flight.

## What you'll adjust

Every knob is a config/env value read by `read_config()` at the top of
`flight.py`; set them as Flight config rather than by editing code. The SQLMesh
project itself lives in the git repo you point the Flight at, not in
`flight.py`.

| Config key | Default | Purpose |
|---|---|---|
| `GIT_REPO` | `…/motherduck-cookbook.git` | HTTPS remote of the repo holding the SQLMesh project. Any git host. |
| `GIT_REF` | `main` | Branch, tag, or full commit SHA to check out. |
| `GIT_USERNAME` | `x-access-token` | Username git authenticates with when a token resolves (GitHub: `x-access-token`, GitLab: `oauth2`). Ignored for public repos. |
| `REPO_SUBDIR` | `sqlmesh-demo` | Path within the repo to the example root. Empty = repo root. |
| `SQLMESH_PROJECT` | `transform` | Path within `REPO_SUBDIR` to the SQLMesh project (holds `config.yaml`). Empty = `REPO_SUBDIR` itself. |
| `LOAD_SCRIPT` | `load/stock_data_pipeline.py` | Python script within `REPO_SUBDIR` to run before SQLMesh (the demo's dlt loader). Empty = skip loading. |
| `RUN_MODE` | `plan_run` | `plan_run`: `sqlmesh plan --auto-apply --no-prompts` then `sqlmesh run`. `run`: `sqlmesh run` only. |
| `SQLMESH_GATEWAY` | `local` | Gateway name in the project's config (`sqlmesh --gateway`). The demo defines `local`. |
| `SQLMESH_ENVIRONMENT` | `prod` | SQLMesh environment to plan and run. |
| `MODELS_DATABASE` | `dlt_test_db` | MotherDuck database for raw tables, SQLMesh models, and SQLMesh state. Overrides the gateway's `database` and the dlt destination database. Created if missing. |
| `SNAPSHOT_DATABASE` | `sqlmesh_flight_git` | Database holding the run history. Created if missing. |
| `SNAPSHOT_TABLE` | `sqlmesh_flight_runs` | Append-only table of runs (`run_at`, `git_sha`, `status`, per-step `success`/`audit_failed`/`error`/`skipped`, output tails). |
| `SQLMESH__*` | (none) | Any extra key with this prefix is forwarded to SQLMesh as a config override, e.g. a Postgres state connection. |

`GIT_TOKEN` is **not** a config key; it is a secret (see
[Deploy as a Flight](#deploy-as-a-flight)). `MOTHERDUCK_TOKEN` is likewise not
config: the runtime injects it automatically at run time, and SQLMesh, dlt, and
the DuckDB client all read it from the environment.

To run your own project, change `GIT_REPO`/`REPO_SUBDIR`/`SQLMESH_PROJECT`,
set `LOAD_SCRIPT` to your loader or empty, and edit
[`requirements.txt`](requirements.txt): keep `sqlmesh` and `duckdb`, and swap
the `dlt`/`yfinance` lines for whatever your loader imports.

## Run it

You need a MotherDuck account and an access token, plus a local `git`. The
default repo/ref make a fresh deploy load the demo data and build the project
with no other credentials. The first run backfills every model; later runs load
fresh prices and only evaluate models whose cron is due.

Smoke-test locally before deploying (this clones the project, loads about 11
tickers from Yahoo Finance into your account, plans and runs SQLMesh there, and
appends one row to `sqlmesh_flight_runs`; expect a few minutes):

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with-requirements requirements.txt flight.py
```

Override any default inline, for example against your own private repo with no
load step (locally `GIT_TOKEN` is a bare env var; deployed, it comes from the
flights secret):

```bash
GIT_REPO=https://github.com/you/your-sqlmesh-repo.git GIT_REF=main \
REPO_SUBDIR= SQLMESH_PROJECT= LOAD_SCRIPT= SQLMESH_GATEWAY=motherduck \
MODELS_DATABASE=analytics GIT_TOKEN=github_pat_... \
  uv run --with-requirements requirements.txt flight.py
```

Check the result:

```sql
SELECT run_at, git_sha, status, load_status, plan_status, run_status, round(duration_s) AS seconds
FROM sqlmesh_flight_git.sqlmesh_flight_runs
ORDER BY run_at DESC;

SELECT * FROM dlt_test_db.mart.stock_price_by_day ORDER BY trade_date DESC LIMIT 10;
```

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT` (no deploy SQL is checked in; adapt the
arguments), passing:

- `name`: a Flight name, for example `sqlmesh_run_git`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: a `MAP` of the knobs above (see [`config`](config) for the full
  default set)
- `flight_secret_names` (private repos only): the `TYPE flights` secrets to
  inject, e.g. `['git_auth']`

A MotherDuck token is attached automatically and injected at run time as
`MOTHERDUCK_TOKEN`. For a **private** repo, create the secret **and** attach it:

```sql
CREATE SECRET git_auth IN motherduck (
  TYPE flights,
  PARAMS MAP {'GIT_TOKEN': 'github_pat_...'}
);
```

Then pass `flight_secret_names := ['git_auth']` to `MD_CREATE_FLIGHT`. At run
time each param is injected as `<secret_name>_<PARAM>` (here
`git_auth_GIT_TOKEN`); `resolve_secret` in `flight.py` matches any env var
ending in `_GIT_TOKEN`, so the secret name is yours to choose. A GitHub token
needs **Contents: Read-only** on the repo; in a SAML-protected org it must also
be SSO-authorized. Miss either the create or the attach and the clone runs
unauthenticated and fails on a private repo.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := …)`, and confirm a `success` row in
`sqlmesh_flight_runs` (with `git_sha` populated) and the `mart.stock_price_by_day`
view in `MODELS_DATABASE`. A green run with `status = 'audit_failed'` means the
project built but a blocking audit failed; check `plan_output`/`run_output`. Once green, add a schedule with `MD_UPDATE_FLIGHT`.
Daily (`0 6 * * *`, 06:00 UTC) matches the demo's `@daily` crons; the first run
of each day loads fresh prices and evaluates the due models, and a second run
the same day would reload but find nothing due. Schedule updates are
metadata-only and do not create a new version.

## Security

- **Token via `GIT_ASKPASS`, never in an argv or URL.** The Flight logs every
  command it runs, so the token cannot ride in the remote URL or a `-c
  http.extraheader` flag. Instead git invokes a one-line askpass script that
  reads the token from its environment; only the non-secret `GIT_USERNAME`
  appears in the logged URL. `GIT_TERMINAL_PROMPT=0` makes a missing credential
  fail fast instead of hanging the run.
- **Identifier safety.** `SNAPSHOT_DATABASE`, `SNAPSHOT_TABLE`, and
  `MODELS_DATABASE` flow into `CREATE` statements that cannot be parameterized,
  so each is double-quote-escaped (`_ident`) before any SQL runs.
- **Parameterized data.** Every value written into the run row (repo, ref, SHA,
  version, statuses, and the captured step output) is passed as a bound
  parameter, never string-formatted into SQL.
- **Clone a trusted ref, and mind the load script.** The Flight runs whatever
  code the checked-out ref contains, including `LOAD_SCRIPT` as an arbitrary
  Python program with your MotherDuck token in its environment. Point
  `GIT_REPO`/`GIT_REF` at a repo and branch/tag you control; pin a tag or SHA
  for reproducibility. The `git_sha` column tells you exactly what ran either
  way.
- **Step output lands in a table.** The tail of each step's stdout/stderr is
  stored in `SNAPSHOT_TABLE`. SQLMesh and dlt do not print credentials, but a
  custom loader that logs its config would; keep secrets out of print
  statements.

## Learn more

- The local, terminal-driven example this Flight builds by default, including
  the SQLMesh model kinds and audits it uses:
  [sqlmesh-demo](../../sqlmesh-demo).
- A Flight that owns dlt ingestion on its own, for the `LOAD_SCRIPT=` split:
  [flight-dlt-ingest](../flight-dlt-ingest).
- SQLMesh's own guidance on `plan` vs `run`, environments, and state
  connections: the [SQLMesh docs](https://sqlmesh.readthedocs.io/en/stable/).
- Flight mechanics (creating, running, scheduling, secrets): the MotherDuck MCP
  `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions: the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight that
  clones the project, runs the loader and SQLMesh, and appends the run row),
  [`requirements.txt`](requirements.txt) (`sqlmesh`, `dlt[motherduck]`,
  `yfinance`, `duckdb`; no git package, the runtime preinstalls the binary),
  and [`config`](config) (the default Flight config MAP as JSON).
