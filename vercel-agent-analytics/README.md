---
title: Capture Vercel Log Drain Traffic and Classify AI Agents in MotherDuck
id: vercel-agent-analytics
description: >-
  A Vercel Function that receives Vercel log drain batches, verifies the HMAC
  signature, classifies AI crawlers and agents from the user agent and referer,
  and writes one bulk INSERT per batch into a native MotherDuck table.
  Use when you want to measure AI bot, agent, and AI-referred human traffic to a
  Vercel-hosted site and query it live from a Dive or any SQL tool, with no AWS or S3.
type: example
category: end-to-end
features: []
tags: [vercel, nodejs, typescript]
prompt: >-
  I want to capture my Vercel log drain, classify AI bot, agent, and AI-referred human
  traffic from the user agent and referer, and write it into MotherDuck to query live,
  with no AWS or S3. Help me adapt the "Capture Vercel Log Drain Traffic and Classify AI
  Agents in MotherDuck" recipe to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/vercel-agent-analytics
published_date: 2026-04-24
---

# Capture Vercel Log Drain Traffic and Classify AI Agents in MotherDuck

A single Vercel Function (`api/drain.ts`) is registered as a Vercel log drain endpoint. Vercel POSTs NDJSON batches of request logs to it; the function verifies the HMAC-SHA1 signature, parses each line, classifies it against the rules in `bots.yaml` (crawler, agent, or human-via-AI), drops static asset requests, and writes the whole batch in one `INSERT` to a native MotherDuck table. It connects directly with `@duckdb/node-api` using a MotherDuck token, and bootstraps the database, schema, table, and an `ai_requests` view on the first cold start. The MotherDuck pattern this shows: an edge collector that lands raw web traffic in a native table you can read live from a Dive or BI tool, with classification kept in code so you can reclassify history in SQL.

## Architecture

```
 Vercel app
     │
     │ NDJSON log drain (HMAC signed)
     ▼
 Vercel Function (api/drain)
     │  verify signature
     │  parse + classify UA / referer
     │  drop static assets
     │  one bulk INSERT per batch
     ▼
 MotherDuck  <MD_DESTINATION>.<MD_TABLE>
     │
     └──── Dive / BI tool reads the same table
```

Why this shape:

- **Log drain, not middleware**: captures every request at the edge without changing app code. Vercel handles batching and retries.
- **One batch, one round-trip**: each Vercel drain POST already carries roughly 100 to 1000 log lines. The function parses, classifies, and writes the whole batch in a single `INSERT`, with no cross-invocation buffering.
- **Static assets excluded**: image requests plus `.js`, `.css`, font files (`.woff`, `.woff2`, `.ttf`, `.otf`, `.eot`), and source maps are dropped before insert so the table stays focused on page and API traffic that matters for AI measurement.
- **Native MotherDuck table**: the simplest write path and the fastest reads. If you want open Parquet under the hood and snapshot isolation instead, see the DuckLake variant note in Caveats.
- **Classifier in code, rules in YAML**: `bots.yaml` is the source of truth for AI identification. Update it and redeploy; the raw payload is stored on every row so you can reclassify history in SQL.

## How it works

- `src/signature.ts`: HMAC-SHA1 verification of the raw body, constant-time comparison.
- `src/handler.ts`: NDJSON and JSON-array parsing, field extraction from Vercel's `proxy.*` log shape (with fallbacks to flat fields and `request.headers.*`), static-asset filtering, IP anonymization, and `BOTS_ONLY` filtering. A single malformed NDJSON line is skipped rather than failing the whole batch.
- `src/classify.ts` + `bots.yaml`: substring matching that loads the YAML rules, user agent first then referer, first match wins. The raw payload is stored on every row so you can reclassify history in SQL.
- `src/db.ts`: the `@duckdb/node-api` connection (`md:` + token), idempotent schema bootstrap, and the multi-row bulk `INSERT`. The connection is module-scoped so warm invocations reuse it. `HOME` and the extension directory are pinned to a writable temp path because Vercel's Node runtime can present `HOME` as empty.
- `api/drain.ts`: the Vercel Function entry point (`POST` only, `405` otherwise); `src/local-server.ts` is the equivalent dev harness on `:8787`.
- `sql/01_setup.sql`: reference DDL for the table and `ai_requests` view (the function runs the same statements automatically on cold start).
- `sql/02_dive_queries.sql`: starter tiles you can save as a MotherDuck Dive (see below).

