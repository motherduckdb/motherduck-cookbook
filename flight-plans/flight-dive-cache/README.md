---
title: Precache a Dive's Data on a Schedule
id: flight-dive-cache
description: >-
  A Flight that takes an existing Dive (title, id, or URL), precomputes the data
  its queries read into cache tables on a schedule, and publishes a cached copy of
  the Dive that reads those tables. Use when a Dive is slow because its queries
  scan large or slow sources.
type: template
category: analytics
features: [flights, dives, shares]
tags: []
prompt: >-
  I have a slow MotherDuck Dive and want a fast, cached copy of it that refreshes
  on a schedule. Help me adapt the "Precache a Dive's Data on a Schedule" Flight
  recipe to my own Dive and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-dive-cache
published_date: 2026-10-05
---

# Precache a Dive's Data on a Schedule

Point this Flight at a Dive. Each run refreshes a cache and publishes
`<your Dive> (cached)`, a copy of the Dive that loads from the cache instead of
the original sources.

## How it works

The Flight parses every SQL string in the Dive with DuckDB's parser. It saves the
result of each fixed query as a table, and for queries that use `${...}` parameters
it copies the tables they read. The copy of the Dive reads those tables through a
share of the cache database.

## Questions to answer

- Which Dive? Its title, id, or URL.
- How often should the cache refresh? Daily at 06:00 UTC (`0 6 * * *`) is a good default.
- Who will view the cached Dive? Choose `ORGANIZATION`, `UNRESTRICTED` (public data only), or `NONE` (just you).

## What you'll adjust

| Config | Default | Purpose |
|---|---|---|
| `DIVE` | (required) | Dive title, id, or URL. |
| `SHARE_ACCESS` | `ORGANIZATION` | Access level for the cache share: `ORGANIZATION`, `UNRESTRICTED`, or `NONE`. |
| `CACHE_DATABASE` | `dive_cache` | Database (and share) that holds the cache. |
| `CACHE_SCHEMA` | Dive title, lowercased | Schema for this Dive's cache tables. |
| `CACHED_DIVE_TITLE` | `<title> (cached)` | Title of the published copy. |
| `MIRROR_TABLES` | `true` | Copy the tables read by `${...}` queries. If `false`, those queries stay live. |

## Run it

```bash
export MOTHERDUCK_TOKEN=your_token_here
DIVE="My slow Dive" uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

```sql
SET VARIABLE src = (SELECT content FROM read_text('flight.py'));
SET VARIABLE req = (SELECT content FROM read_text('requirements.txt'));
SELECT flight_id FROM MD_CREATE_FLIGHT(
  name := 'dive_cache_my_dive',
  source_code := getvariable('src'),
  requirements_txt := getvariable('req'),
  schedule_cron := '0 6 * * *',
  config := MAP {'DIVE': 'My slow Dive', 'SHARE_ACCESS': 'ORGANIZATION'}
);
SELECT * FROM MD_RUN_FLIGHT(flight_id := '<flight_id>'::UUID);
```

Check the run with `MD_GET_FLIGHT_LOGS(flight_id := ..., run_number := 1)`, then
open the cached Dive.

## Caveats

- The cached data is only as fresh as the last run, including queries that use `current_date`.
- Queries the Flight can't parse or run stay live, and the log counts them.
- Edit the original Dive, not the copy: each run overwrites the copy.

## Security

The Flight runs the Dive's SQL with your token, so only point it at Dives you
trust. Pick `SHARE_ACCESS` for the most sensitive table the Dive reads.

## Learn more

- [`flight.py`](flight.py) and [`requirements.txt`](requirements.txt).
- Flights: the MotherDuck MCP `get_flight_guide` tool. Dives: `get_dive_guide`.
