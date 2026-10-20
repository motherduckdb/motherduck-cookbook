import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useDiveState, useSQLQuery } from "@motherduck/react-sql-query";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

// The deploy script replaces the placeholder with the URL of the share it
// creates for the metrics database, so every member of the organization can
// open the Dive. Keep the alias: every query below is qualified with it.
export const REQUIRED_DATABASES = [
  { type: "share", path: "__MD_OBSERVABILITY_SHARE_URL__", alias: "md_observability" },
];

const T = '"md_observability"."main"';
// The Query log tab reads MotherDuck's live query history with the same
// filters, for exact percentiles and error messages. It runs as the viewer,
// who needs the "view query history" permission (Admin and Builder preset
// roles); Explorers get a note instead of a failed query.
const QH = "md_information_schema.query_history";

// ---- Time scope: a preset range or a custom UTC window ---------------------------

type RangeKey = "1h" | "6h" | "24h" | "7d" | "30d";
const PRESETS: Record<RangeKey, { span: string; hours: number }> = {
  "1h": { span: "1 hour", hours: 1 },
  "6h": { span: "6 hours", hours: 6 },
  "24h": { span: "24 hours", hours: 24 },
  "7d": { span: "7 days", hours: 168 },
  "30d": { span: "30 days", hours: 720 },
};
type Scope = { label: string; from: string; to: string; bucket: string; fmt: string; custom: boolean };
type Custom = { from: string; to: string };
// datetime-local values; validated before they are interpolated into SQL.
const DT_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/;

function bucketFor(hours: number): { bucket: string; fmt: string } {
  if (hours <= 2) return { bucket: "1 minute", fmt: "%H:%M" };
  if (hours <= 12) return { bucket: "5 minutes", fmt: "%H:%M" };
  if (hours <= 48) return { bucket: "15 minutes", fmt: "%H:%M" };
  if (hours <= 24 * 14) return { bucket: "1 hour", fmt: "%m-%d %H:%M" };
  return { bucket: "6 hours", fmt: "%m-%d" };
}

function makeScope(range: RangeKey, custom: Custom): Scope {
  if (DT_RE.test(custom.from) && DT_RE.test(custom.to) && custom.from < custom.to) {
    const hours = (Date.parse(`${custom.to}:00Z`) - Date.parse(`${custom.from}:00Z`)) / 3.6e6;
    return {
      label: `${custom.from.replace("T", " ")} to ${custom.to.replace("T", " ")} UTC`,
      from: `TIMESTAMPTZ '${custom.from}:00+00'`, to: `TIMESTAMPTZ '${custom.to}:00+00'`,
      ...bucketFor(hours), custom: true,
    };
  }
  const p = PRESETS[range] ?? PRESETS["6h"];
  return { label: `last ${range}`, from: `now() - INTERVAL '${p.span}'`, to: "now()", ...bucketFor(p.hours), custom: false };
}
const within = (s: Scope, col: string) => `${col} >= ${s.from} AND ${col} < ${s.to}`;

// ---- Filters -------------------------------------------------------------------------

