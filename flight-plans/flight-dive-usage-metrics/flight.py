import datetime
import json
import os
import re

import duckdb


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A candidate string is treated as SQL only if it starts with one of these
# keywords (after stripping leading whitespace/comments). This keeps non-SQL
# template literals (CSS, JSX text, prop strings) out of the parser.
SQL_START_RE = re.compile(r"^\s*(--[^\n]*\n\s*)*(SELECT|WITH|FROM)\b", re.IGNORECASE)

# Capture the contents of backtick template literals and of single/double quoted
# strings. Template literals carry almost all dive SQL; quoted strings catch the
# occasional one-line query. The capture groups hold the inner text only.
TEMPLATE_LITERAL_RE = re.compile(r"`((?:\\.|[^`\\])*)`", re.DOTALL)
QUOTED_STRING_RE = re.compile(r"""(?<![A-Za-z0-9_])(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')""")

# JS template interpolations (`${...}`) are not valid SQL. Replace each with a
# harmless placeholder literal so the surrounding SQL still parses. Nested braces
# inside an interpolation are rare in dive SQL, so a non-greedy single-level match
# is enough in practice; anything left unparseable is skipped and counted.
#
# An interpolation already wrapped in quotes (e.g. `'${orgId}'`) must become a
# single quoted literal, not a quoted-then-quoted-again string, so the
# quote-wrapped form is collapsed first; bare interpolations then become a quoted
# literal too.
QUOTED_INTERPOLATION_RE = re.compile(r"""(['"])\$\{[^{}]*\}\1""", re.DOTALL)
INTERPOLATION_RE = re.compile(r"\$\{[^{}]*\}", re.DOTALL)
INTERPOLATION_PLACEHOLDER = "'__param__'"

OBJECT_TYPES = ("database", "schema", "table", "column")

# SQL date/time (and a few other) keywords that the parser represents as
# COLUMN_REF nodes, e.g. CURRENT_DATE. They are not real columns, so they are
# skipped when collecting column references. A denylist (rather than an all-caps
# heuristic) avoids dropping legitimately upper-cased column names.
KEYWORD_COLUMNS = {
    "current_date", "current_time", "current_timestamp", "localtime",
    "localtimestamp", "now", "current_user", "session_user", "user", "excluded",
}

# Emit a progress line every this many dives so a long org-wide scan is not
# silent for minutes.
HEARTBEAT_EVERY = 200

