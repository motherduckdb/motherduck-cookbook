"""Exercise the recipe's local evidence-capture and correctness paths."""

from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

RECIPE = Path(__file__).resolve().parents[1] / "query-optimization"
FIXTURES = Path(__file__).parent / "fixtures" / "query_optimization"


def helper(
    name: str, *arguments: str | Path, env: dict | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RECIPE / "scripts" / name), *map(str, arguments)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize("mode", ["explain", "profiling"])
def test_profile_preserves_real_operators_and_unmeasured_warmup(
    tmp_path: Path, mode: str
) -> None:
    output = tmp_path / mode
    result = helper(
        "profile.py",
        "--database",
        ":memory:",
        "--setup",
        FIXTURES / "setup.sql",
        "--sql",
        FIXTURES / "baseline.sql",
        "--materialize",
        "--mode",
        mode,
        "--output",
        output,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    record = json.loads((output / "run.json").read_text())
    assert record["status"] == "complete"
    assert record["warmup"]["status"] == "complete"
    assert record["warmup"]["finished_at"] <= record["measurement"]["started_at"]
    assert record["cleanup"] == "complete"
    assert record["validation"] == "not_run"
    profile = json.loads((output / "profile.json").read_text())
    assert "CREATE TABLE" in profile["query_name"]
    assert "md_opt_phase:measure" in profile["query_name"]
    operators = json.loads((output / "operators.json").read_text())["operators"]
    assert any("SCAN" in row["operator_name"] for row in operators)
    assert any("CREATE_TABLE" in row["operator_name"] for row in operators)


def test_default_benchmark_is_one_pair_and_does_not_accept_candidate(
    tmp_path: Path,
) -> None:
    output = tmp_path / "pair"
    result = helper(
        "benchmark.py",
        "--database",
        ":memory:",
        "--setup",
        FIXTURES / "setup.sql",
        "--baseline",
        FIXTURES / "baseline.sql",
        "--candidate",
        FIXTURES / "candidate.sql",
        "--output",
        output,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    record = json.loads((output / "benchmark.json").read_text())
    assert record["status"] == "complete"
    assert record["repetitions"] == 1
    assert [run["variant"] for run in record["runs"]] == ["baseline", "candidate"]
    assert record["validation"] == "not_run"
    assert record["decision"] == "pending_validation_and_profile_review"
    assert record["statistics"]["baseline"]["sample_stdev_seconds"] is None
    assert (
        (output / "candidate.diff")
        .read_text()
        .startswith("--- baseline.sql\n+++ candidate.sql\n")
    )
    assert (output / "report.md").exists()
    for run in record["runs"]:
        child = json.loads((output / run["directory"] / "run.json").read_text())
        assert child["mode"] == "timing"
        assert child["measurement"]["profile"] is None
        assert child["measurement"]["result_rows_fetched"] == 1000


def comparisons(
    connection: duckdb.DuckDBPyConnection, baseline: str, candidate: str
) -> list:
    connection.execute("SET VARIABLE baseline_relation = ?", [baseline])
    connection.execute("SET VARIABLE candidate_relation = ?", [candidate])
    statements = connection.extract_statements(
        (RECIPE / "scripts/compare_results.sql").read_text()
    )
    return [connection.execute(statement).fetchall() for statement in statements]


def test_baseline_and_candidate_outputs_are_schema_and_multiset_equal() -> None:
    with duckdb.connect() as con:
        con.execute((FIXTURES / "setup.sql").read_text())
        con.execute(
            "CREATE TABLE baseline AS " + (FIXTURES / "baseline.sql").read_text()
        )
        con.execute(
            "CREATE TABLE candidate AS " + (FIXTURES / "candidate.sql").read_text()
        )
        assert comparisons(con, "baseline", "candidate") == [
            [(0,)],
            [(1000, 1000)],
            [(0,)],
            [(0,)],
        ]


def test_validation_detects_duplicate_multiplicity_and_column_order() -> None:
    with duckdb.connect() as con:
        con.execute(
            "CREATE TABLE baseline AS SELECT * FROM (VALUES (1, 'x'), (1, 'x'), (2, 'y')) t(id, label)"
        )
        con.execute(
            "CREATE TABLE candidate AS SELECT * FROM (VALUES (1, 'x'), (2, 'y'), (2, 'y')) t(id, label)"
        )
        assert comparisons(con, "baseline", "candidate") == [
            [(0,)],
            [(3, 3)],
            [(1,)],
            [(1,)],
        ]
        con.execute("CREATE TABLE reordered AS SELECT label, id FROM candidate")
        schema_check = con.extract_statements(
            (RECIPE / "scripts/compare_results.sql").read_text()
        )[0]
        con.execute("SET VARIABLE candidate_relation = 'reordered'")
        assert con.execute(schema_check).fetchone()[0] > 0


def test_failed_statement_keeps_evidence_and_cleans_disposable_relation(
    tmp_path: Path,
) -> None:
    sql = tmp_path / "bad.sql"
    sql.write_text("SELECT * FROM missing_input")
    database = tmp_path / "scratch.duckdb"
    output = tmp_path / "failed"
    result = helper(
        "profile.py",
        "--database",
        database,
        "--sql",
        sql,
        "--materialize",
        "--output",
        output,
    )
    assert result.returncode != 0
    record = json.loads((output / "run.json").read_text())
    assert record["status"] == "failed"
    assert record["measurement"] is None
    assert record["cleanup"] == "complete"
    assert (output / "warmup.sql").exists()
    with duckdb.connect(str(database)) as con:
        assert (
            con.execute(
                "SELECT table_name FROM duckdb_tables() WHERE table_name LIKE '__mdopt_%'"
            ).fetchall()
            == []
        )


def test_existing_artifacts_are_not_overwritten(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    (output / "run.json").write_text('{"original": true}\n')
    result = helper(
        "profile.py",
        "--database",
        ":memory:",
        "--sql",
        FIXTURES / "candidate.sql",
        "--output",
        output,
    )
    assert result.returncode != 0
    assert json.loads((output / "run.json").read_text()) == {"original": True}


def test_mutating_input_is_rejected_before_setup_or_connection(tmp_path: Path) -> None:
    sql = tmp_path / "write.sql"
    sql.write_text("DROP TABLE important_table")
    database = tmp_path / "must-not-be-created.duckdb"
    output = tmp_path / "must-not-be-created"
    result = helper(
        "profile.py", "--database", database, "--sql", sql, "--output", output
    )
    assert result.returncode != 0
    assert "exactly one SELECT" in result.stderr
    assert not output.exists()
    assert not database.exists()


def test_motherduck_requires_batch_conditions_without_connecting(
    tmp_path: Path,
) -> None:
    result = helper(
        "profile.py",
        "--database",
        "md:not-a-real-database",
        "--sql",
        FIXTURES / "candidate.sql",
        "--output",
        tmp_path / "remote",
    )
    assert result.returncode != 0
    assert "require --conditions" in result.stderr
    assert not (tmp_path / "remote").exists()


def test_retained_output_is_recorded_for_later_validation(tmp_path: Path) -> None:
    sql = tmp_path / "query.sql"
    sql.write_text("SELECT value: 42")
    database = tmp_path / "retained.duckdb"
    output = tmp_path / "run"
    result = helper(
        "profile.py",
        "--database",
        database,
        "--sql",
        sql,
        "--materialize",
        "--keep-result",
        "--output",
        output,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads((output / "run.json").read_text())
    assert record["cleanup"] == "retained_for_validation"
    with duckdb.connect(str(database)) as con:
        assert con.execute("SELECT * FROM " + record["result_relation"]).fetchall() == [
            (42,)
        ]


def test_inline_environment_token_is_rejected_before_artifact_capture(
    tmp_path: Path,
) -> None:
    fake_token = "fake-experiment-secret-for-test"
    sql = tmp_path / "bad.sql"
    sql.write_text(f"SELECT '{fake_token}'::INTEGER")
    output = tmp_path / "failure"
    result = helper(
        "profile.py",
        "--database",
        ":memory:",
        "--sql",
        sql,
        "--output",
        output,
        env={**os.environ, "MOTHERDUCK_EXPERIMENT_TOKEN": fake_token},
    )
    assert result.returncode != 0
    assert not output.exists()
    assert "credential value" in result.stderr
    assert fake_token not in result.stderr + result.stdout


def test_failure_message_redacts_environment_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_token = "fake-experiment-secret-for-test"
    monkeypatch.setenv("MOTHERDUCK_EXPERIMENT_TOKEN", fake_token)
    module = runpy.run_path(str(RECIPE / "scripts/profile.py"))
    message = module["redact"](
        RuntimeError(f"Connection rejected {fake_token}"), "MOTHERDUCK_EXPERIMENT_TOKEN"
    )
    assert fake_token not in message
    assert "[redacted]" in message


def test_failed_pair_reports_partial_work_without_automatic_retry(
    tmp_path: Path,
) -> None:
    baseline = tmp_path / "baseline.sql"
    candidate = tmp_path / "candidate.sql"
    baseline.write_text("SELECT value: 1")
    candidate.write_text("SELECT * FROM missing_input")
    output = tmp_path / "failed-pair"
    result = helper(
        "benchmark.py",
        "--database",
        ":memory:",
        "--baseline",
        baseline,
        "--candidate",
        candidate,
        "--output",
        output,
    )
    assert result.returncode != 0
    record = json.loads((output / "benchmark.json").read_text())
    assert record["status"] == "failed"
    assert record["runs"] == []
    assert len(record["warmup"]) == 2
    assert record["warmup"][-1]["status"] == "failed"
    assert (output / "report.md").exists()


@pytest.mark.parametrize(
    "age_hours,expected", [(1, "recent_read_recorded"), (25, "complete")]
)
def test_verified_input_read_controls_warmup(
    tmp_path: Path, age_hours: int, expected: str
) -> None:
    sql = tmp_path / "query.sql"
    sql.write_text("SELECT value: 42")
    output = tmp_path / "run"
    read_at = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
    result = helper(
        "profile.py",
        "--database",
        ":memory:",
        "--sql",
        sql,
        "--output",
        output,
        "--mode",
        "timing",
        "--inputs-read-at",
        read_at,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads((output / "run.json").read_text())
    assert record["warmup"]["status"] == expected
    assert (output / "warmup.sql").exists() == (age_hours >= 24)


def test_history_collection_excludes_warmup_and_preserves_total_durations() -> None:
    with duckdb.connect() as con:
        con.execute("CREATE SCHEMA md_information_schema")
        con.execute("""
            CREATE TABLE md_information_schema.query_history AS
            SELECT
                query_id: id, query_text: text, user_name: 'experiment',
                connection_id: 'connection', duckdb_id: 'client', duckdb_version: '1.5.4',
                duckling_id: 'experiment.rw', instance_type: 'standard', query_type: 'QUERY',
                start_time: TIMESTAMPTZ '2026-10-23 12:00:00+00',
                end_time: TIMESTAMPTZ '2026-10-23 12:02:03.456+00',
                execution_time: INTERVAL '2 minutes', wait_time: INTERVAL '3.456 seconds',
                total_elapsed_time: INTERVAL '2 minutes 3.456 seconds',
                bytes_spilled_to_disk: 123, bytes_uploaded: 0, bytes_downloaded: 0,
                error_type: NULL::VARCHAR, error_message: NULL::VARCHAR
            FROM (VALUES
                ('warm', '/* md_opt_run:test md_opt_phase:warmup */ SELECT 42'),
                ('measure', '/* md_opt_run:test md_opt_phase:measure */ SELECT 42'),
                ('other', '/* md_opt_run:other md_opt_phase:measure */ SELECT 42'),
                ('collector', '/* md_opt_run:test md_opt_phase:measure */ FROM md_information_schema.query_history')
            ) queries(id, text)
        """)
        con.execute("SET VARIABLE md_opt_marker = 'md_opt_run:test'")
        result = con.execute((RECIPE / "scripts/collect_history.sql").read_text())
        columns = [column[0] for column in result.description]
        records = [dict(zip(columns, row)) for row in result.fetchall()]
        assert len(records) == 1
        assert records[0]["query_id"] == "measure"
        assert records[0]["total_elapsed_seconds"] == pytest.approx(123.456)
        assert records[0]["wait_seconds"] == pytest.approx(3.456)
        assert records[0]["bytes_spilled_to_disk"] == 123
