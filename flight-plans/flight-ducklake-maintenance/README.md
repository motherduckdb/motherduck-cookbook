---
title: Keep DuckLake Reads Fast With Scheduled Maintenance
id: flight-ducklake-maintenance
description: >-
  A reusable Flight that keeps read performance high in a DuckLake by running the
  individual maintenance operations behind a checkpoint on a schedule, tuned to
  your ingest pattern. Use when a DuckLake accumulates small files, deleted rows,
  or stale snapshots and you want automatic compaction and cleanup; works for both
  MotherDuck-managed and bring-your-own-bucket DuckLakes.
type: template
category: automation
features: [flights, ducklake]
tags: []
prompt: >-
  My DuckLake is accumulating small files, deleted rows, and stale snapshots, and I want
  scheduled compaction and cleanup tuned to my ingest pattern. Help me adapt the "Keep
  DuckLake Reads Fast With Scheduled Maintenance" Flight recipe to my own data and use
  case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-ducklake-maintenance
published_date: 2026-06-15
---

# Keep DuckLake Reads Fast With Scheduled Maintenance

Keep your read performance high in a DuckLake automatically, customized to your
ingest workload pattern. Every write to a DuckLake leaves work behind: streaming
inserts pile up tiny Parquet files, deletes and updates leave rows that still have
to be scanned, and old snapshots keep data files alive long after anyone needs to
time-travel to them. Left alone, that drag shows up as slower queries. This Flight
runs the maintenance that clears it on a schedule you control, so reads stay fast
without anyone remembering to do it by hand.

For extra control, this template runs the six operations that make up a
checkpoint individually, in checkpoint order. Each one is exposed as a `ducklake_*`
function, and every threshold that controls them is a Flight config value.
Just set the parameters to match how you actually write and how long you want to time travel.

It works the same on a **MotherDuck-managed DuckLake and a bring-your-own-bucket
(BYOB) DuckLake**: the operations run through the catalog, so the only thing that
changes is which database you point it at. BYOB lakes are the common case for this
template. 

## How it works

`flight.py` connects to MotherDuck (`md:`) and runs a fixed sequence against one
DuckLake database named by config. The config values only change the inputs and
thresholds, not the steps:

1. **Apply catalog options** (only the ones you set). `target_file_size` and
   `rewrite_delete_threshold` are written with `CALL "<db>".set_option(...)`. These
   persist on the catalog, so they keep steering both this Flight and any
   background maintenance until you change them.
2. **`ducklake_flush_inlined_data`** — write rows that DuckLake inlined into the
   catalog (small inserts) out to Parquet so they can be compacted.
3. **`ducklake_expire_snapshots`** — mark snapshots older than `EXPIRE_OLDER_THAN`
   as expired so their exclusive files become eligible for cleanup.
4. **`ducklake_merge_adjacent_files`** — compact many small adjacent data files
   into fewer larger ones (toward `target_file_size`). This is the step that most
   directly speeds up reads after small-batch ingestion.
5. **`ducklake_rewrite_data_files`** — rewrite files whose deleted-row fraction
   exceeds `rewrite_delete_threshold`, dropping the tombstoned rows from the scan.
6. **`ducklake_cleanup_old_files`** — delete files that only expired snapshots
   still referenced (those older than `CLEANUP_OLDER_THAN`).
7. **`ducklake_delete_orphaned_files`** — delete data files in the lake's storage
   that the catalog no longer references at all (older than `ORPHAN_OLDER_THAN`).

Each step logs the rows it returned, so the run log doubles as a report of what
maintenance did (`merge_adjacent_files: 1 row(s)` with the table and file counts,
which snapshots expired, which files were removed). The order matters: flushing and
expiring first is what gives the merge, rewrite, and cleanup steps something to act
on, mirroring what a real checkpoint does.

## Questions to answer

- Which DuckLake should this maintain (`DUCKLAKE_DATABASE`)? Is it MotherDuck-managed
  or bring-your-own-bucket?
- How is the lake written, and how should that shape the knobs? Small-batch streaming
  favors `TARGET_FILE_SIZE` and frequent merges; delete/update-heavy tables favor a
  lower `REWRITE_DELETE_THRESHOLD`.
- How much time-travel history must stay queryable? That sets `EXPIRE_OLDER_THAN` and,
  with it, how soon `CLEANUP_OLDER_THAN` can reclaim storage.
- How often should maintenance run (cron)? Match it to ingest frequency, not the clock.
- For a first run against a real lake, do you want `DRY_RUN=true` so the destructive
  steps only report what they would remove?

