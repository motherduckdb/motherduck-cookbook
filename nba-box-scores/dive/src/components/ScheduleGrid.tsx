import { useSQLQuery } from "@motherduck/react-sql-query";
import { Loader2 } from "lucide-react";
import GameCard, { GameCardData } from "./GameCard";
import { DB, Filters, scheduleWhere } from "../lib/query";
import { N, COLORS } from "../lib/format";

const DATES_PER_PAGE = 4;

function formatDateHeading(d: string): string {
  // d is 'YYYY-MM-DD' — format without JS Date timezone surprises.
  const [y, m, day] = d.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, day));
  return dt.toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  });
}

export default function ScheduleGrid({
  filters,
  page,
  setPage,
  onSelectGame,
}: {
  filters: Filters;
  page: number;
  setPage: (fn: (p: number) => number) => void;
  onSelectGame: (id: string) => void;
}) {
  const where = scheduleWhere(filters);
  const lo = page * DATES_PER_PAGE;

  const gamesQ = useSQLQuery(`
    WITH filtered AS (
      SELECT s.game_id, s.game_date,
             strftime(s.game_date, '%Y-%m-%d') AS d,
             s.season_type,
             s.home_team_abbreviation AS home_abbr,
             s.away_team_abbreviation AS away_abbr,
             s.home_team_score AS home_score,
             s.away_team_score AS away_score,
             s.game_status
      FROM ${DB}."schedule" s
      WHERE ${where}
    ),
    ranked AS (
      SELECT d, ROW_NUMBER() OVER (ORDER BY d DESC) - 1 AS rn
      FROM (SELECT DISTINCT d FROM filtered)
    ),
    page_dates AS (
      SELECT d FROM ranked WHERE rn >= ${lo} AND rn < ${lo + DATES_PER_PAGE}
    )
    SELECT f.game_id, f.d, f.season_type, f.home_abbr, f.away_abbr,
           f.home_score, f.away_score, f.game_status,
           (SELECT string_agg(ts.period || '|' || ts.team_abbreviation || '|' || ts.points, ','
                              ORDER BY ts.period)
            FROM ${DB}."team_stats" ts
            WHERE ts.game_id = f.game_id AND ts.period <> 'FullGame') AS period_scores
    FROM filtered f
    JOIN page_dates pd ON f.d = pd.d
    ORDER BY f.game_date DESC, f.game_id
  `);

  const countQ = useSQLQuery(`
    SELECT COUNT(*) AS n FROM (
      SELECT DISTINCT strftime(s.game_date, '%Y-%m-%d') AS d
      FROM ${DB}."schedule" s WHERE ${where}
    )
  `);

  const totalDates = countQ.isSuccess ? N((countQ.data as any[])[0]?.n) : 0;
  const totalPages = Math.max(1, Math.ceil(totalDates / DATES_PER_PAGE));

  const rows = Array.isArray(gamesQ.data) ? gamesQ.data : [];

  // Group games by date, building GameCardData (parsing the period_scores string).
  const groups: { date: string; games: GameCardData[] }[] = [];
  const byDate = new Map<string, GameCardData[]>();
  for (const r of rows) {
    const d = String(r.d);
    const periodPoints: Record<string, Record<string, number>> = {};
    const periodSet = new Set<string>();
    const raw = r.period_scores ? String(r.period_scores) : "";
    if (raw) {
      for (const part of raw.split(",")) {
        const [p, team, pts] = part.split("|");
        if (!p) continue;
        periodSet.add(p);
        (periodPoints[p] ||= {})[team] = N(pts);
      }
    }
    const periodList = [...periodSet].sort((a, b) => Number(a) - Number(b));
    const game: GameCardData = {
      game_id: String(r.game_id),
      home_abbr: String(r.home_abbr),
      away_abbr: String(r.away_abbr),
      home_score: N(r.home_score),
      away_score: N(r.away_score),
      isPlayoff: String(r.season_type) === "Playoffs",
      game_status: String(r.game_status ?? ""),
      periodList,
      periodPoints,
    };
    if (!byDate.has(d)) {
      byDate.set(d, []);
      groups.push({ date: d, games: byDate.get(d)! });
    }
    byDate.get(d)!.push(game);
  }

  if (gamesQ.isError) {
    return (
      <div className="py-8 text-sm" style={{ color: "#bc1200" }}>
        Error loading games: {gamesQ.error?.message}
      </div>
    );
  }

  if (gamesQ.isLoading && rows.length === 0) {
    return (
      <div className="flex items-center justify-center py-16 gap-2" style={{ color: COLORS.muted }}>
        <Loader2 className="animate-spin" size={18} /> Loading games…
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="py-16 text-center text-sm" style={{ color: COLORS.muted }}>
        No games match these filters.
      </div>
    );
  }

  return (
    <div>
      {groups.map((g) => (
        <div key={g.date} className="mb-8">
          <h2 className="text-lg font-bold mb-3" style={{ color: COLORS.text }}>
            {formatDateHeading(g.date)}
          </h2>
          {/* Inline auto-fill grid (responsive Tailwind prefixes like md:/lg: don't
              apply in the MotherDuck dive renderer — this flows columns by width). */}
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
              gap: 12,
            }}
          >
            {g.games.map((game) => (
              <GameCard key={game.game_id} game={game} onSelect={onSelectGame} />
            ))}
          </div>
        </div>
      ))}

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-4 py-4">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-4 py-1.5 rounded border text-sm disabled:opacity-40"
            style={{ borderColor: COLORS.border, background: "#fff", color: COLORS.text }}
          >
            Previous
          </button>
          <span className="text-sm" style={{ color: COLORS.muted }}>
            Page {page + 1} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
            disabled={page >= totalPages - 1}
            className="px-4 py-1.5 rounded border text-sm disabled:opacity-40"
            style={{ borderColor: COLORS.border, background: "#fff", color: COLORS.text }}
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
