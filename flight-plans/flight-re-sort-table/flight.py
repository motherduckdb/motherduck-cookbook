"""MotherDuck Flight: re-sort a table so selective queries can skip row groups.

If fast selective read queries are important, use this Flight for up to 10x or more speed improvement!
DuckDB keeps a min/max index (a zonemap) per row group per column. A filtered
query skips a whole row group when the filter value falls outside that range —
but only if rows are physically clustered on the filtered column. This flight
rewrites a table (in full, or just the slice matched by ``WHERE_CLAUSE``) in
``ORDER_BY_CLAUSE`` order so those skips actually happen. Background:
https://duckdb.org/2025/05/14/sorting-for-fast-selective-queries

Inputs (flight config / env vars):

- ``TABLE_TO_ORDER``: the table to rewrite, optionally qualified
  (``db.schema.table``). Quoted identifiers are fine.
- ``ORDER_BY_CLAUSE``: the sort expression(s). A leading ``ORDER BY`` is
  optional, any case.
- ``WHERE_CLAUSE``: optional; only rows matching it are rewritten. A leading
  ``WHERE`` is optional, any case. Omit (or leave empty) to re-sort the whole
  table.
- ``DRY_RUN``: ``true`` to validate everything and report what would happen
  without modifying any data.

Because these inputs are spliced into SQL, each one is validated before it is
allowed anywhere near a real statement.

Once validated, the rewrite runs as one transaction:

    BEGIN;
    CREATE OR REPLACE TABLE <table>_<uuid> AS
        SELECT * FROM <table> WHERE (<where>);
    DELETE FROM <table> WHERE (<where>);
    INSERT INTO <table> SELECT * FROM <table>_<uuid> ORDER BY <order_by>;
    DROP TABLE <table>_<uuid>;
    COMMIT;

The scratch table lives in the same database and schema as the
target and is dropped inside the transaction, so a commit leaves nothing
behind and a rollback undoes everything at once.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid

import duckdb

log = logging.getLogger("re_sort")


class ValidationError(Exception):
    """An input failed SQL-injection validation."""


# ---------------------------------------------------------------------------
# Identifier quoting
# ---------------------------------------------------------------------------
def _ident(name: str) -> str:
    """Quote a SQL identifier; an embedded ``"`` is doubled (the SQL escape),
    so no value can break out of its syntactic position."""
    return '"' + name.replace('"', '""') + '"'


def _qualified(catalog: str, schema: str, name: str) -> str:
    """A fully quoted, dotted reference from AST name parts (empty parts are
    simply absent, mirroring how the user qualified the table)."""
    return ".".join(_ident(p) for p in (catalog, schema, name) if p)


# ---------------------------------------------------------------------------
# Tokenizer helpers (duckdb.tokenize = DuckDB's SQL lexer)
# ---------------------------------------------------------------------------
def _tokens(sql: str) -> list[tuple[str, str, int]]:
    """``(text, token_type, offset)`` for each token DuckDB's tokenizer finds.
    The tokenizer only reports start offsets, so a token's text runs to the
    next token's start (whitespace-stripped)."""
    raw = duckdb.tokenize(sql)
    out = []
    for i, (offset, token_type) in enumerate(raw):
        end = raw[i + 1][0] if i + 1 < len(raw) else len(sql)
        out.append((sql[offset:end].strip(), token_type.name, offset))
    return out


def strip_leading_keywords(clause: str, keywords: tuple[str, ...]) -> str:
    """Drop an optional leading keyword sequence (``('order', 'by')`` or
    ``('where',)``) from a clause, case-insensitively and with any whitespace,
    using the tokenizer rather than string matching — so ``WHEREx > 1`` (an
    identifier) is left alone while ``where x > 1`` (the keyword) is stripped.
    """
    toks = _tokens(clause)
    if len(toks) < len(keywords):
        return clause.strip()
    for (text, token_type, _), keyword in zip(toks, keywords):
        if token_type != "keyword" or text.lower() != keyword:
            return clause.strip()
    if len(toks) == len(keywords):  # the clause was ONLY the keywords
        return ""
    return clause[toks[len(keywords)][2] :].strip()


