import { useState } from "react";
import { useSQLQuery } from "@motherduck/react-sql-query";
import { Search } from "lucide-react";
import { DB, Filters, SeasonTypeFilter } from "../lib/query";
import { N, seasonLabel, sqlStr, COLORS } from "../lib/format";

const SEASON_TYPES: { value: SeasonTypeFilter; label: string }[] = [
  { value: "all", label: "All Games" },
  { value: "regular", label: "Regular Season" },
  { value: "playoffs", label: "Playoffs" },
];

const selectCls = "px-2 py-1 rounded border text-sm";
const selectStyle = { borderColor: COLORS.border, background: "#fff", color: COLORS.text };

export default function SeasonFilter({
  filters,
  onChange,
}: {
  filters: Filters;
  onChange: (patch: Partial<Filters>) => void;
}) {
  const [term, setTerm] = useState(filters.player);
  const [open, setOpen] = useState(false);

  const seasonsQ = useSQLQuery(
    `SELECT DISTINCT season_year FROM ${DB}."schedule" ORDER BY season_year DESC`,
  );
  const teamsQ = useSQLQuery(
    `SELECT abbr FROM (
       SELECT home_team_abbreviation AS abbr FROM ${DB}."schedule"
       UNION SELECT away_team_abbreviation FROM ${DB}."schedule"
     ) WHERE abbr IS NOT NULL ORDER BY abbr`,
  );

  const trimmed = term.trim();
  const suggestQ = useSQLQuery(
    `SELECT arg_max(b.player_name, s.game_date) AS player_name,
            arg_max(b.team_abbreviation, s.game_date) AS team
     FROM ${DB}."box_scores" b JOIN ${DB}."schedule" s ON b.game_id = s.game_id
     WHERE b.period = 'FullGame' AND b.player_name ILIKE ${sqlStr("%" + trimmed + "%")}
     ${filters.season != null ? `AND s.season_year = ${Number(filters.season)}` : ""}
     GROUP BY b.entity_id ORDER BY player_name LIMIT 8`,
    { enabled: open && trimmed.length >= 2 },
  );

  const seasons = (Array.isArray(seasonsQ.data) ? seasonsQ.data : []).map((r) => N(r.season_year));
  const teams = (Array.isArray(teamsQ.data) ? teamsQ.data : []).map((r) => String(r.abbr));
  const suggestions = Array.isArray(suggestQ.data) ? suggestQ.data : [];

  const hasFilters =
    filters.season != null || filters.seasonType !== "all" || !!filters.team || !!filters.player;

  const clearAll = () => {
    setTerm("");
    onChange({ season: null, seasonType: "all", team: "", player: "" });
  };

  return (
    <div
      className="sticky top-0 z-20 pt-3 pb-3 mb-5"
      style={{ background: COLORS.bg, borderBottom: `1px solid ${COLORS.border}` }}
    >
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <select
            className={selectCls}
            style={selectStyle}
            value={filters.season ?? ""}
            onChange={(e) => onChange({ season: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">All Seasons</option>
            {seasons.map((y) => (
              <option key={y} value={y}>
                {seasonLabel(y)}
              </option>
            ))}
          </select>

          <select
            className={selectCls}
            style={selectStyle}
            value={filters.seasonType}
            onChange={(e) => onChange({ seasonType: e.target.value as SeasonTypeFilter })}
          >
            {SEASON_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>

          <select
            className={selectCls}
            style={selectStyle}
            value={filters.team}
            onChange={(e) => onChange({ team: e.target.value })}
          >
            <option value="">All Teams</option>
            {teams.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>

          <div className="relative">
            <Search
              size={15}
              style={{ color: COLORS.muted, position: "absolute", left: 8, top: 8 }}
            />
            <input
              type="text"
              value={term}
              placeholder="Search player…"
              autoComplete="off"
              className="pl-7 pr-2 py-1 rounded border text-sm w-44"
              style={selectStyle}
              onFocus={() => setOpen(true)}
              onChange={(e) => {
                setTerm(e.target.value);
                setOpen(true);
                if (e.target.value === "") onChange({ player: "" });
              }}
            />
            {open && suggestions.length > 0 && (
              <div
                className="absolute top-full left-0 mt-1 w-64 rounded shadow-lg z-30 max-h-60 overflow-y-auto"
                style={{ background: "#fff", border: `1px solid ${COLORS.border}` }}
              >
                {suggestions.map((s) => (
                  <button
                    key={String(s.player_name)}
                    className="w-full text-left px-3 py-1.5 text-sm flex items-center justify-between hover:bg-gray-100"
                    onMouseDown={(e) => {
                      e.preventDefault();
                      setTerm(String(s.player_name));
                      onChange({ player: String(s.player_name) });
                      setOpen(false);
                    }}
                  >
                    <span style={{ color: COLORS.text }}>{String(s.player_name)}</span>
                    <span className="text-xs ml-2" style={{ color: COLORS.muted }}>
                      {String(s.team ?? "")}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>

        {hasFilters && (
          <button
            onClick={clearAll}
            className="text-sm"
            style={{ color: COLORS.primary }}
          >
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