# Rows per INSERT for the (potentially large) edge tables. A single multi-row
# INSERT is far faster than row-by-row, but a few-hundred-thousand-row VALUES
# list is unwieldy, so writes are chunked.
INSERT_BATCH = 1000


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code.
    target_database = validate_identifier("TARGET_DATABASE", env("TARGET_DATABASE", "dive_metrics"))
    target_schema = validate_identifier("TARGET_SCHEMA", env("TARGET_SCHEMA", "main"))
    metrics_table = validate_identifier("METRICS_TABLE", env("METRICS_TABLE", "dive_usage_metrics"))
    edges_table = validate_identifier("EDGES_TABLE", env("EDGES_TABLE", "dive_object_edges"))
    cooccurrence_table = validate_identifier("COOCCURRENCE_TABLE", env("COOCCURRENCE_TABLE", "dive_table_cooccurrence"))
    join_table = validate_identifier("JOIN_TABLE", env("JOIN_TABLE", "dive_join_edges"))
    include_org_shares = env_bool("INCLUDE_ORG_SHARES", True)
    dive_limit = env_int("DIVE_LIMIT", 0)  # 0 means no limit
    # When true, also build the relationship tables (dependency edges, table
    # co-occurrence, and mined join keys). Set false for just the usage metrics.
    build_relationships = env_bool("BUILD_RELATIONSHIPS", True)

    def fqn(table):
        return f"{target_database}.{target_schema}.{table}"

    metrics_fqn = fqn(metrics_table)
    edges_fqn = fqn(edges_table)
    cooccurrence_fqn = fqn(cooccurrence_table)
    join_fqn = fqn(join_table)

    con = duckdb.connect("md:")

    dives = list_dives(con, include_org_shares=include_org_shares, dive_limit=dive_limit)
    scope = "own + org-shared" if include_org_shares else "own"
    print(f"scanning {len(dives)} dive(s) ({scope}); relationships {'on' if build_relationships else 'off'}")

    # Usage aggregates keyed by (object_type, object_name): a set of dive ids (for
    # the distinct dive_count) and a running total reference_count.
    dive_ids = {}
    reference_counts = {}
    # Relationship aggregates.
    object_edges = []          # one (dive_id, dive_title, object_type, object_name) per reference
    cooccurrence_dives = {}    # (table_a, table_b) -> set(dive_id)
    cooccurrence_queries = {}  # (table_a, table_b) -> count of statements
    join_dives = {}            # (left_table, left_col, right_table, right_col) -> set(dive_id)
    join_queries = {}          # same key -> count of statements
    parsed = 0
    skipped = 0
    total = len(dives)

    for i, (dive_id, title) in enumerate(dives, start=1):
        try:
            source = get_dive_source(con, dive_id)
        except duckdb.Error as exc:
            skipped += 1
            print(f"skip dive {dive_id} ({title!r}): could not read source: {str(exc).splitlines()[0]}")
            continue

        candidates = extract_sql_candidates(source)
        # De-dupe object references per dive so a table referenced five times in
        # one dive counts once toward dive_count, while reference_count keeps the
        # raw total across all parsed statements.
        dive_objects = set()
        dive_parsed = 0
        for sql in candidates:
            ast = serialize_sql(con, sql)
            if ast is None:
                skipped += 1
                continue
            dive_parsed += 1
            for obj in objects_from_ast(ast):
                dive_objects.add(obj)
                reference_counts[obj] = reference_counts.get(obj, 0) + 1

            if build_relationships:
                tables, joins = analyze_statement(ast)
                for pair in table_pairs(tables):
                    cooccurrence_queries[pair] = cooccurrence_queries.get(pair, 0) + 1
                    cooccurrence_dives.setdefault(pair, set()).add(dive_id)
                for edge in joins:
                    join_queries[edge] = join_queries.get(edge, 0) + 1
                    join_dives.setdefault(edge, set()).add(dive_id)

        parsed += dive_parsed
        for key in dive_objects:
            dive_ids.setdefault(key, set()).add(dive_id)
            if build_relationships:
                object_edges.append((dive_id, title, key[0], key[1]))

        # Heartbeat so a long org-wide scan reports progress instead of going
        # silent for minutes; always print the final dive too.
        if i % HEARTBEAT_EVERY == 0 or i == total:
            print(f"processed {i}/{total} dive(s); {parsed} statement(s) parsed, {skipped} skipped so far")

    print(f"parsed {parsed} SQL statement(s); skipped {skipped} (unreadable / unparseable / not SQL)")

    metric_rows = [
        (object_type, object_name, len(ids), reference_counts.get((object_type, object_name), 0))
        for (object_type, object_name), ids in dive_ids.items()
    ]
    # Stable, readable order in the table: by type, then by descending dive_count.
    metric_rows.sort(key=lambda r: (OBJECT_TYPES.index(r[0]), -r[2], r[1]))

    # One timestamp for the whole run so every row in this batch shares a run_at,
    # which keeps the history-append model and the `_latest` views consistent.
    # Generated in Python (not SELECT now()) on purpose: reading a TIMESTAMPTZ back
    # from DuckDB into Python requires pytz, which the flight's duckdb wheel does
    # not bundle. Binding a Python datetime for the INSERT needs no such import.
    run_at = datetime.datetime.now(datetime.timezone.utc)

    write_metrics(con, target_database, target_schema, metrics_fqn, metric_rows, run_at)

    cooccurrence_rows = []
    join_rows = []
    if build_relationships:
        write_object_edges(con, edges_fqn, object_edges, run_at)

        cooccurrence_rows = [
            (a, b, len(cooccurrence_dives[(a, b)]), cooccurrence_queries[(a, b)])
            for (a, b) in cooccurrence_dives
        ]
        cooccurrence_rows.sort(key=lambda r: (-r[2], -r[3], r[0], r[1]))
        write_cooccurrence(con, cooccurrence_fqn, cooccurrence_rows, run_at)

        join_rows = [
            (lt, lc, rt, rc, len(join_dives[(lt, lc, rt, rc)]), join_queries[(lt, lc, rt, rc)])
            for (lt, lc, rt, rc) in join_dives
        ]
        join_rows.sort(key=lambda r: (-r[4], -r[5], r[0], r[2]))
        write_join_edges(con, join_fqn, join_rows, run_at)

    con.close()

    print(f"wrote {len(metric_rows)} metric row(s) to {metrics_fqn}")
    for object_type in OBJECT_TYPES:
        n = sum(1 for r in metric_rows if r[0] == object_type)
        print(f"  {object_type}: {n} distinct")
    if build_relationships:
        print(f"wrote {len(object_edges)} dive-object edge(s) to {edges_fqn}")
        print(f"wrote {len(cooccurrence_rows)} table co-occurrence pair(s) to {cooccurrence_fqn}")
        print(f"wrote {len(join_rows)} join-key edge(s) to {join_fqn}")


