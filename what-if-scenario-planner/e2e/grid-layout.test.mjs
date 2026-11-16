#!/usr/bin/env node
// The grid's row-order model (dive/src/gridLayout.ts) and the sort cycle taken
// from the pivot Dive (dive/src/GridSort.tsx): every sheet row maps to the right
// region x category and metric in any sort and metric filter, the inventory and
// total formulas name the right rows, a paste or fill over the shown rows never
// reaches a hidden one, and sorting is stable.
//
//   node e2e/grid-layout.test.mjs        (needs `npm install` in dive/, for esbuild)
import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const SRC = join(ROOT, "dive", "src");
const { build } = createRequire(join(ROOT, "dive", "package.json"))("esbuild");
const dir = mkdtempSync(join(tmpdir(), "grid-layout-"));
await build({
  stdin: { contents: 'export * from "./gridLayout"; export { cycleSortColumn, cleanSort } from "./GridSort";', resolveDir: SRC, loader: "ts" },
  outfile: join(dir, "grid.mjs"), format: "esm", bundle: true, platform: "neutral", logLevel: "silent",
  jsx: "transform", mainFields: ["module", "main"],
});
const G = await import(join(dir, "grid.mjs"));
const { DEMAND, SUPPLY, INVENTORY, FIRST_WEEK_COL, TOTAL_COL, START_COL, METRICS } = G;
const H = 26;

// ── fake plan: inventory follows the grid's formula (MAX(0, prev + supply + sub - demand)) ──
let seed = 7;
const rnd = (n) => ((seed = (seed * 1103515245 + 12345) % 2147483648) % n);
const regions = ["Northeast", "Southeast", "Central", "West"];
const categories = ["Outerwear", "Knitwear", "Denim"];
const cells = [];
for (const region of regions) for (const category of categories) {
  const demand = Array.from({ length: H }, () => 500 + rnd(1500));
  const receipts = Array.from({ length: H }, () => (rnd(4) ? 900 + rnd(400) : 0));
  const sub = rnd(3) ? 0 : 50 + rnd(100);
  const start = 1000 + rnd(3000);
  const inventory = [];
  let prev = start;
  for (let w = 0; w < H; w++) inventory.push((prev = Math.max(0, prev + receipts[w] + sub - demand[w])));
  cells.push({ region, category, demand, receipts, inventory, start_inventory: start, subcontract: Array(H).fill(sub),
    overridden: { demand: Array(H).fill(false), supply: Array(H).fill(false) } });
}
// a tie and a blank-heavy column for the stability checks
cells[1].demand[5] = cells[4].demand[5] = cells[7].demand[5] = 777;
const n = cells.length;

// ── the sort cycle (pivot Dive): none -> desc -> asc -> none; new columns append ──
let s = [];
s = G.cycleSortColumn(s, "w3"); assert.deepEqual(s, [{ col: "w3", direction: "desc" }]);
s = G.cycleSortColumn(s, "region"); assert.deepEqual(s, [{ col: "w3", direction: "desc" }, { col: "region", direction: "desc" }]);
s = G.cycleSortColumn(s, "w3"); assert.deepEqual(s, [{ col: "w3", direction: "asc" }, { col: "region", direction: "desc" }]);
s = G.cycleSortColumn(s, "w3"); assert.deepEqual(s, [{ col: "region", direction: "desc" }]);
assert.deepEqual(G.cleanSort([{ col: "w3", direction: "asc" }, { col: "w3", direction: "desc" }, { col: "nope", direction: "asc" }, null, { col: "total", direction: "up" }], G.isSortKey),
  [{ col: "w3", direction: "asc" }]);
assert.deepEqual(G.SORT_KEYS.slice(0, 5), ["metric", "region", "category", "onhand", "w1"]);
assert.equal(G.colKey(TOTAL_COL), "total");

