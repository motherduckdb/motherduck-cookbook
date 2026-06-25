---
title: Update a HubSpot List From a MotherDuck Query With a Flight
id: flight-hubspot-list-sync
description: >-
  An example of data activation / reverse ETL with Flights.
  Run a MotherDuck SQL Query to pull a list of emails and update a Hubspot list.
  That Hubspot list can then be used for automatic customer or marketing activities.
  This flight resolves emails to Hubspot contact IDs and applies the minimal add/remove via the
  Lists v3 API. Idempotent re-runs, retries with backoff, skip-and-log for
  unmatched emails, and an audit ledger.
type: template
category: integrations
features: [flights]
tags: [hubspot]
prompt: >-
  I want a Flight that runs a MotherDuck SQL query to produce a list of emails and
  reconciles a HubSpot static contact list to match it (data activation / reverse
  ETL) — resolving emails to contact IDs, applying the minimal add/remove, with
  retries and an audit log. Help me adapt the "Update a HubSpot List From a
  MotherDuck Query With a Flight" recipe to my own data and use case, using it as a
  guide: https://motherduck.com/docs/cookbook/flight-hubspot-list-sync
published_date: 2026-06-17
---

# Sync a HubSpot List From a MotherDuck Query With a Flight

Flights allow you to take action in your business based on MotherDuck data.
This flight updates the membership of a HubSpot contact
list from a MotherDuck query. The query returns email addresses; the Flight
makes the list match that set on every run.

The pattern is **reconcile by diff**, not clear-and-re-add. Each run reads the
list's current members, computes the adds and removes against the query output,
and applies only the difference. That means the list is never emptied
mid-run, a re-run with unchanged data is a no-op, and the work (and API calls)
scale with the change, not the audience size. Emails with no matching HubSpot
contact are skipped and logged so one bad address never fails the run.

## How it works

`flight.py` runs a fixed sequence:

1. **Connect.** `duckdb.connect("md:")` and run `QUERY`, reading the
   `EMAIL_COLUMN` column (normalized to lowercase, de-duplicated, blanks dropped).
2. **Resolve emails to record IDs.** Batch-read contacts by `email` (HubSpot's
   `POST /crm/v3/objects/contacts/batch/read`, 100 inputs per call). Unmatched
   emails are collected and logged, not created.
3. **Guard the target.** Fetch the list and refuse to continue unless its
   `processingType` is `MANUAL` or `SNAPSHOT` — HubSpot rejects membership
   writes on `DYNAMIC` (active) lists.
4. **Diff.** Read current membership (paginated), then compute
   `to_add = desired − current` and `to_remove = current − desired`.
5. **Apply.** One or more `PUT /crm/v3/lists/{listId}/memberships/add-and-remove`
   calls (chunked), each wrapped in a tenacity retry with jittered exponential
   backoff that honors `429 Retry-After`. `DRY_RUN=true` logs the diff and stops
   here.
6. **Audit.** Append one row per run to `AUDIT_TABLE` (counts, status, a hash of
   the query) for an at-a-glance history.

## Questions to answer

- Which MotherDuck `QUERY` defines the audience, and does it output an `email`
  column (or set `EMAIL_COLUMN`)?
- Which **static** HubSpot list receives the membership? Create a dedicated
  `MANUAL` list and use its list ID.
- Which HubSpot credential and scopes? A Service Key or private app token with
  `crm.lists.read`, `crm.lists.write`, `crm.objects.contacts.read`,
  `crm.objects.contacts.write`.
- What schedule (cron, UTC) matches how often the underlying data changes?

## Caveats

- **Static lists only.** Membership writes work on `MANUAL`/`SNAPSHOT` lists;
  `DYNAMIC` (active) lists are rule-maintained by HubSpot and the Flight will
  stop with a clear error if pointed at one.
- **Unmatched emails are skipped, not created.** An email with no contact record
  can't be added to a list. The run still succeeds and logs a sample; switch the
  resolve step to a batch upsert if you want contacts created.
- **Members are resolved by email.** Duplicate or recently-changed emails depend
  on HubSpot's indexing; a contact created seconds earlier may not resolve yet.
- **Rate limits.** The client retries `429`/`5xx` with backoff and honors
  `Retry-After`, but a very large audience still consumes daily API quota.
- **`MEMBERSHIP_CHUNK_SIZE` default (1000) is conservative.** Tune it against
  your account's documented limits if you sync large lists.

## What you'll adjust

No code edits are required. Everything is read from Flight config/env, plus a
MotherDuck Flights secret named `hubspot` that holds the HubSpot token (a
credential, so it must be a secret, never config).

