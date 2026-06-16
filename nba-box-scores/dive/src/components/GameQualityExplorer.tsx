import { useState } from "react";
import { useSQLQuery } from "@motherduck/react-sql-query";
import { Loader2 } from "lucide-react";
import PlayerGameLog from "./PlayerGameLog";
import { DB, Filters, seasonTypeWhere, playerEntityIds } from "../lib/query";
import { N, sqlStr, COLORS } from "../lib/format";

interface Col {
  key: string;
  label: string;
  fmt: (v: number) => string;
  num: boolean;
}

const PCT = (v: number) => (v * 100).toFixed(1) + "%";
const D1 = (v: number) => v.toFixed(1);
const SIGNED = (v: number) => (v >= 0 ? "+" : "") + v.toFixed(2);
const INT = (v: number) => String(Math.round(v));

const COLS: Col[] = [
  { key: "avg_gq", label: "GQ", fmt: PCT, num: true },
  { key: "gp", label: "GP", fmt: INT, num: true },
  { key: "ppg", label: "PTS", fmt: D1, num: true },
  { key: "rpg", label: "REB", fmt: D1, num: true },
  { key: "apg", label: "AST", fmt: D1, num: true },
  { key: "spg", label: "STL", fmt: D1, num: true },
  { key: "bpg", label: "BLK", fmt: D1, num: true },
  { key: "topg", label: "TO", fmt: D1, num: true },
  { key: "fg_v", label: "FGv", fmt: SIGNED, num: true },
  { key: "ft_v", label: "FTv", fmt: SIGNED, num: true },
  { key: "tpm", label: "3PM", fmt: D1, num: true },
];

export default function GameQualityExplorer({ filters }: { filters: Filters }) {
  const [minGames, setMinGames] = useState(10);
  const [sortKey, setSortKey] = useState("avg_gq");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [sel, setSel] = useState<{ id: string; name: string } | null>(null);

  const teamJoin = filters.team
    ? `JOIN ${DB}."box_scores" bt ON bt.game_id = gq.game_id AND bt.entity_id = gq.entity_id
        AND bt.period = 'FullGame' AND bt.team_abbreviation = ${sqlStr(filters.team)}`
    : "";
  const playerCond = filters.player ? `AND gq.entity_id IN ${playerEntityIds(filters.player)}` : "";

  // game_quality = -1 is the sentinel for sub-15-min games (don't qualify for
  // the GQ calc); exclude them so they don't drag the average down. Group by the
  // stable entity_id and show the most recent name to defeat name drift.
  const q = useSQLQuery(`
    SELECT gq.entity_id,
           arg_max(gq.player_name, s.game_date) AS player_name,
           COUNT(*) AS gp,
           AVG(gq.game_quality) AS avg_gq,
           AVG(gq.points) AS ppg, AVG(gq.rebounds) AS rpg, AVG(gq.assists) AS apg,
           AVG(gq.steals) AS spg, AVG(gq.blocks) AS bpg, AVG(gq.turnovers) AS topg,
           AVG(gq.fg_v) AS fg_v, AVG(gq.ft_v) AS ft_v, AVG(gq.fg3_made) AS tpm
    FROM ${DB}."game_quality" gq
    JOIN ${DB}."schedule" s ON gq.game_id = s.game_id
    ${teamJoin}
    WHERE ${seasonTypeWhere(filters)} AND gq.game_quality >= 0 ${playerCond}
    GROUP BY gq.entity_id
    HAVING COUNT(*) >= ${Number(minGames)}
    ORDER BY avg_gq DESC
    LIMIT 200
  `);

  const rows = (Array.isArray(q.data) ? q.data : []).map((r) => {
    const o: Record<string, number | string> = {
      entity_id: String(r.entity_id),
      player_name: String(r.player_name),
    };
    for (const c of COLS) o[c.key] = N(r[c.key]);
    return o;
  });

  const sorted = [...rows].sort((a, b) => {
    const av = a[sortKey];
    const bv = b[sortKey];
    // player_name is a string column — subtracting strings yields NaN (no-op sort).
    if (typeof av === "string" || typeof bv === "string") {
      const c = String(av).localeCompare(String(bv));
      return sortDir === "desc" ? -c : c;
    }
    return sortDir === "desc" ? (bv as number) - (av as number) : (av as number) - (bv as number);
  });

  const clickSort = (key: string) => {
    if (sortKey === key) setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const arrow = (key: string) => (sortKey === key ? (sortDir === "desc" ? " ▾" : " ▴") : "");

  return (
    <div>
      <div className="flex items-center gap-3 mb-3">
        <label className="text-sm" style={{ color: COLORS.muted }}>
          Min games
        </label>
        <input
          type="range"
          min={1}
          max={50}
          value={minGames}
          onChange={(e) => setMinGames(Number(e.target.value))}
          className="w-40"
        />
        <span className="text-sm font-medium tabular-nums" style={{ color: COLORS.text }}>
          {minGames}
        </span>
        <span className="text-sm ml-auto" style={{ color: COLORS.muted }}>
          {q.isSuccess ? `${sorted.length} players` : ""}
        </span>
      </div>
      <p className="text-xs mb-3" style={{ color: COLORS.muted }}>
        Game Quality averages only games with 15+ minutes played; GP counts those qualifying games.
      </p>

      {q.isError ? (
        <div className="py-8 text-sm" style={{ color: "#bc1200" }}>
          Error: {q.error?.message}
        </div>
      ) : q.isLoading && rows.length === 0 ? (
        <div className="flex items-center justify-center py-16 gap-2" style={{ color: COLORS.muted }}>
          <Loader2 className="animate-spin" size={18} /> Loading…
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full tabular-nums">
            <thead>
              <tr style={{ background: COLORS.headerBg }}>
                <th
                  className="px-2 py-1 text-left text-xs font-semibold cursor-pointer"
                  style={{ color: COLORS.text }}
                  onClick={() => clickSort("player_name")}
                >
                  Player
                </th>
                {COLS.map((c) => (
                  <th
                    key={c.key}
                    className="px-2 py-1 text-right text-xs font-semibold cursor-pointer whitespace-nowrap"
                    style={{ color: COLORS.text }}
                    onClick={() => clickSort(c.key)}
                  >
                    {c.label}
                    {arrow(c.key)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <tr key={String(r.entity_id)} style={{ background: i % 2 ? COLORS.altRow : COLORS.card }}>
                  <td className="px-2 py-0.5 text-left text-sm whitespace-nowrap">
                    <button
                      className="hover:underline"
                      style={{ color: COLORS.primary }}
                      onClick={() => setSel({ id: String(r.entity_id), name: String(r.player_name) })}
                    >
                      {String(r.player_name)}
                    </button>
                  </td>
                  {COLS.map((c) => (
                    <td
                      key={c.key}
                      className="px-2 py-0.5 text-right text-sm"
                      style={{ color: c.key === "avg_gq" ? COLORS.primary : COLORS.text }}
                    >
                      {c.fmt(r[c.key] as number)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {sel && (
        <PlayerGameLog
          entityId={sel.id}
          playerName={sel.name}
          season={filters.season}
          seasonType={filters.seasonType}
          team={filters.team}
          onClose={() => setSel(null)}
        />
      )}
    </div>
  );
}
