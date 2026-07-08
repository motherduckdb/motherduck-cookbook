---
title: Run a dbt Project as a Flight via git clone
id: flight-dbt-build-git
description: >-
  A reusable Flight that checks a dbt project out with git at run time (the
  Flights runtime preinstalls git), runs dbt build on MotherDuck, and appends
  dbt's run_results.json to a snapshot table tagged with the exact commit SHA
  it built. Use when you want a scheduled dbt build fetched by git clone from
  any git host, with per-commit build history.
type: template
category: automation
features: [flights]
tags: [dbt]
prompt: >-
  I want one deployed Flight that git-clones my dbt project at run time, builds
  it on MotherDuck on a schedule, and keeps a queryable history of build and
  test health tagged with the exact commit SHA it built. Help me adapt the
  "Run a dbt Project as a Flight via git clone" recipe to my own dbt repo and
  use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-dbt-build-git
published_date: 2026-07-09
---

# Run a dbt Project as a Flight via git clone

A single-file Flight that runs a dbt project on MotherDuck and records what
happened — checking the project out **with `git` at run time** (the Flights
runtime preinstalls the binary) and appending dbt's own `run_results.json` to a
snapshot table, one row per node per run. It is the git-clone sibling of
[flight-dbt-build-gh-archive](../flight-dbt-build-gh-archive), which predates preinstalled git and
downloads a GitHub archive over HTTPS instead. Fetching with git changes three
things:

- **Any git host works** — GitHub, GitLab, Bitbucket, self-hosted — not just
  GitHub's archive endpoints.
- **Every snapshot row records the exact commit SHA built** (`git_sha`), not
  just the ref name, so "which commit broke the nightly build?" is one query.
- **A private repo authenticates the way git does**: the token reaches git via
  `GIT_ASKPASS`, never a URL or command line.

Everything downstream of the fetch — running `dbt build`/`dbt test`, the
build-vs-test modes, the failure policy, the per-run config-override pattern,
and the snapshot table queries — matches [flight-dbt-build-gh-archive](../flight-dbt-build-gh-archive).

## How it works

1. Read config from the environment (Flight `config` keys arrive as env vars).
2. Connect to MotherDuck (`md:`) and `CREATE DATABASE IF NOT EXISTS` the
   snapshot and models databases.
3. Check out `GIT_REPO`@`GIT_REF` into a temp dir: `git init` + `git fetch
   --depth 1 origin <ref>` + `git checkout FETCH_HEAD`. A fetch refspec accepts
   a **branch, tag, or full commit SHA** uniformly, which is why the Flight
   fetches into `FETCH_HEAD` instead of using `git clone --branch` (that flag
   rejects SHAs). `--depth 1` keeps the transfer shallow. The resolved
   `git rev-parse HEAD` is captured for the snapshot.
4. If a `GIT_TOKEN` secret resolves, the remote URL gets `GIT_USERNAME` (a host
   convention — `x-access-token` for GitHub, `oauth2` for GitLab) and git reads
   the token through a one-line `GIT_ASKPASS` script. No token: public clone.
5. Discover the dbt project (`dbt_project.yml`) and its profile (`profiles.yml`)
   inside `REPO_SUBDIR` of the checkout; empty `REPO_SUBDIR` means the repo root
   is the project.
6. Run dbt with `--target DBT_TARGET`. `RUN_MODE=build` runs `dbt seed` then
   `dbt build`; `RUN_MODE=test` runs `dbt test` only. The profile reads its
   database name from `env_var()`, fed via `DB_ENV_VAR`/`MODELS_DATABASE`.
7. Append one row per dbt node from `target/run_results.json` to the snapshot
   table, tagged with `run_at`, repo, ref, **`git_sha`**, dbt version, and run
   mode. Node errors fail the run; test assertion failures are recorded but
   keep the run green (see the failure policy in
   [flight-dbt-build-gh-archive](../flight-dbt-build-gh-archive)).

```
config (env) ── git fetch+checkout ── dbt build/test ── run_results.json ── snapshot table
  override per run  GIT_REPO@GIT_REF     on MotherDuck     one row per node    append, git_sha, run_at
```

