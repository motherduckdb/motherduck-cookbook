# Persistent context analysis Flight design

## Status

Design for a new `flight-persistent-context-analysis` Flight Plan.

## Problem

Analytics agents repeatedly inspect source tables, rediscover metric definitions,
and regenerate explanations even when most source data has not changed. This costs
model tokens and time. It also leaves each agent session without the conclusions,
evidence, and organization context gathered by earlier sessions.

The Flight will maintain period-specific analysis as persistent context in
MotherDuck. It will compute measurements with SQL, use an LLM only when an input
changed, and publish the resulting daily, weekly, and monthly analysis as Guides.
Organization members and their agents can then retrieve the same context.

## Goals

- Generate daily, weekly, and monthly e-commerce analysis.
- Reuse an existing analysis when its evidence and context have not changed.
- Recompute a completed period after late-arriving sales data or new context.
- Enrich sales analysis with public weather and RSS feed entries.
- Include organization events such as outages, campaigns, and staffing shortages.
- Preserve the SQL evidence and source versions behind each finding.
- Publish analysis through shallow, searchable Guide topics.
- Keep automated output separate from human-authored context.
- Run as a single-file Flight Plan that readers can adapt to their own tables.

## Non-goals

- Prove that an external event caused a sales change.
- Build a general semantic layer or metric-definition product.
- Let the model issue arbitrary warehouse queries.
- Copy full news articles into MotherDuck or a model prompt.
- Replace incident management, website monitoring, or transport alerting systems.
- Delete old Guides automatically in the first version.

## Public framing

Use "persistent context analysis" for the pattern and "incremental analysis" for
the update mechanism. Caching explains one benefit, but the artifact is durable,
versioned analysis rather than a temporary response cache.

The public title is "Build persistent analysis context with Flights and Guides."
The catalog ID and folder name are `flight-persistent-context-analysis`.

## User experience

A reader starts in demo mode. The Flight creates deterministic e-commerce demo
tables covering the previous 120 days. The data includes orders, order items,
refunds, products, and markets. A fixed seed produces the same rows for the same
date, which makes unchanged-run behavior reproducible.

The reader runs the Flight with an `ANALYSIS_AS_OF` date. The first run computes
the completed daily period and any completed weekly or monthly periods that need
work. Each period produces an analysis Guide. Running the same date again skips
the LLM because the input fingerprints match. Changing a demo revision or adding
an annotation makes only the affected period and its parent periods eligible for
regeneration.

For production use, the reader disables demo mode and replaces the source queries
and metric definitions with their e-commerce tables. The update and Guide logic
does not change.

## Guide organization

Guide topics are shallow discovery paths. Dates belong in Guide titles and
metadata, not topic names.

```text
persistent-analysis/ecommerce/
  definitions/
  annotations/
  daily/
  weekly/
  monthly/
  archive/daily/
  archive/weekly/
```

The Flight reads but never modifies Guides under `definitions` or `annotations`.
It owns Guides under `daily`, `weekly`, `monthly`, and `archive`.

Generated titles use stable period names:

- `Commerce analysis for 2026-09-10`
- `Commerce analysis for 2026-W37`
- `Commerce analysis for 2026-09`

Each generated Guide contains:

1. The period, generation time, input fingerprint, and source watermark.
2. A concise summary and ranked findings.
3. Deterministic metric values and comparisons.
4. Relevant public signals with dates, sources, and URLs.
5. Relevant organization annotations with Guide IDs and versions.
6. Caveats that distinguish correlation from causation.
7. Reproducible SQL that retrieves the underlying evidence rows.
8. References to source tables, the Flight, input Guides, and child-period Guides.

The Guide description states the period, scope, and freshness so an agent can
decide whether to load it without reading the full content.

Generated Guides default to `access = 'user'`. Setting
`GUIDE_ACCESS=organization` is an explicit production choice and requires an
organization admin. A stable service account should own organization Guides.

## Data model

The code organizes state around a `PeriodKey` with four fields:

- `grain`: `day`, `week`, or `month`
- `period_start`: inclusive date
- `period_end`: exclusive date
- `scope`: `all` in the first version

The first version does not create one Guide per market or product. Metrics inside
one period Guide carry those dimensions. This bounds Guide growth and gives the
company one canonical report for each period.

The Flight maintains four tables in a configurable state schema.

### `analysis_periods`

One row per `PeriodKey`. It stores the evidence fingerprint, Guide ID, Guide
version, status, source watermark, prompt version, last successful run time, and
last error. A unique constraint on the period fields prevents duplicate state.

### `metric_evidence`

One row per period, metric, and dimension set. Columns include the current value,
comparison value, absolute change, percentage change, sample size, and an evidence
label. A JSON column stores dimensions such as market, channel, or product
category. SQL computes every value before the model runs.

### `external_signals`

One row per normalized public signal. It stores the provider, provider ID, event
time range, market, title or weather label, source URL, compact attributes, raw
payload hash, and retrieval time. A provider ID and payload hash make ingestion
idempotent.

### `period_dependencies`

