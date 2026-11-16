// Supply planning engine (browser). flight/supply_engine.py is the Python port
// the write Flight uses on approval; keep the two in lock-step.
//
// Demand comes from the scenario's demand source (the baseline forecast or an
// approved demand plan, plus an optional stress-test upside). Supply levers
// decide how much of it is met:
//   planned supply     (standard cost)               -> on-hand stock
//   subcontract        extra supply from the current week on, at a unit-cost premium
//   expedited shipping a shortfall chased from the supplier at 5/3/1-day speed:
//                      faster wins back more customers, costs more per unit,
//                      capped at a share of weekly supply (1-day air costs more
//                      than the unit margin of most categories: not worth it)
//   drop ship          the rest of the shortfall shipped supplier -> customer,
//                      skipping the warehouse: best recapture, highest cost
// Grid overrides replace a week's number outright: a demand override is that
// week's demand in units (instead of source x (1 + upside)), a supply override
// its planned receipts. Every plan is also run with the levers off (same
// demand, same supply, same overrides), so "is expediting worth it" is the
// difference between the two.
export const HORIZON = 26;
export const jsRound = Math.round;

export type Speed = "none" | "5d" | "3d" | "1d";
export const SPEEDS: Record<Speed, { label: string; days: number | null; cost: number; recapture: number }> = {
  none: { label: "No expediting", days: null, cost: 0, recapture: 0 },
  "5d": { label: "5-day", days: 5, cost: 1.5, recapture: 0.35 },
  "3d": { label: "3-day", days: 3, cost: 3.25, recapture: 0.55 },
  // Overnight air: wins back the most, but at $55/unit it costs more than most
  // categories earn per unit, so on its own it is not worth it.
  "1d": { label: "1-day", days: 1, cost: 55, recapture: 0.85 },
};
export const SPEED_ORDER: Speed[] = ["none", "5d", "3d", "1d"];
export const DROP_SHIP = { cost: 14, recapture: 0.92 };
/** Weekly holding cost of on-hand stock, as a share of unit cost. */
export const HOLDING_PCT_WEEK = 0.004;

export type SupplyDrivers = {
  demand_source: string; // "baseline" or an approved demand plan's scenario_id
  demand_upside_pct: number;
  expedite: { speed: Speed; capacity_pct: number };
  drop_ship: { enabled: boolean; share_pct: number };
  /** Extra capacity from the current week on. */
  subcontract: { enabled: boolean; capacity_pct: number; premium_pct: number };
  /** The optimizer (optimizer.ts) sets the expedite, drop ship and subcontract levers and keeps
   * them at the best net margin whenever demand, the premium or the grid changes. */
  auto_optimize?: boolean;
};

export type SupplyBase = {
  weeks: string[];
  regions: string[];
  categories: { category: string; unit_price: number; unit_cost: number }[];
  /** "region|category" -> 26 weeks of demand, per source ("baseline" or a scenario_id). */
  demand: Record<string, Record<string, number[]>>;
  supply: Record<string, { weekly_receipts: number; on_hand_start: number }>;
};

export const defaultDrivers = (): SupplyDrivers => ({
  demand_source: "baseline",
  demand_upside_pct: 0,
  expedite: { speed: "none", capacity_pct: 20 },
  drop_ship: { enabled: false, share_pct: 50 },
  subcontract: { enabled: false, capacity_pct: 15, premium_pct: 25 },
});

const num = (v: unknown, fallback: number) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);

export function normalizeDrivers(raw: any): SupplyDrivers {
  const d = defaultDrivers();
  if (!raw || typeof raw !== "object") return d;
  d.demand_source = typeof raw.demand_source === "string" && raw.demand_source ? raw.demand_source : "baseline";
  d.demand_upside_pct = num(raw.demand_upside_pct, 0);
  const e = raw.expedite ?? {};
  // "2d" was an option once; scenarios saved with it read as 3-day.
  const speed = e.speed === "2d" ? "3d" : e.speed;
  d.expedite = { speed: (SPEED_ORDER as string[]).includes(speed) ? speed : "none", capacity_pct: num(e.capacity_pct, 20) };
  const ds = raw.drop_ship ?? {};
  d.drop_ship = { enabled: !!ds.enabled, share_pct: num(ds.share_pct, 50) };
  const s = raw.subcontract ?? {};
  d.subcontract = { enabled: !!s.enabled, capacity_pct: num(s.capacity_pct, 15), premium_pct: num(s.premium_pct, 25) };
  if (raw.auto_optimize === true) d.auto_optimize = true;
  return d;
}

