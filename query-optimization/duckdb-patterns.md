# Common DuckDB optimization patterns

This are common optimization patterns with DuckDB. After checking baseline query profiles
you can use these patterns to suggest experiments, it they look like a good fit. Don't
blindly suggest all patterns as an experiment. Test it against the same inputs and
validate its results using the [methodology](methodology.md).

## Columnar scans and pushdown

A sequential scan is normal for an analytical, columnar engine. A large scanned
row count alone is not evidence that an index is missing. Inspect the columns
read, row groups skipped, pushed filters, scan output cardinality, and the scan's
contribution to runtime before changing anything.

Frequently useful experiments reduce the width and number of rows entering
expensive work: project only required fields, filter before enrichment, and
aggregate at a narrower grain before joining dimensions. DuckDB already prunes
columns and pushes many predicates through subqueries, so merely moving text
earlier in the SQL may produce exactly the same plan.

Look for filters that remain above expensive scans, especially casts,
compatibility expressions, and predicates combining different tables or columns.
Try an equivalent native predicate or narrower intermediate input, then check
the new plan. Preserve time zones, boundary inclusivity, and NULL behavior.
An optional scan filter can be a pruning hint with the real filter above it;
do not infer the number of rows removed from its text alone.

## Join fanout and usable join keys

Compare join input and output cardinalities. Accidental many-to-many joins can
inflate a small final result into enormous intermediate work. Deduplicate or
pre-aggregate an input only when its intended multiplicity permits that change.
Check the downstream aggregate as well as the join output.

A large nested-loop operator deserves investigation. Disjunctions, expressions,
or wildcard NULL matching can prevent a useful equality join. Possible rewrites
include disjoint branches or an explicit keyed relation that preserves every
matching case. Prove that branches do not double-count rows satisfying multiple
conditions, and retain outer-join unmatched rows. NULL-as-wildcard behavior is
different from NULL-safe equality. A sentinel key needs proof that it cannot
collide with real data.

A nested loop against three lookup rows may be negligible, while the same
operator against two million-row inputs dominates a job. Prioritize measured
work, not the operator name. The expanded keyed input can also consume more
memory; inspect it inside the affected workload.

## Filter and aggregate before wide enrichment

General-purpose models often join many dimensions and carry columns that a
specific consumer never uses. When a profile shows wide intermediate rows,
expensive hash tables, or a large expansion followed by a selective filter,
try forming a narrow candidate set first and adding enrichment afterward.

Examples include selecting completed items before fee attribution, aggregating
facts before joining customer metadata, or deduplicating invoice candidates
before joining them to a large fact. Keep enough keys to preserve multiplicity
and deterministic selection. Reordering joins is not automatically beneficial:
the optimizer may already choose a good order, and different data layouts or
estimates can make an apparently smaller intermediate slower.

## CTE materialization, inlining, and removal

A repeatedly used CTE can benefit from materialization when it performs the
same expensive calculation for every consumer. Conversely, inlining can allow
different consumers to prune different columns and push different predicates.
Several narrow scans may be cheaper than writing and repeatedly scanning one
wide shared intermediate.

Inspect the physical plan before changing `MATERIALIZED` or `NOT MATERIALIZED`.
Count the actual work and projected fields, not the number of CTE references
in the SQL. Materialization is not an automatic remedy for repeated references.

Sometimes the useful refactor removes a CTE altogether: collapse a redundant
projection, move an equivalent window filter into `QUALIFY`, combine compatible
aggregations, or replace a layered correlated calculation with a narrow keyed
pipeline. Removing a CTE is valuable when it changes the physical work. Fewer
CTEs or shorter SQL alone do not establish an improvement. Preserve any reuse,
deduplication, and semantic boundary the original CTE provided.

## Table, view, and ephemeral model tradeoffs

For a dbt table, include the write in the profile. A model dominated by
`CREATE_TABLE_AS` may be spending most of its time storing a wide result rather
than transforming it. A view can remove that write, but every downstream
reference can recompute its query.

