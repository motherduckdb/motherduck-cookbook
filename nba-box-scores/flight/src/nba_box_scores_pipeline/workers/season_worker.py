"""Per-season ingestion driver.

Ports `scripts/ingest/workers/season-worker.ts`. The TS version ran
concurrent in-flight requests (up to 8) with a separate dispatch chain
gated by the rate limiter. Per the migration plan (§1.2), Flights run as
a single process and don't need that complexity — this is a sync linear
loop. The rate limiter still paces requests; backfills fan out by running
the flight multiple times instead of fanning out within one run.

Retry shape preserved from TS:

- HTTP-level retries happen inside `PBPStatsClient._fetch` (5 attempts,
  exponential backoff, 15s pause on 429)
- This worker adds a per-game retry on top (up to 3 attempts) for errors
  that survive the HTTP layer (parse failures, DB errors)
- Games that exhaust both layers are marked `ingestion_status='error'`
  in `ingestion_log` and the run continues
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ..api.nba import PBPStatsClient
from ..config import PipelineConfig
from ..db.loader import IngestionLogEntry, Loader, ScheduleRow
from ..parsers.nba_box_score import parse_box_score


log = logging.getLogger(__name__)


MAX_GAME_RETRIES = 3


@dataclass
class SeasonProgress:
    season_year: int
    season_type: str
    total_games: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0


def year_to_season(year: int) -> str:
    """Convert a season start year to the PBPStats season string (e.g. 2024 -> '2024-25')."""
    end_year = (year + 1) % 100
    return f"{year}-{end_year:02d}"


def _schedule_row(game: dict[str, Any], season_year: int, season_type: str) -> ScheduleRow:
    return ScheduleRow(
        game_id=game["GameId"],
        game_date=game["Date"],
        home_team_id=int(game["HomeTeamId"]),
        away_team_id=int(game["AwayTeamId"]),
        home_team_abbreviation=game["HomeTeamAbbreviation"],
        away_team_abbreviation=game["AwayTeamAbbreviation"],
        home_team_score=game.get("HomePoints") or 0,
        away_team_score=game.get("AwayPoints") or 0,
        game_status="Final",
        season_year=season_year,
        season_type=season_type,
    )


def _raw_game_payload(game: dict[str, Any], box_score: dict[str, Any]) -> dict[str, Any]:
    """Shape the PBPStats response into the structure `parse_box_score` expects."""
    game_id = game["GameId"]
    return {
        "game": {
            "gameId": game_id,
            "gameDateEst": game["Date"],
            "homeTeam": {
                "teamId": int(game["HomeTeamId"]),
                "teamTricode": game["HomeTeamAbbreviation"],
            },
            "awayTeam": {
                "teamId": int(game["AwayTeamId"]),
                "teamTricode": game["AwayTeamAbbreviation"],
            },
        },
        "boxScore": {
            "stats": box_score["stats"],
        },
    }


def process_season(
    *,
    season_year: int,
    season_type: str,
    client: PBPStatsClient,
    loader: Loader,
    config: PipelineConfig,
) -> SeasonProgress:
    """Fetch and ingest all games for a single (season, season_type) pair."""
    progress = SeasonProgress(season_year=season_year, season_type=season_type)
    season = year_to_season(season_year)
    log.info("processing season=%s type=%s", season, season_type)

    games_response = client.get_games(season, season_type)
    games: list[dict[str, Any]] = games_response.get("results", [])
    progress.total_games = len(games)

    if not games:
        log.info("no games found season=%s type=%s", season, season_type)
        return progress

    # Determine skip set
    if config.force:
        skip_ids: set[str] = set()
    elif config.fill_raw:
        skip_ids = loader.get_raw_game_ids(season_year, season_type)
    else:
        skip_ids = loader.get_ingested_game_ids(season_year, season_type)

    to_process: list[dict[str, Any]] = []
    for game in games:
        if game["GameId"] in skip_ids:
            progress.skipped += 1
        else:
            to_process.append(game)

    if not to_process:
        log.info(
            "all games already covered season=%s type=%s skipped=%d",
            season, season_type, progress.skipped,
        )
        return progress

    log.info(
        "games to process season=%s type=%s total=%d to_process=%d skipped=%d",
        season, season_type, len(games), len(to_process), progress.skipped,
    )

    if config.dry_run:
        return progress

    loader.load_schedule([_schedule_row(g, season_year, season_type) for g in to_process])

    for game in to_process:
        game_id = game["GameId"]
        last_err: Exception | None = None

        for attempt in range(1, MAX_GAME_RETRIES + 1):
            try:
                box_score = client.get_box_score(game_id)
                raw = _raw_game_payload(game, box_score)
                loader.store_raw_pbpstats(
                    game_id=game_id,
                    season_year=season_year,
                    season_type=season_type,
                    game_json=raw["game"],
                    box_score_json=raw["boxScore"]["stats"],
                )

                if not config.fill_raw:
                    rows = parse_box_score(raw)
                    # Replace the whole game atomically (delete + insert + mark)
                    # so a reingest/correction can drop rows that vanished.
                    loader.replace_game(
                        game_id=game_id,
                        rows=rows,
                        log_entry=IngestionLogEntry(
                            game_id=game_id,
                            season_year=season_year,
                            season_type=season_type,
                            ingestion_status="success",
                        ),
                    )

                progress.completed += 1
                last_err = None
                break
            except Exception as err:
                last_err = err
                log.warning(
                    "game failed game_id=%s attempt=%d/%d err=%s",
                    game_id, attempt, MAX_GAME_RETRIES, err,
                )

        if last_err is not None:
            progress.failed += 1
            log.error("game exhausted retries game_id=%s err=%s", game_id, last_err)
            try:
                loader.mark_ingested(IngestionLogEntry(
                    game_id=game_id,
                    season_year=season_year,
                    season_type=season_type,
                    ingestion_status="error",
                    error_message=str(last_err),
                ))
            except Exception as log_err:
                log.error("failed to log ingestion error game_id=%s err=%s", game_id, log_err)

    log.info(
        "season complete season=%s type=%s completed=%d skipped=%d failed=%d total=%d",
        season, season_type, progress.completed, progress.skipped, progress.failed, progress.total_games,
    )
    return progress