# ---------------------------------------------------------------------------
# Validation via DuckDB's parser (json_serialize_sql)
# ---------------------------------------------------------------------------
def _parse_single_select(con: duckdb.DuckDBPyConnection, dummy: str, input_name: str) -> dict:
    """Parse a dummy statement with DuckDB itself and require exactly one
    SELECT statement. ``json_serialize_sql`` reports a parse failure or a
    non-SELECT statement as ``error: true`` and returns one entry per
    statement, so this single check rejects multi-statement and non-SELECT
    payloads at once. Returns the statement's AST node for structural checks.
    """
    row = con.execute("SELECT json_serialize_sql(?)", [dummy]).fetchone()
    parsed = json.loads(row[0])
    if parsed.get("error"):
        raise ValidationError(
            f"{input_name} failed validation: {parsed.get('error_message')} "
            f"(dummy statement: {dummy!r})"
        )
    statements = parsed.get("statements", [])
    if len(statements) != 1:
        raise ValidationError(
            f"{input_name} failed validation: dummy statement parsed into "
            f"{len(statements)} statements, expected exactly 1 (dummy statement: {dummy!r})"
        )
    node = statements[0]["node"]
    if node.get("type") != "SELECT_NODE":
        raise ValidationError(
            f"{input_name} failed validation: dummy statement is a "
            f"{node.get('type')}, not a plain SELECT (dummy statement: {dummy!r})"
        )
    return node


def validate_table(con: duckdb.DuckDBPyConnection, raw: str) -> tuple[str, str, str]:
    """Validate ``TABLE_TO_ORDER`` and return ``(catalog, schema, table)`` as
    parsed by DuckDB. The caller rebuilds a quoted reference from these parts,
    so the raw string itself never reaches any executed SQL."""
    if not raw or not raw.strip():
        raise ValidationError("TABLE_TO_ORDER is required")
    node = _parse_single_select(con, f"SELECT * FROM {raw}", "TABLE_TO_ORDER")
    # USING SAMPLE attaches to the statement node, not the table reference.
    if node.get("where_clause") is not None or node.get("modifiers") or node.get("sample") is not None:
        raise ValidationError("TABLE_TO_ORDER must be a plain table reference")
    from_table = node.get("from_table") or {}
    if from_table.get("type") != "BASE_TABLE":
        raise ValidationError(
            "TABLE_TO_ORDER must be a bare table name "
            f"(parsed as {from_table.get('type')}, expected BASE_TABLE)"
        )
    if (
        from_table.get("alias")
        or from_table.get("column_name_alias")
        or from_table.get("sample") is not None
        or from_table.get("at_clause") is not None
    ):
        raise ValidationError(
            "TABLE_TO_ORDER must be a bare table name without an alias, SAMPLE, or AT clause"
        )
    return (
        from_table.get("catalog_name", ""),
        from_table.get("schema_name", ""),
        from_table["table_name"],
    )


def validate_order_by(con: duckdb.DuckDBPyConnection, target: str, raw: str) -> str:
    """Validate ``ORDER_BY_CLAUSE`` (optional leading ORDER BY) and return the
    bare clause. The dummy puts the clause in exactly the position it will be
    executed in, so what is validated is what runs."""
    clause = strip_leading_keywords(raw or "", ("order", "by"))
    if not clause:
        raise ValidationError("ORDER_BY_CLAUSE is required")
    dummy = f"SELECT * FROM {target} ORDER BY {clause}"
    node = _parse_single_select(con, dummy, "ORDER_BY_CLAUSE")
    modifiers = node.get("modifiers") or []
    if len(modifiers) != 1 or modifiers[0].get("type") != "ORDER_MODIFIER":
        raise ValidationError(
            "ORDER_BY_CLAUSE must contain only sort expressions "
            "(no LIMIT/OFFSET or other trailing clauses)"
        )
    if node.get("where_clause") is not None or node.get("group_expressions"):
        raise ValidationError("ORDER_BY_CLAUSE must contain only sort expressions")
    return clause


