---
title: Build a What-If Scenario Planner With Spreadsheet Input and an Optimizer
id: what-if-scenario-planner
description: >-
  A Dive app for what-if planning: scenarios with sliders and a spreadsheet
  grid recalculate instantly in the browser, an optimizer finds the best
  settings, and a Flight saves every change. The sample models supply chain
  levers. Use when building an interactive planning, budgeting or forecasting
  app on MotherDuck where users edit inputs and compare scenarios.
type: example
category: end-to-end
features: [dives, flights, shares, admin_api]
tags: [typescript, python]
prompt: >-
  I want to build an interactive what-if planning app on MotherDuck where users
  edit numbers in a spreadsheet, move sliders, see results recalculate
  instantly, optimize the settings and save scenarios. Help me adapt the "Build
  a What-If Scenario Planner With Spreadsheet Input and an Optimizer" recipe to
  my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/what-if-scenario-planner
published_date: 2026-11-16
---

# Build a what-if scenario planner with spreadsheet input and an optimizer

A Dive app where users build scenarios, edit inputs in a spreadsheet grid, move
sliders, and see the KPIs and chart update as they type. An optimizer picks the
best slider settings, and a Flight saves every change and runs an approval
workflow. The sample is a supply chain plan: demand vs supply over 26 weeks,
with expediting, drop ship and subcontracting levers. The pattern is the point:
swap the data and the model for your own.

## How it works

| Pattern | How | Where |
|---|---|---|
| **Local calculation** | One bootstrap query loads the base data; the model is a pure TypeScript function, so every edit recalculates in the browser with no query | `dive/src/engine.ts` |
| **What-if scenarios** | A scenario is a JSON of drivers plus cell overrides. Each one is also computed with every lever off, so the KPIs show the impact of the decisions | `engine.ts`, `flight/planner.py` |
| **Spreadsheet input** | A [Univer](https://univer.ai) sheet, bundled inline (Dives allow no dynamic `import()`). Typed, pasted and formula edits become overrides; computed rows are live formulas and locked | `dive/src/SupplyGrid.tsx`, `gridLayout.ts`, `univer-runtime.ts` |
| **Optimizer** | Tries all 130,011 lever settings in ~0.2 s in the browser, reusing work across independent cells, then reruns the winner through the engine. Auto-optimize reruns it when an input changes | `dive/src/optimizer.ts` |
| **Writeback** | Dives are read-only, so saves call a Flight with `useRunFlight`, owned by a service account with `GRANT RUN ... TO ORGANIZATION`. It validates, writes, logs, then the Dive runs `REFRESH DATABASE` | `dive/src/flight-backend.ts`, `flight/` |
| **Trusted results** | On approval the Flight recomputes the plan with a Python port of the engine; a parity test keeps the two in step | `flight/supply_engine.py`, `e2e/` |

The Dive reads an organization share with `UPDATE AUTOMATIC`, so every member
sees the same scenarios.

## Questions to answer

- What is the model? Inputs (base tables), decisions (sliders), outputs (KPIs).
- Which inputs can users override cell by cell, and which rows are computed?
- What does the optimizer maximize, and how many settings are there?
- Who can edit, and does a plan need approval?

## Caveats

- Exhaustive search works when the settings fit in about 10^6 and the model
  splits into independent parts. Otherwise use a solver.
- Each save is a Flight run, so it takes a few seconds. Saves are debounced and
  queued, and grid overrides are last-writer-wins per cell.
- Univer adds about 3.5 MB to the Dive.
- `setup.py` needs an organization admin token to create the service account.

## What you'll adjust

| Knob | Where |
|---|---|
| Base data | `flight/static.sql` |
| The model | `dive/src/engine.ts` and its port `flight/supply_engine.py` |
| Decision space and objective | `LEVER_RANGE`, `addCell` in `dive/src/optimizer.ts` |
| Write ops and validation | `Api` in `flight/planner.py` |
| Names of the service account, database, share and Flight | `flight/setup.py` |
| Layout and colors | `dive/src/dive.tsx`, `dive/src/theme.ts` |

## Run it

Needs an admin token, the DuckDB CLI, Node.js 20+ and [uv](https://docs.astral.sh/uv/).

```bash
cd dive && npm install && npm run vendor && cd ..
export motherduck_token=...    # an organization admin
uv run --with duckdb==1.5.6 --with pytz python flight/setup.py prod   # service account, database, share, Flight
dive/scripts/deploy-dive.sh prod                                      # create or update the Dive
```

`setup.py` is idempotent and writes the ids to `dive/deploy-targets.json`.

Tests:

```bash
node e2e/engine-parity.test.mjs    # TypeScript and Python engines agree
node e2e/optimizer.test.mjs        # optimizer matches a brute force
node e2e/grid-layout.test.mjs
uv run --with duckdb==1.5.6 --with pytz python e2e/flight_smoke_test.py
```

## Security

- The service account token is saved to `~/.config/scenario-planner/<target>-sa-token` (mode 0600).
- The Flight never trusts the browser: it checks ranges, allowed cells, status
  transitions and versions, and recomputes approved plans itself.

## Learn more

- Dive and Flight authoring: the MotherDuck MCP `get_dive_guide` and `get_flight_guide` tools.
