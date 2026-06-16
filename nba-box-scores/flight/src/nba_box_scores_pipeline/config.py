"""CLI and runtime configuration for the NBA ingest pipeline.

Mirrors the legacy `scripts/ingest/config.ts` from the TS pipeline, minus
flags that no longer apply in v3:

- `--concurrency` (already dead in TS — silently consumed legacy flag)
- `--season-concurrency` (Flights run one process per invocation; backfills
  fan out by running the flight multiple times instead)

Database default changes from `nba_box_scores_v2` to `nba_box_scores_v3`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from dataclasses import dataclass, field


SEASON_TYPES = ("Regular Season", "Playoffs")


@dataclass(frozen=True)
class Season:
    year: int
    type: str


@dataclass(frozen=True)
class PipelineConfig:
    seasons: tuple[Season, ...]
    delay_ms: int
    min_delay_ms: int
    max_delay_ms: int
    force: bool
    fill_raw: bool
    dry_run: bool
    verbose: bool
    motherduck_token: str
    database: str = "nba_box_scores_v3"


def current_season_year(today: dt.date | None = None) -> int:
    """NBA season starts in October; before October, current season started last year."""
    today = today or dt.date.today()
    return today.year if today.month >= 10 else today.year - 1


def _season_year(value: str) -> int:
    year = int(value)
    if not 1946 <= year <= 2100:
        raise argparse.ArgumentTypeError(f"invalid season year: {value}")
    return year


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nba-ingest",
        description="NBA ingest pipeline. Mirrors the legacy TS CLI surface.",
    )
    group = parser.add_argument_group("Season selection (at least one required)")
    group.add_argument("--season", type=_season_year, help="Single season (e.g. 2025 for 2025-26)")
    group.add_argument("--from", dest="from_year", type=_season_year, help="Range start (inclusive)")
    group.add_argument("--to", dest="to_year", type=_season_year, help="Range end (inclusive)")
    group.add_argument("--all", action="store_true", help="All seasons from 2000 to current")

    parser.add_argument("--season-type", choices=SEASON_TYPES, default="Regular Season")
    parser.add_argument("--delay", type=int, default=500, help="Base delay between API requests (ms)")
    parser.add_argument("--min-delay", type=int, default=200, help="Adaptive floor (ms)")
    parser.add_argument("--max-delay", type=int, default=10_000, help="Adaptive cap (ms)")
    parser.add_argument("--force", action="store_true", help="Re-ingest even if already logged")
    parser.add_argument("--fill-raw", action="store_true", help="Only fetch raw JSON for games missing it")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without writing to DB")
    parser.add_argument("--verbose", action="store_true", help="Debug-level logging")
    return parser


def build_config(argv: list[str] | None = None, *, env: dict[str, str] | None = None) -> PipelineConfig:
    parser = _build_parser()
    args = parser.parse_args(argv)
    environ = env if env is not None else os.environ

    seasons: list[Season] = []
    if args.all:
        for y in range(2000, current_season_year() + 1):
            seasons.append(Season(year=y, type=args.season_type))
    elif args.from_year is not None and args.to_year is not None:
        if args.from_year > args.to_year:
            parser.error(f"--from ({args.from_year}) must be <= --to ({args.to_year})")
        for y in range(args.from_year, args.to_year + 1):
            seasons.append(Season(year=y, type=args.season_type))
    elif args.from_year is not None or args.to_year is not None:
        parser.error("--from and --to must be used together")
    elif args.season is not None:
        seasons.append(Season(year=args.season, type=args.season_type))
    else:
        parser.error("at least one of --season, --from/--to, or --all is required")

    if args.min_delay > args.max_delay:
        parser.error(f"--min-delay ({args.min_delay}) must be <= --max-delay ({args.max_delay})")
    if not args.min_delay <= args.delay <= args.max_delay:
        parser.error(
            f"--delay ({args.delay}) must be between --min-delay ({args.min_delay}) "
            f"and --max-delay ({args.max_delay})"
        )

    token = environ.get("MOTHERDUCK_TOKEN")
    if not token:
        parser.error("MOTHERDUCK_TOKEN environment variable is required")

    return PipelineConfig(
        seasons=tuple(seasons),
        delay_ms=args.delay,
        min_delay_ms=args.min_delay,
        max_delay_ms=args.max_delay,
        force=args.force,
        fill_raw=args.fill_raw,
        dry_run=args.dry_run,
        verbose=args.verbose,
        motherduck_token=token,
    )


def build_config_from_env(env: dict[str, str] | None = None) -> PipelineConfig:
    """Build a PipelineConfig from env vars only.

    Used inside Flights, where there's no argv. Defaults to ingesting
    the current season's Regular Season + Playoffs in one invocation —
    same coverage as the legacy nightly-sync.yml workflow.

    Recognized env vars:
      MOTHERDUCK_TOKEN — required, injected by Flight runtime
      NBA_INGEST_SEASON — season-start year (default: current)
      NBA_INGEST_DELAY_MS, NBA_INGEST_MIN_DELAY_MS, NBA_INGEST_MAX_DELAY_MS
      NBA_INGEST_FORCE, NBA_INGEST_FILL_RAW, NBA_INGEST_DRY_RUN — '1' enables
    """
    environ = env if env is not None else os.environ
    token = environ.get("MOTHERDUCK_TOKEN")
    if not token:
        raise RuntimeError("MOTHERDUCK_TOKEN is required")

    year = int(environ.get("NBA_INGEST_SEASON") or current_season_year())
    seasons = tuple(Season(year=year, type=st) for st in SEASON_TYPES)

    def _bool(name: str) -> bool:
        return environ.get(name, "").strip() == "1"

    return PipelineConfig(
        seasons=seasons,
        delay_ms=int(environ.get("NBA_INGEST_DELAY_MS") or 500),
        min_delay_ms=int(environ.get("NBA_INGEST_MIN_DELAY_MS") or 200),
        max_delay_ms=int(environ.get("NBA_INGEST_MAX_DELAY_MS") or 10_000),
        force=_bool("NBA_INGEST_FORCE"),
        fill_raw=_bool("NBA_INGEST_FILL_RAW"),
        dry_run=_bool("NBA_INGEST_DRY_RUN"),
        verbose=_bool("NBA_INGEST_VERBOSE"),
        motherduck_token=token,
    )
