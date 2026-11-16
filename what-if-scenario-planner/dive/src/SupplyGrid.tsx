// The supply and demand plan as a real spreadsheet (Univer). Each region x
// category gets three rows:
//   Demand     weekly demand from the scenario's demand source (and the levers'
//              upside)
//   Supply     planned weekly receipts
//   Inventory  end-of-week on-hand stock, a live formula on the two rows above:
//              =MAX(0, previous week + supply [+ subcontract units] - demand),
//              starting from the on-hand column. Locked: you can open a cell to see
//              which cells feed it, but any change is put back.
// Demand and Supply edit the same way (one path, keyed by metric): type, paste or
// use a formula to override a week (yellow), delete to revert; saved to MotherDuck.
// Hovering an overridden cell shows who made the edit. Overrides made in this
// session are a stronger yellow than ones saved earlier.
// The header row's Metric / Region / Category cells carry filter dropdowns
// (./GridFilterDropdown, an HTML overlay on the canvas). Region and category
// narrow the cells the parent passes in; metric hides the other metrics' rows
// (hidden, not removed, so the inventory formulas still read them).
// Every header cell sorts (./GridSort, the pivot Dive's multi-column sort): a
// click cycles none -> descending -> ascending -> none and appends the column to
// the sort list. Sorting reorders individual rows; the totals stay at the bottom.
//
// Rows: which sheet row holds which region x category and metric is a Layout
// (./gridLayout); every row <-> cell mapping goes through it (edits, formulas,
// totals, colours, tooltips, hidden rows). The shown rows come first, then the
// total rows, then the hidden rows, so a paste, fill or delete over shown rows
// never touches a hidden one (a write that does reach a hidden row is put back).
// A new layout (sort or metric filter change) is written into the same sheet:
// values, formulas and styles of every row, as raw mutations, then the undo
// history is cleared (its entries name rows that now hold other cells). The
// sheet keeps its scroll position. Sorting never sends an edit.
// When the order is recomputed: on a sort change, a metric filter change, a lever
// or data reload (resortKey) and on mount (scenario / region / category change).
// Not on the planner's own edits: an edited row stays where it is, even if the
// new value now sorts elsewhere, until one of those happens.
import * as React from "react";
import { useEffect, useRef, useState } from "react";
import { loadUniver } from "./univer-runtime";
import { HORIZON, overrideKey, type OverrideMetric, type SupplyCell } from "./engine";
import { C } from "./theme";
import { ColumnFilterDropdown, FilterButton, useApplyOnOutsideClick } from "./GridFilterDropdown";
import { SortHeader, SortHeaderStyle, cycleSortColumn, type SortColumn } from "./GridSort";
import {
  DEMAND, FIRST_WEEK_COL, INVENTORY, METRICS, START_COL, SUPPLY, TOTAL_COL, buildLayout, classify, colKey, inventoryFormula,
  isHidden, rowOf, rowTotalFormula, sameLayout, slotAt, totalFormula, type Layout,
} from "./gridLayout";

export { METRICS };

/** A planner edit of one demand or supply cell; units null clears the override. */
export type CellEdit = { metric: OverrideMetric; region: string; category: string; week_idx: number; units: number | null };

export type EditInfo = { by?: string; at?: string; session: boolean };

/** A header-row filter on one of the label columns; value [] = all. */
export type GridFilter = { col: number; label: string; options: string[]; value: string[]; onChange: (v: string[]) => void };

type Props = {
  cells: SupplyCell[];
  weeks: string[];
  editable: boolean;
  /** Weeks that run short with no action (header tint). */
  shortWeeks: Set<number>;
  /** overrideKey(metric, region, category, week) -> who made the override, and whether it was this session. */
  edits: Record<string, EditInfo>;
  onEdit: (edits: CellEdit[]) => void;
  onBlocked: (message: string) => void;
  /** Dropdowns on the header row's label cells. */
  filters?: GridFilter[];
  /** Metrics shown (empty = all); the other metrics' rows, and their total rows, are hidden. */
  metricSel?: string[];
  /** The rows' sort (gridLayout's column keys), kept by the parent so it outlives a remount. */
  sort?: SortColumn[];
  onSort?: (sort: SortColumn[]) => void;
  /** Changes when the plan changes for a reason other than a grid edit (levers, a reload): the rows re-sort. */
  resortKey?: string;
};

const UNIT_ID = "supply-plan";
const SHEET_ID = "supply";
const NO_SORT: SortColumn[] = [];
const byMetric = <T,>(f: (m: OverrideMetric) => T) => ({ demand: f("demand"), supply: f("supply") });
/** The engine's numbers on an editable row. */
const rowValues = (cell: SupplyCell, m: OverrideMetric) => (m === "demand" ? cell.demand : cell.receipts);
const METRIC_ROW: Record<OverrideMetric, number> = { demand: DEMAND, supply: SUPPLY };
const EDITABLE: OverrideMetric[] = ["demand", "supply"];

// Light tints of the chart's series colours (demand ink, supply sky, inventory duck).
export const ROW_BG = ["#EFEFEE", "#E4F3FF", "#FFEEDC"];
export const SERIES = { demand: C.ink, supply: C.darkerSky, inventory: C.duck };
// An overridden Demand or Supply week, in place of its row tint (bold):
export const OVERRIDE_NOW = C.lighterSun; // edited in this session
export const OVERRIDE_PREV = "#FCF8C4"; // edited in an earlier session
const SHORT_BG = "#FFDCD9";
const MSG = {
  inventory: "Inventory is calculated from demand and supply, so it can't be edited.",
  locked: "Only the Demand and Supply rows can be edited.",
};

