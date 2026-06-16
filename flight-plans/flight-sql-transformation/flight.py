"""MotherDuck Flight: run a set of SQL transformations as a parallel DAG.

Feed it ``CREATE TABLE | VIEW | MACRO ... AS ...`` statements in any order. Each
one is parsed with sqlglot to discover the object it *produces* and the objects
it *reads*; a statement that reads an object another statement produces depends
on it. The resulting DAG is executed concurrently: every statement waits for
*all* of its upstreams, independent statements run in parallel up to a
thread-pool limit, and each statement is retried with exponential backoff before
a failure skips everything downstream.

sqlglot does the parsing so dependency discovery needs no live database. Most
builtins (``avg``, ``read_csv``, …) parse as typed nodes, while a call to a
function sqlglot does not model — which a user macro always is — parses as
``exp.Anonymous``. A reference only becomes an edge if it matches an object
another statement produces, so the rare unmodeled builtin is harmless unless a
macro in the set shares its exact name.

The file is self-contained so it can be uploaded as a flight, while every piece
of logic stays importable for the test suite.
"""

from __future__ import annotations

import logging
import os
import random
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

import duckdb
import sqlglot
from sqlglot import exp

log = logging.getLogger("sql_dag")
DIALECT = "duckdb"


# ===========================================================================
# The SQL Statements to execute. Edit this function to use your own SQL!
# ===========================================================================
def sql_statements(database: str) -> list[str]:
    """One example chain over MotherDuck's public ``sample_data.nyc.taxi``.
    Replace these statements with your own!
    It exercises a table, a view, a scalar macro, a diamond (two summaries built
    off one view and joined back together), and a dependency on a table this
    Flight does not create, ``sample_data.nyc.taxi``
    """
    q = f"{_ident(database)}.{_ident('main')}"
    return [
        # Intentionally putting the first step last to test the reordering
        f"""
        CREATE OR REPLACE VIEW {q}.clean_trips AS
        SELECT payment_type, fare_amount, tip_amount
        FROM {q}.raw_trips
        WHERE fare_amount > 0
        """,
        f"""
        CREATE OR REPLACE MACRO {q}.tip_pct(tip, fare) AS
        tip / NULLIF(fare, 0)
        """,
        # Diamond: fare_by_payment and tips_by_payment both read the view...
        f"""
        CREATE OR REPLACE TABLE {q}.fare_by_payment AS
        SELECT payment_type, count(*) AS trips, avg(fare_amount) AS avg_fare
        FROM {q}.clean_trips
        GROUP BY payment_type
        """,
        f"""
        CREATE OR REPLACE TABLE {q}.tips_by_payment AS
        SELECT payment_type, avg({q}.tip_pct(tip_amount, fare_amount)) AS avg_tip_pct
        FROM {q}.clean_trips
        GROUP BY payment_type
        """,
        # ...and payment_overview joins both branches back together.
        f"""
        CREATE OR REPLACE TABLE {q}.payment_overview AS
        SELECT f.payment_type, f.trips, f.avg_fare, t.avg_tip_pct
        FROM {q}.fare_by_payment AS f
        JOIN {q}.tips_by_payment AS t USING (payment_type)
        """,
        f"""
        CREATE OR REPLACE TABLE {q}.raw_trips AS
        SELECT payment_type, fare_amount, tip_amount, trip_distance
        FROM sample_data.nyc.taxi          -- external: not created by this Flight
        LIMIT 200000
        """,
    ]


def _ident(name: str) -> str:
    """Quote a SQL identifier so a config/env value cannot break out of its
    syntactic position. A ``"`` inside the name is doubled (the SQL escape), so
    a value like ``evil"; DROP DATABASE x; --`` becomes one inert string token
    rather than executable SQL.

    Double-quoting makes the name case-sensitive: DuckDB folds *unquoted*
    identifiers to lowercase, but a quoted identifier is used verbatim. The
    default ``sql_dag_sqlglot`` is already lowercase, so default behavior is
    unchanged.
    """
    return '"' + name.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class TransformError(Exception):
    """Base class for problems found while analyzing the transformation set."""


class StatementError(TransformError):
    """A statement is not a CREATE ... AS that this runner can parse."""


class DuplicateTargetError(TransformError):
    """Two statements create the same object."""


class AmbiguousReferenceError(TransformError):
    """A reference matches more than one produced object."""


class CycleError(TransformError):
    """The dependency graph contains a cycle."""


# ---------------------------------------------------------------------------
# Object names
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ObjectName:
    """A possibly-qualified SQL object name. Comparison is case-insensitive,
    mirroring DuckDB's identifier folding."""

    catalog: str = ""
    schema: str = ""
    name: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.catalog.lower(), self.schema.lower(), self.name.lower())

    def __str__(self) -> str:
        return ".".join(p for p in (self.catalog, self.schema, self.name) if p)


