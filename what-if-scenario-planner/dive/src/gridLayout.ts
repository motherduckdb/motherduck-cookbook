// The grid's row-order model (pure: no Univer, no React; e2e/grid-layout.test.mjs).
// Every mapping between a sheet row and what it holds goes through a Layout:
//   row 0                     the header
//   rows 1 .. visible         the data rows the metric filter shows, in sort order
//   the next 3 rows           the Demand / Supply / Inventory total rows (pinned at
//                             the bottom of what's shown; a filtered-out metric's
//                             total row is hidden)
//   the remaining rows        the filtered-out metrics' data rows, hidden (kept,
//                             not removed: the inventory formulas read them)
// Keeping the hidden rows below the shown ones means a paste, fill or delete over
// the rows you can see never lands in a hidden row: the shown rows are one block.
// A data row is a region x category (index into the cells passed in) and a metric.
import { HORIZON, type OverrideMetric, type SupplyCell } from "./engine";
import type { SortColumn } from "./GridSort";

export const METRICS = ["Demand", "Supply", "Inventory"];
export const DEMAND = 0;
export const SUPPLY = 1;
export const INVENTORY = 2;
export const START_COL = 3; // On hand
export const FIRST_WEEK_COL = 4;
export const TOTAL_COL = FIRST_WEEK_COL + HORIZON;
/** The editable metrics by metric row: Demand and Supply (Inventory is a formula). */
export const EDITABLE_AT: Array<OverrideMetric | undefined> = ["demand", "supply", undefined];

export type Slot = { kind: "data"; cell: number; metric: number } | { kind: "total"; metric: number };
export type Layout = {
  /** Region x category count. */
  n: number;
  /** Sheet row r >= 1 holds slots[r - 1]. */
  slots: Slot[];
  /** Data rows shown: sheet rows 1 .. visible. */
  visible: number;
  /** cell * 3 + metric -> sheet row. */
  dataRow: number[];
  /** metric -> sheet row of its total row. */
  totalRow: number[];
  /** Sheet rows hidden by the metric filter, ascending. */
  hidden: number[];
  /** The last sheet row in use. */
  lastRow: number;
};

