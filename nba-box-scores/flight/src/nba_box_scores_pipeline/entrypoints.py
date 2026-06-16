"""Runnable entrypoints for the ingest flights.

These live in the package (not in flights/<name>/main.py) so the flight
bootstrappers can invoke them from the uv-synced venv via:

    python -m nba_box_scores_pipeline.entrypoints nightly
    python -m nba_box_scores_pipeline.entrypoints backfill

The flights/<name>/main.py files are thin bootstrappers: they clone the
repo, `uv sync`, and shell out to the command above. All real work is here
so it's importable and unit-testable.

Config comes entirely from env vars (see config.build_config_from_env and
the per-command notes below) — there's no argv inside a flight.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import replace

from .api.nba import PBPStatsClient
from .config import (
    SEASON_TYPES,
    PipelineConfig,
    Season,
    build_config_from_env,
)
from .db.connection import connect
from .db.loader import Loader
from .db.schema import ensure_tables_suffixed, ensure_views
from .rate_limiter import RateLimiter
from .workers.season_worker import process_season


DATABASE = "nba_box_scores_v3"


def _loader_for(con, suffix: str) -> Loader:
    """Build a Loader over a full table set sharing one name suffix.

    The whole operational set is suffixed together (not just box_scores) so a
    sandbox run's skip/log state stays isolated from production — see
    schema.ensure_tables_suffixed for why this matters.
    """
    return Loader(
        con,
        box_scores_table=f"box_scores{suffix}",
        schedule_table=f"schedule{suffix}",
        ingestion_log_table=f"ingestion_log{suffix}",
        raw_pbpstats_table=f"raw_game_data_pbpstats{suffix}",
    )


def _run(config: PipelineConfig, *, suffix: str, label: str) -> None:
    log = logging.getLogger(label)
    table_set = "production" if not suffix else f"sandbox (suffix '{suffix}')"
    log.info(
        "starting %s database=%s table_set=%s seasons=%s force=%s fill_raw=%s dry_run=%s",
        label, DATABASE, table_set,
        [(s.year, s.type) for s in config.seasons],
        config.force, config.fill_raw, config.dry_run,
    )

    con = connect(DATABASE)
    if config.dry_run:
        # No mutations at all. The loader's skip-reads tolerate missing tables,
        # so the plan is reported even if the (sandbox) table set isn't created.
        log.info("dry_run: no schema changes, no writes")
    else:
        ensure_tables_suffixed(con, suffix)
        # Repoint the prod-facing views only on a real production run — they
        # read the canonical box_scores, and a clone leaves them aimed at v2.
        if not suffix:
            ensure_views(con, db=DATABASE)

    loader = _loader_for(con, suffix)
    rate_limiter = RateLimiter(
        base_delay_ms=config.delay_ms,
        min_delay_ms=config.min_delay_ms,
        max_delay_ms=config.max_delay_ms,
    )

    totals = {"completed": 0, "skipped": 0, "failed": 0}
    with PBPStatsClient(rate_limiter) as client:
        for season in config.seasons:
            progress = process_season(
                season_year=season.year,
                season_type=season.type,
                client=client,
                loader=loader,
                config=config,
            )
            totals["completed"] += progress.completed
            totals["skipped"] += progress.skipped
            totals["failed"] += progress.failed

    log.info(
        "%s complete completed=%d skipped=%d failed=%d",
        label, totals["completed"], totals["skipped"], totals["failed"],
    )


# Default table-set suffix per command. Both nightly and backfill default to
# the production table set (empty suffix). Set NBA_INGEST_TABLE_SUFFIX to a
# value like "_new" to write an isolated sandbox set for validation/diffing
# ("" = production).
def _suffix(default: str) -> str:
    return os.environ.get("NBA_INGEST_TABLE_SUFFIX", default)


def run_nightly() -> None:
    """Current-season ingest (Regular Season + Playoffs).

    Writes the production table set by default. Set NBA_INGEST_TABLE_SUFFIX
    (e.g. "_new") to write an isolated sandbox set for validation instead.
    """
    config = build_config_from_env()
    _run(config, suffix=_suffix(""), label="nba_nightly")


def run_backfill() -> None:
    """On-demand historical backfill across a season range.

    Requires NBA_BACKFILL_START_SEASON and NBA_BACKFILL_END_SEASON.
    Targets the production table set by default.
    """
    start = os.environ.get("NBA_BACKFILL_START_SEASON")
    end = os.environ.get("NBA_BACKFILL_END_SEASON")
    if not start or not end:
        raise RuntimeError("NBA_BACKFILL_START_SEASON and NBA_BACKFILL_END_SEASON are required")
    start_year, end_year = int(start), int(end)
    if start_year > end_year:
        raise RuntimeError(f"start ({start_year}) must be <= end ({end_year})")

    seasons = tuple(
        Season(year=y, type=st)
        for y in range(start_year, end_year + 1)
        for st in SEASON_TYPES
    )
    config = replace(build_config_from_env(), seasons=seasons)
    _run(config, suffix=_suffix(""), label="nba_backfill")


_COMMANDS = {"nightly": run_nightly, "backfill": run_backfill}


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1 or argv[0] not in _COMMANDS:
        raise SystemExit(f"usage: python -m nba_box_scores_pipeline.entrypoints {{{'|'.join(_COMMANDS)}}}")
    _COMMANDS[argv[0]]()


if __name__ == "__main__":
    main()