The function returns a `200 ok <n> of <total>` body so you can see how many rows of each batch were written after `BOTS_ONLY` and static-asset filtering.

## The classifier

`bots.yaml` is the source of truth, evaluated top to bottom, first match wins. Three categories:

- `crawler`: background training and indexing bots not tied to a user prompt (`GPTBot`, `OAI-SearchBot`, `ClaudeBot`, `Claude-SearchBot`, `Google-Extended`, `GoogleOther`, `CCBot`, `Applebot-Extended`, `Bytespider`, `Meta-ExternalAgent`, `PerplexityBot`, ...).
- `agent`: on-demand fetchers driven by a user prompt (`ChatGPT-User`, `ChatGPT Agent`, `Operator`, `Claude-User`, `Perplexity-User`, `MistralAI-User`, `Gemini-Deep-Research`, `Devin`, ...).
- `human_via_ai`: matched on `referer` for `chatgpt.com`, `claude.ai`, `perplexity.ai`, `gemini.google.com`, `copilot.microsoft.com`, `phind.com`, `meta.ai`, `chat.mistral.ai`, and more.

The list is seeded from the community-maintained [ai-robots-txt](https://github.com/ai-robots-txt/ai.robots.txt) catalog plus vendor docs (OpenAI, Anthropic, Perplexity, Cloudflare, Dark Visitors). Edit the YAML and redeploy to update rules; no reingest is needed, because the raw user agent and referer are stored on every row. You can also reclassify historical rows in SQL against the `raw` JSON column.

## Build a Dive

Save `sql/02_dive_queries.sql` as a MotherDuck Dive. Each block is one tile:

- Live 5-minute AI request counter.
- Requests per minute by category, last 60 minutes.
- Top 20 AI agents by request count, last 24h (with 4xx and 5xx breakdown).
- Humans arriving via AI referers (ChatGPT, Claude, Perplexity, Gemini, ...).
- 404s hit by crawlers (content they wanted but could not find).
- Daily AI share of traffic, last 30 days.
- Top pages by AI category, last 24h.

The queries default to `agent_analytics.raw.vercel_request_logs`; if you changed `MD_DESTINATION` or `MD_TABLE`, update the identifiers in the file before saving it.

## When to turn on `BOTS_ONLY`

Start with `BOTS_ONLY=false` so you have a real baseline that includes human traffic: you can measure AI share of traffic, see which pages humans land on from AI UIs, and so on. Flip it to `true` once the table has grown large enough that dropping non-AI rows is worth it. Because the raw payload is not retained for dropped rows, you cannot recover human traffic after the fact, so keep the baseline long enough first.

## Questions to answer

- Which Vercel project's traffic should be captured, and is there permission to create a log drain on it?
- Target MotherDuck database and schema (`MD_DESTINATION`) and table name (`MD_TABLE`).
- All traffic for a baseline, or AI-only (`BOTS_ONLY`)? Start with all traffic, see "When to turn on BOTS_ONLY".
- Which AI crawlers, agents, and AI referers matter, so `bots.yaml` can be tuned.
- Should client IPs be anonymized (the default) or kept in full.
- Where the MotherDuck token and the shared drain secret will be stored as Vercel environment variables (never commit them to `vercel.json` or the repo).

## Caveats

- **Cold start**: the first invocation after idle pays a roughly 500 ms to 1 s MotherDuck extension load. Vercel Fluid Compute keeps functions warm well enough that this is rare in practice. For high-QPS sites, ping the function every few minutes or enable always-warm via Vercel's Fluid config.
- **`HOME` can be empty on Vercel**: MotherDuck needs a writable extension cache. `src/db.ts` pins `HOME` and `DUCKDB_EXTENSION_DIRECTORY` to a temp path; if you change that code, keep it writable or the connection fails silently on cold start.
- **Bundle size**: `@duckdb/node-api` ships a native binary. Fluid Compute gives you the headroom; classic Serverless Functions may hit the 50 MB zipped limit.
- **No appender on native MotherDuck tables (yet)**: the example uses a multi-row `INSERT` instead. With roughly 500 rows per batch that is one network round-trip per drain POST, which is plenty fast. If you need the appender path or open Parquet storage with snapshot isolation, build the DuckLake variant of this collector instead (run `ask_docs_question` for DuckLake).
- **At-least-once delivery**: on a 5xx response Vercel redelivers the batch, and the handler deliberately returns `503` on a failed write so Vercel retries. If you care about exact counts, dedupe on `event_id` in your queries.
- **Agentic browsers that spoof user agents**: the classifier only sees what the bot tells it. AI agents using plain Chromium UAs fall into the `null` bucket and are counted as human unless their referer matches.
- **`bots.yaml` must be bundled**: `vercel.json` sets `includeFiles: "bots.yaml"`. If you remove that, `src/classify.ts` throws "bots.yaml not found" at cold start and the function fails.
- **Don't sign or transform the body before the handler**: signature verification runs on the raw bytes. Any body rewrite breaks the HMAC check and every delivery returns `401`.
- **`INSERT` column order is load-bearing**: the value tuples in `src/db.ts` must match the `CREATE TABLE` order in `sql/01_setup.sql`. Adding a column means editing both, in the same order.
- **`MD_DESTINATION` parsing is strict**: it must be exactly `<database>.<schema>` with two non-empty parts, or the function throws at cold start. A bare database name or a three-part name is rejected.

## What you'll adjust

| Setting | Purpose | Options / example |
|---|---|---|
| `MOTHERDUCK_TOKEN` (env, required) | MotherDuck access token used by the DuckDB Node API connection | a token from the MotherDuck UI |
| `VERCEL_DRAIN_SECRET` (env, required) | HMAC secret to verify drain deliveries; paste the same value into the Vercel log drain config | any high-entropy string, e.g. `dev-secret` for local testing |
| `MD_DESTINATION` (env, optional) | Target database and schema in `<database>.<schema>` form | default `agent_analytics.raw`; e.g. `web_analytics.ingest` |
| `MD_TABLE` (env, optional) | Target table name | default `vercel_request_logs` |
| `MD_DATABASE` / `MD_SCHEMA` (env, optional) | Legacy split form of the destination, used only when `MD_DESTINATION` is unset | defaults `agent_analytics` / `raw` |
| `BOTS_ONLY` (env, optional) | When `true`, drop rows with no classifier match so only AI traffic is stored | default `false`; set `true` once you only care about AI traffic |
| `bots.yaml` (`user_agent_patterns`, `referer_patterns`) | Source of truth for AI classification; substring match on user agent then referer | add or edit `pattern` / `name` / `category` rows; categories are `crawler`, `agent`, `human_via_ai` |
| `IGNORED_PATH_EXTENSIONS` + `/_next/image` check (`src/handler.ts`) | Static asset paths dropped before insert | extend the extension set or path checks to filter more routes |
| `anonymizeIp` (`src/handler.ts`) | Zeroes the last IPv4 octet before insert | remove or change if you need full IPs or IPv6 handling |
| `maxDuration` / `includeFiles` (`vercel.json`) | Function timeout and which non-code files get bundled | `maxDuration: 30`; `includeFiles: "bots.yaml"` |
| `PORT` (env, local only) | Port for the local dev harness `src/local-server.ts` | default `8787` |

## Run it

Prerequisites: a MotherDuck account and access token, a Vercel project with log drain creation rights, and Node 20+.

Local dev (no deploy):

```bash
npm install
export VERCEL_DRAIN_SECRET=dev-secret
export MOTHERDUCK_TOKEN=...
npm run dev            # starts src/local-server.ts on :8787
./scripts/test-local.sh   # signs and POSTs scripts/sample-payload.ndjson
```

Type check:

```bash
npm run typecheck
```

Deploy the function to Vercel:

```bash
npm install
vercel                 # deploy; prints https://<project>.vercel.app/api/drain
```

Then in the Vercel project settings:

1. Set `MOTHERDUCK_TOKEN` and `VERCEL_DRAIN_SECRET` (plus any optional knobs above) as environment variables.
2. Under Settings : Log Drains : Add, create an `NDJSON` drain pointing at the `/api/drain` URL, using the same custom secret as `VERCEL_DRAIN_SECRET`.

The function creates the database, schema, table, and `ai_requests` view on the first request, so there is no manual SQL step. Verify against the deployed endpoint:

```bash
./scripts/test-local.sh https://<project>.vercel.app/api/drain
```

Then query MotherDuck (adjust to your configured destination and table):

```sql
SELECT * FROM agent_analytics.raw.vercel_request_logs
ORDER BY event_ts DESC LIMIT 20;
```

## Security

- **HMAC verification is mandatory.** `src/signature.ts` recomputes HMAC-SHA1 of the raw body with `VERCEL_DRAIN_SECRET` and compares it with the `x-vercel-signature` header using a constant-time `timingSafeEqual`. A missing or mismatched signature returns `401` and nothing is written. The secret in the Vercel drain config must match `VERCEL_DRAIN_SECRET` exactly, otherwise every delivery is rejected.
- **Verification happens on the raw body before parsing.** Sign the unmodified body bytes; the local test script does this with `openssl dgst -sha1 -hmac`. Any middleware that re-serializes the body before it reaches the handler will break the signature.
- **The collector builds SQL by string concatenation, not bound parameters.** String values are escaped via `sqlStr` (doubling single quotes) and identifiers via `quoteIdent` (doubling double quotes), and `MD_DESTINATION` is validated to be exactly `<database>.<schema>`. This is the trust boundary that keeps log content from breaking out of the `INSERT`. If you add columns or new value types, route them through `sqlStr` / `sqlTs` / `quoteIdent`, do not interpolate raw strings.
- **Do not put secrets in `vercel.json` or commit them.** `MOTHERDUCK_TOKEN` and `VERCEL_DRAIN_SECRET` are read from the environment only.
- **Client IPs are anonymized by default.** `anonymizeIp` zeroes the last IPv4 octet (`203.0.113.42` becomes `203.0.113.0`) before insert. IPv6 and non-IPv4 strings pass through unchanged, so adjust the function if you need IPv6 handling or full IPs for a legitimate reason.

## Files

- [`api/drain.ts`](api/drain.ts) - the Vercel Function entry point: reads the raw POST body, pulls the `x-vercel-signature` header, hands off to `handleDrain`, returns `405` for non-POST.
- [`src/`](src/) - the collector logic in five TypeScript modules: `handler.ts` (NDJSON parse, field extraction, static-asset and `BOTS_ONLY` filtering, IP anonymization), `signature.ts` (constant-time HMAC-SHA1 verification of the raw body), `classify.ts` (substring matching against `bots.yaml`), `db.ts` (the `@duckdb/node-api` connection, schema bootstrap, bulk `INSERT`), and `local-server.ts` (the local dev harness on `:8787`).
- [`bots.yaml`](bots.yaml) - the AI classification rules, the source of truth: `user_agent_patterns` and `referer_patterns` with `pattern` / `name` / `category` (`crawler`, `agent`, `human_via_ai`), evaluated top to bottom, first match wins.
- [`sql/01_setup.sql`](sql/01_setup.sql) - reference DDL for the table and the `ai_requests` view; the function runs the same statements automatically on cold start.
- [`sql/02_dive_queries.sql`](sql/02_dive_queries.sql) - starter tiles you can save as a MotherDuck Dive (AI request counters, top agents, AI-referred humans, crawler 404s, daily AI share).
- [`scripts/test-local.sh`](scripts/test-local.sh) - signs `sample-payload.ndjson` with `VERCEL_DRAIN_SECRET` and POSTs it to the local or deployed endpoint, the same way Vercel signs a real delivery.
- [`scripts/sample-payload.ndjson`](scripts/sample-payload.ndjson) - eight example Vercel log lines (crawlers, agents, AI referers, a human) used by the test script.
- [`vercel.json`](vercel.json) - the Vercel Function config: `maxDuration: 30` and `includeFiles: "bots.yaml"` so the classifier rules get bundled.
- [`package.json`](package.json) - dependencies (`@duckdb/node-api`, `yaml`) and scripts (`dev`, `typecheck`); `package-lock.json` pins the lockfile.
- [`tsconfig.json`](tsconfig.json) - strict TypeScript config (ES2022, ESNext modules) covering `api/` and `src/`.

## Learn more

- For building a dashboard on top of this table, save `sql/02_dive_queries.sql` as a Dive; run the `get_dive_guide` MCP tool for Dive authoring, sharing, and embedding.
- For deeper MotherDuck or DuckDB questions (connection options, native tables, JSON columns, DuckLake storage), use the `ask_docs_question` MCP tool or the MotherDuck docs.