/** The same scenario with every supply lever off: the "is it worth it" reference. */
export const leversOff = (d: SupplyDrivers): SupplyDrivers => ({
  ...d,
  expedite: { ...d.expedite, speed: "none" },
  drop_ship: { ...d.drop_ship, enabled: false },
  subcontract: { ...d.subcontract, enabled: false },
});

/** The grid rows a planner can override. */
export type OverrideMetric = "supply" | "demand";
export const OVERRIDE_METRICS: OverrideMetric[] = ["demand", "supply"]; // grid row order
/** Key of one overridden week in computeSupplyPlan's overrides map (supply_engine.override_key). */
export const overrideKey = (metric: OverrideMetric, region: string, category: string, week: number) => `${metric}|${region}|${category}|${week}`;

export type SupplyCell = {
  region: string; category: string;
  demand: number[]; receipts: number[]; subcontract: number[]; from_stock: number[]; expedited: number[];
  drop_ship: number[]; lost: number[]; revenue: number[]; extra_cost: number[]; net_margin: number[];
  /** Weeks whose demand / planned receipts come from a grid override. */
  overridden: Record<OverrideMetric, boolean[]>;
  /** On-hand stock at the start of week 1, and at the end of each week: max(0, previous + receipts + subcontract - demand). */
  start_inventory: number; inventory: number[];
};
export type SupplyKpis = {
  demand_units: number; met_units: number; lost_units: number; fill_rate: number; revenue: number; lost_revenue: number;
  expedited_units: number; drop_ship_units: number; subcontract_units: number;
  expedite_cost: number; drop_ship_cost: number; subcontract_premium: number; extra_cost: number; holding_cost: number;
  net_margin: number; end_inventory_units: number; end_inventory_value: number;
};
export type SupplyPlan = {
  cells: SupplyCell[];
  kpis: SupplyKpis;
  weekly: { demand: number[]; from_stock: number[]; expedited: number[]; drop_ship: number[]; lost: number[]; supply: number[]; inventory: number[] };
};

// One week's lever arithmetic, shared by computeSupplyPlan and the optimizer's search
// (optimizer.ts), so the two can never compute a lever differently.
/** Units a lever adds per week: a % of the cell's planned weekly receipts. */
export const pctOfReceipts = (weeklyReceipts: number, pct: number) => jsRound(weeklyReceipts * pct / 100);
/** Expedited units: the speed's share of the shortfall, capped. */
export const expeditedUnits = (short: number, speed: Speed, cap: number) => Math.min(jsRound(short * SPEEDS[speed].recapture), cap);
/** Drop shipped units: share_pct of what's still short, of which DROP_SHIP.recapture is kept. */
export const dropShipUnits = (rest: number, sharePct: number) => jsRound(rest * (sharePct / 100) * DROP_SHIP.recapture);