## Caveats

- **Three steps delete data.** `expire_snapshots`, `cleanup_old_files`, and
  `delete_orphaned_files` are destructive. Expiring a snapshot ends the ability to
  time-travel to it; cleanup and orphan deletion remove the underlying files. Start
  with `DRY_RUN=true` and conservative `*_OLDER_THAN` windows, confirm the reported
  set looks right, then tighten.
- **`set_option` persists on the catalog.** `TARGET_FILE_SIZE` and
  `REWRITE_DELETE_THRESHOLD` are not per-run; once set they stay until changed. Leave
  them unset to keep the lake's existing values.
- **Managed DuckLakes already self-maintain.** MotherDuck runs background maintenance
  on managed lakes, so this template is most useful for BYOB lakes, lakes where you
  have turned background maintenance off, or when you want a specific cadence. Running
  it against a managed lake is safe but may find little to do.
- **`expire_snapshots` reads timestamp columns, so the Flight ships `pytz` and pins a
  timezone.** Its result includes `TIMESTAMP WITH TIME ZONE` columns; the DuckDB Python
  client needs `pytz` to read those, and the Flight runtime often has no system zone
  (DuckDB reports `Etc/Unknown`), so `flight.py` runs `SET TimeZone = 'UTC'`. Without
  both the step fails with an `UnknownTimeZoneError`.

## What you'll adjust

Every knob is a config/env value read at the top of `flight.py`. Set them as Flight
config, not by editing code.

| Config key | Default | Purpose |
|---|---|---|
| `DUCKLAKE_DATABASE` | (required) | The DuckLake (managed or BYOB) to maintain. Used as a quoted identifier. |
| `EXPIRE_OLDER_THAN` | `7 days` | Expire snapshots older than this interval. Sets your time-travel retention. |
| `CLEANUP_OLDER_THAN` | `7 days` | Remove files left behind by expired snapshots once they are older than this. |
| `ORPHAN_OLDER_THAN` | `7 days` | Remove unreferenced data files in storage older than this. |
| `TARGET_FILE_SIZE` | (unset) | e.g. `512MB`. Persisted catalog option steering `merge_adjacent_files`. Raise it for small-batch ingest. |
| `REWRITE_DELETE_THRESHOLD` | (unset) | e.g. `0.5`. Persisted catalog option; rewrite a file once this fraction of its rows are deleted. Lower it for delete-heavy tables. |
| `DRY_RUN` | `false` | `true` makes expire/cleanup/orphan report what they would remove without deleting. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Select a token on the Flight; never put it in config. |

## Run it

You need a MotherDuck account, an access token, and a DuckLake database to point at. 
A safe first pass is a dry run that only reports:

```bash
export MOTHERDUCK_TOKEN=your_token_here
DUCKLAKE_DATABASE=my_lake DRY_RUN=true uv run --with-requirements requirements.txt flight.py
```

That connects, applies any options you set, and prints what each step would do.
Drop `DRY_RUN` (or set it to `false`) to actually run maintenance, and add the
threshold knobs inline to tune a single run, for example
`DUCKLAKE_DATABASE=my_lake TARGET_FILE_SIZE=256MB REWRITE_DELETE_THRESHOLD=0.3 uv run --with-requirements requirements.txt flight.py`.

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is checked
in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `ducklake_maintenance`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: at least `DUCKLAKE_DATABASE`, plus any knobs from
  [What you'll adjust](#what-youll-adjust) you want to override (omit any you are
  keeping at default)

A MotherDuck token is attached to the Flight automatically and injected at run time
as `MOTHERDUCK_TOKEN`; no token argument is needed. Give that token write access to
the target lake.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and listed
by `MD_FLIGHTS()`) — using a `config` override of `DRY_RUN := 'true'` for the first
run is a safe way to see what it will touch — and confirm the log looks right. Once a
real run is green, add a schedule that matches your ingest cadence (a lake written all
day might run `0 * * * *` hourly; a nightly batch might run `0 7 * * *`) by updating
the Flight's `schedule_cron` with `MD_UPDATE_FLIGHT`. 

## Security

- **Least privilege.** The Flight's token needs write/maintenance access only to the
  lake it maintains; scope it accordingly rather than reusing a broad admin token.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- DuckLake maintenance internals (what each `ducklake_*` function does, the checkpoint
  sequence, `set_option` keys, managed vs. bring-your-own-bucket behavior): use the
  `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source) and
  [`requirements.txt`](requirements.txt) (`duckdb`, plus `pytz` so the client can read
  `expire_snapshots`' timestamp columns).