With `git_sha` on every row, regressions map straight to commits:

```sql
SELECT git_sha, min(run_at) AS first_seen, count(*) AS failing_tests
FROM dbt_churn_flight_git.dbt_run_results
WHERE resource_type = 'test' AND status = 'fail'
GROUP BY git_sha ORDER BY first_seen DESC;
```

## Questions to answer

- Which git repo, ref, and subdirectory hold the dbt project? Is the host
  GitHub (default `GIT_USERNAME` works) or another git host (set the username
  convention it expects)?
- Is the repo private (needs a `GIT_TOKEN` flights secret) or public?
- Should each run **build** the project (`RUN_MODE=build`) or only **test** an
  already-built one (`RUN_MODE=test`)?
- What target does the project's `profiles.yml` define (`DBT_TARGET`), and which
  env var does that target read the database name from (`DB_ENV_VAR`)?
- Which database should hold the models (`MODELS_DATABASE`) and the
  `run_results` history (`SNAPSHOT_DATABASE`/`SNAPSHOT_TABLE`)?
- Should the build run on a schedule, and at what cadence?

## Caveats

- **Run-time network to the git host.** The Flight clones at run time, so it
  needs HTTPS egress to wherever `GIT_REPO` lives. Only HTTPS remotes are
  supported — there is no SSH key material in a Flight, so `git@…` URLs won't
  work.
- **A private repo needs a `GIT_TOKEN` secret — created and attached.** Without
  one the clone runs unauthenticated and fails on a private repo. See
  [Deploy as a Flight](#deploy-as-a-flight).
- **`GIT_USERNAME` must match the host's convention.** `x-access-token` (the
  default) is for GitHub PATs; GitLab expects `oauth2`. A wrong username reads
  as a 401/403 on fetch even with a valid token.
- **Snapshot schema is a superset of flight-dbt-build-gh-archive's.** This template adds a
  `git_sha` column, so don't point both templates at the same
  `SNAPSHOT_DATABASE`.`SNAPSHOT_TABLE` — the INSERTs have different column
  counts. Defaults differ (`dbt_churn_flight_git`) so a fresh deploy can't
  collide.
- **The build runs every time, and concurrent builds conflict.** Parallel
  `build` runs collide on the shared tables (write-write conflict); use
  `RUN_MODE=test` for fan-out or read-only runs. Build mode seeds before it
  builds so a cold first run on a new database succeeds; test mode assumes the
  tables exist. Test failures are recorded but don't fail the Flight; node
  errors do. These behaviors are shared with — and explained in —
  [flight-dbt-build-gh-archive](../flight-dbt-build-gh-archive).
- **The project's profile must match your config.** Its `profiles.yml` must
  expose the target database through `env_var()` matching `DB_ENV_VAR` and
  define the `DBT_TARGET` target. The default `dbt-churn-prediction` reads
  `MOTHERDUCK_DATABASE` and has a `prod` target.
- **Keep tokens out of config.** The runtime injects `MOTHERDUCK_TOKEN`
  automatically, and the git token belongs in a flights secret; `config` is
  stored unencrypted on the Flight.

## What you'll adjust

Every knob is a config/env value read by `read_config()` at the top of
`flight.py`; set them as Flight config rather than by editing code. The dbt
project itself lives in the git repo you point the Flight at, not in `flight.py`.

| Config key | Default | Purpose |
|---|---|---|
| `GIT_REPO` | `…/motherduck-cookbook.git` | HTTPS remote of the repo holding the dbt project. Any git host. |
| `GIT_REF` | `main` | Branch, tag, or full commit SHA to check out. |
| `GIT_USERNAME` | `x-access-token` | Username git authenticates with when a token resolves (GitHub: `x-access-token`, GitLab: `oauth2`). Ignored for public repos. |
| `REPO_SUBDIR` | `dbt-churn-prediction` | Path within the repo that holds the dbt project. Empty = repo root. |
| `RUN_MODE` | `build` | `build`: `dbt build` (seed + run + test). `test`: `dbt test` only (read-only, parallel-safe). |
| `DBT_TARGET` | `prod` | The target in the project's `profiles.yml` to run against (`dbt --target`). |
| `DB_ENV_VAR` | `MOTHERDUCK_DATABASE` | The env var the profile's target reads its database name from, via `env_var()`. |
| `MODELS_DATABASE` | `dbt_churn_flight_git` | Database the models build into; fed to the profile through `DB_ENV_VAR`. |
| `SELECT` | (empty) | Optional dbt node selection, e.g. `tag:nightly`. Empty runs the whole project. |
| `SNAPSHOT_DATABASE` | `dbt_churn_flight_git` | Database holding the `run_results` history. Created if missing. |
| `SNAPSHOT_TABLE` | `dbt_run_results` | Append-only table of node results (`run_at`, `git_sha`, typed columns, `node` JSON). |

