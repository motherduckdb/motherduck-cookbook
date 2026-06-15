# StatsBomb Flights — `raw -> core -> marts`

Three MotherDuck Flights that turn [StatsBomb open-data](https://github.com/statsbomb/open-data)
into an analysis-ready `statsbomb` database. Each Flight is a single self-contained
`main.py`; they run in order and each writes one schema:

| Flight | Reads | Writes | Job |
|---|---|---|---|
| [`statsbomb-raw-load`](./flights/statsbomb-raw-load/main.py) | open-data tarball | `raw.*` | Download the open-data archive and load every source file **as-is**: one JSON `record` per array element, plus a `match_id` provenance key. No interpretation. |
| [`statsbomb-core-transform`](./flights/statsbomb-core-transform/main.py) | `raw.*` | `core.*` | De-normalize coordinates to a fixed pitch, resolve the 360 freeze-frame tracking, and project typed tables — **entirely in-warehouse SQL** (`CREATE TABLE AS SELECT` / `INSERT … SELECT`, window functions, `unnest`). |
| [`statsbomb-marts`](./flights/statsbomb-marts/main.py) | `core.*` | `marts.*` | Analysis-ready tables the Dive reads: the replay feed (`replay_events`, `replay_dots`, `replay_markers`), `pass_flights`, per-match `match_stats`, rebuilt `spatial_metrics`, and `frame_quality`. |

> **Don't want to run the pipeline?** The output `statsbomb` database is published
> as a public read-only share — see [_Try it without building_](../README.md#try-it-without-building)
> in the top-level README to attach it directly.

## Deploy

Register all three Flights by name (creates them the first time, updates them after):

```bash
export MOTHERDUCK_TOKEN=<token with read+write on your account>
uv run scripts/deploy_flights.py                   # all three
uv run scripts/deploy_flights.py statsbomb-marts   # or one by name
```

Then run them **in order** from the MotherDuck UI (or with `MD_RUN_FLIGHT`):

```
statsbomb-raw-load  ->  statsbomb-core-transform  ->  statsbomb-marts
```

`statsbomb-raw-load` downloads the full open-data archive (a ~15 GB extraction)
and creates the `statsbomb` database, so the first run takes a while; the other
two are SQL-only and finish in about a minute. They are registered on-demand (no
schedule) — add a `schedule_cron` in a `flight.toml` if you want them to refresh
automatically.

## Run a smaller slice first

Set these as a per-run config override on `statsbomb-raw-load` to keep the first
run small:

- `COMPETITION_IDS` — comma-separated `competition_id` filter (e.g. `43` for the FIFA World Cup).
- `MATCH_LIMIT` — cap the number of matches loaded.

`statsbomb-core-transform` accepts `MATCH_IDS` (comma-separated) to rebuild a
single match instead of the whole corpus.

## Develop locally

Every `main.py` defaults to `md:` but takes an `SB_TARGET` override so you can run
the whole pipeline against a local DuckDB file without touching MotherDuck:

```bash
SB_TARGET=/tmp/sb.duckdb COMPETITION_IDS=43 uv run flights/statsbomb-raw-load/main.py
SB_TARGET=/tmp/sb.duckdb                    uv run flights/statsbomb-core-transform/main.py
SB_TARGET=/tmp/sb.duckdb                    uv run flights/statsbomb-marts/main.py
```

`statsbomb-raw-load` also takes `DATA_DIR` to point at an already-extracted
open-data `data/` directory and skip the download.

## The StatsBomb data gotchas this corrects

StatsBomb data is faithful but quirky; `statsbomb-core-transform` exists to fix
these so the Dive can render a coherent pitch:

- **Coordinates are possession-normalized.** Every event (and its freeze frame)
  is recorded as if the acting team attacks `x=120`, both halves. To plot both
  teams coherently, one attacking direction is pinned per team and coordinates
  flipped (`x -> 120-x`, `y -> 80-y`) for the team attacking left.
- **~6% of 360 freeze frames arrive mirrored** vs their event's orientation
  (systematic for the secondary event of paired moments). Detected per frame by
  comparing the freeze-frame actor's position against the event location.
- **Pressure (off-ball) events:** the `location` is the presser, not the ball.
  The effective ball position is carried forward from the previous event's
  endpoint via a window function (`b_eff`).
- **~11% of located events have no 360 frame**, so the renderer falls back to a
  synthesized actor dot from the event's own `player` + `location`.
- **~1% of frames invert the keeper's `teammate` flag**; a keeper dot within 25
  units of a goal mouth is reassigned to the team defending that goal.

## Caveats

- **`statsbomb-core-transform` runs the transform as SQL inside MotherDuck** (native),
  not by piping rows through the Python runtime. The Flight only issues
  `CREATE TABLE AS SELECT` / `INSERT … SELECT` (with window functions and `unnest`) and
  MotherDuck does the work — the data never round-trips to the Flight container. That
  keeps a full-corpus rebuild to about a minute; copying `raw.*` into Python and pushing
  rows back (e.g. `executemany`) is dramatically slower. Reach for Python only for logic
  SQL genuinely can't express.
- The Flight runtime injects a MotherDuck token automatically; that token needs
  read **and** write on your account (the raw-load Flight runs `CREATE DATABASE IF NOT EXISTS statsbomb`).
- Pin `duckdb==1.5.3`. It is already a MotherDuck-compatible client, so there is no separate CLI to manage for `deploy_flights.py`.
- The target database name `statsbomb` is a literal in each `main.py` — change it there (and in the Dive's resource alias) if you want a different name.
