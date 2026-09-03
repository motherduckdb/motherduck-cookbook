"""MotherDuck Flight: run a SQLMesh project on a schedule, snapshotting each run.

The Flight checks a SQLMesh project out of git at run time (the Flights
runtime preinstalls ``git``), optionally runs a load script from the same
checkout so the raw tables exist, then drives SQLMesh the way a scheduler
would: ``sqlmesh plan <env> --auto-apply --no-prompts`` applies whatever model
changes the commit carries and backfills them, and ``sqlmesh run <env>``
evaluates every model whose ``cron`` is due. SQLMesh keeps
its own state (snapshots, intervals, environments) in the MotherDuck database
it targets, so the Flight itself is stateless and safe to redeploy.

The default project is this cookbook's ``sqlmesh-demo`` example: a dlt loader
pulls Yahoo Finance stock data into ``stock_data.*`` and SQLMesh builds
interim (incremental + SCD type 2), conformed, and mart layers on top. Each
scheduled run is therefore one more dlt load *and* one more SCD snapshot.

Point ``GIT_REPO``/``GIT_REF``/``REPO_SUBDIR`` at your own repo; any git host
that serves HTTPS works. For a private repo, store a token in a MotherDuck
``TYPE flights`` secret (param ``GIT_TOKEN``); it reaches git through
``GIT_ASKPASS``, so it never appears in an argv, a URL, or a log line.

Execution errors fail the run; failed audits are recorded as ``audit_failed``
and keep it green, matching the cookbook's dbt Flight plans.

Every knob is chosen per run through Flight config, injected as environment
variables; override with ``MD_RUN_FLIGHT(flight_id := '…', config := MAP {...})``
without redeploying. Any config key that starts with ``SQLMESH__`` passes
straight through to SQLMesh's own environment-variable config overrides.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import duckdb

log = logging.getLogger("sqlmesh_run_git")

# Keep the last N characters of each step's output in the snapshot row. The full
# output always goes to the Flight logs; the table keeps enough to diagnose.
LOG_TAIL_CHARS = 20_000


# ===========================================================================
# Config — every value is overridable per run via the Flight `config` MAP.
# ===========================================================================
def read_config() -> dict[str, str]:
    """Read run config from the environment. Flight `config` keys arrive here as
    env vars; `MD_RUN_FLIGHT(config := MAP {...})` overrides the stored defaults
    for a single run."""
    return {
        # Where the SQLMesh project lives. Point these at your own repo to run
        # your own models; the default is this cookbook's sqlmesh-demo example.
        "GIT_REPO": os.environ.get(
            "GIT_REPO", "https://github.com/motherduckdb/motherduck-cookbook.git"
        ),

        "GIT_REF": os.environ.get("GIT_REF", "main"),  # branch, tag, or commit SHA

        # Username git authenticates with when a GIT_TOKEN secret resolves. A host
        # convention, not a secret: GitHub PATs use "x-access-token", GitLab
        # tokens use "oauth2". Ignored for public repos.
        "GIT_USERNAME": os.environ.get("GIT_USERNAME", "x-access-token"),

        # Path within the repo to the example root. Leave empty (or ".") when the
        # repo root is the example.
        "REPO_SUBDIR": os.environ.get("REPO_SUBDIR", "sqlmesh-demo"),

        # Path within REPO_SUBDIR to the SQLMesh project (the directory holding
        # config.yaml / config.py). Empty means REPO_SUBDIR itself.
        "SQLMESH_PROJECT": os.environ.get("SQLMESH_PROJECT", "transform"),

        # Optional Python script, relative to REPO_SUBDIR, executed before SQLMesh
        # so the raw tables exist. The default is sqlmesh-demo's dlt loader. Set to
        # empty to skip (for example when another Flight owns ingestion).
        "LOAD_SCRIPT": os.environ.get("LOAD_SCRIPT", "load/stock_data_pipeline.py"),

        # How to run. "plan_run" = `sqlmesh plan --auto-apply` (apply model
        # changes + backfill) then `sqlmesh run` (evaluate due intervals).
        # "run" = `sqlmesh run` only, for when CI already applies plans.
        "RUN_MODE": os.environ.get("RUN_MODE", "plan_run"),

        # The gateway in the project's config.yaml to run through, and the SQLMesh
        # environment to plan/run. sqlmesh-demo defines a gateway named "local".
        "SQLMESH_GATEWAY": os.environ.get("SQLMESH_GATEWAY", "local"),

        "SQLMESH_ENVIRONMENT": os.environ.get("SQLMESH_ENVIRONMENT", "prod"),

        # The MotherDuck database the gateway connects to: raw tables from the
        # load script, SQLMesh's physical/virtual layers, and SQLMesh state all
        # live here. Overrides the database in the project's config.yaml.
        "MODELS_DATABASE": os.environ.get("MODELS_DATABASE", "dlt_test_db"),

        # Where the per-run history lands.
        "SNAPSHOT_DATABASE": os.environ.get("SNAPSHOT_DATABASE", "sqlmesh_flight_git"),

        "SNAPSHOT_TABLE": os.environ.get("SNAPSHOT_TABLE", "sqlmesh_flight_runs"),
    }


# ---------------------------------------------------------------------------
# Check the project out with git (the runtime preinstalls the binary)
# ---------------------------------------------------------------------------
def ensure_git() -> str:
    """Return the path to the ``git`` binary. The Flights runtime preinstalls
    git, so this is a plain PATH lookup with a clear error if it is missing
    (for example on a local machine without git)."""
    return _tool("git")


def fetch_project(cfg: dict[str, str], dest: Path, env: dict[str, str]) -> tuple[Path, str]:
    """Check out ``GIT_REPO`` @ ``GIT_REF`` under ``dest`` and return the
    example subdirectory plus the resolved commit SHA.

    init + fetch + checkout of FETCH_HEAD instead of ``git clone --branch``
    because a fetch refspec accepts a branch, tag, or full commit SHA uniformly
    (``--branch`` rejects SHAs); ``--depth 1`` keeps it shallow either way.
    Public vs private is chosen at run time by whether a ``GIT_TOKEN`` secret
    resolves."""
    git = ensure_git()
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
    """Resolve ``REPO_SUBDIR`` inside the checkout. The clone root is the repo
    root, so the subdir is an exact relative path; empty (or ".") means the repo
    itself is the example."""
    sub = repo_subdir.strip().strip("/")
    if not sub or sub == ".":
        return checkout
    target = checkout / sub
    if not target.is_dir():
        raise SystemExit(f"subdirectory {repo_subdir!r} not found in the checkout")
    return target


def locate_sqlmesh_project(example_dir: Path, project_subdir: str) -> Path:
    """Resolve the SQLMesh project directory (the one holding ``config.yaml``,
    ``config.yml``, or ``config.py``) and fail early with a clear message if it
    is not a SQLMesh project."""
    sub = project_subdir.strip().strip("/")
    project = example_dir if not sub or sub == "." else example_dir / sub
    if not any((project / name).exists() for name in ("config.yaml", "config.yml", "config.py")):
        raise SystemExit(f"no SQLMesh config.yaml/config.yml/config.py found in {project}")
    return project


# ---------------------------------------------------------------------------
# Running command-line tools
# ---------------------------------------------------------------------------
def _tool(name: str) -> str:
    """Locate a binary or console script, with a clear error."""
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name!r} not found on PATH")
    return path


def run_cmd(cmd: list[str], cwd: Path | str, env: dict[str, str], check: bool = True) -> tuple[int, str]:
    """Run a command, streaming its output into the Flight logs. Returns the exit
    code and the combined stdout+stderr text. With check=True (default) a
    non-zero exit raises; with check=False the caller inspects the code (used
    for the SQLMesh steps, whose result we record before deciding to fail)."""
    log.info("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, capture_output=True, text=True)
    output = ""
    if proc.stdout:
        log.info(proc.stdout.rstrip())
        output += proc.stdout
    if proc.stderr:
        log.info(proc.stderr.rstrip())
        output += proc.stderr
    if check and proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc.returncode, output


def _tail(text: str) -> str:
    """Keep the end of a step's output — where the error is — for the snapshot."""
    return text if len(text) <= LOG_TAIL_CHARS else "…" + text[-LOG_TAIL_CHARS:]


