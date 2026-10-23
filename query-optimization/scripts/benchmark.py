"""Run one baseline/candidate pair by default, with warm-up and durable artifacts.

Each invocation uses its own Python process. The caller schedules account slots
and records comparable batch conditions. This helper does not accept changes.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path
from profile import conditions, now, redact, save, warmup_needed


def stats(values: list[float]) -> dict:
    return {
        "count": len(values),
        "samples_seconds": values,
        "minimum_seconds": min(values),
        "median_seconds": statistics.median(values),
        "mean_seconds": statistics.mean(values),
        "maximum_seconds": max(values),
        "sample_stdev_seconds": statistics.stdev(values) if len(values) > 1 else None,
    }


def run(args: argparse.Namespace) -> int:
    configuration = conditions(args.conditions, args.database.startswith("md:"))
    needs_warmup = warmup_needed(args.inputs_read_at)
    if args.repetitions < 1:
        raise ValueError("--repetitions must be positive; default is one pair")
    if args.keep_results and (not args.materialize or args.database == ":memory:"):
        raise ValueError(
            "--keep-results requires --materialize and a persistent experiment database"
        )
    baseline = args.baseline.read_text(encoding="utf-8")
    candidate = args.candidate.read_text(encoding="utf-8")
    setup = args.setup.read_text(encoding="utf-8") if args.setup else ""
    credential_names = {
        args.token_env,
        "MOTHERDUCK_TOKEN",
        "MOTHERDUCK_ADMIN_TOKEN",
        "MOTHERDUCK_OBSERVER_TOKEN",
    }
    snapshot_text = (
        baseline
        + candidate
        + setup
        + args.database
        + str(args.output)
        + json.dumps(configuration)
    )
    if any(
        os.environ.get(name) and os.environ[name] in snapshot_text
        for name in credential_names
    ):
        raise ValueError(
            "SQL, setup, or run settings contain a credential value; use environment-based authentication"
        )
    args.output.mkdir(parents=True, exist_ok=False)
    save(args.output / "conditions.json", configuration)
    (args.output / "baseline.sql").write_text(baseline, encoding="utf-8")
    (args.output / "candidate.sql").write_text(candidate, encoding="utf-8")
    diff_lines = difflib.unified_diff(
        baseline.splitlines(keepends=True),
        candidate.splitlines(keepends=True),
        fromfile="baseline.sql",
        tofile="candidate.sql",
    )
    (args.output / "candidate.diff").write_text(
        "".join(
            line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
            for line in diff_lines
        ),
        encoding="utf-8",
    )
    state = {
        "status": "running",
        "process_id": os.getpid(),
        "started_at": now(),
        "conditions": configuration,
        "repetitions": args.repetitions,
        "warmup": [],
        "runs": [],
        "validation": "not_run",
        "decision": "pending_validation_and_profile_review",
    }
    checkpoint = args.output / "benchmark.json"
    save(checkpoint, state)
    snapshots = {
        "baseline": args.output / "baseline.sql",
        "candidate": args.output / "candidate.sql",
    }

    def invoke(
        variant: str, directory: Path, *, warmup: bool, read_at: str | None
    ) -> dict:
        command = [
            sys.executable,
            str(Path(__file__).with_name("profile.py")),
            "--database",
            args.database,
            "--sql",
            str(snapshots[variant]),
            "--output",
            str(directory),
            "--mode",
            "timing",
            "--conditions",
            str(args.output / "conditions.json"),
            "--token-env",
            args.token_env,
        ]
        if args.setup:
            command += ["--setup", str(args.setup)]
        if args.materialize:
            command.append("--materialize")
        if warmup:
            command.append("--warmup-only")
        elif args.keep_results:
            command.append("--keep-result")
        if read_at:
            command += ["--inputs-read-at", read_at]
        record = {
            "variant": variant,
            "directory": str(directory.relative_to(args.output)),
            "status": "running",
            "command": command,
        }
        state["warmup" if warmup else "runs"].append(record)
        save(checkpoint, state)
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (directory / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        path = directory / "run.json"
        details = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        record.update(
            status=details.get("status", "failed"), exit_code=completed.returncode
        )
        save(checkpoint, state)
        if completed.returncode != 0 or details.get("status") != "complete":
            raise RuntimeError(f"{variant} did not complete; inspect {directory}")
        return details

    failed = False
    try:
        # Warm BOTH input paths before measuring either variant. In-memory runs
        # rebuild inputs per process, so each also warms itself.
        read_at = args.inputs_read_at
        if needs_warmup:
            for variant in snapshots:
                invoke(
                    variant, args.output / "warmup" / variant, warmup=True, read_at=None
                )
            read_at = now()
        for repetition in range(args.repetitions):
            order = (
                ("baseline", "candidate")
                if repetition % 2 == 0
                else ("candidate", "baseline")
            )
            for variant in order:
                details = invoke(
                    variant,
                    args.output / f"pair-{repetition + 1:02d}" / variant,
                    warmup=False,
                    read_at=None if args.database == ":memory:" else read_at,
                )
                state["runs"][-1].update(
                    client_elapsed_seconds=details["measurement"][
                        "client_elapsed_seconds"
                    ],
                    query_id=details["measurement"]["query_id"],
                    result_relation=details["result_relation"],
                    cleanup=details["cleanup"],
                )
                save(checkpoint, state)
        summaries = {
            variant: stats(
                [
                    item["client_elapsed_seconds"]
                    for item in state["runs"]
                    if item["variant"] == variant
                ]
            )
            for variant in snapshots
        }
        state.update(
            status="complete",
            statistics=summaries,
            observed_median_saving_seconds=summaries["baseline"]["median_seconds"]
            - summaries["candidate"]["median_seconds"],
        )
    except BaseException as error:  # noqa: BLE001 - checkpoint failures and interrupts
        failed = True
        state.update(
            status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed",
            error=redact(error, args.token_env),
        )
    finally:
        state.update(finished_at=now(), exit_code=1 if failed else 0)
        save(checkpoint, state)
        lines = [
            "# Benchmark observations",
            "",
            f"Status: {state['status']}",
            "",
            "These are client timings. Validation and profile review remain required.",
            "",
            "| Variant | Samples | Median seconds |",
            "| --- | ---: | ---: |",
        ]
        for variant, summary in state.get("statistics", {}).items():
            lines.append(
                f"| {variant} | {summary['count']} | {summary['median_seconds']:.6f} |"
            )
        lines += [
            "",
            "See benchmark.json and each run.json for raw evidence and retained relations.",
            "",
        ]
        (args.output / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(checkpoint)
    return state["exit_code"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--conditions", type=Path)
    parser.add_argument("--setup", type=Path)
    parser.add_argument("--token-env", default="MOTHERDUCK_EXPERIMENT_TOKEN")
    parser.add_argument("--inputs-read-at")
    parser.add_argument("--materialize", action="store_true")
    parser.add_argument("--keep-results", action="store_true")
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="only increase when repeated measurements were requested",
    )
    args = parser.parse_args()
    try:
        return run(args)
    except (ValueError, TypeError, OSError) as error:
        parser.exit(1, redact(error, args.token_env) + "\n")


if __name__ == "__main__":
    sys.exit(main())