def list_dives(con, *, include_org_shares, dive_limit):
    # MD_LIST_DIVES accepts a named INCLUDE_ORG_SHARES argument (verified against
    # live MotherDuck): MD_LIST_DIVES(include_org_shares := true) returns every
    # dive shared in the organization, not just the caller's own. If a MotherDuck
    # build does not support the argument, fall back to the no-argument call so the
    # Flight still runs against the caller's own dives.
    limit_clause = f" LIMIT {int(dive_limit)}" if dive_limit and dive_limit > 0 else ""
    if include_org_shares:
        try:
            return con.execute(
                f"SELECT id::VARCHAR, title FROM MD_LIST_DIVES(include_org_shares := true)"
                f" ORDER BY title{limit_clause}"
            ).fetchall()
        except duckdb.Error as exc:
            print(f"INCLUDE_ORG_SHARES not supported by MD_LIST_DIVES ({str(exc).splitlines()[0]}); "
                  "falling back to the caller's own dives")
    return con.execute(
        f"SELECT id::VARCHAR, title FROM MD_LIST_DIVES() ORDER BY title{limit_clause}"
    ).fetchall()


def get_dive_source(con, dive_id):
    # MD_GET_DIVE is a table function that requires the id as a named UUID
    # argument and only accepts a literal (no lateral column reference), so it is
    # called once per dive id here. The dive's React/JSX source is in the `content`
    # column; bind the id and cast it to UUID inside SQL.
    row = con.execute(
        "SELECT content FROM MD_GET_DIVE(id => ?::UUID)", [dive_id]
    ).fetchone()
    return row[0] if row and row[0] else ""


def extract_sql_candidates(source):
    """Pull candidate SQL strings out of a dive's JSX source.

    Dives embed SQL in template literals (the common case, e.g.
    ``useSQLQuery(`SELECT ...`)``) and occasionally in quoted strings. Capture
    both, neutralize JS ``${...}`` interpolations so the SQL parses, and keep only
    strings that look like SQL. Pure-Python and side-effect free so it is testable
    in isolation.
    """
    seen = set()
    candidates = []
    raw_strings = []

    for match in TEMPLATE_LITERAL_RE.finditer(source):
        raw_strings.append(match.group(1))
    for match in QUOTED_STRING_RE.finditer(source):
        raw_strings.append(match.group(1) if match.group(1) is not None else match.group(2))

    for raw in raw_strings:
        sql = neutralize_interpolations(raw)
        if not SQL_START_RE.match(sql):
            continue
        normalized = sql.strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        candidates.append(normalized)
    return candidates


