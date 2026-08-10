---
title: Ingest Garmin Connect Fitness Data on a Schedule
id: flight-garmin-connect-ingest
description: >-
  A reusable Flight that pulls Garmin Connect activities and daily health
  metrics into MotherDuck on a schedule, authenticating headlessly with a cached
  OAuth token instead of a password. Use when you want your own training data in
  SQL and need a scheduled, incremental refresh from an MFA-gated personal API.
type: template
category: ingestion
features: [flights]
tags: [python, ingest]
prompt: >-
  I want a scheduled Flight that pulls my Garmin Connect training data
  (activities, plus daily metrics like steps, resting HR, and VO2max) into
  MotherDuck so I can analyze it in SQL, authenticating headlessly with a cached
  OAuth token rather than a password. Help me adapt the "Ingest Garmin Connect
  Fitness Data on a Schedule" recipe to my own data and use case, using it as a
  guide: https://motherduck.com/docs/cookbook/flight-garmin-connect-ingest
published_date: 2026-06-29
---

# Ingest Garmin Connect Fitness Data on a Schedule

A single-file Flight that loads your [Garmin Connect](https://connect.garmin.com)
fitness data into MotherDuck so you can analyze training in SQL. It demonstrates
two MotherDuck patterns worth reusing for any personal-API source: **headless
OAuth** (log in once locally through MFA, then run unattended from a cached token
held in a Flights secret) and **idempotent incremental loads** (append immutable
records, delete-and-replace the values that backfill).

Everything is driven by Flight config, so you adapt it by setting config values
rather than editing `flight.py`. A fresh deploy backfills the last `BACKFILL_DAYS`
and builds `garmin.main.activities` and `garmin.main.daily_metrics` in your own
account, so the first run produces queryable tables you can point at your own
analysis.

## How it works

The data comes from the community
[`garminconnect`](https://pypi.org/project/garminconnect/) client, which wraps
Garmin's private Connect API (there is no official public API).

Garmin's first login is always interactive — password plus an MFA code — which a
headless Flight cannot supply. The recipe splits auth from ingestion:

- **Once, locally:** you log in with MFA and serialize the resulting token (a DI
  bearer + refresh token) into a MotherDuck `TYPE flights` secret. See
  [Get a Garmin token](#get-a-garmin-token-one-time).
- **On every scheduled run:** `flight.py` reads that token from the secret and
  passes it straight to `garminconnect`. The client treats a long (>512-char)
  `tokenstore` argument as a serialized token rather than a file path, so no
  token file ever lands on the Flight. The DI refresh token auto-renews for about
  a year.

Each run then:

1. Connects to MotherDuck (`md:`) and `CREATE DATABASE`/`CREATE SCHEMA IF NOT EXISTS`
   for the destination, so the Flight owns everything it needs.
2. Pulls **activities** for the date window (`get_activities_by_date`) — distance,
   duration, speeds, HR, elevation, cadence, training effect — and, for the
   activity types in `HR_ZONES_FOR`, the per-activity HR time-in-zone so an
   easy-vs-hard split rests on real zone seconds.
3. Pulls **daily metrics** per calendar day — steps and resting/min/max HR
   (`get_stats`), VO2max (`get_max_metrics`), and optionally training readiness,
   status, acute/chronic load, and ACWR.
4. Loads both: `activities` is append-only (anti-joined on `activity_id`, since
   activities are immutable once recorded); `daily_metrics` is delete-then-insert
   over the pulled range, because values like training load and VO2max backfill
   into earlier days.

On the first run (destination absent) it backfills `BACKFILL_DAYS`; later runs
re-pull only the last `INCREMENTAL_DAYS` so late-syncing activities and lagging
training-load values get corrected without rescanning history.

## Questions to answer

- Target MotherDuck database and schema (`DESTINATION_DATABASE`, `DESTINATION_SCHEMA`);
  is letting the Flight create them acceptable?
- How much history on the first run (`BACKFILL_DAYS`), and how wide a correction
  window on later runs (`INCREMENTAL_DAYS`)?
- Which activity types need HR time-in-zone (`HR_ZONES_FOR`)?
- Does your device report training readiness / load at all, or should you skip
  those calls (`PULL_TRAINING_LOAD`)?
- What schedule (cron) should it run on?

## Caveats

- **Unofficial API.** `garminconnect` wraps Garmin's private endpoints; field
  names and shapes can change without notice, and Garmin may rate-limit or block.
  The per-call `retry()` and the `time.sleep` pacing are deliberate — keep them.
- **`curl_cffi` is load-bearing, not optional.** `garminconnect` routes login
  through it for TLS-fingerprint rotation, which is what gets past Garmin's WAF on
  cloud IPs (exactly where the Flight runs). It is pinned in `requirements.txt`;
  do not drop it.
- **Device capability gaps look like bugs but are not.** Training readiness,
  training status, and ACWR come back empty on devices/accounts that do not
  compute them, and VO2max only updates on qualifying activities. Empty columns
  on those metrics are expected; set `PULL_TRAINING_LOAD=false` to skip two API
  calls per day if your device never populates them.
- **Token expiry.** The cached refresh token renews automatically (~1 year) but
  is invalidated by a Garmin password change. If runs start failing auth, re-mint
  the secret with the snippet below.
- **The destination schema is inferred on the first run** from the JSON Garmin
  returns. If a future API change adds or renames fields, migrate or recreate the
  table rather than expecting a silent merge.
- **Keep the MotherDuck token out of config.** Select a token on the Flight so
  `MOTHERDUCK_TOKEN` is injected at runtime; the Garmin token is the *only*
  credential this recipe stores in a secret.

## What you'll adjust

Every knob is a config/env value read at the top of `flight.py`. Set them as
Flight config, not by editing code.

| Config key | Default | Purpose |
|---|---|---|
| `DESTINATION_DATABASE` | `garmin` | MotherDuck database to build into. Created if missing. Validated as a SQL identifier. |
| `DESTINATION_SCHEMA` | `main` | Schema for the destination tables. Validated as a SQL identifier. |
| `ACTIVITIES_TABLE` | `activities` | Per-activity table name. Validated as a SQL identifier. |
| `DAILY_METRICS_TABLE` | `daily_metrics` | Per-day metrics table name. Validated as a SQL identifier. |
| `BACKFILL_DAYS` | `56` | History window for the first run (destination absent). |
| `INCREMENTAL_DAYS` | `3` | Correction window re-pulled on later runs. |
| `FORCE_BACKFILL_DAYS` | `0` | If `>0`, pull this many days regardless of whether the table exists. Idempotent — use for a one-off deep backfill (e.g. `730`). |
| `PULL_TRAINING_LOAD` | `true` | Pull training readiness/status per day. Set `false` to skip (empty on some devices; saves two calls/day on long backfills). |
| `HR_ZONES_FOR` | `running` | Comma list of activity `typeKey`s to fetch HR time-in-zone for. |
| `GARMIN_TOKEN` | (Flights secret) | Serialized Garmin OAuth token. Stored in a secret, never in config. See below. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth to MotherDuck. Select a token on the Flight; never put it in config. |

## Run it

You need a MotherDuck account and access token, a Garmin Connect account, and the
three pinned dependencies in [`requirements.txt`](requirements.txt).

### Get a Garmin token (one-time)

Mint the token locally, where you can answer the MFA prompt, and store it as a
MotherDuck **Flights secret**. Save this as `token_mint.py` and run it with your
MotherDuck token in the environment:

```python
import duckdb
from garminconnect import Garmin

EMAIL, PASSWORD = "you@example.com", "your_password"   # Garmin Connect login

client = Garmin(EMAIL, PASSWORD, prompt_mfa=lambda: input("MFA code: ").strip())
client.login()                       # interactive: prompts for MFA the first time
client.client.dump("~/.garminconnect")  # cache locally (0600) for local smoke tests
token = client.client.dumps()        # the serialized DI token (auto-renews ~1yr)

safe = token.replace("'", "''")      # token is JSON (double quotes only); escape defensively
duckdb.connect("md:").execute(
    "CREATE OR REPLACE SECRET garmin_auth IN motherduck "
    f"(TYPE flights, PARAMS MAP {{ 'GARMIN_TOKEN': '{safe}' }})"
)
print("Secret 'garmin_auth' created — the Flight reads it as garmin_auth_GARMIN_TOKEN.")
```

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with garminconnect==0.3.6 --with curl_cffi==0.15.0 --with duckdb==1.5.4 token_mint.py
```

The token never prints to your terminal — it goes straight into the secret. A
`TYPE flights` secret injects each param under `<secret_name>_<PARAM>`, so the
`GARMIN_TOKEN` param above arrives at the Flight as `garmin_auth_GARMIN_TOKEN`.
(DuckDB lowercases the unquoted secret name into the prefix.) `flight.py` accepts
either the bare `GARMIN_TOKEN` (local runs) or any var ending in `_GARMIN_TOKEN`
(the secret, whatever you named it), so the secret name you choose does not
matter. You can also create the secret from the
[MotherDuck UI: Settings > Secrets](https://app.motherduck.com/settings/secrets).

To smoke-test the ingestion locally before deploying, feed it the cached token:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export GARMIN_TOKEN="$(cat ~/.garminconnect/garmin_tokens.json)"
uv run --with garminconnect==0.3.6 --with curl_cffi==0.15.0 --with duckdb==1.5.4 flight.py
```

That run creates `garmin.main.activities` and `garmin.main.daily_metrics`,
backfills `BACKFILL_DAYS`, and prints row counts. Override any default inline,
for example `FORCE_BACKFILL_DAYS=730 uv run ... flight.py` for a two-year backfill.

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `garmin_ingest`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the keys from [What you'll adjust](#what-youll-adjust) you want to
  override (omit any you are keeping at default)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed. The Garmin token comes
from the `garmin_auth` Flights secret you created above — make sure that secret is
available to the Flight's token.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm the backfill succeeds. Then add a daily
schedule by updating `schedule_cron` with `MD_UPDATE_FLIGHT` — `0 13 * * *`
(13:00 UTC) is a reasonable default, late enough that the prior day has finished
syncing from the watch. Schedule updates are metadata-only and do not create a
new Flight version.

## Security

- **No password on the Flight.** Only the OAuth token lives in a MotherDuck
  `TYPE flights` secret (or a local env var for smoke tests); your Garmin password
  is used once, locally, during the token mint and never stored or sent to
  MotherDuck. The token is read from the secret-injected env var at runtime, never
  hard-coded or placed in Flight `config`.
- **Identifier validation.** Every config-supplied name (`DESTINATION_DATABASE`,
  `DESTINATION_SCHEMA`, `ACTIVITIES_TABLE`, `DAILY_METRICS_TABLE`) flows into
  `CREATE`/`DELETE`/`INSERT` statements that cannot be parameterized, so each is
  checked against `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL runs.
- **Parameterized data.** The staged JSON paths and the daily delete window are
  passed as bound parameters to `read_json_auto`, the `DELETE`, and the existence
  check, never string-formatted into SQL.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions (`read_json_auto` type inference,
  `TYPE flights` secrets, incremental loads): use the `ask_docs_question` MCP tool.
- The Garmin client and its available endpoints:
  [`garminconnect` on PyPI](https://pypi.org/project/garminconnect/).
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (its three pinned dependencies).