def _matches(ref: ObjectName, target: ObjectName) -> bool:
    """True if a reference can point at ``target``. Names must match; an absent
    catalog/schema on *either* side is a wildcard, so a bare ``orders`` and a
    qualified ``mydb.main.orders`` match each other."""
    return (
        ref.name.lower() == target.name.lower()
        and _part_matches(ref.schema, target.schema)
        and _part_matches(ref.catalog, target.catalog)
    )


def _part_matches(a: str, b: str) -> bool:
    return not a or not b or a.lower() == b.lower()


# ---------------------------------------------------------------------------
# Static analysis: one CREATE statement -> a Transformation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Transformation:
    sql: str
    target: ObjectName
    kind: str  # table | view | macro
    table_refs: tuple[ObjectName, ...]
    function_refs: tuple[ObjectName, ...]


_KIND = {"TABLE": "table", "VIEW": "view", "MACRO": "macro"}


def _dotted_parts(node: exp.Expression) -> list[str]:
    """Flatten a dotted identifier (``db.s`` -> ``['db', 's']``)."""
    if isinstance(node, exp.Dot):
        return _dotted_parts(node.this) + _dotted_parts(node.expression)
    return [node.name]


def _function_ref(anon: exp.Anonymous) -> ObjectName:
    """Recover a (possibly qualified) macro reference from a function call.

    A bare ``f(...)`` is a plain Anonymous; a qualified scalar call ``db.s.f(...)``
    wraps it in Dot nodes that carry the qualifier; a table-macro call
    ``db.s.f()`` in FROM hangs it under a Table that carries the qualifier.
    """
    parent = anon.parent
    if isinstance(parent, exp.Table):  # table macro used in FROM
        return ObjectName(parent.catalog, parent.db, anon.name)
    if isinstance(parent, exp.Dot) and parent.expression is anon:  # qualified scalar call
        prefix = _dotted_parts(parent.this)[-2:]  # at most catalog.schema
        catalog = prefix[-2] if len(prefix) == 2 else ""
        return ObjectName(catalog, prefix[-1], anon.name)
    return ObjectName(name=anon.name)


def get_target_name_and_kind(create: exp.Create) -> tuple[ObjectName, str]:
    """Resolve the object a CREATE statement produces and its kind (table, view, macro)."""
    kind = _KIND.get((create.args.get("kind") or "").upper())
    if kind is None:
        raise StatementError(f"unsupported CREATE kind: {create.args.get('kind')!r}")

    this = create.this
    if isinstance(this, exp.UserDefinedFunction):  # macro: name carried as a Table
        this = this.this
    if isinstance(this, exp.Schema):  # CREATE TABLE t(cols) AS ...
        this = this.this
    if not isinstance(this, exp.Table):
        # Note that sqlglot stores the object as an exp.Table for tables, views, and macros
        raise StatementError(f"cannot determine target name in: {create.sql(dialect=DIALECT)!r}")
    return ObjectName(this.catalog, this.db, this.name), kind


def parse(sql: str) -> Transformation:
    """Parse one CREATE statement into a Transformation with its references."""
    sql = sql.strip().rstrip(";").strip()
    try:
        tree = sqlglot.parse_one(sql, dialect=DIALECT)
    except sqlglot.errors.SqlglotError as exc:
        raise StatementError(f"cannot parse statement: {exc}") from exc
    if not isinstance(tree, exp.Create):
        raise StatementError(f"not a CREATE statement: {sql!r}")

    target, kind = get_target_name_and_kind(tree)
    if tree.args.get("expression") is None:  # a CREATE ... (cols) with no AS query
        raise StatementError(f"CREATE {kind} has no AS query to parse: {sql!r}")

    tables = _table_refs(tree, target)
    # An unmodeled function call (exp.Anonymous) is a candidate macro reference.
    funcs = [_function_ref(a) for a in tree.find_all(exp.Anonymous) if a.name]
    return Transformation(sql, target, kind, _dedup(tables), _dedup(funcs))


def _table_refs(tree: exp.Expression, target: ObjectName) -> list[ObjectName]:
    """Table-like references in a statement body, dropping the target itself,
    function-call wrappers (``read_csv(...)``), and CTE-local names."""
    cte_names = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
    refs = []
    for node in tree.find_all(exp.Table):
        ref = ObjectName(node.catalog, node.db, node.name)
        if not ref.name or ref.key == target.key:
            continue
        if not ref.schema and not ref.catalog and ref.name.lower() in cte_names:
            continue  # CTE-local name, not a real dependency
        refs.append(ref)
    return refs


