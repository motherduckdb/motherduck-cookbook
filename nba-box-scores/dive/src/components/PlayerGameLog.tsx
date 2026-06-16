import { useSQLQuery } from "@motherduck/react-sql-query";
import { Loader2, X } from "lucide-react";
import { DB, SeasonTypeFilter } from "../lib/query";
import { N, sqlStr, seasonLabel, COLORS } from "../lib/format";

const SEASON_TYPE_SQL: Record<SeasonTypeFilter, string | null> = {
  all: null,
  regular: "Regular Season",
  playoffs: "Playoffs",
};

export default function PlayerGameLog({
  entityId,
  playerName,
  season,
  seasonType = "all",
  team = "",
  onClose,
}: {
  entityId: string;
  playerName: string;
  season: number | null;
  seasonType?: SeasonTypeFilter;
  team?: string;
  onClose: () => void;
}) {
  // Match the filter context of the row the user clicked, so a Playoffs or
  // team-filtered view doesn't mix other games back into the log.
  const seasonCond = season != null ? `AND s.season_year = ${Number(season)}` : "";
  const st = SEASON_TYPE_SQL[seasonType];
  const typeCond = st ? `AND s.season_type = ${sqlStr(st)}` : "";
  const teamCond = team ? `AND b.team_abbreviation = ${sqlStr(team)}` : "";

  const q = useSQLQuery(`
    SELECT strftime(s.game_date, '%Y-%m-%d') AS d, s.season_type,
           b.team_abbreviation AS team,
           CASE WHEN s.home_team_abbreviation = b.team_abbreviation
                THEN s.away_team_abbreviation ELSE s.home_team_abbreviation END AS opp,
           CASE WHEN s.home_team_abbreviation = b.team_abbreviation THEN 'vs' ELSE '@' END AS loc,
           CASE WHEN (s.home_team_abbreviation = b.team_abbreviation AND s.home_team_score > s.away_team_score)
                  OR (s.away_team_abbreviation = b.team_abbreviation AND s.away_team_score > s.home_team_score)
                THEN 'W' ELSE 'L' END AS result,
           greatest(s.home_team_score, s.away_team_score) AS win_score,
           least(s.home_team_score, s.away_team_score) AS lose_score,
           b.minutes, b.points, b.rebounds, b.assists, b.steals, b.blocks, b.turnovers,
           b.fg_made, b.fg_attempted, b.fg3_made, b.fg3_attempted, b.ft_made, b.ft_attempted
    FROM ${DB}."box_scores" b
    JOIN ${DB}."schedule" s ON b.game_id = s.game_id
    WHERE b.period = 'FullGame' AND b.entity_id = ${sqlStr(entityId)} ${seasonCond} ${typeCond} ${teamCond}
    ORDER BY s.game_date DESC
  `);

  const rows = Array.isArray(q.data) ? q.data : [];
  const cols = ["Date", "", "Opp", "W/L", "MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "FG", "3P", "FT"];
  const td = "px-2 py-0.5 text-right text-sm tabular-nums";

  return (
    <div
      className="fixed inset-0 flex justify-end"
      style={{ background: "rgba(0,0,0,0.5)", zIndex: 60 }}
      // stopPropagation: when opened over BoxScorePanel this backdrop is nested
      // inside the parent backdrop, so a bubbling click would also close the box score.
      onClick={(e) => {
        e.stopPropagation();
        onClose();
      }}
    >
      <div
        className="h-full overflow-y-auto p-4"
        style={{ width: "min(820px, 92%)", background: COLORS.card }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div>
            <h2 className="text-xl font-bold" style={{ color: COLORS.text }}>
              {playerName}
            </h2>
            <p className="text-sm" style={{ color: COLORS.muted }}>
              Game log{season != null ? ` · ${seasonLabel(season)}` : " · all seasons"}
              {rows.length ? ` · ${rows.length} games` : ""}
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="p-2 rounded-full hover:bg-gray-100" style={{ color: COLORS.muted }}>
            <X size={20} />
          </button>
        </div>

        {q.isLoading && rows.length === 0 ? (
          <div className="flex items-center justify-center py-20 gap-2" style={{ color: COLORS.muted }}>
            <Loader2 className="animate-spin" size={20} /> Loading…
          </div>
        ) : rows.length === 0 ? (
          <p className="text-sm py-10 text-center" style={{ color: COLORS.muted }}>
            No games found.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full tabular-nums">
              <thead>
                <tr style={{ background: COLORS.headerBg }}>
                  {cols.map((c, i) => (
                    <th
                      key={i}
                      className={`px-2 py-1 text-xs font-semibold whitespace-nowrap ${i <= 3 ? "text-left" : "text-right"}`}
                      style={{ color: COLORS.text }}
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const win = String(r.result) === "W";
                  return (
                    <tr key={i} style={{ background: i % 2 ? COLORS.altRow : COLORS.card }}>
                      <td className="px-2 py-0.5 text-left text-sm whitespace-nowrap" style={{ color: COLORS.text }}>
                        {String(r.d)}
                        {String(r.season_type) === "Playoffs" ? (
                          <span style={{ color: COLORS.playoff }}> ●</span>
                        ) : null}
                      </td>
                      <td className="px-2 py-0.5 text-left text-sm" style={{ color: COLORS.muted }}>
                        {String(r.loc)}
                      </td>
                      <td className="px-2 py-0.5 text-left text-sm" style={{ color: COLORS.text }}>
                        {String(r.opp)}
                      </td>
                      <td
                        className="px-2 py-0.5 text-left text-sm font-medium whitespace-nowrap"
                        style={{ color: win ? "#2d7a00" : "#bc1200" }}
                      >
                        {String(r.result)} {N(r.win_score)}-{N(r.lose_score)}
                      </td>
                      <td className={td} style={{ color: COLORS.text }}>{String(r.minutes || "0:00")}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.points)}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.rebounds)}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.assists)}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.steals)}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.blocks)}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.turnovers)}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.fg_made)}-{N(r.fg_attempted)}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.fg3_made)}-{N(r.fg3_attempted)}</td>
                      <td className={td} style={{ color: COLORS.text }}>{N(r.ft_made)}-{N(r.ft_attempted)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
