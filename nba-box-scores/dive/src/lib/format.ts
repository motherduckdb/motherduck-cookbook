// Shared formatting + SQL helpers for the NBA Box Scores dive.

// DuckDB returns BIGINT/HUGEINT/DECIMAL as BigInt or special objects — coerce everything.
export const N = (v: unknown): number => (v != null ? Number(v) : 0);

// Single-quote a string literal for inline SQL (doubles embedded quotes).
export const sqlStr = (s: string): string => `'${String(s).replace(/'/g, "''")}'`;

// season_year = 2025 means the 2025-26 season.
export const seasonLabel = (year: number): string =>
  `${year}-${String((year + 1) % 100).padStart(2, "0")}`;

// period values are '1'..'8' (regulation 1-4, then OT) plus 'FullGame'.
export const periodLabel = (p: string): string => {
  const n = Number(p);
  if (n >= 1 && n <= 4) return `Q${n}`;
  if (n === 5) return "OT";
  if (n > 5) return `${n - 4}OT`;
  return p;
};

// "MM:SS" -> total seconds (for client-side sorting / fallbacks).
export const minutesToSeconds = (min: string | null | undefined): number => {
  if (!min) return 0;
  const [m, s] = String(min).split(":").map(Number);
  return (m || 0) * 60 + (s || 0);
};

// Matches the legacy nba-box-scores look: monospace, white background, gray
// palette, blue-600 links/accents, amber-500 playoff marker.
export const MONO =
  'ui-monospace, SFMono-Regular, Menlo, Monaco, "Cascadia Code", "Roboto Mono", Consolas, "Courier New", monospace';

export const COLORS = {
  bg: "#ffffff",
  card: "#ffffff",
  text: "#171717", // gray-900
  muted: "#6b7280", // gray-500
  border: "#e5e7eb", // gray-200
  primary: "#2563eb", // blue-600
  playoff: "#f59e0b", // amber-500
  headerBg: "#f3f4f6", // gray-100
  altRow: "#f9fafb", // gray-50
  totalBg: "#eff6ff", // blue-50
};
