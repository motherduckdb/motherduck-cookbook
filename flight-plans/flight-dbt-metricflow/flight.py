"""MotherDuck Flight: query dbt MetricFlow metrics, fetching the project from git.

A Flight runs as a single ``main.py`` in a fresh, torn-down container. Rather than
embed a copy of the dbt project (which would drift from the canonical example),
this Flight **fetches the dbt + MetricFlow project from git at run time** and runs
the ``dbt`` and ``mf`` CLIs against it. Point ``GIT_REPO``/``GIT_REF`` at your own
dbt repo to query your own semantic model — the engine here never changes.

The metric, grouping, and date window are chosen **per run through Flight config**.
A Flight's ``config`` MAP is injected as environment variables; override it per run
with ``MD_RUN_FLIGHT(flight_id := '…', config := MAP {...})`` and the same project
answers a different metric question — no redeploy. (Override changes *values* of
keys that already exist on the Flight; it cannot add new keys.)

The fetched project's own ``profiles.yml`` is used as-is; nothing is written from
scratch. Its MotherDuck target reads the database name from ``MD_DATABASE`` via
dbt's ``env_var()``, so the same committed profile serves every target database.

Each run appends the ``mf query`` result to a snapshot table as a ``JSON`` column,
because the output columns change with the metric/group-by chosen; JSON keeps one
table usable across every run, tagged with ``run_at`` and the config used.
"""

from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import duckdb

log = logging.getLogger("dbt_metricflow")


# ===========================================================================
# Config — every value is overridable per run via the Flight `config` MAP.
# ===========================================================================
def read_config() -> dict[str, str]:
    """Read run config from the environment. Flight `config` keys arrive here as
    env vars; `MD_RUN_FLIGHT(config := MAP {...})` overrides the stored defaults
    for a single run."""
    return {
        # Where the dbt project lives. Point these at your own fork to run your
        # own models; the default is this cookbook's dbt-metricflow example.
        "GIT_REPO": os.environ.get(
            "GIT_REPO", "https://github.com/motherduckdb/motherduck-cookbook.git"
        ),
        "GIT_REF": os.environ.get("GIT_REF", "main"),  # branch or tag
        "REPO_SUBDIR": os.environ.get("REPO_SUBDIR", "dbt-metricflow"),
        # What to query — the per-run knobs.
        "METRICS": os.environ.get("METRICS", "revenue,orders,customers"),
        "GROUP_BY": os.environ.get("GROUP_BY", "metric_time__month"),
        "START_DATE": os.environ.get("START_DATE", "2024-01-01"),
        "END_DATE": os.environ.get("END_DATE", "2024-12-31"),
        # Where results land.
        "MD_DATABASE": os.environ.get("MD_DATABASE", "ecommerce_metrics_flight"),
        "SNAPSHOT_TABLE": os.environ.get("SNAPSHOT_TABLE", "metric_snapshots"),
    }


# ---------------------------------------------------------------------------
# Fetch the dbt project from git (with a tarball fallback if git is absent)
# ---------------------------------------------------------------------------
def fetch_project(cfg: dict[str, str], dest: Path) -> Path:
    """Materialize ``REPO_SUBDIR`` of the repo under ``dest`` and return the path
    to that subdirectory. Prefer a sparse ``git clone``; if no ``git`` binary is
    present, download the ref tarball over HTTPS (stdlib only) so the Flight still
    runs in a minimal container."""
    if shutil.which("git"):
        checkout = _git_sparse_clone(cfg, dest)
    else:
        log.warning("no git binary found — falling back to the HTTPS tarball")
        checkout = _tarball_download(cfg, dest)
    return _locate_subdir(checkout, cfg["REPO_SUBDIR"])


def _git_sparse_clone(cfg: dict[str, str], dest: Path) -> Path:
    """Shallow, blobless, sparse clone of just ``REPO_SUBDIR`` — a few hundred KB."""
    repo = dest / "repo"
    run_cmd(
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
         "--branch", cfg["GIT_REF"], cfg["GIT_REPO"], str(repo)],
        dest, os.environ,
    )
    run_cmd(
        ["git", "-C", str(repo), "sparse-checkout", "set", cfg["REPO_SUBDIR"]],
        dest, os.environ,
    )
    return repo