// ── a tiny evaluator for the grid's formulas, on a sheet built from a layout ──
const colIndex = (name) => [...name].reduce((a, ch) => a * 26 + ch.charCodeAt(0) - 64, 0) - 1;
function sheetOf(L) {
  const v = new Map(); // "r|c" -> number | string | formula
  L.slots.forEach((slot, k) => {
    const r = k + 1;
    if (slot.kind === "total") {
      v.set(`${r}|0`, METRICS[slot.metric]);
      for (let c = START_COL; c <= TOTAL_COL; c++) { const f = G.totalFormula(L, slot.metric, c); if (f) v.set(`${r}|${c}`, f); }
      return;
    }
    const cell = cells[slot.cell];
    v.set(`${r}|0`, METRICS[slot.metric]);
    if (slot.metric === INVENTORY) v.set(`${r}|${START_COL}`, cell.start_inventory);
    for (let w = 0; w < H; w++) {
      const c = FIRST_WEEK_COL + w;
      v.set(`${r}|${c}`, slot.metric === DEMAND ? cell.demand[w] : slot.metric === SUPPLY ? cell.receipts[w] : G.inventoryFormula(L, slot.cell, w, cell.subcontract[0]));
    }
    if (slot.metric !== INVENTORY) v.set(`${r}|${TOTAL_COL}`, G.rowTotalFormula(r));
  });
  const ref = (a1) => { const m = /^\$?([A-Z]+)\$?(\d+)$/.exec(a1); return [Number(m[2]) - 1, colIndex(m[1])]; };
  const get = (r, c) => {
    const x = v.get(`${r}|${c}`);
    if (typeof x !== "string" || !x.startsWith("=")) return x ?? 0;
    let m;
    if ((m = /^=MAX\(0,([A-Z]+\d+)\+([A-Z]+\d+)(?:\+(\d+))?-([A-Z]+\d+)\)$/.exec(x)))
      return Math.max(0, get(...ref(m[1])) + get(...ref(m[2])) + Number(m[3] ?? 0) - get(...ref(m[4])));
    if ((m = /^=SUM\(([A-Z]+\d+):([A-Z]+\d+)\)$/.exec(x))) {
      const [r0, c0] = ref(m[1]); const [, c1] = ref(m[2]); let t = 0;
      for (let cc = c0; cc <= c1; cc++) t += get(r0, cc);
      return t;
    }
    if ((m = /^=SUMIF\((\$A\$\d+):(\$A\$\d+),"(\w+)",([A-Z]+\d+):([A-Z]+\d+)\)$/.exec(x))) {
      const [ra] = ref(m[1]); const [rb] = ref(m[2]); const [, cs] = ref(m[4]); let t = 0;
      for (let rr = ra; rr <= rb; rr++) if (v.get(`${rr}|0`) === m[3]) t += get(rr, cs);
      return t;
    }
    throw new Error(`unknown formula ${x}`);
  };
  return { get };
}