const weekLabel = (iso: string) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const headerLabel = (c: number, weeks: string[]) =>
  c < FIRST_WEEK_COL ? ["Metric", "Region", "Category", "On hand"][c] : c === TOTAL_COL ? "26-wk total" : `W${c - FIRST_WEEK_COL + 1} · ${weekLabel(weeks[c - FIRST_WEEK_COL])}`;
const normF = (f: string) => f.replace(/\s+/g, "").toUpperCase();
/** getValue() can return the formatted text ("1,152") of a number cell. */
const toNum = (v: any) => (typeof v === "string" ? Number(v.replace(/[,\s]/g, "")) : Number(v));

type OvState = 0 | 1 | 2; // none, earlier session, this session
const ovState = (cell: SupplyCell, m: OverrideMetric, w: number, edits: Record<string, EditInfo>): OvState =>
  !cell.overridden[m][w] ? 0 : edits[overrideKey(m, cell.region, cell.category, w)]?.session ? 2 : 1;
const ovStates = (cells: SupplyCell[], m: OverrideMetric, edits: Record<string, EditInfo>) =>
  cells.map((c) => c.overridden[m].map((_, w) => ovState(c, m, w, edits)));
const editStyle = (m: OverrideMetric, s: OvState) => (s === 2 ? "ovNow" : s === 1 ? "ovPrev" : m === "demand" ? "dNum" : "sNum");

type Expected = {
  /** The engine's numbers and override states on the editable rows, [cell][week]. */
  nums: Record<OverrideMetric, number[][]>;
  ov: Record<OverrideMetric, OvState[][]>;
  sub: number[];
  short: Set<number>;
  /** "row|col" -> the formula a locked formula cell must hold. */
  formulas: Map<string, string>;
  /** "row|col" -> the value a locked plain cell must hold (labels, on-hand, blanks). */
  values: Map<string, string | number>;
};
const expectedOf = (cells: SupplyCell[], edits: Record<string, EditInfo>, short: Set<number>, formulas: Map<string, string>, values: Map<string, string | number>): Expected => ({
  nums: byMetric((m) => cells.map((c) => rowValues(c, m).slice())),
  ov: byMetric((m) => ovStates(cells, m, edits)),
  sub: cells.map((c) => c.subcontract[0] ?? 0),
  short,
  formulas,
  values,
});

const NUMFMT = { pattern: "#,##0" };
const line = (cl: string) => ({ b: { s: 1, cl: { rgb: cl } } });
const GROUP_END = line(C.darkSand);
const BASE = { fs: 10, cl: { rgb: C.ink } };
const num = (bg: string, extra: any = {}) => ({ ...BASE, n: NUMFMT, ht: 3, bg: { rgb: bg }, ...extra });
const STYLES = {
  // headers: left-aligned, clear of the sort indicator (and the label columns' filter button) at the right
  hdr: { ...BASE, bl: 1, bg: { rgb: C.sand }, bd: line(C.ink), ht: 1 },
  hdrShort: { ...BASE, bl: 1, bg: { rgb: SHORT_BG }, bd: line(C.ink), ht: 1 },
  dLabel: { ...BASE, bg: { rgb: ROW_BG[DEMAND] } },
  sLabel: { ...BASE, bg: { rgb: ROW_BG[SUPPLY] } },
  iLabel: { ...BASE, bg: { rgb: ROW_BG[INVENTORY] }, bd: GROUP_END },
  dNum: num(ROW_BG[DEMAND]),
  sNum: num(ROW_BG[SUPPLY]),
  iNum: num(ROW_BG[INVENTORY], { bd: GROUP_END }),
  ovPrev: num(OVERRIDE_PREV, { bl: 1 }),
  ovNow: num(OVERRIDE_NOW, { bl: 1 }),
  dTot: num(ROW_BG[DEMAND], { bl: 1 }),
  sTot: num(ROW_BG[SUPPLY], { bl: 1 }),
  iTot: num(ROW_BG[INVENTORY], { bl: 1, bd: GROUP_END }),
  totLabel: { ...BASE, bl: 1, bg: { rgb: C.snow } },
  totNum: num(C.snow, { bl: 1 }),
};

/** Every cell of the sheet in a layout, and what the locked ones must hold. */
function buildCells(L: Layout, cells: SupplyCell[], weeks: string[], short: Set<number>, edits: Record<string, EditInfo>) {
  const cellData: Record<number, Record<number, any>> = {};
  const formulas = new Map<string, string>();
  const values = new Map<string, string | number>();
  const put = (r: number, c: number, cell: any, locked = true) => {
    (cellData[r] ??= {})[c] = cell;
    if (!locked) return;
    if (cell.f) formulas.set(`${r}|${c}`, cell.f);
    else values.set(`${r}|${c}`, cell.v ?? "");
  };
  for (let c = 0; c <= TOTAL_COL; c++) {
    const w = c - FIRST_WEEK_COL;
    put(0, c, { v: headerLabel(c, weeks), s: w >= 0 && w < HORIZON && short.has(w) ? "hdrShort" : "hdr" });
  }
  L.slots.forEach((s, k) => {
    const r = k + 1;
    if (s.kind === "total") {
      // Totals over the rows shown: SUMIF on the Metric column.
      put(r, 0, { v: METRICS[s.metric], s: "totLabel" });
      put(r, 1, { v: "Total", s: "totLabel" });
      put(r, 2, { v: "All shown", s: "totLabel" });
      for (let c = START_COL; c <= TOTAL_COL; c++) {
        const f = totalFormula(L, s.metric, c);
        put(r, c, f ? { f, s: "totNum" } : { v: "", s: "totNum" });
      }
      return;
    }
    const cell = cells[s.cell];
    const m = s.metric;
    const k3 = ["d", "s", "i"][m];
    const sub = cell.subcontract[0] ?? 0;
    put(r, 0, { v: METRICS[m], s: `${k3}Label` });
    put(r, 1, { v: cell.region, s: `${k3}Label` });
    put(r, 2, { v: cell.category, s: `${k3}Label` });
    put(r, START_COL, m === INVENTORY ? { v: cell.start_inventory, s: "iNum" } : { v: "", s: `${k3}Num` });
    const em = m === DEMAND ? "demand" : m === SUPPLY ? "supply" : null;
    for (let w = 0; w < HORIZON; w++) {
      const c = FIRST_WEEK_COL + w;
      if (em) put(r, c, { v: rowValues(cell, em)[w], s: editStyle(em, ovState(cell, em, w, edits)) }, false);
      else put(r, c, { f: inventoryFormula(L, s.cell, w, sub), s: "iNum" });
    }
    put(r, TOTAL_COL, m === INVENTORY ? { v: "", s: "iTot" } : { f: rowTotalFormula(r), s: `${k3}Tot` });
  });
  return { cellData, formulas, values };
}