def _dedup(refs: Iterable[ObjectName]) -> tuple[ObjectName, ...]:
    seen, out = set(), []
    for ref in refs:
        if ref.key not in seen:
            seen.add(ref.key)
            out.append(ref)
    return tuple(out)


# ---------------------------------------------------------------------------
# DAG construction
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Dag:
    nodes: tuple[Transformation, ...]
    upstream_nodes: dict[int, frozenset[int]]  # node id -> direct upstream node ids

    def find_downstream(self) -> dict[int, set[int]]:
        """Reverse of ``upstream_nodes``: each node id -> the nodes that *directly* depend
        on it (1-hop downstream, i.e. must run after it)."""
        rev: dict[int, set[int]] = {i: set() for i in range(len(self.nodes))}
        for node, upstreams in self.upstream_nodes.items():
            for up in upstreams:
                rev[up].add(node)
        return rev

    def topological_levels(self) -> list[list[int]]:
        """Group nodes into waves; every node in a wave can run in parallel.
        Raises CycleError if the graph is not acyclic."""
        remaining = {i: set(d) for i, d in self.upstream_nodes.items()}
        levels: list[list[int]] = []
        while remaining:
            wave = sorted(i for i, d in remaining.items() if not d)
            if not wave:
                raise CycleError(sorted(str(self.nodes[i].target) for i in remaining))
            levels.append(wave)
            for i in wave:
                del remaining[i]
            for ups in remaining.values():
                ups.difference_update(wave)
        return levels


def build_dag(statements: Sequence[str]) -> Dag:
    """Parse statements, resolve dependencies, and return an acyclic DAG."""
    nodes = tuple(parse(s) for s in statements)

    by_key: dict[tuple, int] = {}
    for i, node in enumerate(nodes):
        if node.target.key in by_key:
            raise DuplicateTargetError(f"{node.target} is created by more than one statement")
        by_key[node.target.key] = i

    relations = [i for i, n in enumerate(nodes) if n.kind in ("table", "view")]
    macros = [i for i, n in enumerate(nodes) if n.kind == "macro"]

    upstream_nodes: dict[int, frozenset[int]] = {}
    for i, node in enumerate(nodes):
        upstream_nodes[i] = find_direct_upstream(i, node, nodes, relations, macros)

    dag = Dag(nodes, upstream_nodes)
    dag.topological_levels()  # Not using the levels, just validating no cycles exist
    return dag


def find_direct_upstream(
    node_id: int,
    node: Transformation,
    nodes: tuple[Transformation, ...],
    relations: list[int],
    macros: list[int],
) -> frozenset[int]:
    """Producer node ids this node depends on.
    The node's own id is dropped to prevent a self-reference."""
    found: set[int] = set()
    for ref in node.table_refs:
        producer = _resolve(ref, nodes, relations)
        if producer is not None:
            found.add(producer)
    for ref in node.function_refs:
        producer = _resolve(ref, nodes, macros)
        if producer is not None:
            found.add(producer)
    found.discard(node_id)
    return frozenset(found)


def _resolve(
    ref: ObjectName, nodes: tuple[Transformation, ...], candidates: list[int]
) -> int | None:
    """Return the producing node id for a reference, or None if it is external."""
    hits = [i for i in candidates if _matches(ref, nodes[i].target)]
    if len(hits) > 1:
        names = ", ".join(str(nodes[i].target) for i in hits)
        raise AmbiguousReferenceError(f"reference {ref} matches multiple objects: {names}")
    return hits[0] if hits else None


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
@dataclass
class RetryConfig:
    attempts: int = 4
    base_delay: float = 1.0
    factor: float = 2.0
    max_delay: float = 30.0
    jitter: float = 0.25  # jitter as a fraction of the delay
    sleep: Callable[[float], None] = time.sleep
    rng: Callable[[], float] = random.random

    def delay_for(self, attempt: int) -> float:
        delay = self.base_delay * self.factor ** (attempt - 1)
        if self.jitter:
            delay += delay * self.jitter * self.rng()
        return min(self.max_delay, delay)  # cap applies after jitter


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------
@dataclass
class NodeResult:
    id: int
    target: str
    kind: str
    status: str  # succeeded | failed | skipped
    attempts: int
    duration: float
    error: str | None = None


@dataclass
class RunReport:
    results: list[NodeResult]

    def by_status(self, status: str) -> list[NodeResult]:
        return [r for r in self.results if r.status == status]

    @property
    def ok(self) -> bool:
        return all(r.status == "succeeded" for r in self.results)

    def summary(self) -> str:
        counts = {s: len(self.by_status(s)) for s in ("succeeded", "failed", "skipped")}
        return "  ".join(f"{k}={v}" for k, v in counts.items())


