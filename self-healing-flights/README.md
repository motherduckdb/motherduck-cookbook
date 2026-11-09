---
title: Self-Healing Pipelines With MotherDuck Flights
id: self-healing-flights
description: >-
  One healer Flight that notices when your pipeline Flights break, fixes them with
  AI coding agents that run as separate service accounts on zero-copy clones, has
  every fix checked and reviewed independently, and proposes it for your approval
  (or ships it) as a new Flight version, plus a Dive that shows every step.
  Use when you want pipelines that repair upstream changes on their own.
type: example
category: automation
features: [flights, dives, shares, admin_api]
tags: [python, openrouter]
prompt: >-
  I want my MotherDuck pipelines to detect their own failures and fix them with an
  AI agent, with a human approving each fix and a readable record of every step.
  Help me adapt the "Self-Healing Pipelines With MotherDuck Flights" recipe to my
  own pipelines and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/self-healing-flights
published_date: 2026-10-19
---

# Self-Healing Pipelines With MotherDuck Flights

Two pipeline Flights load weather data. A healer Flight notices when one breaks, opens an incident, and
has an AI coding agent fix it. The agent works as its own service account, on a zero-copy clone of
your data that it can only read through a restricted share. Every fix must pass the healer's own
checks, a separate test-reviewer agent and an approver model. It then waits for you to approve it in
the Dive, or ships on its own if you allow that. A Dive shows the pipelines, runs, alerts, fixes
waiting for approval, and each step of every fix.

## How it works

| File | What it does |
|---|---|
| `pipeline_flight.py`, `hourly_flight.py` | Two pipelines (NWS daily forecast and hourly temperatures). On failure they print one `HEAL_EVENT {...}` line: that is their whole contract with the healer. |
| `healer_flight.py` | The healer. Runs every 5 minutes; see below. The Dive's Approve, Reject and Retry buttons run it too. |
| `dive.tsx` | The board: health, alerts, fixes waiting for approval, pipeline graph, run timeline, incidents with each fix's steps, Guides, and three Architecture tabs. |
| `schema.sql` | The `heal` database the healer writes and the Dive reads. |
| `deploy.py` | Creates or updates everything above (safe to re-run). |

**One coordinator at a time.** A healer run takes the `healer` lease (a row in `heal.main.leases`) or exits
because a live run already holds it. While it holds the lease it renews it every 30 seconds, but only while
its main loop makes progress. A run that hangs stops renewing; the next scheduled run cancels it and sends a
`stuck` alert. A run that crashes is reported by the next one (`healer_failed`).

**Detection, even mid-fix.** The coordinator checks every finished pipeline run once per minute, also while
fixes are running. A failure becomes an incident, or joins an open one with the same fingerprint (numbers,
ids and hashes are masked first). `prompt_jev` classifies new incidents: fix, watch (a source outage) or
escalate (credentials). A run that cannot be processed, for example because of a malformed `HEAL_EVENT`
line, is retried for 30 minutes and then parked with a `dead_letter` alert. It never blocks later runs.
Floods are capped:
- at most 10 new incidents per pipeline per pass
- a 45-second budget per pass
- more than 5 new kinds of failure on one pipeline within an hour fold into one incident, with a `storm` alert

**Picking up new failures while an agent fixes.** Every new failure on the same pipeline, or on another
pipeline with the same upstream, goes to the fixing agent's inbox. The inbox is a local SQLite file in WAL
mode. The coordinator steers at most one short notice per minute into the agent's running opencode session:
- If the failures are short, the notice carries them inline, fenced as untrusted log text.
- If they are long, the notice tells the agent to run `inbox <key>` and read them there.
- A notice the agent could not see before it finished goes with its next prompt, or to the reviewers.

**Agent accounts.** On its first run the healer creates:
- the role `self_healing_pipeline_agent`
- `AGENT_POOL_SIZE` service accounts named `heal_agent_<owner>_1..N` that hold the role (later runs create none)
- one restricted share per pipeline database, granted only to that role

