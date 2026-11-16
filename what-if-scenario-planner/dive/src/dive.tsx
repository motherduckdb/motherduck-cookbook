// Supply Chain Scenario Modeling — a MotherDuck Dive App with writeback.
//
// Scenarios move draft → submitted → approved; edits are saved by running a
// MotherDuck Flight (./flight-backend.ts). The question: when demand outruns
// planned supply, is it worth paying to expedite? Levers: expedited
// freight (5/3/1-day), drop ship supplier → customer, and a subcontract
// manufacturer, each at a higher cost. The grid shows demand and planned supply
// (both editable: an override replaces that week's number) and the resulting
// inventory (formulas) per region x category. Every scenario is also computed
// with the levers off (same demand, same supply, same overrides), and the KPIs
// compare the two. Region / category filters narrow the
// KPIs, chart and grid (set from the side panel or the grid's header row); the
// grid's metric filter only hides rows.
//
// Data: planning tables in "scenario_planner".main (categories, regions,
// baseline_forecast, supply_plan, and approved_plan: approved demand plans from
// an upstream demand planner, an optional demand source); this Dive's
// scenarios, approvals and ERP outbox in "scenario_planner".supply, written by
// the supply-planner-write Flight (../flight).
import * as React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useDiveState, useSQLQuery } from "@motherduck/react-sql-query";
import { Bar, CartesianGrid, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Activity, Check, ChevronDown, CircleAlert, Copy, Factory, GitCompare, LineChart as LineIcon, Loader2, Lock, PackageCheck, Pencil, Plus, RefreshCw, Send, Trash2, Truck, Undo2, WandSparkles, X, Zap } from "lucide-react";
import {
  DROP_SHIP, HORIZON, OVERRIDE_METRICS, SPEEDS, SPEED_ORDER, computeSupplyPlan, defaultDrivers, leversOff, normalizeDrivers, overrideKey,
  type OverrideMetric, type SupplyBase, type SupplyCell, type SupplyDrivers, type SupplyPlan,
} from "./engine";
import { useFlightBackend } from "./flight-backend";
import { optimizeLevers } from "./optimizer";
import { DB, N } from "./sql";
import SupplyGrid, { METRICS, SERIES, type CellEdit, type EditInfo } from "./SupplyGrid";
import { cleanSort, type SortColumn } from "./GridSort";
import { isSortKey } from "./gridLayout";
import { C, MONO, SANS, STATUS_STYLE, border, shadow } from "./theme";
import { Btn, Capsule, Chip, Kpi, SectionTitle, Select, Slider, fmtInt, fmtMoney, fmtPct, signed } from "./ui";

// The planner database's organization-wide share (scripts/build-dive.mjs swaps
// in each deploy target's share from deploy-targets.json).
export const REQUIRED_DATABASES = [
  { type: "share", path: "md:_share/scenario_planner_org/00000000-0000-0000-0000-000000000000", alias: "scenario_planner" },
];

// The supply-planner-write Flight's id, per deploy target (scripts/build-dive.mjs).
declare const __WRITE_FLIGHT_ID__: string;
const FLIGHT_ID: string = typeof __WRITE_FLIGHT_ID__ === "undefined" ? "" : __WRITE_FLIGHT_ID__;
const SCHEMA = "supply";
// The page is one viewport tall (the grid takes what's left); shorter than this, the page scrolls instead.
const PAGE_MIN_H = 680;

type Scenario = {
  scenario_id: string; name: string; description: string;
  status: "baseline" | "draft" | "submitted" | "approved"; is_baseline: boolean; version: number;
  drivers: SupplyDrivers;
  /** Grid overrides: a week's demand or planned supply, in units. */
  overrides: { metric: OverrideMetric; region: string; category: string; week_idx: number; units: number; edited_by?: string; edited_at?: string }[];
  updated_by?: string; updated_at?: string; submitted_by?: string; approved_by?: string; approved_at?: string;
};
type ActivityRow = { ts: string; scenario_id?: string; scenario_name?: string; actor: string; action: string; detail: string };
type Tab = "overview" | "compare" | "activity";
type Pair = { plan: SupplyPlan; off: SupplyPlan };

const M = (table: string) => `"${DB}"."main"."${table}"`;
const S = (table: string) => `"${DB}"."${SCHEMA}"."${table}"`;
const weekLabel = (iso: string) => new Date(iso + "T00:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
/** overrideKey of a grid override or edit (one key space for demand and supply). */
const keyOf = (o: { metric: OverrideMetric; region: string; category: string; week_idx: number }) => overrideKey(o.metric, o.region, o.category, o.week_idx);
const overridesMap = (s: Scenario) => {
  const m: Record<string, number> = {};
  for (const o of s.overrides) m[keyOf(o)] = o.units;
  return m;
};
const editableOf = (s?: Scenario) => !!s && !s.is_baseline && s.status === "draft";
const pts = (v: number) => `${v >= 0 ? "+" : "−"}${Math.abs(v * 100).toFixed(1)} pts`;
/** KPIs and weekly series over the region x category cells a filter keeps. */
type Summary = {
  demand_units: number; met_units: number; fill_rate: number; revenue: number; extra_cost: number; net_margin: number;
  weekly: { demand: number[]; supply: number[]; inventory: number[]; from_stock: number[]; expedited: number[]; drop_ship: number[]; lost: number[] };
};
function summarize(plan: SupplyPlan, keep: (c: SupplyCell) => boolean): Summary {
  const z = () => new Array(HORIZON).fill(0);
  const weekly = { demand: z(), supply: z(), inventory: z(), from_stock: z(), expedited: z(), drop_ship: z(), lost: z() };
  const s = { demand_units: 0, met_units: 0, fill_rate: 1, revenue: 0, extra_cost: 0, net_margin: 0, weekly };
  for (const c of plan.cells) {
    if (!keep(c)) continue;
    for (let w = 0; w < HORIZON; w++) {
      const met = c.from_stock[w] + c.expedited[w] + c.drop_ship[w];
      weekly.demand[w] += c.demand[w];
      weekly.supply[w] += c.receipts[w] + c.subcontract[w];
      weekly.inventory[w] += c.inventory[w];
      weekly.from_stock[w] += c.from_stock[w];
      weekly.expedited[w] += c.expedited[w];
      weekly.drop_ship[w] += c.drop_ship[w];
      weekly.lost[w] += c.lost[w];
      s.demand_units += c.demand[w];
      s.met_units += met;
      s.revenue += c.revenue[w];
      s.extra_cost += c.extra_cost[w];
      s.net_margin += c.net_margin[w];
    }
  }
  s.fill_rate = s.demand_units > 0 ? s.met_units / s.demand_units : 1;
  return s;
}
/** Recovered gross margin per $1 of extra supply cost (null when nothing was spent). */
const roiOf = (k: Summary, off: Summary) => {
  const spent = k.extra_cost;
  if (spent < 1) return null;
  return (k.net_margin - off.net_margin + spent) / spent;
};

class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" style={{ padding: 24, fontFamily: SANS, color: C.ink, background: C.sand, minHeight: "100vh" }}>
        <h1 style={{ fontFamily: MONO, fontWeight: 400, textTransform: "uppercase", fontSize: 18 }}>Something went wrong</h1>
        <pre style={{ whiteSpace: "pre-wrap", fontSize: 12 }}>{String(this.state.error?.message ?? this.state.error)}</pre>
        <button type="button" onClick={() => this.setState({ error: null })} style={{ height: 34, padding: "0 14px", border, borderRadius: 2, background: C.sky }}>Try again</button>
      </div>
    );
  }
}

export default function SupplyPlannerDive() {
  return (
    <ErrorBoundary>
      <SupplyPlanner />
    </ErrorBoundary>
  );
}