def neutralize_interpolations(text):
    # Replace `${...}` with a harmless placeholder literal so an interpolated
    # template literal still parses as SQL. Collapse a quote-wrapped interpolation
    # (`'${...}'`) into one quoted literal first, so it does not become a doubled
    # quoted string, then handle bare interpolations. Heavily nested
    # interpolations may not be fully neutralized; those statements fail
    # json_serialize_sql and are skipped (and counted) rather than guessed at.
    text = QUOTED_INTERPOLATION_RE.sub(INTERPOLATION_PLACEHOLDER, text)
    return INTERPOLATION_RE.sub(INTERPOLATION_PLACEHOLDER, text)


def serialize_sql(con, sql):
    # json_serialize_sql returns a JSON string AST. Bind the SQL as a parameter
    # (never string-formatted). On a parse error the top-level object carries
    # "error": true, so return None and let the caller count it as skipped.
    try:
        raw = con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0]
    except duckdb.Error:
        return None
    try:
        ast = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(ast, dict) or ast.get("error"):
        return None
    return ast


def objects_from_ast(ast):
    """Walk a json_serialize_sql AST and collect referenced objects.

    Returns a set of ``(object_type, object_name)`` pairs:

    - ``BASE_TABLE`` nodes carry ``catalog_name`` / ``schema_name`` /
      ``table_name``; each contributes a ``database`` (catalog), a ``schema``
      (catalog.schema), and a fully-qualified ``table`` entry.
    - ``COLUMN_REF`` nodes carry a ``column_names`` array; the last element is the
      column name. Columns are attributed by NAME only (see README Caveats).

    Pure-Python so it is testable in isolation on a parsed AST.
    """
    objects = set()

    for node in iter_nodes(ast):
        node_type = node.get("type")
        node_class = node.get("class")
        if node_type == "BASE_TABLE":
            catalog = (node.get("catalog_name") or "").strip()
            schema = (node.get("schema_name") or "").strip()
            table = (node.get("table_name") or "").strip()
            if catalog:
                objects.add(("database", catalog))
            if catalog and schema:
                objects.add(("schema", f"{catalog}.{schema}"))
            # Only count fully-qualified tables (database.schema.table). A bare or
            # partially-qualified name is almost always a CTE or an alias, which
            # the parser also reports as a BASE_TABLE, so it is skipped to keep the
            # table metric clean.
            if catalog and schema and table:
                objects.add(("table", f"{catalog}.{schema}.{table}"))
        if node_type == "COLUMN_REF" or node_class == "COLUMN_REF":
            names = node.get("column_names")
            if isinstance(names, list) and names:
                column = str(names[-1]).strip()
                # Skip "*" and SQL keywords the parser surfaces as columns (e.g.
                # CURRENT_DATE) so the column metric reflects real columns.
                if column and column != "*" and column.lower() not in KEYWORD_COLUMNS:
                    objects.add(("column", column))

    return objects


# ---- relationship extraction ----------------------------------------------


def iter_nodes(node):
    # Yield every dict node anywhere in the AST tree (depth-first). The AST is a
    # tree, so there are no cycles to guard against.
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            for value in current.values():
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            for item in current:
                if isinstance(item, (dict, list)):
                    stack.append(item)


def qualified_table(node):
    # Return database.schema.table when all three parts are present, else "".
    catalog = (node.get("catalog_name") or "").strip()
    schema = (node.get("schema_name") or "").strip()
    table = (node.get("table_name") or "").strip()
    if catalog and schema and table:
        return f"{catalog}.{schema}.{table}"
    return ""