/** The grid's style id for sheet cell (r, c) (undefined past the plan); `ov` gives an editable week's override state. */
function styleAt(L: Layout, r: number, c: number, short: Set<number>, ov: (m: OverrideMetric, cell: number, w: number) => OvState) {
  if (c < 0 || c > TOTAL_COL) return undefined;
  const w = c - FIRST_WEEK_COL;
  if (r === 0) return w >= 0 && w < HORIZON && short.has(w) ? "hdrShort" : "hdr";
  const s = slotAt(L, r);
  if (!s) return undefined;
  if (s.kind === "total") return c < START_COL ? "totLabel" : "totNum";
  const k3 = ["d", "s", "i"][s.metric];
  if (c < START_COL) return `${k3}Label`;
  if (c === START_COL) return s.metric === INVENTORY ? "iNum" : `${k3}Num`;
  if (c === TOTAL_COL) return `${k3}Tot`;
  const em = s.metric === DEMAND ? "demand" : s.metric === SUPPLY ? "supply" : null;
  return em ? editStyle(em, ov(em, s.cell, w)) : "iNum";
}

function buildSnapshot(L: Layout, built: ReturnType<typeof buildCells>) {
  const columnData: Record<number, any> = { 0: { w: 78 }, 1: { w: 84 }, 2: { w: 100 }, [START_COL]: { w: 70 }, [TOTAL_COL]: { w: 96 } };
  for (let w = 0; w < HORIZON; w++) columnData[FIRST_WEEK_COL + w] = { w: 96 }; // room for the sort indicator
  const rowData: Record<number, any> = {};
  for (const r of L.hidden) rowData[r] = { hd: 1 };
  return {
    id: UNIT_ID,
    name: "Supply and demand plan",
    appVersion: "0.25.1",
    locale: "enUS",
    styles: STYLES,
    sheetOrder: [SHEET_ID],
    sheets: {
      [SHEET_ID]: {
        id: SHEET_ID,
        name: "Supply and demand (units)",
        rowCount: L.lastRow + 1 + 4,
        columnCount: TOTAL_COL + 3,
        defaultRowHeight: 22,
        defaultColumnWidth: 80,
        freeze: { xSplit: FIRST_WEEK_COL, ySplit: 1, startRow: 1, startColumn: FIRST_WEEK_COL },
        columnData,
        rowData,
        cellData: built.cellData,
      },
    },
  };
}

type Box = { left: number; top: number; width: number; height: number };
type Touched = { r: number; c: number; metric: OverrideMetric; cell: number; w: number; formula?: boolean };

