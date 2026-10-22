---
title: Self-Healing Pipelines With MotherDuck Flights
id: self-healing-flights
description: >-
  One healer Flight that notices when your pipeline Flights break, fixes them in
  parallel with AI coding agents in zero-copy sandboxes, gets a second model to
  approve each fix, and ships it as a new Flight version, plus a Dive that shows
  every step.
  Use when you want pipelines that repair upstream changes on their own.
type: example
category: automation
features: [flights, dives]
tags: [python, openrouter]
prompt: >-
  I want my MotherDuck pipelines to detect their own failures and fix them with an
  AI agent, with a human-readable record of every fix. Help me adapt the
  "Self-Healing Pipelines With MotherDuck Flights" recipe to my pipelines, using it
  as a guide:
  https://motherduck.com/docs/cookbook/self-healing-flights
published_date: 2026-10-19
---

# Self-Healing Pipelines With MotherDuck Flights

Two pipeline Flights load weather data. A single healer Flight runs every 5 minutes: when a pipeline
breaks, it opens an incident, repairs the code with AI agents (one per incident, in parallel), and
ships the fix as a new version of the pipeline Flight. A Dive shows the pipelines, the runs, and each
step of every fix.

## How it works

| File | What it does |
|---|---|
| `pipeline_flight.py` | Loads the NWS daily forecast and fails if a data test fails. |
| `hourly_flight.py` | A second pipeline (NWS hourly temperatures) that breaks the same way, so the healer can reuse what it learned. |
| `healer_flight.py` | Every 5 minutes: exits if another healer run is still alive (and cancels one that is stuck), turns failed runs into incidents with `prompt_jev` (an LLM only when Jev is unsure), reproduces each failure in a zero-copy clone, has opencode + Claude fix all incidents at once with one subagent each, asks GPT to approve each fix, ships it with `MD_UPDATE_FLIGHT`, confirms it on the next runs, and writes a MotherDuck Guide. |
| `dive.tsx` | The status board: pipeline graph, run history, incidents, and each step of every fix. |
| `schema.sql` | The `heal` database the Flights write and the Dive reads. |
| `deploy.py` | Creates or updates everything above. |

## Run it

1. Add your OpenRouter key as a Flight secret (DuckDB CLI):

   ```sql
   CREATE OR REPLACE SECRET openrouter IN motherduck (TYPE flights, PARAMS MAP {'OPENROUTER_API_KEY': getenv('OPENROUTER_API_KEY')});
   ```

2. Deploy (from the repo root):

   ```bash
   cd self-healing-flights
   MOTHERDUCK_TOKEN=... uv run --with duckdb==1.5.6 --with pytz deploy.py
   ```

3. Break the pipeline on purpose. This makes the weather API send a new payload shape:

   ```sql
   SET VARIABLE w = (SELECT flight_id FROM MD_LIST_FLIGHTS() WHERE flight_name = 'heal-demo-weather');
   SELECT * FROM MD_UPDATE_FLIGHT(flight_id := getvariable('w'), config := MAP {'NWS_FEATURE_FLAGS': 'forecast_temperature_qv'});
   SELECT * FROM MD_RUN_FLIGHT(flight_id := getvariable('w'));
   ```

4. Open the **Self-healing Flights** Dive. In about three healer runs (~15 minutes) the incident goes
   `open → investigating → verifying → resolved`. The simulated API change stays on; the fixed code
   handles the new shape.

To undo the fix, roll the pipeline Flight back to an earlier version in the Flights UI. Re-running `deploy.py`
leaves existing pipeline Flights alone (so it never overwrites a fix); `--reset-pipelines` restores the original code.

## What you'll adjust

| Knob | Where | Default |
|---|---|---|
| Your pipelines | `PIPELINES` in `healer_flight.py` | `heal-demo-weather`, `heal-demo-hourly` |
| Fixer, approver and coordinator models | Healer config `MODEL`, `APPROVER_MODEL`, `COORDINATOR_MODEL` | Claude Opus latest, GPT Sol latest, GPT Luna latest |
| Budget per incident | Healer config `BUDGET_USD` | `10` |
| Approver retries | Healer config `APPROVER_ROUNDS` | `2` |
| Autonomy | `heal.main.control` key `autonomy_cap` (`L1` ships the fix, `L3` only proposes it) | `L1` |
| Kill switch, daily spend cap | `heal.main.control` keys `kill_switch`, `daily_spend_cap_usd` | `off`, `20` |
| Schedules | `deploy.py` (`--no-schedules` to skip) | healer every 5 min |

## Caveats

- The healer edits pipeline code and ships it. Start with `autonomy_cap = 'L3'` so a
  human reviews each fix.
- Each fix costs real LLM money (about $0.20 to $2 here). Keep the budget and spend cap.
- Disable the schedules when you are done: `deploy.py --no-schedules`.
- DuckDB uses a lowercase `motherduck_token` before `MOTHERDUCK_TOKEN`; `deploy.py` stops if both are set to different tokens.

## Security

- The agents never hold a MotherDuck token. Their only tool is `verify`, served by the
  healer, which runs the pipeline against a sandbox clone.
- A second model approves every fix from the diff and the test evidence alone.
- Upstream text is treated as data, never as instructions.

## Learn more

- Flights and Dives guides: `get_flight_guide`, `get_dive_guide`.
- Guides: resolved incidents are stored under the `self-healing/incidents/<category>` topic (`MD_LIST_GUIDES`, DuckDB 1.5.6+).