For each fix it leases a free account and mints a short-lived token for it. Before cloning, it runs
`CREATE SNAPSHOT` on the database, and the agent account runs `REFRESH DATABASE` on its share and
zero-copy clones it under the production name (`CREATE DATABASE heal_demo FROM heal_demo__share`). So the
unchanged pipeline code writes into the clone, inside the agent's own account.

**A fix.** The opencode fixer can only edit its directory and run two commands, `verify <key>` and
`inbox <key>`. `verify` runs the pipeline as the agent account, as the unprivileged `nobody` user,
against a fresh clone. When the agent is done, four gates must pass in order, and any "no" goes back to
the agent as feedback:
1. **The healer's own checks**: only `pipeline.py` changed, protected lines kept, no merge markers, green
   in a fresh clone, plus the pipeline's contract (SQL assertions on the output) and drift against
   production.
2. **An independent test-reviewer agent** reviews every test the fix adds, changes or removes.
3. **An approver model** sees only the evidence the healer gathered, never the agent's reasoning.

**Ship: check, merge, retest, check again.** Before submitting, the healer re-reads the pipeline's current
code. If it changed since the fix started, it 3-way merges the fix onto it (`git merge-file`):
- A clean merge is retested through every gate.
- A conflict goes back to the agent to resolve.

It then checks again, because retesting takes time. This loops until the code is unchanged.
- With `AUTONOMY=L3`, the default, the fix becomes a proposal. Approve in the Dive runs the healer again, which
  checks once more and ships with `MD_UPDATE_FLIGHT` only if the pipeline is still exactly what was tested. If it has
  changed, the healer redoes the fix on the new version through every gate and proposes it again, so you always
  approve exactly what ships.
- With `L1` it ships at once.

Two green runs resolve the incident. The fix then becomes a MotherDuck Guide with a reusable code snippet,
and later fixes recall it.

## Questions to answer

- Which pipeline Flights should be healed, which databases do they write, and which lines must a fix never
  touch? (`PIPELINES` in `healer_flight.py`.)
- What does correct output look like? Write it as the pipeline's `contract`: SQL that returns true when a
  check passes, run by the healer on the agent's output.
- Should fixes ship on their own (`AUTONOMY=L1`) or wait for a human (`L3`, the default)?
- How many fixes may run at once (`AGENT_POOL_SIZE`), and what may one fix and one day cost?
- Where should alerts go? Set up a Slack-compatible incoming webhook as the `heal_webhook` secret, or rely on
  the Dive's Alerts panel.

## Caveats

- **The owner must be an Admin on a Business or Enterprise plan.** The healer creates service accounts and
  their tokens through the REST API (Admin only) and a custom role (custom roles need Business or Enterprise).
  Deploy as a dedicated Admin service account rather than a person.
- **This recipe's demo runs at `AUTONOMY=L1`**: `deploy.py` sets it so the demo heals on its own. Remove it, or
  set `L3`, for real pipelines; then every fix waits in the Dive's Approvals tab.
- **The role is organization-wide.** Every owner's agent accounts hold `self_healing_pipeline_agent`, so they can
  read every share granted to it. Set `AGENT_ROLE` per owner or team to separate them.
- **Agent accounts and shares stay** after you delete the Flights. Remove them with
  `DELETE /v1/users/<name>` (REST API) and `DROP SHARE <db>_heal_agents`.
- **The sandbox runs the healer's Python and packages, without the pipeline's secrets.** A failure that depends on either
  will not reproduce: the incident is watched, and escalated if production keeps failing.
- `CREATE SNAPSHOT` briefly blocks writes to the database. The healer runs it once per fix, not per verify.
- **One fix per pipeline at a time.** A pending proposal holds its pipeline until you decide; the healer
  reminds you daily.
- **Cost.** Each fix costs real LLM money (about $0.10 to $0.30 here). Keep `BUDGET_USD` (enforced while the
  agent works) and the daily cap (`daily_spend_cap_usd` in `heal.main.control`).
