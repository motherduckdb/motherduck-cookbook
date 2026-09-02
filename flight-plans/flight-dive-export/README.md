---
title: Export a Dive to PDF and Deliver It
id: flight-dive-export
description: >-
  A Flight that renders one of your Dives in headless Chromium on MotherDuck
  compute, stores the PNG and the PDF as BLOBs in a MotherDuck table, and
  delivers them to a Slack or Microsoft Teams channel. Use when you want a
  scheduled PDF or PNG of a Dive delivered to where people already work, while
  Dives have no native export.
type: template
category: automation
features: [flights, dives, admin_api]
tags: [slack, microsoft-teams]
prompt: >-
  I want a scheduled PDF or PNG of one of my MotherDuck Dives rendered and
  delivered to my team's Slack or Microsoft Teams channel, without anyone
  clicking anything. Help me
  adapt the "Export a Dive to PDF and Deliver It" recipe to my own data and use
  case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-dive-export
published_date: 2026-09-02
---

# Export a Dive to PDF and Deliver It

Dives have no native PDF or PNG export yet. This single-file Flight builds one
out of the pieces that already exist: a Dive embed session, headless Chromium
running on MotherDuck compute, and a MotherDuck table to keep the results in.
Each run renders the Dive as it looks to a service account, captures a
full-height PNG and a single-page PDF, appends both to `STORE_TABLE` as BLOBs,
and hands them to whatever `DELIVERY` names. Put it on a cron and the report
arrives on its own.

A first run takes roughly 95 seconds, most of it the one-off Chromium download;
later runs on a warm image are much quicker.

## How it works

`flight.py` runs a fixed sequence, and every input is a config value:

1. `POST /v1/dives/{DIVE_ID}/embed-session` with `{"username": SERVICE_ACCOUNT}`
   mints a 24-hour embed session for the Dive. Each run mints its own, so there
   is nothing to refresh.
2. `playwright install chromium` pulls the browser into the Flight container.
   The image ships without one, which is what makes the first run slow.
3. Chromium opens the session URL at `VIEWPORT`, waits `WAIT_MS` for the Dive's
   queries to finish, then captures a `full_page` PNG at `SCALE` and a PDF sized
   to the rendered content height (one tall page, so no chart is cut in half).
4. Each rendition named in `ATTACH` is appended to `STORE_TABLE`
   (`captured_at`, `label`, `source_url`, `kind`, `filename`, `mime`,
   `byte_count`, `content`). The database and schema are created on first run.
5. Every target named in `DELIVERY` gets the same renditions. Delivery runs
   after the store, so a broken webhook still leaves the file somewhere you can
   reach it. `DELIVERY=""` stores and stops.

Delivery credentials are validated before Chromium starts, so a missing secret
fails the run in seconds instead of after a 90-second render.

The Dive is rendered *as the service account*, so the export contains exactly
what that account is allowed to read. Grant it read access on the databases the
Dive queries and nothing else.

## Questions to answer

- Which Dive is being exported, and what is its id (the UUID in the Dive URL)?
- Which service account renders it, and does it have read access to everything
  the Dive queries?
