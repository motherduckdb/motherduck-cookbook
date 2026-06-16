"""Idempotency tests for the loader.

Runs against an in-memory DuckDB instance so each test is hermetic and
fast. The schema DDL is identical to what runs against MotherDuck — the
primary keys are what give `INSERT OR REPLACE` its idempotency, and they
work the same locally.

These tests are the contract that protects Flight retries: a Flight that
dies mid-run and is restarted must converge to the same end state, not
duplicate rows.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from nba_box_scores_pipeline.db.loader import (
    IngestionLogEntry,
    Loader,
    ScheduleRow,
)
from nba_box_scores_pipeline.db.schema import (
    ensure_tables,
    ensure_tables_suffixed,
    ensure_views,
)
from nba_box_scores_pipeline.parsers.nba_box_score import parse_box_score


FIXTURES = Path(__file__).parent / "fixtures"


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
def regulation_rows():
    return parse_box_score(json.loads((FIXTURES / "0022400061.json").read_text()))


def _count(con, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]


class TestReplaceGame:
    """replace_game makes the freshly parsed set canonical — unlike a bare
    INSERT OR REPLACE, it removes rows that are no longer present."""

    def test_replace_drops_vanished_rows(self, con, loader, regulation_rows):
        full = list(regulation_rows)
        loader.load_box_scores(full)
        assert _count(con, "box_scores") == len(full)

        game_id = full[0].game_id
        fewer = [r for r in full if r is not full[-1]]  # drop one row
        loader.replace_game(
            game_id=game_id,
            rows=fewer,
            log_entry=IngestionLogEntry(game_id=game_id, season_year=2024, season_type="Regular Season"),
        )
        assert _count(con, "box_scores") == len(fewer)
        assert loader.is_game_ingested(game_id)

    def test_empty_rows_fails_closed(self, con, loader, regulation_rows):
        full = list(regulation_rows)
        loader.load_box_scores(full)
        game_id = full[0].game_id
        # An empty parse (not-ready payload) must NOT wipe the game or mark success.
        with pytest.raises(ValueError):
            loader.replace_game(
                game_id=game_id,
                rows=[],
                log_entry=IngestionLogEntry(game_id=game_id, season_year=2024, season_type="Regular Season"),
            )
        assert _count(con, "box_scores") == len(full)
        assert not loader.is_game_ingested(game_id)


class TestBoxScoreIdempotency:
    def test_first_load_writes_all_rows(self, con, loader, regulation_rows):
        loader.load_box_scores(regulation_rows)
        assert _count(con, "box_scores") == len(regulation_rows)

    def test_reload_same_rows_is_a_noop(self, con, loader, regulation_rows):
        loader.load_box_scores(regulation_rows)
        first = _count(con, "box_scores")
        loader.load_box_scores(regulation_rows)
        assert _count(con, "box_scores") == first

    def test_reload_with_changed_stat_overwrites(self, con, loader, regulation_rows):
        loader.load_box_scores(regulation_rows)
        # Mutate one row's points and reload — same PK should overwrite
        target = regulation_rows[0]
        original_points = target.points
        target.points = original_points + 99
        loader.load_box_scores([target])
        (new_points,) = con.execute(
            "SELECT points FROM main.box_scores WHERE game_id = ? AND entity_id = ? AND period = ?",
            [target.game_id, target.entity_id, target.period],
        ).fetchone()
        assert new_points == original_points + 99
        # And the total row count hasn't changed
        assert _count(con, "box_scores") == len(regulation_rows)

    def test_partial_reload_does_not_lose_other_rows(self, con, loader, regulation_rows):
        loader.load_box_scores(regulation_rows)
        before = _count(con, "box_scores")
        # Replay only the first 10 rows — the other ~80 should remain
        loader.load_box_scores(regulation_rows[:10])
        assert _count(con, "box_scores") == before

    def test_empty_load_is_noop(self, con, loader):
        loader.load_box_scores([])
        assert _count(con, "box_scores") == 0


class TestScheduleIdempotency:
    def test_reload_same_schedule_does_not_duplicate(self, con, loader):
        row = ScheduleRow(
            game_id="0022400061",
            game_date="2024-10-22 19:30:00",
            home_team_id=1610612738,
            away_team_id=1610612752,
            home_team_abbreviation="BOS",
            away_team_abbreviation="NYK",
            home_team_score=132,
            away_team_score=109,
            game_status="Final",
            season_year=2024,
            season_type="Regular Season",
        )
        loader.load_schedule([row])
        loader.load_schedule([row])
        assert _count(con, "schedule") == 1

    def test_changed_score_overwrites(self, con, loader):
        row = ScheduleRow(
            game_id="0022400061",
            game_date="2024-10-22 19:30:00",
            home_team_id=1610612738,
            away_team_id=1610612752,
            home_team_abbreviation="BOS",
            away_team_abbreviation="NYK",
            home_team_score=0,
            away_team_score=0,
            game_status="Scheduled",
            season_year=2024,
            season_type="Regular Season",
        )
        loader.load_schedule([row])
        row.home_team_score = 132
        row.away_team_score = 109
        row.game_status = "Final"
        loader.load_schedule([row])
        (status, home, away) = con.execute(
            "SELECT game_status, home_team_score, away_team_score FROM main.schedule WHERE game_id = ?",
            [row.game_id],
        ).fetchone()
        assert (status, home, away) == ("Final", 132, 109)


class TestIngestionLog:
    def test_mark_ingested_then_query(self, loader):
        loader.mark_ingested(IngestionLogEntry(
            game_id="0022400061", season_year=2024, season_type="Regular Season",
        ))
        assert loader.is_game_ingested("0022400061")
        assert not loader.is_game_ingested("0022400062")
        assert loader.get_ingested_game_ids(2024, "Regular Season") == {"0022400061"}
        # Different season filters cleanly
        assert loader.get_ingested_game_ids(2024, "Playoffs") == set()

    def test_remark_overwrites_status(self, con, loader):
        loader.mark_ingested(IngestionLogEntry(
            game_id="0022400061", season_year=2024, season_type="Regular Season",
            ingestion_status="failed", error_message="api timeout",
        ))
        loader.mark_ingested(IngestionLogEntry(
            game_id="0022400061", season_year=2024, season_type="Regular Season",
            ingestion_status="success",
        ))
        assert _count(con, "ingestion_log") == 1
        assert loader.is_game_ingested("0022400061")


class TestRawPbpstats:
    def test_reload_same_raw_does_not_duplicate(self, con, loader):
        payload = {"hello": "world"}
        loader.store_raw_pbpstats(
            game_id="0022400061", season_year=2024, season_type="Regular Season",
            game_json=payload, box_score_json=payload,
        )
        loader.store_raw_pbpstats(
            game_id="0022400061", season_year=2024, season_type="Regular Season",
            game_json=payload, box_score_json=payload,
        )
        assert _count(con, "raw_game_data_pbpstats") == 1

    def test_get_raw_game_ids_filters_by_season(self, loader):
        for game_id, season in [("a", 2023), ("b", 2024), ("c", 2024)]:
            loader.store_raw_pbpstats(
                game_id=game_id, season_year=season, season_type="Regular Season",
                game_json={}, box_score_json={},
            )
        assert loader.get_raw_game_ids(2024, "Regular Season") == {"b", "c"}
        assert loader.get_raw_game_ids(2023, "Regular Season") == {"a"}
        assert loader.get_raw_game_ids(2024, "Playoffs") == set()


class TestSandboxTableSet:
    """Validation path: a sandbox run writes an isolated `_new` table set and
    leaves the entire production set untouched — box scores AND skip/log state.
    This is the P1 fix: a sandbox run must not mark success in the production
    ingestion_log (else a later prod flip skips those games)."""

    def test_ensure_tables_suffixed_creates_full_set(self, con):
        ensure_tables_suffixed(con, "_new")
        for name in ("box_scores_new", "schedule_new", "ingestion_log_new", "raw_game_data_pbpstats_new"):
            assert _count(con, name) == 0  # exists and empty

    def test_suffixed_box_scores_keeps_primary_key(self, con, regulation_rows):
        # Reusing canonical DDL means the sandbox table keeps the PK, so
        # INSERT OR REPLACE has a conflict target (reload is idempotent).
        ensure_tables_suffixed(con, "_new")
        sandbox = Loader(con, box_scores_table="box_scores_new")
        sandbox.load_box_scores(regulation_rows)
        sandbox.load_box_scores(regulation_rows)
        assert _count(con, "box_scores_new") == len(regulation_rows)
        assert _count(con, "box_scores") == 0

    def test_sandbox_log_isolated_from_production(self, con):
        ensure_tables_suffixed(con, "_new")
        sandbox = Loader(
            con,
            box_scores_table="box_scores_new",
            schedule_table="schedule_new",
            ingestion_log_table="ingestion_log_new",
            raw_pbpstats_table="raw_game_data_pbpstats_new",
        )
        sandbox.mark_ingested(IngestionLogEntry(
            game_id="0099999999", season_year=2024, season_type="Regular Season",
        ))
        # Sandbox run recorded success in its own log...
        assert sandbox.is_game_ingested("0099999999")
        # ...but the production loader must NOT see it (else prod flip skips it).
        prod = Loader(con)
        assert not prod.is_game_ingested("0099999999")
        assert _count(con, "ingestion_log") == 0


class TestSkipReadsTolerateMissingTables:
    """A dry run performs no schema bootstrap; skip-reads against a not-yet-
    created (sandbox) table set must return empty rather than raising."""

    def test_reads_on_absent_tables_return_empty(self):
        bare = duckdb.connect(":memory:")  # no tables at all
        try:
            loader = Loader(
                bare,
                ingestion_log_table="ingestion_log_new",
                raw_pbpstats_table="raw_game_data_pbpstats_new",
            )
            assert loader.get_ingested_game_ids(2024, "Regular Season") == set()
            assert loader.get_raw_game_ids(2024, "Regular Season") == set()
            assert loader.is_game_ingested("0022400061") is False
        finally:
            bare.close()


class TestViewsRebind:
    """Cloned databases inherit view DDL pointing at the source DB; ensure_views fixes that."""

    def test_views_compile_against_active_db(self, con, regulation_rows, loader):
        loader.load_box_scores(regulation_rows)
        # In-memory DuckDB names the database "memory"
        ensure_views(con, db="memory")
        # team_stats should aggregate the box_scores we loaded
        teams = con.execute(
            "SELECT DISTINCT team_abbreviation FROM main.team_stats ORDER BY team_abbreviation"
        ).fetchall()
        assert [t[0] for t in teams] == ["BOS", "NYK"]