- **Missed runs.** The healer reads the last 25 runs of each pipeline. Runs that finish while it is off for
  longer than that are not processed; it sends a `gap` alert.
- Turn the schedules off when you are done: `deploy.py --no-schedules`.
- DuckDB uses a lowercase `motherduck_token` before `MOTHERDUCK_TOKEN`; `deploy.py` stops if both are set to
  different tokens.

## What you'll adjust

| Knob | Where | Default |
|---|---|---|
| Pipelines to heal, their databases, protected lines, contract and drift check | `PIPELINES` in `healer_flight.py` | the two weather pipelines |
| Autonomy: `L3` waits for Approve in the Dive, `L1` ships | Healer config `AUTONOMY` | `L3` in code; `L1` in this demo's `deploy.py` |
| Agent pool size (= most fixes at once) and role | Healer config `AGENT_POOL_SIZE`, `AGENT_ROLE`, `AGENT_PREFIX` | `3`, `self_healing_pipeline_agent`, `heal_agent_<owner>_` |
| Fixer, test-reviewer, approver and classifier models | Healer config `MODEL`, `TEST_REVIEW_MODEL`, `APPROVER_MODEL`, `CLASSIFIER_MODEL` | Claude Opus latest, GPT Sol latest, GPT Sol latest, GPT Luna latest |
| Budget per incident; review rounds; attempts | Healer config `BUDGET_USD`, `APPROVER_ROUNDS`, `MAX_ATTEMPTS` | `10`, `2`, `2` |
| Check interval while fixing; notices to an agent | Healer config `CHECK_EVERY_S`, `NOTICE_EVERY_S` (and `NOTICE_MAX_CHARS` in code) | `60`, `60` (`600`) |
| Flood limits | `MAX_NEW_INCIDENTS_PER_CHECK`, `STORM_MAX_PER_HOUR`, `CHECK_BUDGET_S` | `10`, `5`, `45` |
| Kill switch, daily spend cap | `heal.main.control` keys `kill_switch`, `daily_spend_cap_usd` | `off`, `20` |
| Alert webhook | Flight secret `heal_webhook` with key `WEBHOOK_URL` | none (alerts only in the Dive) |
| Schedules | `deploy.py` (`--no-schedules` to skip) | healer every 5 min, pipelines hourly |

## Run it

Everything runs as one MotherDuck account (an Admin, see Caveats), the owner of the Flights. These steps
work from a fresh service account.

1. Add your OpenRouter key as a Flight secret (DuckDB CLI 1.5.6 or later, connected as the owner):

   ```sql
   CREATE OR REPLACE SECRET openrouter IN motherduck (TYPE flights, PARAMS MAP {'OPENROUTER_API_KEY': getenv('OPENROUTER_API_KEY')});
   ```

   Optional: alerts to Slack (or anything that accepts a Slack-style `{"text": ...}` POST):

   ```sql
   CREATE OR REPLACE SECRET heal_webhook IN motherduck (TYPE flights, PARAMS MAP {'WEBHOOK_URL': getenv('HEAL_WEBHOOK_URL')});
   ```

### Deploy as a Flight

2. Deploy from this folder. This creates the `heal` and `heal_demo` databases, the three Flights (resolved
   by name with `MD_LIST_FLIGHTS`, created with `MD_CREATE_FLIGHT`) and the Dive. It also runs each new
   pipeline once, so there is production data to clone:

   ```bash
   cd self-healing-flights
   MOTHERDUCK_TOKEN=... uv run --with duckdb==1.5.6 --with pytz deploy.py
   ```

3. Break a pipeline on purpose. This makes the weather API send a new payload shape (DuckDB 1.5.6 or later, connected as the owner):

   ```sql
   SET VARIABLE w = (SELECT flight_id FROM MD_LIST_FLIGHTS(owner_only := true) WHERE flight_name = 'heal-demo-weather');
   SELECT * FROM MD_UPDATE_FLIGHT(flight_id := getvariable('w'), config := MAP {'NWS_FEATURE_FLAGS': 'forecast_temperature_qv'});
   SELECT * FROM MD_RUN_FLIGHT(flight_id := getvariable('w'));
   ```