# ---------------------------------------------------------------------------
# The steps: optional load, then SQLMesh plan/run
# ---------------------------------------------------------------------------
def sqlmesh_env(cfg: dict[str, str], env: dict[str, str]) -> dict[str, str]:
    """Point the project's gateway at MODELS_DATABASE without editing its
    config.yaml. SQLMesh reads ``SQLMESH__<YAML path with __ separators>`` env
    vars as config overrides, so the gateway's connection.database is set here.
    The token is picked up from ``MOTHERDUCK_TOKEN``, which the runtime injects.
    Any other ``SQLMESH__*`` key in the Flight config is already in ``env`` and
    passes through untouched."""
    gateway = cfg["SQLMESH_GATEWAY"].strip().upper()
    return dict(env, **{
        f"SQLMESH__GATEWAYS__{gateway}__CONNECTION__DATABASE": cfg["MODELS_DATABASE"],
    })


def load_env(cfg: dict[str, str], env: dict[str, str]) -> dict[str, str]:
    """Point a dlt load script at MODELS_DATABASE. dlt reads its MotherDuck
    destination settings from ``DESTINATION__MOTHERDUCK__CREDENTIALS__*`` env
    vars and authenticates with ``MOTHERDUCK_TOKEN``, so the script needs no
    ``.dlt/secrets.toml`` in the checkout. Harmless for a non-dlt script."""
    return dict(env, DESTINATION__MOTHERDUCK__CREDENTIALS__DATABASE=cfg["MODELS_DATABASE"])


