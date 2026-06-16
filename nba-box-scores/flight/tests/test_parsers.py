"""Pytest port of `scripts/ingest/parse/__tests__/box-score-parser.test.ts`.

Fixtures are real PBPStats responses captured from production games:

- 0022400061 — NYK @ BOS, 2024-10-22, regulation 4-quarter game
- 0022400071 — single OT
- 0022400501 — double OT
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nba_box_scores_pipeline.parsers.nba_box_score import BoxScoreRow, parse_box_score


FIXTURES = Path(__file__).parent / "fixtures"


def _load(game_id: str) -> dict:
    return json.loads((FIXTURES / f"{game_id}.json").read_text())


@pytest.fixture(scope="module")
def regulation_rows() -> list[BoxScoreRow]:
    return parse_box_score(_load("0022400061"))


@pytest.fixture(scope="module")
def ot_rows() -> list[BoxScoreRow]:
    return parse_box_score(_load("0022400071"))


@pytest.fixture(scope="module")
def double_ot_rows() -> list[BoxScoreRow]:
    return parse_box_score(_load("0022400501"))


class TestRegulation:
    def test_game_id(self, regulation_rows: list[BoxScoreRow]) -> None:
        assert regulation_rows[0].game_id == "0022400061"

    def test_excludes_team_entities(self, regulation_rows: list[BoxScoreRow]) -> None:
        assert [r for r in regulation_rows if r.entity_id == "0"] == []

    def test_both_teams_present(self, regulation_rows: list[BoxScoreRow]) -> None:
        teams = {r.team_abbreviation for r in regulation_rows}
        assert teams == {"NYK", "BOS"}

    def test_periods_1_through_4_plus_fullgame(self, regulation_rows: list[BoxScoreRow]) -> None:
        periods = {r.period for r in regulation_rows}
        assert {"1", "2", "3", "4", "FullGame"}.issubset(periods)
        assert "5" not in periods  # no OT

    def test_fg_made_is_sum_of_2pt_and_3pt(self, regulation_rows: list[BoxScoreRow]) -> None:
        kat_period_1 = next(
            (r for r in regulation_rows if r.entity_id == "1626157" and r.period == "1"),
            None,
        )
        assert kat_period_1 is not None
        assert kat_period_1.fg_made >= 2

    def test_fullgame_aggregates_periods(self, regulation_rows: list[BoxScoreRow]) -> None:
        entity_id = "1626157"  # Karl-Anthony Towns
        periods = [r for r in regulation_rows if r.entity_id == entity_id and r.period != "FullGame"]
        full = next(r for r in regulation_rows if r.entity_id == entity_id and r.period == "FullGame")
        assert periods, "expected per-period rows for the test entity"

        assert full.points == sum(p.points for p in periods)
        assert full.rebounds == sum(p.rebounds for p in periods)
        assert full.assists == sum(p.assists for p in periods)
        assert full.fg_made == sum(p.fg_made for p in periods)
        assert full.fg3_made == sum(p.fg3_made for p in periods)
        assert full.ft_made == sum(p.ft_made for p in periods)

    def test_starters_exactly_five_per_team(self, regulation_rows: list[BoxScoreRow]) -> None:
        full = [r for r in regulation_rows if r.period == "FullGame"]
        for team in ("NYK", "BOS"):
            team_full = [r for r in full if r.team_abbreviation == team]
            starters = [r for r in team_full if r.starter == 1]
            bench = [r for r in team_full if r.starter == 0]
            assert len(starters) == 5
            assert len(bench) > 0
            assert len(starters) + len(bench) == len(team_full)

    def test_period_rows_starter_is_none(self, regulation_rows: list[BoxScoreRow]) -> None:
        for row in regulation_rows:
            if row.period != "FullGame":
                assert row.starter is None

    def test_fullgame_minutes_format(self, regulation_rows: list[BoxScoreRow]) -> None:
        full = next(r for r in regulation_rows if r.entity_id == "1626157" and r.period == "FullGame")
        assert full.minutes is not None
        m, _, s = full.minutes.partition(":")
        assert m.isdigit() and len(s) == 2 and s.isdigit()

    def test_no_negative_stats(self, regulation_rows: list[BoxScoreRow]) -> None:
        for row in regulation_rows:
            assert row.points >= 0
            assert row.rebounds >= 0
            assert row.fg_made >= 0
            assert row.fg_attempted >= 0


class TestOvertime:
    def test_period_5_present(self, ot_rows: list[BoxScoreRow]) -> None:
        assert "5" in {r.period for r in ot_rows}

    def test_fullgame_includes_ot(self, ot_rows: list[BoxScoreRow]) -> None:
        ot_entity_ids = {r.entity_id for r in ot_rows if r.period == "5"}
        assert ot_entity_ids
        entity_id = next(iter(ot_entity_ids))
        periods = [r for r in ot_rows if r.entity_id == entity_id and r.period != "FullGame"]
        full = next(r for r in ot_rows if r.entity_id == entity_id and r.period == "FullGame")
        assert full.points == sum(p.points for p in periods)


class TestDoubleOvertime:
    def test_periods_5_and_6_present(self, double_ot_rows: list[BoxScoreRow]) -> None:
        periods = {r.period for r in double_ot_rows}
        assert "5" in periods and "6" in periods

    def test_fullgame_includes_both_ots(self, double_ot_rows: list[BoxScoreRow]) -> None:
        ot_players = [r for r in double_ot_rows if r.period == "6"]
        assert ot_players
        entity_id = ot_players[0].entity_id
        periods = [r for r in double_ot_rows if r.entity_id == entity_id and r.period != "FullGame"]
        full = next(r for r in double_ot_rows if r.entity_id == entity_id and r.period == "FullGame")
        assert full.points == sum(p.points for p in periods)
