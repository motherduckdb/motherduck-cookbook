"""Bootstrapper for the nba_backfill Flight.

Thin, stable bootstrapper (see flights/nba_nightly/main.py for the full
rationale): clones the motherduck-cookbook repo at a branch, `uv sync`s the pipeline
package, and runs the backfill entrypoint from the synced venv.

Env vars consumed here:
  NBA_FLIGHT_REPO_BRANCH   branch to clone (default: main)
Required by the entrypoint (pass through to the subprocess):
  NBA_BACKFILL_START_SEASON / NBA_BACKFILL_END_SEASON
Plus the usual NBA_INGEST_* / MOTHERDUCK_TOKEN.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/motherduckdb/motherduck-cookbook"
DEFAULT_BRANCH = "main"
REPO_DIR = Path("/app/motherduck-cookbook")
PROJECT_SUBDIR = REPO_DIR / "nba-box-scores" / "flight"
ENTRYPOINT_COMMAND = "backfill"


def sh(args, cwd=None, env=None, check=True):
    # argv list, never shell=True — env-derived values (e.g. the branch) must
    # not be interpolated into a shell string in a process that holds the
    # MotherDuck token.
    print("$ " + " ".join(args), flush=True)
    merged = dict(os.environ)
    merged.update(env or {})
    r = subprocess.run(
        args, cwd=str(cwd) if cwd else None,
        env=merged, capture_output=True, text=True,
    )
    if r.stdout:
        print(r.stdout, flush=True)
    if r.stderr:
        print("STDERR:", r.stderr, flush=True)
    if check and r.returncode != 0:
        raise RuntimeError("command failed rc=" + str(r.returncode) + ": " + " ".join(args))
    return r


def ensure_git():
    if shutil.which("git"):
        return
    sh(["apt-get", "update", "-y"], check=False)
    sh(["apt-get", "install", "-y", "--no-install-recommends", "git", "ca-certificates"])


def main():
    print("python:", sys.version, flush=True)
    branch = os.environ.get("NBA_FLIGHT_REPO_BRANCH", DEFAULT_BRANCH)

    ensure_git()
    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)
    sh(["git", "clone", "--depth", "1", "--branch", branch, REPO_URL, str(REPO_DIR)])
    sh(["git", "log", "-1", "--oneline"], cwd=REPO_DIR)

    # --locked: fail rather than silently re-resolve if uv.lock is stale.
    # --no-dev: runtime doesn't need pytest/pydantic.
    sh(["uv", "sync", "--locked", "--no-dev"], cwd=PROJECT_SUBDIR, env={"UV_LINK_MODE": "copy"})
    sh(
        [".venv/bin/python", "-m", "nba_box_scores_pipeline.entrypoints", ENTRYPOINT_COMMAND],
        cwd=PROJECT_SUBDIR,
    )


if __name__ == "__main__":
    main()