def collect_cte_defs(ast):
    # Map each CTE name (lower-cased) to the SELECT node of its definition. CTEs
    # live under cte_map.map as {"key": <name>, "value": {"query": {"node": ...}}}.
    defs = {}
    for node in iter_nodes(ast):
        cte_map = node.get("cte_map")
        if not isinstance(cte_map, dict):
            continue
        for entry in cte_map.get("map", []):
            if not isinstance(entry, dict):
                continue
            name = (entry.get("key") or "").strip().lower()
            value = entry.get("value")
            query = value.get("query") if isinstance(value, dict) else None
            node_def = query.get("node") if isinstance(query, dict) else None
            if name and isinstance(node_def, dict):
                defs[name] = node_def
    return defs


def cte_underlying(name, defs, memo, stack):
    # The set of real fully-qualified tables a CTE ultimately reads from, expanding
    # references to other CTEs. `stack` guards against recursive CTEs.
    if name in memo:
        return memo[name]
    if name in stack or name not in defs:
        return set()
    new_stack = stack | {name}
    result = set()
    for node in iter_nodes(defs[name]):
        if node.get("type") != "BASE_TABLE":
            continue
        fq = qualified_table(node)
        if fq:
            result.add(fq)
        else:
            ref = (node.get("table_name") or "").strip().lower()
            if ref in defs:
                result |= cte_underlying(ref, defs, memo, new_stack)
    memo[name] = result
    return result


def analyze_statement(ast):
    """Return (tables, joins) for one parsed statement.

    - ``tables``: the set of fully-qualified real tables the statement reads,
      including those reached through CTEs.
    - ``joins``: a set of normalized ``(left_table, left_col, right_table,
      right_col)`` edges mined from every ``col = col`` equality (JOIN ON and
      WHERE alike), with table aliases and CTE references resolved.

    Pure-Python so it is testable in isolation on a parsed AST.
    """
    defs = collect_cte_defs(ast)
    memo = {}
    tables = set()
    alias_map = {}  # alias-or-table-name (lower) -> set of fully-qualified tables

    for node in iter_nodes(ast):
        if node.get("type") != "BASE_TABLE":
            continue
        fq = qualified_table(node)
        if fq:
            target = {fq}
            tables.add(fq)
        else:
            ref = (node.get("table_name") or "").strip().lower()
            target = cte_underlying(ref, defs, memo, set())
            tables |= target
        key = (node.get("alias") or node.get("table_name") or "").strip().lower()
        if key and target:
            alias_map.setdefault(key, set()).update(target)

    joins = set()
    for node in iter_nodes(ast):
        if node.get("class") == "COMPARISON" and node.get("type") == "COMPARE_EQUAL":
            joins |= column_pair(node.get("left"), node.get("right"), alias_map)
    return tables, joins


def resolve_colref(node, alias_map):
    # A qualified column reference (qualifier.column) resolved to a set of
    # (table, column) tuples via the alias map. Bare columns (no qualifier) cannot
    # be attributed to a table, so they yield nothing.
    if not isinstance(node, dict):
        return set()
    if node.get("class") != "COLUMN_REF" and node.get("type") != "COLUMN_REF":
        return set()
    names = node.get("column_names")
    if not isinstance(names, list) or len(names) < 2:
        return set()
    column = str(names[-1]).strip()
    qualifier = str(names[-2]).strip().lower()
    if not column or not qualifier:
        return set()
    return {(table, column) for table in alias_map.get(qualifier, set())}


def column_pair(left, right, alias_map):
    # Normalized cross-table join edges from a `col = col` equality. Self-equality
    # (same table on both sides) is dropped; it is not a relationship.
    edges = set()
    for (lt, lc) in resolve_colref(left, alias_map):
        for (rt, rc) in resolve_colref(right, alias_map):
            if lt == rt:
                continue
            ends = sorted([(lt, lc), (rt, rc)])
            edges.add((ends[0][0], ends[0][1], ends[1][0], ends[1][1]))
    return edges


def table_pairs(tables):
    # Every unordered pair of distinct tables co-occurring in one statement.
    ordered = sorted(tables)
    pairs = set()
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            pairs.add((ordered[i], ordered[j]))
    return pairs


