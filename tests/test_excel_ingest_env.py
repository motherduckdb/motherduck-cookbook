from __future__ import annotations

import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATHS = (
    ROOT / "excel-local-ingest" / "load_excel.py",
    ROOT / "flight-plans" / "flight-excel-s3-ingest" / "flight.py",
)


@pytest.mark.parametrize("script_path", SCRIPT_PATHS, ids=lambda path: path.name)
@pytest.mark.parametrize(
    ("configured_value", "expected"),
    ((None, "orders"), ("", ""), (" regions ", "regions")),
    ids=("unset", "empty", "named"),
)
def test_sheet_env_distinguishes_unset_empty_and_named_values(
    monkeypatch: pytest.MonkeyPatch,
    script_path: Path,
    configured_value: str | None,
    expected: str,
) -> None:
    if configured_value is None:
        monkeypatch.delenv("SHEET", raising=False)
    else:
        monkeypatch.setenv("SHEET", configured_value)

    # DuckDB is not used while loading or testing the environment helper.
    monkeypatch.setitem(sys.modules, "duckdb", ModuleType("duckdb"))
    module = runpy.run_path(str(script_path), run_name="excel_ingest_env_test")

    assert module["env"]("SHEET", "orders", allow_empty=True) == expected
