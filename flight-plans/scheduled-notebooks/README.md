---
title: Schedule a MotherDuck Notebook
id: scheduled-notebooks
description: >-
  Run every cell of an existing MotherDuck notebook, in order, on a schedule.
  Use to schedule a notebook by title or UUID without copying its SQL into code.
type: template
category: automation
features: [flights]
tags: []
prompt: >-
  I want to run an existing MotherDuck notebook on a schedule without copying
  its SQL into code. Help me adapt the "Schedule a MotherDuck Notebook" recipe
  to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/scheduled-notebooks
published_date: 2026-09-27
---

# Schedule a MotherDuck Notebook

Schedule an existing notebook without copying its SQL into code.

## How it works

Each run fetches the latest notebook and executes every cell in order on one
MotherDuck connection, preserving session state and each cell's selected
database. Cells without a selection keep the current database. SQL is logged,
and the first error fails the Flight run. Notebook edits take effect without
redeploying.

## Questions to answer

- Which notebook should run?
- Is every cell safe to run unattended and repeatedly?
- What UTC schedule should it use?

## Caveats

- The Flight calls the internal, undocumented API behind the notebook UI. It
  can change without notice; if it does, runs fail at fetch time instead of
  running the wrong SQL.
- Only notebooks owned by the token's user are visible, so run as the
  notebook's owner.
- Titles must match exactly one notebook. Prefer the UUID from the notebook
  URL; titles can be renamed or duplicated.
- Every cell runs; per-cell run settings in the UI are ignored. Results are
  discarded, so persist outputs in tables.
- No rollback: writes from cells before a failure stay committed, and a rerun
  repeats them. Make cells rerunnable (`CREATE OR REPLACE`) or wrap them in
  `BEGIN`/`COMMIT`.

## What you'll adjust

| Knob | Purpose |
|---|---|
| `NOTEBOOK` | Required UUID or exact title, e.g. `Daily rollup`. |
| `schedule_cron` | Optional UTC cron, e.g. `0 6 * * *` for daily at 06:00. |

## Run it

```bash
export MOTHERDUCK_TOKEN=your_token_here
NOTEBOOK='Daily rollup' uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Call `MD_CREATE_FLIGHT` with:

- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `config`: `MAP {'NOTEBOOK': '<uuid or title>'}`

The runtime injects `MOTHERDUCK_TOKEN`. Run once with `MD_RUN_FLIGHT`, inspect
`MD_GET_FLIGHT_LOGS`, then set `schedule_cron` with `MD_UPDATE_FLIGHT`.

## Security

Cell SQL runs with the token user's full permissions, so anyone who can edit
the notebook controls what the Flight executes. Each cell's SQL is written to
the Flight logs: keep secrets out of the notebook and create them once with
`CREATE PERSISTENT SECRET`, outside it.

## Learn more

MotherDuck MCP: `get_flight_guide` for deployment and scheduling;
`ask_docs_question` for MotherDuck or DuckDB questions.
