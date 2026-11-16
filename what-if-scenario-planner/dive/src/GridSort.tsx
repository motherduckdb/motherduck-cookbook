// Multi-column sorting for the grid's header row, taken from the MotherDuck
// pivot Dive's drilldown table, next to ./GridFilterDropdown (its filter).
// Verbatim from the pivot: SortColumn, cycleSortColumn (a click cycles a column
// none -> desc -> asc -> none; a newly sorted column goes to the end of the sort
// list, so the list order is the sort priority) and SortIndicator (the green
// triangle with the column's priority number).
// Adapted: the pivot sorts on a double-click of an HTML <th>; this grid's
// header is a canvas, so SortHeader is a transparent button laid over a header
// cell (one click cycles it), showing the indicator, or a faint sort hint on
// hover. The sorting itself is ./gridLayout's sortRows (the pivot sorts in SQL).
import * as React from "react";
import { ArrowUpDown } from "lucide-react";
import { C } from "./theme";

/* ── Shared sort types & utilities (pivot Dive) ── */
export type SortColumn = { col: string; direction: 'asc' | 'desc' };

export function cycleSortColumn(prev: SortColumn[], col: string): SortColumn[] {
  const idx = prev.findIndex(s => s.col === col);
  if (idx === -1) return [...prev, { col, direction: 'desc' }];
  if (prev[idx].direction === 'desc') {
    const next = [...prev];
    next[idx] = { col, direction: 'asc' };
    return next;
  }
  return prev.filter(s => s.col !== col);
}

export function SortIndicator({ direction, order }: { direction: 'asc' | 'desc'; order: number }) {
  const isAsc = direction === 'asc';
  return (
    <svg width={14} height={14} viewBox="0 0 16 16" style={{ flexShrink: 0, verticalAlign: 'middle' }}>
      {isAsc ? (
        <polygon points="8,1 15,15 1,15" fill="#22a34a" />
      ) : (
        <polygon points="8,15 15,1 1,1" fill="#22a34a" />
      )}
      <text x="8" y={isAsc ? "12.5" : "9"} textAnchor="middle" fill="#fff" fontSize="8" fontWeight="bold"
        style={{ userSelect: 'none' }}>{order}</text>
    </svg>
  );
}

/** A sort list read back from saved state: drops malformed entries and duplicate columns. */
export function cleanSort(raw: unknown, valid: (col: string) => boolean): SortColumn[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: SortColumn[] = [];
  for (const s of raw) {
    if (!s || typeof s.col !== "string" || (s.direction !== "asc" && s.direction !== "desc") || seen.has(s.col) || !valid(s.col)) continue;
    seen.add(s.col);
    out.push({ col: s.col, direction: s.direction });
  }
  return out;
}

const HINT_CSS = `.sp-sort:hover .sp-sort-hint, .sp-sort:focus-visible .sp-sort-hint { opacity: 0.7 !important; }
.sp-sort:focus-visible { outline: 2px solid ${C.darkerSky}; outline-offset: -2px; }`;
/** The hover rule for SortHeader's hint; render once per grid. */
export const SortHeaderStyle = () => <style>{HINT_CSS}</style>;

/**
 * A header cell's click target: cycles the column's sort. The indicator (or the
 * hover hint) sits at the right, `inset` px in from the cell's edge (room for a
 * filter button).
 */
export function SortHeader({ label, sort, inset = 0, onClick, style }: {
  label: string; sort: { direction: 'asc' | 'desc'; order: number } | null; inset?: number; onClick: () => void; style?: React.CSSProperties;
}) {
  const next = !sort ? "descending" : sort.direction === "desc" ? "ascending" : "off";
  return (
    <button type="button" className="sp-sort" data-testid={`grid-sort-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}
      aria-label={`Sort by ${label}${sort ? ` (${sort.direction === "asc" ? "ascending" : "descending"}, priority ${sort.order})` : ""}`}
      title={`Sort by ${label}: click for ${next}${sort ? "" : " (adds to the current sort)"}`}
      onMouseDown={(e) => e.stopPropagation()}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      style={{
        position: "absolute", display: "flex", alignItems: "center", justifyContent: "flex-end", padding: `0 ${inset + 2}px 0 0`,
        margin: 0, border: 0, background: "transparent", cursor: "pointer", color: C.darkGrey, ...style,
      }}>
      {sort ? <SortIndicator direction={sort.direction} order={sort.order} />
        : <ArrowUpDown className="sp-sort-hint" size={11} style={{ opacity: 0, flexShrink: 0 }} />}
    </button>
  );
}
