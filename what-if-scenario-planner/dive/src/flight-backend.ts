// Writeback transport for the Dive App build: every write runs a MotherDuck Flight.
//
// The Dive reads the planner database through a share owned by the
// scenario_planner_writer service account, and can never write it. A write
// runs the supply-planner-write Flight (owned by that service account, which
// granted the organization `run`) with the RPC op in its config. The Flight
// runs ../flight/planner.py's Api, stores the reply in rpc_results and
// snapshots the database; the Dive then refreshes its share and reads the
// reply. `call(op, args)` returns an RpcResult. Each write takes a few seconds.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useFlightRun, useRunFlight, useSQLQuery } from "@motherduck/react-sql-query";
import { DB, useImperativeSql } from "./sql";

export type RpcResult<T = any> = { ok: true; result: T } | { ok: false; status: number; error: string };
export type Backend = {
  mode: "host";
  /** The viewer's MotherDuck user name, recorded on every change. */
  actor: string;
  call<T = any>(op: string, args?: Record<string, unknown>): Promise<RpcResult<T>>;
};
const RUN_TIMEOUT_MS = 180_000;

const T = (schema: string, table: string) => `"${DB}"."${schema}"."${table}"`;
const iso = (col: string) => `strftime(${col} AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%SZ')`;
const quote = (s: string) => `'${s.replace(/'/g, "''")}'`;

// The `bootstrap` read, as one statement: scenarios with their overrides,
// the last 60 activity rows and the 5 latest published plans.
const bootstrapSql = (schema: string, overrideMetric: boolean) => {
  const T = (table: string) => `"${DB}"."${schema}"."${table}"`;
  return `
SELECT to_json({
  scenarios: coalesce((
    SELECT list({
      scenario_id: s.scenario_id, name: s.name, description: s.description, status: s.status,
      is_baseline: s.is_baseline, version: s.version, drivers: s.drivers,
      created_by: s.created_by, updated_by: s.updated_by, updated_at: ${iso("s.updated_at")},
      submitted_by: s.submitted_by, approved_by: s.approved_by, approved_at: ${iso("s.approved_at")},
      overrides: coalesce((
        SELECT list({${overrideMetric ? "metric: o.metric, " : ""}region: o.region, category: o.category, week_idx: o.week_idx, units: o.units, edited_by: o.edited_by, edited_at: ${iso("o.edited_at")}})
        FROM ${T("scenario_overrides")} o WHERE o.scenario_id = s.scenario_id), [])
    } ORDER BY s.is_baseline DESC, s.created_at)
    FROM ${T("scenarios")} s), []),
  activity: coalesce((
    SELECT list({ts: ${iso("ts")}, scenario_id: scenario_id, scenario_name: scenario_name, actor: actor, action: action, detail: detail} ORDER BY ts DESC)
    FROM (SELECT * FROM ${T("activity_log")} ORDER BY ts DESC LIMIT 60)), []),
  approved: coalesce((
    SELECT list({scenario_id: scenario_id, scenario_name: scenario_name, approved_at: ${iso("approved_at")},
      approved_by: approved_by, revenue: revenue, sold_units: sold_units, row_count: row_count} ORDER BY approved_at DESC)
    FROM (SELECT scenario_id, any_value(scenario_name) AS scenario_name, max(approved_at) AS approved_at, any_value(approved_by) AS approved_by,
                 sum(revenue) AS revenue, sum(sold_units)::BIGINT AS sold_units, count(*)::INT AS row_count
          FROM ${T("approved_plan")} GROUP BY scenario_id ORDER BY approved_at DESC LIMIT 5)), [])
})::VARCHAR AS j`;
};

type Waiter = { resolve: (run: any) => void };

export type FlightBackendOptions = {
  /** Schema holding scenarios, overrides, activity_log, approved_plan and rpc_results. */
  schema?: string;
  /** scenario_overrides has a `metric` column ('supply' | 'demand': the supply planner's grid rows); read it too. */
  overrideMetric?: boolean;
  /** The write Flight's id. */
  flightId: string;
};

