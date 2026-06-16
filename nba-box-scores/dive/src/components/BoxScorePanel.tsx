import { useState } from "react";
import { useSQLQuery } from "@motherduck/react-sql-query";
import { Loader2, X } from "lucide-react";
import BoxScoreTable, { PlayerRow } from "./BoxScoreTable";
import PlayerGameLog from "./PlayerGameLog";
import { DB } from "../lib/query";
import { N, sqlStr, COLORS } from "../lib/format";
import { getTeamName } from "../lib/teams";

function formatLongDate(d: string): string {
  const [y, m, day] = d.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, day)).toLocaleDateString("en-US", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

export default function BoxScorePanel({
  gameId,
  onClose,
}: {
  gameId: string | null;
  onClose: () => void;
}) {
  const enabled = !!gameId;
  const id = gameId ?? "";
  const [sel, setSel] = useState<{ id: string; name: string } | null>(null);

  const gameQ = useSQLQuery(
    `SELECT home_team_abbreviation AS home, away_team_abbreviation AS away,
            home_team_score AS home_score, away_team_score AS away_score,
            strftime(game_date, '%Y-%m-%d') AS d, season_type, season_year
     FROM ${DB}."schedule" WHERE game_id = ${sqlStr(id)}`,
    { enabled },
  );

  const playersQ = useSQLQuery(
    `SELECT entity_id, player_name, team_abbreviation, minutes,
            points, rebounds, assists, steals, blocks, turnovers,
            fg_made, fg_attempted, fg3_made, fg3_attempted, ft_made, ft_attempted, starter
     FROM ${DB}."box_scores"
     WHERE game_id = ${sqlStr(id)} AND period = 'FullGame'
     ORDER BY (coalesce(TRY_CAST(split_part(minutes, ':', 1) AS INTEGER), 0) * 60
             + coalesce(TRY_CAST(split_part(minutes, ':', 2) AS INTEGER), 0)) DESC,
              points DESC`,
    { enabled },
  );

  if (!gameId) return null;

  const game = gameQ.isSuccess ? (gameQ.data as any[])[0] : null;
  const allPlayers = (Array.isArray(playersQ.data) ? playersQ.data : []) as any[];
  const homeAbbr = game ? String(game.home) : "";
  const awayAbbr = game ? String(game.away) : "";
  const homePlayers = allPlayers.filter((p) => String(p.team_abbreviation) === homeAbbr) as PlayerRow[];
  const awayPlayers = allPlayers.filter((p) => String(p.team_abbreviation) === awayAbbr) as PlayerRow[];

  const loading = gameQ.isLoading || playersQ.isLoading;
  const homeWins = game ? N(game.home_score) > N(game.away_score) : false;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end"
      style={{ background: "rgba(0,0,0,0.5)" }}
      onClick={onClose}
    >
      <div
        className="h-full overflow-y-auto p-4"
        style={{ width: "min(900px, 90%)", background: COLORS.card }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between mb-3">
          <div>
            {game && (
              <>
                <h2 className="text-xl text-center" style={{ color: COLORS.text }}>
                  {homeWins ? (
                    <>
                      <span>{awayAbbr} {N(game.away_score)}</span>
                      {" — "}
                      <span className="font-bold">{homeAbbr} {N(game.home_score)} *</span>
                    </>
                  ) : (
                    <>
                      <span className="font-bold">* {awayAbbr} {N(game.away_score)}</span>
                      {" — "}
                      <span>{homeAbbr} {N(game.home_score)}</span>
                    </>
                  )}
                </h2>
                <p className="text-sm mt-0.5" style={{ color: COLORS.muted }}>
                  {formatLongDate(String(game.d))}
                  {String(game.season_type) === "Playoffs" ? " · Playoffs" : ""}
                </p>
              </>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="p-2 rounded-full hover:bg-gray-100"
            style={{ color: COLORS.muted }}
          >
            <X size={20} />
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-20 gap-2" style={{ color: COLORS.muted }}>
            <Loader2 className="animate-spin" size={20} /> Loading box score…
          </div>
        ) : (
          <div className="space-y-5">
            <BoxScoreTable
              teamName={getTeamName(awayAbbr)}
              players={awayPlayers}
              onPlayerClick={(eid, name) => setSel({ id: eid, name })}
            />
            <BoxScoreTable
              teamName={getTeamName(homeAbbr)}
              players={homePlayers}
              onPlayerClick={(eid, name) => setSel({ id: eid, name })}
            />
          </div>
        )}
      </div>

      {sel && (
        <PlayerGameLog
          entityId={sel.id}
          playerName={sel.name}
          season={game ? N(game.season_year) : null}
          onClose={() => setSel(null)}
        />
      )}
    </div>
  );
}