Consider the write/compute balance, consumer count, actual scan count per
consumer, pushed filters, and microbatch reuse. Even a single consumer may
reference the view dozens of times or execute it once per batch. A view whose
underlying query is almost a copy is a different proposition from a view that
repeats expensive joins and aggregates. Measure the consumer and the job.

Ephemeral models also remove persistent relations. Preserve public marts and
any externally consumed relation, even when an ephemeral rewrite is faster.
View results can observe newer inputs or reevaluate time-dependent expressions;
materialization changes must retain the workload's output contract.

## Narrow dbt dependency branches

An expensive canonical model can sit on the critical path for several narrow
consumers. Reconstruct its downstream dependencies and inspect what each
consumer actually needs. A purpose-built branch can filter earlier, carry fewer
columns, and avoid waiting for unrelated enrichment.

This can trade some duplicated computation for a shorter dependency path.
Materialize narrow common inputs where reuse pays, and allow independent
branches to progress concurrently. Keep the original canonical relation for
consumers that need it. Validate the branch outputs, attribution rules, and
their downstream tests, then measure the affected job. More branches alone do
not guarantee a shorter job.

## Sorts, windows, distincts, and deterministic selection

Global sorts, broad window partitions, and distinct aggregates can hold large
working sets. Inspect their input width, cardinality, partition skew, and spill.
Reduce the input before the blocking operator when semantics permit it. Avoid
unneeded intermediate ordering, especially ordering every column of a wide row.

Some latest-row windows can be expressed as grouped bounds or `arg_max` /
`arg_min`; some repeated calculations can share a narrow key spine. Preserve
tie-breaking, NULL handling, ordering, and range semantics. A simple aggregate
can choose a different row than the original ordered window on tied values.
Combining multiple windows is not necessarily cheaper than separate windows
with different physical plans.

## Top N per group with bounded aggregates

When a ranking window selects only a few rows per group, compare it with bounded
aggregates. `min(value, N)` and `max(value, N)` return lists of values;
`min_by(row, key, N)` / `max_by(row, key, N)` (also `arg_min` / `arg_max`)
select associated rows. Hash aggregation can avoid sorting every partition.
`UNNEST` turns the list into rows; `recursive := true` also expands a `STRUCT`
payload into columns.

For example, given non-null, unique event IDs within each account, this selects
up to three latest events per account. The composite key matches
`ORDER BY event_at DESC, event_id DESC`, after excluding null timestamps:

```sql
SELECT unnest(
    max_by(
        e,
        struct_pack(event_at := e.event_at, event_id := e.event_id),
        3
    ),
    recursive := true
)
FROM events e
WHERE e.event_at IS NOT NULL
GROUP BY e.account_id;
```