def validate_where(con: duckdb.DuckDBPyConnection, target: str, raw: str) -> str | None:
    """Validate ``WHERE_CLAUSE`` (optional leading WHERE) and return the bare
    predicate, or ``None`` when empty (re-sort the whole table). Validation
    wraps the predicate in parentheses exactly as execution will, so a clause
    that only parses unparenthesized (e.g. one ending in a ``--`` comment that
    would swallow the closing paren) is rejected here, not at run time."""
    clause = strip_leading_keywords(raw or "", ("where",))
    if not clause:
        return None
    # The predicate must parse BOTH bare and parenthesized. Bare rejects
    # paren-rebalancing tricks like ``1=1) or (2=2``; parenthesized rejects a
    # clause that would break once wrapped (e.g. ending in a ``--`` comment
    # that swallows the closing paren) and matches how it is executed.
    node = _parse_single_select(con, f"SELECT * FROM {target} WHERE {clause}", "WHERE_CLAUSE")
    _parse_single_select(con, f"SELECT * FROM {target} WHERE ({clause})", "WHERE_CLAUSE")
    if node.get("where_clause") is None:
        raise ValidationError("WHERE_CLAUSE did not produce a WHERE predicate")
    if (
        node.get("modifiers")
        or node.get("group_expressions")
        or node.get("having") is not None
        or node.get("qualify") is not None
    ):
        raise ValidationError(
            "WHERE_CLAUSE must contain only a predicate "
            "(no ORDER BY/GROUP BY/HAVING/LIMIT or other trailing clauses)"
        )
    return clause


# ---------------------------------------------------------------------------
# The re-sort itself
# ---------------------------------------------------------------------------
def resort(
    con: duckdb.DuckDBPyConnection,
    target: str,
    scratch: str,
    order_by: str,
    where_sql: str,
) -> int:
    """Rewrite the rows of ``target`` matching ``where_sql`` in ``order_by``
    order, atomically. Returns the number of rows rewritten."""
    statements = {
        "copy": f"CREATE OR REPLACE TABLE {scratch} AS SELECT * FROM {target} WHERE {where_sql}",
        "delete": f"DELETE FROM {target} WHERE {where_sql}",
        "insert": f"INSERT INTO {target} SELECT * FROM {scratch} ORDER BY {order_by}",
        "drop": f"DROP TABLE {scratch}",
    }
    for name, sql in statements.items():
        log.info("%s: %s", name, sql)

    con.execute("BEGIN TRANSACTION")
    try:
        copied = con.execute(statements["copy"]).fetchone()[0]
        deleted = con.execute(statements["delete"]).fetchone()[0]
        inserted = con.execute(statements["insert"]).fetchone()[0]
        if not (copied == deleted == inserted):
            raise RuntimeError(
                f"row count mismatch, rolling back: copied={copied} "
                f"deleted={deleted} inserted={inserted}"
            )
        con.execute(statements["drop"])
        con.execute("COMMIT")
    except BaseException:
        try:
            con.execute("ROLLBACK")
        except duckdb.Error:
            pass  # connection-level failures: the server abandons the txn anyway
        raise
    return inserted


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    table_raw = os.environ.get("TABLE_TO_ORDER", "")
    order_raw = os.environ.get("ORDER_BY_CLAUSE", "")
    where_raw = os.environ.get("WHERE_CLAUSE", "")
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

    # Validate on a local in-memory DuckDB: nothing unvalidated reaches MotherDuck.
    validator = duckdb.connect()
    catalog, schema, table = validate_table(validator, table_raw)
    target = _qualified(catalog, schema, table)
    scratch = _qualified(catalog, schema, f"{table}_{uuid.uuid4().hex}")
    order_by = validate_order_by(validator, target, order_raw)
    where = validate_where(validator, target, where_raw)
    where_sql = f"({where})" if where else "true"
    log.info("target=%s order_by=%r where=%r dry_run=%s", target, order_by, where, dry_run)

    con = duckdb.connect("md:")
    # Bind-only dry run: WHERE ... AND false scans nothing but still makes the
    # server resolve the table and every column before any data is touched.
    con.execute(f"SELECT * FROM {target} WHERE {where_sql} AND false ORDER BY {order_by}")
    matched = con.execute(f"SELECT count(*) FROM {target} WHERE {where_sql}").fetchone()[0]
    log.info("%s rows match; they will be rewritten in sorted order", matched)

    if dry_run:
        log.info("DRY_RUN set: validation passed, no data modified")
        return

    start = time.monotonic()
    rewritten = resort(con, target, scratch, order_by, where_sql)
    log.info("re-sorted %s rows in %s in %.1fs", rewritten, target, time.monotonic() - start)


if __name__ == "__main__":
    main()