def run_load(cfg: dict[str, str], example_dir: Path, env: dict[str, str]) -> tuple[int, str]:
    """Run ``LOAD_SCRIPT`` (a Python file relative to REPO_SUBDIR) so the raw
    tables SQLMesh reads exist and are fresh. Returns (exit code, output); a
    non-zero exit is recorded and then fails the run — SQLMesh cannot do useful
    work on a half-loaded source."""
    script = example_dir / cfg["LOAD_SCRIPT"].strip()
    if not script.is_file():
        raise SystemExit(f"LOAD_SCRIPT {cfg['LOAD_SCRIPT']!r} not found under {example_dir}")
    python = _tool("python3") if shutil.which("python3") else _tool("python")
    return run_cmd([python, str(script)], script.parent, load_env(cfg, env), check=False)


def sqlmesh_base(cfg: dict[str, str], sqlmesh: str, project: Path) -> list[str]:
    """The common prefix of every SQLMesh invocation: project path + gateway."""
    return [sqlmesh, "-p", str(project), "--gateway", cfg["SQLMESH_GATEWAY"]]


def run_plan(cfg: dict[str, str], sqlmesh: str, project: Path, env: dict[str, str]) -> tuple[int, str]:
    """``sqlmesh plan <env> --auto-apply --no-prompts``: apply the model changes
    in this commit to the target environment and backfill them. Non-interactive
    on purpose — a Flight has no terminal — so an uncategorized (ambiguous)
    change fails the plan instead of waiting for a prompt."""
    cmd = sqlmesh_base(cfg, sqlmesh, project) + [
        "plan", cfg["SQLMESH_ENVIRONMENT"], "--auto-apply", "--no-prompts",
    ]
    return run_cmd(cmd, project, env, check=False)


def run_run(cfg: dict[str, str], sqlmesh: str, project: Path, env: dict[str, str]) -> tuple[int, str]:
    """``sqlmesh run <env>``: evaluate every model whose ``cron`` interval is
    due since the last run. Models that are not due are skipped, so scheduling
    this Flight more often than the most frequent model cron is safe."""
    cmd = sqlmesh_base(cfg, sqlmesh, project) + ["run", cfg["SQLMESH_ENVIRONMENT"]]
    return run_cmd(cmd, project, env, check=False)


def sqlmesh_version(sqlmesh: str, env: dict[str, str]) -> str:
    proc = subprocess.run([sqlmesh, "--version"], env=env, capture_output=True, text=True)
    return (proc.stdout or proc.stderr).strip()


# ---------------------------------------------------------------------------
# Persisting the result as an append-only snapshot
# ---------------------------------------------------------------------------
def _ident(name: str) -> str:
    """Quote a SQL identifier so a config value cannot break out of its position."""
    return '"' + name.replace('"', '""') + '"'


# SQLMesh prints each failed node inside a fenced block under "Failed models".
# An audit failure reads "'<audit>' audit error: N rows failed"; an execution
# error leads with the exception class ("BinderException:", "CatalogException:").
AUDIT_ERROR_RE = re.compile(r"' audit error: \d+ rows? failed")
EXEC_ERROR_RE = re.compile(r"^\s*\w+(?:Error|Exception)\b:", re.M)


def classify_failure(output: str) -> str:
    """Decide whether a non-zero SQLMesh exit was *only* failed audits.

    Returns 'audit_failed' when every failed node under "Failed models" reports
    audit errors and nothing else, otherwise 'error'. Unrecognized output is
    'error' on purpose: the fail-safe direction for a scheduled job is red."""
    marker = "Failed models"
    if marker not in output:
        return "error"
    section = output[output.rindex(marker):]
    blocks = re.findall(r"```(.*?)```", section, re.S)
    if not blocks:
        return "error"
    for block in blocks:
        if EXEC_ERROR_RE.search(block) or not AUDIT_ERROR_RE.search(block):
            return "error"
    return "audit_failed"


def _status(rc: int | None, output: str = "") -> str:
    """Map a step's exit code to a status: 'skipped' (not run), 'success',
    'audit_failed' (SQLMesh ran, but blocking audits failed), or 'error'."""
    if rc is None:
        return "skipped"
    if rc == 0:
        return "success"
    return classify_failure(output)