One row for each parent and child relationship. A week depends on its days. A
month depends on overlapping days and weeks used as narrative context. The Flight
uses these rows to invalidate parents after a completed child changes.

## Demo commerce data

Demo mode creates a small current Shopify-shaped model rather than relying on a
credentialed service or a frozen historical download. Its structure follows a
private MotherDuck dataset containing Shopify orders, line items, products,
variants, cancellations, refunds, currencies, and shipping countries.

- `demo_orders` has one row per order with date, synthetic customer key, market,
  source channel, amounts, currency, cancellation state, and financial status.
- `demo_order_items` has one row per order and synthetic product with category,
  variant, quantity, price, discount, and net revenue.
- `demo_refunds` has one row per refund event with order, date, quantity, and
  amount.
- `demo_products` maps synthetic duck merchandise to generic categories and
  variants.
- `demo_markets` stores coarse market names, time zones, latitude, and longitude.

The public fixture has no copied orders and no one-to-one mapping to source rows.
It excludes names, emails, addresses, phone numbers, IP addresses, checkout
tokens, Shopify IDs, SKUs, and exact transaction values. The generator keeps only
the useful structural relationships. It uses invented volumes and amounts,
synthetic keys, generic duck products, coarse markets, shifted dates, and a fixed
random seed. Generation parameters must be reviewed as public code before commit.

The generated data includes weekly seasonality, market differences,
product-category differences, refunds, cancellations, occasional bulk orders,
and a small number of documented anomalies. `DEMO_REVISION` changes a bounded set
of late-arriving rows so tests and readers can observe invalidation without
editing source code. The README labels every demo row and anomaly as synthetic.

The default metrics are net revenue, gross revenue, orders, average order value,
refund rate, cancellation rate, units per order, and returning-customer share.
The Flight computes totals plus breakdowns for market, source channel, and product
category. Configuration caps the number of breakdown rows sent to the model.

Comparisons follow fixed rules rather than model judgment. A daily report compares
with the previous day, the same weekday in the previous week, and the trailing
28-day baseline. A weekly report compares with the previous week and trailing
four-week baseline. A monthly report compares with the previous month and the
trailing 90-day baseline. The current daily report also receives the latest
completed weekly and monthly Guides as narrative context.

## Public signals

The Flight has two narrow public-signal adapters.

### Weather

The Open-Meteo Historical Weather API supplies daily temperature, precipitation,
snowfall, wind, and weather codes for each configured market coordinate. The
Flight stores the normalized daily values and the source request URL. Weather is
available for both backfills and current runs.

### RSS entries

The sample reads two configurable RSS 2.0 feeds:

- BBC News "Ducks" at
  `https://feeds.bbci.co.uk/news/topics/czednw5qgllt/rss.xml`
- Het Parool English-language Amsterdam news at
  `https://www.parool.nl/international/rss.xml`

The Flight stores the feed name, item GUID, title, short description when
present, publication time, source URL, and retrieval time. It does not fetch
article pages, images, or full article bodies. It preserves source attribution
and the canonical link. Deduplication uses the feed URL plus GUID, with the item
URL as a fallback.

RSS feeds expose a moving set of recent entries rather than a historical archive.
The Flight accumulates entries from each scheduled run. An initial historical
backfill can have weather without matching RSS context. Missing or unavailable
feeds produce an explicit caveat and do not fail the sales analysis.

Public text is untrusted data. The prompt wraps titles and descriptions in a
delimited data block and treats them only as claims reported by an external
publisher. Instructions inside an RSS entry are ignored. A generated finding
links to the source and uses language such as "may be related" rather than
claiming causation.

## Organization annotations

People add organization events as separate Guides under
`persistent-analysis/ecommerce/annotations`. One Guide can describe an incident,
campaign, launch, staffing constraint, tracking problem, or other event. The
recommended shape is:

```markdown
---
event_id: inc-142
start_at: 2026-09-10T09:12:00Z
end_at: 2026-09-10T10:04:00Z
scope: market=NL,channel=web
category: incident
source: INC-142
---

# Checkout outage

Checkout requests returned HTTP 503 during a production deployment.
```

The Flight lists visible annotation Guides, reads their current versions, and
parses the YAML front matter. It selects Guides whose stated time range overlaps
the analysis period. The model receives the annotation body plus its declared
fields, Guide ID, version, and access level. An annotation without valid front
matter is excluded and logged. The free-text body remains unstructured.

An annotation version change affects the evidence fingerprint. The Flight then
regenerates overlapping period Guides. The generated report cites the annotation
Guide and its version. The Flight never edits an annotation Guide.

## Analysis and rollup flow

The Flight runs these steps in order:

1. Validate configuration, credentials, source tables, and Guide availability.
2. Create the state schema and demo data when demo mode is enabled.
3. Determine completed daily, weekly, and monthly periods inside the reconciliation
   window.
4. Compute and store deterministic metric evidence for each candidate period.
5. Fetch and normalize missing public signals.
6. Load visible definitions and annotation Guides.
7. Build an `EvidenceBundle` for each period.
8. Hash the canonical bundle with the prompt version and source watermark.
9. Skip periods whose successful stored fingerprint matches.
10. Ask the model for a typed analysis only for changed periods.
11. Render the Guide Markdown deterministically from the typed analysis and the
    evidence bundle.