def _mark_skipped(
    failed: int,
    dag: Dag,
    downstream: dict[int, set[int]],
    results: dict[int, NodeResult],
    scheduled: set[int],
) -> None:
    """Mark every not-yet-resolved transitive dependent of a failed node skipped."""
    stack = list(downstream[failed])
    while stack:
        node = stack.pop()
        if node in results:
            continue
        results[node] = NodeResult(
            node, str(dag.nodes[node].target), dag.nodes[node].kind,
            "skipped", 0, 0.0, f"upstream {dag.nodes[failed].target} did not succeed",
        )
        scheduled.add(node)
        stack.extend(downstream[node])


# run_dag's own branching is within budget (radon CC 9); the C901 suppression is
# needed only because mccabe folds in the nested run_one/launch_ready closures it hosts.
def run_dag(  # noqa: C901
    dag: Dag,
    execute: Callable[[Transformation], None],
    *,
    max_workers: int = 4,
    retry: RetryConfig | None = None,
) -> RunReport:
    """Execute the DAG, honoring dependencies and running independent nodes
    concurrently. ``execute`` is called once per node and must be thread-safe;
    all shared bookkeeping is mutated only on this coordinator thread."""
    retry = retry or RetryConfig()
    pending = {i: set(d) for i, d in dag.upstream_nodes.items()}  # unfinished upstreams
    downstream = dag.find_downstream()
    results: dict[int, NodeResult] = {}
    scheduled: set[int] = set()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        in_flight: dict = {}

        def run_one(i: int) -> NodeResult:
            """Run one node on its worker thread, retrying with exponential backoff."""
            node = dag.nodes[i]
            start = time.monotonic()
            last_error: Exception | None = None
            for attempt in range(1, retry.attempts + 1):
                try:
                    execute(node)
                    return NodeResult(i, str(node.target), node.kind,
                                      "succeeded", attempt, time.monotonic() - start)
                except Exception as exc:  # SQL failures are opaque; retry them all
                    last_error = exc
                    if attempt >= retry.attempts:
                        break
                    delay = retry.delay_for(attempt)
                    log.warning("retry %s (attempt %d/%d) after error: %s — sleeping %.2fs",
                                node.target, attempt, retry.attempts, exc, delay)
                    retry.sleep(delay)
            return NodeResult(i, str(node.target), node.kind,
                              "failed", retry.attempts, time.monotonic() - start, str(last_error))

        def launch_ready() -> None:
            for i in range(len(dag.nodes)):
                if i not in scheduled and not pending[i]:
                    scheduled.add(i)
                    log.info("start %s (%s)", dag.nodes[i].target, dag.nodes[i].kind)
                    in_flight[pool.submit(run_one, i)] = i

        launch_ready()
        while in_flight:
            # After any node finishes, check for all other nodes that can start
            done, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
            for fut in done:
                i = in_flight.pop(fut)
                result = results[i] = fut.result()
                if result.status == "succeeded":
                    log.info(
                        "done  %s in %.2fs (%d attempt%s)",
                        result.target, result.duration, result.attempts,
                        "" if result.attempts == 1 else "s",
                    )
                    for down in downstream[i]:
                        pending[down].discard(i)
                else:
                    log.error("FAILED %s: %s", result.target, result.error)
                    _mark_skipped(i, dag, downstream, results, scheduled)
            launch_ready()

    return RunReport([results[i] for i in range(len(dag.nodes))])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    max_workers = int(os.environ.get("MAX_WORKERS", "4"))
    database = os.environ.get("TARGET_DATABASE", "sql_dag_sqlglot")

    log.info("workers=%d target=%s", max_workers, database)
    con = duckdb.connect("md:")  # MotherDuck production
    # The database name comes from config, so it is always quoted and escaped
    con.execute(f"CREATE DATABASE IF NOT EXISTS {_ident(database)}")

    dag = build_dag(sql_statements(database))
    for level, wave in enumerate(dag.topological_levels()):
        log.info("plan L%d: %s", level, [str(dag.nodes[i].target) for i in wave])

    def execute(node: Transformation) -> None:
        # Each node runs in its own thread so needs its own cursor
        con.cursor().execute(node.sql)

    retry = RetryConfig(
        attempts=int(os.environ.get("MAX_ATTEMPTS", "4")),
        base_delay=float(os.environ.get("RETRY_BASE_DELAY", "1.0")),
    )
    report = run_dag(dag, execute, max_workers=max_workers, retry=retry)
    log.info("run complete: %s", report.summary())
    for r in report.results:
        log.info("  %-9s %-45s %d attempt(s) %6.2fs", r.status, r.target, r.attempts, r.duration)

    if not report.ok:
        failed = [r.target for r in report.by_status("failed")]
        raise SystemExit(f"transformation run failed: {failed}")


if __name__ == "__main__":
    main()