def append_run_row(
    con: "duckdb.DuckDBPyConnection",
    cfg: dict[str, str],
    *,
    git_sha: str,
    version: str,
    started: float,
    steps: dict[str, tuple[int | None, str]],
) -> str:
    """Append one row for this run: what was built (repo/ref/exact SHA, SQLMesh
    version, environment), how each step ended ('success', 'audit_failed',
    'error', or 'skipped'), and the tail of each step's output. Identifiers are
    quoted; every value is a bound parameter. Returns the overall status."""
    db = _ident(cfg["SNAPSHOT_DATABASE"])
    table = _ident(cfg["SNAPSHOT_TABLE"])
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {db}.{table} (
            run_at           TIMESTAMP,
            git_repo         VARCHAR,
            git_ref          VARCHAR,
            git_sha          VARCHAR,
            sqlmesh_version  VARCHAR,
            environment      VARCHAR,
            run_mode         VARCHAR,
            models_database  VARCHAR,
            status           VARCHAR,
            load_status      VARCHAR,
            plan_status      VARCHAR,
            run_status       VARCHAR,
            duration_s       DOUBLE,
            load_output      VARCHAR,
            plan_output      VARCHAR,
            run_output       VARCHAR
        )
        """
    )
    statuses = {name: _status(rc, out) for name, (rc, out) in steps.items()}
    if "error" in statuses.values():
        overall = "error"
    elif "audit_failed" in statuses.values():
        overall = "audit_failed"
    else:
        overall = "success"
    con.execute(
        f"INSERT INTO {db}.{table} VALUES (now()::TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            cfg["GIT_REPO"], cfg["GIT_REF"], git_sha, version,
            cfg["SQLMESH_ENVIRONMENT"], cfg["RUN_MODE"], cfg["MODELS_DATABASE"],
            overall, statuses["load"], statuses["plan"], statuses["run"],
            round(time.monotonic() - started, 3),
            _tail(steps["load"][1]), _tail(steps["plan"][1]), _tail(steps["run"][1]),
        ],
    )
    return overall


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started = time.monotonic()
    cfg = read_config()
    log.info("config: %s", cfg)
    mode = cfg["RUN_MODE"].strip().lower()
    if mode not in ("plan_run", "run"):
        raise SystemExit(f"RUN_MODE must be 'plan_run' or 'run', got {cfg['RUN_MODE']!r}")

    # The Flight runtime injects MOTHERDUCK_TOKEN; SQLMesh, dlt, and duckdb all
    # read it from the environment. Pass the whole environment to subprocesses.
    env = dict(os.environ)

    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {_ident(cfg['SNAPSHOT_DATABASE'])}")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {_ident(cfg['MODELS_DATABASE'])}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # git, dlt (~/.dlt), and SQLMesh (~/.sqlmesh) all write under HOME; a
        # Flight's HOME may be read-only, so point it at the writable temp dir.
        env["HOME"] = str(root)

        example_dir, git_sha = fetch_project(cfg, root, env)
        project = locate_sqlmesh_project(example_dir, cfg["SQLMESH_PROJECT"])
        log.info("example=%s sqlmesh project=%s", example_dir, project)

        sqlmesh = _tool("sqlmesh")
        version = sqlmesh_version(sqlmesh, env)
        log.info("sqlmesh version: %s", version)
        sm_env = sqlmesh_env(cfg, env)

        # Each step tolerates a non-zero exit so the outcome is recorded before
        # the Flight decides. A non-zero step short-circuits the ones after it:
        # there is no point planning on a broken load, and `run` against an
        # environment whose plan did not finalize would wait indefinitely.
        steps: dict[str, tuple[int | None, str]] = {
            "load": (None, ""), "plan": (None, ""), "run": (None, ""),
        }
        if cfg["LOAD_SCRIPT"].strip():
            steps["load"] = run_load(cfg, example_dir, env)
        if steps["load"][0] in (None, 0) and mode == "plan_run":
            steps["plan"] = run_plan(cfg, sqlmesh, project, sm_env)
        if steps["load"][0] in (None, 0) and steps["plan"][0] in (None, 0):
            steps["run"] = run_run(cfg, sqlmesh, project, sm_env)

        overall = append_run_row(
            con, cfg, git_sha=git_sha, version=version, started=started, steps=steps,
        )
        log.info(
            "recorded run (%s) in %s.%s — load=%s plan=%s run=%s",
            overall, cfg["SNAPSHOT_DATABASE"], cfg["SNAPSHOT_TABLE"],
            *(_status(rc, out) for rc, out in steps.values()),
        )

        # Policy (matching the dbt Flight plans): execution errors fail the
        # Flight; audit failures are recorded as 'audit_failed' but keep the run
        # green, because quality drift is a trend to query, not a page. SQLMesh
        # audits are still blocking on their side — the failing model is not
        # promoted, and a plan that fails audits leaves the environment
        # unfinalized, which is why `run` is skipped after any non-success plan.
        if overall == "error":
            raise SystemExit("a SQLMesh step failed — see the snapshot row and logs")
        if overall == "audit_failed":
            log.warning("blocking audits failed — recorded as audit_failed; run stays green")


if __name__ == "__main__":
    main()