export const colName = (i: number) => {
  let s = "";
  for (let n = i + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
};

/** A column's sort key: metric, region, category, onhand, w1 .. w26, total. */
export function colKey(c: number) {
  if (c === 0) return "metric";
  if (c === 1) return "region";
  if (c === 2) return "category";
  if (c === START_COL) return "onhand";
  if (c === TOTAL_COL) return "total";
  return `w${c - FIRST_WEEK_COL + 1}`;
}
export const SORT_KEYS = Array.from({ length: TOTAL_COL + 1 }, (_, c) => colKey(c));
export const isSortKey = (k: string) => SORT_KEYS.includes(k);

/** What a data row shows in a column (null = blank); what sorting compares. */
export function rowValue(cell: SupplyCell, metric: number, col: string): string | number | null {
  if (col === "metric") return METRICS[metric];
  if (col === "region") return cell.region;
  if (col === "category") return cell.category;
  if (col === "onhand") return metric === INVENTORY ? cell.start_inventory : null;
  const series = metric === DEMAND ? cell.demand : metric === SUPPLY ? cell.receipts : cell.inventory;
  if (col === "total") return metric === INVENTORY ? null : series.reduce((a, b) => a + b, 0);
  const w = Number(col.slice(1)) - 1;
  return w >= 0 && w < HORIZON ? series[w] ?? null : null;
}

/**
 * Data rows in sort order: a stable multi-column sort (ties keep the
 * region x category x metric order, which is also the order with no sort).
 * Blanks sort last in both directions, as in DuckDB (the pivot sorts in SQL).
 */
export function sortRows(cells: SupplyCell[], rows: Array<{ cell: number; metric: number }>, sort: SortColumn[]) {
  if (!sort.length) return rows.slice();
  const keyed = rows.map((r, i) => ({ r, i, k: sort.map((s) => rowValue(cells[r.cell], r.metric, s.col)) }));
  keyed.sort((a, b) => {
    for (let j = 0; j < sort.length; j++) {
      const x = a.k[j];
      const y = b.k[j];
      if (x === y) continue;
      if (x == null) return 1;
      if (y == null) return -1;
      const d = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      if (d) return sort[j].direction === "asc" ? d : -d;
    }
    return a.i - b.i;
  });
  return keyed.map((x) => x.r);
}

export function buildLayout(cells: SupplyCell[], metricSel: string[], sort: SortColumn[]): Layout {
  const n = cells.length;
  const shown = (m: number) => !metricSel.length || metricSel.includes(METRICS[m]);
  const all: Array<{ cell: number; metric: number }> = [];
  for (let i = 0; i < n; i++) for (let m = DEMAND; m <= INVENTORY; m++) all.push({ cell: i, metric: m });
  const sorted = sortRows(cells, all, sort);
  const vis = sorted.filter((r) => shown(r.metric));
  const hid = sorted.filter((r) => !shown(r.metric));
  const slots: Slot[] = [
    ...vis.map((r) => ({ kind: "data" as const, ...r })),
    ...[DEMAND, SUPPLY, INVENTORY].map((metric) => ({ kind: "total" as const, metric })),
    ...hid.map((r) => ({ kind: "data" as const, ...r })),
  ];
  const dataRow = new Array(3 * n).fill(-1);
  const totalRow = [0, 0, 0];
  const hidden: number[] = [];
  slots.forEach((s, k) => {
    const r = k + 1;
    if (s.kind === "data") dataRow[s.cell * 3 + s.metric] = r;
    else totalRow[s.metric] = r;
    if (!shown(s.metric)) hidden.push(r);
  });
  return { n, slots, visible: vis.length, dataRow, totalRow, hidden, lastRow: slots.length };
}

/** Same rows in the same places (nothing to rewrite). */
export const sameLayout = (a: Layout, b: Layout) =>
  a.n === b.n && a.visible === b.visible && a.slots.every((s, k) => {
    const t = b.slots[k];
    return s.kind === t.kind && s.metric === t.metric && (s.kind === "total" || (t.kind === "data" && s.cell === t.cell));
  });

export const slotAt = (L: Layout, r: number): Slot | undefined => (r >= 1 ? L.slots[r - 1] : undefined);
export const rowOf = (L: Layout, cell: number, metric: number) => L.dataRow[cell * 3 + metric];
export const isHidden = (L: Layout, r: number) => L.hidden.includes(r);

export type CellKind =
  | { kind: "edit"; metric: OverrideMetric; cell: number; week: number }
  | { kind: "inventory"; cell: number; week: number }
  | { kind: "locked" }
  | { kind: "none" };

/** What sheet cell (r, c) is: an editable Demand / Supply week, an Inventory week, or another locked cell. */
export function classify(L: Layout, r: number, c: number): CellKind {
  if (r == null || c == null || r < 0 || c < 0) return { kind: "none" };
  const s = slotAt(L, r);
  if (s?.kind === "data" && c >= FIRST_WEEK_COL && c < TOTAL_COL) {
    const week = c - FIRST_WEEK_COL;
    const metric = EDITABLE_AT[s.metric];
    return metric ? { kind: "edit", metric, cell: s.cell, week } : { kind: "inventory", cell: s.cell, week };
  }
  return { kind: "locked" };
}

/** A1 of a region x category's metric row in a column. */
const a1 = (L: Layout, cell: number, metric: number, col: number) => `${colName(col)}${rowOf(L, cell, metric) + 1}`;

/** End-of-week inventory: =MAX(0, previous week + supply [+ subcontract] - demand); week 1 reads the On hand column. */
export function inventoryFormula(L: Layout, cell: number, w: number, sub: number) {
  const c = FIRST_WEEK_COL + w;
  return `=MAX(0,${a1(L, cell, INVENTORY, c - 1)}+${a1(L, cell, SUPPLY, c)}${sub > 0 ? `+${sub}` : ""}-${a1(L, cell, DEMAND, c)})`;
}

/** A Demand / Supply row's 26-week total. */
export const rowTotalFormula = (r: number) => `=SUM(${colName(FIRST_WEEK_COL)}${r + 1}:${colName(TOTAL_COL - 1)}${r + 1})`;

/** A total row's cell: SUMIF on the Metric column over the shown data rows (null = blank). */
export function totalFormula(L: Layout, metric: number, c: number) {
  if ((metric === INVENTORY && c === TOTAL_COL) || (metric !== INVENTORY && c === START_COL)) return null;
  const last = Math.max(L.visible, 1) + 1;
  return `=SUMIF($A$2:$A$${last},"${METRICS[metric]}",${colName(c)}2:${colName(c)}${last})`;
}
