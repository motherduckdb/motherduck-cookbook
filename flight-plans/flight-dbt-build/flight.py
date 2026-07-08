"""MotherDuck Flight: run a dbt project on a schedule, snapshotting run results.

A Flight runs as a single ``flight.py`` in a fresh, torn-down container with no
git binary. This Flight **downloads a dbt project as a GitHub archive at run
time** (stdlib only — no clone), runs ``dbt build`` against it on MotherDuck, and
appends dbt's own ``run_results.json`` to a snapshot table — one row per node per
run. A scheduled Flight thus builds a queryable history of build and test health
(per-model status, timing, test pass/fail) over time.

Point ``GIT_REPO``/``GIT_REF``/``REPO_SUBDIR`` at your own dbt repo to run your own
project; for a private repo, store a token in a MotherDuck ``TYPE flights`` secret
(param ``GIT_TOKEN``) and the authenticated GitHub API archive endpoint is used.

Every knob is chosen per run through Flight config, injected as environment
variables; override with ``MD_RUN_FLIGHT(flight_id := '…', config := MAP {...})``
without redeploying. ``RUN_MODE=build`` runs ``dbt build`` (seed+run+test);
``RUN_MODE=test`` runs ``dbt test`` only, for when a separate job owns the build.
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
        # own models; the default is this cookbook's dbt-churn-prediction example.
        "GIT_REPO": os.environ.get(
            "GIT_REPO", "https://github.com/motherduckdb/motherduck-cookbook.git"
        ),
        "GIT_REF": os.environ.get("GIT_REF", "main"),  # branch, tag, or commit SHA
        "REPO_SUBDIR": os.environ.get("REPO_SUBDIR", "dbt-churn-prediction"),
        # How to run. "build" = dbt build (seed+run+test). "test" = dbt test only,
        # for when a separate job already built the tables.
        "RUN_MODE": os.environ.get("RUN_MODE", "build"),
        # dbt target in the project's profiles.yml (root cookbook dbt projects
        # name their MotherDuck target "prod").
        "DBT_TARGET": os.environ.get("DBT_TARGET", "prod"),
        # The project's profile reads its target database from an env var; name it
        # and give it a value. dbt-churn-prediction reads MOTHERDUCK_DATABASE.
        "DB_ENV_VAR": os.environ.get("DB_ENV_VAR", "MOTHERDUCK_DATABASE"),
        "MODELS_DATABASE": os.environ.get("MODELS_DATABASE", "dbt_churn_flight"),
        # Optional dbt node selection (e.g. "tag:nightly"). Empty = whole project.
        "SELECT": os.environ.get("SELECT", ""),
        # Where the run_results history lands.
        "SNAPSHOT_DATABASE": os.environ.get("SNAPSHOT_DATABASE", "dbt_churn_flight"),
        "SNAPSHOT_TABLE": os.environ.get("SNAPSHOT_TABLE", "dbt_run_results"),
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


def run_cmd(cmd: list[str], cwd: Path | str, env: dict[str, str], check: bool = True) -> int:
    """Run a command, streaming its output into the Flight logs. Returns the exit
    code. With check=True (default) a non-zero exit raises; with check=False the
    caller inspects the code (used for dbt, which exits non-zero on test failures
    we still want to record)."""
    log.info("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)
    if proc.stdout:
        log.info(proc.stdout.rstrip())
    if proc.stderr:
        log.info(proc.stderr.rstrip())
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc.returncode


def _truthy(value: str) -> bool:
    """Parse a config string as a boolean (Flight config values are always strings)."""
    return value.strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Persisting the result as an append-only snapshot
# ---------------------------------------------------------------------------
def _ident(name: str) -> str:
    """Quote a SQL identifier so a config value cannot break out of its position."""
    return '"' + name.replace('"', '""') + '"'


def dbt_command(cfg: dict[str, str], dbt: str) -> list[str]:
    """Build the dbt argv from config. RUN_MODE 'test' runs `dbt test` (models
    built elsewhere); anything else runs the full `dbt build`. SELECT scopes the
    nodes when set."""
    verb = "test" if cfg["RUN_MODE"].strip().lower() == "test" else "build"
    cmd = [dbt, verb, "--target", cfg["DBT_TARGET"]]
    if cfg["SELECT"].strip():
        cmd += ["--select", cfg["SELECT"].strip()]
    return cmd


def maybe_deps(dbt: str, project_dir: Path, env: dict[str, str]) -> None:
    """Install dbt packages if the project declares any. The default project has
    none, so this is a no-op there; it generalizes the Flight to projects that do."""
    if (project_dir / "packages.yml").exists() or (project_dir / "dependencies.yml").exists():
        run_cmd([dbt, "deps"], project_dir, env)


def has_build_error(results: list[dict]) -> bool:
    """True if any node errored (a model/seed/snapshot that failed to build, or a
    test that errored). dbt reports a *test* that fails its assertion as status
    'fail' — that is recorded but does NOT count as a build error, so scheduled
    quality drift does not turn the Flight run red. Only 'error' does."""
    return any(r.get("status") == "error" for r in results)


def append_run_results(con: "duckdb.DuckDBPyConnection", cfg: dict[str, str], run_results_path: Path) -> list[dict]:
    """Append one row per dbt node to the snapshot table, tagged with the run, and
    return the parsed `results` list. Each row keeps the full node as JSON, so the
    schema survives any project/selection; typed columns are pulled out for easy
    querying. Reading via read_text->JSON->json_each tolerates nodes that omit
    fields (rows_affected, failures) without failing the insert."""
    db = _ident(cfg["SNAPSHOT_DATABASE"])
    table = _ident(cfg["SNAPSHOT_TABLE"])
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{table} (
            run_at         TIMESTAMP,
            invocation_id  VARCHAR,
            git_repo       VARCHAR,
            git_ref        VARCHAR,
            dbt_version    VARCHAR,
            run_mode       VARCHAR,
            resource_type  VARCHAR,
            unique_id      VARCHAR,
            status         VARCHAR,
            execution_time DOUBLE,
            rows_affected  BIGINT,
            failures       BIGINT,
            message        VARCHAR,
            node           JSON
        )
        """
    )
    con.execute(
        f"""
        INSERT INTO {db}.{table}
        WITH doc AS (SELECT CAST(content AS JSON) AS j FROM read_text(?))
        SELECT
            now(),
            doc.j->>'$.metadata.invocation_id',
            ?, ?,
            doc.j->>'$.metadata.dbt_version',
            ?,
            split_part(r.value->>'unique_id', '.', 1),
            r.value->>'unique_id',
            r.value->>'status',
            TRY_CAST(r.value->>'execution_time' AS DOUBLE),
            TRY_CAST(r.value->'adapter_response'->>'rows_affected' AS BIGINT),
            TRY_CAST(r.value->>'failures' AS BIGINT),
            r.value->>'message',
            r.value
        FROM doc, json_each(doc.j, '$.results') AS r
        """,
        [str(run_results_path), cfg["GIT_REPO"], cfg["GIT_REF"], cfg["RUN_MODE"]],
    )
    (payload,) = con.execute(
        "SELECT CAST(content AS JSON)->'$.results' FROM read_text(?)", [str(run_results_path)]
    ).fetchone()
    import json
    return json.loads(payload) if payload else []


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
        prepare_project(cfg, dbt, project_dir, env)

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