const sorts = [
  [],
  [{ col: "w3", direction: "desc" }],
  [{ col: "w6", direction: "asc" }, { col: "region", direction: "desc" }],
  [{ col: "metric", direction: "asc" }, { col: "total", direction: "desc" }],
  [{ col: "onhand", direction: "desc" }],
  [{ col: "category", direction: "asc" }, { col: "w26", direction: "desc" }],
];
const metricSels = [[], ["Demand"], ["Supply"], ["Inventory"], ["Demand", "Supply"], ["Supply", "Inventory"]];
let checks = 0;
for (const sort of sorts) for (const metricSel of metricSels) {
  const L = G.buildLayout(cells, metricSel, sort);
  const shown = (m) => !metricSel.length || metricSel.includes(METRICS[m]);
  const tag = `${JSON.stringify(sort)} ${JSON.stringify(metricSel)}`;
  assert.equal(L.slots.length, 3 * n + 3, tag);
  assert.equal(L.visible, n * [0, 1, 2].filter(shown).length, tag);
  // shown data rows, then the three total rows, then the hidden data rows
  L.slots.forEach((slot, k) => {
    const r = k + 1;
    if (r <= L.visible) assert.ok(slot.kind === "data" && shown(slot.metric), tag);
    else if (r <= L.visible + 3) assert.ok(slot.kind === "total" && slot.metric === r - L.visible - 1, tag);
    else assert.ok(slot.kind === "data" && !shown(slot.metric), tag);
    assert.equal(G.isHidden(L, r), !shown(slot.metric), tag);
  });
  // every region x category x metric is in exactly one row, both ways
  const seen = new Set();
  for (let i = 0; i < n; i++) for (let m = 0; m < 3; m++) {
    const r = G.rowOf(L, i, m);
    const slot = G.slotAt(L, r);
    assert.ok(slot.kind === "data" && slot.cell === i && slot.metric === m, tag);
    seen.add(r);
    for (const w of [0, 7, 25]) {
      const k = G.classify(L, r, FIRST_WEEK_COL + w);
      if (m === INVENTORY) assert.deepEqual(k, { kind: "inventory", cell: i, week: w }, tag);
      else assert.deepEqual(k, { kind: "edit", metric: m === DEMAND ? "demand" : "supply", cell: i, week: w }, tag);
    }
    assert.equal(G.classify(L, r, 1).kind, "locked", tag);
    assert.equal(G.classify(L, r, TOTAL_COL).kind, "locked", tag);
  }
  assert.equal(seen.size, 3 * n, tag);
  for (const m of [0, 1, 2]) assert.equal(G.classify(L, L.totalRow[m], FIRST_WEEK_COL).kind, "locked", tag);
  // the shown rows are sorted, stably (ties and blanks keep the original order; blanks last)
  const vis = L.slots.slice(0, L.visible);
  for (let k = 1; k < vis.length; k++) {
    const a = vis[k - 1], b = vis[k];
    let cmp = 0;
    for (const sc of sort) {
      const x = G.rowValue(cells[a.cell], a.metric, sc.col), y = G.rowValue(cells[b.cell], b.metric, sc.col);
      if (x === y) continue;
      if (x == null) { cmp = 1; break; }
      if (y == null) { cmp = -1; break; }
      const d = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
      cmp = sc.direction === "asc" ? Math.sign(d) : -Math.sign(d);
      if (cmp) break;
    }
    if (cmp === 0) cmp = a.cell * 3 + a.metric < b.cell * 3 + b.metric ? -1 : 1;
    assert.equal(cmp, -1, `${tag}: rows ${k} and ${k + 1} out of order`);
  }
  // the formulas, evaluated on the sheet, give the plan's inventory and totals in any order
  const sh = sheetOf(L);
  for (let i = 0; i < n; i++) for (let w = 0; w < H; w++) {
    assert.equal(sh.get(G.rowOf(L, i, INVENTORY), FIRST_WEEK_COL + w), cells[i].inventory[w], `${tag}: inventory ${i} w${w}`);
    checks++;
  }
  for (const m of [0, 1, 2].filter(shown)) for (const w of [0, 12, 25]) {
    const series = (c) => (m === DEMAND ? c.demand : m === SUPPLY ? c.receipts : c.inventory);
    assert.equal(sh.get(L.totalRow[m], FIRST_WEEK_COL + w), cells.reduce((t, c) => t + series(c)[w], 0), `${tag}: total ${m} w${w}`);
  }
  // a paste or fill of any height from any shown row: up to the totals it fills shown rows in order,
  // never a hidden one; past them it can only reach hidden rows that the grid puts back (isHidden)
  for (let start = 1; start <= L.visible; start++) for (const h of [1, 2, 3, 5, 9]) {
    const rows = Array.from({ length: h }, (_, j) => start + j);
    const shownTargets = rows.filter((r) => r <= L.visible);
    assert.deepEqual(shownTargets, rows.slice(0, shownTargets.length), tag);
    for (const r of rows) if (r <= L.visible + 3) assert.ok(!G.isHidden(L, r) || G.slotAt(L, r).kind === "total", tag);
  }
}

// a hidden metric's rows never sit between shown rows (the filtered-paste bug)
const Ld = G.buildLayout(cells, ["Demand"], []);
assert.deepEqual(Ld.slots.slice(0, n).map((x) => [x.cell, x.metric]), cells.map((_, i) => [i, DEMAND]));
assert.ok(Ld.hidden.every((r) => r > n));
assert.ok(G.sameLayout(G.buildLayout(cells, [], []), G.buildLayout(cells, [], [])));
assert.ok(!G.sameLayout(G.buildLayout(cells, [], []), G.buildLayout(cells, [], [{ col: "w1", direction: "asc" }])));

console.log(`grid layout: ${sorts.length * metricSels.length} sort x metric-filter layouts, ${checks} inventory cells and every row mapping OK`);
