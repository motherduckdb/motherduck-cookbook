// Finds the supply levers with the highest net margin for a scenario as it stands: its
// demand source, demand upside, subcontract premium and grid overrides are inputs; the
// four lever settings are the decisions:
//   expedite speed      off, 5-day, 3-day, 1-day
//   expedite capacity   1-50 % of weekly receipts   (the slider's range, whole points)
//   drop ship share     0-100 % in steps of 5        (0 = off)
//   subcontract         0-40 % extra capacity        (0 = off)
// 151 expedite choices x 21 shares x 41 capacities = 130,011 settings, and every one is
// tried: the answer is exact, and maximizing net margin is maximizing the "Net margin
// impact" KPI (the no-action plan doesn't depend on the levers).
//
// Speed: the engine's cells are independent, so each region x category cell is evaluated
// once per setting with nested loops that reuse work (stock and shortfall once per
// subcontract value, expedited units once per speed and capacity, then each drop ship
// share), using the engine's own lever functions (pctOfReceipts, expeditedUnits,
// dropShipUnits). About 0.15 s for 24 cells. The winner is re-run through
// computeSupplyPlan, and those are the numbers the Dive shows.
import {
  DROP_SHIP, HOLDING_PCT_WEEK, HORIZON, SPEEDS, computeSupplyPlan, dropShipUnits, expeditedUnits, overrideKey, pctOfReceipts,
  type Speed, type SupplyBase, type SupplyDrivers,
} from "./engine";

/** The lever controls' ranges (dive.tsx LeversPanel sliders). */
export const LEVER_RANGE = { expediteMax: 50, dropShipStep: 5, subcontractMax: 40 } as const;
const EXPEDITE: { speed: Speed; cap: number }[] = [{ speed: "none", cap: 0 }];
for (const speed of ["5d", "3d", "1d"] as Speed[]) for (let cap = 1; cap <= LEVER_RANGE.expediteMax; cap++) EXPEDITE.push({ speed, cap });
const NE = EXPEDITE.length;                                  // 151
const ND = 100 / LEVER_RANGE.dropShipStep + 1;               // 21
const NS = LEVER_RANGE.subcontractMax + 1;                   // 41
export const SETTINGS = NE * ND * NS;                        // 130,011

export type Setting = { speed: Speed; expedite_pct: number; drop_ship_pct: number; subcontract_pct: number };
export const decodeSetting = (i: number): Setting => {
  const j = i % ND, k = Math.floor(i / ND) % NE, s = Math.floor(i / ND / NE);
  return { speed: EXPEDITE[k].speed, expedite_pct: EXPEDITE[k].cap, drop_ship_pct: j * LEVER_RANGE.dropShipStep, subcontract_pct: s };
};

/** The drivers with a setting's levers. A lever that ends up off keeps its slider value. */
export function withSetting(d: SupplyDrivers, s: Setting): SupplyDrivers {
  return {
    ...d,
    expedite: { speed: s.speed, capacity_pct: s.speed === "none" ? d.expedite.capacity_pct : s.expedite_pct },
    drop_ship: { enabled: s.drop_ship_pct > 0, share_pct: s.drop_ship_pct > 0 ? s.drop_ship_pct : d.drop_ship.share_pct },
    subcontract: { ...d.subcontract, enabled: s.subcontract_pct > 0, capacity_pct: s.subcontract_pct > 0 ? s.subcontract_pct : d.subcontract.capacity_pct },
  };
}

type Cell = { price: number; cost: number; receipts: number; onHand: number; demand: number[]; supply: number[] };

function cellsOf(base: SupplyBase, drivers: SupplyDrivers, overrides: Record<string, number>): Cell[] {
  const source = base.demand[drivers.demand_source] ? drivers.demand_source : "baseline";
  const upside = 1 + drivers.demand_upside_pct / 100;
  const out: Cell[] = [];
  for (const region of base.regions) for (const cat of base.categories) {
    const key = `${region}|${cat.category}`;
    const fc = base.demand[source]?.[key] ?? new Array(HORIZON).fill(0);
    const sp = base.supply[key] ?? { weekly_receipts: 0, on_hand_start: 0 };
    const demand: number[] = [], supply: number[] = [];
    for (let w = 0; w < HORIZON; w++) {
      const dKey = overrideKey("demand", region, cat.category, w), sKey = overrideKey("supply", region, cat.category, w);
      demand.push(dKey in overrides ? Math.max(0, Math.round(overrides[dKey])) : Math.round(fc[w] * upside));
      supply.push(sKey in overrides ? Math.max(0, Math.round(overrides[sKey])) : sp.weekly_receipts);
    }
    out.push({ price: cat.unit_price, cost: cat.unit_cost, receipts: sp.weekly_receipts, onHand: sp.on_hand_start, demand, supply });
  }
  return out;
}

