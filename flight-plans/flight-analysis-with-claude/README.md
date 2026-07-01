---
title: Build Your Own Analysis Agent in a Flight
id: flight-analysis-with-claude
description: >-
  Build a data-analysis agent from scratch and run it on a schedule in
  MotherDuck. A Pydantic AI agent, with its model served through OpenRouter so it
  is not tied to one provider, is made strong by an inline company-context skill
  and two tools you build yourself: a read-only SQL tool over your warehouse and
  a weather API tool. It fans out one agent per NYC borough to write a "notable
  things" brief from the public 311 dataset.
type: template
category: automation
features: [flights]
tags: [pydantic-ai, openrouter, python]
---

# Build Your Own Analysis Agent in a Flight

This Flight shows how to build your own data-analysis agent and run it on a
schedule in MotherDuck. Instead of reaching for a finished framework and hoping
it does the right thing, you compose an agent from parts you understand, then let
a Flight trigger it on your latest data.

The shipped example briefs **NYC 311 service requests by borough** using the
public `sample_data` dataset, so a fresh deploy runs end to end with no data of
your own. Each run discovers the active boroughs in a recent window, fans out one
agent per borough, and each agent explores the 311 data, optionally enriches it
with weather, and writes a ranked "notable things" brief that is stored in a
table. Swap the discovery query, the source table, the skill, and the tools to
point it at your entities and your domain.

## An agent is just a loop