12. Create the Guide or append a version to the existing Guide.
13. Update the period state and dependency rows in one database transaction.
14. Move expired Guides into an archive topic when archive mode is enabled.

A daily run rechecks a configurable number of completed days. Seven days is the
default. This catches late orders and refunds. A changed day invalidates its week
and month. Weekly and monthly metrics always come from the commerce tables. Child
Guides supply prior interpretations and relevant narrative, not their numeric
truth.

## Model contract

The Flight reuses the repository's Pydantic AI and OpenRouter pattern. The model
receives a compact `EvidenceBundle`, the relevant prior Guide, definitions,
annotations, public signals, and the previous analysis when one exists.

The model returns a typed object with:

- a two or three sentence summary
- ranked findings
- referenced evidence labels
- referenced signal IDs
- referenced annotation Guide IDs
- caveats
- a statement that no material change occurred when appropriate

The renderer rejects unknown evidence, signal, or Guide IDs. It inserts metric
values and URLs from trusted records rather than copying them from model output.
This prevents invented citations and keeps formatting stable across models.

## Idempotency and failure handling

- Canonical sorting and JSON encoding make fingerprints repeatable.
- `MERGE` operations deduplicate evidence and public signals.
- The period state is updated only after the Guide write succeeds.
- A retry reads the current Guide version before appending a new version.
- If another run changed that Guide, the Flight rebuilds the fingerprint or fails
  that period instead of overwriting concurrent work.
- One period failure does not block independent periods. A parent does not run
  when a required child or source metric failed.
- Public-signal timeouts produce an explicit missing-input caveat. They do not
  erase signals stored by a prior successful fetch.
- The Flight fails during preflight when Guide functions are unavailable.

## Retention

The first version archives rather than deletes generated Guides. Daily Guides
older than a configurable threshold move to `archive/daily` after their weekly
and monthly parents succeed. Weekly Guides can move to `archive/weekly` after a
configured number of months. Monthly Guides remain active by default.

Archive moves use `MD_UPDATE_GUIDE_METADATA`. `RETENTION_MODE=keep` is the safe
default. Automatic deletion is outside the first version because Guide references
would need coordinated cleanup.

## Security and governance

- The Flight uses parameterized queries for values and validates all configured
  identifiers before interpolating them into SQL.
- The model cannot execute SQL or write Guides directly.
- Public RSS titles and descriptions are untrusted text and remain inside
  delimited prompt data.
- The Flight stores no article body and preserves the publisher link for
  attribution.
- Feed use must follow each publisher's RSS terms and attribution requirements.
- Organization publication requires an admin-authorized Flight identity.
- Source-table and state-schema permissions should follow least privilege.
- Generated reports can reveal commercial data. Their access must match the
  source data's intended audience.
- A stable service account should own shared output Guides so ownership does not
  leave with an employee account.

## Repository artifacts

The implementation adds one template folder under `flight-plans/`:

```text
flight-plans/flight-persistent-context-analysis/
  README.md
  flight.py
  requirements.txt
```

`flight.py` remains a single deployable file. The domain skill, typed models,
demo generator, signal adapters, Guide operations, and orchestration live in that
file because Flight Plan templates are single-file artifacts. Sections and small
functions keep the file navigable.

The README follows the cookbook structure. It explains the persistent context
pattern, the topic hierarchy, the demo, the production adaptation points, Guide
access, secrets, cost controls, external-source limits, and Flight deployment.
It references SQL functions or the UI outside the Caveats and Learn more sections.

No generated `catalog.json` or create-Flight SQL file is committed.

## Verification

Unit tests will import `flight.py` and cover period construction, metric
fingerprints, late-arrival invalidation, annotation-version invalidation,
dependency propagation, rendering, identifier validation, public-signal
normalization, and idempotent second runs. Network and model calls use recorded or
fake responses.

Repository checks will build and schema-validate the catalog. A local demo run
will use a temporary DuckDB database plus a fake Guide store to prove the full
period flow without changing a MotherDuck account.

Live validation requires an account where the Guide SQL functions are enabled.
The current production and staging CLI sessions returned `Catalog Error: Table
Function with name md_list_guides does not exist` on 2026-09-11. Before claiming
end-to-end MotherDuck support, run the Flight with user-scoped Guides in an
enabled environment, read the created Guides back, rerun unchanged, and then test
one late-data revision. Organization publication is a separate admin-permission
check.

## Success criteria

- The first demo run creates the expected daily, weekly, and monthly Guides.
- An unchanged rerun makes no model call and creates no Guide version.
- A changed day regenerates that day and only its affected parents.
- Weather, relevant RSS entries, and overlapping annotations appear with
  provenance.
- Reports distinguish correlation from causation.
- A changed annotation version invalidates only overlapping periods.
- Generated Guides can use organization access when the Flight identity is an
  admin.
- Repository catalog and test validation pass.
- Live Guide creation remains explicitly unverified until tested in an enabled
  MotherDuck environment.
