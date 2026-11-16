#!/usr/bin/env node
// The lever optimizer (dive/src/optimizer.ts) against the unmodified engine:
//  1. its value for random settings equals computeSupplyPlan's, in several scenarios (grid edits incl.);
//  2. its winner equals a brute force over all 130,011 settings through computeSupplyPlan (~25 s);
//  3. known optima; a second run on optimal levers changes nothing; every run is well under 10 s.
//
//   node e2e/optimizer.test.mjs            # needs the duckdb CLI and `npm install` in dive/
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const { build } = createRequire(join(ROOT, "dive", "package.json"))("esbuild");
const dir = mkdtempSync(join(tmpdir(), "supply-opt-"));
const db = join(dir, "planning.duckdb");
execFileSync("duckdb", [db, "-c", `.read ${join(ROOT, "flight", "static.sql")}`]);
const q = (sql) => JSON.parse(execFileSync("duckdb", ["-json", db, "-c", sql]).toString() || "[]");
const categories = q("SELECT category, unit_price::DOUBLE AS unit_price, unit_cost::DOUBLE AS unit_cost FROM main.categories ORDER BY sort_order");
const regions = q("SELECT region FROM main.regions ORDER BY sort_order").map((r) => r.region);
const baseline = {};
for (const r of q("SELECT region, category, week_idx, units FROM main.baseline_forecast")) (baseline[`${r.region}|${r.category}`] ??= Array(26).fill(0))[r.week_idx] = r.units;
const supply = {};
for (const s of q("SELECT * FROM main.supply_plan")) supply[`${s.region}|${s.category}`] = { weekly_receipts: s.weekly_receipts, on_hand_start: s.on_hand_start };
const base = { weeks: [], regions, categories, demand: { baseline }, supply };

await build({ entryPoints: [join(ROOT, "dive/src/optimizer.ts")], outfile: join(dir, "optimizer.mjs"), format: "esm", bundle: true, logLevel: "silent" });
const opt = await import(join(dir, "optimizer.mjs"));
await build({ entryPoints: [join(ROOT, "dive/src/engine.ts")], outfile: join(dir, "engine.mjs"), format: "esm", bundle: true, logLevel: "silent" });
const eng = await import(join(dir, "engine.mjs"));

let failures = 0;
const fail = (m) => { failures++; console.error(`FAIL ${m}`); };
const kpis = (d, ov) => eng.computeSupplyPlan(base, d, ov).kpis;
const describe = (d) => `${d.expedite.speed === "none" ? "no expediting" : `${d.expedite.speed} ${d.expedite.capacity_pct}%`}, drop ship ${d.drop_ship.enabled ? d.drop_ship.share_pct + "%" : "off"}, subcontract ${d.subcontract.enabled ? d.subcontract.capacity_pct + "%" : "off"}`;
// Test case 5's grid edits (e2e/engine-parity.test.mjs): demand overrides and a supply override.
const OV = { "demand|West|Outerwear|3": 12000.4, "demand|Central|Denim|0": 0, "demand|Southeast|Footwear|25": 7500.5, "supply|West|Outerwear|3": 4000, "demand|Northeast|Accessories|7": -5 };
// Expected optima from an independent full grid over engine.ts and an exact MIP model of the engine.
const scenarios = [
  { name: "+10% demand", d: { demand_upside_pct: 10 }, ov: {}, expect: 49921288.05 },
  { name: "+10% demand, grid edits", d: { demand_upside_pct: 10 }, ov: OV, expect: 50687915.83 },
  { name: "-15% demand, premium 55%", d: { demand_upside_pct: -15, subcontract: { premium_pct: 55 } }, ov: {} },
  { name: "+35% demand, premium 12%, grid edits", d: { demand_upside_pct: 35, subcontract: { premium_pct: 12 } }, ov: OV },
];

let seed = 11;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
for (const sc of scenarios) {
  const drivers = eng.normalizeDrivers(sc.d);
  // 1. the search's numbers are the engine's
  const { net, extra } = await opt.evaluateAll(base, drivers, sc.ov);
  let worst = 0;
  for (let n = 0; n < 300; n++) {
    const i = Math.floor(rnd() * opt.SETTINGS);
    const k = kpis(opt.withSetting(drivers, opt.decodeSetting(i)), sc.ov);
    worst = Math.max(worst, Math.abs(k.net_margin - net[i]), Math.abs(k.extra_cost - extra[i]));
  }
  if (worst > 1e-4) fail(`${sc.name}: search vs engine differ by ${worst}`);
  // 3. the optimum, its speed, and stability
  const r = await opt.optimizeLevers(base, drivers, sc.ov);
  const again = await opt.optimizeLevers(base, r.drivers, sc.ov);
  console.log(`${sc.name}: ${describe(r.drivers)} -> net ${r.net.toFixed(2)} (default levers ${r.currentNet.toFixed(2)}) in ${r.ms.toFixed(0)} ms; search vs engine within $${worst.toExponential(1)}`);
  if (sc.expect != null && Math.abs(r.net - sc.expect) > 0.01) fail(`${sc.name}: net ${r.net}, expected ${sc.expect}`);
  if (again.changed) fail(`${sc.name}: re-optimizing optimal levers changed them`);
  if (r.ms > 10000) fail(`${sc.name}: ${r.ms} ms`);
  if (Math.abs(kpis(r.drivers, sc.ov).net_margin - r.net) > 1e-6) fail(`${sc.name}: reported net is not the engine's`);
}

// 2. brute force through the unmodified engine (every setting, computeSupplyPlan each time)
{
  const sc = scenarios[1], drivers = eng.normalizeDrivers(sc.d);
  const t = performance.now();
  let bestNet = -Infinity;
  for (let i = 0; i < opt.SETTINGS; i++) bestNet = Math.max(bestNet, kpis(opt.withSetting(drivers, opt.decodeSetting(i)), sc.ov).net_margin);
  const r = await opt.optimizeLevers(base, drivers, sc.ov);
  console.log(`brute force (${sc.name}): ${opt.SETTINGS.toLocaleString("en-US")} engine runs in ${((performance.now() - t) / 1000).toFixed(1)} s, best ${bestNet.toFixed(2)}; optimizer ${r.net.toFixed(2)} in ${r.ms.toFixed(0)} ms`);
  if (Math.abs(bestNet - r.net) > 0.01) fail("optimizer misses the brute-force optimum");
}
if (failures) { console.error(`FAIL: ${failures} problems`); process.exit(1); }
console.log("PASS: the optimizer's numbers are the engine's, it finds the brute-force optimum, and runs well under 10 s");