export function useFlightBackend(enabled: boolean, opts: FlightBackendOptions): Backend | null {
  const schema = opts.schema ?? "main";
  const overrideMetric = !!opts.overrideMetric;
  const flightId = opts.flightId;
  const sql = useImperativeSql();
  const nonce = useRef(0);
  // Every statement gets a unique comment: an identical statement may be
  // answered from the runtime's cache, and a read after a write must not be.
  const read = useCallback((text: string) => sql(`/* r${++nonce.current} */ ${text}`), [sql]);

  // Read next to a MotherDuck table so MotherDuck answers it, not the local DuckDB-Wasm
  // (md_user_info() is disabled in a Dive's SaaS mode).
  const me = useSQLQuery(`SELECT current_user() AS u FROM ${T("main", "regions")} LIMIT 1`, { enabled });
  const actor = Array.isArray(me.data) && me.data[0] ? String(me.data[0].u) : "";

  const runner = useRunFlight(flightId);
  const [runNumber, setRunNumber] = useState<number | undefined>(undefined);
  const waiter = useRef<Waiter | null>(null);
  const latest = useFlightRun(flightId, runNumber, {
    onFinished: (run: any) => {
      const w = waiter.current;
      waiter.current = null;
      w?.resolve(run);
    },
  });
  // A status read that fails ends the wait instead of hanging the save queue.
  useEffect(() => {
    if (latest.error && waiter.current) {
      const w = waiter.current;
      waiter.current = null;
      w.resolve({ status: "UNKNOWN", error: latest.error });
    }
  }, [latest.error]);

  const runnerRef = useRef(runner);
  runnerRef.current = runner;
  const actorRef = useRef(actor);
  actorRef.current = actor;
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const refreshedOnLoad = useRef(false);

  const write = useCallback(async (op: string, args: Record<string, unknown>): Promise<RpcResult> => {
    const requestId = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    let started: any;
    try {
      started = await runnerRef.current.runAsync({ OP: op, ARGS: JSON.stringify(args), ACTOR: actorRef.current, REQUEST_ID: requestId });
    } catch (e: any) {
      const msg = String(e?.message ?? e);
      return { ok: false, status: 403, error: /own|not found|access/i.test(msg) ? `You don't have access to run this Dive's write Flight, so changes can't be saved. Ask the Dive's owner. (${msg})` : `Could not start the save: ${msg}` };
    }
    const run = await new Promise<any>((resolve) => {
      const timer = setTimeout(() => {
        if (waiter.current) waiter.current = null;
        resolve({ status: "TIMEOUT" });
      }, RUN_TIMEOUT_MS);
      waiter.current = { resolve: (r) => { clearTimeout(timer); resolve(r); } };
      setRunNumber(Number(started?.runNumber));
    });
    if (run?.status !== "SUCCEEDED") {
      return { ok: false, status: 0, error: `The save didn't finish (run #${started?.runNumber}: ${String(run?.status ?? "unknown").toLowerCase()}).` };
    }
    // The Flight snapshotted the database; pull that snapshot into the share, then read the reply.
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        await read(`REFRESH DATABASE "${DB}"`);
        const rows = await read(`SELECT ok, status, error, result FROM ${T(schema, "rpc_results")} WHERE request_id = ${quote(requestId)}`);
        const r = rows[0];
        if (r) {
          return r.ok ? { ok: true, result: JSON.parse(String(r.result)) } : { ok: false, status: Number(r.status) || 0, error: String(r.error || "Request failed") };
        }
      } catch (e: any) {
        if (attempt === 3) return { ok: false, status: 0, error: `Saved, but could not read the result: ${String(e?.message ?? e)}` };
      }
      await new Promise((res) => setTimeout(res, 700 * (attempt + 1)));
    }
    return { ok: false, status: 0, error: "Saved, but the result has not reached this Dive yet. Refresh to see it." };
  }, [read, schema]);

  const call = useCallback(<T = any,>(op: string, args: Record<string, unknown> = {}): Promise<RpcResult<T>> => {
    const task = async (): Promise<RpcResult> => {
      if (op === "bootstrap") {
        try {
          // Once per page load: pull the share's latest snapshot first, so the
          // page shows changes made since the share last updated itself (other
          // viewers' saves, the owner's edits) instead of up to a minute late.
          if (!refreshedOnLoad.current) {
            refreshedOnLoad.current = true;
            try {
              await read(`REFRESH DATABASE "${DB}"`);
            } catch {
              /* fall back to the share's current snapshot */
            }
          }
          const rows = await read(bootstrapSql(schema, overrideMetric));
          const j = JSON.parse(String(rows[0]?.j ?? "{}"));
          for (const s of j.scenarios ?? []) {
            s.drivers = typeof s.drivers === "string" ? JSON.parse(s.drivers || "{}") : s.drivers ?? {};
            s.overrides = (s.overrides ?? []).map((o: any) => ({ ...o, week_idx: Number(o.week_idx), units: Number(o.units) }));
          }
          return { ok: true, result: j };
        } catch (e: any) {
          return { ok: false, status: 0, error: String(e?.message ?? e) };
        }
      }
      return write(op, args);
    };
    const p = queue.current.then(task, task);
    queue.current = p.catch(() => undefined);
    return p as Promise<RpcResult<T>>;
  }, [read, write, schema, overrideMetric]);

  return useMemo<Backend | null>(() => (enabled && actor
    ? { mode: "host", actor, call }
    : null), [enabled, actor, call]);
}
