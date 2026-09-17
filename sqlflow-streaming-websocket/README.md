---
title: Stream a WebSocket Firehose into MotherDuck with sqlflow
id: sqlflow-streaming-websocket
description: >-
  Streams the public Bluesky firehose into a MotherDuck table with sqlflow, a
  single-binary stream processor that runs SQL over each micro-batch. Use when
  you want a continuously running ingest from a WebSocket, MQTT-style feed, or
  webhook into MotherDuck without standing up Kafka or a cluster.
type: example
category: ingestion
features: []
tags: [docker, ingest]
prompt: >-
  I want to continuously stream events from a WebSocket feed into a MotherDuck
  table, transforming each batch with SQL, without running Kafka or a streaming
  cluster. Help me adapt the "Stream a WebSocket Firehose into MotherDuck with
  sqlflow" recipe to my own feed and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/sqlflow-streaming-websocket
published_date: 2026-09-08
---

# Stream a WebSocket Firehose into MotherDuck with sqlflow

This example keeps a MotherDuck table filled from a live WebSocket feed. It
connects to the public [Bluesky](https://bsky.app) firehose, flattens each post
into columns with SQL, and inserts the result into MotherDuck. The whole thing
is one container, one YAML file, and a MotherDuck token. There is no broker, no
scheduler, and no cluster.

[sqlflow](https://github.com/turbolytics/sql-flow) is an MIT-licensed stream
processor written in Go. It embeds DuckDB, so the transformation is ordinary
DuckDB SQL, and it reaches MotherDuck the same way any DuckDB client does, with
`ATTACH 'md:'`.

## How it works

A sqlflow pipeline has three parts, all declared in
[`pipeline.yml`](pipeline.yml).

**The source** is a WebSocket URI. sqlflow holds the connection open, reconnects
on its own when the feed drops, and accumulates messages until `batch_size` of
them have arrived.

**The handler** is SQL run against a table named `batch`, which holds the
current micro-batch. Because the handler statement is an `INSERT ... SELECT`
into the attached MotherDuck database, the transformation and the load are the
same statement:

```sql
INSERT INTO my_db.bluesky_posts
SELECT
  did,
  to_timestamp(time_us / 1000000)              AS event_time,
  commit ->> 'operation'                       AS operation,
  commit ->> 'collection'                      AS collection,
  CAST(commit AS JSON) ->> '$.record.text'     AS post_text,
  CAST(commit AS JSON) ->> '$.record.langs[0]' AS lang
FROM batch
WHERE kind = 'commit'
```

**The sink** is `noop`. The handler has already written the rows, so there is
nothing left to send anywhere.

Two `commands` run once at startup, before any message is read: the `ATTACH`
that connects to MotherDuck, and a `CREATE TABLE IF NOT EXISTS` so the first run
works on an empty account.

## Questions to answer

- **Which feed?** Any WebSocket that emits one JSON object per message works.
  The default is the Bluesky jetstream, which needs no credentials.
- **What shape are the messages?** You need to know which fields you want as
  columns. Run the pipeline once with `handlers.InferredMemBatch` and a
  `SELECT * FROM (DESCRIBE batch)` handler to print the inferred schema.
- **Which database and table?** Set `SQLFLOW_MD_DATABASE` and `SQLFLOW_MD_TABLE`,
  or accept `my_db.bluesky_posts`.
- **How fresh do rows need to be?** `batch_size` sets the trade. 500 rows on a
  feed producing 50 a second means a row waits about ten seconds.

## Caveats

- **Throughput here is the feed's, not the engine's.** A 2,000-event run
  measured 54.5 messages a second, which is how fast Bluesky published posts at
  the time. It is not a sqlflow benchmark. The same engine writing to MotherDuck
  from a Kafka source measures around 6,500 a second, where the public internet
  becomes the bottleneck rather than the source.
- **Inferred nested types vary between batches.** sqlflow infers the schema of
  `batch` from the JSON in it, so a deeply nested field present in one batch can
  be absent from the next. Reading nested values with a JSON path,
  `CAST(commit AS JSON) ->> '$.record.text'`, returns `NULL` for a missing path.
  Struct dot-notation fails to bind instead, which stops the pipeline. Prefer
  the JSON path for anything below the top level.
- **Delivery is at least once.** A WebSocket source has no offsets to replay, so
  a crash loses whatever was buffered rather than duplicating it. If you need
  every event, use a source that can be replayed, such as Kafka.
- **The table grows without bound.** This recipe appends. Add a retention job,
  or aggregate before inserting, if you keep it running.
- **Not every message becomes a row.** The `WHERE kind = 'commit'` filter drops
  the feed's identity and account events. A 2,000-event run inserted 1,962 rows.

## What you'll adjust

| Knob | Where | Purpose |
| --- | --- | --- |
| `SQLFLOW_WEBSOCKET_URI` | `.env` or `pipeline.yml` | The feed to consume. Any WebSocket emitting JSON per message. |
| `SQLFLOW_MD_DATABASE` | `.env` | MotherDuck database to attach. Default `my_db`. |
| `SQLFLOW_MD_TABLE` | `.env` | Target table. Default `bluesky_posts`. |
| `SQLFLOW_BATCH_SIZE` | `.env` | Rows per `INSERT`. Default 500. Raise for throughput, lower for freshness. |
| `CREATE TABLE` columns | `pipeline.yml`, `commands` | The target schema. Must match what the handler selects. |
| Handler `SELECT` | `pipeline.yml`, `handler.sql` | Which fields become columns, and how they are cast. |
| `WHERE` clause | `pipeline.yml`, `handler.sql` | Which messages become rows. |
| `MSGS` | `make run MSGS=5000` | How many events to consume before stopping. |

## Run it

You need Docker and a MotherDuck token. Every command below runs
`turbolytics/sql-flow:v1.1.0`. Pass `IMAGE` to use a different release.

Set your token:

```bash
cp .env.template .env
# then put your MotherDuck token in .env
```

Check the config before connecting to anything:

```bash
make validate
```

```
/conf/pipeline.yml: valid
```

Stream 2,000 live events and stop:

```bash
make run
```

```
INFO  Executing command step  {"name": "attach motherduck"}
INFO  Executing command step  {"name": "create the target table"}
INFO  initializing websocket source  {"uri": "wss://jetstream2.us-east.bsky.network/..."}
INFO  consumer loop starting
INFO  throughput  {"messages_consumed": 2000, "total_throughput_per_second": 54.53796092203534}
INFO  max messages consumed, stopping consumer loop
```

Verify in MotherDuck:

```sql
SELECT count(*) AS rows, count(DISTINCT did) AS authors, max(event_time) AS latest
FROM my_db.bluesky_posts;
```

```
┌───────┬─────────┬────────────────────────────┐
│ rows  │ authors │           latest           │
│ int64 │  int64  │         timestamp          │
├───────┼─────────┼────────────────────────────┤
│  1962 │    1756 │ 2026-09-08 17:03:01.076255 │
└───────┴─────────┴────────────────────────────┘
```

To keep it running instead, use `make stream` and stop it with Ctrl-C.

Remove the table when you are done:

```bash
make clean
```

## Security

- **The token is the only credential.** sqlflow reads `MOTHERDUCK_TOKEN` from
  its environment. `make run` passes it with `--env-file .env`, so it never
  appears in a command line or an image layer. Keep `.env` out of version
  control.
- **Scope the token.** A read/write token for one database is enough. This
  recipe writes to one table and reads nothing else.
- **The handler SQL is trusted, the feed is not.** Message content becomes
  column values, never SQL text, so a hostile payload cannot change the
  statement. It can still be large or malformed, so treat `post_text` as
  untrusted when you read it back.
- **The default feed is public.** Bluesky's jetstream carries public posts and
  needs no credentials. Point this at an internal feed and the WebSocket URI may
  itself carry a secret, so move it into `.env`.

## Learn more

- [sqlflow documentation](https://turbolytics.io/docs/sqlflow) covers the other
  sources (Kafka, webhooks), the other sinks, error policies, and Prometheus
  metrics.
- [MotherDuck sink tutorial](https://turbolytics.io/docs/sqlflow/tutorials/motherduck-sink)
  is the same pipeline with a Kafka source, including a throughput measurement.
- [Loading data into MotherDuck](https://motherduck.com/docs/key-tasks/loading-data-into-motherduck/)
  for the other ingestion paths.
- Ask `ask_docs_question` for MotherDuck specifics such as token scopes and
  database sharing.
