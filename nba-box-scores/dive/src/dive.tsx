import { useDiveState } from "@motherduck/react-sql-query";
import SeasonFilter from "./components/SeasonFilter";
import ScheduleGrid from "./components/ScheduleGrid";
import BoxScorePanel from "./components/BoxScorePanel";
import GameQualityExplorer from "./components/GameQualityExplorer";
import TrendsScatter from "./components/TrendsScatter";
import { Filters, SeasonTypeFilter } from "./lib/query";
import { COLORS, MONO } from "./lib/format";

// Current season: season_year = 2025 is the 2025-26 season.
const DEFAULT_SEASON = 2025;

type Tab = "schedule" | "gq" | "trends";
const TABS: { key: Tab; label: string }[] = [
  { key: "schedule", label: "Schedule" },
  { key: "gq", label: "Game Quality" },
  { key: "trends", label: "Trends" },
];

export default function NBABoxScores() {
  const [tab, setTab] = useDiveState<Tab>("tab", "schedule");
  const [season, setSeason] = useDiveState<number | null>("season", DEFAULT_SEASON);
  const [seasonType, setSeasonType] = useDiveState<SeasonTypeFilter>("type", "all");
  const [team, setTeam] = useDiveState<string>("team", "");
  const [player, setPlayer] = useDiveState<string>("player", "");
  const [page, setPage] = useDiveState<number>("page", 0);
  const [gameId, setGameId] = useDiveState<string | null>("game", null);

  // Fall back to Schedule if a stale/unknown tab is in the URL state.
  const activeTab: Tab = TABS.some((t) => t.key === tab) ? tab : "schedule";

  const filters: Filters = { season, seasonType, team, player };

  const onFilterChange = (patch: Partial<Filters>) => {
    if ("season" in patch) setSeason(patch.season ?? null);
    if ("seasonType" in patch) setSeasonType(patch.seasonType!);
    if ("team" in patch) setTeam(patch.team!);
    if ("player" in patch) setPlayer(patch.player!);
    setPage(0); // any filter change returns to the first page
  };

  return (
    <div
      className="p-6"
      style={{ background: COLORS.bg, minHeight: "100vh", color: COLORS.text, fontFamily: MONO }}
    >
      <header className="mb-3">
        <h1 className="text-2xl font-bold" style={{ color: COLORS.text }}>
          NBA Box Scores
        </h1>
        <p className="text-sm" style={{ color: COLORS.muted }}>
          Schedule, box scores, and player analytics — 2000-01 through 2025-26.
        </p>
      </header>

      {/* Tab navigation */}
      <div className="flex gap-1 mb-4" style={{ borderBottom: `1px solid ${COLORS.border}` }}>
        {TABS.map((t) => {
          const active = activeTab ===t.key;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className="px-4 py-2 text-sm font-medium"
              style={{
                color: active ? COLORS.primary : COLORS.muted,
                borderBottom: active ? `2px solid ${COLORS.primary}` : "2px solid transparent",
                marginBottom: -1,
              }}
            >
              {t.label}
            </button>
          );
        })}
      </div>

      <SeasonFilter filters={filters} onChange={onFilterChange} />

      {activeTab ==="schedule" && (
        <ScheduleGrid
          filters={filters}
          page={page}
          setPage={(fn) => setPage(fn)}
          onSelectGame={(id) => setGameId(id)}
        />
      )}
      {activeTab ==="gq" && <GameQualityExplorer filters={filters} />}
      {activeTab ==="trends" && <TrendsScatter filters={filters} />}

      <BoxScorePanel gameId={gameId} onClose={() => setGameId(null)} />
    </div>
  );
}
