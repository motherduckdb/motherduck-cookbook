import { periodLabel, COLORS } from "../lib/format";

export interface GameCardData {
  game_id: string;
  home_abbr: string;
  away_abbr: string;
  home_score: number;
  away_score: number;
  isPlayoff: boolean;
  game_status: string;
  // period -> { [team_abbr]: points }
  periodList: string[];
  periodPoints: Record<string, Record<string, number>>;
}

export default function GameCard({
  game,
  onSelect,
}: {
  game: GameCardData;
  onSelect: (id: string) => void;
}) {
  const cellPts = (period: string, team: string) =>
    game.periodPoints[period]?.[team] ?? "";

  const teamRow = (abbr: string, total: number, isWinner: boolean) => (
    <tr>
      <td
        className="text-left py-0.5 pr-2 font-medium"
        style={{ color: COLORS.text }}
      >
        {abbr}
      </td>
      {game.periodList.map((p) => (
        <td
          key={p}
          className="text-center py-0.5 tabular-nums"
          style={{ color: COLORS.muted, width: 26 }}
        >
          {cellPts(p, abbr)}
        </td>
      ))}
      <td
        className="text-center py-0.5 tabular-nums font-bold"
        style={{ color: COLORS.text, width: 30 }}
      >
        {total}
      </td>
    </tr>
  );

  const homeWins = game.home_score > game.away_score;

  return (
    <div
      onClick={() => onSelect(game.game_id)}
      className="cursor-pointer rounded-lg p-4 shadow-md hover:shadow-lg transition-shadow"
      style={{
        background: COLORS.card,
        borderLeft: game.isPlayoff ? `4px solid ${COLORS.playoff}` : undefined,
      }}
    >
      <table className="w-full text-sm">
        <thead>
          <tr style={{ color: COLORS.muted }}>
            <th className="text-left font-normal pb-1"> </th>
            {game.periodList.map((p) => (
              <th key={p} className="text-center font-normal pb-1" style={{ width: 26 }}>
                {periodLabel(p)}
              </th>
            ))}
            <th className="text-center font-normal pb-1" style={{ width: 30 }}>
              T
            </th>
          </tr>
        </thead>
        <tbody>
          {teamRow(game.away_abbr, game.away_score, !homeWins)}
          {teamRow(game.home_abbr, game.home_score, homeWins)}
        </tbody>
      </table>
    </div>
  );
}