def _tarball_download(cfg: dict[str, str], dest: Path) -> Path:
    """Download ``<repo>/archive/<ref>.tar.gz`` and extract it under ``dest``,
    returning the single top-level directory GitHub wraps the archive in.
    ``/archive/<ref>`` accepts a branch, tag, or commit SHA."""
    base = cfg["GIT_REPO"].removesuffix(".git")
    url = f"{base}/archive/{cfg['GIT_REF']}.tar.gz"
    log.info("downloading %s", url)
    with urllib.request.urlopen(url) as resp:  # noqa: S310 — fixed https host
        data = resp.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest)  # noqa: S202 — trusted GitHub archive
    tops = [p for p in dest.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise SystemExit(f"unexpected archive layout: {[p.name for p in tops]}")
    return tops[0]


def _locate_subdir(checkout: Path, repo_subdir: str) -> Path:
    """Find ``repo_subdir`` inside the checkout. The git path leaves it at
    ``<repo>/<subdir>``; the tarball path wraps everything in a ``<repo>-<ref>/``
    top dir, so match on the trailing path components rather than a fixed prefix."""
    target = Path(repo_subdir)
    direct = checkout / target
    if direct.is_dir():
        return direct
    parts = target.parts
    for d in checkout.rglob(parts[-1]):
        if d.is_dir() and d.parts[-len(parts):] == parts:
            return d
    raise SystemExit(f"subdirectory {repo_subdir!r} not found in the fetched repo")


def discover(subdir: Path) -> tuple[Path, Path]:
    """Within the fetched ``REPO_SUBDIR``, locate the dbt project dir (holds
    ``dbt_project.yml``) and the profiles dir (the nearest ancestor holding
    ``profiles.yml``). Scoping to ``subdir`` keeps it from matching a sibling
    project elsewhere in the repo (the tarball path extracts the whole repo)."""
    matches = sorted(subdir.rglob("dbt_project.yml"))
    if not matches:
        raise SystemExit(f"no dbt_project.yml found under {subdir}")
    project_dir = matches[0].parent
    for candidate in (project_dir, *project_dir.parents):
        if (candidate / "profiles.yml").exists():
            return project_dir, candidate
        if candidate == subdir:
            break
    raise SystemExit("no profiles.yml found near the dbt project")


# ---------------------------------------------------------------------------
# Running the dbt / MetricFlow CLIs
# ---------------------------------------------------------------------------
def _tool(name: str) -> str:
    """Locate a console script installed by requirements, with a clear error."""
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name!r} not found on PATH — is it in requirements.txt?")
    return path


def run_cmd(cmd: list[str], cwd: Path | str, env: dict[str, str]) -> None:
    """Run a command, streaming its output into the Flight logs."""
    log.info("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)
    if proc.stdout:
        log.info(proc.stdout.rstrip())
    if proc.stderr:
        log.info(proc.stderr.rstrip())
    if proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}")


# ---------------------------------------------------------------------------
# Persisting the result as an append-only snapshot
# ---------------------------------------------------------------------------
def _ident(name: str) -> str:
    """Quote a SQL identifier so a config value cannot break out of its position."""
    return '"' + name.replace('"', '""') + '"'


def append_snapshot(con: duckdb.DuckDBPyConnection, cfg: dict[str, str], csv_path: Path) -> int:
    """Append each result row to the snapshot table as JSON, tagged with the run.

    The aliased subquery (``r``) resolves to a STRUCT of the whole row, which
    ``to_json`` serializes — so the table holds any metric/group-by combination
    without a schema change."""
    db = _ident(cfg["MD_DATABASE"])
    table = _ident(cfg["SNAPSHOT_TABLE"])
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{table} (
            run_at TIMESTAMP,
            metrics VARCHAR,
            group_by VARCHAR,
            start_date VARCHAR,
            end_date VARCHAR,
            result JSON
        )
        """
    )
    con.execute(
        f"""
        INSERT INTO {db}.{table}
        SELECT now(), ?, ?, ?, ?, to_json(r)
        FROM read_csv(?) AS r
        """,
        [cfg["METRICS"], cfg["GROUP_BY"], cfg["START_DATE"], cfg["END_DATE"], str(csv_path)],
    )
    (rows,) = con.execute("SELECT count(*) FROM read_csv(?)", [str(csv_path)]).fetchone()
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = read_config()
    log.info("config: %s", cfg)

    # The Flight runtime injects MOTHERDUCK_TOKEN; dbt-duckdb and the CLIs read it
    # from the environment. Pass the whole environment through to subprocesses.
    env = dict(os.environ)
    env["MD_DATABASE"] = cfg["MD_DATABASE"]  # consumed by the project's profiles.yml env_var()
    env["DBT_TARGET"] = "motherduck"  # the `mf` CLI selects its target from this

    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {_ident(cfg['MD_DATABASE'])}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        subdir = fetch_project(cfg, root)
        project_dir, profiles_dir = discover(subdir)
        log.info("project=%s profiles=%s", project_dir, profiles_dir)
        env["DBT_PROFILES_DIR"] = str(profiles_dir)
        # dbt/MetricFlow write working files under HOME; a Flight's HOME may be
        # read-only, so point it at the writable temp dir.
        env["HOME"] = str(root)

        dbt = _tool("dbt")
        run_cmd([dbt, "seed", "--target", "motherduck"], project_dir, env)
        run_cmd([dbt, "run", "--target", "motherduck"], project_dir, env)

        csv_path = root / "mf_result.csv"
        run_cmd(
            [_tool("mf"), "query",
             "--metrics", cfg["METRICS"],
             "--group-by", cfg["GROUP_BY"],
             "--start-time", cfg["START_DATE"],
             "--end-time", cfg["END_DATE"],
             "--csv", str(csv_path)],
            project_dir, env,
        )
        if not csv_path.exists():
            raise SystemExit("mf query produced no CSV — check the metric/group-by names")
        rows = append_snapshot(con, cfg, csv_path)

    log.info("appended %d row(s) to %s.%s", rows, cfg["MD_DATABASE"], cfg["SNAPSHOT_TABLE"])


if __name__ == "__main__":
    main()