/** Adds one cell's net margin and extra cost for every setting to `net` / `extra`. */
function addCell(c: Cell, premiumPct: number, net: Float64Array, extra: Float64Array) {
  const short = new Float64Array(HORIZON), rest = new Float64Array(HORIZON);
  for (let s = 0; s < NS; s++) {
    const subUnits = s > 0 ? pctOfReceipts(c.receipts, s) : 0;
    const subPremium = subUnits * c.cost * premiumPct / 100;
    let inv = c.onHand, sNet = 0, sExtra = 0;
    for (let w = 0; w < HORIZON; w++) {
      const available = inv + c.supply[w] + subUnits, fromStock = Math.min(c.demand[w], available);
      short[w] = c.demand[w] - fromStock;
      inv = available - fromStock;
      sNet += fromStock * (c.price - c.cost) - subPremium - inv * c.cost * HOLDING_PCT_WEEK;
      sExtra += subPremium;
    }
    for (let k = 0; k < NE; k++) {
      const { speed, cap } = EXPEDITE[k];
      const capUnits = speed === "none" ? 0 : pctOfReceipts(c.receipts, cap);
      const unitGain = c.price - c.cost - SPEEDS[speed].cost;
      let eNet = 0, eExtra = 0;
      for (let w = 0; w < HORIZON; w++) {
        const ex = expeditedUnits(short[w], speed, capUnits);
        rest[w] = short[w] - ex;
        eNet += ex * unitGain;
        eExtra += ex * SPEEDS[speed].cost;
      }
      const base = (s * NE + k) * ND;
      for (let j = 0; j < ND; j++) {
        let ds = 0;
        if (j > 0) for (let w = 0; w < HORIZON; w++) ds += dropShipUnits(rest[w], j * LEVER_RANGE.dropShipStep);
        net[base + j] += sNet + eNet + ds * (c.price - c.cost - DROP_SHIP.cost);
        extra[base + j] += sExtra + eExtra + ds * DROP_SHIP.cost;
      }
    }
  }
}

/** Net margin and extra cost of every setting (index -> decodeSetting). `pause` lets the UI breathe between cells. */
export async function evaluateAll(base: SupplyBase, drivers: SupplyDrivers, overrides: Record<string, number>, pause?: () => Promise<unknown>) {
  const net = new Float64Array(SETTINGS), extra = new Float64Array(SETTINGS);
  for (const c of cellsOf(base, drivers, overrides)) {
    addCell(c, drivers.subcontract.premium_pct, net, extra);
    if (pause) await pause();
  }
  return { net, extra };
}

export type OptimizeResult = {
  /** The drivers with the best levers (the input drivers when they are already as good). */
  drivers: SupplyDrivers;
  changed: boolean;
  net: number;        // engine net margin with the best levers
  currentNet: number; // and with the levers as they were
  ms: number;
};

export async function optimizeLevers(base: SupplyBase, drivers: SupplyDrivers, overrides: Record<string, number>): Promise<OptimizeResult> {
  const t0 = performance.now();
  const { net, extra } = await evaluateAll(base, drivers, overrides, () => new Promise((r) => setTimeout(r, 0)));
  // Highest net margin; on a tie (within a cent) the cheaper setting.
  let best = 0;
  for (let i = 1; i < SETTINGS; i++) {
    if (net[i] > net[best] + 0.01 || (net[i] > net[best] - 0.01 && extra[i] < extra[best])) best = i;
  }
  const next = withSetting(drivers, decodeSetting(best));
  const bestNet = computeSupplyPlan(base, next, overrides).kpis.net_margin;
  const currentNet = computeSupplyPlan(base, drivers, overrides).kpis.net_margin;
  // Keep the current levers when they are already as good: no churn in auto-optimize.
  const changed = bestNet > currentNet + 0.01;
  return { drivers: changed ? next : drivers, changed, net: changed ? bestNet : currentNet, currentNet, ms: performance.now() - t0 };
}
