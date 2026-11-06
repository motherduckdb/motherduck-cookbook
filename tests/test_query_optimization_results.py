"""Exercise the results ledger, loader and comparison views locally (no MotherDuck needed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb
import pytest

RESULTS = Path(__file__).resolve().parents[1] / "query-optimization" / "results"
sys.path.insert(0, str(RESULTS))
import record_results  # noqa: E402

CAMPAIGN = "generic-campaign"


def run(rid, exp, variant, wall, size="standard", threads=1, purpose="measure", status="succeeded", steps=None):
    return {"type": "run", "run_id": rid, "experiment_id": exp, "variant": variant, "subject": "nightly build",
            "scope": "pipeline", "duckling_size": size, "threads": threads, "purpose": purpose, "status": status,
            "started_at": "2026-01-01T10:00:00Z", "finished_at": "2026-01-01T10:10:00+00:00", "wall_s": wall,
            "steps": steps or []}


@pytest.fixture()
def loaded(tmp_path: Path):
    ledger = tmp_path / "optimization"
    ledger.mkdir()
    records = [
        {"type": "campaign", "campaign_id": CAMPAIGN, "title": "Generic", "headline_subject": "nightly build",
         "headline_size": "standard", "headline_threads": 1, "status": "running"},
        {"type": "experiment", "experiment_id": "e0", "change_kind": "baseline", "decision": "informational",
         "status": "complete"},
        {"type": "experiment", "experiment_id": "e1", "change_kind": "sql_rewrite", "decision": "pending",
         "status": "running"},
        {"type": "experiment", "experiment_id": "e2", "change_kind": "config", "decision": "accepted",
         "validation_status": "passed", "status": "complete", "baseline_experiment_id": "e1"},
        run("e0/a", "e0", "original", 100),
        run("e0/b", "e0", "original", 104),
        run("e0/w", "e0", "original", 300, purpose="warmup"),
        run("e1/a", "e1", "candidate", 60, steps=[
            {"name": "orders", "kind": "model", "started_at": "2026-01-01T10:00:00Z",
             "finished_at": "2026-01-01T10:00:40Z"}]),
        run("e1/oom", "e1", "candidate", None, status="oom"),
        run("e2/a", "e2", "candidate", 61),
        {"type": "event", "event_id": "ev1", "kind": "run", "started_at": "2026-01-01T10:00:00Z",
         "finished_at": "2026-01-01T10:10:00Z"},
        # a later partial update merges into the earlier record
        {"type": "experiment", "experiment_id": "e1", "decision": "accepted", "validation_status": "passed",
         "status": "complete", "reason": "faster"},
    ]
    (ledger / "results.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n")
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS optimization_results")
    con.execute("USE optimization_results")
    con.execute(record_results.SCHEMA.read_text())
    record_results.load(con, ledger, "optimization_results", CAMPAIGN)
    return con, ledger


def test_comparisons_use_experiment_reference_and_original(loaded) -> None:
    con, _ = loaded
    rows = dict(
        (r[0], r[1:])
        for r in con.execute(
            "SELECT experiment_id, baseline_s, baseline_source, delta_pct, vs_original_pct, effect "
            "FROM v_comparisons ORDER BY 1").fetchall()
    )
    assert rows["e1"][0] == 102 and rows["e1"][1] == "original" and rows["e1"][4] == "improved"
    # e2 is measured against e1's candidate, so its own change is neutral but cumulative gain shows
    assert rows["e2"][0] == 60 and rows["e2"][4] == "neutral"
    assert rows["e2"][3] == pytest.approx(100 * (61 - 102) / 102)


def test_warmups_failures_steps_and_merge(loaded) -> None:
    con, _ = loaded
    assert con.execute("SELECT samples FROM v_run_stats WHERE experiment_id = 'e0'").fetchone()[0] == 2
    assert con.execute("SELECT failed_runs FROM v_campaign_summary").fetchone()[0] == 1
    assert con.execute("SELECT duration_s FROM run_steps").fetchone()[0] == 40
    assert con.execute("SELECT decision, reason FROM experiments WHERE experiment_id = 'e1'").fetchone() == ("accepted", "faster")
    assert con.execute("SELECT headline_experiment_id FROM v_campaign_summary").fetchone()[0] == "e1"


def test_decision_checks_flag_contradictions(loaded) -> None:
    con, ledger = loaded
    warnings = record_results.check_decisions(con, CAMPAIGN)
    assert any("e2 is accepted but no comparison is 'improved'" in w for w in warnings)
    assert not any(w.startswith("e1") for w in warnings)


def test_reload_is_idempotent(loaded) -> None:
    con, ledger = loaded
    record_results.load(con, ledger, "optimization_results", CAMPAIGN)
    assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 6


def test_dbt_run_results_become_steps(tmp_path: Path) -> None:
    path = tmp_path / "run_results.json"
    path.write_text(json.dumps({"elapsed_time": 12.5, "results": [
        {"unique_id": "model.proj.orders", "status": "success", "execution_time": 5.0,
         "timing": [{"name": "compile", "started_at": "2026-01-01T10:00:00Z", "completed_at": "2026-01-01T10:00:01Z"},
                    {"name": "execute", "started_at": "2026-01-01T10:00:01Z", "completed_at": "2026-01-01T10:00:05Z"}]}]}))
    steps, fields = record_results.steps_from_dbt(path)
    assert steps[0]["name"] == "orders" and steps[0]["kind"] == "model"
    assert fields["wall_s"] == 12.5 and fields["status"] == "succeeded"


def test_dive_source_points_at_share() -> None:
    content = record_results.dive_content("optimization_results", "md:_share/optimization_results/abc")
    assert "type: 'share', path: 'md:_share/optimization_results/abc', alias: 'optimization_results'" in content


def test_diffs_are_uploaded_unless_disabled(tmp_path: Path) -> None:
    ledger = tmp_path / "optimization"
    (ledger / "experiments" / "e1").mkdir(parents=True)
    (ledger / "experiments" / "e1" / "candidate.diff").write_text("--- a\n+++ b\n-x\n+y\n")
    (ledger / "results.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"type": "campaign", "campaign_id": CAMPAIGN},
        {"type": "experiment", "experiment_id": "e1", "diff_path": "experiments/e1/candidate.diff"},
        {"type": "run", "run_id": "r1", "experiment_id": "e1", "databases": "clone_a", "service_account": "slot_1"},
    ]) + "\n")
    con = duckdb.connect()
    con.execute("ATTACH ':memory:' AS optimization_results")
    con.execute("USE optimization_results")
    con.execute(record_results.SCHEMA.read_text())
    record_results.load(con, ledger, "optimization_results", CAMPAIGN)
    assert con.execute("SELECT diff_text FROM experiments").fetchone()[0].endswith("+y\n")
    assert con.execute("SELECT databases, service_account FROM runs").fetchone() == (["clone_a"], "slot_1")
    record_results.load(con, ledger, "optimization_results", CAMPAIGN, diffs=False)
    assert con.execute("SELECT diff_text FROM experiments").fetchone()[0] is None


def test_benchmark_checkpoint_becomes_runs(tmp_path: Path) -> None:
    import subprocess

    fixtures = Path(__file__).parent / "fixtures" / "query_optimization"
    output = tmp_path / "pair"
    done = subprocess.run(
        [sys.executable, str(RESULTS.parent / "scripts" / "benchmark.py"), "--database", ":memory:",
         "--setup", str(fixtures / "setup.sql"), "--baseline", str(fixtures / "baseline.sql"),
         "--candidate", str(fixtures / "candidate.sql"), "--output", str(output), "--repetitions", "2"],
        capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr + done.stdout
    runs = record_results.runs_from_benchmark(output / "benchmark.json",
                                              {"experiment_id": "e1", "subject": "q", "duckling_size": "standard"})
    measured = [r for r in runs if r["purpose"] == "measure"]
    assert len(measured) == 4 and {r["variant"] for r in measured} == {"baseline", "candidate"}
    assert all(r["status"] == "succeeded" and r["wall_s"] > 0 and r["started_at"] for r in measured)
    assert sorted(r["repetition"] for r in measured) == [1, 1, 2, 2]
    assert len({r["run_id"] for r in runs}) == len(runs)
    as_original = record_results.runs_from_benchmark(output / "benchmark.json", {"experiment_id": "e1"}, True)
    assert {r["variant"] for r in as_original} == {"original", "candidate"}


def test_leaderboard_ranks_adoptable_candidates(loaded) -> None:
    con, _ = loaded
    best = con.execute(
        "SELECT experiment_id, candidate_s FROM v_leaderboard WHERE rank = 1 AND subject = 'nightly build'").fetchone()
    # e1 (accepted, 60 s) beats e2 (accepted, 61 s); the original is never a candidate
    assert best == ("e1", 60)
    assert con.execute("SELECT count(*) FROM v_leaderboard WHERE variant = 'original'").fetchone()[0] == 0
