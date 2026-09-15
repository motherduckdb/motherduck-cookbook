---
title: Build a Common Crawl Web Graph Time Series
id: flight-commoncrawl-web-graph
description: >-
  A reusable Flight that loads a fixed quarterly Common Crawl host-rank series,
  then verifies each platform's host count against the matching domain-rank file.
  Use when you need a reproducible, host-level time series for public hosting
  platforms without scanning overlapping web-graph releases.
type: template
category: analytics
features: [flights]
tags: [python]
prompt: >-
  I want a reproducible Common Crawl web-graph time series for a fixed set of
  hosted platforms, with host-level rows and a domain-file count check. Help me
  adapt the "Build a Common Crawl Web Graph Time Series" recipe to my own data
  and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-commoncrawl-web-graph
published_date: 2026-09-12
---

# Build a Common Crawl Web Graph Time Series

`flight.py` loads 16 pinned Common Crawl web-graph releases into MotherDuck. It
keeps host-level ranks for a fixed platform registry, then checks every retained
domain against Common Crawl's domain-rank `n_hosts` value before it records a
release as complete.

The default source uses `https://data.commoncrawl.org`. It needs no AWS
credentials. A full backfill reads about 103 GB of compressed public data, so
start with one release while you confirm the destination and platform list.

## How it works

The Flight stores four tables in `DESTINATION_DATABASE.DESTINATION_SCHEMA`.

| Table | Contents |
| --- | --- |
| `PLATFORMS_TABLE` | The frozen `(domain, reversed_domain, label, kind)` registry. |
| `HOSTS_TABLE` | Retained host ranks, keyed by release and reversed hostname. |
| `RUN_LEDGER_TABLE` | Releases that passed the domain-file count check. |
| `REGISTRY_STATE_TABLE` | The SHA-256 hash for the platform registry. |

`RELEASES` is a fixed 16-release registry. It does not discover releases with a
glob. `RELEASE_NAMES` can select a comma-separated subset of those names for a
small backfill, but it rejects any release outside the pinned list.

For each unfinished release, the Flight starts a transaction and reads the
five-column host file with an explicit schema. It derives `domain_rev` from the
first two labels of `host_rev`, then uses an equality join to
`platforms.reversed_domain`. The domain file uses its own explicit six-column
schema. A full outer comparison checks every retained host count against
`n_hosts`. The Flight rolls back the release if one or more domains disagree.
Only a clean check writes the completed-run ledger row.

On a rerun, the Flight checks the ledger before it constructs a source URL. A
completed release skips without reading either Common Crawl file. Before all
release work, the Flight compares the configured platform-registry hash and its
stored platform rows with the existing series. It stops on a mismatch rather
than mixing different platform definitions in one time series.

## Questions to answer

- Which MotherDuck database and schema should own this time series?
- Does the fixed platform registry answer the analysis question, or should a new
  destination hold a changed registry?
- Which releases should the first run backfill?
- What cron should check for a newly added, manually pinned release after the
  initial backfill?

## Caveats

- **Do not use a glob.** Recent web-graph releases are overlapping three-month
  windows. A glob would turn one quarterly series into a mix of repeated crawl
  windows.
- **The registry is intentionally frozen.** Add or remove platforms only in a
  new destination, unless you intentionally migrate and recompute the series.
- **The domain file checks the host extraction.** It does not replace it. The
  host file is needed to inspect real hostnames and ranks.
- **The source is large.** One release can take several minutes. Running all 16
  releases sequentially is deliberate because each release has its own
  transaction and verification step.
- **Use public HTTPS by default.** `SOURCE_BASE_URL` accepts an HTTPS directory.
  A private source needs separate access configuration and is outside this
  template.

## What you'll adjust

Set these values as Flight config. `flight.py` validates every value that
becomes a SQL identifier before it opens a connection.

| Config key | Default | Purpose |
| --- | --- | --- |
| `DESTINATION_DATABASE` | `commoncrawl` | Database that owns the series. |
| `DESTINATION_SCHEMA` | `main` | Schema for all four tables. |
| `PLATFORMS_TABLE` | `platforms` | Table that stores the frozen platform registry. |
| `HOSTS_TABLE` | `platform_hosts` | Table that stores retained host ranks. |
| `RUN_LEDGER_TABLE` | `completed_runs` | Ledger that makes completed releases idempotent. |
| `REGISTRY_STATE_TABLE` | `registry_state` | Table that records the registry hash. |
| `RELEASE_NAMES` | all 16 pinned releases | Comma-separated pinned release names to process. |
| `SOURCE_BASE_URL` | Common Crawl public HTTPS directory | Directory before the release name. |
| `MOTHERDUCK_TOKEN` | Flight-injected | Auth. Select a token on the Flight. Do not put it in config. |

## Run it

Install the pinned DuckDB client and select one pinned release for a first run:

```bash
export MOTHERDUCK_TOKEN=your_token_here
RELEASE_NAMES=cc-main-2026-apr-may-jun \
  uv run --with duckdb==1.5.5 flight.py
```

The first run creates the database, schema, tables, platform registry, and one
completed-run record. Run without `RELEASE_NAMES` to process every remaining
release. Repeating either command skips releases already present in the ledger.

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT`, supplying a name, the contents of
[`flight.py`](flight.py), and the contents of
[`requirements.txt`](requirements.txt). Set `max_runtime_sec` high enough for a
single web-graph release. The Flight runtime injects the selected
`MOTHERDUCK_TOKEN`; do not add an `access_token_name` argument.

Create the Flight without a schedule. Run one release with
`MD_RUN_FLIGHT(flight_id := ...)`, then inspect `completed_runs` and the
retained host counts. Add a schedule with `MD_UPDATE_FLIGHT` only after the
manual run completes. A schedule does not find releases on its own. Add a new
`Release` entry to the pinned registry before it can process a future release.

## Security

The destination database, schema, and table names enter DDL and cannot use query
parameters. The Flight accepts only plain SQL identifiers for those config
values. URLs, release names, dates, and registry hash values use query
parameters where DuckDB accepts them. The public HTTPS source does not need an
AWS key or a MotherDuck S3 secret.

## Learn more

- [Common Crawl web graph documentation](https://data.commoncrawl.org/projects/hyperlinkgraph/cc-main-2026-apr-may-jun/index.html)
- [Common Crawl blog analysis](https://motherduck.com/posts/querying-the-entire-internet-with-duckdb-and-common-crawl/)
- Use the MotherDuck MCP `get_flight_guide` tool for Flight SQL details.
- Use the MotherDuck MCP `ask_docs_question` tool for further Common Crawl or
  MotherDuck questions.
