"""Box-score parser — raw PBPStats JSON → list of `BoxScoreRow` matching v3's `box_scores` table.

Line-by-line port of `scripts/ingest/parse/box-score-parser.ts`.

Behavioral notes (preserved from the TS version):

- Team-level aggregates (EntityId == "0") are excluded
- FullGame rows in the raw JSON are ignored — we recompute them from
  per-period rows so the totals stay internally consistent
- Starter heuristic: per team, the 5 FullGame rows with the highest
  points are starters; ties broken by original appearance order
  (stable sort, same as the TS sort)
- starter is only set on FullGame rows; per-period rows keep starter=None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class BoxScoreRow:
    game_id: str
    team_abbreviation: str
    entity_id: str
    player_name: str
    period: str
    minutes: str | None
    points: int
    rebounds: int
    assists: int
    steals: int
    blocks: int
    turnovers: int
    fg_made: int
    fg_attempted: int
    fg3_made: int
    fg3_attempted: int
    ft_made: int
    ft_attempted: int
    starter: int | None


def _num(v: Any) -> int:
    return int(v) if v is not None else 0


def _sum_minutes(minutes: list[str | None]) -> str:
    total_seconds = 0
    for m in minutes:
        if not m:
            continue
        parts = m.split(":")
        if len(parts) != 2:
            continue
        try:
            total_seconds += int(parts[0]) * 60 + int(parts[1])
        except ValueError:
            continue
    return f"{total_seconds // 60}:{total_seconds % 60:02d}"


def _parse_player_period(
    raw: dict[str, Any], game_id: str, team_abbr: str, period: str
) -> BoxScoreRow | None:
    if raw.get("EntityId") == "0":
        return None

    off_reb = _num(raw.get("OffRebounds"))
    def_reb = _num(raw.get("DefRebounds"))
    rebounds = _num(raw["Rebounds"]) if raw.get("Rebounds") is not None else off_reb + def_reb

    return BoxScoreRow(
        game_id=game_id,
        team_abbreviation=team_abbr,
        entity_id=raw["EntityId"],
        player_name=raw["Name"],
        period=period,
        minutes=raw.get("Minutes"),
        points=_num(raw.get("Points")),
        rebounds=rebounds,
        assists=_num(raw.get("Assists")),
        steals=_num(raw.get("Steals")),
        blocks=_num(raw.get("Blocks")),
        turnovers=_num(raw.get("Turnovers")),
        fg_made=_num(raw.get("FG2M")) + _num(raw.get("FG3M")),
        fg_attempted=_num(raw.get("FG2A")) + _num(raw.get("FG3A")),
        fg3_made=_num(raw.get("FG3M")),
        fg3_attempted=_num(raw.get("FG3A")),
        ft_made=_num(raw.get("FtPoints")),
        ft_attempted=_num(raw.get("FTA")),
        starter=None,
    )


def _assign_starters(full_game_rows: list[BoxScoreRow]) -> None:
    by_team: dict[str, list[BoxScoreRow]] = {}
    for row in full_game_rows:
        by_team.setdefault(row.team_abbreviation, []).append(row)

    for players in by_team.values():
        # Python's sort is stable; sorting by -points preserves original order on ties,
        # matching the TS Array.prototype.sort behavior used in the legacy parser.
        ordered = sorted(players, key=lambda p: -p.points)
        starter_ids = {p.entity_id for p in ordered[:5]}
        for player in players:
            player.starter = 1 if player.entity_id in starter_ids else 0


def parse_box_score(data: Any) -> list[BoxScoreRow]:
    """Parse a raw PBPStats box-score JSON object into `BoxScoreRow` instances.

    One row per player per period (1-4 plus any OT), plus a recomputed
    FullGame row per player.
    """
    game = data["game"]
    game_id = game["gameId"]
    home_abbr = game["homeTeam"]["teamTricode"]
    away_abbr = game["awayTeam"]["teamTricode"]
    team_stats = data["boxScore"]["stats"]

    period_rows: list[BoxScoreRow] = []
    for side_key, abbr in (("Away", away_abbr), ("Home", home_abbr)):
        for period, players in team_stats[side_key].items():
            if period == "FullGame":
                continue  # recomputed below
            for player in players:
                row = _parse_player_period(player, game_id, abbr, period)
                if row is not None:
                    period_rows.append(row)

    # Aggregate per-period rows into FullGame rows
    player_map: dict[tuple[str, str], list[BoxScoreRow]] = {}
    for row in period_rows:
        player_map.setdefault((row.team_abbreviation, row.entity_id), []).append(row)

    full_game_rows: list[BoxScoreRow] = []
    for periods in player_map.values():
        first = periods[0]
        full_game_rows.append(
            BoxScoreRow(
                game_id=first.game_id,
                team_abbreviation=first.team_abbreviation,
                entity_id=first.entity_id,
                player_name=first.player_name,
                period="FullGame",
                minutes=_sum_minutes([p.minutes for p in periods]),
                points=sum(p.points for p in periods),
                rebounds=sum(p.rebounds for p in periods),
                assists=sum(p.assists for p in periods),
                steals=sum(p.steals for p in periods),
                blocks=sum(p.blocks for p in periods),
                turnovers=sum(p.turnovers for p in periods),
                fg_made=sum(p.fg_made for p in periods),
                fg_attempted=sum(p.fg_attempted for p in periods),
                fg3_made=sum(p.fg3_made for p in periods),
                fg3_attempted=sum(p.fg3_attempted for p in periods),
                ft_made=sum(p.ft_made for p in periods),
                ft_attempted=sum(p.ft_attempted for p in periods),
                starter=None,
            )
        )

    _assign_starters(full_game_rows)
    return period_rows + full_game_rows