# ---- writers ----------------------------------------------------------------


def insert_rows(con, fqn, columns, rows, run_at):
    # Bulk multi-row INSERT (chunked) with bound parameters. run_at is prepended
    # to every row so the whole run shares one timestamp. Never string-formats data.
    if not rows:
        return
    all_columns = ["run_at"] + list(columns)
    placeholder = "(" + ", ".join(["?"] * len(all_columns)) + ")"
    column_list = ", ".join(all_columns)
    for start in range(0, len(rows), INSERT_BATCH):
        batch = rows[start:start + INSERT_BATCH]
        params = []
        for row in batch:
            params.append(run_at)
            params.extend(row)
        con.execute(
            f"INSERT INTO {fqn} ({column_list}) VALUES " + ", ".join([placeholder] * len(batch)),
            params,
        )


def latest_view(con, fqn):
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {fqn}_latest AS
        SELECT * FROM {fqn}
        WHERE run_at = (SELECT max(run_at) FROM {fqn})
        """
    )


def write_metrics(con, target_database, target_schema, metrics_fqn, rows, run_at):
    # The metrics live in a writable database, so create it (and its schema) on
    # first run. History-append model: every run stamps its rows with one run_at,
    # so trends over time stay visible. A `<table>_latest` view always exposes the
    # most recent run.
    con.execute(f"CREATE DATABASE IF NOT EXISTS {target_database}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {target_database}.{target_schema}")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {metrics_fqn} (
            run_at TIMESTAMPTZ,
            object_type VARCHAR,
            object_name VARCHAR,
            dive_count INTEGER,
            reference_count INTEGER
        )
        """
    )
    insert_rows(con, metrics_fqn, ["object_type", "object_name", "dive_count", "reference_count"], rows, run_at)
    latest_view(con, metrics_fqn)


def write_object_edges(con, fqn, rows, run_at):
    # One row per (dive, referenced object): the dependency graph for impact
    # analysis ("which dives reference this table?").
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {fqn} (
            run_at TIMESTAMPTZ,
            dive_id VARCHAR,
            dive_title VARCHAR,
            object_type VARCHAR,
            object_name VARCHAR
        )
        """
    )
    insert_rows(con, fqn, ["dive_id", "dive_title", "object_type", "object_name"], rows, run_at)
    latest_view(con, fqn)


def write_cooccurrence(con, fqn, rows, run_at):
    # Tables that appear together in the same statement, with how many distinct
    # dives and statements pair them.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {fqn} (
            run_at TIMESTAMPTZ,
            table_a VARCHAR,
            table_b VARCHAR,
            dive_count INTEGER,
            query_count INTEGER
        )
        """
    )
    insert_rows(con, fqn, ["table_a", "table_b", "dive_count", "query_count"], rows, run_at)
    latest_view(con, fqn)


def write_join_edges(con, fqn, rows, run_at):
    # Join keys mined from `col = col` equalities, with alias and CTE references
    # resolved to real tables: an ERD inferred from how dives actually query.
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {fqn} (
            run_at TIMESTAMPTZ,
            left_table VARCHAR,
            left_column VARCHAR,
            right_table VARCHAR,
            right_column VARCHAR,
            dive_count INTEGER,
            query_count INTEGER
        )
        """
    )
    insert_rows(
        con, fqn,
        ["left_table", "left_column", "right_table", "right_column", "dive_count", "query_count"],
        rows, run_at,
    )
    latest_view(con, fqn)


# ---- env / validation helpers ----------------------------------------------


def env(name, default):
    value = os.environ.get(name, default).strip()
    return value or default


def env_bool(name, default):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def env_int(name, default):
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {value!r}")


def validate_identifier(name, value):
    # Config-supplied database, schema, and table names flow into SQL that cannot
    # be parameterized, so reject anything that is not a plain SQL identifier
    # before any SQL runs. All data values (object names, counts) are bound as
    # parameters instead.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


if __name__ == "__main__":
    main()
