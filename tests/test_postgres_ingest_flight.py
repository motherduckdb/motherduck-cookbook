from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "flight-plans" / "flight-postgres-ingest" / "flight.py"


@pytest.fixture
def flight(monkeypatch: pytest.MonkeyPatch) -> dict:
    # resolve_pg_env() writes libpq vars into os.environ; give each test its own copy.
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("PG")})
    # Neither DuckDB nor tenacity is used by the helpers under test.
    monkeypatch.setitem(sys.modules, "duckdb", ModuleType("duckdb"))
    tenacity = ModuleType("tenacity")
    for name in ("Retrying", "stop_after_attempt", "wait_exponential", "wait_random"):
        setattr(tenacity, name, None)
    monkeypatch.setitem(sys.modules, "tenacity", tenacity)
    return runpy.run_path(str(SCRIPT), run_name="postgres_ingest_flight_test")


def test_url_is_decoded_and_query_params_reach_libpq(flight: dict, monkeypatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgres://bob:p%40ss%2Fword@ep-x.eu-central-1.aws.neon.tech:5433/my%20db"
        "?sslmode=require&channel_binding=require&foo=bar",
    )
    flight["resolve_pg_env"]()
    env = os.environ
    assert (env["PGHOST"], env["PGPORT"], env["PGDATABASE"]) == (
        "ep-x.eu-central-1.aws.neon.tech", "5433", "my db")
    assert (env["PGUSER"], env["PGPASSWORD"]) == ("bob", "p@ss/word")
    assert (env["PGSSLMODE"], env["PGCHANNELBINDING"]) == ("require", "require")


def test_malformed_url_error_never_contains_the_password(flight: dict, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://bob:s3cr/etpass@host/db")
    with pytest.raises(RuntimeError) as err:
        flight["resolve_pg_env"]()
    assert "s3cr" not in str(err.value)


def test_sslmode_defaults_to_require(flight: dict, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://bob:pw@db.example.com/app")
    flight["resolve_pg_env"]()
    assert os.environ["PGSSLMODE"] == "require"


@pytest.mark.parametrize(
    ("host", "port", "user", "message"),
    (
        ("dpg-abc123-a", "5432", "app", "Internal Database URL"),
        ("aws-0-eu-central-1.pooler.supabase.com", "5432", "postgres", "<role>.<project_ref>"),
        ("aws-0-eu-central-1.pooler.supabase.com", "6543", "postgres.abc", "transaction pooler"),
        ("srv.private.postgres.database.azure.com", "5432", "app", "private-access"),
        ("srv.postgres.database.azure.com", "6432", "app", "PgBouncer"),
        ("srv.postgres.database.azure.com", "5432", "app@srv", "Single Server"),
    ),
)
def test_known_bad_hosts_fail_fast(flight: dict, host, port, user, message) -> None:
    with pytest.raises(RuntimeError, match=message):
        flight["check_host"](host, port, user)


@pytest.mark.parametrize(
    ("host", "port", "user"),
    (
        ("dpg-abc123-a.frankfurt-postgres.render.com", "5432", "app"),
        ("aws-0-eu-central-1.pooler.supabase.com", "5432", "postgres.abc"),
        ("srv.postgres.database.azure.com", "5432", "app"),
        ("ec2-1-2-3-4.compute-1.amazonaws.com", "5432", "u1"),
    ),
)
def test_working_hosts_pass(flight: dict, host, port, user) -> None:
    flight["check_host"](host, port, user)


def test_incremental_spec(flight: dict) -> None:
    assert flight["parse_incremental"]("orders:updated_at:id, app.lines:ts:order_id|line_no") == {
        "public.orders": ("updated_at", ["id"]),
        "app.lines": ("ts", ["order_id", "line_no"]),
    }
    with pytest.raises(RuntimeError, match="Bad INCREMENTAL_TABLES entry"):
        flight["parse_incremental"]("public.orders:updated_at")
