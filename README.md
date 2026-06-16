<img src="assets/cookbook-duck.png" align="right" width="200" alt="MotherDuck Cookbook — a chef duck cooking code" />

# MotherDuck cookbook

A cookbook of opinionated, ready-to-use examples for building with MotherDuck.
Each example is a self-contained folder with working code and a README that
reads like an agent skill: minimal front matter for discovery, plus a body that
tells an agent (and a human) exactly what to adapt, what to ask, and how to run
or deploy it.

Browse the rendered cookbook at **[motherduck.com/docs/cookbook](https://motherduck.com/docs/cookbook/)**.

The same source feeds several surfaces from one place: GitHub, the
[docs site](https://motherduck.com/docs/cookbook/), and an agent via the
MotherDuck MCP server.

There are two kinds of entry:

- **Examples** at the repo root are standalone projects: dbt projects, app and
  edge integrations, ingestion scripts, and UI walkthroughs.
- **Flight templates** live under [`flight-plans/`](flight-plans): reusable,
  single-file Flight programs (`type: template`) that an agent adapts to a user's
  situation and deploys. This directory is reserved for template-style Flight
  files.

## Quick start

Pull a single example into a new folder with the get-starter script (short name
works for both top-level examples and Flight Plans):

```bash
curl -fsSL https://get.motherduck.com | bash -s <name>
# e.g.
curl -fsSL https://get.motherduck.com | bash -s dbt-ingestion-s3
```

## Examples

### dbt and transformation

- [dbt-ingestion-s3](dbt-ingestion-s3) - Build Hacker News models from Parquet in S3 with dbt, run locally or on MotherDuck.
- [dbt-churn-prediction](dbt-churn-prediction) - Build churn feature and label tables with dbt, plus a Python model trained on top.
- [dbt-ducklake](dbt-ducklake) - Run TPC-DS dbt models on a DuckLake-backed database (`features: ducklake`).
- [dbt-duckdb-dwh-starter](dbt-duckdb-dwh-starter) - Build a MotherDuck warehouse with dbt-duckdb (Common Crawl + Hacker News) and deploy a Dive (`features: dives`).
- [dbt-ai-prompt](dbt-ai-prompt) - Extract structured data from review text with dbt and `prompt()`.
- [dbt-dual-execution](dbt-dual-execution) - Run dbt models across local DuckDB and MotherDuck.
- [dbt-local-ducklake](dbt-local-ducklake) - Run dbt on a local DuckLake catalog (`features: ducklake`).
- [dbt-metricflow](dbt-metricflow) - Define and query metrics with dbt MetricFlow.
- [sqlmesh-demo](sqlmesh-demo) - Transform stock data with SQLMesh and dlt.

### Ingestion and replication

- [python-ingestion](python-ingestion) - Ingest API data into MotherDuck with the Python client.
- [dlt-db-replication](dlt-db-replication) - Replicate PostgreSQL tables to MotherDuck with dlt.

### Apps, serverless, and edge

- [cloudflare-workers](cloudflare-workers) - Query MotherDuck from Cloudflare Workers via the Postgres endpoint (`features: pg_endpoint`).
- [cloudflare-workers-duckoffee](cloudflare-workers-duckoffee) - Full-stack Worker: world map, live chart, and Durable Object voting on a MotherDuck share (`features: pg_endpoint, shares`).
- [vercel-nextjs](vercel-nextjs) - Query MotherDuck from Vercel and Next.js API routes (`features: pg_endpoint`).
- [vercel-agent-analytics](vercel-agent-analytics) - Capture a Vercel log drain and classify AI-agent traffic in MotherDuck.
- [nodejs-motherduck](nodejs-motherduck) - Connect from Node.js with the DuckDB Neo driver, including a connection pool.
- [nba-box-scores](nba-box-scores) - Ingest NBA box scores with scheduled Flights and ship a Dive frontend over the data (`features: flights, dives`).

### Integrations and UI

- [postgres-demo](postgres-demo) - Bridge local Postgres and MotherDuck with pg_duckdb (`features: pg_duckdb`).
- [motherduck-grafana](motherduck-grafana) - Visualize MotherDuck data in Grafana.
- [motherduck-ui](motherduck-ui) - Clean and analyze a CSV in the MotherDuck UI.

## Flight templates

Reusable, single-file Flights under [`flight-plans/`](flight-plans)
(`type: template`) that an agent adapts and deploys. Deploy them with the Flight
SQL functions (`MD_CREATE_FLIGHT`, then `MD_RUN_FLIGHT`); each README lists the
config knobs to set. The runtime attaches a MotherDuck token automatically and
injects it as `MOTHERDUCK_TOKEN`.

- [flight-scheduled-s3-ingest](flight-plans/flight-scheduled-s3-ingest) - Refresh a MotherDuck table from Hive-partitioned S3 Parquet on a schedule, reading only the partition that changes.
- [flight-dlt-ingest](flight-plans/flight-dlt-ingest) - Run a dlt pipeline into MotherDuck on a schedule, with Parquet loader files and schema evolution.
- [flight-provision-user-databases](flight-plans/flight-provision-user-databases) - Admin Flight that provisions a per-user database and restricted share from a users control table, and revokes access for inactive users (`features: shares`).

## Anatomy of an example

Every example README starts with YAML front matter holding exactly seven keys:

```yaml
---
title: Build Hacker News Models From S3 With dbt
id: dbt-ingestion-s3            # must equal the folder name
description: >-
  One or two sentences on what it does, then a "Use when ..." clause so an
  agent can route to it.
type: example                   # example | template
category: ingestion             # one primary navigation category (see list below)
features: []                    # MotherDuck capabilities used; [] if none (see list below)
tags: [dbt]                     # curated lowercase slugs: significant third-party tools
---
```

- `type` is `example` (a concrete, worked instance) or `template` (a generic plan
  an agent parameterizes).
- `category` is one primary navigation bucket: `getting-started`, `ingestion`,
  `analytics`, `integrations`, `end-to-end`, or `automation`. Pick by primary
  intent: Flights are categorized by what they do, not by being Flights.
- `features` names the MotherDuck capabilities the code actually uses, from:
  `admin_api`, `dives`, `ducklake`, `flights`, `mcp`, `pg_duckdb`, `pg_endpoint`,
  `shares`, `wasm`. Note `pg_endpoint` (MotherDuck's Postgres wire endpoint) and
  `pg_duckdb` (the pg_duckdb extension) are different features.
- `tags` come from a curated list (`ALLOWED_TAGS` in `scripts/build-catalog.py`)
  of significant third-party tools, frameworks, languages, and platforms (for
  example `dbt`, `dlt`, `cloudflare`, `vercel`, `nextjs`, `pandas`,
  `node-postgres`). They are not for datasets, generic concepts (`sql`, `etl`),
  the DuckDB engine, redundant variants, or things already covered by `features`.
  Tags may be empty. Add a new tag only for a significant new tool.

The body follows a consistent, skimmable structure:

1. `# <title>` and one paragraph on what it is and the MotherDuck pattern it shows.
2. `## How it works` - the concrete pattern, architecture, or files that explain
   what the example does before readers start changing values.
3. `## Questions to answer` - the inputs needed before adapting the example.
4. `## Caveats` - footguns, limitations, and cases where the example is the
   wrong tool.
5. `## What you'll adjust` - a table of the real knobs found in the code, each
   with its purpose and options or an example value.
6. `## Run it` - exact commands for the project's runtime (plus a
   `### Deploy as a Flight` subsection for any flight-capable example).
7. `## Security` - credential handling, SQL safety, access-control notes, and
   other security-specific concerns when relevant.
8. `## Learn more` - progressive disclosure: links to extra in-folder files, and
   pointers to the MotherDuck MCP guides (`get_flight_guide`, `get_dive_guide`)
   and `ask_docs_question` instead of duplicating them.

## Authoring and validation

- `flight-plans/` is only for reusable, single-file Flight templates
  (`type: template`). Concrete examples live at the repo root, even when they can
  deploy as a Flight (`features: [flights]`).
- Validate front matter and build the catalog with:

  ```bash
  uv run scripts/build-catalog.py                 # validate only
  uv run scripts/build-catalog.py --output catalog.json   # write the catalog
  ```

  The catalog shape is defined by [`catalog.schema.json`](catalog.schema.json).
- On pushes to `main`, CI publishes `catalog.json` as a GitHub Release asset.
  Download the latest catalog from
  `https://github.com/motherduckdb/motherduck-cookbook/releases/latest/download/catalog.json`;
  earlier versions remain available on earlier catalog releases.

## Getting an example

Each example is self-contained. You can:

1. **Use the get-starter script** (recommended):
   ```bash
   curl -fsSL https://get.motherduck.com | bash -s dbt-ingestion-s3
   ```
   It uses git sparse checkout to fetch only that folder (resolving a template
   under `flight-plans/` automatically) and drops it into a clean folder without
   git history.

   **Testing from a PR branch**: set `BRANCH` and fetch the script from that
   branch (since `get.motherduck.com` always serves `main`):
   ```bash
   BRANCH=my-branch curl -fsSL https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/my-branch/scripts/get-starter.sh | bash -s dbt-ingestion-s3
   ```

2. **Clone the repo** and navigate to a folder:
   ```bash
   git clone https://github.com/motherduckdb/motherduck-cookbook.git
   cd motherduck-cookbook/dbt-ingestion-s3
   ```

3. **Copy a folder** to start your project:
   ```bash
   cp -r dbt-ingestion-s3 my-new-project
   ```

## Requirements

Most examples require:

- **MotherDuck account** - [sign up](https://motherduck.com) (free tier available)
- **MotherDuck token** - from [Settings -> Access Tokens](https://app.motherduck.com)
- Tooling specific to each example (dbt, Node.js, Docker, etc.)

See each example's README for its exact prerequisites and commands.
