"""MotherDuck Flight: define metrics once with dbt MetricFlow, query them per run.

A Flight runs as a single ``main.py`` in a fresh, torn-down container, but a dbt
project is many files. So this file *embeds* a small dbt + MetricFlow project as
string constants, materializes it into a temp working directory at run time, then
shells out to the ``dbt`` and ``mf`` CLIs against MotherDuck.

The point is the parameterization. Every knob below is read from an environment
variable, and a Flight's ``config`` MAP is injected as env vars at run time. So
one deployed Flight answers many metric questions: override ``METRICS``,
``GROUP_BY``, ``START_DATE``, or ``END_DATE`` per run with
``MD_RUN_FLIGHT(flight_id := '…', config := MAP {...})`` — no redeploy, no
cloning the project per variant. (Override changes *values* of keys that already
exist on the Flight; it cannot introduce new keys.)

Each run appends the ``mf query`` result to a snapshot table, tagged with the
run timestamp and the exact config used, so a scheduled Flight builds a queryable
time series of metric values. The result is stored as a ``JSON`` column because
the output columns change with the metric/group-by chosen, and JSON keeps the
snapshot table schema-stable across any combination.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
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
        "TARGET_DATABASE": os.environ.get("TARGET_DATABASE", "ecommerce_metrics_flight"),
        "METRICS": os.environ.get("METRICS", "revenue,orders,customers"),
        "GROUP_BY": os.environ.get("GROUP_BY", "metric_time__month"),
        "START_DATE": os.environ.get("START_DATE", "2024-01-01"),
        "END_DATE": os.environ.get("END_DATE", "2024-12-31"),
        "SNAPSHOT_TABLE": os.environ.get("SNAPSHOT_TABLE", "metric_snapshots"),
    }


# ===========================================================================
# The embedded dbt + MetricFlow project. Edit these to model your own metrics.
# ===========================================================================
DBT_PROJECT_YML = """\
name: 'ecommerce_metrics'
version: '1.0.0'
profile: 'ecommerce_metrics'

model-paths: ["models"]
seed-paths: ["seeds"]

models:
  ecommerce_metrics:
    +materialized: table

semantic-models:
  time-spine:
    model: metricflow_time_spine
    time_column: date_day
    granularities: [day]
"""

# profiles.yml is rendered at run time because the target database name is config.
PROFILES_YML_TEMPLATE = """\
ecommerce_metrics:
  target: motherduck
  outputs:
    motherduck:
      type: duckdb
      path: 'md:{database}'
      threads: 4
"""

FCT_ORDERS_SQL = """\
{{ config(materialized='table') }}

SELECT
    order_id,
    customer_id,
    CAST(order_date AS DATE) AS order_date,
    status,
    amount
FROM {{ ref('raw_orders') }}
"""

TIME_SPINE_SQL = """\
{{ config(materialized='table') }}

SELECT CAST(date_day AS DATE) AS date_day
FROM (
    SELECT UNNEST(generate_series(
        DATE '2024-01-01', DATE '2025-12-31', INTERVAL '1 day'
    )) AS date_day
) dates
"""

SEMANTIC_MODELS_YML = """\
time_spines:
  - name: time_spine
    model: ref('metricflow_time_spine')
    time_column: date_day
    grains:
      - name: day
        column: date_day

semantic_models:
  - name: orders
    description: E-commerce order transactions
    model: ref('fct_orders')
    defaults:
      agg_time_dimension: order_date
    entities:
      - name: order_id
        type: primary
        expr: order_id
      - name: customer
        type: foreign
        expr: customer_id
    dimensions:
      - name: order_date
        type: time
        type_params:
          time_granularity: day
      - name: status
        type: categorical
    measures:
      - name: order_count
        agg: count
        expr: order_id
      - name: total_revenue
        agg: sum
        expr: amount
      - name: average_order_value
        agg: average
        expr: amount
      - name: unique_customers
        agg: count_distinct
        expr: customer_id

metrics:
  - name: revenue
    description: Total revenue from all orders
    type: simple
    label: Total Revenue
    type_params:
      measure: total_revenue
  - name: orders
    description: Total number of orders
    type: simple
    label: Order Count
    type_params:
      measure: order_count
  - name: avg_order_value
    description: Average order value
    type: simple
    label: Average Order Value
    type_params:
      measure: average_order_value
  - name: customers
    description: Count of unique customers
    type: simple
    label: Customer Count
    type_params:
      measure: unique_customers
  - name: revenue_per_customer
    description: Average revenue per customer
    type: derived
    label: Revenue Per Customer
    type_params:
      expr: revenue / customers
      metrics:
        - revenue
        - customers
