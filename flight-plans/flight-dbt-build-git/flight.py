"""MotherDuck Flight: run a dbt project on a schedule, snapshotting run results.

The Flights runtime preinstalls a ``git`` binary, so this Flight checks the dbt
project out with git at run time — the git-clone sibling of
flight-dbt-build-gh-archive, which predates preinstalled git and downloads a
GitHub archive over HTTPS instead. A shallow fetch of ``GIT_REF`` (branch, tag, or commit SHA)
materializes the repo, ``dbt build`` runs against MotherDuck, and dbt's own
``run_results.json`` is appended to a snapshot table — one row per node per
run, tagged with the exact commit SHA that was built.

Point ``GIT_REPO``/``GIT_REF``/``REPO_SUBDIR`` at your own dbt repo; any git
host that serves HTTPS works (GitHub, GitLab, Bitbucket, self-hosted). For a
private repo, store a token in a MotherDuck ``TYPE flights`` secret (param
``GIT_TOKEN``); it reaches git through ``GIT_ASKPASS``, so it never appears in
an argv, a URL, or a log line.

Every knob is chosen per run through Flight config, injected as environment
variables; override with ``MD_RUN_FLIGHT(flight_id := '…', config := MAP {...})``
without redeploying. ``RUN_MODE=build`` runs ``dbt build`` (seed+run+test);
``RUN_MODE=test`` runs ``dbt test`` only, for when a separate job owns the build.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import duckdb

log = logging.getLogger("dbt_build_git")


# ===========================================================================
# Config — every value is overridable per run via the Flight `config` MAP.
# ===========================================================================
def read_config() -> dict[str, str]:
    """Read run config from the environment. Flight `config` keys arrive here as
    env vars; `MD_RUN_FLIGHT(config := MAP {...})` overrides the stored defaults
    for a single run."""
    return {
        # Where the dbt project lives. Point these at your own repo to run your
        # own models; the default is this cookbook's dbt-churn-prediction example.
        "GIT_REPO": os.environ.get(
            "GIT_REPO", "https://github.com/motherduckdb/motherduck-cookbook.git"
        ),

        "GIT_REF": os.environ.get("GIT_REF", "main"),  # branch, tag, or commit SHA

        # Username git authenticates with when a GIT_TOKEN secret resolves. A host
        # convention, not a secret: GitHub PATs use "x-access-token", GitLab
        # tokens use "oauth2". Ignored for public repos.
        "GIT_USERNAME": os.environ.get("GIT_USERNAME", "x-access-token"),

        # Path within the repo to the dbt project. Leave empty (or ".") when the
        # repo is the dbt project — i.e. dbt_project.yml sits at the repo root.
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

        "MODELS_DATABASE": os.environ.get("MODELS_DATABASE", "dbt_churn_flight_git"),

        # Optional dbt node selection (e.g. "tag:nightly"). Leave empty to run the
        # whole project (no --select is passed).
        "SELECT": os.environ.get("SELECT", ""),

        # Where the run_results history lands.
        "SNAPSHOT_DATABASE": os.environ.get("SNAPSHOT_DATABASE", "dbt_churn_flight_git"),

        "SNAPSHOT_TABLE": os.environ.get("SNAPSHOT_TABLE", "dbt_run_results"),
    }


# ---------------------------------------------------------------------------
# Check the dbt project out with git (the runtime preinstalls a git binary)
# ---------------------------------------------------------------------------
def fetch_project(cfg: dict[str, str], dest: Path, env: dict[str, str]) -> tuple[Path, str]:
    """Check out ``GIT_REPO`` @ ``GIT_REF`` under ``dest`` and return the dbt
    project subdirectory plus the resolved commit SHA.

    init + fetch + checkout of FETCH_HEAD instead of ``git clone --branch``
    because a fetch refspec accepts a branch, tag, or full commit SHA uniformly
    (``--branch`` rejects SHAs); ``--depth 1`` keeps it shallow either way.
    Public vs private is chosen at run time by whether a ``GIT_TOKEN`` secret
    resolves."""
    git = _tool("git")
    env = dict(env, GIT_TERMINAL_PROMPT="0")  # fail fast; never prompt
    url = cfg["GIT_REPO"]
    token = resolve_secret("GIT_TOKEN")
    if token:
        url = _authenticated_url(url, cfg["GIT_USERNAME"])
        env.update(_askpass_env(dest, token))
    log.info("checking out %s repo %s @ %s", "private" if token else "public", url, cfg["GIT_REF"])

    checkout = dest / "repo"
    run_cmd([git, "init", "-q", str(checkout)], dest, env)
    for args in (
        ["remote", "add", "origin", url],
        ["fetch", "-q", "--depth", "1", "origin", cfg["GIT_REF"]],
        ["checkout", "-q", "--detach", "FETCH_HEAD"],
    ):
        run_cmd([git, "-C", str(checkout), *args], dest, env)

    sha = subprocess.run(
        [git, "-C", str(checkout), "rev-parse", "HEAD"],
        env=env, capture_output=True, text=True, check=True,
    ).stdout.strip()
    log.info("checked out commit %s", sha)
    return _locate_subdir(checkout, cfg["REPO_SUBDIR"]), sha


def _authenticated_url(url: str, username: str) -> str:
    """Embed the auth *username* (never the token) in the HTTPS remote URL; git
    then asks GIT_ASKPASS only for the password. The username is a host
    convention, not a secret, so it is safe in the logged argv."""
    if not url.startswith("https://"):
        raise SystemExit("GIT_TOKEN auth requires an https:// GIT_REPO url")
    return f"https://{username}@{url.removeprefix('https://')}"


def _askpass_env(dest: Path, token: str) -> dict[str, str]:
    """Route the token to git via GIT_ASKPASS: git runs this one-line script for
    its password prompt, and the script reads the token from its environment.
    The token therefore never appears in an argv (run_cmd logs every argv), the
    remote URL, or the on-disk git config."""
    askpass = dest / "git-askpass.sh"
    askpass.write_text('#!/bin/sh\nprintf "%s" "$GIT_PASSWORD"\n')
    askpass.chmod(0o700)
    return {"GIT_ASKPASS": str(askpass), "GIT_PASSWORD": token}


def resolve_secret(param: str) -> str:
    """Resolve a secret param from a MotherDuck ``TYPE flights`` secret. A local
    run sets the bare env var (e.g. ``GIT_TOKEN``); deployed as a Flight, the
    secret injects each param as ``<secret_name>_<PARAM>``, so accept the exact
    name first, then any var ending in ``_<PARAM>``. Returns ``""`` when neither
    is set — i.e. a public repo."""
    direct = os.environ.get(param, "").strip()
    if direct:
        return direct
    suffix = f"_{param}"
    for key, value in os.environ.items():
        if key.endswith(suffix) and value.strip():
            return value.strip()
    return ""


def _locate_subdir(checkout: Path, repo_subdir: str) -> Path:
    """Resolve ``REPO_SUBDIR`` inside the checkout. Unlike a GitHub archive there
    is no wrapper directory — the clone root is the repo root — so the subdir is
    an exact relative path; empty (or ".") means the repo itself is the project."""
    sub = repo_subdir.strip().strip("/")
    if not sub or sub == ".":
        return checkout
    target = checkout / sub
    if not target.is_dir():
        raise SystemExit(f"subdirectory {repo_subdir!r} not found in the checkout")
    return target


def discover(subdir: Path) -> tuple[Path, Path]:
    """Within ``REPO_SUBDIR``, locate the dbt project dir (holds
    ``dbt_project.yml``) and the profiles dir (the nearest ancestor holding
    ``profiles.yml``). Scoping to ``subdir`` keeps it from matching a sibling
    project elsewhere in the repo."""
    matches = sorted(subdir.rglob("dbt_project.yml"))
    if not matches:
        raise SystemExit(f"no dbt_project.yml found under {subdir}")
    project_dir = matches[0].parentdiscover
    for candidate in (project_dir, *project_dir.parents):
        if (candidate / "profiles.yml").exists():
            return project_dir, candidate
        if candidate == subdir:
            break
    raise SystemExit("no profiles.yml found near the dbt project")


# ---------------------------------------------------------------------------
# Running the dbt CLI
# ---------------------------------------------------------------------------
def _tool(name: str) -> str:
    """Locate a binary or console script, with a clear error."""
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name!r} not found on PATH")
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


def append_run_results(
    con: "duckdb.DuckDBPyConnection", cfg: dict[str, str], run_results_path: Path, git_sha: str
) -> list[dict]:
    """Append one row per dbt node to the snapshot table, tagged with the run and
    the exact commit SHA that was built, and return the parsed `results` list.
    Each row keeps the full node as JSON, so the schema survives any
    project/selection; typed columns are pulled out for easy querying. Reading
    via read_text->JSON->json_each tolerates nodes that omit fields
    (rows_affected, failures) without failing the insert."""
    db = _ident(cfg["SNAPSHOT_DATABASE"])
    table = _ident(cfg["SNAPSHOT_TABLE"])
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{table} (
            run_at         TIMESTAMP,
            invocation_id  VARCHAR,
            git_repo       VARCHAR,
            git_ref        VARCHAR,
            git_sha        VARCHAR,
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
            now()::TIMESTAMP,
            doc.j->>'$.metadata.invocation_id',
            ?, ?, ?,
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
        [str(run_results_path), cfg["GIT_REPO"], cfg["GIT_REF"], git_sha, cfg["RUN_MODE"]],
    )
    return json.loads(run_results_path.read_text())["results"]


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = read_config()
    log.info("config: %s", cfg)

    # The Flight runtime injects MOTHERDUCK_TOKEN; dbt-duckdb and duckdb read it
    # from the environment. Pass the whole environment through to subprocesses.
    env = dict(os.environ)
    # Feed the project's profiles.yml env_var() so models land in our chosen db.
    env[cfg["DB_ENV_VAR"]] = cfg["MODELS_DATABASE"]

    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {_ident(cfg['SNAPSHOT_DATABASE'])}")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {_ident(cfg['MODELS_DATABASE'])}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # git and dbt both write working files under HOME; a Flight's HOME may be
        # read-only, so point it at the writable temp dir before either runs.
        env["HOME"] = str(root)

        subdir, git_sha = fetch_project(cfg, root, env)
        project_dir, profiles_dir = subdir
        log.info("project=%s profiles=%s", project_dir, profiles_dir)
        env["DBT_PROFILES_DIR"] = str(profiles_dir)

        dbt = _tool("dbt")
        maybe_deps(dbt, project_dir, env)

        # In build mode, seed first so the seed tables exist before the models run.
        # dbt only orders seeds ahead of models that `ref()` them; a project whose
        # models read the seeds as `source()`s (like the default
        # dbt-churn-prediction) has no such edge, so a cold `dbt build` can run
        # models before seeds and error on the first run against a new database.
        # Priming the seeds makes the first build deterministic. Tolerant on
        # purpose: the build below re-runs seeds and its run_results is the record,
        # so a genuine seed error is still captured and enforced there.
        if cfg["RUN_MODE"].strip().lower() != "test":
            run_cmd([dbt, "seed", "--target", cfg["DBT_TARGET"]], project_dir, env, check=False)

        # Run dbt tolerating a non-zero exit — a test failure must not stop us from
        # recording results. Capture the code, then decide after snapshotting.
        rc = run_cmd(dbt_command(cfg, dbt), project_dir, env, check=False)

        run_results = project_dir / "target" / "run_results.json"
        if not run_results.exists():
            # No results written => dbt failed before executing any node (e.g. a
            # parse/compile error). That is a hard failure with nothing to record.
            raise SystemExit(f"dbt produced no run_results.json (exit {rc}) — check the logs")

        results = append_run_results(con, cfg, run_results, git_sha)
        log.info(
            "snapshotted %d node result(s) to %s.%s",
            len(results), cfg["SNAPSHOT_DATABASE"], cfg["SNAPSHOT_TABLE"],
        )

        # Policy: model/seed/snapshot errors fail the Flight; test failures are
        # recorded but do not (quality drift is a trend, not a page).
        if has_build_error(results):
            raise SystemExit("dbt reported node errors — see the snapshot table and logs")


if __name__ == "__main__":
    main()
