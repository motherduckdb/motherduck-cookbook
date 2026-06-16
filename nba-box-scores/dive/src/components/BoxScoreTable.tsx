import { N, COLORS } from "../lib/format";

export interface PlayerRow {
  entity_id: string;
  player_name: string;
  minutes: string;
  points: number;
  rebounds: number;
  assists: number;
  steals: number;
  blocks: number;
  turnovers: number;
  fg_made: number;
  fg_attempted: number;
  fg3_made: number;
  fg3_attempted: number;
  ft_made: number;
  ft_attempted: number;
  starter: number;
}

const COLS = ["MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "FG", "3P", "FT"];

export default function BoxScoreTable({
  teamName,
  players,
  onPlayerClick,
}: {
  teamName: string;
  players: PlayerRow[];
  onPlayerClick?: (entityId: string, playerName: string) => void;
}) {
  if (!players.length) {
    return (
      <div>
        <h3 className="font-bold text-base mb-1" style={{ color: COLORS.text }}>
          {teamName}
        </h3>
        <p className="text-sm" style={{ color: COLORS.muted }}>
          No player stats available
        </p>
      </div>
    );
  }

  const totals = players.reduce(
    (a, p) => ({
      points: a.points + N(p.points),
      rebounds: a.rebounds + N(p.rebounds),
      assists: a.assists + N(p.assists),
      steals: a.steals + N(p.steals),
      blocks: a.blocks + N(p.blocks),
      turnovers: a.turnovers + N(p.turnovers),
      fgM: a.fgM + N(p.fg_made),
      fgA: a.fgA + N(p.fg_attempted),
      tpM: a.tpM + N(p.fg3_made),
      tpA: a.tpA + N(p.fg3_attempted),
      ftM: a.ftM + N(p.ft_made),
      ftA: a.ftA + N(p.ft_attempted),
    }),
    { points: 0, rebounds: 0, assists: 0, steals: 0, blocks: 0, turnovers: 0, fgM: 0, fgA: 0, tpM: 0, tpA: 0, ftM: 0, ftA: 0 },
  );

  const th = "px-2 py-0.5 text-right text-xs font-semibold";
  const td = "px-2 py-0.5 text-right text-sm tabular-nums";

  return (
    <div>
      <h3 className="font-bold text-base mb-1" style={{ color: COLORS.text }}>
        {teamName}
      </h3>
      <table className="min-w-full table-fixed tabular-nums">
        <thead>
          <tr style={{ background: COLORS.headerBg }}>
            <th className="px-2 py-0.5 text-left text-xs font-semibold" style={{ color: COLORS.text }}>
              Player
            </th>
            {COLS.map((c) => (
              <th key={c} className={th} style={{ color: COLORS.text }}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {players.map((p, i) => (
            <tr key={p.entity_id || p.player_name} style={{ background: i % 2 ? COLORS.altRow : COLORS.card }}>
              <td className="px-2 py-0.5 text-left text-sm">
                {onPlayerClick ? (
                  <button
                    className="hover:underline"
                    style={{ color: COLORS.primary }}
                    onClick={() => onPlayerClick(p.entity_id, p.player_name)}
                  >
                    {p.player_name}
                  </button>
                ) : (
                  <span style={{ color: COLORS.text }}>{p.player_name}</span>
                )}
                {N(p.starter) === 1 ? <span style={{ color: COLORS.muted }}> *</span> : null}
              </td>
              <td className={td} style={{ color: COLORS.text }}>{p.minutes || "0:00"}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.points)}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.rebounds)}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.assists)}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.steals)}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.blocks)}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.turnovers)}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.fg_made)}-{N(p.fg_attempted)}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.fg3_made)}-{N(p.fg3_attempted)}</td>
              <td className={td} style={{ color: COLORS.text }}>{N(p.ft_made)}-{N(p.ft_attempted)}</td>
            </tr>
          ))}
          <tr className="font-bold" style={{ background: COLORS.totalBg }}>
            <td className="px-2 py-0.5 text-left text-sm" style={{ color: COLORS.text }}>TOTAL</td>
            <td className={td} style={{ color: COLORS.text }}>-</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.points}</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.rebounds}</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.assists}</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.steals}</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.blocks}</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.turnovers}</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.fgM}-{totals.fgA}</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.tpM}-{totals.tpA}</td>
            <td className={td} style={{ color: COLORS.text }}>{totals.ftM}-{totals.ftA}</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
