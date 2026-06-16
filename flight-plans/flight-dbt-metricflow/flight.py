"""MotherDuck Flight: query dbt MetricFlow metrics, fetching the project over HTTPS.

A Flight runs as a single ``main.py`` in a fresh, torn-down container with no git
binary. Rather than embed a copy of the dbt project (which would drift from the
canonical example), this Flight **downloads the dbt + MetricFlow project as a
GitHub archive at run time** (stdlib only — no clone) and runs the ``dbt`` and
``mf`` CLIs against it. Point ``GIT_REPO``/``GIT_REF`` at your own dbt repo to
query your own semantic model; for a private repo, store a token in a MotherDuck
``TYPE flights`` secret (param ``GIT_TOKEN``) and the authenticated GitHub API
archive endpoint is used instead. The engine here never changes.

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
# Fetch the dbt project as a GitHub archive over HTTPS (no git, stdlib only)
# ---------------------------------------------------------------------------
def fetch_project(cfg: dict[str, str], dest: Path) -> Path:
    """Materialize ``REPO_SUBDIR`` of the repo under ``dest`` and return the path
    to that subdirectory. The Flight container ships no git, so we never clone —
    we download the repo as a gzip archive with the stdlib and extract it. Public
    vs private is chosen at run time by whether a ``GIT_TOKEN`` secret resolves."""
    token = resolve_secret("GIT_TOKEN")
    url, headers = _archive_request(cfg, token)
    checkout = _download_and_extract(url, headers, dest)
    return _locate_subdir(checkout, cfg["REPO_SUBDIR"])


def _archive_request(cfg: dict[str, str], token: str) -> tuple[str, dict[str, str]]:
    """Build the archive URL and headers, deciding public vs private at run time.

    Public  -> ``github.com/<owner>/<repo>/archive/<ref>.tar.gz`` (no auth).
    Private -> ``api.github.com/repos/<owner>/<repo>/tarball/<ref>`` with a bearer
    token, which 302-redirects to a short-lived signed download URL. Both accept a
    branch, tag, or commit SHA as ``<ref>``. The token rides in the Authorization
    header — never the URL — so it cannot leak into the Flight logs."""
    base = cfg["GIT_REPO"].removesuffix(".git")
    ref = cfg["GIT_REF"]
    if token:
        owner_repo = base.removeprefix("https://github.com/")
        log.info("fetching private repo %s @ %s", owner_repo, ref)
        url = f"https://api.github.com/repos/{owner_repo}/tarball/{ref}"
        return url, {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    log.info("fetching public repo %s @ %s", base, ref)
    return f"{base}/archive/{ref}.tar.gz", {}


def _download_and_extract(url: str, headers: dict[str, str], dest: Path) -> Path:
    """GET the gzip archive and extract it under ``dest``, returning the single
    top-level directory GitHub wraps every archive in."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:  # noqa: S310 — fixed https github host
        data = resp.read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(dest)  # noqa: S202 — trusted GitHub archive
    tops = [p for p in dest.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise SystemExit(f"unexpected archive layout: {[p.name for p in tops]}")
    return tops[0]


def resolve_secret(param: str) -> str:
    """Resolve a secret param from a MotherDuck ``TYPE flights`` secret. A local
    run sets the bare env var (e.g. ``GIT_TOKEN``); deployed as a Flight, the
    secret injects each param as ``<secret_name>_<PARAM>`` (the lowercased secret
    name becomes a prefix), so accept the exact name first, then any var ending in
    ``_<PARAM>``. Returns ``""`` when neither is set — i.e. a public repo. Mirrors
    resolve_secret_param() in flight-snowflake-ingest."""
    direct = os.environ.get(param, "").strip()
    if direct:
        return direct
    suffix = f"_{param}"
    for key, value in os.environ.items():
        if key.endswith(suffix) and value.strip():
            return value.strip()
    return ""


def _locate_subdir(checkout: Path, repo_subdir: str) -> Path:
    """Find ``repo_subdir`` inside the checkout. GitHub wraps every archive in a
    ``<repo>-<ref>/`` top dir, so the subdir sits at ``<top>/<subdir>`` — match on
    the trailing path components rather than guessing that prefix."""
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
    project elsewhere in the repo (the archive contains the whole repo)."""
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