"""

# A thin seed so the Flight runs end-to-end out of the box. Swap for your own
# source (or replace fct_orders.sql with a model over an existing table).
RAW_ORDERS_CSV = """\
order_id,customer_id,order_date,status,amount
1,101,2024-01-15,completed,150.00
2,102,2024-01-16,completed,250.50
3,101,2024-01-17,completed,75.25
4,103,2024-01-18,cancelled,100.00
5,104,2024-01-19,completed,320.00
6,102,2024-01-20,completed,180.75
7,105,2024-01-21,completed,99.99
8,103,2024-01-22,completed,450.00
9,106,2024-01-23,completed,210.30
10,101,2024-01-24,completed,125.50
11,107,2024-02-01,completed,300.00
12,108,2024-02-02,completed,175.25
13,102,2024-02-03,cancelled,50.00
14,109,2024-02-04,completed,425.75
15,104,2024-02-05,completed,89.99
16,110,2024-02-06,completed,650.00
17,105,2024-02-07,completed,115.50
18,111,2024-02-08,completed,275.00
19,103,2024-02-09,completed,340.25
20,112,2024-02-10,completed,199.99
"""


def write_project(root: Path, database: str) -> None:
    """Materialize the embedded dbt project to disk under ``root``."""
    (root / "models").mkdir(parents=True, exist_ok=True)
    (root / "seeds").mkdir(parents=True, exist_ok=True)
    (root / "dbt_project.yml").write_text(DBT_PROJECT_YML)
    (root / "profiles.yml").write_text(PROFILES_YML_TEMPLATE.format(database=database))
    (root / "models" / "fct_orders.sql").write_text(FCT_ORDERS_SQL)
    (root / "models" / "metricflow_time_spine.sql").write_text(TIME_SPINE_SQL)
    (root / "models" / "semantic_models.yml").write_text(SEMANTIC_MODELS_YML)
    (root / "seeds" / "raw_orders.csv").write_text(RAW_ORDERS_CSV)


# ---------------------------------------------------------------------------
# Running the dbt / MetricFlow CLIs
# ---------------------------------------------------------------------------
def _tool(name: str) -> str:
    """Locate a console script installed by requirements, with a clear error."""
    path = shutil.which(name)
    if path is None:
        raise SystemExit(f"{name!r} not found on PATH — is it in requirements.txt?")
    return path


def run_cmd(cmd: list[str], cwd: Path, env: dict[str, str]) -> None:
    """Run a CLI command, streaming its output into the Flight logs."""
    log.info("$ %s", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    if proc.stdout:
        log.info(proc.stdout.rstrip())
    if proc.stderr:
        log.info(proc.stderr.rstrip())
    if proc.returncode != 0:
        raise SystemExit(f"command failed ({proc.returncode}): {' '.join(cmd)}")


def build_models(root: Path, env: dict[str, str]) -> None:
    """Load the seed and build the models so MetricFlow has tables to read."""
    dbt = _tool("dbt")
    run_cmd([dbt, "seed", "--profiles-dir", str(root)], root, env)
    run_cmd([dbt, "run", "--profiles-dir", str(root)], root, env)


def query_metrics(root: Path, cfg: dict[str, str], env: dict[str, str]) -> Path:
    """Run ``mf query`` for the configured metrics, writing a CSV result."""
    out = root / "mf_result.csv"
    cmd = [
        _tool("mf"), "query",
        "--metrics", cfg["METRICS"],
        "--group-by", cfg["GROUP_BY"],
        "--start-time", cfg["START_DATE"],
        "--end-time", cfg["END_DATE"],
        "--csv", str(out),
    ]
    run_cmd(cmd, root, env)
    if not out.exists():
        raise SystemExit("mf query produced no CSV — check the metric/group-by names")
    return out


# ---------------------------------------------------------------------------
# Persisting the result as an append-only snapshot
# ---------------------------------------------------------------------------
def _ident(name: str) -> str:
    """Quote a SQL identifier so a config value cannot break out of its position."""
    return '"' + name.replace('"', '""') + '"'


def append_snapshot(con: duckdb.DuckDBPyConnection, cfg: dict[str, str], csv_path: Path) -> int:
    """Append each result row to the snapshot table as JSON, tagged with the run.

    The result column is JSON because ``mf query`` output columns change with the
    chosen metric/group-by; JSON keeps one table usable across every run. The
    aliased subquery (``r``) resolves to a STRUCT of the whole row, which
    ``to_json`` serializes."""
    db = _ident(cfg["TARGET_DATABASE"])
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
    (rows,) = con.execute(f"SELECT count(*) FROM read_csv(?)", [str(csv_path)]).fetchone()
    return rows


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = read_config()
    log.info("config: %s", cfg)

    # The Flight runtime injects MOTHERDUCK_TOKEN; dbt-duckdb and the CLIs read it
    # from the environment. Pass the whole environment through to the subprocesses.
    env = dict(os.environ)
    env["DBT_TARGET"] = "motherduck"  # the `mf` CLI selects its target from this

    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {_ident(cfg['TARGET_DATABASE'])}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_project(root, cfg["TARGET_DATABASE"])
        env["DBT_PROFILES_DIR"] = str(root)
        # dbt/MetricFlow write working files under HOME; a Flight's HOME may be
        # read-only, so point it at the writable temp dir.
        env["HOME"] = str(root)
        build_models(root, env)
        csv_path = query_metrics(root, cfg, env)
        rows = append_snapshot(con, cfg, csv_path)

    log.info(
        "appended %d row(s) to %s.%s",
        rows, cfg["TARGET_DATABASE"], cfg["SNAPSHOT_TABLE"],
    )


if __name__ == "__main__":
    main()
