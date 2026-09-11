from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FLIGHT_PATH = (
    ROOT / "flight-plans" / "flight-persistent-context-analysis" / "flight.py"
)


@pytest.fixture(scope="module")
def flight():
    spec = importlib.util.spec_from_file_location("persistent_context_flight", FLIGHT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_config_uses_public_fixture_and_private_guides(flight):
    config = flight.parse_config({})
    assert config.demo_data_url == (
        "https://us.data.motherduck.com/persistent-context-analysis/"
        "v1/duck_shop_sales.parquet"
    )
    assert config.guide_access == "user"
    assert config.demo_revision == 0


def test_parse_config_rejects_unknown_guide_access(flight):
    with pytest.raises(ValueError, match="GUIDE_ACCESS"):
        flight.parse_config({"GUIDE_ACCESS": "public"})


def test_parse_config_rejects_unsafe_state_identifier(flight):
    with pytest.raises(ValueError, match="STATE_SCHEMA"):
        flight.parse_config({"STATE_SCHEMA": "main; DROP DATABASE prod"})