4. Open the **Self-healing Flights** Dive. Within about 5 minutes the healer opens an incident. On its first fix
   it creates the agent accounts and the restricted share; the Overview shows them under Agent accounts. In
   a few more minutes the fix is reviewed and shipped, and two green runs later the incident is `resolved` with a
   Guide. With `AUTONOMY=L3`, the fix instead waits in the **Approvals** tab with its diff and every reviewer's
   verdict. **Approve and ship** runs the healer to ship it.

To undo a fix, roll the pipeline Flight back in the Flights UI. Re-running `deploy.py` leaves existing pipeline
Flights alone, so it never overwrites a fix; `--reset-pipelines` restores the original code.

## Security

- **The agent holds no MotherDuck token.** It can read and edit only its own directory, and its shell is limited to
  `verify` and `inbox` with plain arguments. opencode rejects anything else: chaining, pipes, redirections, `$`
  expansion and substitution. The OpenRouter key is in opencode's config file, which only the healer can read, not
  in its environment.
- **Code the agent writes runs as an agent service account, not as you.** `verify` runs it under that account's own
  unprivileged uid, with no supplementary groups, `python -I`, limits on processes, file size and CPU, and every
  leftover process killed afterwards. The code is copied from a directory the healer owns, so it cannot plant links
  for the healer to follow. Its token is short-lived and is for an agent account that reads production only through a
  read-only, restricted share and writes only to its own zero-copy clones. After each fix the healer revokes every
  token of that account and drops every database and share it owns. The healer marks itself non-dumpable and gives
  child processes a minimal environment, so the owner's token never leaves the healer process.
- **Every fix is checked and reviewed independently:**
  - The healer's own checks and the pipeline's contract run on the output, on exactly the code that would ship.
  - A separate test-reviewer agent rejects removed or weakened tests and vacuous new ones.
  - An approver model judges the diff and the healer's evidence, not the agent's reasoning.
  - At `L3` a human approves each fix.
  - After a fix ships, its green runs only count if production passes the contract too.
- **A deterministic risk gate overrides `L1`.** A fix that adds anything a data fix rarely needs waits for a human,
  even at `L1`:
  - imports
  - process, file, network or environment access
  - new hosts
  - SQL such as `ATTACH`, `SECRET`, `GRANT` or `MD_*`
- **Pipelines get no secrets.** Only the healer Flight holds the OpenRouter key and the webhook URL. A healer run started
  with any setting overridden other than `ACTION`, `PROPOSAL_ID` or `INCIDENT_ID` refuses to run, so run access cannot
  switch autonomy to `L1`.
- **Upstream text is data.** Log excerpts and recalled Guides reach agents and reviewers fenced and labelled as
  untrusted, never as instructions. Alerts escape it for Slack.
- **The Dive's buttons run the healer Flight.** Approve and Reject claim the proposal atomically and record who
  decided. A run always uses the owner's settings, so grant `run` on the healer only to people who may approve fixes.

What remains:
- **A shipped fix runs as the owner, an Admin.** A malicious change that got past every gate (and, at `L3`, past you)
  would run with Admin rights. Keep `L3` for real pipelines.
- **opencode itself runs as root** in the healer's container. It is fenced by its permissions, not by the operating
  system.
- **Production data reaches the model provider.** It goes to OpenRouter's models in failure messages and samples, and
  into the organization's Guides.

## Learn more

- Flights and Dives guides: `get_flight_guide`, `get_dive_guide`; service accounts, roles and shares:
  `ask_docs_question`.
- Guides: resolved incidents are stored under the `self-healing/incidents/<category>` topic (`MD_LIST_GUIDES`,
  DuckDB 1.5.6+), each with a code snippet a later fix can reuse.
- The Dive's Architecture tabs explain the design in three ways: a 2 a.m. story, a zoom from system to fix loop,
  and an incident's life as states and time.