function SupplyPlanner() {
  // ── shared planning data (main schema) ──
  const catsQ = useSQLQuery(`SELECT category, unit_price, unit_cost, sort_order FROM ${M("categories")} ORDER BY sort_order`);
  const regsQ = useSQLQuery(`SELECT region FROM ${M("regions")} ORDER BY sort_order`);
  const baseQ = useSQLQuery(`SELECT week_idx, strftime(week, '%Y-%m-%d') AS week, region, category, units FROM ${M("baseline_forecast")} ORDER BY week_idx`);
  const supplyQ = useSQLQuery(`SELECT region, category, weekly_receipts, on_hand_start FROM ${M("supply_plan")}`);
  // Approved demand plans from the demand planner, usable as a demand source.
  const demandQ = useSQLQuery(`
    SELECT scenario_id, scenario_name, region, category, week_idx, demand_units
    FROM ${M("approved_plan")} ORDER BY scenario_id, week_idx`);

  const base: SupplyBase | null = useMemo(() => {
    const cats = Array.isArray(catsQ.data) ? catsQ.data : null;
    const regs = Array.isArray(regsQ.data) ? regsQ.data : null;
    const rows = Array.isArray(baseQ.data) ? baseQ.data : null;
    const sup = Array.isArray(supplyQ.data) ? supplyQ.data : null;
    if (!cats || !regs || !rows || !sup || !rows.length) return null;
    const weeks: string[] = new Array(HORIZON).fill("");
    const demand: SupplyBase["demand"] = { baseline: {} };
    for (const r of rows) {
      const w = N(r.week_idx);
      weeks[w] = String(r.week);
      (demand.baseline[`${r.region}|${r.category}`] ??= new Array(HORIZON).fill(0))[w] = N(r.units);
    }
    for (const r of Array.isArray(demandQ.data) ? demandQ.data : []) {
      const src = (demand[String(r.scenario_id)] ??= {});
      (src[`${r.region}|${r.category}`] ??= new Array(HORIZON).fill(0))[N(r.week_idx)] = N(r.demand_units);
    }
    const supply: SupplyBase["supply"] = {};
    for (const s of sup) supply[`${s.region}|${s.category}`] = { weekly_receipts: N(s.weekly_receipts), on_hand_start: N(s.on_hand_start) };
    return {
      weeks,
      regions: regs.map((r: any) => String(r.region)),
      categories: cats.map((c: any) => ({ category: String(c.category), unit_price: N(c.unit_price), unit_cost: N(c.unit_cost) })),
      demand,
      supply,
    };
  }, [catsQ.data, regsQ.data, baseQ.data, supplyQ.data, demandQ.data]);
  const demandSources = useMemo(() => {
    const names = new Map<string, string>();
    for (const r of Array.isArray(demandQ.data) ? demandQ.data : []) names.set(String(r.scenario_id), String(r.scenario_name));
    return [["baseline", "Baseline forecast"] as [string, string], ...[...names].map(([id, name]) => [id, `Approved demand plan: ${name}`] as [string, string])];
  }, [demandQ.data]);
  const baseError = catsQ.error || regsQ.error || baseQ.error || supplyQ.error;

  // ── writes: a Flight run per save (../flight-backend.ts) ──
  const backend = useFlightBackend(!!base, { schema: SCHEMA, flightId: FLIGHT_ID, overrideMetric: true });
  const actor = backend?.actor ?? "";
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [activity, setActivity] = useState<ActivityRow[]>([]);
  const [loadError, setLoadError] = useState("");
  // Bumped by every full reload: the grid re-sorts on fresh data.
  const [loadNonce, setLoadNonce] = useState(0);
  const scenariosRef = useRef<Scenario[]>([]);
  scenariosRef.current = scenarios;
  const versions = useRef<Record<string, number>>({});
  const savedDrivers = useRef<Record<string, string>>({});
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const [saving, setSaving] = useState(0);
  const [toast, setToast] = useState<{ kind: "ok" | "err" | "info"; text: string } | null>(null);
  const toastTimer = useRef<any>(null);
  const say = useCallback((kind: "ok" | "err" | "info", text: string) => {
    setToast({ kind, text });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), kind === "err" ? 6000 : 3500);
  }, []);

  const normalize = useCallback((s: any): Scenario => ({
    ...s, drivers: normalizeDrivers(s.drivers),
    // Overrides saved before demand overrides existed are supply overrides.
    overrides: (s.overrides ?? []).map((o: any) => ({ ...o, metric: o.metric === "demand" ? "demand" : "supply" })),
  }), []);

  const reload = useCallback(async (onlyActivity = false) => {
    if (!backend) return;
    const r = await backend.call("bootstrap");
    if (!r.ok) {
      setLoadError(r.error);
      return;
    }
    setActivity(r.result.activity ?? []);
    if (onlyActivity) return;
    const list: Scenario[] = (r.result.scenarios ?? []).map(normalize);
    for (const s of list) {
      versions.current[s.scenario_id] = s.version;
      savedDrivers.current[s.scenario_id] = JSON.stringify(s.drivers);
    }
    setScenarios(list);
    setLoadNonce((n) => n + 1);
    setLoadError("");
  }, [backend, normalize]);
  useEffect(() => {
    reload();
  }, [reload]);

  const enqueue = useCallback(<T,>(task: () => Promise<T>): Promise<T> => {
    setSaving((n) => n + 1);
    const p = queue.current.then(task, task).finally(() => setSaving((n) => n - 1));
    queue.current = p.catch(() => undefined);
    return p;
  }, []);
  const activityTimer = useRef<any>(null);
  const refreshActivitySoon = useCallback(() => {
    clearTimeout(activityTimer.current);
    activityTimer.current = setTimeout(() => reload(true), 800);
  }, [reload]);
  const mergeMeta = useCallback((server: any) => {
    versions.current[server.scenario_id] = server.version;
    setScenarios((list) => list.map((s) => (s.scenario_id === server.scenario_id
      ? { ...s, version: server.version, status: server.status, name: server.name, description: server.description,
          updated_by: server.updated_by, updated_at: server.updated_at, submitted_by: server.submitted_by,
          approved_by: server.approved_by, approved_at: server.approved_at }
      : s)));
  }, []);
  const failed = useCallback(async (error: string) => {
    say("err", error);
    await reload();
  }, [reload, say]);

  // ── selection ──
  const [selectedId, setSelectedId] = useDiveState<string>("scenario", "");
  const [tab, setTab] = useDiveState<Tab>("tab", "overview");
  const selected = scenarios.find((s) => s.scenario_id === selectedId) ?? scenarios.find((s) => !s.is_baseline) ?? scenarios[0];
  const editable = editableOf(selected);
  // The scenario dropdown next to the Baseline tab keeps showing the last
  // non-baseline scenario while the Baseline is selected.
  const others = scenarios.filter((s) => !s.is_baseline);
  const [lastPicked, setLastPicked] = useState("");
  useEffect(() => {
    if (selected && !selected.is_baseline) setLastPicked(selected.scenario_id);
  }, [selected]);
  const current = selected && !selected.is_baseline ? selected : others.find((s) => s.scenario_id === lastPicked) ?? others[0];

  // ── drivers: instant local recompute, debounced save ──
  const driverTimers = useRef<Record<string, any>>({});
  const setDrivers = useCallback((id: string, next: SupplyDrivers) => {
    setScenarios((list) => list.map((s) => (s.scenario_id === id ? { ...s, drivers: next } : s)));
    clearTimeout(driverTimers.current[id]);
    driverTimers.current[id] = setTimeout(() => {
      enqueue(async () => {
        const s = scenariosRef.current.find((x) => x.scenario_id === id);
        if (!s || !backend) return;
        const json = JSON.stringify(s.drivers);
        if (savedDrivers.current[id] === json) return;
        const r = await backend.call("update_scenario", { scenario_id: id, version: versions.current[id], drivers: s.drivers });
        if (r.ok) {
          savedDrivers.current[id] = json;
          mergeMeta(r.result);
          refreshActivitySoon();
        } else await failed(r.error);
      });
    }, 600);
  }, [backend, enqueue, failed, mergeMeta, refreshActivitySoon]);
  const updateDrivers = (fn: (d: SupplyDrivers) => SupplyDrivers) => {
    if (!selected || !editable) return;
    setDrivers(selected.scenario_id, fn(JSON.parse(JSON.stringify(selected.drivers))));
  };

  // ── grid: demand and supply overrides; edits made during a save go out together ──
  const pendingEdits = useRef<Record<string, Map<string, CellEdit>>>({});
  const flushQueued = useRef<Record<string, boolean>>({});
  // Overrides made in this browser session ("scenario|metric|region|category|week"):
  // the grid shows them in a stronger yellow than ones saved earlier.
  const [sessionEdits, setSessionEdits] = useState<Set<string>>(() => new Set());
  const onGridEdit = useCallback((edits: CellEdit[]) => {
    const s = selected;
    if (!s || !backend) return;
    setSessionEdits((prev) => {
      const next = new Set(prev);
      for (const e of edits) {
        const key = `${s.scenario_id}|${keyOf(e)}`;
        if (e.units == null) next.delete(key);
        else next.add(key);
      }
      return next;
    });
    setScenarios((list) => list.map((x) => {
      if (x.scenario_id !== s.scenario_id) return x;
      let ov = x.overrides.slice();
      for (const e of edits) {
        ov = ov.filter((o) => keyOf(o) !== keyOf(e));
        if (e.units != null) ov.push({ metric: e.metric, region: e.region, category: e.category, week_idx: e.week_idx, units: e.units, edited_by: actor });
      }
      return { ...x, overrides: ov };
    }));
    const id = s.scenario_id;
    const pending = (pendingEdits.current[id] ??= new Map());
    for (const e of edits) pending.set(keyOf(e), e);
    if (flushQueued.current[id]) return;
    flushQueued.current[id] = true;
    enqueue(async () => {
      flushQueued.current[id] = false;
      const changes = [...(pendingEdits.current[id]?.values() ?? [])];
      delete pendingEdits.current[id];
      if (!changes.length) return;
      const r = await backend.call("set_overrides", { scenario_id: id, changes });
      if (r.ok) {
        mergeMeta(r.result);
        refreshActivitySoon();
      } else await failed(r.error);
    });
  }, [selected, backend, actor, enqueue, mergeMeta, failed, refreshActivitySoon]);
  const clearOverrides = () => {
    if (!selected || !editable || !selected.overrides.length) return;
    onGridEdit(selected.overrides.map((o) => ({ metric: o.metric, region: o.region, category: o.category, week_idx: o.week_idx, units: null })));
  };

  // ── workflow buttons (the clicked one shows a spinner while its Flight runs) ──
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const busy = async <T,>(action: string, task: () => Promise<T>): Promise<T> => {
    setBusyAction(action);
    try {
      return await task();
    } finally {
      setBusyAction(null);
    }
  };
  const spin = (action: string, icon: React.ReactNode) => (busyAction === action ? <Loader2 size={14} className="animate-spin" /> : icon);

  /** Duplicate a scenario (the selected one by default) and select the copy. */
  const createScenario = async (from: Scenario | undefined = selected) => {
    if (!backend || !from) return;
    const name = (from.is_baseline ? "New supply scenario" : `${from.name} (copy)`).slice(0, 60);
    const r = await busy(`create:${from.scenario_id}`, () => enqueue(() => backend.call("create_scenario", { name, based_on: from.scenario_id })));
    if (!r.ok) return failed(r.error);
    const s = normalize(r.result);
    versions.current[s.scenario_id] = s.version;
    savedDrivers.current[s.scenario_id] = JSON.stringify(s.drivers);
    setScenarios((list) => [...list, s]);
    setSelectedId(s.scenario_id);
    refreshActivitySoon();
    say("ok", `Created “${s.name}” in MotherDuck`);
  };

  const [approvedNonce, setApprovedNonce] = useState(0);
  const refetchApproved = useCallback(() => setApprovedNonce((n) => n + 1), []);
  const setStatus = async (status: string) => {
    if (!backend || !selected) return;
    const id = selected.scenario_id;
    const r = await busy(status, () => enqueue(() => backend.call("set_status", { scenario_id: id, status, version: versions.current[id] })));
    if (!r.ok) return failed(r.error);
    mergeMeta(r.result);
    refreshActivitySoon();
    if (status === "approved") {
      say("ok", `Approved — supply plan written to ${DB}.${SCHEMA}.approved_plan`);
      refetchApproved();
    } else say("ok", status === "submitted" ? "Submitted for approval" : "Back in draft");
  };

  const pushToErp = async () => {
    if (!backend || !selected) return;
    const id = selected.scenario_id;
    const r = await busy("erp", () => enqueue(() => backend.call("push_to_erp", { scenario_id: id })));
    if (!r.ok) return failed(r.error);
    mergeMeta(r.result.scenario);
    refreshActivitySoon();
    refetchApproved();
    say("ok", r.result.lines
      ? `Queued ${fmtInt(r.result.lines)} order lines (${fmtInt(r.result.units)} units) in the ERP outbox`
      : "Nothing to push: this plan has no expedites, drop ships or subcontract orders");
  };

  const deleteScenario = async (id: string) => {
    if (!backend) return;
    const gone = scenariosRef.current.find((s) => s.scenario_id === id);
    const r = await busy(`delete:${id}`, () => enqueue(() => backend.call("delete_scenario", { scenario_id: id })));
    if (!r.ok) return failed(r.error);
    setScenarios((list) => list.filter((s) => s.scenario_id !== id));
    if (selected?.scenario_id === id) setSelectedId("");
    refreshActivitySoon();
    say("ok", `Deleted “${gone?.name ?? "scenario"}”`);
  };

  const renameScenario = async (id: string, raw: string) => {
    const s = scenariosRef.current.find((x) => x.scenario_id === id);
    const name = raw.trim().slice(0, 60);
    if (!backend || !s || !name || name === s.name) return;
    setScenarios((list) => list.map((x) => (x.scenario_id === id ? { ...x, name } : x)));
    const r = await enqueue(() => backend.call("update_scenario", { scenario_id: id, version: versions.current[id], name }));
    if (r.ok) {
      mergeMeta(r.result);
      refreshActivitySoon();
    } else await failed(r.error);
  };

  // ── the engine: each scenario with its levers, and with them off ──
  const pairs = useMemo(() => {
    const out: Record<string, Pair> = {};
    if (!base) return out;
    for (const s of scenarios) {
      const ov = overridesMap(s);
      out[s.scenario_id] = { plan: computeSupplyPlan(base, s.drivers, ov), off: computeSupplyPlan(base, leversOff(s.drivers), ov) };
    }
    return out;
  }, [base, scenarios]);
  const pair = selected ? pairs[selected.scenario_id] : undefined;

  // ── optimizer: the expedite, drop ship and subcontract levers with the highest net margin ──
  // Inputs are the scenario's demand, premium and grid overrides (saved or not); see optimizer.ts.
  const optInputs = (s: Scenario) => JSON.stringify([s.drivers.demand_source, s.drivers.demand_upside_pct, s.drivers.subcontract.premium_pct,
    Object.entries(overridesMap(s)).sort()]);
  const [optimizing, setOptimizing] = useState(false);
  const optRun = useRef(0);
  const optimize = async (id: string, manual: boolean) => {
    const s0 = scenariosRef.current.find((x) => x.scenario_id === id);
    if (!s0 || !base || !editableOf(s0)) return;
    const run = ++optRun.current;
    setOptimizing(true);
    try {
      const r = await optimizeLevers(base, s0.drivers, overridesMap(s0));
      const s1 = scenariosRef.current.find((x) => x.scenario_id === id);
      // A newer run, or inputs that moved on while this one ran (the next run covers them), wins.
      if (run !== optRun.current || !s1 || !editableOf(s1) || optInputs(s1) !== optInputs(s0) || (!manual && !s1.drivers.auto_optimize)) return;
      if (r.changed) {
        setDrivers(id, { ...s1.drivers, expedite: r.drivers.expedite, drop_ship: r.drivers.drop_ship,
          subcontract: { ...r.drivers.subcontract, premium_pct: s1.drivers.subcontract.premium_pct } });
      }
      if (manual) say("ok", r.changed ? `Optimized: net margin ${fmtMoney(r.net - r.currentNet)} higher (130,011 settings in ${(r.ms / 1000).toFixed(1)} s)` : "These levers already give the highest net margin");
    } finally {
      if (run === optRun.current) setOptimizing(false);
    }
  };
  const autoOptimize = !!selected?.drivers.auto_optimize && editable;
  const autoKey = selected && autoOptimize ? `${selected.scenario_id}|${optInputs(selected)}` : "";
  useEffect(() => {
    if (!autoKey || !selected || !base) return;
    const t = setTimeout(() => optimize(selected.scenario_id, false), 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoKey, base]);
  const setAutoOptimize = (on: boolean) => updateDrivers((d) => {
    const { auto_optimize: _a, ...rest } = d;
    return on ? { ...rest, auto_optimize: true } : rest;
  });

  // ── filters: narrow the KPIs, chart and grid to some regions / categories (empty = all) ──
  const [regionFilter, setRegionFilter] = useDiveState<string[]>("regions", []);
  const [categoryFilter, setCategoryFilter] = useDiveState<string[]>("categories", []);
  const regionSel = Array.isArray(regionFilter) ? regionFilter.filter((r) => !base || base.regions.includes(r)) : [];
  const categorySel = Array.isArray(categoryFilter) ? categoryFilter.filter((c) => !base || base.categories.some((x) => x.category === c)) : [];
  const filterKey = `${regionSel.join(",")}|${categorySel.join(",")}`;
  // Grid-only: which metric rows the grid shows (empty = all); doesn't touch the KPIs or chart.
  const [metricFilter, setMetricFilter] = useDiveState<string[]>("metrics", []);
  const metricSel = Array.isArray(metricFilter) ? metricFilter.filter((m) => METRICS.includes(m)) : [];
  // Grid-only: the rows' multi-column sort (the pivot Dive's), kept here and in the Dive state so it
  // outlives the grid's remount on a scenario or filter change, and a reload.
  const [gridSortRaw, setGridSort] = useDiveState<SortColumn[]>("sort", []);
  const gridSort = useMemo(() => cleanSort(gridSortRaw, isSortKey), [gridSortRaw]);
  const keep = useCallback((c: SupplyCell) => (!regionSel.length || regionSel.includes(c.region)) && (!categorySel.length || categorySel.includes(c.category)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [filterKey]);
  const summary = useMemo(() => (pair ? { plan: summarize(pair.plan, keep), off: summarize(pair.off, keep) } : null), [pair, keep]);
  const gridCells = useMemo(() => (pair ? pair.plan.cells.filter(keep) : []), [pair, keep]);
  const shortWeeks = useMemo(() => {
    const set = new Set<number>();
    summary?.off.weekly.lost.forEach((v, w) => v > 0 && set.add(w));
    return set;
  }, [summary]);
  // Who made each override of the selected scenario, and whether it was this session.
  const editInfo = useMemo(() => {
    const m: Record<string, EditInfo> = {};
    if (!selected) return m;
    for (const o of selected.overrides) {
      const key = keyOf(o);
      m[key] = { by: o.edited_by, at: o.edited_at, session: sessionEdits.has(`${selected.scenario_id}|${key}`) };
    }
    return m;
  }, [selected, sessionEdits]);

  // ── published supply plans and the ERP outbox, read back from MotherDuck ──
  const approvedQ = useSQLQuery(`
    /* refresh ${approvedNonce} */
    SELECT scenario_name, strftime(max(approved_at), '%Y-%m-%d %H:%M') AS approved_at, any_value(approved_by) AS approved_by,
           sum(sold_units)::BIGINT AS met_units, sum(demand_units)::BIGINT AS demand_units, sum(extra_cost) AS extra_cost, count(*)::INT AS row_count
    FROM ${S("approved_plan")} GROUP BY scenario_name ORDER BY max(approved_at) DESC`, { enabled: !!backend });
  const erpQ = useSQLQuery(`
    /* refresh ${approvedNonce} */
    SELECT scenario_name, order_type, count(*)::INT AS lines, sum(units)::BIGINT AS units, sum(est_cost) AS est_cost,
           strftime(max(pushed_at), '%Y-%m-%d %H:%M') AS pushed_at, any_value(pushed_by) AS pushed_by
    FROM ${S("erp_orders")} WHERE status = 'queued' GROUP BY ALL ORDER BY max(pushed_at) DESC, order_type`, { enabled: !!backend });

  const k = summary?.plan;
  const off = summary?.off;
  const roi = k && off ? roiOf(k, off) : null;
  const loading = !base || !backend || !scenarios.length;
  const onBaseline = !!selected?.is_baseline;
  const baselineScenario = scenarios.find((s) => s.is_baseline);

  return (
    <div style={{ height: "100vh", minHeight: PAGE_MIN_H, display: "flex", flexDirection: "column", background: C.sand, color: C.ink, fontFamily: SANS, letterSpacing: "0.01em" }}>
      <div className="p-5 flex flex-col gap-4" style={{ flex: 1, minHeight: 0, width: "100%", maxWidth: 1600, margin: "0 auto", boxSizing: "border-box" }}>
        {/* ── title, scenario switcher, save state and workflow ── */}
        <div className="flex flex-wrap items-center gap-3">
          <h1 style={{ margin: 0, minWidth: 0, fontFamily: MONO, fontWeight: 400, fontSize: 22, lineHeight: 1.2, textTransform: "uppercase" }}>Supply Chain Scenario Modeling</h1>
          <div className="flex flex-wrap items-center gap-2">
            {loading ? (
              [150, 240].map((w) => <div key={w} className="animate-pulse" style={{ width: w, height: 38, background: C.darkSnow, borderRadius: 2 }} />)
            ) : (
              <>
                {baselineScenario && (
                  <button type="button" data-testid="baseline-tab" onClick={() => setSelectedId(baselineScenario.scenario_id)} className="inline-flex items-center gap-2" style={tabStyle(onBaseline)}>
                    <Lock size={12} />
                    <span style={{ fontWeight: onBaseline ? 600 : 400 }}>{baselineScenario.name}</span>
                  </button>
                )}
                {current ? (
                  <ScenarioPicker
                    scenarios={others}
                    current={current}
                    baseline={baselineScenario}
                    active={!onBaseline}
                    busyAction={busyAction}
                    onSelect={setSelectedId}
                    onDuplicate={(s) => createScenario(s)}
                    onRename={renameScenario}
                    onDelete={deleteScenario}
                  />
                ) : baselineScenario && (
                  // No scenarios yet: the only way in is a new one from the Baseline.
                  <Btn variant="ghost" onClick={() => createScenario(baselineScenario)} disabled={!!busyAction} testId="new-scenario">
                    {spin(`create:${baselineScenario.scenario_id}`, <Plus size={14} />)}New scenario
                  </Btn>
                )}
              </>
            )}
          </div>
          <div className="flex items-center gap-3" style={{ marginLeft: "auto", flexWrap: "nowrap" }}>
          {backend && (
            <Capsule bg={C.sky} testId="mode-capsule">
              {saving ? <><Loader2 size={11} className="animate-spin" />Saving…</> : "Saved to MotherDuck"}
            </Capsule>
          )}
          {selected?.status === "draft" && !selected.is_baseline && (
            <Btn variant="go" onClick={() => setStatus("submitted")} disabled={!!busyAction} testId="submit">{spin("submitted", <Send size={14} />)}Submit for approval</Btn>
          )}
          {selected?.status === "submitted" && (
            <>
              <Btn variant="secondary" onClick={() => setStatus("draft")} disabled={!!busyAction}>{spin("draft", <Undo2 size={14} />)}Send back</Btn>
              <Btn variant="sun" onClick={() => setStatus("approved")} disabled={!!busyAction}>{spin("approved", <Check size={14} />)}Approve supply plan</Btn>
            </>
          )}
          {selected?.status === "approved" && (
            <>
              <Btn variant="sun" onClick={pushToErp} disabled={!!busyAction} title="Queue this plan's expedites, drop ships and subcontract orders in the ERP outbox">
                {spin("erp", <PackageCheck size={14} />)}Push to ERP
              </Btn>
              <Btn variant="secondary" onClick={() => setStatus("draft")} disabled={!!busyAction}>{spin("draft", <Undo2 size={14} />)}Reopen</Btn>
            </>
          )}
          </div>
        </div>

        {baseError ? (
          <p role="alert" style={{ color: C.darkWatermelon }}>Could not read the planning tables: {String(baseError)}</p>
        ) : loadError ? (
          <p role="alert" style={{ color: C.darkWatermelon }}>Could not load scenarios: {loadError}</p>
        ) : null}

        {/* both columns fill the rest of the viewport and end at the same line */}
        <div className="grid gap-4" style={{ flex: 1, minHeight: 0, gridTemplateColumns: "300px minmax(0, 1fr)", gridTemplateRows: "minmax(0, 1fr)" }}>
          <div className="flex flex-col gap-4" style={{ minHeight: 0 }}>
            <FiltersPanel
              regions={base?.regions ?? []}
              categories={base?.categories.map((c) => c.category) ?? []}
              regionSel={regionSel}
              categorySel={categorySel}
              onRegions={setRegionFilter}
              onCategories={setCategoryFilter}
            />
            <LeversPanel
              drivers={selected?.drivers}
              demandSources={demandSources}
              editable={editable}
              locked={selected ? (selected.is_baseline ? "The Baseline is locked. Start a new scenario from it in the scenario menu." : selected.status !== "draft" ? `This scenario is ${selected.status}. Reopen or send it back to edit.` : "") : ""}
              onChange={updateDrivers}
              onReset={() => selected && editable && setDrivers(selected.scenario_id, defaultDrivers())}
              autoOptimize={!!selected?.drivers.auto_optimize}
              onAutoOptimize={setAutoOptimize}
              onOptimize={() => selected && optimize(selected.scenario_id, true)}
              optimizing={optimizing}
              overrides={selected?.overrides ?? []}
              onClearOverrides={clearOverrides}
            />
          </div>

          <div className="flex flex-col gap-4" style={{ minWidth: 0, minHeight: 0 }}>
            {/* ── KPIs: with the levers vs the same demand with no action (net margin first: it decides) ── */}
            <div className="grid gap-6" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))", padding: "4px 2px" }}>
              <Kpi stackDelta testId="kpi-net" label="Net margin impact" loading={!k} value={k && off ? signed(fmtMoney(Math.abs(k.net_margin - off.net_margin)), k.net_margin - off.net_margin) : ""}
                delta={roi != null ? `$${roi.toFixed(2)} margin per $1 spent` : undefined}
                deltaGood={k && off ? (Math.abs(k.net_margin - off.net_margin) < 1 ? null : k.net_margin > off.net_margin) : null} />
              <Kpi stackDelta testId="kpi-met" label="Demand met" loading={!k} value={k ? fmtPct(k.fill_rate) : ""}
                delta={k && off ? `${pts(k.fill_rate - off.fill_rate)} vs no action` : undefined}
                deltaGood={k && off ? (Math.abs(k.fill_rate - off.fill_rate) < 1e-4 ? null : k.fill_rate > off.fill_rate) : null} />
              <Kpi stackDelta testId="kpi-revenue" label="Recovered revenue" loading={!k} value={k && off ? fmtMoney(k.revenue - off.revenue) : ""}
                delta={k && off ? `${fmtInt(k.met_units - off.met_units)} more units sold` : undefined} deltaGood={null} />
              <Kpi stackDelta testId="kpi-cost" label="Extra supply cost" loading={!k} value={k ? fmtMoney(k.extra_cost) : ""}
                delta={k && off && k.met_units > off.met_units ? `${fmtMoney(k.extra_cost / (k.met_units - off.met_units))}/unit recovered` : undefined} deltaGood={null} />
            </div>

            {/* ── tabs ── */}
            <div className="flex flex-wrap gap-0" role="tablist" style={{ borderBottom: border }}>
              {([
                ["overview", "Overview & supply and demand edits", LineIcon],
                ["compare", "Compare", GitCompare],
                ["activity", "Activity", Activity],
              ] as const).map(([key, label, Icon]) => (
                <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => setTab(key)} className="inline-flex items-center gap-2"
                  style={{
                    height: 38, padding: "0 16px", marginBottom: -2, border, borderBottom: tab === key ? `2px solid ${C.white}` : border,
                    borderLeft: key === "overview" ? border : "none", background: tab === key ? C.white : C.sand, cursor: "pointer",
                    fontFamily: MONO, fontSize: 12, textTransform: "uppercase", color: C.ink,
                  }}>
                  <Icon size={14} />{label}
                </button>
              ))}
            </div>

            <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflowY: tab === "overview" ? "hidden" : "auto" }}>
              {tab === "overview" && (
                <Overview summary={summary?.plan} base={base}>
                  {!pair || !base ? (
                    <div className="animate-pulse" style={{ height: "100%", background: C.darkSnow, borderRadius: 2 }} />
                  ) : !gridCells.length ? (
                    <p className="text-sm" style={{ color: C.darkerGrey }}>No rows match the filters.</p>
                  ) : (
                    <SupplyGrid
                      key={`${selected!.scenario_id}|${filterKey}`}
                      cells={gridCells}
                      weeks={base.weeks}
                      editable={editable}
                      shortWeeks={shortWeeks}
                      edits={editInfo}
                      onEdit={onGridEdit}
                      metricSel={metricSel}
                      sort={gridSort}
                      onSort={setGridSort}
                      // the rows re-sort when the levers or a reload change the plan, not on a grid edit
                      resortKey={`${loadNonce}|${JSON.stringify(selected!.drivers)}`}
                      filters={[
                        { col: 0, label: "Metric", options: METRICS, value: metricSel, onChange: setMetricFilter },
                        { col: 1, label: "Region", options: base.regions, value: regionSel, onChange: setRegionFilter },
                        { col: 2, label: "Category", options: base.categories.map((c) => c.category), value: categorySel, onChange: setCategoryFilter },
                      ]}
                      onBlocked={(message) => say("info", message || (selected!.is_baseline ? "The Baseline is locked — start a new scenario from it in the scenario menu." : "Reopen this scenario to edit it."))}
                    />
                  )}
                </Overview>
              )}
              {tab === "compare" && (
                <Compare scenarios={scenarios} pairs={pairs} selectedId={selected?.scenario_id} onSelect={setSelectedId}
                  approvedQ={approvedQ} erpQ={erpQ} refetch={refetchApproved} />
              )}
              {tab === "activity" && <ActivityList rows={activity} onRefresh={() => reload(true)} />}
            </div>
          </div>
        </div>
      </div>

      {toast && (
        <div role="status" style={{
          position: "fixed", top: 16, right: 16, zIndex: 50, display: "flex", alignItems: "center", gap: 8, maxWidth: 440,
          padding: "10px 14px", background: toast.kind === "err" ? C.lightWatermelon : toast.kind === "ok" ? C.white : C.lighterSun,
          border, borderRadius: 2, boxShadow: shadow(4), fontSize: 13,
        }}>
          {toast.kind === "err" ? <CircleAlert size={16} /> : <Check size={16} />}{toast.text}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── scenario picker ───────────────────────────

const tabStyle = (on: boolean): React.CSSProperties => ({
  height: 38, padding: "0 12px", border, borderRadius: 2, background: on ? C.white : C.sand, cursor: "pointer",
  boxShadow: on ? shadow(4) : "none", transform: on ? "translate(2px, -2px)" : "none",
  fontFamily: SANS, fontSize: 13, color: C.ink, transition: "transform 120ms ease-in-out",
});

function StatusDot({ s }: { s: Scenario }) {
  return <span style={{ flex: "none", width: 8, height: 8, borderRadius: "50%", border: `1.5px solid ${C.ink}`, background: STATUS_STYLE[s.status]?.bg }} title={STATUS_STYLE[s.status]?.label} />;
}

/** A small square icon button. "off" keeps it focusable and its tooltip visible but does nothing. */
function IconBtn({ title, onClick, off, danger, testId, children }: {
  title: string; onClick: () => void; off?: boolean; danger?: boolean; testId?: string; children: React.ReactNode;
}) {
  return (
    <button type="button" className="sp-icon" title={title} aria-label={title} aria-disabled={off || undefined} data-testid={testId}
      onClick={(e) => { e.stopPropagation(); if (!off) onClick(); }}
      style={{
        flex: "none", display: "inline-grid", placeItems: "center", width: 26, height: 26, padding: 0, border: 0, borderRadius: 2,
        background: "transparent", color: danger && !off ? C.darkWatermelon : C.ink, opacity: off ? 0.3 : 1, cursor: off ? "not-allowed" : "pointer",
      }}>
      {children}
    </button>
  );
}

/** Width of the scenario name in the closed picker box (longer names end in "…"). */
const BOX_NAME_W = 320;
const PICKER_CSS = `
.sp-icon:not([aria-disabled]):hover, .sp-icon:focus-visible { background: ${C.darkSnow} !important; }
.sp-row:hover { background: ${C.snow}; }
.sp-row[aria-selected="true"], .sp-row[aria-selected="true"]:hover { background: ${C.lightestSky}; }`;

/**
 * The scenario dropdown in the title row. The closed box shows the current
 * scenario with its duplicate / rename / delete icons; the menu lists every
 * scenario with the same icons, then "New scenario from Baseline". Rename is in
 * place; delete asks to confirm.
 */
function ScenarioPicker({ scenarios, current, baseline, active, busyAction, onSelect, onDuplicate, onRename, onDelete }: {
  scenarios: Scenario[]; current: Scenario; baseline?: Scenario; active: boolean; busyAction: string | null;
  onSelect: (id: string) => void; onDuplicate: (s: Scenario) => void;
  onRename: (id: string, name: string) => void; onDelete: (id: string) => Promise<unknown>;
}) {
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState<{ id: string; where: "box" | "menu"; value: string } | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open && !confirmId) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setConfirmId(null);
      }
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      setConfirmId(null);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open, confirmId]);

  const busy = !!busyAction;
  const commitRename = () => {
    if (renaming) onRename(renaming.id, renaming.value);
    setRenaming(null);
  };
  const statusLabel = (s: Scenario) => STATUS_STYLE[s.status]?.label ?? s.status;

  const isRenaming = (s: Scenario, where: "box" | "menu") => renaming?.id === s.scenario_id && renaming.where === where;
  const name = (s: Scenario, where: "box" | "menu") => (isRenaming(s, where) ? (
    <input autoFocus data-testid={`${where}-rename-input`} aria-label="Scenario name" value={renaming?.value ?? ""} maxLength={60}
      onChange={(e) => setRenaming({ id: s.scenario_id, where, value: e.target.value })}
      onBlur={commitRename}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter") commitRename();
        if (e.key === "Escape") setRenaming(null);
      }}
      onFocus={(e) => e.currentTarget.select()}
      style={{ flex: "1 1 auto", minWidth: 0, width: where === "box" ? BOX_NAME_W : undefined, height: 26, padding: "0 6px", border, borderRadius: 2, background: C.white, fontFamily: SANS, fontSize: 13, color: C.ink, outline: "none" }} />
  ) : (
    <span style={{ flex: where === "box" ? "none" : "1 1 auto", minWidth: 0, width: where === "box" ? BOX_NAME_W : undefined, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: where === "box" && active ? 600 : 400 }}>{s.name}</span>
  ));

  const actions = (s: Scenario, where: "box" | "menu") => {
    const id = s.scenario_id;
    if (confirmId === id) {
      return (
        <span className="inline-flex items-center gap-1" style={{ flex: "none" }} onClick={(e) => e.stopPropagation()}>
          <Btn variant="danger" small testId={`${where}-confirm-delete`} disabled={busy}
            onClick={async () => { await onDelete(id); setConfirmId(null); }}>
            {busyAction === `delete:${id}` ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}Delete?
          </Btn>
          <IconBtn title="Keep it" onClick={() => setConfirmId(null)}><X size={14} /></IconBtn>
        </span>
      );
    }
    const draft = s.status === "draft";
    return (
      <span className="inline-flex items-center" style={{ flex: "none", gap: 1 }}>
        <IconBtn title={draft ? "Rename" : `${statusLabel(s)}: reopen or send it back to draft to rename`} testId={`${where}-rename`} off={busy || !draft}
          onClick={() => { setConfirmId(null); setRenaming({ id, where, value: s.name }); }}>
          <Pencil size={14} />
        </IconBtn>
        <IconBtn title={`Duplicate “${s.name}”`} testId={`${where}-duplicate`} off={busy}
          onClick={() => { setOpen(false); onDuplicate(s); }}>
          {busyAction === `create:${id}` ? <Loader2 size={14} className="animate-spin" /> : <Copy size={14} />}
        </IconBtn>
        <IconBtn title={s.status === "approved" ? "Approved: reopen it to delete" : "Delete"} testId={`${where}-delete`} danger off={busy || s.status === "approved"}
          onClick={() => { setRenaming(null); setConfirmId(id); }}>
          <Trash2 size={14} />
        </IconBtn>
      </span>
    );
  };

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <style>{PICKER_CSS}</style>
      <div data-testid="scenario-select" className="inline-flex items-center" style={{ ...tabStyle(active), gap: 6, padding: "0 4px 0 12px", cursor: "default" }}>
        <button type="button" data-testid="scenario-current" aria-haspopup="listbox" aria-expanded={open}
          title={active ? "Choose a scenario" : `Show “${current.name}”`}
          onClick={() => (active ? setOpen((o) => !o) : onSelect(current.scenario_id))}
          className="inline-flex items-center gap-2"
          style={{ minWidth: 0, height: 34, padding: 0, border: 0, background: "transparent", cursor: "pointer", fontFamily: SANS, fontSize: 13, color: C.ink }}>
          <StatusDot s={current} />
          {!isRenaming(current, "box") && name(current, "box")}
        </button>
        {isRenaming(current, "box") && name(current, "box")}
        {actions(current, "box")}
        <button type="button" data-testid="scenario-toggle" aria-label="Show all scenarios" aria-haspopup="listbox" aria-expanded={open}
          className="sp-icon" onClick={() => setOpen((o) => !o)}
          style={{ display: "inline-grid", placeItems: "center", width: 26, height: 30, padding: 0, border: 0, borderLeft: `1px solid ${C.darkSand}`, borderRadius: 0, background: "transparent", cursor: "pointer", color: C.ink }}>
          <ChevronDown size={15} style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 120ms ease-in-out" }} />
        </button>
      </div>
      {open && (
        <div role="listbox" aria-label="Scenarios" data-testid="scenario-menu"
          style={{ position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 60, width: 460, minWidth: "100%", maxWidth: "90vw", maxHeight: 380, overflow: "auto", padding: 4, background: C.white, border, borderRadius: 2, boxShadow: shadow(4) }}>
          {scenarios.map((s) => (
            <div key={s.scenario_id} role="option" aria-selected={s.scenario_id === current.scenario_id} className="sp-row" data-testid="scenario-option"
              onClick={() => {
                if (isRenaming(s, "menu") || confirmId === s.scenario_id) return;
                onSelect(s.scenario_id);
                setOpen(false);
              }}
              style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 36, padding: "2px 4px 2px 10px", borderRadius: 2, cursor: "pointer", fontSize: 13 }}>
              <StatusDot s={s} />
              {name(s, "menu")}
              <span style={{ flex: "none", fontFamily: MONO, fontSize: 10, textTransform: "uppercase", color: C.darkerGrey }}>{statusLabel(s)}</span>
              {actions(s, "menu")}
            </div>
          ))}
          {baseline && (
            <div role="option" aria-selected={false} className="sp-row" data-testid="scenario-new"
              onClick={() => { if (busy) return; setOpen(false); onDuplicate(baseline); }}
              style={{ display: "flex", alignItems: "center", gap: 8, minHeight: 36, marginTop: 4, padding: "2px 4px 2px 10px", borderTop: `1px solid ${C.darkSand}`, borderRadius: 2, cursor: busy ? "default" : "pointer", fontSize: 13, opacity: busy ? 0.5 : 1 }}>
              {busyAction === `create:${baseline.scenario_id}` ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
              New scenario from {baseline.name}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── filters ───────────────────────────

function FiltersPanel(props: {
  regions: string[]; categories: string[]; regionSel: string[]; categorySel: string[];
  onRegions: (v: string[]) => void; onCategories: (v: string[]) => void;
}) {
  const any = props.regionSel.length > 0 || props.categorySel.length > 0;
  return (
    <div data-testid="filters" className="grid gap-3" style={{ gridTemplateColumns: "minmax(0, 1fr)", padding: 16, background: C.white, border, borderRadius: 2, boxShadow: shadow(4) }}>
      <div className="flex items-center justify-between">
        <SectionTitle>Filters</SectionTitle>
        <Btn variant="ghost" small disabled={!any} onClick={() => { props.onRegions([]); props.onCategories([]); }}><RefreshCw size={12} />Reset</Btn>
      </div>
      <MultiSelect label="Region" plural="regions" testId="filter-region" options={props.regions} value={props.regionSel} onChange={props.onRegions} />
      <MultiSelect label="Category" plural="categories" testId="filter-category" options={props.categories} value={props.categorySel} onChange={props.onCategories} />
    </div>
  );
}

/** A dropdown of checkboxes; nothing checked means all. */
function MultiSelect({ label, plural, options, value, onChange, testId }: {
  label: string; plural: string; options: string[]; value: string[]; onChange: (v: string[]) => void; testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);
  const all = value.length === 0 || value.length === options.length;
  const summary = all ? `All ${plural}` : value.length === 1 ? value[0] : `${value.length} of ${options.length} ${plural}`;
  const toggle = (o: string) => {
    const next = all ? [o] : value.includes(o) ? value.filter((x) => x !== o) : [...value, o];
    onChange(next.length === options.length ? [] : options.filter((x) => next.includes(x)));
  };
  const row: React.CSSProperties = { display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", fontSize: 13, cursor: "pointer" };
  return (
    <div ref={ref} style={{ position: "relative", minWidth: 0 }}>
      <button type="button" data-testid={testId} aria-label={label} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, width: "100%", height: 34, padding: "0 8px", border, borderRadius: 2, background: all ? C.white : C.lightestSky, color: C.ink, fontFamily: SANS, fontSize: 13, cursor: "pointer", minWidth: 0 }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{summary}</span>
        <ChevronDown size={14} style={{ flex: "none" }} />
      </button>
      {open && (
        <div role="listbox" aria-multiselectable="true" data-testid={testId ? `${testId}-menu` : undefined}
          style={{ position: "absolute", top: "100%", left: 0, right: 0, marginTop: 4, zIndex: 40, background: C.white, border, borderRadius: 2, boxShadow: shadow(4), maxHeight: 280, overflow: "auto" }}>
          <label style={{ ...row, borderBottom: `1px solid ${C.darkSnow}` }}>
            <input type="checkbox" checked={all} onChange={() => onChange([])} style={{ accentColor: C.darkSky }} />All {plural}
          </label>
          {options.map((o) => (
            <label key={o} role="option" aria-selected={!all && value.includes(o)} style={row}>
              <input type="checkbox" checked={!all && value.includes(o)} onChange={() => toggle(o)} style={{ accentColor: C.darkSky }} />{o}
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── levers ───────────────────────────

function LeversPanel(props: {
  drivers?: SupplyDrivers; demandSources: Array<[string, string]>; editable: boolean; locked: string;
  onChange: (fn: (d: SupplyDrivers) => SupplyDrivers) => void; onReset: () => void; overrides: Scenario["overrides"]; onClearOverrides: () => void;
  autoOptimize: boolean; onAutoOptimize: (on: boolean) => void; onOptimize: () => void; optimizing: boolean;
}) {
  const { drivers: d, editable, onChange, autoOptimize: auto } = props;
  if (!d) return <div className="animate-pulse" style={{ flex: 1, minHeight: 0, background: C.darkSnow, borderRadius: 2 }} />;
  // In auto-optimize the optimizer owns the expedite, drop ship and subcontract levers.
  const leverOff = !editable || auto;
  const autoMark = auto ? <AutoMark /> : null;
  const section = { gridTemplateColumns: "minmax(0, 1fr)", paddingTop: 10, borderTop: `1px solid ${C.darkSand}` } as const;
  return (
    <div data-testid="levers" className="grid gap-3" style={{ flex: 1, minHeight: 0, overflowY: "auto", alignContent: "start", gridTemplateColumns: "minmax(0, 1fr)", padding: 16, background: C.white, border, borderRadius: 2, boxShadow: shadow(4) }}>
      <div className="flex items-center justify-between">
        <SectionTitle>Supply levers</SectionTitle>
        <Btn variant="ghost" small onClick={props.onReset} disabled={!editable}><RefreshCw size={12} />Reset</Btn>
      </div>
      <div className="flex items-center gap-2" data-testid="optimize-row">
        <button type="button" role="switch" aria-checked={auto} aria-label="Auto-optimize margins" data-testid="auto-optimize" disabled={!editable}
          title={auto ? "Auto-optimize is on: click to set the levers yourself" : "Auto-optimize: keep the expedite, drop ship and subcontract levers at the highest net margin as demand, the premium and the grid change"}
          onClick={() => props.onAutoOptimize(!auto)}
          style={{ position: "relative", width: 34, height: 18, flex: "none", padding: 0, border: `1.5px solid ${C.ink}`, borderRadius: 9, background: auto ? C.garden : C.white, cursor: editable ? "pointer" : "default", opacity: editable ? 1 : 0.55, transition: "background 120ms" }}>
          <span style={{ position: "absolute", top: 1.5, left: auto ? 17 : 1.5, width: 12, height: 12, borderRadius: 6, background: auto ? C.white : C.ink, transition: "left 120ms" }} />
        </button>
        <Btn variant={auto ? "secondary" : "primary"} small fill testId="optimize" onClick={props.onOptimize} disabled={!editable || auto || props.optimizing}
          title={auto ? "The levers marked with the wand follow demand, the premium and the grid. Switch auto-optimize off to set them yourself." : "Set the expedite, drop ship and subcontract levers to the highest net margin for this demand, premium and grid"}>
          {props.optimizing ? <Loader2 size={13} className="animate-spin" /> : <WandSparkles size={13} />}{auto ? "Auto-Optimizing Margins" : "Optimize Margins"}
        </Btn>
      </div>
      {props.locked && (
        <p className="text-xs flex items-start gap-2" style={{ margin: 0, padding: 8, background: C.darkSnow, borderRadius: 2 }}>
          <Lock size={12} style={{ flex: "none", marginTop: 2 }} />{props.locked}
        </p>
      )}

      <div className="grid gap-2" style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
        <SectionTitle size={12}>Demand to plan for</SectionTitle>
        <Select value={props.demandSources.some(([id]) => id === d.demand_source) ? d.demand_source : "baseline"} disabled={!editable}
          onChange={(v) => onChange((x) => ({ ...x, demand_source: v }))} options={props.demandSources} />
        <Slider label="Demand vs forecast" value={d.demand_upside_pct} min={-20} max={40} step={1} disabled={!editable}
          onChange={(v) => onChange((x) => ({ ...x, demand_upside_pct: v }))} />
      </div>

      <div className="grid gap-2" style={section}>
        <SectionTitle size={12}><span className="inline-flex items-center gap-2"><Zap size={13} />Expedited shipping{autoMark}</span></SectionTitle>
        <div className="flex gap-1">
          {SPEED_ORDER.map((sp) => (
            <Chip key={sp} on={d.expedite.speed === sp} disabled={leverOff} onClick={() => onChange((x) => ({ ...x, expedite: { ...x.expedite, speed: sp } }))}>
              <span title={sp === "none" ? "Shortfalls are lost" : `$${SPEEDS[sp].cost.toFixed(2)}/unit, wins back ${Math.round(SPEEDS[sp].recapture * 100)}% of a shortfall`}>
                {sp === "none" ? "Off" : SPEEDS[sp].label}
              </span>
            </Chip>
          ))}
        </div>
        <Slider label="Expedite capacity" value={d.expedite.capacity_pct} min={0} max={50} step={1} badge={autoMark}
          disabled={leverOff || d.expedite.speed === "none"} onChange={(v) => onChange((x) => ({ ...x, expedite: { ...x.expedite, capacity_pct: v } }))} />
      </div>

      <div className="grid gap-2" style={section}>
        <div className="flex items-center justify-between">
          <SectionTitle size={12}><span className="inline-flex items-center gap-2" title={`Supplier ships straight to the customer: $${DROP_SHIP.cost.toFixed(2)}/unit, keeps ${Math.round(DROP_SHIP.recapture * 100)}% of customers`}><Truck size={13} />Drop ship to customer{autoMark}</span></SectionTitle>
          <label className="inline-flex items-center gap-2 text-xs" style={{ cursor: editable ? "pointer" : "default" }}>
            <input type="checkbox" checked={d.drop_ship.enabled} disabled={leverOff} style={{ accentColor: C.darkSky }}
              onChange={(e) => onChange((x) => ({ ...x, drop_ship: { ...x.drop_ship, enabled: e.target.checked } }))} />On
          </label>
        </div>
        <Slider label="Share of what's still short" value={d.drop_ship.share_pct} min={0} max={100} step={5} badge={autoMark} disabled={leverOff || !d.drop_ship.enabled}
          onChange={(v) => onChange((x) => ({ ...x, drop_ship: { ...x.drop_ship, share_pct: v } }))} />
      </div>

      <div className="grid gap-2" style={section}>
        <div className="flex items-center justify-between">
          <SectionTitle size={12}><span className="inline-flex items-center gap-2"><Factory size={13} />Subcontract manufacturer{autoMark}</span></SectionTitle>
          <label className="inline-flex items-center gap-2 text-xs" style={{ cursor: editable ? "pointer" : "default" }}>
            <input type="checkbox" checked={d.subcontract.enabled} disabled={leverOff} style={{ accentColor: C.darkSky }}
              onChange={(e) => onChange((x) => ({ ...x, subcontract: { ...x.subcontract, enabled: e.target.checked } }))} />On
          </label>
        </div>
        <Slider label="Extra capacity" value={d.subcontract.capacity_pct} min={0} max={40} step={1} badge={autoMark} disabled={leverOff || !d.subcontract.enabled}
          onChange={(v) => onChange((x) => ({ ...x, subcontract: { ...x.subcontract, capacity_pct: v } }))} />
        <Slider label="Cost premium" value={d.subcontract.premium_pct} min={10} max={60} step={1} disabled={!editable || (!d.subcontract.enabled && !auto)}
          onChange={(v) => onChange((x) => ({ ...x, subcontract: { ...x.subcontract, premium_pct: v } }))} />
      </div>

      {props.overrides.length > 0 && (
        <div className="flex items-center justify-between text-xs" style={{ paddingTop: 12, borderTop: `1px solid ${C.darkSand}` }}>
          <span><span style={{ display: "inline-block", width: 10, height: 10, background: C.lighterSun, border: `1.5px solid ${C.ink}`, marginRight: 6, verticalAlign: -1 }} />{overrideSummary(props.overrides)}</span>
          <Btn variant="ghost" small disabled={!editable} onClick={props.onClearOverrides}>Clear</Btn>
        </div>
      )}
    </div>
  );
}

/** Marks a lever that auto-optimize sets. */
function AutoMark() {
  return (
    <span title="Set by auto-optimize" aria-label="Set by auto-optimize" data-testid="auto-mark"
      style={{ display: "inline-flex", alignItems: "center", marginLeft: 6, marginRight: 4, verticalAlign: -2, color: C.garden }}>
      <WandSparkles size={13} strokeWidth={2.25} />
    </span>
  );
}

/** "2 demand + 1 supply overrides" */
function overrideSummary(overrides: Scenario["overrides"]) {
  const parts = OVERRIDE_METRICS.map((m) => [m, overrides.filter((o) => o.metric === m).length] as const).filter(([, n]) => n > 0);
  return `${parts.map(([m, n]) => `${n} ${m}`).join(" + ")} override${overrides.length === 1 ? "" : "s"}`;
}

// ─────────────────────────── overview ───────────────────────────

// Chart series, in legend and tooltip order: lines first, then how demand was met.
const CHART_SERIES: Array<{ key: string; name: string; color: string; kind: "line" | "dash" | "bar" }> = [
  { key: "demand", name: "Demand", color: SERIES.demand, kind: "line" },
  { key: "supply", name: "Supply", color: SERIES.supply, kind: "dash" },
  { key: "inventory", name: "Inventory", color: SERIES.inventory, kind: "line" },
  { key: "fromStock", name: "From stock", color: C.lighterGarden, kind: "bar" },
  { key: "expedited", name: "Expedited", color: C.sky, kind: "bar" },
  { key: "dropShip", name: "Drop shipped", color: C.sun, kind: "bar" },
  { key: "lost", name: "Lost", color: C.watermelon, kind: "bar" },
];

function SeriesSwatch({ kind, color }: { kind: "line" | "dash" | "bar"; color: string }) {
  return kind === "bar"
    ? <span style={{ display: "inline-block", width: 10, height: 10, background: color, border: `1px solid ${C.ink}` }} />
    : <span style={{ display: "inline-block", width: 16, height: 0, borderTop: `${kind === "dash" ? 3.5 : 4}px ${kind === "dash" ? "dashed" : "solid"} ${color}` }} />;
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload ?? {};
  return (
    <div style={{ background: C.white, border, borderRadius: 2, padding: "6px 10px", fontSize: 12, boxShadow: shadow(2) }}>
      <div style={{ fontFamily: MONO, textTransform: "uppercase", marginBottom: 4 }}>{label}</div>
      {CHART_SERIES.map((s) => (
        <div key={s.key} className="flex items-center gap-2" style={{ justifyContent: "space-between" }}>
          <span className="inline-flex items-center gap-2"><SeriesSwatch kind={s.kind} color={s.color} />{s.name}</span>
          <span style={{ fontVariantNumeric: "tabular-nums", marginLeft: 12 }}>{fmtInt(Number(row[s.key] ?? 0))}</span>
        </div>
      ))}
    </div>
  );
}

// Lines are drawn 2px heavier than recharts' default weight so they read over the bars.
const LINE_W = { demand: 4, supply: 3.5, inventory: 4 };

/** The weekly chart over the supply and demand grid; the grid takes the height left in the viewport. */
function Overview({ summary, base, children }: { summary?: Summary; base: SupplyBase | null; children?: React.ReactNode }) {
  if (!summary || !base) return <div className="animate-pulse" style={{ flex: 1, minHeight: 0, background: C.darkSnow, borderRadius: 2 }} />;
  const wk = summary.weekly;
  const data = base.weeks.map((w, i) => ({
    label: weekLabel(w),
    demand: wk.demand[i],
    supply: wk.supply[i],
    inventory: wk.inventory[i],
    fromStock: wk.from_stock[i],
    expedited: wk.expedited[i],
    dropShip: wk.drop_ship[i],
    lost: wk.lost[i],
  }));
  const bar = (key: string) => CHART_SERIES.find((s) => s.key === key)!;
  return (
    <div className="flex flex-col gap-4" style={{ flex: 1, minHeight: 0 }}>
      <div data-testid="chart-block" style={{ flex: "none" }}>
        <div className="flex items-baseline justify-between">
          <SectionTitle>Weekly demand met, units</SectionTitle>
          <span className="text-xs" style={{ color: C.darkGrey }}>Lines: demand, supply and end-of-week inventory · bars: how demand was met</span>
        </div>
        <div style={{ height: 240, marginTop: 6 }}>
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke={C.darkSand} vertical={false} />
              <XAxis dataKey="label" fontSize={11} tick={{ fill: C.ink }} interval={2} />
              <YAxis fontSize={11} tick={{ fill: C.ink }} tickFormatter={(v) => `${Math.round(v / 1000)}K`} width={44} />
              <Tooltip content={<ChartTooltip />} />
              {["fromStock", "expedited", "dropShip", "lost"].map((key) => (
                <Bar key={key} dataKey={key} name={bar(key).name} stackId="m" fill={bar(key).color} isAnimationActive={false} />
              ))}
              <Line type="linear" dataKey="demand" name="Demand" stroke={SERIES.demand} strokeWidth={LINE_W.demand} dot={false} isAnimationActive={false} />
              <Line type="linear" dataKey="supply" name="Supply" stroke={SERIES.supply} strokeDasharray="6 4" strokeWidth={LINE_W.supply} dot={false} isAnimationActive={false} />
              <Line type="linear" dataKey="inventory" name="Inventory" stroke={SERIES.inventory} strokeWidth={LINE_W.inventory} dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </div>
        <div data-testid="chart-legend" className="flex flex-wrap items-center justify-center gap-4 text-xs" style={{ marginTop: 6 }}>
          {CHART_SERIES.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-2"><SeriesSwatch kind={s.kind} color={s.color} />{s.name}</span>
          ))}
        </div>
      </div>
      <div className="flex flex-col" style={{ flex: 1, minHeight: 0 }}>
        <SectionTitle>Supply and Demand Edits</SectionTitle>
        <div data-testid="grid-area" style={{ position: "relative", flex: 1, minHeight: 0, marginTop: 8 }}>
          <div style={{ position: "absolute", inset: 0 }}>{children}</div>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────── compare ───────────────────────────

function Compare({ scenarios, pairs, selectedId, onSelect, approvedQ, erpQ, refetch }: {
  scenarios: Scenario[]; pairs: Record<string, Pair>; selectedId?: string; onSelect: (id: string) => void;
  approvedQ: any; erpQ: any; refetch: () => void;
}) {
  const rows = scenarios.filter((s) => pairs[s.scenario_id]).map((s) => ({ s, p: pairs[s.scenario_id] }));
  const approved = Array.isArray(approvedQ.data) ? approvedQ.data : [];
  const erp = Array.isArray(erpQ.data) ? erpQ.data : [];
  const cell = { padding: "8px 6px" } as const;
  return (
    <div className="grid gap-5">
      <table className="w-full text-sm" style={{ borderCollapse: "collapse", fontVariantNumeric: "tabular-nums" }}>
        <thead>
          <tr style={{ borderBottom: border, textAlign: "right", fontFamily: MONO, fontSize: 11, textTransform: "uppercase" }}>
            <th style={{ ...cell, textAlign: "left", fontWeight: 400 }}>Scenario</th>
            <th style={{ ...cell, fontWeight: 400 }}>Demand</th>
            <th style={{ ...cell, fontWeight: 400 }}>Met</th>
            <th style={{ ...cell, fontWeight: 400 }}>Revenue</th>
            <th style={{ ...cell, fontWeight: 400 }}>Extra cost</th>
            <th style={{ ...cell, fontWeight: 400 }}>Net margin</th>
            <th style={{ ...cell, fontWeight: 400 }}>vs no action</th>
            <th style={{ ...cell, fontWeight: 400 }}>End inventory</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ s, p }) => {
            const k = p.plan.kpis;
            const d = k.net_margin - p.off.kpis.net_margin;
            return (
              <tr key={s.scenario_id} onClick={() => onSelect(s.scenario_id)} style={{ borderBottom: `1px solid ${C.darkSnow}`, textAlign: "right", cursor: "pointer", outline: s.scenario_id === selectedId ? `2px solid ${C.darkSky}` : "none", outlineOffset: -2 }}>
                <td style={{ ...cell, textAlign: "left" }}>
                  <span className="inline-flex items-center gap-2">
                    <span style={{ width: 8, height: 8, borderRadius: "50%", border: `1.5px solid ${C.ink}`, background: STATUS_STYLE[s.status]?.bg }} />{s.name}
                  </span>
                </td>
                <td style={cell}>{fmtInt(k.demand_units)}</td>
                <td style={cell}>{fmtPct(k.fill_rate)}</td>
                <td style={cell}>{fmtMoney(k.revenue)}</td>
                <td style={cell}>{k.extra_cost > 0 ? fmtMoney(k.extra_cost) : "—"}</td>
                <td style={cell}>{fmtMoney(k.net_margin)}</td>
                <td style={{ ...cell, color: Math.abs(d) < 1 ? C.darkGrey : d > 0 ? C.darkGarden : C.darkWatermelon }}>{Math.abs(d) < 1 ? "—" : signed(fmtMoney(Math.abs(d)), d)}</td>
                <td style={cell}>{fmtMoney(k.end_inventory_value)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="grid gap-5" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)" }}>
        <div>
          <div className="flex items-center justify-between">
            <SectionTitle>Approved supply plans</SectionTitle>
            <Btn variant="ghost" small onClick={refetch} title="Re-read from MotherDuck"><RefreshCw size={12} /></Btn>
          </div>
          <p className="text-xs" style={{ margin: "4px 0 8px", color: C.darkGrey }}>From <code>{DB}.{SCHEMA}.approved_plan</code>, written by the supply-planner-write Flight on approval.</p>
          {approvedQ.isLoading && !approved.length ? <div className="animate-pulse h-4 rounded" style={{ background: C.darkSnow }} />
            : approvedQ.isError ? <p role="alert" className="text-xs" style={{ color: C.darkWatermelon }}>{String(approvedQ.error)}</p>
            : !approved.length ? <p className="text-sm" style={{ color: C.darkerGrey }}>No approved supply plan yet.</p>
            : (
              <table className="w-full text-xs" style={{ borderCollapse: "collapse", fontVariantNumeric: "tabular-nums" }}>
                <tbody>
                  {approved.map((r: any) => (
                    <tr key={String(r.scenario_name)} style={{ borderBottom: `1px solid ${C.darkSnow}` }}>
                      <td style={{ padding: "6px 4px" }}><b>{String(r.scenario_name)}</b><div style={{ color: C.darkGrey }}>{String(r.approved_at)} UTC · {String(r.approved_by)}</div></td>
                      <td style={{ padding: "6px 4px", textAlign: "right" }}>{fmtPct(N(r.demand_units) ? N(r.met_units) / N(r.demand_units) : 1)} met<div style={{ color: C.darkGrey }}>{fmtMoney(N(r.extra_cost))} extra cost</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
        <div>
          <SectionTitle>ERP outbox</SectionTitle>
          <p className="text-xs" style={{ margin: "4px 0 8px", color: C.darkGrey }}>Queued lines in <code>{DB}.{SCHEMA}.erp_orders</code>, added by “Push to ERP” on an approved plan for the ERP connector to pick up.</p>
          {erpQ.isLoading && !erp.length ? <div className="animate-pulse h-4 rounded" style={{ background: C.darkSnow }} />
            : erpQ.isError ? <p role="alert" className="text-xs" style={{ color: C.darkWatermelon }}>{String(erpQ.error)}</p>
            : !erp.length ? <p className="text-sm" style={{ color: C.darkerGrey }}>Nothing queued.</p>
            : (
              <table className="w-full text-xs" style={{ borderCollapse: "collapse", fontVariantNumeric: "tabular-nums" }}>
                <tbody>
                  {erp.map((r: any, i: number) => (
                    <tr key={i} style={{ borderBottom: `1px solid ${C.darkSnow}` }}>
                      <td style={{ padding: "6px 4px" }}><b>{String(r.scenario_name)}</b> · {String(r.order_type).replace("_", " ")}<div style={{ color: C.darkGrey }}>{String(r.pushed_at)} UTC · {String(r.pushed_by)}</div></td>
                      <td style={{ padding: "6px 4px", textAlign: "right" }}>{fmtInt(N(r.units))} units<div style={{ color: C.darkGrey }}>{fmtInt(N(r.lines))} lines{r.est_cost != null ? ` · ${fmtMoney(N(r.est_cost))}` : ""}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────── activity ───────────────────────────

function ActivityList({ rows, onRefresh }: { rows: ActivityRow[]; onRefresh: () => void }) {
  const tone: Record<string, string> = { approved: C.lighterGarden, submitted: C.sun, override: C.lighterSun, drivers: C.lightestSky, create: C.white, delete: C.lightWatermelon, draft: C.white, erp: C.duck, seed: C.darkSand };
  return (
    <div className="grid gap-3">
      <div className="flex items-center justify-between">
        <SectionTitle>Writeback activity</SectionTitle>
        <Btn variant="ghost" small onClick={onRefresh}><RefreshCw size={12} />Refresh</Btn>
      </div>
      <p className="text-xs" style={{ margin: 0, color: C.darkGrey }}>From {DB}.{SCHEMA}.activity_log, written by the supply-planner-write Flight on every save.</p>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, border, borderRadius: 2, background: C.white, maxHeight: 480, overflow: "auto" }}>
        {rows.length === 0 && <li className="text-sm" style={{ padding: 12, color: C.darkerGrey }}>No activity yet.</li>}
        {rows.map((r, i) => (
          <li key={i} className="grid text-sm" style={{ gridTemplateColumns: "150px 110px 1fr", gap: 12, padding: "8px 12px", borderBottom: `1px solid ${C.darkSnow}`, alignItems: "baseline" }}>
            <span className="text-xs" style={{ color: C.darkGrey, fontVariantNumeric: "tabular-nums" }}>{new Date(r.ts).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
            <span><Capsule bg={tone[r.action] ?? C.white}>{r.action}</Capsule></span>
            <span><b>{r.actor}</b>{r.scenario_name ? <> · {r.scenario_name}</> : null} — {r.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
