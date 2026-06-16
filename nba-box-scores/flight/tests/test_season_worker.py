"""Tests for the season-worker orchestration loop.

Uses a fake PBPStats client (duck-typed; no network) and an in-memory
DuckDB. Exercises skip-on-retry, force, fill-raw, dry-run, and the
per-game retry path with a fault-injecting client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from nba_box_scores_pipeline.config import PipelineConfig, Season
from nba_box_scores_pipeline.db.loader import Loader
from nba_box_scores_pipeline.db.schema import ensure_tables
from nba_box_scores_pipeline.workers.season_worker import (
    process_season,
    year_to_season,
)


FIXTURES = Path(__file__).parent / "fixtures"
SEASON_YEAR = 2024
SEASON_TYPE = "Regular Season"


def _load_fixture(game_id: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{game_id}.json").read_text())


def _games_list_entry(fixture: dict[str, Any]) -> dict[str, Any]:
    """Synthesize a PBPStats games-list entry from a stored fixture's `game` block."""
    g = fixture["game"]
    return {
        "GameId": g["gameId"],
        "Date": g["gameDateEst"],
        "HomeTeamId": str(g["homeTeam"]["teamId"]),
        "AwayTeamId": str(g["awayTeam"]["teamId"]),
        "HomeTeamAbbreviation": g["homeTeam"]["teamTricode"],
        "AwayTeamAbbreviation": g["awayTeam"]["teamTricode"],
        "HomePoints": 0,
        "AwayPoints": 0,
    }


class FakeClient:
    """Duck-typed stand-in for PBPStatsClient.

    `games` is the list returned by get_games. `box_scores` maps game_id → response.
    `fail_box_score_on` is a set of game_ids that should raise on every box-score fetch.
    """

    def __init__(
        self,
        *,
        games: list[dict[str, Any]],
        box_scores: dict[str, dict[str, Any]],
        fail_box_score_on: set[str] | None = None,
    ) -> None:
        self.games = games
        self.box_scores = box_scores
        self.fail_box_score_on = fail_box_score_on or set()
        self.box_score_calls = 0
        self.games_calls = 0

    def get_games(self, season: str, season_type: str) -> dict[str, Any]:
        self.games_calls += 1
        return {"results": self.games}

    def get_box_score(self, game_id: str) -> dict[str, Any]:
        self.box_score_calls += 1
        if game_id in self.fail_box_score_on:
            raise RuntimeError(f"injected failure for {game_id}")
        return self.box_scores[game_id]


def _config(**overrides: Any) -> PipelineConfig:
    defaults: dict[str, Any] = dict(
        seasons=(Season(year=SEASON_YEAR, type=SEASON_TYPE),),
        delay_ms=500,
        min_delay_ms=200,
        max_delay_ms=10_000,
        force=False,
        fill_raw=False,
        dry_run=False,
        verbose=False,
        motherduck_token="test-token",
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    ensure_tables(c)
    yield c
    c.close()


@pytest.fixture
def loader(con):
    return Loader(con)


@pytest.fixture
def one_game_fixtures():
    fixture = _load_fixture("0022400061")
    return {
        "games": [_games_list_entry(fixture)],
        "box_scores": {"0022400061": {"stats": fixture["boxScore"]["stats"]}},
    }


@pytest.fixture
def two_game_fixtures():
    f1 = _load_fixture("0022400061")
    f2 = _load_fixture("0022400071")
    return {
        "games": [_games_list_entry(f1), _games_list_entry(f2)],
        "box_scores": {
            "0022400061": {"stats": f1["boxScore"]["stats"]},
            "0022400071": {"stats": f2["boxScore"]["stats"]},
        },
    }


def _count(con, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]


class TestYearToSeason:
    def test_basic(self):
        assert year_to_season(2024) == "2024-25"
        assert year_to_season(2000) == "2000-01"

    def test_century_rollover(self):
        assert year_to_season(2099) == "2099-00"


class TestHappyPath:
    def test_one_game_writes_everything(self, con, loader, one_game_fixtures):
        client = FakeClient(**one_game_fixtures)
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(),
        )

        assert progress.total_games == 1
        assert progress.completed == 1
        assert progress.failed == 0
        assert progress.skipped == 0
        assert _count(con, "schedule") == 1
        assert _count(con, "raw_game_data_pbpstats") == 1
        assert _count(con, "ingestion_log") == 1
        # 91 box-score rows for this regulation game (verified in test_parsers)
        assert _count(con, "box_scores") == 91
        assert loader.is_game_ingested("0022400061")

    def test_two_games(self, con, loader, two_game_fixtures):
        client = FakeClient(**two_game_fixtures)
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(),
        )
        assert progress.completed == 2
        assert _count(con, "schedule") == 2
        assert client.box_score_calls == 2