export default function SupplyGrid({ cells, weeks, editable, shortWeeks, edits, onEdit, onBlocked, filters = [], metricSel = [], sort = NO_SORT, onSort, resortKey = "" }: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const apiRef = useRef<any>(null);
  const univerRef = useRef<any>(null);
  const globalsRef = useRef<any>(null);
  const expectedRef = useRef<Expected | null>(null);
  const layoutRef = useRef<Layout | null>(null);
  const propsRef = useRef({ cells, weeks, editable, edits, onEdit, onBlocked, metricSel, sort });
  propsRef.current = { cells, weeks, editable, edits, onEdit, onBlocked, metricSel, sort };
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");
  // Last value sent per cell ("metric|cell|week"): the formula engine re-emits a
  // result several times; only the first one is a change worth saving.
  const sentRef = useRef<Record<string, { units: number | null; at: number }>>({});
  // What a cell was before its first edit since the last re-sort ("metric|cell|week"):
  // an undo that brings back a number that wasn't an override clears the override.
  const priorRef = useRef<Record<string, { units: number; overridden: boolean }>>({});
  // Last time each locked cell was put back (guards against a revert loop).
  const revertedRef = useRef<Record<string, number>>({});
  // Edits waiting to be read back (formulas settle first). A new layout waits for
  // them; an unmount (filter or scenario change) reads them back at once.
  const pendingRef = useRef(new Set<{ run: () => void }>());
  // True while the grid writes a new layout (those writes are not edits).
  const rewritingRef = useRef(false);
  const relayoutTimer = useRef<any>(null);
  const mouseRef = useRef({ x: 0, y: 0 });
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  // Header overlays (sort and filter): where each header cell sits over the canvas,
  // and the scrolling part's visible span (week columns scroll under the frozen ones).
  const [hdr, setHdr] = useState<{ boxes: Record<number, Box>; clipLeft: number; clipRight: number } | null>(null);
  const scrollXRef = useRef(0);
  const measureRaf = useRef(0);
  const [openFilter, setOpenFilter] = useState<number | null>(null);
  const filterApplyRef = useRef<(() => void) | null>(null);
  useApplyOnOutsideClick(openFilter != null, filterApplyRef, () => setOpenFilter(null));
  const hidingRef = useRef(false);

  useEffect(() => {
    let disposed = false;
    const offs: any[] = [];
    (async () => {
      try {
        const g = await loadUniver();
        if (disposed || !hostRef.current) return;
        globalsRef.current = g;
        const { Univer, LocaleType, mergeLocales, UniverInstanceType } = g.UniverCore;
        const univer = new Univer({
          locale: LocaleType.EN_US,
          locales: {
            [LocaleType.EN_US]: mergeLocales(
              g.UniverDesignEnUS, g.UniverUiEnUS, g.UniverDocsUiEnUS, g.UniverSheetsEnUS,
              g.UniverSheetsUiEnUS, g.UniverSheetsFormulaUiEnUS, g.UniverSheetsNumfmtUiEnUS,
            ),
          },
        });
        univer.registerPlugin(g.UniverEngineRender.UniverRenderEnginePlugin);
        univer.registerPlugin(g.UniverEngineFormula.UniverFormulaEnginePlugin);
        univer.registerPlugin(g.UniverUi.UniverUIPlugin, { container: hostRef.current, header: true, toolbar: false, footer: false, contextMenu: true });
        univer.registerPlugin(g.UniverDocs.UniverDocsPlugin);
        univer.registerPlugin(g.UniverDocsUi.UniverDocsUIPlugin);
        univer.registerPlugin(g.UniverSheets.UniverSheetsPlugin);
        univer.registerPlugin(g.UniverSheetsUi.UniverSheetsUIPlugin);
        univer.registerPlugin(g.UniverSheetsFormula.UniverSheetsFormulaPlugin);
        univer.registerPlugin(g.UniverSheetsFormulaUi.UniverSheetsFormulaUIPlugin);
        univer.registerPlugin(g.UniverSheetsNumfmt.UniverSheetsNumfmtPlugin);
        univer.registerPlugin(g.UniverSheetsNumfmtUi.UniverSheetsNumfmtUIPlugin);
        const p = propsRef.current;
        const short = new Set(shortWeeks);
        const L = buildLayout(p.cells, p.metricSel, p.sort);
        const built = buildCells(L, p.cells, weeks, short, p.edits);
        univer.createUnit(UniverInstanceType.UNIVER_SHEET, buildSnapshot(L, built));
        layoutRef.current = L;
        expectedRef.current = expectedOf(p.cells, p.edits, short, built.formulas, built.values);
        const api = g.UniverCoreFacade.FUniver.newAPI(univer);
        univerRef.current = univer;
        apiRef.current = api;
        offs.push(api.addEvent(api.Event.CommandExecuted, (ev: any) => {
          if (ev?.id === "sheet.mutation.set-range-values") handleMutation(ev.params);
          // rows hidden or unhidden from the row header or menu: the metric filter decides which rows show
          else if ((ev?.id === "sheet.mutation.set-row-visible" || ev?.id === "sheet.mutation.set-row-hidden") && !hidingRef.current) setTimeout(() => applyHidden(), 0);
        }));
        // Header cells move when columns are resized, the sheet zooms or scrolls, or the grid resizes.
        for (const name of ["SheetSkeletonChanged", "SheetZoomChanged"]) {
          if (api.Event[name]) offs.push(api.addEvent(api.Event[name], () => measureSoon()));
        }
        if (api.Event.Scroll) {
          offs.push(api.addEvent(api.Event.Scroll, (ev: any) => {
            if (typeof ev?.viewportScrollX === "number") scrollXRef.current = ev.viewportScrollX;
            measureSoon();
          }));
        }
        const ro = new ResizeObserver(() => measureSoon());
        if (wrapRef.current) ro.observe(wrapRef.current);
        offs.push({ dispose: () => ro.disconnect() });
        // Locked cells: no editor, except Inventory weeks, which open so the
        // formula's input cells are highlighted (any change is put back).
        offs.push(api.addEvent(api.Event.BeforeSheetEditStart, (ev: any) => {
          const kind = cellKind(ev?.row, ev?.column);
          if (kind.kind === "inventory") return;
          if (kind.kind === "edit" && propsRef.current.editable) return;
          ev.cancel = true;
          propsRef.current.onBlocked(kind.kind === "edit" ? "" : MSG.locked);
        }));
        const hover = (ev: any) => {
          // CellPointerMove carries the pointer event: position the tooltip from it.
          const pe = ev?.event;
          const box = wrapRef.current?.getBoundingClientRect();
          if (box && pe && typeof pe.clientX === "number") mouseRef.current = { x: pe.clientX - box.left, y: pe.clientY - box.top };
          showTip(ev?.row, ev?.column);
        };
        for (const name of ["CellPointerMove", "CellHover"]) {
          if (api.Event[name]) offs.push(api.addEvent(api.Event[name], hover));
        }
        (window as any).__spGrid = { api, univer, layout: () => layoutRef.current }; // for e2e tests
        setStatus("ready");
        measureSoon();
      } catch (e: any) {
        console.error("[SupplyGrid]", e);
        setError(String(e?.message ?? e));
        setStatus("error");
      }
    })();
    return () => {
      disposed = true;
      clearTimeout(relayoutTimer.current);
      cancelAnimationFrame(measureRaf.current);
      // A filter or scenario change remounts the grid: commit an open cell editor (its
      // write is synchronous) and read back edits still settling first.
      try {
        commitEditor();
        flushPending();
      } catch (e) {
        console.error("[SupplyGrid] flush", e);
      }
      try {
        for (const off of offs) off?.dispose?.();
        univerRef.current?.dispose?.();
      } catch {
        /* ignore */
      }
      univerRef.current = null;
      apiRef.current = null;
      delete (window as any).__spGrid;
    };
    // weeks never change for a mounted grid; the parent remounts it per scenario and filter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function sheet() {
    return apiRef.current?.getActiveWorkbook?.()?.getSheetBySheetId?.(SHEET_ID) ?? apiRef.current?.getActiveWorkbook?.()?.getActiveSheet?.();
  }
  const cellKind = (r: number, c: number) => (layoutRef.current ? classify(layoutRef.current, r, c) : { kind: "none" as const });

  /** Commits an open cell editor (before a filter, sort or remount changes the rows under it). */
  function commitEditor() {
    try {
      const wb = apiRef.current?.getActiveWorkbook?.();
      if (wb?.isCellEditing?.()) return wb.endEditing?.(true);
    } catch {
      /* ignore */
    }
    return undefined;
  }

  function measureSoon() {
    cancelAnimationFrame(measureRaf.current);
    measureRaf.current = requestAnimationFrame(() => measureHeader());
  }

  /** Positions the header overlays over the header cells (canvas coordinates x zoom, minus the scroll for week columns). */
  function measureHeader(retry = 0) {
    if (!apiRef.current) return;
    const ws = sheet();
    const wrap = wrapRef.current;
    const canvas = hostRef.current?.querySelector<HTMLCanvasElement>('canvas[id^="univer-sheet-main-canvas"]'); // not the formula bar's canvas
    // the sheet canvas renders a little after the unit is created
    if (!ws || !wrap || !canvas || !canvas.getBoundingClientRect().width) {
      if (retry < 50) setTimeout(() => measureHeader(retry + 1), 100);
      return;
    }
    const box = wrap.getBoundingClientRect();
    const cv = canvas.getBoundingClientRect();
    const zoom = Number(ws.getZoom?.() ?? 1) || 1;
    const ox = cv.left - box.left - wrap.clientLeft;
    const oy = cv.top - box.top - wrap.clientTop;
    const boxes: Record<number, Box> = {};
    let clipLeft = 0;
    for (let c = 0; c <= TOTAL_COL; c++) {
      try {
        const r = ws.getRange(0, c, 1, 1).getCellRect();
        const scroll = c >= FIRST_WEEK_COL ? scrollXRef.current : 0;
        boxes[c] = { left: ox + (r.left - scroll) * zoom, top: oy + r.top * zoom, width: r.width * zoom, height: r.height * zoom };
        if (c === FIRST_WEEK_COL - 1) clipLeft = ox + r.right * zoom;
      } catch {
        /* not rendered yet */
      }
    }
    const next = { boxes, clipLeft, clipRight: ox + cv.width };
    setHdr((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
  }

  /** Hides the rows the metric filter leaves out and shows the rest (mutations: kept out of undo). */
  function applyHidden() {
    const api = apiRef.current;
    const L = layoutRef.current;
    if (!api || !L) return;
    const range = (r: number) => ({ startRow: r, endRow: r, startColumn: 0, endColumn: TOTAL_COL, rangeType: 1 /* ROW */ });
    const base = { unitId: UNIT_ID, subUnitId: SHEET_ID };
    hidingRef.current = true;
    try {
      api.syncExecuteCommand("sheet.mutation.set-row-visible", { ...base, ranges: [{ ...range(1), endRow: L.lastRow }] });
      if (L.hidden.length) api.syncExecuteCommand("sheet.mutation.set-row-hidden", { ...base, ranges: L.hidden.map(range) });
    } finally {
      hidingRef.current = false;
    }
  }

  /** Writes a new row order into the sheet: every row's values, formulas and styles, then the hidden rows. */
  function applyLayout(next: Layout) {
    const api = apiRef.current;
    const exp = expectedRef.current;
    if (!api || !exp) return;
    const p = propsRef.current;
    const built = buildCells(next, p.cells, p.weeks, exp.short, p.edits);
    const last = Math.max(next.lastRow, layoutRef.current?.lastRow ?? 0);
    const clear: Record<number, Record<number, null>> = {};
    const fill: Record<number, Record<number, any>> = {};
    for (let r = 1; r <= last; r++) {
      clear[r] = {};
      for (let c = 0; c <= TOTAL_COL; c++) clear[r][c] = null;
      if (built.cellData[r]) fill[r] = built.cellData[r];
    }
    // two steps: a set merges styles and keeps keys it doesn't name (a formula, a border)
    writeRaw(clear);
    writeRaw(fill);
    layoutRef.current = next;
    expectedRef.current = expectedOf(p.cells, p.edits, exp.short, built.formulas, built.values);
    revertedRef.current = {};
    applyHidden();
    forgetHistory(); // undo entries name sheet rows, which now hold other cells
  }

  /**
   * The grid's own writes (the plan's numbers, put-backs, styles, a new layout):
   * raw mutations, so they are not edits and stay out of the undo history, which
   * then holds only the planner's own changes (an undo is one keystroke).
   */
  function writeRaw(cellValue: Record<number, Record<number, any>>) {
    const api = apiRef.current;
    if (!api || !Object.keys(cellValue).length) return;
    rewritingRef.current = true;
    try {
      api.syncExecuteCommand("sheet.mutation.set-range-values", { unitId: UNIT_ID, subUnitId: SHEET_ID, cellValue });
    } finally {
      rewritingRef.current = false;
    }
  }

  /** Clears the undo history and what undo would bring back (after a new layout, or the plan's numbers moving under it). */
  function forgetHistory() {
    priorRef.current = {};
    try {
      univerRef.current?.__getInjector?.().get(globalsRef.current.UniverCore.IUndoRedoService).clearUndoRedo(UNIT_ID);
    } catch (e) {
      console.warn("[SupplyGrid] could not clear undo", e);
    }
  }

  /** Recomputes the row order (after an open edit is committed and read back) and writes it if it changed. */
  function requestRelayout(delay = 0) {
    clearTimeout(relayoutTimer.current);
    const run = async () => {
      if (!apiRef.current || !layoutRef.current) return;
      await commitEditor();
      if (pendingRef.current.size) {
        relayoutTimer.current = setTimeout(run, 50);
        return;
      }
      const p = propsRef.current;
      const next = buildLayout(p.cells, p.metricSel, p.sort);
      if (!sameLayout(next, layoutRef.current)) applyLayout(next);
    };
    relayoutTimer.current = setTimeout(run, delay);
  }

  function showTip(r: number, c: number) {
    const k = cellKind(r, c);
    if (k.kind !== "edit") return setTip(null);
    const { cells: cs, edits: ed } = propsRef.current;
    const cell = cs[k.cell];
    if (!cell?.overridden[k.metric][k.week]) return setTip(null);
    const e = ed[overrideKey(k.metric, cell.region, cell.category, k.week)];
    const who = e?.by || "an unknown user";
    const when = e?.session ? "this session"
      : e?.at ? new Date(e.at).toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "";
    setTip({ ...mouseRef.current, text: `Edited by ${who}${when ? ` · ${when}` : ""}` });
  }

  /** Reads back every edit still waiting (formulas that haven't settled are put back, not sent). */
  function flushPending() {
    for (const b of [...pendingRef.current]) b.run();
  }

  function handleMutation(params: any) {
    const exp = expectedRef.current;
    const L = layoutRef.current;
    const cellValue = params?.cellValue;
    if (rewritingRef.current || !exp || !L || !cellValue || (params.subUnitId && params.subUnitId !== SHEET_ID)) return;
    const touched: Touched[] = [];
    const locked: Array<[number, number]> = [];
    const hidden: Touched[] = [];
    // cells a paste or fill restyled (it brings the source's format): the grid's style goes back
    const restyled = new Set<string>();
    let hasFormula = false;
    for (const rs of Object.keys(cellValue)) {
      const r = Number(rs);
      for (const cs of Object.keys(cellValue[rs] ?? {})) {
        const c = Number(cs);
        const cell = cellValue[rs][cs];
        if (!cell || !("v" in cell || "f" in cell || "p" in cell || "si" in cell)) continue; // style-only
        // the formula engine dropping formulas a new layout moved away ({f: null, si: null}): not an edit
        if (!("v" in cell) && !("p" in cell) && !cell.f && !cell.si) continue;
        const kind = classify(L, r, c);
        if (kind.kind === "none") continue;
        if (cell.s != null) restyled.add(`${r}|${c}`);
        if (kind.kind !== "edit") {
          locked.push([r, c]);
          continue;
        }
        const t: Touched = { r, c, metric: kind.metric, cell: kind.cell, w: kind.week };
        if (!cell.f && !cell.si && Number(cell.v) === expectedAt(t) && cell.v !== "" && cell.v != null) continue; // our own write
        // a paste, fill or delete reaching a row the metric filter hides: put it back
        if (isHidden(L, r)) {
          hidden.push(t);
          continue;
        }
        if (cell.f || cell.si) hasFormula = t.formula = true;
        touched.push(t);
      }
    }
    if (locked.length) setTimeout(() => restoreLocked(locked), 60);
    if (hidden.length) setTimeout(() => hidden.forEach(writeExpected), 60);
    const others = [...restyled].filter((k) => !touched.some((t) => `${t.r}|${t.c}` === k));
    if (others.length) setTimeout(() => restoreStyles(others.map((k) => k.split("|").map(Number) as [number, number])), 60);
    if (!touched.length) return;
    // Read back after the formula engine settles, so pasted formulas resolve.
    const batch = {
      run: () => {
        if (!pendingRef.current.delete(batch)) return;
        apply();
      },
    };
    pendingRef.current.add(batch);
    const settle = (attempt: number) => {
      if (!pendingRef.current.has(batch)) return;
      const ws = sheet();
      const pending = hasFormula && attempt < 15 && touched.some((t) => {
        const v = ws?.getRange(t.r, t.c, 1, 1)?.getValue?.();
        return v == null || v === "" || (typeof v === "string" && v.startsWith("=")) || toNum(v) === expectedAt(t);
      });
      if (pending) setTimeout(() => settle(attempt + 1), 100);
      else batch.run();
    };
    const apply = () => {
      const { cells: cs, editable, onEdit, onBlocked } = propsRef.current;
      const restyle = touched.filter((t) => restyled.has(`${t.r}|${t.c}`));
      if (!editable) {
        onBlocked("");
        touched.forEach(writeExpected);
        restoreStyles(restyle.map((t) => [t.r, t.c]));
        return;
      }
      const ws = sheet();
      const out: CellEdit[] = [];
      // an override the planner just made shows as this session's; a revert as none
      const after = new Map<string, OvState>();
      // a formula is a way to type a number: the cell keeps the number (an override is a number, and
      // a formula naming other rows would go stale when they change or the rows re-sort)
      const asValue: Record<number, Record<number, any>> = {};
      for (const t of touched) {
        const cell = cs[t.cell];
        let raw: any = ws?.getRange(t.r, t.c, 1, 1)?.getValue?.();
        if (typeof raw === "string") raw = raw.replace(/[,\s]/g, "");
        const blank = raw == null || raw === "";
        const n = Number(raw);
        if (!blank && (!Number.isFinite(n) || n < 0)) {
          writeExpected(t);
          continue;
        }
        if (t.formula && !blank) (asValue[t.r] ??= {})[t.c] = { v: Math.round(n), f: null, si: null };
        const k = `${t.metric}|${t.cell}|${t.w}`;
        const prior = priorRef.current[k];
        let units = blank ? null : Math.round(n);
        if (units != null && prior && !prior.overridden && units === prior.units) units = null; // undone back to the plan's number
        const overridden = cell.overridden[t.metric][t.w];
        if (units != null && units === expectedAt(t) && overridden) continue; // unchanged
        if (units == null && !overridden) {
          writeExpected(t);
          continue;
        }
        const prev = sentRef.current[k];
        if (prev && prev.units === units && Date.now() - prev.at < 5000) continue;
        sentRef.current[k] = { units, at: Date.now() };
        priorRef.current[k] ??= { units: expectedAt(t), overridden };
        after.set(`${t.r}|${t.c}`, units == null ? 0 : 2);
        out.push({ metric: t.metric, region: cell.region, category: cell.category, week_idx: t.w, units });
      }
      writeRaw(asValue);
      restoreStyles(restyle.map((t) => [t.r, t.c]), after);
      if (out.length) onEdit(out);
    };
    setTimeout(() => settle(0), hasFormula ? 120 : 30);
  }

  /** Puts the grid's own style back on cells (raw mutations: not an edit, not in the undo history). */
  function restoreStyles(list: Array<[number, number]>, after?: Map<string, OvState>) {
    const api = apiRef.current;
    const L = layoutRef.current;
    const exp = expectedRef.current;
    if (!api || !L || !exp || !list.length) return;
    const clear: Record<number, Record<number, any>> = {};
    const set: Record<number, Record<number, any>> = {};
    for (const [r, c] of list) {
      const id = styleAt(L, r, c, exp.short, (m, cell, w) => after?.get(`${r}|${c}`) ?? exp.ov[m][cell][w]);
      if (!id) continue;
      (clear[r] ??= {})[c] = { s: null };
      (set[r] ??= {})[c] = { s: id };
    }
    writeRaw(clear);
    writeRaw(set);
  }

  /** Put locked cells (inventory formulas, labels, totals) back the way the plan has them. */
  function restoreLocked(list: Array<[number, number]>) {
    const exp = expectedRef.current;
    const ws = sheet();
    if (!exp || !ws) return;
    let message = "";
    const put: Record<number, Record<number, any>> = {};
    for (const [r, c] of list) {
      const key = `${r}|${c}`;
      const kind = cellKind(r, c).kind;
      const range = ws.getRange(r, c, 1, 1);
      let want: { f?: string; v?: string | number };
      if (exp.formulas.has(key)) want = { f: exp.formulas.get(key)! };
      else if (exp.values.has(key)) want = { v: exp.values.get(key)! };
      else continue;
      let ok: boolean;
      if (want.f != null) ok = normF(String(range.getFormula?.() ?? "")) === normF(want.f);
      else {
        const v = range.getValue?.();
        ok = typeof want.v === "number" ? toNum(v) === want.v && v !== "" && v != null : String(v ?? "") === want.v;
      }
      if (ok) continue;
      if (Date.now() - (revertedRef.current[key] ?? 0) < 1500) continue; // already put back just now
      revertedRef.current[key] = Date.now();
      (put[r] ??= {})[c] = want.f != null ? { f: want.f, si: null } : { v: want.v, f: null, si: null };
      // a hidden row's locked cells (a paste or fill that ran past the totals) go back quietly
      if (!isHidden(layoutRef.current!, r)) message ||= kind === "inventory" ? MSG.inventory : MSG.locked;
    }
    writeRaw(put);
    if (message) propsRef.current.onBlocked(message);
  }

  /** The engine's number for an editable (demand / supply) week cell. */
  function expectedAt(t: { metric: OverrideMetric; cell: number; w: number }) {
    return expectedRef.current!.nums[t.metric][t.cell][t.w];
  }

  /** Put an editable cell back to the engine's number (a rejected or blocked edit). */
  function writeExpected(t: Touched) {
    const L = layoutRef.current;
    if (!expectedRef.current || !L) return;
    writeRaw({ [rowOf(L, t.cell, METRIC_ROW[t.metric])]: { [FIRST_WEEK_COL + t.w]: { v: expectedAt(t), f: null, si: null } } });
  }

  // Push engine output into the grid whenever the plan changes. Inventory
  // recalculates by itself (formulas); only changed cells are written, as the
  // grid's own writes (raw mutations: not edits, not in the undo history).
  useEffect(() => {
    const exp = expectedRef.current;
    const L = layoutRef.current;
    if (status !== "ready" || !exp || !L || !apiRef.current) return;
    const next = expectedOf(cells, edits, new Set(shortWeeks), new Map(exp.formulas), exp.values);
    expectedRef.current = next;
    const put: Record<number, Record<number, any>> = {};
    const at = (r: number, c: number, v: any) => ((put[r] ??= {})[c] = v);
    for (const m of EDITABLE) next.nums[m].forEach((row, i) => row.forEach((v, w) => {
      if (v !== exp.nums[m][i][w]) at(rowOf(L, i, METRIC_ROW[m]), FIRST_WEEK_COL + w, { v, f: null, si: null });
    }));
    cells.forEach((_, i) => {
      if (next.sub[i] === exp.sub[i]) return;
      for (let w = 0; w < HORIZON; w++) {
        const f = inventoryFormula(L, i, w, next.sub[i]);
        next.formulas.set(`${rowOf(L, i, INVENTORY)}|${FIRST_WEEK_COL + w}`, f);
        at(rowOf(L, i, INVENTORY), FIRST_WEEK_COL + w, { f, si: null });
      }
    });
    writeRaw(put);
    const restyle: Array<[number, number]> = [];
    for (const m of EDITABLE) next.ov[m].forEach((row, i) => row.forEach((s, w) => {
      if (s !== exp.ov[m][i][w]) restyle.push([rowOf(L, i, METRIC_ROW[m]), FIRST_WEEK_COL + w]);
    }));
    for (let w = 0; w < HORIZON; w++) if (next.short.has(w) !== exp.short.has(w)) restyle.push([0, FIRST_WEEK_COL + w]);
    restoreStyles(restyle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cells, edits, shortWeeks, status]);

  // A new row order: at once for a sort or metric filter change, shortly after a lever or reload.
  const metricKey = metricSel.join(",");
  const sortKey = JSON.stringify(sort);
  const appliedRef = useRef({ metricKey, sortKey, resortKey });
  useEffect(() => {
    if (status !== "ready") return;
    const prev = appliedRef.current;
    appliedRef.current = { metricKey, sortKey, resortKey };
    if (prev.metricKey !== metricKey || prev.sortKey !== sortKey) requestRelayout(0);
    else if (prev.resortKey !== resortKey) {
      forgetHistory(); // the plan's own numbers moved: an undo would bring back stale ones
      requestRelayout(250);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [metricKey, sortKey, resortKey, status]);

  const sortOf = (c: number) => {
    const i = sort.findIndex((s) => s.col === colKey(c));
    return i < 0 ? null : { direction: sort[i].direction, order: i + 1 };
  };
  const sortHeader = (c: number, b: Box, inset: number, dx = 0) => (
    <SortHeader key={`sort-${c}`} label={headerLabel(c, weeks)} sort={sortOf(c)} inset={inset}
      onClick={() => {
        commitEditor();
        onSort?.(cycleSortColumn(sort, colKey(c)));
      }}
      style={{ left: b.left - dx, top: b.top, width: b.width, height: b.height, zIndex: 14 }} />
  );
  const boxes = hdr?.boxes ?? {};

  return (
    <div ref={wrapRef} style={{ position: "relative", height: "100%", minHeight: 0, border: `2px solid ${C.ink}`, borderRadius: 2, background: "#fff" }}
      onMouseMove={(e) => {
        const box = wrapRef.current?.getBoundingClientRect();
        if (!box) return;
        mouseRef.current = { x: e.clientX - box.left, y: e.clientY - box.top };
        setTip((t) => (t ? { ...t, ...mouseRef.current } : t));
      }}
      onMouseLeave={() => setTip(null)}>
      <div ref={hostRef} data-testid="supply-grid" style={{ position: "absolute", inset: 0 }} />
      {status === "ready" && hdr && onSort && (
        <>
          <SortHeaderStyle />
          {/* frozen label and On hand headers */}
          {[0, 1, 2, START_COL].map((c) => boxes[c] && sortHeader(c, boxes[c], filters.some((f) => f.col === c) ? Math.min(18, boxes[c].height - 4) + 3 : 0))}
          {/* week and total headers: follow the horizontal scroll, clipped to the scrolling part */}
          {boxes[FIRST_WEEK_COL] && hdr.clipRight > hdr.clipLeft && (
            <div style={{ position: "absolute", left: hdr.clipLeft, top: boxes[FIRST_WEEK_COL].top, width: hdr.clipRight - hdr.clipLeft, height: boxes[FIRST_WEEK_COL].height, overflow: "hidden", zIndex: 14 }}>
              {Array.from({ length: TOTAL_COL - FIRST_WEEK_COL + 1 }, (_, k) => FIRST_WEEK_COL + k).map((c) => {
                const b = boxes[c];
                if (!b || b.left + b.width <= hdr.clipLeft || b.left >= hdr.clipRight) return null;
                return sortHeader(c, { ...b, top: 0 }, 0, hdr.clipLeft);
              })}
            </div>
          )}
        </>
      )}
      {status === "ready" && filters.map((f) => {
        const b = boxes[f.col];
        if (!b) return null;
        const size = Math.min(18, b.height - 4);
        return (
          <React.Fragment key={f.col}>
            <FilterButton label={f.label} active={f.value.length > 0} open={openFilter === f.col}
              onClick={() => {
                commitEditor(); // the region / category filters remount the grid
                setOpenFilter((o) => (o === f.col ? null : f.col));
              }}
              style={{ left: b.left + b.width - size - 3, top: b.top + (b.height - size) / 2, width: size, height: size, zIndex: 15 }} />
            {openFilter === f.col && (
              <ColumnFilterDropdown label={f.label} values={f.options} currentSelection={f.value.length ? new Set(f.value) : null} applyRef={filterApplyRef}
                // fit inside the grid (its container clips): the value list scrolls
                style={{ left: b.left, top: b.top + b.height + 2, maxHeight: Math.max(160, Math.min(350, (wrapRef.current?.clientHeight ?? 400) - b.top - b.height - 8)) }}
                onApply={(sel) => {
                  setOpenFilter(null);
                  f.onChange(sel == null ? [] : f.options.filter((o) => sel.has(o)));
                }}
                onCancel={() => setOpenFilter(null)} />
            )}
          </React.Fragment>
        );
      })}
      {tip && (
        <div role="tooltip" data-testid="edit-tooltip" style={{
          position: "absolute", left: Math.min(tip.x + 14, (wrapRef.current?.clientWidth ?? 9999) - 240), top: tip.y + 18, zIndex: 20, pointerEvents: "none",
          padding: "4px 8px", background: C.ink, color: C.white, borderRadius: 2, fontSize: 12, whiteSpace: "nowrap",
        }}>{tip.text}</div>
      )}
      {status !== "ready" && (
        <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", background: C.snow }}>
          {status === "loading" ? (
            <div className="animate-pulse text-sm" style={{ color: C.darkGrey }}>Loading the spreadsheet engine…</div>
          ) : (
            <p role="alert" className="text-sm" style={{ color: C.darkWatermelon, padding: 16 }}>Spreadsheet failed to load: {error}</p>
          )}
        </div>
      )}
    </div>
  );
}
