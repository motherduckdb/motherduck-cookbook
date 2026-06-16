import { sqlStr } from "./format";

export const DB = `"nba_box_scores_v3"."main"`;

export type SeasonTypeFilter = "all" | "regular" | "playoffs";

export interface Filters {
  season: number | null; // null = all seasons
  seasonType: SeasonTypeFilter;
  team: string; // "" = all teams
  player: string; // "" = no player filter
}

const SEASON_TYPE_SQL: Record<SeasonTypeFilter, string | null> = {
  all: null,
  regular: "Regular Season",
  playoffs: "Playoffs",
};

// Player names drift over time (diacritics / nicknames: "Nikola Jokić" vs
// "Nikola Jokic", "Alex" vs "Alexandre"). entity_id is the stable key. Given a
// (selected) display name, resolve to the entity_id(s) so a filter catches ALL
// of that player's rows regardless of how the name was spelled in a given game.
export const playerEntityIds = (player: string): string =>
  `(SELECT entity_id FROM ${DB}."box_scores" WHERE player_name = ${sqlStr(player)})`;

// Season + season_type conditions only (on schedule alias `s`), for aggregate
// queries (GQ leaderboard, player index, scatter) that handle team/player
// against their own columns. Returns "1=1" when neither is set.
export function seasonTypeWhere(f: Filters): string {
  const conds: string[] = ["1=1"];
  if (f.season != null) conds.push(`s.season_year = ${Number(f.season)}`);
  const st = SEASON_TYPE_SQL[f.seasonType];
  if (st) conds.push(`s.season_type = ${sqlStr(st)}`);
  return conds.join(" AND ");
}

// Build the WHERE conditions for the schedule table (aliased `s`).
// Returns a string beginning with the conditions, joined by AND, or "1=1".
export function scheduleWhere(f: Filters): string {
  const conds: string[] = ["1=1"];
  if (f.season != null) conds.push(`s.season_year = ${Number(f.season)}`);
  const st = SEASON_TYPE_SQL[f.seasonType];
  if (st) conds.push(`s.season_type = ${sqlStr(st)}`);
  if (f.team) conds.push(`(s.home_team_abbreviation = ${sqlStr(f.team)} OR s.away_team_abbreviation = ${sqlStr(f.team)})`);
  if (f.player) {
    conds.push(
      `s.game_id IN (SELECT bs.game_id FROM ${DB}."box_scores" bs ` +
        `WHERE bs.period = 'FullGame' AND bs.entity_id IN ${playerEntityIds(f.player)})`,
    );
  }
  return conds.join(" AND ");
}
