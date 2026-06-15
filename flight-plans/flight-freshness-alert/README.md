---
title: Alert on Stale Tables From a Flight
id: flight-freshness-alert
description: >-
  A reusable Flight that checks table freshness with dbt-style warn/error age
  thresholds on a schedule and posts a Slack alert when data goes stale. Use
  when you want scheduled freshness monitoring on MotherDuck tables with a
  warn/error severity model and a Slack notification.
type: template
features: [flights]
tags: [slack]
---

# Alert on Stale Tables From a Flight

A single-file Flight that monitors how fresh your MotherDuck tables are and
posts a Slack alert when one falls behind. It follows the dbt source-freshness
model: each table names a timestamp column, and you compare `now() - MAX(column)`
against a `warn` and an `error` threshold. The Flight schedules the check, writes
an audit ledger, and pings Slack when something is stale.

Unlike the config-driven templates here, the thing you edit is a `CHECKS` list at
the top of `flight.py` (much like you replace `repo_rows()` in
[flight-dlt-ingest](../flight-dlt-ingest)). The defaults point at read-only
`sample_data`, a frozen ~2022 snapshot, so a fresh deploy always reports `error`
and fires one Slack message. That doubles as a built-in test that your webhook is
wired up. Swap `CHECKS` for your own tables before you add a schedule.

## How it works

`flight.py` runs a fixed sequence; only the `CHECKS` list changes its inputs:

1. Connect to MotherDuck (`md:`).
2. For each check, run `SELECT max(column), date_diff('hour', max(column), now())`
   and assign `pass` / `warn` / `error` from the two thresholds. The comparison
   is done in SQL against the runtime clock, so it follows the runtime timezone
   (see [Time zones](#time-zones)). A missing table/column or an empty table is
   recorded as `error` for that one check, not raised, so a single typo does not
   hide the other checks.
3. Write one ledger row per check to `RESULTS_TABLE` (its database and schema are
   created on first run, since `sample_data` is read-only).
4. Print the report. If any check is at or above `ALERT_LEVEL` and
   `SLACK_WEBHOOK_URL` is set, POST a Slack Block Kit message listing each stale
   table with its lag and severity.

## Time zones

Freshness is `now() - MAX(column)`, computed in the database, so the comparison
uses the **runtime's clock and timezone**. A deployed Flight runs in **UTC**; a
local `uv run` uses your machine's timezone. Two consequences:

- **`TIMESTAMPTZ` columns are unambiguous.** They carry an offset, so the age and
  the stored `max_timestamp` are correct no matter where the code runs. Prefer
  these.
- **Naive `TIMESTAMP` columns are read in the runtime timezone.** A value like
  `2024-01-01 09:00:00` with no offset is treated as 09:00 *in the runtime tz*
  (UTC on a Flight). If your naive timestamps are actually stored in another
  timezone, the computed age is off by that UTC offset, and the `max_timestamp`
  written to the ledger reflects the runtime tz. Deploy as a Flight (UTC) for
  consistent results, or store source timestamps as `TIMESTAMPTZ`.

**Why `pytz` is a dependency.** Reading a `TIMESTAMPTZ` value back into Python as a
tz-aware `datetime` requires `pytz`. The duckdb wheel ships with no required
dependencies (timezone math inside the engine uses the bundled ICU extension), so
`pytz` is not installed automatically; without it, a check on a `TIMESTAMPTZ`
column fails with `Required module 'pytz' failed to import`. It is pinned next to
`duckdb` in `requirements.txt` so checks work on tz-aware columns.

## Questions to answer

- Which tables need monitoring, and which column on each records when its rows arrive (load time, event time, ingest time)?
- What `warn`/`error` age thresholds match each table's expected update cadence?
- Should the alert fire on `warn`, or only on `error`?
- Which Slack channel receives the alert, and is its Incoming Webhook stored as a MotherDuck secret?
- Which service account token can read the checked tables and write the ledger?
- What schedule (cron, UTC) matches how often the data should refresh?

## Caveats

- **The default `CHECKS` always alert.** `sample_data` is a read-only ~2022
  snapshot, so both default checks report `error` every run. This is intentional:
  it proves a fresh deploy can reach Slack. Replace `CHECKS` with your own tables
  before adding a schedule, or you will get a Slack message on every run.
- **Pick the right column.** Freshness is `MAX(column)`. Use the column that
  records when a row landed (load, ingest, or event time), not an unrelated date.
- **Failures degrade per check.** A missing table/column or an empty table is
  recorded as `error` for that check with the reason, rather than aborting the run.
- **A broken webhook fails the run.** A configured `SLACK_WEBHOOK_URL` that returns
  an error raises after the ledger is written, so a broken alert path shows up as a
  FAILED run instead of silently dropping the alert.
- **The ledger needs a writable database.** `RESULTS_TABLE` must live in a database
  you can write to; the Flight creates `flights_demo` if it is missing. Set
  `RESULTS_TABLE = ""` to skip the ledger.
- **Keep secrets out of code.** Put the webhook in a MotherDuck secret and select a
  token on the Flight; never hard-code either.

## What you'll adjust

The freshness checks live in the `CHECKS` list at the top of `flight.py`; two
module constants below it control alerting and the ledger. Only the Slack webhook
and the MotherDuck token come from outside the code.

| Knob | Where | Default | Purpose |
|---|---|---|---|
| `CHECKS` | top of `flight.py` | two `sample_data` tables | The list of freshness checks. Replace these entries with your own tables. |
| `table` (per check) | `CHECKS` entry | `sample_data.hn.hacker_news`, `sample_data.nyc.taxi` | Table to check, as `database.schema.table` or `schema.table`. Each part validated as a SQL identifier. |
| `column` (per check) | `CHECKS` entry | `timestamp`, `tpep_pickup_datetime` | The timestamp/date column whose `MAX` defines freshness. Use the column that records when a row arrived. |
| `warn_after_hours` / `error_after_hours` | `CHECKS` entry | `24` / `48` | Age thresholds in hours. `lag >= error` → `error`, `>= warn` → `warn`, else `pass`. |
| `ALERT_LEVEL` | top of `flight.py` | `warn` | `warn` alerts on warn+error; `error` alerts only on error. |
| `RESULTS_TABLE` | top of `flight.py` | `flights_demo.main.freshness_check_runs` | Audit ledger target as `database.schema.table`. Must be a writable database. `""` disables the ledger. |
| `SLACK_WEBHOOK_URL` | Flight secret / env var | (unset) | Slack Incoming Webhook URL. Provide it through a MotherDuck secret, never in code. As a Flight the secret arrives as `<secret_name>_SLACK_WEBHOOK_URL`; `flight.py` resolves either name. Unset → the run prints the report and skips Slack. |
| `MOTHERDUCK_TOKEN` | Flight-injected | (Flight-injected) | Auth. Select a token on the Flight; never hard-code it. |

## Run it

You need a MotherDuck account and an access token. With the defaults, the check
reads two public `sample_data` tables (no extra credentials needed). Provide
`SLACK_WEBHOOK_URL` only if you want a real Slack post during the smoke test (see
[Create the Slack webhook](#create-the-slack-webhook) below to get one).

```bash
export MOTHERDUCK_TOKEN=your_token_here
# optional: actually post to Slack instead of only printing
export SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
uv run --with-requirements requirements.txt flight.py
```

With the defaults this checks the two frozen `sample_data` tables, prints two
`error` lines, writes the ledger to `flights_demo.main.freshness_check_runs`, and
(if `SLACK_WEBHOOK_URL` is set) posts one Slack alert. That confirms the whole
path before you point `CHECKS` at your own tables.

### Create the Slack webhook

Skip this if you only want the printed report. To post to Slack, create one
Incoming Webhook and reuse its URL (see the
[Slack incoming webhooks docs](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)):

1. Open [api.slack.com/apps?new_app=1](https://api.slack.com/apps?new_app=1) and choose **From a manifest**.
2. Select your workspace.
3. Paste this manifest (it only sets the app name; rename it if you like), then review and create the app:
   ```json
   {
       "display_information": {
           "name": "Fresh Ducks"
       },
       "settings": {
           "org_deploy_enabled": false,
           "socket_mode_enabled": false,
           "is_hosted": false,
           "token_rotation_enabled": false
       }
   }
   ```
4. Open **Incoming Webhooks** in the app settings (`https://api.slack.com/apps/<APP_ID>/incoming-webhooks`),
   toggle **Activate Incoming Webhooks** on, click **Add New Webhook to Workspace**, pick the
   destination channel, and **Authorize**.
5. Copy the generated webhook URL (it looks like `https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXX`)
   and use it as `SLACK_WEBHOOK_URL`: an env var for a local run, or the secret below for a Flight.

The webhook URL is a secret and is tied to the one channel you picked. Keep it out
of code and version control (store it as a MotherDuck secret for a Flight); Slack
revokes webhook URLs that leak.

### Deploy as a Flight

Store the webhook URL from [Create the Slack webhook](#create-the-slack-webhook) as
a MotherDuck **Flights secret**. The simplest way is the MotherDuck UI: open
[Settings > Secrets](https://app.motherduck.com/settings/secrets), add a secret of
type **Flights**, and give it a `SLACK_WEBHOOK_URL` parameter. If you would rather
use SQL, you can create the same secret from the DuckDB client or any
write-enabled SQL connection (read-only connections reject `CREATE SECRET`):

```sql
CREATE SECRET freshness_slack IN motherduck (
  TYPE flights,
  PARAMS MAP { 'SLACK_WEBHOOK_URL': 'https://hooks.slack.com/services/...' }
);
```

A `TYPE flights` secret injects each param under the env var
`<secret_name>_<PARAM>`, not the bare param name: the param above arrives as
`freshness_slack_SLACK_WEBHOOK_URL`, not `SLACK_WEBHOOK_URL`. (DuckDB lowercases
the unquoted secret name into the prefix.) `flight.py` handles this: it reads
`SLACK_WEBHOOK_URL` for local runs and otherwise picks up any env var ending in
`_SLACK_WEBHOOK_URL`, so the secret name you choose does not matter.

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL
is checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `freshness_alert`
- `source_code`: the contents of [`flight.py`](flight.py), with `CHECKS` edited to your tables
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["freshness_slack"]` so the webhook is injected (as
  `freshness_slack_SLACK_WEBHOOK_URL`; `flight.py` resolves it)

No `config` is needed: every knob lives in the code. A MotherDuck token is
attached to the Flight automatically and injected at run time as
`MOTHERDUCK_TOKEN`; no token argument is needed.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm it succeeds and a Slack alert arrives.
Then edit `CHECKS` to your real tables and add a schedule (for example
`0 * * * *`, hourly) by updating the Flight's `schedule_cron` with
`MD_UPDATE_FLIGHT`. Schedule updates are metadata-only and do not create a new
Flight version.

## Security

- **Identifier validation.** Each `CHECKS` table (split on `.`) and column, and
  `RESULTS_TABLE`, are checked against `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL
  runs, because they flow into `CREATE`/`SELECT`/`INSERT` statements that cannot be
  parameterized.
- **Parameterized data.** Ledger rows (table name, column, timestamps, lag, status,
  detail) are written with bound parameters, never string-formatted into SQL.
- **Secret-based webhook.** `SLACK_WEBHOOK_URL` is read from a MotherDuck secret or
  env var at runtime, never hard-coded or placed in Flight config.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Slack delivery: [Incoming Webhooks](https://api.slack.com/messaging/webhooks) and
  [Block Kit](https://api.slack.com/block-kit) for the message format.
- Deeper MotherDuck or DuckDB questions: use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (`duckdb`, `httpx`, `pytz` — see
  [Time zones](#time-zones)).