| Knob | Default | Purpose |
|---|---|---|
| `QUERY` | (required) | MotherDuck SQL whose result drives the list. Must output the email column. |
| `HUBSPOT_LIST_ID` | (required) | Target static (`MANUAL`/`SNAPSHOT`) list ID to reconcile. |
| `EMAIL_COLUMN` | `email` | Name of the column in the query result holding emails. |
| `OBJECT_TYPE_ID` | `0-1` | HubSpot list object type (`0-1` = contacts). |
| `OBJECT_NAME` | `contacts` | CRM object path used for the batch read. |
| `ID_PROPERTY` | `email` | Property used to resolve query rows to records. |
| `BATCH_READ_SIZE` | `100` | Emails per batch-read call (HubSpot caps this at 100). |
| `MEMBERSHIP_CHUNK_SIZE` | `1000` | Record IDs per membership write call. |
| `MAX_RETRIES` | `5` | Retry attempts per HTTP operation. |
| `RETRY_BASE_SECONDS` | `2` | Exponential-backoff multiplier (seconds). |
| `DRY_RUN` | `false` | `true` computes and logs the diff without changing the list. |
| `AUDIT_TABLE` | `hubspot_list_sync.main.flight_tracker` | Ledger table (created if absent); `""` to skip. |
| `hubspot` **secret** | (required) | `TYPE flights` secret with param `ACCESS_TOKEN` (Service Key or private app token). |

The secret injects its param as `HUBSPOT_ACCESS_TOKEN`. The Flight reads that at
runtime; for a local run you can instead export `HUBSPOT_PRIVATE_APP_TOKEN`.

## Run it

You need a MotherDuck account and token, plus a HubSpot token and an existing
Hubspot static list. For a safe first pass, use `DRY_RUN=true` to see the diff without
touching the list.

```bash
export MOTHERDUCK_TOKEN=your_token_here
export HUBSPOT_ACCESS_TOKEN=your_service_key_or_pat   # or HUBSPOT_PRIVATE_APP_TOKEN
QUERY="SELECT email FROM my_db.main.audience" \
HUBSPOT_LIST_ID=12345 \
DRY_RUN=true \
  uv run --with-requirements requirements.txt flight.py
```

This runs the query, resolves emails to contact IDs, reads current membership,
and logs `desired / current / add / remove / unmatched`. Drop `DRY_RUN` (or set
it to `false`) to apply the diff and write an audit row.

### Deploy as a Flight

First store the HubSpot token as a **Flights secret** named `hubspot` (UI:
[Settings > Secrets](https://app.motherduck.com/settings/secrets), type
**Flights**, param `ACCESS_TOKEN`). Or via SQL from a write-enabled connection
(read-only connections reject `CREATE SECRET`):

```sql
CREATE SECRET hubspot IN motherduck (
  TYPE flights,
  PARAMS MAP { 'ACCESS_TOKEN': 'your_service_key_or_pat' }
);
```

To avoid putting the literal token in SQL or shell history, run that statement
from the **duckdb CLI** with the token in an env var — `getenv()` resolves
client-side there:

```sql
CREATE SECRET hubspot IN motherduck (
  TYPE flights,
  PARAMS MAP { 'ACCESS_TOKEN': getenv('HUBSPOT_PRIVATE_APP_TOKEN') }
);
```

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments), passing:

- `name`: a Flight name, for example `hubspot-list-sync`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `flight_secret_names`: `["hubspot"]` so `HUBSPOT_ACCESS_TOKEN` is injected
- `config`: at least `QUERY` and `HUBSPOT_LIST_ID`, plus any other knobs above.
  The token stays in the `hubspot` secret, never in config.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Create without a schedule, run once with `MD_RUN_FLIGHT(flight_id := ...)` (the
id is returned by `MD_CREATE_FLIGHT` and listed by `MD_FLIGHTS()`), and confirm
the list membership matches the query and `AUDIT_TABLE` has a new row. Decide a
schedule with the user before adding one.

## Security

- **Token in a secret, never config or SQL.** The HubSpot token comes from a
  `TYPE flights` secret and is read at runtime as `HUBSPOT_ACCESS_TOKEN`. The code
  only ever places it on the HTTP `Authorization` header — it is never logged.
- **Keep the literal token out of history.** Prefer the duckdb-CLI `getenv()`
  form above (or the Settings UI) so the raw token is not typed into SQL text or
  shell history.
- **Dedicated static list.** Point the Flight at a purpose-built `MANUAL` list.
- **Validated audit target.** `AUDIT_TABLE` is checked as plain SQL identifiers
  before it is interpolated into `CREATE`/`INSERT` (not parameterizable).
- **Least privilege.** Scope the Service Key / private app token to exactly the
  four `crm.lists.*` / `crm.objects.contacts.*` scopes the Flight uses.

## Learn more

- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- HubSpot Lists v3 API: [Lists API guide](https://developers.hubspot.com/docs/api-reference/crm-lists-v3/guide)
  and [add/remove memberships](https://developers.hubspot.com/docs/api-reference/crm-lists-v3/memberships/put-crm-v3-lists-listId-memberships-add-and-remove).
- HubSpot Service Keys (recommended credential for data integrations):
  [docs](https://developers.hubspot.com/blog/hubspot-service-keys-the-right-api-credential-for-data-integrations).
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
- Files: [`flight.py`](flight.py) (the Flight source), [`requirements.txt`](requirements.txt)
  (`duckdb`, `httpx` for the HubSpot API, and `tenacity` for retry/backoff).
