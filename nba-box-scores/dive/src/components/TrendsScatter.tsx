import { useState } from "react";
import { useSQLQuery } from "@motherduck/react-sql-query";
import {
  ScatterChart, Scatter, XAxis, YAxis, ZAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { Loader2 } from "lucide-react";
import { DB, Filters, seasonTypeWhere, playerEntityIds } from "../lib/query";
import { N, sqlStr, seasonLabel, COLORS } from "../lib/format";

function PointTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded px-2 py-1 text-xs" style={{ background: "#fff", border: `1px solid ${COLORS.border}` }}>
      <div className="font-semibold" style={{ color: COLORS.text }}>{p.player_name}</div>
      <div style={{ color: COLORS.muted }}>
        {p.ppg.toFixed(1)} PPG · GQ {(p.avg_gq * 100).toFixed(1)}% · {p.gp} GP
      </div>
    </div>
  );
}

export default function TrendsScatter({ filters }: { filters: Filters }) {
  const [minGames, setMinGames] = useState(20);

  // Apply the same shared team/player filters as the leaderboard so the tab
  // agrees with the filter bar. team isn't on the game_quality view → join
  // box_scores. Exclude the -1 (sub-15-min) GQ sentinel; group by stable
  // entity_id with the latest name.
  const teamJoin = filters.team
    ? `JOIN ${DB}."box_scores" bt ON bt.game_id = gq.game_id AND bt.entity_id = gq.entity_id
        AND bt.period = 'FullGame' AND bt.team_abbreviation = ${sqlStr(filters.team)}`
    : "";
  const playerCond = filters.player ? `AND gq.entity_id IN ${playerEntityIds(filters.player)}` : "";

  const q = useSQLQuery(`
    SELECT gq.entity_id,
           arg_max(gq.player_name, s.game_date) AS player_name,
           COUNT(*) AS gp,
           AVG(gq.points) AS ppg,
           AVG(gq.game_quality) AS avg_gq
    FROM ${DB}."game_quality" gq
    JOIN ${DB}."schedule" s ON gq.game_id = s.game_id
    ${teamJoin}
    WHERE ${seasonTypeWhere(filters)} AND gq.game_quality >= 0 ${playerCond}
    GROUP BY gq.entity_id
    HAVING COUNT(*) >= ${Number(minGames)}
    LIMIT 800
  `);

  const data = (Array.isArray(q.data) ? q.data : []).map((r) => ({
    player_name: String(r.player_name),
    gp: N(r.gp),
    ppg: N(r.ppg),
    avg_gq: N(r.avg_gq),
  }));

  const seasonTxt = filters.season != null ? seasonLabel(filters.season) : "all seasons";

  return (
    <div>
      <div className="flex items-center gap-3 mb-1">
        <label className="text-sm" style={{ color: COLORS.muted }}>Min games</label>
        <input type="range" min={1} max={60} value={minGames}
          onChange={(e) => setMinGames(Number(e.target.value))} className="w-40" />
        <span className="text-sm font-medium tabular-nums" style={{ color: COLORS.text }}>{minGames}</span>
        <span className="text-sm ml-auto" style={{ color: COLORS.muted }}>
          {q.isSuccess ? `${data.length} players` : ""}
        </span>
      </div>
      <p className="text-sm mb-3" style={{ color: COLORS.muted }}>
        Scoring vs. Game Quality — {seasonTxt}. Each dot is a player.
      </p>

      {q.isError ? (
        <div className="py-8 text-sm" style={{ color: "#bc1200" }}>Error: {q.error?.message}</div>
      ) : q.isLoading && data.length === 0 ? (
        <div className="flex items-center justify-center gap-2" style={{ color: COLORS.muted, height: 420 }}>
          <Loader2 className="animate-spin" size={18} /> Loading…
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={460}>
          <ScatterChart margin={{ top: 10, right: 20, bottom: 30, left: 10 }}>
            <CartesianGrid stroke="#eee" />
            <XAxis
              type="number" dataKey="ppg" name="PPG" fontSize={11}
              label={{ value: "Points per game", position: "insideBottom", offset: -15, fontSize: 12 }}
            />
            <YAxis
              type="number" dataKey="avg_gq" name="GQ" fontSize={11}
              tickFormatter={(v) => `${(v * 100).toFixed(0)}%`}
              label={{ value: "Avg Game Quality", angle: -90, position: "insideLeft", fontSize: 12 }}
            />
            <ZAxis type="number" dataKey="gp" range={[20, 200]} name="GP" />
            <Tooltip content={<PointTooltip />} cursor={{ strokeDasharray: "3 3" }} />
            <Scatter data={data} fill={COLORS.primary} fillOpacity={0.55} />
          </ScatterChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
