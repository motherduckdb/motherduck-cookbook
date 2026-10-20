import datetime as dt
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import flight  # noqa: E402

UTC = dt.timezone.utc


def test_every_table_ddl_is_valid_duckdb(monkeypatch):
    con = duckdb.connect()
    monkeypatch.setattr(flight, "TARGET_DATABASE", "memory")
    monkeypatch.setattr(flight, "TARGET_SCHEMA", "main")
    for table, (time_column, columns) in flight.TABLES.items():
        con.execute(f'CREATE TABLE "{table}" ({columns})')
        names = {r[0] for r in con.execute(f'DESCRIBE "{table}"').fetchall()}
        if time_column:
            assert time_column in names, f"{table}: retention column {time_column} missing"
    # retention touches every time-indexed table without error
    flight.prune(con, dt.datetime(2026, 1, 1, tzinfo=UTC))


def test_client_classification_from_user_agent():
    con = duckdb.connect()
    cases = {
        "duckdb/v1.5.6(osx_arm64) md-flights(abc,run_id=def) dbt/1.9": "flight",
        "duckdb/v1.5.6(wasm_eh) md-dives/v1(0f1e2d3c-0000-0000-0000-000000000000)": "dive",
        "duckdb/v1.5.6 mcp-server-motherduck-remote": "mcp",
        "duckdb/v1.5.6(wasm_eh) motherduck-wasm": "ui",
        "duckdb/v1.5.6(linux_amd64) dbt/1.9.0 dbt-duckdb/1.9.1": "dbt",
        "duckdb/v1.5.6(linux_amd64) dlt/1.4": "dlt",
        "duckdb/v1.5.6 pgendpoint(dbt)": "pg_endpoint",
        "duckdb/v1.5.6(linux_amd64) python/3.12": "python",
        "duckdb/v1.5.6(osx_arm64) jdbc": "jdbc",
        "something else": "other",
    }
    rows = con.execute(
        f"SELECT user_agent, {flight.CLIENT_SQL} FROM (SELECT unnest(?::VARCHAR[]) AS user_agent)",
        [list(cases)],
    ).fetchall()
    assert dict(rows) == cases


def test_dive_id_extraction():
    con = duckdb.connect()
    (dive_id,) = con.execute(
        f"SELECT {flight.DIVE_ID_SQL} FROM (SELECT 'duckdb/v1 md-dives/v1(0f1e2d3c-1111-2222-3333-444455556666)' AS user_agent)"
    ).fetchone()
    assert dive_id == "0f1e2d3c-1111-2222-3333-444455556666"


def test_resolve_window_backfills_on_first_run(monkeypatch):
    monkeypatch.setattr(flight, "load_window_end", lambda con: None)
    monkeypatch.setattr(flight, "DELAY_SECONDS", 60)
    monkeypatch.setattr(flight, "BACKFILL_HOURS", 24)
    now = dt.datetime(2026, 10, 1, 10, 5, 7, tzinfo=UTC)
    start, end = flight.resolve_window(None, now)
    assert end == dt.datetime(2026, 10, 1, 10, 4, tzinfo=UTC)
    assert start == dt.datetime(2026, 9, 30, 10, 4, tzinfo=UTC)


def test_resolve_window_without_backfill_uses_window_minutes(monkeypatch):
    monkeypatch.setattr(flight, "load_window_end", lambda con: None)
    monkeypatch.setattr(flight, "DELAY_SECONDS", 60)
    monkeypatch.setattr(flight, "BACKFILL_HOURS", 0)
    monkeypatch.setattr(flight, "WINDOW_MINUTES", 5)
    now = dt.datetime(2026, 10, 1, 10, 5, 7, tzinfo=UTC)
    start, end = flight.resolve_window(None, now)
    assert (start, end) == (dt.datetime(2026, 10, 1, 9, 59, tzinfo=UTC), dt.datetime(2026, 10, 1, 10, 4, tzinfo=UTC))


def test_resolve_window_continues_from_state_and_caps_catch_up(monkeypatch):
    monkeypatch.setattr(flight, "DELAY_SECONDS", 60)
    monkeypatch.setattr(flight, "MAX_CATCHUP_HOURS", 2)
    now = dt.datetime(2026, 10, 1, 10, 5, 7, tzinfo=UTC)
    monkeypatch.setattr(flight, "load_window_end", lambda con: dt.datetime(2026, 10, 1, 9, 57, tzinfo=UTC))
    assert flight.resolve_window(None, now) == (
        dt.datetime(2026, 10, 1, 9, 57, tzinfo=UTC), dt.datetime(2026, 10, 1, 10, 4, tzinfo=UTC))
    # a week-old watermark is caught up two hours per run, contiguously
    monkeypatch.setattr(flight, "load_window_end", lambda con: dt.datetime(2026, 9, 24, 7, 0, tzinfo=UTC))
    assert flight.resolve_window(None, now) == (
        dt.datetime(2026, 9, 24, 7, 0, tzinfo=UTC), dt.datetime(2026, 9, 24, 9, 0, tzinfo=UTC))


def test_insert_rows_is_chunked_and_bound(monkeypatch):
    con = duckdb.connect()
    monkeypatch.setattr(flight, "TARGET_DATABASE", "memory")
    con.execute('CREATE TABLE "memory"."main"."t" (a INTEGER, b VARCHAR)')
    run = flight.Run(con, dt.datetime(2026, 1, 1, tzinfo=UTC), dt.datetime(2026, 1, 2, tzinfo=UTC),
                     dt.datetime(2026, 1, 2, tzinfo=UTC))
    n = run.insert_rows("t", ["a", "b"], [(i, f"it's {i}") for i in range(1000)])
    assert n == 1000 and run.rows_written == 1000
    assert con.execute('SELECT count(*), max(a), min(b) FROM "memory"."main"."t"').fetchone() == (1000, 999, "it's 0")


def test_replace_window_then_insert_is_idempotent(monkeypatch):
    con = duckdb.connect()
    monkeypatch.setattr(flight, "TARGET_DATABASE", "memory")
    con.execute('CREATE TABLE "memory"."main"."query_minutes" (minute TIMESTAMPTZ, queries BIGINT)')
    start, end = dt.datetime(2026, 1, 1, 10, 0, tzinfo=UTC), dt.datetime(2026, 1, 1, 10, 5, tzinfo=UTC)
    run = flight.Run(con, start, end, end)
    for _ in range(2):
        run.replace_window("query_minutes", "minute")
        run.insert_select("query_minutes", "SELECT ? + INTERVAL 1 MINUTE, 7", [start])
    assert con.execute('SELECT count(*) FROM "memory"."main"."query_minutes"').fetchone() == (1,)


def test_validate_config_rejects_bad_identifiers(monkeypatch):
    monkeypatch.setattr(flight, "TARGET_DATABASE", "md-obs; drop")
    with pytest.raises(ValueError):
        flight.validate_config()
    monkeypatch.setattr(flight, "TARGET_DATABASE", "md_observability")
    monkeypatch.setattr(flight, "FLIGHT_RUN_SCOPE", "everything")
    with pytest.raises(ValueError):
        flight.validate_config()
