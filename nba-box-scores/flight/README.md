# nba-box-scores — flight (ingest pipeline)

Python ingest pipeline for `nba_box_scores_v3`, deployed as MotherDuck Flights.
Replaces the legacy TypeScript pipeline + GitHub Actions cron at
[`matsonj/nba-box-scores`](https://github.com/matsonj/nba-box-scores). The
companion Dive (frontend) lives in [`../dive`](../dive).

`nba_nightly` (cron `0 16 * * *` UTC) ingests the current season's box scores
into the production table set; `nba_backfill` is on-demand for historical
season ranges.

## Layout

```
nba-box-scores/flight/
├── pyproject.toml                # uv / hatchling
├── src/nba_box_scores_pipeline/  # importable package, used by every Flight
└── flights/                      # one folder per Flight
    ├── nba_nightly/              # scheduled (0 16 * * * UTC)
    └── nba_backfill/             # on-demand (no schedule_cron)
```

## Develop locally

```bash
cd nba-box-scores/flight
uv venv
uv pip install -e ".[dev]"

export MOTHERDUCK_TOKEN=<your token with v3 read+write>
python flights/nba_nightly/main.py
```

`NBA_INGEST_TABLE_SUFFIX` defaults to `""` (production); set it (e.g. `_new`)
to write an isolated sandbox table set for validation. In the Flight runtime,
`MOTHERDUCK_TOKEN` is injected from the token named `dives-loader-nba`.

## Deploy

One command registers (or updates) both Flights:

```bash
export MOTHERDUCK_TOKEN=<token that can manage flights>
uv run scripts/deploy_flights.py               # all flights
uv run scripts/deploy_flights.py nba_nightly   # just one
```

[`scripts/deploy_flights.py`](./scripts/deploy_flights.py) reads each
`flights/<name>/flight.toml` (name, `md_token_name`, `schedule_cron`, config,
secrets) plus its `main.py`, then calls `MD_CREATE_FLIGHT` / `MD_UPDATE_FLIGHT`,
resolving the flight by name via `MD_FLIGHTS()` — create the first time, update
after. No flight id is pinned in the repo, and the pinned `duckdb` dependency is
already a MotherDuck-compatible client.

The registered `source_code` is the thin bootstrapper in `flights/<name>/main.py`:
it clones this repo at `NBA_FLIGHT_REPO_BRANCH` (default `main`), `uv sync`s the
package, and runs the entrypoint from the synced venv. So you only run the deploy
command for the **first** registration (or when the bootstrapper, token,
schedule, config, or requirements change) — **shipping new pipeline code
afterwards is just a `git push`** to the branch the bootstrapper clones. If
you'd rather not run the script, call the same `MD_CREATE_FLIGHT` /
`MD_UPDATE_FLIGHT` SQL functions from any MotherDuck connection.