class TestSkipOnRetry:
    def test_already_ingested_games_are_skipped(self, con, loader, two_game_fixtures):
        # Pre-mark game 1 as ingested
        from nba_box_scores_pipeline.db.loader import IngestionLogEntry
        loader.mark_ingested(IngestionLogEntry(
            game_id="0022400061", season_year=SEASON_YEAR, season_type=SEASON_TYPE,
        ))

        client = FakeClient(**two_game_fixtures)
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(),
        )
        assert progress.skipped == 1
        assert progress.completed == 1
        # Only the non-skipped game had its box score fetched
        assert client.box_score_calls == 1

    def test_force_ignores_skip_set(self, con, loader, one_game_fixtures):
        from nba_box_scores_pipeline.db.loader import IngestionLogEntry
        loader.mark_ingested(IngestionLogEntry(
            game_id="0022400061", season_year=SEASON_YEAR, season_type=SEASON_TYPE,
        ))

        client = FakeClient(**one_game_fixtures)
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(force=True),
        )
        assert progress.skipped == 0
        assert progress.completed == 1
        assert client.box_score_calls == 1


class TestFillRaw:
    def test_fill_raw_skips_box_scores_and_log(self, con, loader, one_game_fixtures):
        client = FakeClient(**one_game_fixtures)
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(fill_raw=True),
        )
        assert progress.completed == 1
        assert _count(con, "raw_game_data_pbpstats") == 1
        # fill_raw skips both hydration and the ingestion_log entry
        assert _count(con, "box_scores") == 0
        assert _count(con, "ingestion_log") == 0

    def test_fill_raw_skip_set_uses_raw_not_log(self, con, loader, two_game_fixtures):
        # Game 1 already has raw stored; game 2 is fresh
        loader.store_raw_pbpstats(
            game_id="0022400061", season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            game_json={}, box_score_json={},
        )
        client = FakeClient(**two_game_fixtures)
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(fill_raw=True),
        )
        assert progress.skipped == 1
        assert progress.completed == 1


class TestDryRun:
    def test_dry_run_writes_nothing(self, con, loader, one_game_fixtures):
        client = FakeClient(**one_game_fixtures)
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(dry_run=True),
        )
        # Progress reports total/skipped but no rows are written
        assert progress.total_games == 1
        assert _count(con, "schedule") == 0
        assert _count(con, "box_scores") == 0
        assert _count(con, "ingestion_log") == 0
        # Box score was never even fetched
        assert client.box_score_calls == 0


class TestFailures:
    def test_failed_game_marked_as_error_others_continue(self, con, loader, two_game_fixtures):
        client = FakeClient(
            **two_game_fixtures,
            fail_box_score_on={"0022400061"},
        )
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(),
        )
        assert progress.completed == 1
        assert progress.failed == 1
        # The failing game retried up to MAX_GAME_RETRIES
        from nba_box_scores_pipeline.workers.season_worker import MAX_GAME_RETRIES
        # 3 retries on failing game + 1 success on the other = 4 box-score calls
        assert client.box_score_calls == MAX_GAME_RETRIES + 1

        # Failure is logged in ingestion_log with status='error'
        (status, err) = con.execute(
            "SELECT ingestion_status, error_message FROM main.ingestion_log WHERE game_id = ?",
            ["0022400061"],
        ).fetchone()
        assert status == "error"
        assert "injected failure" in err

        # The successful game has a normal log entry
        assert loader.is_game_ingested("0022400071")


class TestEmptySeason:
    def test_no_games_returns_zeros(self, con, loader):
        client = FakeClient(games=[], box_scores={})
        progress = process_season(
            season_year=SEASON_YEAR, season_type=SEASON_TYPE,
            client=client, loader=loader, config=_config(),
        )
        assert progress.total_games == 0
        assert progress.completed == 0
        assert client.box_score_calls == 0
