"""MotherDuck connection helper.

Thin wrapper around `duckdb.connect`. Reads `MOTHERDUCK_TOKEN` from the
environment (injected by the Flight runtime, or set locally for dev) and
uses the v3 database as the default working catalog.

Tests pass `database=":memory:"` to get a hermetic in-memory instance —
the same loader and schema modules work against both.
"""

from __future__ import annotations

import os

import duckdb


DEFAULT_DATABASE = "nba_box_scores_v3"


def connect(database: str = DEFAULT_DATABASE) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection.

    For MotherDuck-backed runs, pass a database name (default v3). The
    function relies on `MOTHERDUCK_TOKEN` being set in the environment;
    duckdb's `md:` connector picks it up automatically.

    For tests, pass `":memory:"` to get an in-memory DuckDB instance.
    """
    if database == ":memory:":
        return duckdb.connect(":memory:")

    if not os.environ.get("MOTHERDUCK_TOKEN"):
        raise RuntimeError("MOTHERDUCK_TOKEN is not set")

    con = duckdb.connect("md:")
    con.execute(f'USE "{database}"')
    return con
