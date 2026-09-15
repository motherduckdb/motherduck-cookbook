from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
FLIGHT_PATH = ROOT / "flight-plans" / "flight-commoncrawl-web-graph" / "flight.py"


@pytest.fixture()
def flight_module(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setitem(sys.modules, "duckdb", ModuleType("duckdb"))
    return runpy.run_path(str(FLIGHT_PATH), run_name="commoncrawl_web_graph_flight_test")


def test_identifier_validation_accepts_simple_names_and_rejects_sql(flight_module: dict[str, object]) -> None:
    validate_identifier = flight_module["validate_identifier"]

    assert validate_identifier("HOSTS_TABLE", "platform_hosts") == "platform_hosts"
    with pytest.raises(ValueError, match="simple SQL identifier"):
        validate_identifier("HOSTS_TABLE", "hosts; DROP TABLE hosts")


def test_release_selection_uses_only_the_pinned_registry(flight_module: dict[str, object]) -> None:
    parse_release_selection = flight_module["parse_release_selection"]
    releases = flight_module["RELEASES"]

    assert len(parse_release_selection("")) == 16
    selected = parse_release_selection("cc-main-2026-apr-may-jun,cc-main-2021-feb-apr-may")
    assert [release.name for release in selected] == [
        "cc-main-2021-feb-apr-may",
        "cc-main-2026-apr-may-jun",
    ]
    assert releases[0].end_date.isoformat() == "2021-05-31"
    assert releases[-1].end_date.isoformat() == "2026-06-30"
    with pytest.raises(ValueError, match="outside the pinned registry"):
        parse_release_selection("cc-main-2026-jun-jul-aug")
    with pytest.raises(ValueError, match="duplicate"):
        parse_release_selection("cc-main-2026-apr-may-jun,cc-main-2026-apr-may-jun")


def test_platform_registry_hash_changes_when_a_row_changes(flight_module: dict[str, object]) -> None:
    platform_registry_hash = flight_module["platform_registry_hash"]
    Platform = flight_module["Platform"]
    platforms = flight_module["PLATFORMS"]

    changed = (Platform("base44.app", "app.base44", "Base 44", "platform"), *platforms[1:])
    assert platform_registry_hash(platforms) == platform_registry_hash(tuple(reversed(platforms)))
    assert platform_registry_hash(platforms) != platform_registry_hash(changed)
    with pytest.raises(ValueError, match="Invalid platform domain mapping"):
        platform_registry_hash((Platform("github.io", "github.io", "GitHub Pages", "platform"),))


def test_https_source_urls_and_rank_types_are_validated(flight_module: dict[str, object]) -> None:
    validate_https_base_url = flight_module["validate_https_base_url"]
    release_source_url = flight_module["release_source_url"]
    release = flight_module["RELEASES"][-1]

    base_url = validate_https_base_url("https://data.commoncrawl.org/projects/hyperlinkgraph/")
    assert base_url == "https://data.commoncrawl.org/projects/hyperlinkgraph"
    assert release_source_url(base_url, release, "host").endswith(
        "/cc-main-2026-apr-may-jun/host/cc-main-2026-apr-may-jun-host-ranks.txt.gz"
    )
    with pytest.raises(ValueError, match="absolute HTTPS URL"):
        validate_https_base_url("s3://commoncrawl/projects/hyperlinkgraph")
    with pytest.raises(ValueError, match="Unsupported rank type"):
        release_source_url(base_url, release, "node")
