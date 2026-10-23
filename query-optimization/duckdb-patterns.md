# Common DuckDB optimization patterns

Choose experiments from baseline profiles, not SQL appearance. Apply only fitting
patterns; validate against identical inputs using the [methodology](methodology.md).

## Columnar scans and pushdown

- Sequential scans are normal, not proof of a missing index. Inspect projected
  columns, skipped row groups, pushed filters, cardinality, and runtime.
- Narrow/filter inputs before expensive work; DuckDB may already push/prune, so
  moving SQL text alone may change nothing.
- Investigate filters above scans: casts, compatibility expressions, cross-table
  predicates. Try native equivalents; preserve zones, boundaries, and NULLs.
  Optional scan filters may be pruning hints, not the final filtering operator.

## Join fanout and keys

- Compare input/output cardinalities and downstream aggregates. Deduplicate or
  pre-aggregate only when multiplicity permits.
- Expensive nested loops may follow disjunctions, expressions, or NULL-wildcard
  joins. Try disjoint branches/keyed relations without overlap or lost outer rows.
  NULL wildcards differ from NULL-safe equality; sentinels must not collide.
- Prioritize measured work, not operator names. Expanded keys can increase memory.

## Narrow before enrichment

For wide intermediates, large hash tables, or fanout followed by filtering,
form a narrow candidate set first; enrich later. Preserve keys, multiplicity,
and deterministic selection. Smaller SQL intermediates need not improve the
optimizer's existing join order.

## CTE materialization, inlining, removal

- Materialize costly shared computation; inline when consumers benefit from
  different projections/predicates. Several narrow scans may beat one wide
  materialized intermediate.
- Inspect physical work and width before `MATERIALIZED`/`NOT MATERIALIZED`;
  reference count alone is insufficient.
- Remove redundant CTE layers via collapsed projections, `QUALIFY`, compatible
  aggregates, or narrow keyed pipelines. Preserve reuse/deduplication boundaries;
  fewer CTEs alone is not an optimization.

## Table, view, and ephemeral models

Profile table writes: `CREATE_TABLE_AS` may dominate. Views/ephemeral models avoid
writes but may recompute per reference or microbatch, even for one consumer.
Compare actual scans, consumer count, pushdown, and write/recompute cost across
the job. Preserve public marts and external contracts; views can expose newer
data or reevaluate time-dependent expressions.

## Narrow dbt branches

Build purpose-specific branches when canonical models block narrow consumers.
Filter/project early; materialize shared narrow inputs where reuse pays.
Balance duplicated work against shorter dependencies; preserve canonical/public
outputs, validate attribution/downstream tests, and measure the affected job.

## Sorts, windows, distincts

Inspect input width/cardinality, partition skew, and spill. Narrow before blocking
operators; remove unnecessary ordering of wide rows. Try shared key spines,
grouped bounds, or `arg_max`/`arg_min` for latest-row windows. Preserve ties,
NULLs, ordering, and range frames; merging windows need not reduce work.

## Top N per group

Compare ranking windows with bounded hash aggregates that avoid partition sorts:

- `min(value, N)` / `max(value, N)`: lists of values.
- `min_by(row, key, N)` / `max_by(row, key, N)` (aliases `arg_min` / `arg_max`):
  associated rows. Scalar forms select one row.
- `UNNEST`: list to rows; `recursive := true` also expands STRUCT columns.

For non-null unique event IDs per account, this matches selecting at most three
rows ordered by `event_at DESC, event_id DESC`, excluding null timestamps:

```sql
SELECT unnest(
    max_by(e, struct_pack(event_at := e.event_at, event_id := e.event_id), 3),
    recursive := true
)
FROM events e
WHERE e.event_at IS NOT NULL
GROUP BY e.account_id;
```

Preserve full sort keys, NULL policy, duplicates, schema, and tie semantics:
this does not generally replace `rank()`/`dense_rank()` filters retaining ties.
`arg_min`/`arg_max` skip NULL arguments/keys; a STRUCT with NULL fields is not
itself NULL. Do not add filters that change results. Use outer `ORDER BY` if needed.

Many groups, large N, or wide payloads erode memory gains. Narrow payloads;
consider windows when groups approach row count. Fewer DuckDB engine threads
may reduce aggregation memory/spill; test separately. Engine threads are not
dbt's model-scheduling threads.

## Insertion-order preservation

If input order is unnecessary, test lower-memory reads/writes, especially large
CSV/Parquet imports/exports, with:

```sql
SELECT current_setting('preserve_insertion_order');
SET preserve_insertion_order = false;
```

This is global, not query-local: use an isolated process, record each variant's
effective value, and restore before reuse. For dbt, configure experiment-profile
connections, not original sources. Check LIMIT/OFFSET, empty-OVER row numbering,
order-sensitive aggregates, and consumers for implicit-order dependencies.
Preserve explicit ordering; validate contents/duplicates and downstream layouts.

## Physical ordering and row-group pruning

Sort materialized data on frequently filtered scalar/date keys. Zonemaps hold
per-column row-group min/max; clustering narrows ranges so filters skip groups.
Verify scanned rows and weigh sorting cost against downstream pruning/compression.
Avoid expensive all-column/nested/text sorts without evidence. Removing useful
ordering can accelerate writes but slow readers. Storage order is not an output
ordering guarantee: consumers still need `ORDER BY`.

## JSON strings to typed columns

Repeated JSON extraction/casting is a staging opportunity: extract frequently
filtered/joined/grouped fields early into properly typed columns; retain raw
payloads if needed. JSON type alone does not create typed fields. Expand only
used paths; predictable structures can use typed STRUCT/LIST. If extraction
remains, batch paths/transforms rather than repeatedly parsing.

Preserve missing key vs JSON null vs SQL NULL, malformed values, failed casts,
numeric precision, zones, and mixed types. Include extraction/materialization
cost in pipeline comparisons. Do not introduce `VARIANT`.
See the [typed-staging demo](demo/setup.sql).

## Compatibility macros and setup

Inspect expanded migration macros for repeated casts, regex, parsing, or joins.
Test native equivalents for zones, calendars, overflow, rounding, and NULLs.
Move repeated unchanged bootstrap/hooks to one-time setup only after validating
fresh-database behavior and documenting rerun conditions. Report setup savings
separately from query gains.
