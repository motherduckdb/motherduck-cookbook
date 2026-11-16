#!/usr/bin/env node
// The browser engine (dive/src/engine.ts) and its Python port (flight/supply_engine.py,
// which the Flight uses on approval) must compute identical plans. Runs both on
// the planning tables for several lever / override combinations (supply and
// demand overrides, keyed "metric|region|category|week").
//
//   node e2e/engine-parity.test.mjs                 # tables from a local static.sql run (DuckDB CLI)
//   PARITY_DB=md:scenario_planner node e2e/...      # or from MotherDuck ($motherduck_token)
//
// Needs the duckdb CLI, uv, and `npm install` in dive/ (for esbuild).
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const { build } = createRequire(join(ROOT, "dive", "package.json"))("esbuild");
const dir = mkdtempSync(join(tmpdir(), "supply-parity-"));
let db = process.env.PARITY_DB;
if (!db) {
  db = join(dir, "planning.duckdb");
  execFileSync("duckdb", [db, "-c", `.read ${join(ROOT, "flight", "static.sql")}`]);
}
const q = (sql) => JSON.parse(execFileSync("duckdb", ["-json", db, "-c", sql]).toString() || "[]");

const categories = q("SELECT category, unit_price::DOUBLE AS unit_price, unit_cost::DOUBLE AS unit_cost FROM main.categories ORDER BY sort_order");
const regions = q("SELECT region FROM main.regions ORDER BY sort_order").map((r) => r.region);
const baseline = {};
for (const r of q("SELECT region, category, week_idx, units FROM main.baseline_forecast")) (baseline[`${r.region}|${r.category}`] ??= Array(26).fill(0))[r.week_idx] = r.units;
const supply = {};
for (const s of q("SELECT * FROM main.supply_plan")) supply[`${s.region}|${s.category}`] = { weekly_receipts: s.weekly_receipts, on_hand_start: s.on_hand_start };
const base = { weeks: [], regions, categories, demand: { baseline }, supply };
const cases = [
  [{}, {}],
  [{ demand_upside_pct: 10 }, {}],
  [{ demand_upside_pct: 10, expedite: { speed: "3d", capacity_pct: 25 }, drop_ship: { enabled: true, share_pct: 50 } }, {}],
  [{ demand_upside_pct: 15, expedite: { speed: "1d", capacity_pct: 40 }, subcontract: { enabled: true, capacity_pct: 20, premium_pct: 25 } },
    { "supply|West|Outerwear|10": 2500, "supply|Central|Denim|0": 0 }],
  [{ demand_upside_pct: 25, expedite: { speed: "5d", capacity_pct: 15 }, drop_ship: { enabled: true, share_pct: 100 }, subcontract: { enabled: true, capacity_pct: 40, premium_pct: 60 } },
    { "supply|Northeast|Accessories|7": 9000 }],
  // Demand overrides replace source x (1 + upside); one week has both a demand and a supply override.
  [{ demand_upside_pct: 10, expedite: { speed: "3d", capacity_pct: 25 }, drop_ship: { enabled: true, share_pct: 50 } },
    { "demand|West|Outerwear|3": 12000.4, "demand|Central|Denim|0": 0, "demand|Southeast|Footwear|25": 7500.5,
      "supply|West|Outerwear|3": 4000, "demand|Northeast|Accessories|7": -5 }],
];
writeFileSync(join(dir, "input.json"), JSON.stringify({ base, cases }));

await build({ entryPoints: [join(ROOT, "dive/src/engine.ts")], outfile: join(dir, "engine.mjs"), format: "esm", bundle: true, logLevel: "silent" });
const eng = await import(join(dir, "engine.mjs"));
const js = cases.map(([d, o]) => eng.computeSupplyPlan(base, eng.normalizeDrivers(d), o));

const py = JSON.parse(execFileSync("uv", ["run", "--quiet", "--no-project", "python", "-c", `
import json, sys
sys.path.insert(0, ${JSON.stringify(join(ROOT, "flight"))})
import supply_engine as engine
inp = json.load(open(${JSON.stringify(join(dir, "input.json"))}))
print(json.dumps([engine.compute_plan(inp["base"], engine.normalize_drivers(d), o) for d, o in inp["cases"]]))
`]).toString());

const FIELDS = ["demand", "receipts", "subcontract", "from_stock", "expedited", "drop_ship", "lost", "inventory"];
let failures = 0;
cases.forEach((_, i) => {
  for (const k of Object.keys(js[i].kpis)) {
    const a = js[i].kpis[k], b = py[i].kpis[k];
    if (Math.abs(a - b) > 1e-6 * Math.max(1, Math.abs(a))) { failures++; console.error(`case ${i} kpi ${k}: js=${a} py=${b}`); }
  }
  js[i].cells.forEach((c, j) => FIELDS.forEach((f) => c[f].forEach((v, w) => {
    if (v !== py[i].cells[j][f][w]) { failures++; if (failures < 10) console.error(`case ${i} ${c.region}|${c.category} ${f}[${w}]: js=${v} py=${py[i].cells[j][f][w]}`); }
  })));
  js[i].cells.forEach((c, j) => ["supply", "demand"].forEach((m) => {
    if (JSON.stringify(c.overridden[m]) !== JSON.stringify(py[i].cells[j].overridden[m])) { failures++; console.error(`case ${i} ${c.region}|${c.category} overridden.${m} differs`); }
  }));
  // Every override in the case shows up in its cell, and a demand override is that week's demand.
  for (const [key, units] of Object.entries(cases[i][1])) {
    const [m, region, category, w] = key.split("|");
    const c = js[i].cells.find((x) => x.region === region && x.category === category);
    if (!c?.overridden[m][Number(w)]) { failures++; console.error(`case ${i} override ${key} not applied`); }
    if (m === "demand" && c && c.demand[Number(w)] !== Math.max(0, Math.round(units))) { failures++; console.error(`case ${i} ${key}: demand ${c.demand[Number(w)]}`); }
  }
  const k = js[i].kpis;
  console.log(`case ${i}: met=${(k.fill_rate * 100).toFixed(2)}% lost=${k.lost_units} expedited=${k.expedited_units} drop=${k.drop_ship_units} sub=${k.subcontract_units} extra=$${k.extra_cost.toFixed(0)} net=$${(k.net_margin / 1e6).toFixed(3)}M`);
});
if (failures) { console.error(`FAIL: ${failures} mismatches`); process.exit(1); }
console.log("PASS: TypeScript and Python supply engines agree on every cell and KPI");