An agent is a loop: send the model a prompt, let it call tools, feed the tool
results back, and repeat until it stops and answers. That loop is cheap to own,
and you do not hand-write it here. [Pydantic AI](https://ai.pydantic.dev/) runs
it. What determines whether the answer is any good is not the loop, it is what
you put around it.

Consider the classic failure: ask an agent "what is the average order value?" and
it writes `SELECT avg(order_total)` and returns `16,990.42`. Plausible, and wrong,
because `order_total` is stored in cents and the real answer is `169.90`. The loop
did exactly what it was told. The missing piece was context. That is the whole
game: a capable loop plus the context and tools that make it correct.

## The three layers

`build_agent()` composes the agent from three things you can see and change:

1. **The model, via OpenRouter.** `MODEL` is an OpenRouter model slug, so the
   agent is not locked to one provider. Swap it for any tool-capable model
   (Anthropic, OpenAI, Google, open-weight) without touching the rest of the
   code. OpenRouter speaks one OpenAI-compatible API for all of them.
2. **A skill: your company context.** The `SKILL` constant is the domain
   knowledge the agent needs to be correct: what counts as *notable* for 311, the
   `Unspecified` borough exclusion (the analog of internal or test accounts), how
   to ground every number in a query, and reference data such as the borough
   coordinates the weather tool needs. A Flight ships as a single file, so the
   skill lives inline and is passed to the agent as its instructions. Editing
   `SKILL` is how you retarget the agent to your own domain.
3. **Tools you build yourself.** Two ordinary Python functions handed to
   `Agent(tools=[...])`:
   - `explore_warehouse(sql)` runs read-only SQL against `md:sample_data`. It
     guards writes with DuckDB's `json_serialize_sql()`: that function only
     serializes a statement to an AST when it is a read, so a mutating statement
     (INSERT, UPDATE, DELETE, CREATE, ATTACH) comes back with `error: true` and
     is refused before it runs. This is the same read-only primitive the
     MotherDuck Wasm client and Dives use. Its description tells the agent to
     stick to `SELECT` and to explore the schema through `information_schema`.
   - `get_weather(latitude, longitude, start_date, end_date)` is a thin wrapper
     over the [Open-Meteo](https://open-meteo.com/) historical archive API (no
     key required). It lets the agent explain a complaint spike with weather, for
     example heavy rain preceding flooding reports.

## How it works

`flight.py` runs a fixed sequence. The parts you change are the discovery query,
`SOURCE_TABLE`, the `SKILL`, and the prompt in `build_prompt()`:

1. **Anchor the window.** `sample_data` is a frozen snapshot, so "recent" is
   measured from `MAX(created_date)`, not `now()`. The run computes an anchor and
   a `BRIEF_WINDOW_DAYS` lookback once. Against a live warehouse you would anchor
   to `now()` instead.
2. **Discover entities.** `discover_boroughs()` lists the boroughs active in the
   window (busiest first, excluding the geography-less `Unspecified` bucket). The
   `BOROUGHS` env var overrides discovery; `MAX_BOROUGHS` caps the count for
   testing.
3. **Build the agent once.** `build_agent()` composes the three layers above into
   a single Pydantic AI `Agent`, shared across the run.
4. **Fan out, bounded.** One `agent.run()` per borough runs behind an
   `asyncio.Semaphore(CONCURRENCY)`, so the batch stays inside the Flight runtime
   and under OpenRouter rate limits. The prompt fixes the borough and window and
   asks the agent to profile activity, decide what is notable, and check weather
   where it fits.
5. **Persist and summarize.** Each non-empty brief is written to `RESULTS_TABLE`
   (`flights_demo.main.borough_briefs`), and the run logs an `ok` / `failed`
   batch summary to stderr. One borough's failure is caught so it does not abort
   the rest.

Discovery and persistence use a direct `duckdb.connect("md:")` (deterministic
infra); only the agent's exploration goes through the `explore_warehouse` tool,
which connects to the single `md:sample_data` database.

## What you'll adjust

| Knob | Where | Default | Purpose |
| --- | --- | --- | --- |
| `SKILL` | constant in `flight.py` | 311 domain context | The company context that makes the agent correct. The main thing you tune. |
| Discovery query | `discover_boroughs()` | top boroughs by volume | The SQL that lists the entities to brief. Replace with your own partition. |
| `SOURCE_TABLE` | top of `flight.py` | `sample_data.nyc.service_requests` | The table each agent analyzes. Point at your data. |
| `build_prompt()` | function in `flight.py` | 311 "notable things" prompt | The per-entity task and how the brief is shaped. |
| `explore_warehouse` / `get_weather` | functions in `flight.py` | SQL tool + weather tool | The tools the agent gets. Add, remove, or replace them. |
| `MODEL` | config / env | `anthropic/claude-sonnet-4.6` | OpenRouter model slug. Any tool-capable model works. |
| `CONCURRENCY` | config / env | `3` | Max simultaneous agents. Bound by runtime and API rate limits. |
| `BRIEF_WINDOW_DAYS` | config / env | `7` | Lookback window in days, measured from the anchor date. |
| `MAX_BOROUGHS` | config / env | `0` (all) | Cap the entity count for a cheap test run. `0` = no cap. |
| `BOROUGHS` | env | (unset) | Comma-separated override that skips discovery (e.g. `BROOKLYN,QUEENS`). |
| `RESULTS_TABLE` | top of `flight.py` | `flights_demo.main.borough_briefs` | Where briefs are stored. Must be writable. |
| `OPENROUTER_API_KEY` | Flight secret / env | (required) | OpenRouter API key. A local run sets it directly; a Flight injects it from a secret (see below). |
| `MOTHERDUCK_TOKEN` | Flight-injected | (Flight-injected) | Auth for `duckdb.connect("md:")` (discovery, persistence, and the warehouse tool). |

## Run it

You need a MotherDuck account, a MotherDuck access token, and an
[OpenRouter API key](https://openrouter.ai/keys). With the defaults the agents
read the public `sample_data.nyc.service_requests` table (no data of your own
required) and write briefs to `flights_demo.main.borough_briefs`.

```bash
export MOTHERDUCK_TOKEN=your_md_token_here
export OPENROUTER_API_KEY=sk-or-your_key_here
# optional: keep the first run small and cheap
export MAX_BOROUGHS=1
uv run --with-requirements requirements.txt flight.py
```

The run discovers boroughs, fans out the agents, prints a batch summary to
stderr, and stores one brief per borough. Inspect them with:

```sql
SELECT run_ts, borough, window_days, left(brief_md, 280) AS preview
FROM flights_demo.main.borough_briefs
ORDER BY run_ts DESC, borough;
```

### Deploy as a Flight

Store your OpenRouter key as a MotherDuck **Flights secret**. The simplest way is
the MotherDuck UI: open
[Settings > Secrets](https://app.motherduck.com/settings/secrets), add a secret of
type **Flights**, and give it an `OPENROUTER_API_KEY` parameter. If you would
rather use SQL, create the same secret from a write-enabled SQL connection
(read-only connections reject `CREATE SECRET`):

```sql
CREATE SECRET openrouter IN motherduck (
  TYPE flights,
  PARAMS MAP { 'OPENROUTER_API_KEY': 'sk-or-...' }
);
```

A `TYPE flights` secret injects each param under the env var
`<secret_name>_<PARAM>`, not the bare param name: the param above arrives as
`openrouter_OPENROUTER_API_KEY`, not `OPENROUTER_API_KEY`. (DuckDB lowercases the
unquoted secret name into the prefix.) `flight.py` handles this: it reads
`OPENROUTER_API_KEY` for local runs and otherwise picks up any env var ending in
`_OPENROUTER_API_KEY`, so the secret name you choose does not matter.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (adapt the
arguments to your situation), passing:

- `name`: a Flight name, for example `analysis_agent`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["openrouter"]` so the key is injected (as
  `openrouter_OPENROUTER_API_KEY`; `flight.py` resolves it)
- `config` (optional): override `MODEL`, `CONCURRENCY`, `BRIEF_WINDOW_DAYS`,
  `MAX_BOROUGHS` as key/value pairs

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm it succeeds and briefs land in
`RESULTS_TABLE`. Keep `MAX_BOROUGHS` small for that first run to bound cost. Then
clear the cap and add a schedule (for example `0 13 * * *`, daily at 13:00 UTC)
by updating the Flight's `schedule_cron` with `MD_UPDATE_FLIGHT`. Schedule updates
are metadata-only and do not create a new Flight version.

## Building agents, briefly

The takeaway is not this specific Flight, it is the shape. An agent is a loop plus
tools plus context. Pydantic AI gives you the loop and clean tool wiring;
OpenRouter gives you any model behind one API; the two functions here show how to
build a safe internal tool (a read-only warehouse query) and an external one (an
API call). To make the agent stronger you add context to the `SKILL` and tools to
the list. To do more work you scale out, as this Flight does by fanning out one
agent per entity, or you point a single agent at more tools.

## Security

- **The warehouse tool refuses writes.** `explore_warehouse` runs
  `json_serialize_sql()` on every query and executes only reads; a mutating
  statement is refused and the error is handed back to the agent so it can
  correct. This is the in-code first layer.
- **Still scope the token.** Defense in depth: give the Flight a MotherDuck token
  scoped to read the source data and write only the `RESULTS_TABLE` database, so
  the guard is not the only backstop.
- **The weather tool only makes outbound GETs** to a single fixed public host.
- **Keep secrets out of code.** The OpenRouter key comes from a MotherDuck secret
  (or a local env var), never hard-coded or placed in Flight `config`. The
  MotherDuck token is injected by the runtime, never checked in.
- **Briefs can contain real data.** The stored `brief_md` quotes figures the
  agent pulled from your tables. Apply the same access controls to
  `RESULTS_TABLE` as to the source data.

## Learn more

- Pydantic AI: [overview](https://ai.pydantic.dev/), the
  [tools](https://ai.pydantic.dev/tools/) reference, and the
  [OpenRouter model](https://ai.pydantic.dev/models/openrouter/) page
  (`OpenRouterModel` + `OpenRouterProvider`).
- OpenRouter: the [models list](https://openrouter.ai/models) for tool-capable
  slugs to use as `MODEL`.
- Open-Meteo: the [historical weather API](https://open-meteo.com/en/docs/historical-weather-api).
- DuckDB: `json_serialize_sql`, the read-only guard primitive used here.
- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool. Deeper MotherDuck or DuckDB questions: use the
  `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (`duckdb`, `pydantic-ai-slim`).