`GIT_TOKEN` is **not** a config key — it is a secret (see
[Deploy as a Flight](#deploy-as-a-flight)). `MOTHERDUCK_TOKEN` is likewise not
config: the runtime injects it automatically at run time.

## Run it

You need a MotherDuck account and an access token, plus a local `git`. The
default repo/ref make a fresh deploy produce a successful run with no other
credentials.

Smoke-test locally before deploying (this clones the project, builds it in your
account, and appends one batch of rows to `dbt_run_results`):

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with-requirements requirements.txt flight.py
```

Override any default inline, for example against your own private repo (locally
`GIT_TOKEN` is a bare env var; deployed, it comes from the flights secret):

```bash
GIT_REPO=https://github.com/you/your-dbt-repo.git GIT_REF=main REPO_SUBDIR= \
GIT_TOKEN=github_pat_... \
  uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT` (no deploy SQL is checked in; adapt the
arguments), passing:

- `name`: a Flight name, for example `dbt_build_git`
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
`MD_RUN_FLIGHT(flight_id := …)`, and confirm the snapshot database and a batch
of `dbt_run_results` rows appear (with `git_sha` populated). Once green, add a
schedule with `MD_UPDATE_FLIGHT` (`0 6 * * *`, 06:00 UTC daily, is a reasonable
default); schedule updates are metadata-only and do not create a new version.

## Security

- **Token via `GIT_ASKPASS`, never in an argv or URL.** The Flight logs every
  command it runs, so the token cannot ride in the remote URL or a `-c
  http.extraheader` flag. Instead git invokes a one-line askpass script that
  reads the token from its environment; only the non-secret `GIT_USERNAME`
  appears in the logged URL. `GIT_TERMINAL_PROMPT=0` makes a missing credential
  fail fast instead of hanging the run.
- **Identifier safety.** `SNAPSHOT_DATABASE` and `SNAPSHOT_TABLE` flow into
  `CREATE`/`INSERT` statements that cannot be parameterized, so each is
  double-quote-escaped (`_ident`) before any SQL runs.
- **Parameterized data.** Every value written into the snapshot row (repo, ref,
  SHA, run mode, and the node results parsed from `run_results.json`) is passed
  as a bound parameter, never string-formatted into SQL.
- **Clone a trusted ref.** The Flight runs whatever code the checked-out ref
  contains. Point `GIT_REPO`/`GIT_REF` at a repo and branch/tag you control; pin
  a tag or SHA for reproducibility — the snapshot's `git_sha` column tells you
  exactly what ran either way.

## Learn more

- The archive-download sibling of this template (same dbt engine, HTTPS fetch,
  GitHub-only): [flight-dbt-build-gh-archive](../flight-dbt-build-gh-archive). Its README covers the
  shared build-vs-test modes, failure policy, config-override pattern, and
  snapshot-table queries in depth.
- The local, terminal-driven dbt example this Flight builds by default:
  [dbt-churn-prediction](../../dbt-churn-prediction).
- Flight mechanics (creating, running, scheduling, secrets): the MotherDuck MCP
  `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions: the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight that
  clones the project, runs dbt, and snapshots `run_results.json`),
  [`requirements.txt`](requirements.txt) (`dbt-core`, `dbt-duckdb`, `duckdb` —
  no git package; the runtime preinstalls the binary), and [`config`](config)
  (the default Flight config MAP as JSON).
