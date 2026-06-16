---
title: Run Agentic Analysis From a Flight With the Claude Agent SDK
id: flight-analysis-with-claude
description: >-
  Schedule an automated agentic analysis in MotherDuck using Claude. Use it when
  you want to trigger Claude to find new insights in your latest data on a
  recurring schedule. The Flight creates multiple Claude agents each of which 
  explores the warehouse, analyzes the data, and writes a summary of its findings.
type: template
category: automation
features: [flights, mcp]
tags: [claude-agent-sdk, python]
---

# Run Agentic Analysis From a Flight With the Claude Agent SDK

This Flight showcases how to schedule an automated agentic analysis in MotherDuck
using Claude. Use it when you want to trigger Claude to find new insights in your
latest data on a recurring schedule.

It's composed of a single-file Flight that produces a recurring set of analytical briefs, one per
entity, where **Claude writes the analysis**.
Each run discovers a list of entities, then fans out one
[Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk/overview) agent per
entity under a concurrency cap. Each agent is given the **read-only tools of the
hosted MotherDuck MCP server** (`query`, `list_tables`, `list_columns`,
`search_catalog`, `query_context_layer`, ...) and a prompt; it explores the
warehouse and returns a ranked "notable things" brief grounded in real query
results. The run stores every brief and logs a batch summary.

This example analyzes **NYC 311 service requests by borough** using the
public `sample_data` dataset, so a fresh deploy runs end to end with no data of
your own. Swap the discovery query, the source table, and the prompt to point it
at your entities (customers, regions, services, repos — anything you can
partition by) and your tables.

## How it works

`flight.py` runs a fixed sequence; the parts you change are the discovery query,
the `SOURCE_TABLE`, and the prompt in `build_prompt()`:

1. **Mirror the hosted MCP toolset.** At startup the Flight connects
   `MotherDuckMCPClient` to the hosted MCP server, lists its tools, keeps the
   **read-only** ones (dropping a mutating-name denylist plus `query_rw`), and
   wraps each as an in-process SDK tool via `create_sdk_mcp_server` + `@tool`.
2. **Anchor the window.** `sample_data` is a frozen snapshot, so "recent" is
   measured from `MAX(created_date)` in the table, not `now()`. The run computes
   an anchor and a `BRIEF_WINDOW_DAYS` lookback once. Against a live warehouse
   you would anchor to `now()` instead.
3. **Discover entities.** A SQL query lists the boroughs active in the window
   (busiest first, excluding the geography-less `Unspecified` bucket). The
   `BOROUGHS` env var overrides discovery; `MAX_BOROUGHS` caps the count for
   testing.
4. **Fan out, bounded.** One Claude Agent SDK `query()` per borough runs behind
   an `asyncio.Semaphore(CONCURRENCY)`. Each `query()` spawns its own bundled-CLI
   subprocess, so the semaphore keeps the run inside the 2-CPU / 16 GB Flight
   runtime and under Anthropic API rate limits. `ClaudeAgentOptions` sets
   `tools=[]` (all built-in tools off — no Bash, Read, Write),
   `allowed_tools=[...]` (auto-approve exactly the mirrored read-only tools), and
   `permission_mode="dontAsk"` (deny anything else, never prompt — it runs
   headless). The prompt fixes the borough and window and decides what is
   *notable* (complaint spikes, aging open requests, zip/community-board
   hotspots, channel shifts), forbidding invented numbers.
5. **Persist + summarize.** Each non-empty brief is written to `RESULTS_TABLE`
   (`flights_demo.main.borough_briefs`), and the run logs an `ok` / `failed`
   batch summary to stderr. Per-entity errors are caught so one failure does not
   abort the rest.

Discovery and persistence use a direct `duckdb.connect("md:")` (deterministic
infra steps); only the agent's exploration goes through the MCP tools.

## Questions to answer

- What is the entity you fan out over (the analog of "borough"), and what query
  discovers the current list?
- Which table(s) should each agent analyze, and what is the time window and the
  column that defines "recent"? Is the data live (anchor to `now()`) or a frozen
  snapshot (anchor to `MAX(...)`)?
- What counts as *notable* for your domain — what should the prompt tell the
  agent to look for, and what should it ignore?
- Which hosted MCP tools should the agent get? The default mirrors all read-only
  tools; narrow the set if you want a tighter surface.
- Which Claude model fits the budget and latency you want, and what concurrency
  fits your entity count and API rate limits?