/** overrides: overrideKey(metric, region, category, week) -> units (demand, or planned receipts). */
export function computeSupplyPlan(base: SupplyBase, drivers: SupplyDrivers, overrides: Record<string, number>): SupplyPlan {
  const source = base.demand[drivers.demand_source] ? drivers.demand_source : "baseline";
  const upside = 1 + drivers.demand_upside_pct / 100;
  const speed = SPEEDS[drivers.expedite.speed];
  const ds = drivers.drop_ship;
  const sub = drivers.subcontract;
  const k: SupplyKpis = {
    demand_units: 0, met_units: 0, lost_units: 0, fill_rate: 1, revenue: 0, lost_revenue: 0,
    expedited_units: 0, drop_ship_units: 0, subcontract_units: 0,
    expedite_cost: 0, drop_ship_cost: 0, subcontract_premium: 0, extra_cost: 0, holding_cost: 0,
    net_margin: 0, end_inventory_units: 0, end_inventory_value: 0,
  };
  const z = () => new Array(HORIZON).fill(0);
  const weekly = { demand: z(), from_stock: z(), expedited: z(), drop_ship: z(), lost: z(), supply: z(), inventory: z() };
  const cells: SupplyCell[] = [];
  for (const region of base.regions) {
    for (const cat of base.categories) {
      const key = `${region}|${cat.category}`;
      const fc = base.demand[source]?.[key] ?? z();
      const sp = base.supply[key] ?? { weekly_receipts: 0, on_hand_start: 0 };
      let inv = sp.on_hand_start;
      const c: SupplyCell = {
        region, category: cat.category, demand: [], receipts: [], subcontract: [], from_stock: [], expedited: [],
        drop_ship: [], lost: [], revenue: [], extra_cost: [], net_margin: [], overridden: { supply: [], demand: [] },
        start_inventory: sp.on_hand_start, inventory: [],
      };
      for (let w = 0; w < HORIZON; w++) {
        const dKey = overrideKey("demand", region, cat.category, w);
        const sKey = overrideKey("supply", region, cat.category, w);
        const demand = dKey in overrides ? Math.max(0, jsRound(overrides[dKey])) : jsRound(fc[w] * upside);
        const receipts = sKey in overrides ? Math.max(0, jsRound(overrides[sKey])) : sp.weekly_receipts;
        const subUnits = sub.enabled ? pctOfReceipts(sp.weekly_receipts, sub.capacity_pct) : 0;
        const available = inv + receipts + subUnits;
        const fromStock = Math.min(demand, available);
        const short = demand - fromStock;
        const expCap = speed.days == null ? 0 : pctOfReceipts(sp.weekly_receipts, drivers.expedite.capacity_pct);
        const expedited = expeditedUnits(short, drivers.expedite.speed, expCap);
        const rest = short - expedited;
        const dropShip = ds.enabled ? dropShipUnits(rest, ds.share_pct) : 0;
        const lost = rest - dropShip;
        inv = available - fromStock;
        const met = fromStock + expedited + dropShip;
        const revenue = met * cat.unit_price;
        const expCost = expedited * speed.cost;
        const dsCost = dropShip * DROP_SHIP.cost;
        const subPremium = subUnits * cat.unit_cost * sub.premium_pct / 100;
        const holding = inv * cat.unit_cost * HOLDING_PCT_WEEK;
        const extra = expCost + dsCost + subPremium;
        const net = revenue - met * cat.unit_cost - extra - holding;
        c.demand.push(demand); c.receipts.push(receipts); c.subcontract.push(subUnits); c.from_stock.push(fromStock);
        c.expedited.push(expedited); c.drop_ship.push(dropShip); c.lost.push(lost); c.revenue.push(revenue);
        c.extra_cost.push(extra); c.net_margin.push(net); c.inventory.push(inv);
        c.overridden.demand.push(dKey in overrides); c.overridden.supply.push(sKey in overrides);
        k.demand_units += demand; k.met_units += met; k.lost_units += lost; k.revenue += revenue; k.lost_revenue += lost * cat.unit_price;
        k.expedited_units += expedited; k.drop_ship_units += dropShip; k.subcontract_units += subUnits;
        k.expedite_cost += expCost; k.drop_ship_cost += dsCost; k.subcontract_premium += subPremium; k.extra_cost += extra;
        k.holding_cost += holding; k.net_margin += net;
        weekly.demand[w] += demand; weekly.from_stock[w] += fromStock; weekly.expedited[w] += expedited;
        weekly.drop_ship[w] += dropShip; weekly.lost[w] += lost; weekly.supply[w] += receipts + subUnits; weekly.inventory[w] += inv;
      }
      k.end_inventory_units += inv;
      k.end_inventory_value += inv * cat.unit_cost;
      cells.push(c);
    }
  }
  k.fill_rate = k.demand_units > 0 ? k.met_units / k.demand_units : 1;
  return { cells, kpis: k, weekly };
}
