"""Tests for scripts/deploy_flights.py — flight.toml parsing and SQL building.

These are offline: they never connect to MotherDuck. They lock in that the
deploy args are read straight from each flight's flight.toml + main.py, and
that the MAP/LIST literals and schedule handling come out as expected.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

FLIGHT_ROOT = Path(__file__).parent.parent


def _load_module():
    path = FLIGHT_ROOT / "scripts" / "deploy_flights.py"
    spec = importlib.util.spec_from_file_location("deploy_flights", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = _load_module()


class TestLoadFlight:
    def test_nightly_has_schedule_and_token(self):
        flight = M.load_flight(FLIGHT_ROOT / "flights" / "nba_nightly")
        assert flight["name"] == "nba_nightly"
        assert flight["access_token_name"] == "dives-loader-nba"
        assert flight["schedule_cron"] == "0 16 * * *"
        assert flight["flight_secret_names"] == []
        assert flight["config"] == {}
        # source_code is the clone-the-repo bootstrapper, not the pipeline package.
        assert "REPO_URL" in flight["source_code"]
        assert "clone" in flight["source_code"]

    def test_backfill_is_on_demand(self):
        flight = M.load_flight(FLIGHT_ROOT / "flights" / "nba_backfill")
        assert flight["name"] == "nba_backfill"
        assert flight["schedule_cron"] == ""


class TestSqlLiterals:
    def test_empty_collections_are_typed(self):
        assert M._sql_list([]) == "[]::VARCHAR[]"
        assert M._sql_map({}) == "MAP {}::MAP(VARCHAR, VARCHAR)"

    def test_non_empty_collections(self):
        assert M._sql_list(["a", "b"]) == "['a', 'b']"
        assert M._sql_map({"REGION": "us"}) == "MAP {'REGION': 'us'}"

    def test_single_quotes_are_escaped(self):
        assert M._sql_str("O'Brien") == "'O''Brien'"


class TestBuildArgs:
    def test_scheduled_flight_includes_cron_param(self):
        flight = M.load_flight(FLIGHT_ROOT / "flights" / "nba_nightly")
        fragments, params = M._build_args(flight)
        assert "schedule_cron := ?" in fragments
        assert params[-1] == "0 16 * * *"
        assert params[0] == "nba_nightly"

    def test_on_demand_flight_omits_cron(self):
        flight = M.load_flight(FLIGHT_ROOT / "flights" / "nba_backfill")
        fragments, params = M._build_args(flight)
        assert not any(f.startswith("schedule_cron") for f in fragments)