- Which MotherDuck token will the agents use, and is it scoped to read-only on
  the data they touch (see [Security](#security))?
- Where should briefs be stored, and on what schedule should the Flight run?

## Caveats

- **Agentic analysis is non-deterministic.** Two runs over identical data can
  word findings differently or surface different items. That is the point — the
  agent decides what is notable — but it means briefs are a starting point for a
  human, not an audited report. The prompt pins the window and forbids invented
  numbers to keep findings grounded, but verify anything load-bearing.
- **Cost and time scale with entities × model.** Each entity is a full agent
  session that runs several exploratory queries. Five boroughs on a large model
  is cheap; hundreds of entities is not. Use `MAX_BOROUGHS` and `CONCURRENCY` to
  bound a test run before scheduling, and pick the model deliberately.
- **Read-only is enforced by what is mirrored, plus the token.** The agent can
  only call the tools `flight.py` mirrors, and the mutating-name filter
  (`is_read_only_tool`) drops `query_rw` and any `save_/update_/delete_/...` tool
  before they reach the agent; built-in tools are disabled so it cannot shell
  out. The filter keys on tool *names*, so review it if the server adds a
  read-only tool with an unusual name (it would be excluded) or a mutating tool
  with an unusual name (it would slip through). The strongest backstop is still
  the token's permissions — see [Security](#security).
- **How the agent reaches MCP: an in-process bridge.** The agent does *not* point
  the SDK at the hosted MCP server directly. With the pinned `claude-agent-sdk`,
  the bundled CLI's HTTP-MCP client fails with `Connection closed` (even though
  the server is healthy for a standard client), so `flight.py` calls the hosted
  server over plain HTTP from Python and mirrors each read-only tool as an
  in-process SDK tool, which the CLI runs reliably. Gotcha: if you try the direct
  route and leave built-in tools enabled (no `tools=[]`), the failure is silently
  masked — the agent falls back to running SQL through `Bash` and never uses the
  MCP server. The bridge may become unnecessary once a future SDK fixes the CLI's
  HTTP-MCP client.
- **Frozen sample data.** `sample_data` ends in 2023 and never changes, so the
  window is anchored to `MAX(created_date)`. If you point this at a live table,
  switch the anchor to `now()` or briefs will drift to a fixed historical window.
- **Empty windows produce empty briefs.** If discovery or the window returns no
  rows for an entity, that entity is logged as `empty` and skipped, not stored.

## What you'll adjust

Most knobs are module constants or `config` values at the top of `flight.py`;
the source query and prompt are functions you edit directly.

| Knob | Where | Default | Purpose |
|---|---|---|---|
| Discovery query | `discover_boroughs()` in `flight.py` | top boroughs by volume | The SQL that lists the entities to brief. Replace with your own partition. |
| `SOURCE_TABLE` | top of `flight.py` | `sample_data.nyc.service_requests` | The table each agent analyzes. Point at your data. |
| `build_prompt()` | function in `flight.py` | 311 "notable things" prompt | What the agent looks for and how the brief is shaped. The main thing you tune. |
| `MUTATING_PREFIXES` / `is_read_only_tool` | top of `flight.py` | drop `query_rw` + `save_/update_/delete_/...` | Which hosted MCP tools the agent gets. Tighten for a narrower surface. |
| `MCP_URL` | env `MD_MCP_URL` | `https://api.motherduck.com/mcp` | The hosted MotherDuck MCP endpoint the bridge calls. |
| `RESULTS_TABLE` | top of `flight.py` | `flights_demo.main.borough_briefs` | Where briefs are stored, as `database.schema.table`. Must be writable. |
| `BRIEF_WINDOW_DAYS` | config / env | `7` | Lookback window in days, measured from the anchor date. |
| `CONCURRENCY` | config / env | `3` | Max simultaneous agents. Bound by CPU/RAM and API rate limits. |
| `MODEL` | config / env | `claude-opus-4-8` | Claude model id the agents run on. |
| `MAX_BOROUGHS` | config / env | `0` (all) | Cap the number of entities for a cheap test run. `0` = no cap. |
| `BOROUGHS` | env | (unset) | Comma-separated override that skips discovery (e.g. `BROOKLYN,QUEENS`). |
| `ANTHROPIC_API_KEY` | Flight secret / env | (required) | Anthropic API key. A local run sets it directly; a Flight injects it from a secret (see below). |
| `MOTHERDUCK_TOKEN` | Flight-injected | (Flight-injected) | Auth for both `duckdb.connect("md:")` (discovery + persistence) and the hosted MCP server (the agent's tools). Select a token on the Flight; never hard-code it. |

## Run it

You need a MotherDuck account, a MotherDuck access token, and an
[Anthropic API key](https://console.anthropic.com/settings/keys). With the
defaults the agents read the public `sample_data.nyc.service_requests` table (no
data of your own required) and write briefs to `flights_demo.main.borough_briefs`.

The MotherDuck token must be one the **MCP server accepts** — a Personal Access
Token (the kind under [Settings > Access Tokens](https://app.motherduck.com/settings/tokens)).
A Flight's auto-injected token is already such a token (see
[Deploy as a Flight](#deploy-as-a-flight)).

```bash
export MOTHERDUCK_TOKEN=your_md_token_here
export ANTHROPIC_API_KEY=sk-ant-your_key_here
# optional: keep the first run small and cheap
export MAX_BOROUGHS=1
uv run --with-requirements requirements.txt flight.py
```

The run mirrors the read-only MCP tools (it logs how many), discovers boroughs,
fans out the agents, prints a batch summary to stderr, and stores one brief per
borough. Inspect them with:

```sql
SELECT run_ts, borough, window_days, left(brief_md, 280) AS preview
FROM flights_demo.main.borough_briefs
ORDER BY run_ts DESC, borough;
```

### Deploy as a Flight

Store your Anthropic API key as a MotherDuck **Flights secret**. The simplest way
is the MotherDuck UI: open
[Settings > Secrets](https://app.motherduck.com/settings/secrets), add a secret of
type **Flights**, and give it an `ANTHROPIC_API_KEY` parameter. If you would
rather use SQL, create the same secret from the DuckDB client or any
write-enabled SQL connection (read-only connections reject `CREATE SECRET`):

```sql
CREATE SECRET claude IN motherduck (
  TYPE flights,
  PARAMS MAP { 'ANTHROPIC_API_KEY': 'sk-ant-...' }
);
```

A `TYPE flights` secret injects each param under the env var
`<secret_name>_<PARAM>`, not the bare param name: the param above arrives as
`claude_ANTHROPIC_API_KEY`, not `ANTHROPIC_API_KEY`. (DuckDB lowercases the
unquoted secret name into the prefix.) `flight.py` handles this: it reads
`ANTHROPIC_API_KEY` for local runs and otherwise picks up any env var ending in
`_ANTHROPIC_API_KEY`, so the secret name you choose does not matter.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `analysis_with_claude`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["claude"]` so the key is injected (as
  `claude_ANTHROPIC_API_KEY`; `flight.py` resolves it)
- `config` (optional): override `MODEL`, `CONCURRENCY`, `BRIEF_WINDOW_DAYS`,
  `MAX_BOROUGHS` as key/value pairs

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed. That injected token is a
Personal Access Token, which the hosted MCP server accepts, so the bridge works
with the default token — no extra secret for MCP.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm it succeeds and briefs land in
`RESULTS_TABLE`. Keep `MAX_BOROUGHS` small for that first run to bound cost. Then
clear the cap and add a schedule (for example `0 13 * * *`, daily at 13:00 UTC)
by updating the Flight's `schedule_cron` with `MD_UPDATE_FLIGHT`. Schedule
updates are metadata-only and do not create a new Flight version.

## Security

- **Least privilege by construction.** The agent only gets the mirrored
  read-only MCP tools. Built-in tools are disabled (`tools=[]`), so it cannot run
  shell commands, read files, or reach the network on its own;
  `permission_mode="dontAsk"` denies anything not pre-approved without prompting;
  `setting_sources=[]` keeps local Claude settings from quietly adding tools.
- **Only read-only tools are exposed.** `is_read_only_tool` filters the hosted
  server's tool list before any tool reaches the agent, dropping `query_rw` and
  every `save_/update_/delete_/edit_/create_/...` tool. Review the filter if the
  server's tool naming changes (it keys on names).
- **Still scope the token.** Defense in depth: give the Flight a MotherDuck token
  scoped to read the source data (and write only the `RESULTS_TABLE` database), so
  even if a mutating tool slipped through the name filter it could not modify
  anything else. In `flight.py` only the discovery and persistence steps write,
  and only to `RESULTS_TABLE`.
- **The token rides on the MCP requests.** `MotherDuckMCPClient` sends
  `MOTHERDUCK_TOKEN` as a bearer header to the hosted MCP endpoint over HTTPS.
  Treat it as any warehouse credential; it is read from the environment, never
  hard-coded.
- **Keep secrets out of code.** The Anthropic key comes from a MotherDuck secret
  (or a local env var), never hard-coded or placed in Flight `config`. The
  MotherDuck token is injected by the runtime, never checked in.
- **Briefs can contain real data.** The stored `brief_md` quotes figures the
  agent pulled from your tables. Apply the same access controls to
  `RESULTS_TABLE` as to the source data.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Claude Agent SDK: [overview](https://docs.claude.com/en/api/agent-sdk/overview)
  and the [Python SDK reference](https://docs.claude.com/en/api/agent-sdk/python),
  including `ClaudeAgentOptions`, `permission_mode`, `tools` / `allowed_tools`, and
  defining in-process tools with `create_sdk_mcp_server` and the `@tool` decorator.
- MotherDuck MCP server: the hosted endpoint and its tools (the `query` tool used
  here takes `database`, `sql`, and `new_fragments`). Deeper MotherDuck or DuckDB
  questions: use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (`duckdb`, `claude-agent-sdk`,
  `requests`).