- Is the PDF, the PNG, or both wanted?
- How tall does the viewport have to be to fit the whole Dive (see
  [Sizing](#sizing))?
- Which database holds the exports, and who is allowed to read that table?
- Where should the report land: a Slack channel, a Teams channel, the exports
  table, or several of those?
- For Teams, is there an Entra app registration to use, and who can grant it
  admin consent?
- What schedule (cron, UTC) matches how often the underlying data changes?

## Caveats

- **A service account is required.** `embed-session` rejects a regular user
  account with a generic `404 entity not found`. Create one with the REST API
  (see [Run it](#run-it)) before deploying.
- **The Flight's token must be able to mint embed sessions.** That means a
  read/write token on an account with the admin permission; a read-scaling or
  read-only token fails the API call.
- **A Dive scrolls inside its own frame**, so `full_page` stops at the fold
  instead of growing the page. `VIEWPORT` is what decides how much is captured,
  which makes this a good fit for KPI and chart layouts and a bad fit for long
  browsable tables. See [Sizing](#sizing).
- **The exports table grows.** Each run appends a few MB of BLOBs. Prune it on a
  schedule (`DELETE FROM ... WHERE captured_at < now() - INTERVAL 90 DAY`) or set
  `STORE_TABLE = ""` once delivery is enough.
- **Expect noisy logs on a successful run.** `Incomplete gRPC response no
  trailer transmitted` and `unassociated response: [undefined, MD_EVENT]` show up
  on every healthy render. So do Content Security Policy console errors from
  third-party scripts. None of them mean the capture failed; check the reported
  PNG and PDF byte counts instead.
- **Slack needs a bot token, not a webhook.** Incoming webhooks cannot carry a
  file. Delivery uses a bot token with `files:write`, and the bot has to be a
  member of the target channel or the upload is rejected with `not_in_channel`.
- **Teams delivery takes two hops.** No Teams webhook can carry a file, and
  posting a channel message with an app-only token is restricted to migration
  scenarios. So the file goes into the SharePoint folder behind the channel
  through Microsoft Graph (it appears in the channel's **Files** tab) and the
  optional `TEAMS_WEBHOOK_URL` posts a card that links to it. Without the
  webhook the file still lands, it is just not announced.
- **`WAIT_MS` is the whole correctness story for freshness.** The capture happens
  on a timer, not on a "queries finished" signal, so a Dive that is still loading
  at the deadline is captured half-rendered. Raise it if charts come out empty.

## Sizing

Set `VIEWPORT` tall enough to fit everything you want in the shot. On a Dive with
a KPI row, a chart, and a table, `1440x1000` captured 8 table rows and
`1440x2600` captured about 25. `SCALE` is the PNG pixel ratio: `2` is crisp,
`1.5` halves the file size.

## What you'll adjust

Everything is a Flight config value (or an env var for a local run). Nothing has
to be edited in the code.

| Knob | Default | Purpose |
|---|---|---|
| `DIVE_ID` | (unset) | The Dive to render, the UUID from its URL. Required unless `SHOT_URL` is set. |
| `SERVICE_ACCOUNT` | (unset) | Service account username the Dive is rendered as. Must be a service account. |
| `REPORT_NAME` | `Dive export` | Human name for the report. Used in the filenames (slugified) and in the message text. |
| `ATTACH` | `pdf,png` | Which renditions to produce: `pdf`, `png`, or both. |
| `VIEWPORT` | `1440x2600` | Browser viewport as `WxH`. Decides how much of the Dive is captured. |
| `SCALE` | `2` | PNG device pixel ratio. `1.5` for smaller files. |
| `WAIT_MS` | `15000` | Settle time in ms after load, so the Dive's queries can finish. |
| `STORE_TABLE` | `flights_demo.main.dive_exports` | Where the BLOBs land, as `database.schema.table`. `""` skips the copy. |
| `DELIVERY` | (unset) | Comma-separated delivery targets: `slack`, `teams`. Empty stores the export and stops. |
| `DRY_RUN` | `false` | `true` renders and stores, then logs what each target would send instead of sending it. |
| `MESSAGE` | (generated) | Message text stored with the export. Defaults to `<REPORT_NAME> captured <timestamp> UTC.` |
| `API_BASE` | `https://api.motherduck.com` | REST API base. The API is region-scoped, so only a non-production environment needs this. |
| `SHOT_URL` | (unset) | Debugging only: capture this URL instead of minting a Dive session. |
| `LABEL` | `adhoc` | Label stored with a `SHOT_URL` capture. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth for the REST API and for the write. Select a token on the Flight; never hard-code it. |

Slack delivery adds two credentials, which belong in a Flight secret rather than
in config:

| Knob | Purpose |
|---|---|
| `SLACK_BOT_TOKEN` | Bot User OAuth token (`xoxb-...`) with the `files:write` and `chat:write` scopes. |
| `SLACK_CHANNEL_ID` | Destination channel id (`C...`), from the channel's details in Slack. |

Teams delivery needs an Entra app registration, the channel's ids, and
optionally a webhook to announce the upload:

| Knob | Purpose |
|---|---|
| `TEAMS_TENANT_ID` | Directory (tenant) id of the Entra app registration. |
| `TEAMS_CLIENT_ID` | Application (client) id of that registration. |
| `TEAMS_CLIENT_SECRET` | Client secret for it. |
| `TEAMS_TEAM_ID` | The team's group id (`groupId` in the channel link). |
| `TEAMS_CHANNEL_ID` | The channel id (`19:...@thread.tacv2`). |
| `TEAMS_WEBHOOK_URL` | Optional. Workflows webhook that posts the Adaptive Card linking to the uploaded files. |

Config is per-run overridable, so one Flight can export several Dives.

## Run it

You need a MotherDuck account, an admin read/write token, and a Dive.

First create the service account that renders the Dive (skip if you already have
one), then grant it read access on the databases the Dive queries:

```bash
export MOTHERDUCK_TOKEN=your_admin_token_here
curl -X POST "https://api.motherduck.com/v1/users" \
  -H "Authorization: Bearer $MOTHERDUCK_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username":"dive_export_bot"}'
```

Then run the export locally:

```bash
export DIVE_ID=00000000-0000-0000-0000-000000000000
export SERVICE_ACCOUNT=dive_export_bot
uv run --with-requirements requirements.txt flight.py
```

The first local run downloads Chromium too. To check the render path without a
Dive or a service account, point it at any URL:

```bash
SHOT_URL=https://motherduck.com STORE_TABLE="" \
  uv run --with-requirements requirements.txt flight.py
```

### Set up Slack delivery

Slack's incoming webhooks cannot attach a file, so delivery uses a bot token and
the [external upload flow](https://docs.slack.dev/messaging/working-with-files/):

1. Open [api.slack.com/apps?new_app=1](https://api.slack.com/apps?new_app=1),
   choose **From a manifest**, select your workspace, and paste this manifest:
   ```json
   {
       "display_information": { "name": "Dive Export" },
       "features": { "bot_user": { "display_name": "Dive Export" } },
       "oauth_config": { "scopes": { "bot": ["files:write", "chat:write"] } },
       "settings": {
           "org_deploy_enabled": false,
           "socket_mode_enabled": false,
           "token_rotation_enabled": false
       }
   }
   ```
2. Review, create the app, then **Install to Workspace** and copy the **Bot User
   OAuth Token** (`xoxb-...`) from **OAuth & Permissions**.
3. Invite the bot to the destination channel (`/invite @Dive Export`). Without
   this the upload fails with `not_in_channel`.
4. Copy the channel id (`C...`) from the channel's **About** details.

Then send a real report to Slack:

```bash
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_CHANNEL_ID=C0123456789
DELIVERY=slack uv run --with-requirements requirements.txt flight.py
```

Use `DRY_RUN=true` first to confirm the render and the filenames without posting
anything.

### Set up Teams delivery

Teams needs Microsoft Graph for the file and (optionally) a webhook for the
announcement:

1. In the [Microsoft Entra admin center](https://entra.microsoft.com), register
   an application (**App registrations > New registration**). Copy the
   **Application (client) ID** and **Directory (tenant) ID**, then add a client
   secret under **Certificates & secrets**.
2. Under **API permissions**, add Microsoft Graph **application** permissions
   `ChannelSettings.Read.All` (resolve the channel's Files folder) and
   `Files.ReadWrite.All` (write into it), then grant admin consent. If a call is
   refused, the Graph error body names the permission it wanted; this Flight
   surfaces that body verbatim.
3. Get the ids from Teams: on the channel, **Get link to channel** produces a URL
   containing `groupId=<TEAMS_TEAM_ID>` and
   `threadId=<TEAMS_CHANNEL_ID>` (the `19:...@thread.tacv2` value).
4. Optional, for the card: in Teams open the channel's **Workflows** and create
   one from the **Post to a channel when a webhook request is received**
   template, then copy its URL as `TEAMS_WEBHOOK_URL`. (This replaces the retired
   Office 365 connectors.)

```bash
export TEAMS_TENANT_ID=... TEAMS_CLIENT_ID=... TEAMS_CLIENT_SECRET=...
export TEAMS_TEAM_ID=... TEAMS_CHANNEL_ID='19:...@thread.tacv2'
export TEAMS_WEBHOOK_URL=https://prod-00.westeurope.logic.azure.com/...
DELIVERY=teams uv run --with-requirements requirements.txt flight.py
```

### Getting the files out

The renditions are BLOBs, so pull the newest one through the client:

```bash
duckdb "md:?motherduck_token=$MOTHERDUCK_TOKEN" -noheader -list -c "
  SELECT to_base64(content)
  FROM flights_demo.main.dive_exports
  WHERE kind = 'pdf'
  ORDER BY captured_at DESC
  LIMIT 1
" | tail -1 | base64 --decode > dive.pdf
```

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `dive_export`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: at least `DIVE_ID`, `SERVICE_ACCOUNT`, and `DELIVERY`, plus any knob
  from the table above
- `max_runtime_sec`: optional cap on a run's duration in seconds (`0` = no cap).
  Leave room for the Chromium download on the first run.
- `flight_secret_names`: the secret holding the delivery credentials

Store the delivery credentials as a MotherDuck **Flights secret**, never in
config. The simplest way is the MotherDuck UI: open
[Settings > Secrets](https://app.motherduck.com/settings/secrets), add a secret
of type **Flights**, and give it one param per credential. The same secret can be
created from any write-enabled SQL connection (read-only connections reject
`CREATE SECRET`):

```sql
CREATE SECRET dive_export_delivery IN motherduck (
  TYPE flights,
  PARAMS MAP {
    'SLACK_BOT_TOKEN': 'xoxb-...',
    'SLACK_CHANNEL_ID': 'C0123456789',
    'TEAMS_CLIENT_SECRET': '...'
  }
);
```

A `TYPE flights` secret injects each param under its bare name, so the params
above arrive as `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID` whatever you name the
secret. (Each param is also injected namespaced as `<secret_name>_<PARAM>`,
which disambiguates when several secrets define the same param name.)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed. It must be a read/write
token on an admin account, or minting the embed session fails.

Create the Flight without a schedule, trigger one run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`; inspect a run with `MD_GET_FLIGHT_RUN(flight_id := ...,
run_number := ...)`), and look at the exported PDF before you trust it. Then add
a schedule by updating the Flight's `schedule_cron` with `MD_UPDATE_FLIGHT` (for
example `0 6 * * 1`, Mondays at 06:00 UTC). Schedule updates are metadata-only
and do not create a new Flight version.

## Security

- **The export inherits the service account's access.** Whatever that account
  can read can end up in a PDF that leaves MotherDuck, so scope its grants to
  the Dive's own data.
- **The session token stays out of the table.** `source_url` is stored without
  the URL fragment, which is where the embed session lives.
- **Identifier validation.** `STORE_TABLE` is split and each part checked
  against `^[A-Za-z_][A-Za-z0-9_]*$` before it is interpolated into the `CREATE`
  and `INSERT` statements, which cannot be parameterized. Row values are bound
  parameters.
- **Delivery credentials live in a secret.** `SLACK_BOT_TOKEN` and
  `TEAMS_CLIENT_SECRET` are read from the environment at run time and never
  logged; put them in a Flights secret, not in Flight config, which is visible to
  anyone who can read the Flight.
- **Scope the Entra app narrowly.** `Files.ReadWrite.All` is tenant-wide. If your
  tenant allows it, prefer `Sites.Selected` granted only on the site behind the
  destination team, so the app cannot write anywhere else.
- **Delivery is a data egress path.** A Dive that renders sensitive numbers
  sends them to whoever can read the destination channel. Pick the channel with
  that in mind.
- **Treat the exports table as sensitive.** It holds rendered business data; the
  same read grants you would put on the Dive's tables belong on it.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Dive embed sessions:
  [Create a Dive embed session](https://motherduck.com/docs/sql-reference/rest-api/dashboards-create-embed-session/)
  and the [REST API reference](https://motherduck.com/docs/sql-reference/rest-api/motherduck-rest-api/).
- Capture options (viewport, `full_page`, PDF sizing): the
  [Playwright screenshots](https://playwright.dev/python/docs/screenshots) and
  [PDF](https://playwright.dev/python/docs/api/class-page#page-pdf) docs.
- Slack file delivery:
  [uploading files](https://docs.slack.dev/messaging/working-with-files/) and the
  [`files.completeUploadExternal`](https://docs.slack.dev/reference/methods/files.completeuploadexternal)
  reference.
- Teams file delivery:
  [channel filesFolder](https://learn.microsoft.com/en-us/graph/api/channel-get-filesfolder)
  and [upload sessions](https://learn.microsoft.com/en-us/graph/api/driveitem-createuploadsession)
  in Microsoft Graph, plus
  [Adaptive Cards](https://learn.microsoft.com/en-us/power-automate/teams/send-a-message-in-teams)
  through a Workflows webhook.
- Deeper MotherDuck or DuckDB questions: use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight
  source) and [`requirements.txt`](requirements.txt) (`duckdb`, `playwright`,
  `httpx`).