type Filters = { user: string; client: string; instance: string; qtype: string; status: string; error: string; version: string; search: string; query: string };
const NO_FILTERS: Filters = { user: "", client: "", instance: "", qtype: "", status: "", error: "", version: "", search: "", query: "" };
const esc = (v: string) => v.replace(/'/g, "''");
const like = (v: string) => `'%${esc(v).replace(/%/g, "\\%").replace(/_/g, "\\_")}%' ESCAPE '\\'`;

// Conditions on the columns of query_detail_minutes (and slow_queries).
function detailWhere(f: Filters): string {
  const c: string[] = [];
  if (f.user) c.push(`user_name = '${esc(f.user)}'`);
  if (f.client) c.push(`client = '${esc(f.client)}'`);
  if (f.instance) c.push(`instance_type = '${esc(f.instance)}'`);
  if (f.qtype) c.push(`query_type = '${esc(f.qtype)}'`);
  if (f.status) c.push(`status = '${esc(f.status)}'`);
  if (f.error) c.push(`error_type = '${esc(f.error)}'`);
  if (f.version) c.push(`duckdb_version = '${esc(f.version)}'`);
  return c.join(" AND ");
}
const queryFiltered = (f: Filters) => !!(f.user || f.client || f.instance || f.qtype || f.status || f.error || f.version || f.query);
// The query id filter (set by the links in Slack alerts) only applies where rows carry a query id.
const queryIdWhere = (f: Filters) => (f.query ? `query_id::VARCHAR = '${esc(f.query)}'` : "");
const andWhere = (...parts: string[]) => parts.filter(Boolean).join(" AND ");

// The same client classification the collector applies to user_agent, for the live query log.
const CLIENT_SQL = `CASE
  WHEN user_agent LIKE '%md-flights(%' THEN 'flight' WHEN user_agent LIKE '%md-dives/%' THEN 'dive'
  WHEN user_agent LIKE '%mcp-server-motherduck%' THEN 'mcp' WHEN user_agent LIKE '%motherduck-wasm%' THEN 'ui'
  WHEN user_agent LIKE '% dbt/%' OR user_agent LIKE '%dbt-duckdb%' THEN 'dbt' WHEN user_agent LIKE '%dlt/%' THEN 'dlt'
  WHEN user_agent LIKE '%airflow%' THEN 'airflow' WHEN user_agent LIKE '%pgendpoint%' THEN 'pg_endpoint'
  WHEN user_agent LIKE '%node-neo-api%' OR user_agent LIKE '%nodejs%' THEN 'node' WHEN user_agent LIKE '%jdbc%' THEN 'jdbc'
  WHEN user_agent LIKE '% python/%' THEN 'python' WHEN user_agent LIKE '% go%' THEN 'go' ELSE 'other' END`;

// Conditions on the live query_history columns.
function liveWhere(f: Filters): string {
  const c: string[] = [];
  if (f.user) c.push(`user_name = '${esc(f.user)}'`);
  if (f.client) c.push(`(${CLIENT_SQL}) = '${esc(f.client)}'`);
  if (f.instance) c.push(`coalesce(instance_type, 'unknown') = '${esc(f.instance)}'`);
  if (f.qtype) c.push(`coalesce(query_type, 'UNKNOWN') = '${esc(f.qtype)}'`);
  if (f.status === "ok") c.push("error_type IS NULL");
  if (f.status === "error") c.push("error_type IS NOT NULL");
  if (f.error) c.push(`error_type = '${esc(f.error)}'`);
  if (f.version) c.push(`coalesce(duckdb_version, 'unknown') = '${esc(f.version)}'`);
  if (f.search) c.push(`query_text ILIKE ${like(f.search)}`);
  if (f.query) c.push(queryIdWhere(f));
  return c.join(" AND ");
}

// ---- SQL generators -------------------------------------------------------------------

const spine = (s: Scope) =>
  `SELECT time_bucket(INTERVAL '${s.bucket}', ts) AS b
   FROM generate_series(time_bucket(INTERVAL '${s.bucket}', ${s.from}), ${s.to}, INTERVAL '${s.bucket}') AS g(ts)`;

// One row per bucket with the aggregate columns in `cols`; gaps are kept as NULL.
const seriesSql = (s: Scope, table: string, time: string, cols: string, where = "") =>
  `WITH spine AS (${spine(s)}),
   d AS (SELECT time_bucket(INTERVAL '${s.bucket}', ${time}) AS b, ${cols}
         FROM ${T}."${table}" WHERE ${andWhere(within(s, time), where)} GROUP BY 1)
   SELECT strftime(spine.b, '${s.fmt}') AS t, d.* EXCLUDE (b) FROM spine LEFT JOIN d USING (b) ORDER BY spine.b`;

// One row per (bucket, key) for the `top` keys by total; missing cells are 0.
const keyedSql = (s: Scope, table: string, time: string, key: string, value: string, where = "", top = 8) =>
  `WITH spine AS (${spine(s)}),
   d AS (SELECT time_bucket(INTERVAL '${s.bucket}', ${time}) AS b, coalesce(${key}, 'unknown') AS k, ${value} AS v
         FROM ${T}."${table}" WHERE ${andWhere(within(s, time), where)} GROUP BY 1, 2),
   keys AS (SELECT k FROM d GROUP BY 1 ORDER BY sum(v) DESC LIMIT ${top})
   SELECT strftime(spine.b, '${s.fmt}') AS t, keys.k AS k, coalesce(d.v, 0) AS v
   FROM spine CROSS JOIN keys LEFT JOIN d ON d.b = spine.b AND d.k = keys.k ORDER BY spine.b, keys.k`;

// ---- Formatting --------------------------------------------------------------------------

const N = (v: unknown): number => (v == null ? 0 : Number(v));
const NN = (v: unknown): number | null => (v == null ? null : Number(v));
const fmtNum = (v: number | null): string => {
  if (v == null || !isFinite(v)) return "–";
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(0)}K`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
};
const fmtBytes = (v: number | null): string => {
  if (v == null || !isFinite(v)) return "–";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let i = 0; let x = v;
  while (Math.abs(x) >= 1024 && i < units.length - 1) { x /= 1024; i++; }
  return `${x.toFixed(i >= 3 ? 2 : 0)} ${units[i]}`;
};
const fmtMs = (v: number | null): string => {
  if (v == null || !isFinite(v)) return "–";
  if (v < 1000) return `${v.toFixed(0)} ms`;
  if (v < 60_000) return `${(v / 1000).toFixed(1)} s`;
  return `${(v / 60_000).toFixed(1)} min`;
};
const fmtPct = (v: number | null): string => (v == null || !isFinite(v) ? "–" : `${v.toFixed(2)}%`);
const fmtSec = (v: number | null): string => (v == null ? "–" : fmtMs(v * 1000));

const PALETTE = ["#0777b3", "#bd4e35", "#2d7a00", "#e18727", "#638cad", "#8e5ea2", "#17a2b8", "#adadad"];
const INK = "#231f20";
const MUTED = "#6a6a6a";
const STATUS_COLOR: Record<string, string> = { ok: "#2d7a00", warning: "#e18727", critical: "#bc1200", error: "#bc1200" };

// ---- Building blocks ---------------------------------------------------------------------

type Row = Record<string, unknown>;
const rowsOf = (q: { data?: unknown }): Row[] => (Array.isArray(q.data) ? (q.data as Row[]) : []);

function Section({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="mb-6">
      <h2 className="text-sm font-semibold" style={{ color: INK }}>{title}</h2>
      {note && <p className="text-xs mb-1" style={{ color: MUTED }}>{note}</p>}
      <div className="mt-1">{children}</div>
    </section>
  );
}

function Status({ q, height, children }: { q: { isLoading: boolean; isError: boolean; error: unknown }; height: number; children: ReactNode }) {
  if (q.isLoading) return <div className="animate-pulse rounded bg-gray-200" style={{ height }} />;
  if (q.isError) return <p className="text-xs" role="alert" style={{ color: "#bc1200" }}>{String(q.error)}</p>;
  return <>{children}</>;
}

function Kpis({ sql, items }: { sql: string; items: { key: string; label: string; fmt: (v: number | null) => string }[] }) {
  const q = useSQLQuery(sql);
  const row = rowsOf(q)[0] ?? {};
  const cols = Math.min(items.length, 6);
  return (
    <div className={`grid gap-6 mb-6 ${cols >= 6 ? "grid-cols-6" : cols === 5 ? "grid-cols-5" : cols === 4 ? "grid-cols-4" : "grid-cols-3"}`}>
      {items.map((it) => (
        <div key={it.key}>
          {q.isLoading ? (
            <div className="h-9 w-20 animate-pulse rounded bg-gray-200" />
          ) : (
            <p className="text-3xl font-bold" style={{ color: INK }}>{q.isError ? "!" : it.fmt(NN(row[it.key]))}</p>
          )}
          <p className="text-xs mt-1" style={{ color: MUTED }}>{it.label}</p>
        </div>
      ))}
    </div>
  );
}

type LineSpec = { key: string; label: string; fmt?: (v: number | null) => string; axis?: "left" | "right" };

// Multi-column time series (one row per bucket) as lines, or stacked areas.
function Lines({ sql, lines, stacked = false, height = 220 }: { sql: string; lines: LineSpec[]; stacked?: boolean; height?: number }) {
  const q = useSQLQuery(sql);
  const data = rowsOf(q).map((r) => {
    const o: Record<string, unknown> = { t: String(r.t) };
    for (const l of lines) o[l.key] = stacked ? N(r[l.key]) : NN(r[l.key]);
    return o;
  });
  // A series with only a few snapshots (a fresh deployment) has no line segments to draw; show its points.
  const sparse = data.filter((o) => lines.some((l) => o[l.key] != null)).length <= 3;
  const fmt = lines[0]?.fmt ?? fmtNum;
  const rightFmt = lines.find((l) => l.axis === "right")?.fmt ?? fmtNum;
  const hasRight = lines.some((l) => l.axis === "right");
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
      <XAxis dataKey="t" fontSize={10} minTickGap={24} />
      <YAxis yAxisId="left" fontSize={10} width={52} tickFormatter={(v) => fmt(Number(v))} />
      {hasRight && <YAxis yAxisId="right" orientation="right" fontSize={10} width={52} tickFormatter={(v) => rightFmt(Number(v))} />}
      <Tooltip formatter={(v: unknown, name: unknown) => {
        const l = lines.find((x) => x.label === name);
        return [(l?.fmt ?? fmt)(NN(v)), String(name)];
      }} />
      {lines.length > 1 && <Legend wrapperStyle={{ fontSize: 11 }} />}
    </>
  );
  return (
    <Status q={q} height={height}>
      <ResponsiveContainer width="100%" height={height}>
        {stacked ? (
          <AreaChart data={data}>
            {axes}
            {lines.map((l, i) => (
              <Area key={l.key} yAxisId="left" type="linear" dataKey={l.key} name={l.label} stackId="s"
                stroke={PALETTE[i % PALETTE.length]} fill={PALETTE[i % PALETTE.length]} fillOpacity={0.6} />
            ))}
          </AreaChart>
        ) : (
          <LineChart data={data}>
            {axes}
            {lines.map((l, i) => (
              <Line key={l.key} yAxisId={l.axis ?? "left"} type="linear" dataKey={l.key} name={l.label}
                stroke={PALETTE[i % PALETTE.length]} strokeWidth={2} dot={sparse} connectNulls={false} />
            ))}
          </LineChart>
        )}
      </ResponsiveContainer>
    </Status>
  );
}

// Long (t, k, v) rows pivoted into one stacked bar (or area) per key.
function Stacked({ sql, fmt = fmtNum, area = false, height = 220 }: { sql: string; fmt?: (v: number | null) => string; area?: boolean; height?: number }) {
  const q = useSQLQuery(sql);
  const rows = rowsOf(q);
  const keys: string[] = [];
  const byT = new Map<string, Record<string, unknown>>();
  for (const r of rows) {
    const t = String(r.t); const k = String(r.k);
    if (!keys.includes(k)) keys.push(k);
    if (!byT.has(t)) byT.set(t, { t });
    byT.get(t)![k] = N(r.v);
  }
  const data = Array.from(byT.values());
  const axes = (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke="#eee" />
      <XAxis dataKey="t" fontSize={10} minTickGap={24} />
      <YAxis fontSize={10} width={52} tickFormatter={(v) => fmt(Number(v))} />
      <Tooltip formatter={(v: unknown) => fmt(NN(v))} />
      <Legend wrapperStyle={{ fontSize: 11 }} />
    </>
  );
  return (
    <Status q={q} height={height}>
      {data.length === 0 ? (
        <p className="text-xs" style={{ color: MUTED }}>No data for this scope.</p>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          {area ? (
            <AreaChart data={data}>
              {axes}
              {keys.map((k, i) => (
                <Area key={k} type="linear" dataKey={k} stackId="s" stroke={PALETTE[i % PALETTE.length]}
                  fill={PALETTE[i % PALETTE.length]} fillOpacity={0.6} />
              ))}
            </AreaChart>
          ) : (
            <BarChart data={data}>
              {axes}
              {keys.map((k, i) => (
                <Bar key={k} dataKey={k} stackId="s" fill={PALETTE[i % PALETTE.length]} />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      )}
    </Status>
  );
}

type Col = { key: string; label: string; fmt?: (v: unknown) => string; align?: "left" | "right"; mono?: boolean; wide?: boolean };
const num = (f: (v: number | null) => string) => (v: unknown) => f(NN(v));
const text = (v: unknown) => (v == null ? "–" : String(v));

function Table({ sql, columns, empty = "Nothing in this scope." }: { sql: string; columns: Col[]; empty?: string }) {
  const q = useSQLQuery(sql);
  const rows = rowsOf(q);
  return (
    <Status q={q} height={120}>
      {rows.length === 0 ? (
        <p className="text-xs" style={{ color: MUTED }}>{empty}</p>
      ) : (
        <table className="w-full text-xs" style={{ color: INK }}>
          <thead>
            <tr style={{ color: MUTED }}>
              {columns.map((c) => (
                <th key={c.key} className={`py-1 pr-3 font-medium ${c.align === "right" ? "text-right" : "text-left"}`}>{c.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} style={{ borderTop: "1px solid #eee" }}>
                {columns.map((c) => (
                  <td key={c.key} className={`py-1 pr-3 align-top ${c.align === "right" ? "text-right" : "text-left"} ${c.mono ? "font-mono" : ""}`}
                    style={c.wide ? { maxWidth: 520, whiteSpace: "pre-wrap", wordBreak: "break-word" }
                      : { maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                    title={text(r[c.key])}>
                    {(c.fmt ?? text)(r[c.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Status>
  );
}

function StatusPill({ status }: { status: string }) {
  return (
    <span className="inline-block rounded px-2 py-0.5 text-xs font-semibold text-white"
      style={{ background: STATUS_COLOR[status] ?? MUTED }}>{status}</span>
  );
}

const Two = ({ children }: { children: ReactNode }) => <div className="grid grid-cols-2 gap-6">{children}</div>;
const orgWide = (f: Filters, what: string) => (queryFiltered(f) ? `${what} are org-wide: the stored percentiles cannot be filtered. See the Query log tab for exact filtered percentiles.` : undefined);

type P = { s: Scope; f: Filters };

// ---- Tabs ----------------------------------------------------------------------------------

function Overview({ s, f }: P) {
  const dw = detailWhere(f);
  const alerts = useSQLQuery(`SELECT check_name, group_key, status, value, strftime(since, '%m-%d %H:%M') AS since
                               FROM ${T}."alert_state" WHERE status <> 'ok' ORDER BY status, check_name`);
  const firing = rowsOf(alerts);
  return (
    <>
      <Kpis
        sql={`WITH q AS (SELECT sum(queries) AS queries,
                                100.0 * sum(queries) FILTER (WHERE status = 'error') / nullif(sum(queries), 0) AS error_rate,
                                max(max_ms) AS max_ms, count(DISTINCT user_name) AS users
                         FROM ${T}."query_detail_minutes" WHERE ${andWhere(within(s, "minute"), dw)}),
                   l AS (SELECT avg(p95_ms) AS p95 FROM ${T}."query_latency_minutes" WHERE ${within(s, "minute")}),
                   rq AS (SELECT running FROM ${T}."running_queries" ORDER BY captured_at DESC LIMIT 1),
                   st AS (SELECT active_bytes FROM ${T}."storage_totals" ORDER BY captured_at DESC LIMIT 1)
              SELECT q.queries, q.error_rate, q.max_ms, q.users, l.p95, rq.running, st.active_bytes
              FROM q LEFT JOIN l ON true LEFT JOIN rq ON true LEFT JOIN st ON true`}
        items={[
          { key: "queries", label: "Queries", fmt: fmtNum },
          { key: "error_rate", label: "Error rate", fmt: fmtPct },
          { key: "max_ms", label: "Slowest query", fmt: fmtMs },
          { key: "users", label: "Distinct users", fmt: fmtNum },
          { key: "p95", label: "p95 latency (org-wide avg)", fmt: fmtMs },
          { key: "running", label: "Running now (org-wide)", fmt: fmtNum },
        ]}
      />
      <Section title="Alerts firing now" note="From the alert rules Flight; see the Alerts tab for history.">
        <Status q={alerts} height={24}>
          {firing.length === 0 ? (
            <p className="text-xs" style={{ color: STATUS_COLOR.ok }}>All rules are ok.</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {firing.map((a, i) => (
                <span key={i} className="text-xs rounded px-2 py-1" style={{ background: "#fff", border: "1px solid #ddd" }}>
                  <StatusPill status={String(a.status)} /> {String(a.check_name)}
                  {String(a.group_key) !== "all" && <span style={{ color: MUTED }}> [{String(a.group_key)}]</span>}
                  <span style={{ color: MUTED }}> since {String(a.since)}</span>
                </span>
              ))}
            </div>
          )}
        </Status>
      </Section>
      <Two>
        <Section title="Queries per bucket by status">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "status", "sum(queries)", dw)} />
        </Section>
        <Section title="Latency percentiles" note={orgWide(f, "Percentiles") ?? "Per-minute percentiles averaged per bucket."}>
          <Lines sql={seriesSql(s, "query_latency_minutes", "minute", "avg(p50_ms) AS p50, avg(p95_ms) AS p95, avg(p99_ms) AS p99")}
            lines={[{ key: "p50", label: "p50", fmt: fmtMs }, { key: "p95", label: "p95", fmt: fmtMs }, { key: "p99", label: "p99", fmt: fmtMs }]} />
        </Section>
      </Two>
      <Two>
        <Section title="Queries per bucket by client">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "client", "sum(queries)", dw)} />
        </Section>
        <Section title="Running queries and age of the oldest" note="Org-wide snapshots.">
          <Lines sql={seriesSql(s, "running_queries", "captured_at", "max(running) AS running, max(oldest_age_sec) / 60 AS oldest_min")}
            lines={[{ key: "running", label: "running" }, { key: "oldest_min", label: "oldest (min)", axis: "right", fmt: (v) => (v == null ? "–" : `${v.toFixed(0)} min`) }]} />
        </Section>
      </Two>
    </>
  );
}

function Queries({ s, f }: P) {
  const dw = detailWhere(f);
  const slowWhere = andWhere(within(s, "window_end"), dw, f.search ? `query_text ILIKE ${like(f.search)}` : "", queryIdWhere(f));
  return (
    <>
      <Two>
        <Section title="Queries per bucket by type">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "query_type", "sum(queries)", dw)} />
        </Section>
        <Section title="Queries per bucket by instance type">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "instance_type", "sum(queries)", dw)} />
        </Section>
      </Two>
      <Two>
        <Section title="Latency percentiles" note={orgWide(f, "Percentiles")}>
          <Lines sql={seriesSql(s, "query_latency_minutes", "minute", "avg(p50_ms) AS p50, avg(p95_ms) AS p95, avg(p99_ms) AS p99")}
            lines={[{ key: "p50", label: "p50", fmt: fmtMs }, { key: "p95", label: "p95", fmt: fmtMs }, { key: "p99", label: "p99", fmt: fmtMs }]} />
        </Section>
        <Section title="Slowest and average successful query per bucket" note="Filtered. Average is weighted by queries.">
          <Lines sql={seriesSql(s, "query_detail_minutes", "minute",
            "max(max_ms) AS slowest, sum(avg_ms * queries) / nullif(sum(queries), 0) AS average", andWhere("status = 'ok'", dw))}
            lines={[{ key: "slowest", label: "slowest", fmt: fmtMs }, { key: "average", label: "average", fmt: fmtMs }]} />
        </Section>
      </Two>
      <Two>
        <Section title="Slowest successful query per bucket by instance type">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "instance_type", "max(max_ms)", andWhere("status = 'ok'", dw))} fmt={fmtMs} area />
        </Section>
        <Section title="Queue wait: longest per bucket" note="Filtered maximum of the time a query spent queued before execution.">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "instance_type", "max(max_wait_ms)", dw)} fmt={fmtMs} area />
        </Section>
      </Two>
      <Two>
        <Section title="Top clients" note="Derived from user_agent.">
          <Table sql={`SELECT client, sum(queries) AS queries, sum(queries) FILTER (WHERE status = 'error') AS errors, max(max_ms) AS slowest
                       FROM ${T}."query_detail_minutes" WHERE ${andWhere(within(s, "minute"), dw)} GROUP BY 1 ORDER BY 2 DESC LIMIT 8`}
            columns={[{ key: "client", label: "Client" }, { key: "queries", label: "Queries", fmt: num(fmtNum), align: "right" },
              { key: "errors", label: "Errors", fmt: num(fmtNum), align: "right" }, { key: "slowest", label: "Slowest", fmt: num(fmtMs), align: "right" }]} />
        </Section>
        <Section title="Top DuckDB client versions">
          <Table sql={`SELECT duckdb_version, sum(queries) AS queries, count(DISTINCT user_name) AS users, sum(queries) FILTER (WHERE status = 'error') AS errors
                       FROM ${T}."query_detail_minutes" WHERE ${andWhere(within(s, "minute"), dw)} GROUP BY 1 ORDER BY 2 DESC LIMIT 8`}
            columns={[{ key: "duckdb_version", label: "Version" }, { key: "queries", label: "Queries", fmt: num(fmtNum), align: "right" },
              { key: "users", label: "Users", fmt: num(fmtNum), align: "right" }, { key: "errors", label: "Errors", fmt: num(fmtNum), align: "right" }]} />
        </Section>
      </Two>
      <Section title="Slowest queries" note="The slowest queries of each collection window, text truncated. The search box filters the text.">
        <Table sql={`SELECT strftime(end_time, '%m-%d %H:%M') AS at, user_name, client, instance_type, query_type, total_ms, wait_ms, spilled_bytes, error_type, query_text
                     FROM ${T}."slow_queries" WHERE ${slowWhere} ORDER BY total_ms DESC LIMIT 10`}
          columns={[{ key: "at", label: "Finished" }, { key: "user_name", label: "User" }, { key: "client", label: "Client" },
            { key: "instance_type", label: "Instance" }, { key: "query_type", label: "Type" },
            { key: "total_ms", label: "Elapsed", fmt: num(fmtMs), align: "right" }, { key: "wait_ms", label: "Wait", fmt: num(fmtMs), align: "right" },
            { key: "spilled_bytes", label: "Spilled", fmt: num(fmtBytes), align: "right" }, { key: "error_type", label: "Error" },
            { key: "query_text", label: "Query", mono: true }]} />
      </Section>
    </>
  );
}

function Errors({ s, f }: P) {
  const dw = detailWhere(f);
  const w = andWhere(within(s, "minute"), dw);
  return (
    <>
      <Two>
        <Section title="Errors per bucket by error type">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "error_type", "sum(queries)", andWhere("status = 'error'", dw))} />
        </Section>
        <Section title="Error rate">
          <Lines sql={seriesSql(s, "query_detail_minutes", "minute", "100.0 * sum(queries) FILTER (WHERE status = 'error') / nullif(sum(queries), 0) AS rate", dw)}
            lines={[{ key: "rate", label: "error rate", fmt: fmtPct }]} />
        </Section>
      </Two>
      <Two>
        <Section title="Top error types">
          <Table sql={`SELECT error_type, sum(queries) AS errors, count(DISTINCT user_name) AS users FROM ${T}."query_detail_minutes"
                       WHERE ${andWhere(w, "status = 'error'")} GROUP BY 1 ORDER BY 2 DESC LIMIT 8`}
            columns={[{ key: "error_type", label: "Error type" }, { key: "errors", label: "Errors", fmt: num(fmtNum), align: "right" },
              { key: "users", label: "Users", fmt: num(fmtNum), align: "right" }]} />
        </Section>
        <Section title="Users with most errors">
          <Table sql={`SELECT user_name, sum(queries) FILTER (WHERE status = 'error') AS errors, sum(queries) AS queries,
                              100.0 * sum(queries) FILTER (WHERE status = 'error') / sum(queries) AS rate
                       FROM ${T}."query_detail_minutes" WHERE ${w} GROUP BY 1 HAVING errors > 0 ORDER BY 2 DESC LIMIT 8`}
            columns={[{ key: "user_name", label: "User" }, { key: "errors", label: "Errors", fmt: num(fmtNum), align: "right" },
              { key: "queries", label: "Queries", fmt: num(fmtNum), align: "right" }, { key: "rate", label: "Rate", fmt: num(fmtPct), align: "right" }]} />
        </Section>
      </Two>
      <Two>
        <Section title="Errors by client">
          <Table sql={`SELECT client, sum(queries) FILTER (WHERE status = 'error') AS errors, sum(queries) AS queries
                       FROM ${T}."query_detail_minutes" WHERE ${w} GROUP BY 1 HAVING errors > 0 ORDER BY 2 DESC LIMIT 8`}
            columns={[{ key: "client", label: "Client" }, { key: "errors", label: "Errors", fmt: num(fmtNum), align: "right" },
              { key: "queries", label: "Queries", fmt: num(fmtNum), align: "right" }]} />
        </Section>
        <Section title="Errors by DuckDB version">
          <Table sql={`SELECT duckdb_version, sum(queries) FILTER (WHERE status = 'error') AS errors, sum(queries) AS queries
                       FROM ${T}."query_detail_minutes" WHERE ${w} GROUP BY 1 HAVING errors > 0 ORDER BY 2 DESC LIMIT 8`}
            columns={[{ key: "duckdb_version", label: "Version" }, { key: "errors", label: "Errors", fmt: num(fmtNum), align: "right" },
              { key: "queries", label: "Queries", fmt: num(fmtNum), align: "right" }]} />
        </Section>
      </Two>
    </>
  );
}

function Users({ s, f }: P) {
  const dw = detailWhere(f);
  const w = andWhere(within(s, "minute"), dw);
  const userTable = (metric: string, label: string, fmt: (v: number | null) => string, having = "") =>
    <Table sql={`SELECT user_name, ${metric} AS v FROM ${T}."query_detail_minutes" WHERE ${w} GROUP BY 1 ${having} ORDER BY 2 DESC LIMIT 8`}
      columns={[{ key: "user_name", label: "User" }, { key: "v", label, fmt: num(fmt), align: "right" }]} />;
  return (
    <>
      <div className="grid grid-cols-3 gap-6">
        <Section title="Top users by queries">{userTable("sum(queries)", "Queries", fmtNum)}</Section>
        <Section title="Top users by slowest query">{userTable("max(max_ms)", "Slowest", fmtMs)}</Section>
        <Section title="Top users by bytes spilled">{userTable("sum(spilled_bytes)", "Spilled", fmtBytes, "HAVING sum(spilled_bytes) > 0")}</Section>
      </div>
      <Two>
        <Section title="Queries per bucket by user (busiest)">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "user_name", "sum(queries)", dw)} />
        </Section>
        <Section title="Queries per bucket by DuckDB version">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "duckdb_version", "sum(queries)", dw)} area />
        </Section>
      </Two>
      <Two>
        <Section title="Bytes spilled to disk per bucket by instance type">
          <Stacked sql={keyedSql(s, "query_detail_minutes", "minute", "instance_type", "sum(spilled_bytes)", dw)} fmt={fmtBytes} />
        </Section>
        <Section title="Bytes uploaded and downloaded per bucket">
          <Lines sql={seriesSql(s, "query_detail_minutes", "minute", "sum(bytes_uploaded) AS up, sum(bytes_downloaded) AS down", dw)}
            lines={[{ key: "up", label: "uploaded", fmt: fmtBytes }, { key: "down", label: "downloaded", fmt: fmtBytes }]} />
        </Section>
      </Two>
      <Two>
        <Section title="Active users and ducklings per window" note="Org-wide snapshots.">
          <Lines sql={seriesSql(s, "activity_snapshots", "captured_at", "max(active_users) AS users, max(active_ducklings) AS ducklings")}
            lines={[{ key: "users", label: "users" }, { key: "ducklings", label: "ducklings" }]} />
        </Section>
        <Section title="Active ducklings by instance type" note="Org-wide snapshots.">
          <Stacked sql={keyedSql(s, "duckling_snapshots", "captured_at", "instance_type", "max(ducklings)")} area />
        </Section>
      </Two>
    </>
  );
}

function QueryLog({ s, f }: P) {
  const [sort, setSort] = useDiveState<"newest" | "slowest" | "spilled" | "waited">("logsort", "newest");
  const access = useSQLQuery(`SELECT 1 FROM ${QH} LIMIT 1`);
  const w = andWhere(within(s, "end_time"), liveWhere(f));
  const order = { newest: "end_time DESC", slowest: "total_elapsed_time DESC", spilled: "bytes_spilled_to_disk DESC NULLS LAST", waited: "wait_time DESC" }[sort];
  if (access.isLoading) return <div className="animate-pulse rounded bg-gray-200" style={{ height: 120 }} />;
  if (access.isError) {
    return (
      <p className="text-xs" role="alert" style={{ color: MUTED }}>
        The Query log reads <code>md_information_schema.query_history</code> live as you, which needs the &quot;view query history&quot;
        permission (the Admin and Builder preset roles). Every other tab reads the collected tables through the share.
        <br />
        <span style={{ color: "#bc1200" }}>{String(access.error)}</span>
      </p>
    );
  }
  return (
    <>
      <p className="text-xs mb-3" style={{ color: MUTED }}>
        Live from <code>md_information_schema.query_history</code> with the filters above, not from the collected tables.
        Exact percentiles for the filtered set, error messages, and the most recent queries. Long windows on a busy org take a few seconds.
      </p>
      <Kpis
        sql={`SELECT count(*) AS queries, count(*) FILTER (WHERE error_type IS NOT NULL) AS errors,
                     quantile_cont(epoch_ms(total_elapsed_time), 0.5) AS p50, quantile_cont(epoch_ms(total_elapsed_time), 0.95) AS p95,
                     quantile_cont(epoch_ms(total_elapsed_time), 0.99) AS p99, sum(bytes_spilled_to_disk) AS spilled
              FROM ${QH} WHERE ${w}`}
        items={[
          { key: "queries", label: "Queries", fmt: fmtNum }, { key: "errors", label: "Errors", fmt: fmtNum },
          { key: "p50", label: "p50", fmt: fmtMs }, { key: "p95", label: "p95", fmt: fmtMs }, { key: "p99", label: "p99", fmt: fmtMs },
          { key: "spilled", label: "Spilled to disk", fmt: fmtBytes },
        ]}
      />
      <Section title="Error messages" note="Grouped by error type and the first 120 characters of the message.">
        <Table sql={`SELECT error_type, left(regexp_replace(error_message, '\\s+', ' ', 'g'), 120) AS message, count(*) AS n,
                            count(DISTINCT user_name) AS users, strftime(max(end_time), '%m-%d %H:%M') AS last_seen
                     FROM ${QH} WHERE ${andWhere(w, "error_type IS NOT NULL")} GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10`}
          empty="No errors in this scope."
          columns={[{ key: "error_type", label: "Type" }, { key: "message", label: "Message", mono: true, wide: true },
            { key: "n", label: "Count", fmt: num(fmtNum), align: "right" }, { key: "users", label: "Users", fmt: num(fmtNum), align: "right" },
            { key: "last_seen", label: "Last seen" }]} />
      </Section>
      <Section title="Queries">
        <div className="flex gap-1 mb-2">
          {(["newest", "slowest", "spilled", "waited"] as const).map((k) => (
            <button key={k} onClick={() => setSort(k)} className="text-xs rounded px-2 py-1"
              style={{ background: k === sort ? INK : "#fff", color: k === sort ? "#fff" : INK, border: "1px solid #ddd" }}>{k}</button>
          ))}
        </div>
        <Table sql={`SELECT strftime(end_time, '%m-%d %H:%M:%S') AS at, user_name, duckling_id, ${CLIENT_SQL} AS client, duckdb_version, instance_type, query_type,
                            epoch_ms(total_elapsed_time) AS total_ms, epoch_ms(wait_time) AS wait_ms, bytes_spilled_to_disk AS spilled,
                            error_type, left(regexp_replace(query_text, '\\s+', ' ', 'g'), 400) AS query_text, query_id::VARCHAR AS query_id
                     FROM ${QH} WHERE ${w} ORDER BY ${order} LIMIT 50`}
          columns={[{ key: "at", label: "Finished" }, { key: "user_name", label: "User" }, { key: "duckling_id", label: "Duckling" },
            { key: "client", label: "Client" },
            { key: "duckdb_version", label: "Version" }, { key: "instance_type", label: "Instance" }, { key: "query_type", label: "Type" },
            { key: "total_ms", label: "Elapsed", fmt: num(fmtMs), align: "right" }, { key: "wait_ms", label: "Wait", fmt: num(fmtMs), align: "right" },
            { key: "spilled", label: "Spilled", fmt: num(fmtBytes), align: "right" }, { key: "error_type", label: "Error" },
            { key: "query_text", label: "Query", mono: true, wide: true }, { key: "query_id", label: "Query id", mono: true }]} />
      </Section>
    </>
  );
}

function Dives({ s, f }: P) {
  const nameWhere = f.search ? `dive_title ILIKE ${like(f.search)}` : "";
  const w = andWhere(within(s, "minute"), nameWhere);
  return (
    <>
      <Kpis
        sql={`WITH a AS (SELECT dives_active, dive_viewers FROM ${T}."activity_snapshots" ORDER BY captured_at DESC LIMIT 1),
                   d AS (SELECT total, created_7d FROM ${T}."catalog_snapshots" WHERE entity = 'dive' ORDER BY captured_at DESC LIMIT 1),
                   g AS (SELECT total, updated_7d FROM ${T}."catalog_snapshots" WHERE entity = 'guide' ORDER BY captured_at DESC LIMIT 1)
              SELECT d.total AS dives, a.dives_active, a.dive_viewers, d.created_7d, g.total AS guides, g.updated_7d
              FROM (SELECT 1) x LEFT JOIN a ON true LEFT JOIN d ON true LEFT JOIN g ON true`}
        items={[
          { key: "dives", label: "Dives", fmt: fmtNum },
          { key: "dives_active", label: "Dives queried (last window)", fmt: fmtNum },
          { key: "dive_viewers", label: "Dive viewers (last window)", fmt: fmtNum },
          { key: "created_7d", label: "Dives created, 7d", fmt: fmtNum },
          { key: "guides", label: "Guides", fmt: fmtNum },
          { key: "updated_7d", label: "Guides updated, 7d", fmt: fmtNum },
        ]}
      />
      <Two>
        <Section title="Dive queries per bucket (busiest Dives)" note="The search box filters Dive titles.">
          <Stacked sql={keyedSql(s, "dive_minutes", "minute", "dive_title", "sum(queries)", nameWhere, 6)} />
        </Section>
        <Section title="Top Dives by queries">
          <Table sql={`SELECT dive_title, sum(queries) AS queries, max(viewers) AS peak_viewers, sum(errors) AS errors, max(max_ms) AS slowest
                       FROM ${T}."dive_minutes" WHERE ${w} GROUP BY 1 ORDER BY 2 DESC LIMIT 8`}
            columns={[{ key: "dive_title", label: "Dive" }, { key: "queries", label: "Queries", fmt: num(fmtNum), align: "right" },
              { key: "peak_viewers", label: "Peak viewers/min", fmt: num(fmtNum), align: "right" },
              { key: "errors", label: "Errors", fmt: num(fmtNum), align: "right" }, { key: "slowest", label: "Slowest", fmt: num(fmtMs), align: "right" }]} />
        </Section>
      </Two>
      <Two>
        <Section title="Dives by status" note="Org-wide snapshots.">
          <Stacked sql={keyedSql(s, "inventory_counts", "captured_at", "label", "max(count)", "entity = 'dive' AND dimension = 'status'")} area />
        </Section>
        <Section title="Active users: 24h / 7d / 30d" note="Org-wide snapshots.">
          <Lines sql={seriesSql(s, "activity_snapshots", "captured_at", "max(users_24h) AS d1, max(users_7d) AS d7, max(users_30d) AS d30")}
            lines={[{ key: "d1", label: "24h" }, { key: "d7", label: "7d" }, { key: "d30", label: "30d" }]} />
        </Section>
      </Two>
      <Two>
        <Section title="Guides by topic" note="Latest snapshot.">
          <Table sql={`SELECT label AS topic, count FROM ${T}."inventory_counts"
                       WHERE entity = 'guide' AND dimension = 'topic' AND captured_at = (SELECT max(captured_at) FROM ${T}."inventory_counts")
                       ORDER BY count DESC LIMIT 8`}
            columns={[{ key: "topic", label: "Topic" }, { key: "count", label: "Guides", fmt: num(fmtNum), align: "right" }]} />
        </Section>
        <Section title="Guides by access" note="Latest snapshot.">
          <Table sql={`SELECT label AS access, count FROM ${T}."inventory_counts"
                       WHERE entity = 'guide' AND dimension = 'access' AND captured_at = (SELECT max(captured_at) FROM ${T}."inventory_counts")
                       ORDER BY count DESC`}
            columns={[{ key: "access", label: "Access" }, { key: "count", label: "Guides", fmt: num(fmtNum), align: "right" }]} />
        </Section>
      </Two>
    </>
  );
}

function Storage({ s, f }: P) {
  const nameWhere = f.search ? `(database_name ILIKE ${like(f.search)} OR owner ILIKE ${like(f.search)})` : "";
  const latest = `captured_at = (SELECT max(captured_at) FROM ${T}."storage_snapshots")`;
  return (
    <>
      <Kpis
        sql={`SELECT databases, owners, active_bytes, total_bytes FROM ${T}."storage_totals" ORDER BY captured_at DESC LIMIT 1`}
        items={[
          { key: "databases", label: "Databases with storage", fmt: fmtNum },
          { key: "owners", label: "Database owners", fmt: fmtNum },
          { key: "active_bytes", label: "Active bytes", fmt: fmtBytes },
          { key: "total_bytes", label: "All bytes (incl. history, clones, failsafe)", fmt: fmtBytes },
        ]}
      />
      <Two>
        <Section title="Total storage by kind" note="Org-wide.">
          <Lines stacked sql={seriesSql(s, "storage_totals", "captured_at",
            "max(active_bytes) AS active, max(historical_bytes) AS historical, max(retained_for_clone_bytes) AS clone, max(failsafe_bytes) AS failsafe")}
            lines={[{ key: "active", label: "active", fmt: fmtBytes }, { key: "historical", label: "historical", fmt: fmtBytes },
              { key: "clone", label: "retained for clone", fmt: fmtBytes }, { key: "failsafe", label: "failsafe", fmt: fmtBytes }]} />
        </Section>
        <Section title="Active bytes per database (largest)" note="The search box filters database and owner names.">
          <Stacked sql={keyedSql(s, "storage_snapshots", "captured_at", "database_name", "max(active_bytes)", nameWhere, 8)} fmt={fmtBytes} area />
        </Section>
      </Two>
      <Section title="Largest databases" note="Latest snapshot; needs the Admin role on the collector's token. The search box filters database and owner names.">
        <Table sql={`SELECT database_name, owner, transient, active_bytes, historical_bytes,
                            active_bytes + historical_bytes + retained_for_clone_bytes + failsafe_bytes AS total_bytes
                     FROM ${T}."storage_snapshots" WHERE ${andWhere(latest, nameWhere)} ORDER BY active_bytes DESC LIMIT 15`}
          empty="No storage snapshot for this filter. storage_info requires an organization admin token."
          columns={[{ key: "database_name", label: "Database" }, { key: "owner", label: "Owner" },
            { key: "transient", label: "Transient", fmt: (v) => (v ? "yes" : "no") },
            { key: "active_bytes", label: "Active", fmt: num(fmtBytes), align: "right" },
            { key: "historical_bytes", label: "Historical", fmt: num(fmtBytes), align: "right" },
            { key: "total_bytes", label: "Total", fmt: num(fmtBytes), align: "right" }]} />
      </Section>
    </>
  );
}

function Flights({ s, f }: P) {
  const nameWhere = f.search ? `flight_name ILIKE ${like(f.search)}` : "";
  const w = andWhere(within(s, "ended_at"), nameWhere);
  return (
    <>
      <Kpis
        sql={`WITH fl AS (SELECT sum(in_flight) AS in_flight, count(*) AS tracked FROM ${T}."flight_run_snapshots"
                          WHERE captured_at = (SELECT max(captured_at) FROM ${T}."flight_run_snapshots")),
                   c AS (SELECT sum(count) AS flights FROM ${T}."inventory_counts"
                         WHERE entity = 'flight' AND dimension = 'status' AND captured_at = (SELECT max(captured_at) FROM ${T}."inventory_counts")),
                   ru AS (SELECT count(*) AS runs, count(*) FILTER (WHERE status = 'failed') AS failed FROM ${T}."flight_runs" WHERE ${w})
              SELECT c.flights, fl.tracked, fl.in_flight, ru.runs, ru.failed FROM (SELECT 1) x LEFT JOIN c ON true LEFT JOIN fl ON true LEFT JOIN ru ON true`}
        items={[
          { key: "flights", label: "Flights visible", fmt: fmtNum },
          { key: "tracked", label: "Flights with run metrics", fmt: fmtNum },
          { key: "in_flight", label: "Runs in flight now", fmt: fmtNum },
          { key: "runs", label: "Runs finished", fmt: fmtNum },
          { key: "failed", label: "Failed runs", fmt: fmtNum },
        ]}
      />
      <Two>
        <Section title="Flight runs by status" note="The search box filters Flight names.">
          <Stacked sql={keyedSql(s, "flight_runs", "ended_at", "status", "count(*)", nameWhere)} />
        </Section>
        <Section title="Longest run per bucket by Flight">
          <Stacked sql={keyedSql(s, "flight_runs", "ended_at", "flight_name", "max(duration_sec)", nameWhere, 6)} fmt={fmtSec} area />
        </Section>
      </Two>
      <Two>
        <Section title="Failed runs by Flight">
          <Table sql={`SELECT flight_name, count(*) AS failed, strftime(max(ended_at), '%m-%d %H:%M') AS last_failure
                       FROM ${T}."flight_runs" WHERE ${andWhere(w, "status = 'failed'")} GROUP BY 1 ORDER BY 2 DESC LIMIT 8`}
            empty="No failed runs in this scope."
            columns={[{ key: "flight_name", label: "Flight" }, { key: "failed", label: "Failed", fmt: num(fmtNum), align: "right" },
              { key: "last_failure", label: "Last failure" }]} />
        </Section>
        <Section title="Recent runs">
          <Table sql={`SELECT flight_name, status, strftime(ended_at, '%m-%d %H:%M') AS ended, duration_sec, is_scheduled
                       FROM ${T}."flight_runs" WHERE ${w} ORDER BY ended_at DESC LIMIT 10`}
            columns={[{ key: "flight_name", label: "Flight" }, { key: "status", label: "Status" }, { key: "ended", label: "Ended" },
              { key: "duration_sec", label: "Duration", fmt: num(fmtSec), align: "right" },
              { key: "is_scheduled", label: "Scheduled", fmt: (v) => (v ? "yes" : "no") }]} />
        </Section>
      </Two>
    </>
  );
}

function Inventory({ s }: P) {
  const latest = `captured_at = (SELECT max(captured_at) FROM ${T}."inventory_counts")`;
  return (
    <>
      <Kpis
        sql={`SELECT sum(count) FILTER (WHERE entity = 'database' AND dimension = 'type') AS databases,
                     sum(count) FILTER (WHERE entity = 'access_token' AND dimension = 'token_type') AS tokens,
                     sum(count) FILTER (WHERE entity = 'access_token' AND dimension = 'expiring_7d') AS expiring,
                     sum(count) FILTER (WHERE entity = 'dive' AND dimension = 'status') AS dives,
                     sum(count) FILTER (WHERE entity = 'guide' AND dimension = 'access') AS guides,
                     sum(count) FILTER (WHERE entity = 'role' AND dimension = 'role_type') AS roles
              FROM ${T}."inventory_counts" WHERE ${latest}`}
        items={[
          { key: "databases", label: "Databases", fmt: fmtNum },
          { key: "tokens", label: "Access tokens (collector's)", fmt: fmtNum },
          { key: "expiring", label: "Tokens expiring < 7d", fmt: fmtNum },
          { key: "dives", label: "Dives", fmt: fmtNum },
          { key: "guides", label: "Guides", fmt: fmtNum },
          { key: "roles", label: "Roles", fmt: fmtNum },
        ]}
      />
      <div className="grid grid-cols-3 gap-6">
        <Section title="Databases by type">
          <Stacked sql={keyedSql(s, "inventory_counts", "captured_at", "label", "max(count)", "entity = 'database' AND dimension = 'type'")} area height={180} />
        </Section>
        <Section title="Access tokens by type">
          <Stacked sql={keyedSql(s, "inventory_counts", "captured_at", "label", "max(count)", "entity = 'access_token' AND dimension = 'token_type'")} area height={180} />
        </Section>
        <Section title="Flights by status">
          <Stacked sql={keyedSql(s, "inventory_counts", "captured_at", "label", "max(count)", "entity = 'flight' AND dimension = 'status'")} area height={180} />
        </Section>
      </div>
      <Section title="Latest inventory counts">
        <Table sql={`SELECT entity, dimension, label, count FROM ${T}."inventory_counts" WHERE ${latest} ORDER BY entity, dimension, count DESC`}
          columns={[{ key: "entity", label: "Entity" }, { key: "dimension", label: "Dimension" }, { key: "label", label: "Value" },
            { key: "count", label: "Count", fmt: num(fmtNum), align: "right" }]} />
      </Section>
    </>
  );
}

function Alerts({ s }: P) {
  return (
    <>
      <Section title="Current state of every rule" note="One row per rule and group; written by the alert rules Flight on every run.">
        <Table sql={`SELECT check_name, group_key, status, value, strftime(since, '%m-%d %H:%M') AS since,
                            strftime(last_notified_at, '%m-%d %H:%M') AS notified, strftime(updated_at, '%m-%d %H:%M') AS evaluated
                     FROM ${T}."alert_state" ORDER BY CASE status WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, check_name, group_key`}
          empty="No evaluations yet. Deploy and run the alerts Flight."
          columns={[{ key: "check_name", label: "Rule" }, { key: "group_key", label: "Group" },
            { key: "status", label: "Status", fmt: (v) => String(v) },
            { key: "value", label: "Value", fmt: num(fmtNum), align: "right" }, { key: "since", label: "Since" },
            { key: "notified", label: "Last notified" }, { key: "evaluated", label: "Evaluated" }]} />
      </Section>
      <Section title="Alert events" note="Transitions and re-notifications in the selected time scope; `notified` means a Slack message was posted.">
        <Table sql={`SELECT strftime(evaluated_at, '%m-%d %H:%M') AS at, check_name, group_key, old_status || ' → ' || new_status AS transition,
                            kind, value, unit, notified
                     FROM ${T}."alert_events" WHERE ${within(s, "evaluated_at")} ORDER BY evaluated_at DESC LIMIT 20`}
          empty="No transitions in this scope."
          columns={[{ key: "at", label: "At" }, { key: "check_name", label: "Rule" }, { key: "group_key", label: "Group" },
            { key: "transition", label: "Transition" }, { key: "kind", label: "Kind" },
            { key: "value", label: "Value", fmt: num(fmtNum), align: "right" }, { key: "unit", label: "Unit" },
            { key: "notified", label: "Notified", fmt: (v) => (v ? "yes" : "no") }]} />
      </Section>
    </>
  );
}

function Health({ s }: P) {
  return (
    <>
      <Kpis
        sql={`SELECT date_diff('minute', run_at, now()) AS age_min, duration_sec, rows_written, collectors_failed
              FROM ${T}."exporter_runs" ORDER BY run_at DESC LIMIT 1`}
        items={[
          { key: "age_min", label: "Minutes since last collection", fmt: (v) => (v == null ? "–" : `${v.toFixed(0)}`) },
          { key: "duration_sec", label: "Last run duration", fmt: fmtSec },
          { key: "rows_written", label: "Rows written last run", fmt: fmtNum },
          { key: "collectors_failed", label: "Collectors failed last run", fmt: fmtNum },
        ]}
      />
      <Two>
        <Section title="Collector run duration and rows written">
          <Lines sql={seriesSql(s, "exporter_runs", "run_at", "max(duration_sec) AS duration, sum(rows_written) AS rows")}
            lines={[{ key: "duration", label: "duration", fmt: fmtSec }, { key: "rows", label: "rows written", axis: "right" }]} />
        </Section>
        <Section title="Collector status">
          <Table sql={`SELECT collector, count(*) AS runs, count(*) FILTER (WHERE ok) AS ok_runs, avg(duration_sec) AS avg_sec,
                              strftime(max(run_at), '%m-%d %H:%M') AS last_run, max(error) FILTER (WHERE NOT ok) AS last_error
                       FROM ${T}."collector_runs" WHERE ${within(s, "run_at")} GROUP BY 1 ORDER BY 1`}
            columns={[{ key: "collector", label: "Collector" }, { key: "runs", label: "Runs", fmt: num(fmtNum), align: "right" },
              { key: "ok_runs", label: "OK", fmt: num(fmtNum), align: "right" }, { key: "avg_sec", label: "Avg", fmt: num(fmtSec), align: "right" },
              { key: "last_run", label: "Last run" }, { key: "last_error", label: "Last error", mono: true }]} />
        </Section>
      </Two>
      <Section title="Failed collector runs">
        <Table sql={`SELECT strftime(run_at, '%m-%d %H:%M') AS at, collector, duration_sec, error FROM ${T}."collector_runs"
                     WHERE NOT ok AND ${within(s, "run_at")} ORDER BY run_at DESC LIMIT 10`}
          empty="No collector failures in this scope."
          columns={[{ key: "at", label: "At" }, { key: "collector", label: "Collector" },
            { key: "duration_sec", label: "Duration", fmt: num(fmtSec), align: "right" }, { key: "error", label: "Error", mono: true, wide: true }]} />
      </Section>
    </>
  );
}

// ---- Filter bar --------------------------------------------------------------------------------

// A text input that commits on Enter or blur, so typing does not fire a query per keystroke.
function TextFilter({ value, onCommit, placeholder, list, width = 170 }: { value: string; onCommit: (v: string) => void; placeholder: string; list?: string; width?: number }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => { setDraft(value); }, [value]);
  return (
    <input value={draft} list={list} placeholder={placeholder} className="text-xs rounded px-2 py-1"
      style={{ border: "1px solid #ddd", background: "#fff", width }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft !== value && onCommit(draft.trim())}
      onKeyDown={(e) => { if (e.key === "Enter") onCommit(draft.trim()); }} />
  );
}

function Select({ value, onChange, options, placeholder, width = 130 }: { value: string; onChange: (v: string) => void; options: string[]; placeholder: string; width?: number }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} className="text-xs rounded px-2 py-1"
      style={{ border: "1px solid #ddd", background: "#fff", width, color: value ? INK : MUTED }}>
      <option value="">{placeholder}</option>
      {!options.includes(value) && value && <option value={value}>{value}</option>}
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function FilterBar({ s, f, setF, custom, setCustom, range, setRange }: {
  s: Scope; f: Filters; setF: (f: Filters) => void; custom: Custom; setCustom: (c: Custom) => void; range: RangeKey; setRange: (r: RangeKey) => void;
}) {
  // One query fills every dropdown with the values seen in the current scope.
  const opts = useSQLQuery(`
    WITH d AS (SELECT user_name, client, instance_type, query_type, error_type, duckdb_version, queries
               FROM ${T}."query_detail_minutes" WHERE ${within(s, "minute")})
    SELECT 'user' AS dim, user_name AS v, sum(queries) AS n FROM d GROUP BY 2
    UNION ALL SELECT 'client', client, sum(queries) FROM d GROUP BY 2
    UNION ALL SELECT 'instance', instance_type, sum(queries) FROM d GROUP BY 2
    UNION ALL SELECT 'qtype', query_type, sum(queries) FROM d GROUP BY 2
    UNION ALL SELECT 'error', error_type, sum(queries) FROM d WHERE error_type IS NOT NULL GROUP BY 2
    UNION ALL SELECT 'version', duckdb_version, sum(queries) FROM d GROUP BY 2
    ORDER BY 1, 3 DESC`);
  const by = (dim: string) => rowsOf(opts).filter((r) => r.dim === dim).map((r) => String(r.v));
  const set = (k: keyof Filters) => (v: string) => setF({ ...f, [k]: v });
  const active = Object.entries(f).filter(([, v]) => v);
  const [draft, setDraft] = useState<Custom>(custom);
  useEffect(() => { setDraft(custom); }, [custom]);
  const applyCustom = () => setCustom({ from: draft.from, to: draft.to });

  return (
    <div className="mb-4 rounded p-3" style={{ background: "#fff", border: "1px solid #e5e5e5" }}>
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-xs font-semibold" style={{ color: MUTED }}>Time</span>
        {(Object.keys(PRESETS) as RangeKey[]).map((k) => (
          <button key={k} onClick={() => { setRange(k); setCustom({ from: "", to: "" }); }} className="text-xs rounded px-2 py-1"
            style={{ background: !s.custom && k === range ? INK : "#fff", color: !s.custom && k === range ? "#fff" : INK, border: "1px solid #ddd" }}>
            {k}
          </button>
        ))}
        <span className="text-xs" style={{ color: MUTED }}>or UTC window</span>
        <input type="datetime-local" value={draft.from} onChange={(e) => setDraft({ ...draft, from: e.target.value })}
          className="text-xs rounded px-2 py-1" style={{ border: "1px solid #ddd" }} />
        <span className="text-xs" style={{ color: MUTED }}>to</span>
        <input type="datetime-local" value={draft.to} onChange={(e) => setDraft({ ...draft, to: e.target.value })}
          className="text-xs rounded px-2 py-1" style={{ border: "1px solid #ddd" }} />
        <button onClick={applyCustom} className="text-xs rounded px-2 py-1"
          style={{ background: s.custom ? INK : "#fff", color: s.custom ? "#fff" : INK, border: "1px solid #ddd" }}>apply</button>
        {custom.from && custom.to && !s.custom && <span className="text-xs" style={{ color: STATUS_COLOR.critical }}>invalid window (needs from &lt; to)</span>}
        <span className="text-xs ml-auto" style={{ color: MUTED }}>scope: {s.label}, {s.bucket} buckets</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs font-semibold" style={{ color: MUTED }}>Filters</span>
        <TextFilter value={f.user} onCommit={set("user")} placeholder="user or service account" list="md-obs-users" />
        <datalist id="md-obs-users">{by("user").map((u) => <option key={u} value={u} />)}</datalist>
        <Select value={f.client} onChange={set("client")} options={by("client")} placeholder="client" width={110} />
        <Select value={f.instance} onChange={set("instance")} options={by("instance")} placeholder="instance type" />
        <Select value={f.qtype} onChange={set("qtype")} options={by("qtype")} placeholder="query type" width={110} />
        <Select value={f.status} onChange={set("status")} options={["ok", "error"]} placeholder="status" width={90} />
        <Select value={f.error} onChange={set("error")} options={by("error")} placeholder="error type" />
        <Select value={f.version} onChange={set("version")} options={by("version")} placeholder="DuckDB version" width={140} />
        <TextFilter value={f.search} onCommit={set("search")} placeholder="search: query text, database, Dive, Flight" width={240} />
        <TextFilter value={f.query} onCommit={set("query")} placeholder="query id (Query log, Slowest queries)" width={230} />
        {active.length > 0 && (
          <button onClick={() => setF(NO_FILTERS)} className="text-xs rounded px-2 py-1" style={{ border: "1px solid #ddd", background: "#fff" }}>
            clear {active.length}
          </button>
        )}
      </div>
    </div>
  );
}

// ---- Page ------------------------------------------------------------------------------------------

const TABS = [
  ["overview", "Overview", Overview],
  ["queries", "Queries", Queries],
  ["errors", "Errors", Errors],
  ["users", "Users & compute", Users],
  ["log", "Query log", QueryLog],
  ["dives", "Dives & Guides", Dives],
  ["storage", "Storage", Storage],
  ["flights", "Flights", Flights],
  ["inventory", "Inventory", Inventory],
  ["alerts", "Alerts", Alerts],
  ["health", "Collector health", Health],
] as const;
type TabKey = (typeof TABS)[number][0];

export default function MotherDuckObservability() {
  const [tab, setTab] = useDiveState<TabKey>("tab", "overview");
  const [range, setRange] = useDiveState<RangeKey>("range", "6h");
  const [custom, setCustom] = useDiveState<Custom>("window", { from: "", to: "" });
  const [filters, setFilters] = useDiveState<Filters>("filters", NO_FILTERS);
  const f: Filters = { ...NO_FILTERS, ...(filters ?? {}) };
  const s = makeScope(range, custom ?? { from: "", to: "" });
  const fresh = useSQLQuery(`SELECT strftime(max(run_at), '%Y-%m-%d %H:%M') AS last_run, date_diff('minute', max(run_at), now()) AS age
                             FROM ${T}."exporter_runs"`);
  const freshRow = rowsOf(fresh)[0];
  const age = NN(freshRow?.age);
  const Active = (TABS.find((t) => t[0] === tab) ?? TABS[0])[2];

  return (
    <div className="p-6" style={{ background: "#f8f8f8", color: INK, minHeight: "100%" }}>
      <div className="flex items-baseline justify-between gap-4 mb-3">
        <div>
          <h1 className="text-2xl font-semibold">MotherDuck Observability</h1>
          <p className="text-sm" style={{ color: MUTED }}>
            Times in UTC.{" "}
            {freshRow?.last_run
              ? <>Last collection {String(freshRow.last_run)} ({age == null ? "" : `${age.toFixed(0)} min ago`})</>
              : "No collector run recorded yet."}
            {age != null && age > 15 && <span style={{ color: STATUS_COLOR.critical }}> · collector may be stale</span>}
          </p>
        </div>
      </div>
      <FilterBar s={s} f={f} setF={setFilters} custom={custom ?? { from: "", to: "" }} setCustom={setCustom} range={range} setRange={setRange} />
      <div className="flex flex-wrap gap-1 mb-5" style={{ borderBottom: "1px solid #ddd" }}>
        {TABS.map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)} className="text-sm px-3 py-2"
            style={{ color: key === tab ? "#0777b3" : MUTED, borderBottom: key === tab ? "2px solid #0777b3" : "2px solid transparent", fontWeight: key === tab ? 600 : 400 }}>
            {label}
          </button>
        ))}
      </div>
      <Active s={s} f={f} />
    </div>
  );
}