Use scalar `max_by` / `arg_max` for latest-row-per-group selection when only one
row is needed. Preserve the original ordering keys, tie-breakers, NULL policy,
duplicates, and output schema. This is not a general replacement for
`rank()` / `dense_rank()` filters that retain ties. Ordinary `arg_max` /
`arg_min` ignore NULL arguments or keys; a non-null `STRUCT` containing NULL
fields is not itself NULL. Match the [aggregate semantics](https://duckdb.org/docs/stable/sql/functions/aggregates.html)
to the original query rather than adding filters that change its results. Add
an outer `ORDER BY` if consumers require ordered output.

Many groups, large N, or wide payloads can erode the memory advantage. Project
only needed payload fields and inspect spill. When the group count approaches
the row count, keep the window approach as a candidate. Fewer DuckDB engine
threads can reduce aggregation memory pressure; test that separately from the
SQL rewrite. Engine threads are distinct from dbt's model-scheduling threads.

## Avoid unnecessary insertion-order preservation

When the workload does not depend on input row order, try disabling
`preserve_insertion_order`. DuckDB can then reorder results without an
`ORDER BY`, potentially reducing memory usage, especially for large CSV or
Parquet imports and exports. Treat this as a measured experiment, not a
guaranteed speedup. See [DuckDB workload tuning](https://duckdb.org/docs/stable/guides/performance/how_to_tune_workloads.html#the-preserve_insertion_order-option).

Capture the existing value before changing it in the isolated experiment runner:

```sql
SELECT current_setting('preserve_insertion_order');
SET preserve_insertion_order = false;
```

This is a [global DuckDB setting](https://duckdb.org/docs/stable/configuration/overview.html#global-configuration-options),
not a query-local toggle. Use a dedicated experiment process, record each
variant's effective value, and restore the previous value if reusing the
process. For dbt, apply it through the experiment profile's connection
configuration so every relevant adapter connection uses it. Leave the original
project's configuration unchanged.

Check for implicit ordering dependencies: `LIMIT`/`OFFSET`, `row_number() OVER ()`,
order-sensitive aggregates, and downstream consumers can produce different
results when input order changes. Preserve explicit `ORDER BY` clauses and use
deterministic tie-breakers wherever order is part of correctness. Validate row
contents and duplicates, not incidental output order, unless order is required.
See [DuckDB order preservation](https://duckdb.org/docs/stable/sql/dialect/order_preservation.html).
Compare runtime, memory, and spill evidence under the same workload conditions;
if writes change the physical layout, check downstream scans too.

## Physical ordering and row-group pruning

When filters repeatedly target a date or another selective scalar field,
ordering a materialized table by that field can improve pruning and compression.
DuckDB's zonemaps store per-column minimum and maximum values for row groups.
Clustering similar values narrows those ranges, allowing a filter to skip groups
whose ranges cannot match. Inspect scan filters and scanned rows to decide
whether this is relevant.

Compare the cost of creating that layout with savings for its actual consumers.
Sorting on dozens of dimensions, especially nested or large text values, may
cost much more than sorting on the useful pruning key. Removing all ordering
may also discard a valuable layout. A producer's faster write can make its
unchanged downstream scans slower.

Physical storage order does not promise query result order. Queries that require
an ordered result still need their own `ORDER BY`.

## JSON text and early typed extraction

Look for JSON payloads carried as strings through many models while downstream
queries repeatedly extract and cast the same fields. A JSON-typed value alone
does not turn frequently accessed fields into ordinary typed columns.

Extract commonly filtered, joined, grouped, and aggregated fields early in the
staging pipeline. Store identifiers, numeric values, booleans, dates, and
timestamps in their appropriate column types, and preserve the raw payload
where consumers still need it. Subsequent models can operate on those columns
without repeating parsing and casts. The [synthetic demo](demo/setup.sql)
illustrates this tradeoff for recurring consumers.

Choose fields based on real access patterns. Do not expand every possible JSON
path into a very wide table. For predictable nested structures, typed `STRUCT`
or `LIST` values can also avoid repeated text extraction. When text extraction
must remain, investigate extracting several paths or transforming the structure
once rather than parsing separately for each expression.

Preserve the differences between a missing key, JSON null, SQL NULL, malformed
input, and failed casts. Check numeric precision, timestamp zones, and mixed
field types. Include the extraction/materialization cost when measuring a full
pipeline; moving that work upstream is not free. Do not introduce `VARIANT`
as an optimization in this recipe.

## Compatibility macros and repeated setup

Migrated projects can emulate another warehouse's functions through macros.
Inspect their expanded SQL and profile cost before replacing them. Verified
native DuckDB expressions can avoid repeated casts, regular expressions, JSON
parsing, or larger relational constructs, but apparent equivalents may differ
on time zones, calendar intervals, overflow, rounding, and NULLs.

Also inspect hooks that recreate unchanged catalog macros or bootstrap objects
on every invocation. A verified one-time bootstrap can remove repeated setup
cost. Demonstrate a fresh-database bootstrap and document when it must be
rerun. Keep its saved time separate from improvements in model queries.

## Further reading

- [DuckDB workload tuning](https://duckdb.org/docs/stable/guides/performance/how_to_tune_workloads.html).
- [DuckDB CTE behavior](https://duckdb.org/docs/stable/sql/query_syntax/with.html).
- [DuckDB JSON transformations](https://duckdb.org/docs/stable/data/json/json_functions.html).
- [MotherDuck query performance](https://motherduck.com/docs/key-tasks/query-performance/).
