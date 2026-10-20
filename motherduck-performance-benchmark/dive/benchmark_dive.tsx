// MotherDuck query benchmarking: results from md_bench.py, stored in the results database (--results-db).
// Styled with the MotherDuck docs design system (motherduck.com/docs/internal).
import { useSQLQuery, useDiveState } from "@motherduck/react-sql-query";
import { useEffect, useMemo, useState, Fragment } from "react";
import type { ReactNode } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from "recharts";

export const REQUIRED_DATABASES = [
  { type: "database", path: "md:md_benchmarks", alias: "md_benchmarks" },
];

// ── Design tokens (motherduck-docs src/css/custom.css) ──
const MD = {
  white: "#FFFFFF",
  snow: "#F8F8F7",
  darkSnow: "#ECE5DD",
  sand: "#F4EFEA",
  sandGrey: "#ECE6DF",
  darkSand: "#E1D6CB",
  lighterGrey: "#D7D7D7",
  lightGrey: "#C0C0C0",
  grey: "#A1A1A1",
  darkGrey: "#818181",
  black: "#383838",
  textSecondary: "#4B5563",
  lightSky: "#97D4FF",
  sky: "#6FC2FF",
  darkSky: "#2BA5FF",
  lightSun: "#F9EE3E",
  sun: "#FFDE00",
  lightGarden: "#50C7B7",
  garden: "#16AA98",
  darkGarden: "#068475",
  lightWatermelon: "#FF9A94",
  watermelon: "#FF7169",
  darkWatermelon: "#E23F35",
} as const;
const FONT_BODY = "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
const FONT_MONO = "'Aeonik Mono', 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace";
const SHADOW = "0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)";

// Diagram actor palette, assigned in order (ScalingPatternsDiagram).
const ACTORS = ["#16AA98", "#6FC2FF", "#B07CC6", "#E5C700", "#FF9538", "#FF7169"];
const replicaColor = (r: number | null) => (r == null ? MD.lightGrey : ACTORS[r % ACTORS.length]);

const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap');
@font-face {
  font-family: 'Aeonik Mono';
  src: url('https://motherduck.com/fonts/AeonikMono/AeonikMono-Regular.woff2') format('woff2'),
       url('https://motherduck.com/fonts/AeonikMono/AeonikMono-Regular.woff') format('woff');
  font-style: normal; font-weight: 400; font-display: swap;
}

:root {
  --S: 0.25rem;
  --space-1: var(--S); --space-2: calc(2 * var(--S)); --space-3: calc(3 * var(--S));
  --space-4: calc(4 * var(--S)); --space-5: calc(5 * var(--S)); --space-6: calc(6 * var(--S));
  --space-7: calc(7 * var(--S)); --space-8: calc(8 * var(--S)); --space-10: calc(10 * var(--S));
  --space-12: calc(12 * var(--S));
  --shadow: ${SHADOW};
}

* { box-sizing: border-box; }
html, body, #root { background: ${MD.sand}; color: ${MD.black}; }

.page { font-family: ${FONT_BODY}; font-size: 16px; line-height: 1.5; font-weight: 400; color: ${MD.black};
  background: ${MD.sand}; min-height: 100vh; padding: var(--space-8) var(--space-10) var(--space-12);
  display: flex; flex-direction: column; gap: var(--space-10); }

h1.title { font-family: ${FONT_BODY}; font-weight: 700; font-size: 2rem; line-height: 1.25; margin: 0; }
h2.section { font-family: ${FONT_BODY}; font-weight: 600; font-size: 1.5rem; line-height: 1.33; margin: 0; }
h3.card-title { font-family: ${FONT_BODY}; font-weight: 600; font-size: 1.25rem; line-height: 1.4; margin: 0; }
p { margin: 0; }
.lede { max-width: 72ch; }
.secondary { color: ${MD.textSecondary}; font-size: 14px; }
.mono { font-family: ${FONT_MONO}; font-size: 14px; }
.label { font-family: ${FONT_MONO}; font-size: 14px; text-transform: uppercase; letter-spacing: 0.02em; }
.num { font-variant-numeric: tabular-nums; }
code.inline { font-family: ${FONT_MONO}; font-size: 0.875em; background: ${MD.white}; border: 2px solid ${MD.lighterGrey};
  border-radius: 2px; padding: 0 var(--space-1); }

.header { display: flex; justify-content: space-between; align-items: flex-end; gap: var(--space-6); flex-wrap: wrap; }
.stack-2 { display: flex; flex-direction: column; gap: var(--space-2); }
.stack-4 { display: flex; flex-direction: column; gap: var(--space-4); }
.section-head { display: flex; justify-content: space-between; align-items: flex-end; gap: var(--space-4); flex-wrap: wrap; }

.select { font-family: ${FONT_BODY}; font-size: 14px; color: ${MD.black}; background: ${MD.white};
  border: 2px solid ${MD.black}; border-radius: 2px; padding: var(--space-2) var(--space-3); min-width: 320px; cursor: pointer; }
.select:focus-visible, .btn:focus-visible, .chips button:focus-visible, tr.clickable:focus-visible {
  outline: 2px solid ${MD.darkSky}; outline-offset: 2px; }

/* Card: raised surface, 2px border, 2px radius, soft elevation */
.card { background: ${MD.white}; border: 2px solid ${MD.black}; border-radius: 2px; box-shadow: var(--shadow); min-width: 0; }
.card-head { display: flex; justify-content: space-between; align-items: baseline; gap: var(--space-3);
  padding: var(--space-4) var(--space-5); border-bottom: 2px solid ${MD.black}; flex-wrap: wrap; }
.card-body { padding: var(--space-5); }
.card-chart { padding: var(--space-4) var(--space-4) var(--space-2) 0; }
.grid-2 { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr); gap: var(--space-6); align-items: start; }

/* KPI tiles */
.kpis { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: var(--space-5); }
.kpi { border: 2px solid ${MD.black}; border-radius: 2px; box-shadow: var(--shadow); padding: var(--space-5);
  display: flex; flex-direction: column; gap: var(--space-2); min-width: 0; background: ${MD.white}; }
.kpi-value { font-family: ${FONT_BODY}; font-weight: 600; font-size: 2.5rem; line-height: 1.1; font-variant-numeric: tabular-nums; }
.kpi-sub { font-size: 14px; }

/* Nav chips: segmented control */
.chips { display: inline-flex; flex-wrap: wrap; border: 2px solid ${MD.black}; border-radius: 2px; background: ${MD.white}; }
.chips button { font-family: ${FONT_BODY}; font-size: 14px; letter-spacing: 0.02em; text-transform: uppercase; color: ${MD.black};
  background: ${MD.white}; border: 0; padding: var(--space-2) var(--space-3); cursor: pointer; }
.chips button + button { border-left: 2px solid ${MD.black}; }
.chips button[aria-checked="true"] { background: ${MD.sky}; font-weight: 600; }

/* Button with the pop interaction */
.btn { font-family: ${FONT_BODY}; font-size: 14px; letter-spacing: 0.02em; text-transform: uppercase; color: ${MD.black};
  background: ${MD.sky}; border: 2px solid ${MD.black}; border-radius: 2px; padding: var(--space-2) var(--space-4); cursor: pointer;
  transition: transform 120ms ease-in-out, box-shadow 120ms ease-in-out; }
.btn--secondary { background: ${MD.white}; }
.btn:hover { transform: translate(7px, -7px); box-shadow: -7px 7px 0 0 ${MD.black}; }
.btn:active { transform: none; box-shadow: none; transition: transform 30ms ease-in-out, box-shadow 30ms ease-in-out; }
.btn--reverse:hover { transform: translate(-7px, -7px); box-shadow: 7px 7px 0 0 ${MD.black}; }
.btn:disabled, .btn:disabled:hover { opacity: 0.4; cursor: not-allowed; transform: none; box-shadow: none; }
.btn[aria-current="page"] { background: ${MD.sky}; font-weight: 600; }
.pager { display: flex; justify-content: space-between; align-items: center; gap: var(--space-4); flex-wrap: wrap;
  padding: var(--space-3) var(--space-5); border-top: 2px solid ${MD.black}; background: ${MD.sand}; }
.pager-pages { display: flex; gap: var(--space-2); align-items: center; flex-wrap: wrap; }
@media (prefers-reduced-motion: reduce) {
  .btn, .btn:hover, .btn:active { transform: none; transition: none; }
  .btn:hover { box-shadow: -7px 7px 0 0 ${MD.black}; }
  .skel { animation: none !important; }
}

/* Tables: docs prose-table treatment */
.tbl-wrap { overflow-x: auto; }
.tbl { width: 100%; background: ${MD.sand}; border-collapse: collapse; font-size: 14px; line-height: 1.5; }
.tbl th, .tbl td { padding: var(--space-3) var(--space-4); text-align: left; vertical-align: top;
  border: 0; border-right: 2px solid ${MD.black}; border-bottom: 2px solid ${MD.black}; }
.tbl th:last-child, .tbl td:last-child { border-right: 0; }
.tbl tbody tr:last-child td { border-bottom: 0; }
.tbl thead tr { background: ${MD.black}; }
.tbl thead th { color: ${MD.snow}; font-family: ${FONT_MONO}; font-size: 11px; font-weight: 700; letter-spacing: 0.06em;
  text-transform: uppercase; white-space: nowrap; border-right-color: color-mix(in srgb, ${MD.black} 65%, ${MD.snow});
  position: sticky; top: 0; background: ${MD.black}; z-index: 1; }
.tbl thead th.sortable { cursor: pointer; }
.tbl thead th[aria-sort="ascending"], .tbl thead th[aria-sort="descending"] { color: ${MD.sky}; }
.tbl tbody tr { background: ${MD.sand}; transition: background 80ms ease-in-out; }
.tbl tbody tr:nth-child(even) { background: ${MD.snow}; }
.tbl tbody tr:hover, .tbl tbody tr.selected { background: color-mix(in srgb, ${MD.sky} 35%, ${MD.snow}); }
.tbl tbody tr.clickable { cursor: pointer; }
.tbl td.r, .tbl th.r { text-align: right; }
.tbl td.r, .tbl td.mono { font-family: ${FONT_MONO}; font-size: 14px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.tbl tfoot td { border-top: 2px solid ${MD.black}; border-bottom: 0; background: ${MD.darkSnow}; font-weight: 600; }
.tbl tr.detail, .tbl tr.detail:hover { background: ${MD.snow}; cursor: default; }
.tbl tr.detail td { max-width: 0; padding: 0; }
.slide { display: grid; grid-template-rows: 1fr; animation: slide-open 200ms ease-out; }
.slide.closing { animation: slide-close 160ms ease-in forwards; }
.slide > div { overflow: hidden; min-height: 0; }
.detail-inner { padding: var(--space-4); display: flex; flex-direction: column; gap: var(--space-2); }
@keyframes slide-open { from { grid-template-rows: 0fr; opacity: 0; } to { grid-template-rows: 1fr; opacity: 1; } }
@keyframes slide-close { from { grid-template-rows: 1fr; opacity: 1; } to { grid-template-rows: 0fr; opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .slide, .slide.closing { animation: none; } }
.scroll { max-height: 560px; overflow: auto; }

.tag { display: inline-block; font-family: ${FONT_MONO}; font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  border: 2px solid ${MD.black}; border-radius: 2px; background: ${MD.lightWatermelon}; padding: 0 var(--space-1); margin-left: var(--space-2); }
.swatch { display: inline-block; width: 12px; height: 12px; border: 2px solid ${MD.black}; border-radius: 2px;
  margin-right: var(--space-2); vertical-align: -1px; }

/* Admonitions */
.admonition { border: 2px solid ${MD.black}; border-radius: 2px; padding: var(--space-3) var(--space-4); font-size: 14px; }
.admonition .label { display: block; margin-bottom: var(--space-1); font-weight: 700; }
.admonition--warning { background: color-mix(in srgb, ${MD.sun} 30%, ${MD.white}); }
.admonition--danger { background: color-mix(in srgb, ${MD.watermelon} 30%, ${MD.white}); }
.admonition--note { background: ${MD.white}; }

/* Terminal chrome */
.chrome { background: ${MD.white}; border: 2px solid ${MD.black}; border-radius: 2px; overflow: hidden; }
.chrome-bar { display: flex; align-items: center; gap: var(--space-3); padding: var(--space-2) var(--space-3);
  background: ${MD.sand}; border-bottom: 2px solid ${MD.black}; }
.chrome-dots { display: flex; gap: var(--space-2); }
.chrome-dot { width: 12px; height: 12px; border-radius: 50%; border: 2px solid ${MD.black}; }
.chrome-title { font-family: ${FONT_MONO}; font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.chrome-body { padding: var(--space-4); font-family: ${FONT_MONO}; font-size: 14px; line-height: 1.5; white-space: pre;
  overflow-x: auto; max-height: 480px; overflow-y: auto; }

/* Code block (docs CodeBlock): title bar, copy actions, wrapped lines, no sideways scroll */
.codeblock { border: 2px solid ${MD.black}; border-radius: 2px; background: ${MD.white}; min-width: 0; }
.codeblock-bar { display: flex; justify-content: space-between; align-items: center; gap: var(--space-3); flex-wrap: wrap;
  padding: var(--space-2) var(--space-3); background: ${MD.sand}; border-bottom: 2px solid ${MD.black}; }
.codeblock pre { margin: 0; padding: var(--space-4); font-family: ${FONT_MONO}; font-size: 14px; line-height: 1.5;
  white-space: pre-wrap; overflow-wrap: anywhere; overflow-x: hidden; overflow-y: auto; max-height: 480px;
  user-select: text; -webkit-user-select: text; tab-size: 2; }
.btn--small { padding: var(--space-1) var(--space-3); }
.facts { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-5); font-family: ${FONT_MONO}; font-size: 14px;
  font-variant-numeric: tabular-nums; }
.facts span b { font-weight: 700; }

.filter-strip { display: flex; justify-content: space-between; align-items: center; gap: var(--space-4); flex-wrap: wrap;
  padding: var(--space-3) var(--space-5); border-top: 2px solid ${MD.black}; background: color-mix(in srgb, ${MD.sky} 35%, ${MD.snow}); }

/* Status bar (Sim): mono uppercase, middot separators, tabular numerals */
.status { display: flex; flex-wrap: wrap; gap: var(--space-2) var(--space-4); padding: var(--space-3) var(--space-5);
  border-top: 2px solid ${MD.black}; background: ${MD.sand}; font-family: ${FONT_MONO}; font-size: 14px;
  text-transform: uppercase; letter-spacing: 0.02em; font-variant-numeric: tabular-nums; }
.status span + span::before { content: "·"; margin-right: var(--space-4); color: ${MD.darkGrey}; }

/* Scaled SVGs keep true pixel stroke widths */
.card svg rect, .card svg path, .card svg line { vector-effect: non-scaling-stroke; }

.tooltip { background: ${MD.white}; border: 2px solid ${MD.black}; border-radius: 2px; box-shadow: var(--shadow);
  padding: var(--space-2) var(--space-3); font-size: 14px; }

.explainer { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: var(--space-6) var(--space-8); }
.explainer h4 { font-weight: 600; font-size: 1rem; margin: 0 0 var(--space-2); }
.explainer ul { margin: 0; padding-left: var(--space-5); }
.steps { margin: 0; padding: 0; list-style: none; counter-reset: step; display: flex; flex-direction: column; gap: var(--space-5); }
.steps li { counter-increment: step; display: grid; grid-template-columns: 32px minmax(0, 1fr); gap: var(--space-3); }
.steps li::before { content: counter(step); font-family: ${FONT_MONO}; font-size: 14px; font-weight: 700; width: 32px; height: 32px;
  display: flex; align-items: center; justify-content: center; background: ${MD.sky}; border: 2px solid ${MD.black}; border-radius: 2px; }
.steps h4 { font-weight: 600; font-size: 1rem; margin: 0 0 var(--space-1); }
.explainer li + li { margin-top: var(--space-1); }
a { color: ${MD.black}; text-decoration: underline; text-decoration-color: ${MD.darkSky}; text-decoration-thickness: 2px; text-underline-offset: 3px; }

.skel { background: ${MD.darkSand}; border-radius: 2px; animation: pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 0%, 100% { opacity: 1 } 50% { opacity: 0.5 } }

/* User-to-Duckling affinity: sessions grouped under the Duckling that ran them */
.affinity { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: var(--space-4); }
.aff-col { border: 2px solid ${MD.black}; border-radius: 2px; background: ${MD.snow}; min-width: 0; }
.aff-head { display: flex; justify-content: space-between; align-items: center; gap: var(--space-2);
  padding: var(--space-2) var(--space-3); border-bottom: 2px solid ${MD.black}; font-family: ${FONT_MONO}; font-size: 14px; }
.aff-chips { display: flex; flex-wrap: wrap; gap: var(--space-1); padding: var(--space-3); }
.chip { font-family: ${FONT_MONO}; font-size: 12px; border: 1px solid ${MD.black}; border-radius: 2px; background: ${MD.white};
  padding: 0 var(--space-1); font-variant-numeric: tabular-nums; white-space: nowrap; }
.chip.split { background: ${MD.lightWatermelon}; border-width: 2px; }
.aff-stat { font-weight: 600; font-size: 1.25rem; line-height: 1.4; }

/* Business plan Duckling prices */
.sizes { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: var(--space-4); }
.size { border: 2px solid ${MD.black}; border-radius: 2px; background: ${MD.white}; padding: var(--space-4);
  display: flex; flex-direction: column; gap: var(--space-2); min-width: 0; }
.size.current { background: ${MD.sky}; }
.size-icon { width: 32px; height: 32px; color: ${MD.black}; }
.size-icon svg { width: 32px; height: 32px; display: block; }
.size-price { font-weight: 600; font-size: 1.25rem; line-height: 1.4; font-variant-numeric: tabular-nums; }
.size-track { height: 8px; border: 1px solid ${MD.black}; border-radius: 2px; background: ${MD.sandGrey}; overflow: hidden; }
.size-fill { height: 100%; background: ${MD.black}; }
.size.current .size-track { background: ${MD.white}; }

.print-only { display: none; }
@media print {
  @page { size: A4 portrait; margin: 12mm; }
  html, body, #root, .page { background: ${MD.white} !important; }
  .page { padding: 0; gap: var(--space-6); min-height: 0; }
  * { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
  .btn, .select, .pager, .no-print { display: none !important; }
  .print-only { display: block; }
  .card, .kpi { box-shadow: none; }
  /* Large cards may split across pages; keep the small pieces inside them whole. */
  .card { break-inside: auto; }
  .kpi, .size, .tbl tr, .chrome, .status, svg { break-inside: avoid; }
  /* Long text cards flow like a document: one column, normal paragraph breaks, no empty rows. */
  .explainer { display: block; }
  .explainer > div { margin-bottom: var(--space-5); }
  .explainer > div:last-child { margin-bottom: 0; }
  .explainer h4 { break-after: avoid; }
  .steps { display: block; }
  .steps li { margin-bottom: var(--space-4); }
  p, li { orphans: 3; widows: 3; }
  h2.section, h3.card-title, .section-head, .card-head { break-after: avoid; }
  .tbl thead th { position: static; }
  .slide, .skel { animation: none; }
  /* Portrait page: stack side-by-side cards and fit the KPIs two across */
  .grid-2 { grid-template-columns: minmax(0, 1fr); }
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .header { flex-direction: column; align-items: flex-start; }
  .kpi-value { font-size: 2rem; }
  .tbl { font-size: 12px; }
  .tbl th, .tbl td { padding: var(--space-2) var(--space-3); }
}

@media (max-width: 860px) {
  .sizes { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .page { padding: var(--space-6) var(--space-4) var(--space-10); }
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .grid-2, .explainer { grid-template-columns: 1fr; }
  .select { min-width: 0; width: 100%; }
}
`;

// ── Helpers ──
const N = (v: unknown): number => (v == null ? 0 : Number(v));
const S = (v: unknown): string => (v == null ? "" : String(v));
const rows = (q: { data?: unknown }) => (Array.isArray(q.data) ? (q.data as any[]) : []);
const esc = (s: string) => s.replace(/'/g, "''");
const DB = `"md_benchmarks"."main"`;

const fmtSec = (ms: number) => (ms >= 100_000 ? `${(ms / 1000).toFixed(0)} s` : `${(ms / 1000).toFixed(2)} s`);
const fmtMs = (ms: number) => (ms >= 10_000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms).toLocaleString()} ms`);
const fmtUsd = (v: number | null) => (v == null ? "n/a" : v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);
const testLabel = (name: string, c: number) => (name === "serial" ? "Serial" : `Concurrency ${c}`);
const shortTest = (name: string, c: number) => (name === "serial" ? "Serial" : `C=${c}`);
const replicaLabel = (r: number | null) => (r == null ? "Primary" : `rs.${r}`);

// md_bench.py prepends three tag comments; strip them to get the query as written in the CSV.
const stripTags = (sql: string) => sql.replace(/^-- (query_name|concurrency|md_bench_run|session_name): .*\n/gm, "");

// Clipboard API first; fall back to a hidden textarea for sandboxed frames that block it.
async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch {
      return false;
    }
  }
}

function Skeleton({ h = 200 }: { h?: number }) {
  return <div className="skel" style={{ height: h, width: "100%" }} />;
}
function Admonition({ kind, title, children }: { kind: "warning" | "danger" | "note"; title: string; children: ReactNode }) {
  return (
    <div className={`admonition admonition--${kind}`} role={kind === "note" ? undefined : "alert"}>
      <span className="label">{title}</span>
      {children}
    </div>
  );
}
function QueryError({ error }: { error: unknown }) {
  return <Admonition kind="danger" title="Query failed"><span className="mono">{String(error)}</span></Admonition>;
}
function TerminalChrome({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="chrome">
      <div className="chrome-bar">
        <span className="chrome-dots" aria-hidden="true">
          <span className="chrome-dot" style={{ background: MD.watermelon }} />
          <span className="chrome-dot" style={{ background: MD.sun }} />
          <span className="chrome-dot" style={{ background: MD.garden }} />
        </span>
        <span className="chrome-title">{title}</span>
      </div>
      <div className="chrome-body">{children}</div>
    </div>
  );
}
function ChartTooltip({ active, payload, label, unit }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <div className="label" style={{ marginBottom: 4 }}>{label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey} className="num">
          <span className="swatch" style={{ background: p.color || p.fill }} />
          {p.name}: <b>{unit === "s" ? `${N(p.value).toFixed(2)} s` : N(p.value).toLocaleString()}</b>
        </div>
      ))}
    </div>
  );
}

// ── Read scaling pool diagram: client → MotherDuck → Ducklings ──
function PoolDiagram({ replicas, total, connections, sessions = 0, sessionsByReplica = {} }: {
  replicas: { r: number | null; n: number; size: string }[]; total: number; connections: number;
  sessions?: number; sessionsByReplica?: Record<string, number>;
}) {
  const affinity = sessions > 0;
  const W = 760;
  const rowH = 80;
  const H = Math.max(240, replicas.length * rowH + 24);
  const cy = H / 2;
  const mono = FONT_MONO;
  const clientX = 4, clientW = 168, mdX = 252, mdW = 168, dX = 504, dW = W - 504 - 4;
  const max = Math.max(1, ...replicas.map((x) => x.n));
  const ys = replicas.map((_, i) => cy + (i - (replicas.length - 1) / 2) * rowH);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img"
      aria-label={affinity
        ? `Benchmark client sending ${total} queries with ${sessions} session names; MotherDuck routes each session_name to one of ${replicas.length} read scaling Ducklings`
        : `Benchmark client sending ${total} queries over ${connections} connections through MotherDuck to ${replicas.length} read scaling Ducklings`}>
      <defs>
        <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill={MD.black} />
        </marker>
      </defs>

      {/* Client block */}
      <rect x={clientX} y={cy - 44} width={clientW} height={88} rx={2} fill={MD.white} stroke={MD.black} strokeWidth={2} />
      <text x={clientX + 16} y={cy - 14} fontFamily={mono} fontSize={13} fill={MD.black}>MD_BENCH.PY</text>
      <text x={clientX + 16} y={cy + 8} fontFamily={mono} fontSize={12} fill={MD.textSecondary}>
        {sessions > 0 ? `${sessions} session names` : `${connections} connection${connections === 1 ? "" : "s"}`}
      </text>
      <text x={clientX + 16} y={cy + 26} fontFamily={mono} fontSize={12} fill={MD.textSecondary}>{total} queries</text>

      <line x1={clientX + clientW} y1={cy} x2={mdX - 2} y2={cy} stroke={MD.black} strokeWidth={2} markerEnd="url(#arrow)" />

      {/* MotherDuck endpoint block */}
      {affinity ? (
        <>
          <rect x={mdX} y={cy - 64} width={mdW} height={128} rx={2} fill={MD.sun} stroke={MD.black} strokeWidth={2} />
          <text x={mdX + 16} y={cy - 36} fontFamily={mono} fontSize={13} fill={MD.black}>MOTHERDUCK</text>
          <text x={mdX + 16} y={cy - 14} fontFamily={mono} fontSize={12} fill={MD.black}>Postgres endpoint</text>
          <text x={mdX + 16} y={cy + 4} fontFamily={mono} fontSize={12} fill={MD.black}>read scaling token</text>
          <line x1={mdX + 16} y1={cy + 16} x2={mdX + mdW - 16} y2={cy + 16} stroke={MD.black} strokeWidth={1} />
          <text x={mdX + 16} y={cy + 34} fontFamily={mono} fontSize={12} fill={MD.black}>routes by</text>
          <text x={mdX + 16} y={cy + 52} fontFamily={mono} fontSize={13} fontWeight={700} fill={MD.black}>session_name</text>
        </>
      ) : (
        <>
          <rect x={mdX} y={cy - 44} width={mdW} height={88} rx={2} fill={MD.sky} stroke={MD.black} strokeWidth={2} />
          <text x={mdX + 16} y={cy - 14} fontFamily={mono} fontSize={13} fill={MD.black}>MOTHERDUCK</text>
          <text x={mdX + 16} y={cy + 8} fontFamily={mono} fontSize={12} fill={MD.black}>Postgres endpoint</text>
          <text x={mdX + 16} y={cy + 26} fontFamily={mono} fontSize={12} fill={MD.black}>read scaling token</text>
        </>
      )}

      {/* Edges to each Duckling */}
      {replicas.map((rep, i) => (
        <path key={`e${S(rep.r)}`} d={`M ${mdX + mdW} ${cy} C ${mdX + mdW + 48} ${cy}, ${dX - 48} ${ys[i]}, ${dX - 2} ${ys[i]}`}
          fill="none" stroke={MD.black} strokeWidth={2} markerEnd="url(#arrow)" />
      ))}

      {/* Duckling blocks with a share bar */}
      {replicas.map((rep, i) => {
        const y = ys[i];
        const share = total ? rep.n / total : 0;
        const barW = dW - 28;
        return (
          <g key={`d${S(rep.r)}`}>
            <rect x={dX} y={y - 34} width={dW} height={68} rx={2} fill={MD.white} stroke={MD.black} strokeWidth={2} />
            <rect x={dX + 14} y={y - 24} width={12} height={12} rx={2} fill={replicaColor(rep.r)} stroke={MD.black} strokeWidth={2} />
            <text x={dX + 34} y={y - 13} fontFamily={mono} fontSize={13} fill={MD.black}>DUCKLING {replicaLabel(rep.r).toUpperCase()}</text>
            <text x={dX + dW - 14} y={y - 13} textAnchor="end" fontFamily={mono} fontSize={12} fill={MD.black}>
              {rep.n} · {Math.round(share * 100)}%
            </text>
            <text x={dX + 14} y={y + 7} fontFamily={mono} fontSize={12} fill={MD.textSecondary}>
              {rep.size || "Size unknown"}
              {affinity ? ` · ${sessionsByReplica[String(rep.r)] ?? 0} session${(sessionsByReplica[String(rep.r)] ?? 0) === 1 ? "" : "s"} pinned` : ""}
            </text>
            <rect x={dX + 14} y={y + 15} width={barW} height={8} rx={2} fill={MD.sandGrey} stroke={MD.black} strokeWidth={1} />
            <rect x={dX + 14} y={y + 15} width={Math.max(2, (rep.n / max) * barW)} height={8} rx={2} fill={replicaColor(rep.r)} stroke={MD.black} strokeWidth={1} />
          </g>
        );
      })}
    </svg>
  );
}

// ── Duckling size glyphs (motherduck-docs ScalingPatternsDiagram/ducklingIcons.tsx) ──
const DUCKLING_ICONS: Record<string, ReactNode> = {
  Pulse: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" aria-hidden="true">
      <path d="M6.667 5.5h5c1.104 0 2 .895 2 2v5c0 1.105-.896 2-2 2h-5c-1.105 0-2-.895-2-2v-5c0-1.105.895-2 2-2z" />
      <circle cx="17" cy="10" r="1" />
      <path d="M13.75 10H16" />
      <circle cx="9.167" cy="10" r="1.667" fill="currentColor" fillOpacity=".2" />
    </svg>
  ),
  Standard: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" aria-hidden="true">
      <rect x="7.5" y="7.5" width="5" height="5" rx=".5" fill="currentColor" fillOpacity=".2" />
      <rect x="4.5" y="4.5" width="11" height="11" rx="1.5" />
      <path d="M8 4V1.5M12 4V1.5M8 18.5V16M12 18.5V16M18.5 12H16M18.5 8H16M4 12H1.5M4 8H1.5" />
    </svg>
  ),
  Jumbo: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" aria-hidden="true">
      <rect x="3" y="3" width="14" height="14" rx="2.5" />
      <rect x="5.5" y="5.5" width="9" height="9" rx=".5" fill="currentColor" fillOpacity=".2" />
      <path d="M10 2.5V5M10 14.167V16.667" />
    </svg>
  ),
  Mega: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" aria-hidden="true">
      <path d="M17.375 1.597H3.625c-.844 0-1.528.684-1.528 1.528v13.75c0 .844.684 1.528 1.528 1.528h13.75c.844 0 1.528-.684 1.528-1.528V3.125c0-.844-.684-1.528-1.528-1.528z" />
      <rect x="11.833" y="11.333" width="4.833" height="4.833" rx=".5" fill="currentColor" fillOpacity=".3" />
      <rect x="4.333" y="11.333" width="4.833" height="4.833" rx=".5" fill="currentColor" fillOpacity=".3" />
      <rect x="4.333" y="3.833" width="4.833" height="4.833" rx=".5" fill="currentColor" fillOpacity=".3" />
      <rect x="11.833" y="3.833" width="4.833" height="4.833" rx=".5" fill="currentColor" fillOpacity=".3" />
    </svg>
  ),
  Giga: (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" aria-hidden="true">
      <circle cx="10.5" cy="10" r="9.167" />
      <circle cx="10.5" cy="10" r="8.333" strokeOpacity=".4" />
      <circle cx="10.5" cy="10" r="7.5" strokeOpacity=".2" />
      <circle cx="10.5" cy="10" r="6.667" strokeDasharray="3 3" />
      <circle cx="10.5" cy="10" r="3.333" strokeDasharray="1 1" />
      <circle cx="10.5" cy="10" r=".833" fill="currentColor" />
    </svg>
  ),
};

// Business plan compute prices per region tier. Keep in sync with HOURLY_RATES in md_bench.py.
const PRICING_URL = "https://motherduck.com/docs/about-motherduck/billing/pricing/#compute-pricing";
const REGIONS_URL = "https://motherduck.com/docs/about-motherduck/cloud-regions/";
const DUCKLING_SIZES_URL = "https://motherduck.com/docs/about-motherduck/billing/duckling-sizes/";
const REGION_TIERS: Record<string, number> = {
  "us-east-1": 1, "us-west-2": 1, "eu-central-1": 2, "eu-west-1": 2, "ap-northeast-1": 3, "ap-southeast-2": 3,
};
const TIER_RATES: Record<number, { Pulse: number; Standard: number; Jumbo: number; Mega: number; Giga: number }> = {
  1: { Pulse: 0.6, Standard: 2.4, Jumbo: 4.8, Mega: 12, Giga: 24 },
  2: { Pulse: 0.73, Standard: 2.93, Jumbo: 5.86, Mega: 14.65, Giga: 29.3 },
  3: { Pulse: 0.77, Standard: 3.1, Jumbo: 6.19, Mega: 15.48, Giga: 30.96 },
};

// Runs saved without a region assume us-east-1, as md_bench.py does.
function regionTier(region: string) {
  return REGION_TIERS[region || "us-east-1"] ?? null;
}

function businessPrices(tier: number) {
  const r = TIER_RATES[tier];
  return [
    { size: "Pulse", price: r.Pulse, unit: "per CU-hour", billing: "Metered per query", hourly: null as number | null },
    { size: "Standard", price: r.Standard, unit: "per hour", billing: "Billed per second", hourly: r.Standard },
    { size: "Jumbo", price: r.Jumbo, unit: "per hour", billing: "Billed per second", hourly: r.Jumbo },
    { size: "Mega", price: r.Mega, unit: "per hour", billing: "Billed per second", hourly: r.Mega },
    { size: "Giga", price: r.Giga, unit: "per hour", billing: "Billed per second", hourly: r.Giga },
  ];
}

function DucklingPrices({ current, tier }: { current: string; tier: number }) {
  const prices = businessPrices(tier);
  const max = Math.max(...prices.map((p) => p.hourly ?? 0));
  return (
    <div className="sizes" role="list" aria-label="Business plan Duckling prices">
      {prices.map((p) => (
        <div key={p.size} role="listitem" className={`size ${p.size === current ? "current" : ""}`}>
          <span className="size-icon">{DUCKLING_ICONS[p.size]}</span>
          <span className="label">{p.size}{p.size === current ? " · this run" : ""}</span>
          <span className="size-price">${p.price.toFixed(2)}</span>
          <span className="secondary" style={{ color: MD.black }}>{p.unit}</span>
          {p.hourly != null ? (
            <div className="size-track" aria-hidden="true"><div className="size-fill" style={{ width: `${(p.hourly / max) * 100}%` }} /></div>
          ) : (
            <div className="size-track" aria-hidden="true" style={{ borderStyle: "dashed" }} />
          )}
          <span className="secondary" style={{ color: MD.black }}>{p.billing}</span>
        </div>
      ))}
    </div>
  );
}

// ── Timeline: queries in flight over time, one lane per in-flight slot ──
type TimelineItem = {
  id: string; start: number; end: number; r: number | null; name: string; err: boolean;
  clock: string; exec: number; wait: number; size: string; session: string;
};
function Timeline({ items, selectedId, onSelect }: { items: TimelineItem[]; selectedId: string; onSelect: (id: string) => void }) {
  const lanes = useMemo(() => {
    const sorted = [...items].sort((a, b) => a.start - b.start);
    const laneEnds: number[] = [];
    return sorted.map((it) => {
      let lane = laneEnds.findIndex((e) => e <= it.start);
      if (lane === -1) { lane = laneEnds.length; laneEnds.push(it.end); } else laneEnds[lane] = it.end;
      return { ...it, lane };
    });
  }, [items]);
  const laneCount = Math.max(1, ...lanes.map((l) => l.lane + 1));
  const span = Math.max(1, ...items.map((i) => i.end));
  const laneH = laneCount > 24 ? 8 : laneCount > 12 ? 12 : 20;
  const gap = 4;
  const W = 760, left = 4, right = 4, top = 4;
  const H = top + laneCount * (laneH + gap) + 24;
  const x = (ms: number) => left + (ms / span) * (W - left - right);
  const ticks = Array.from({ length: 6 }, (_, i) => (span / 5) * i);
  const [hover, setHover] = useState<{ item: TimelineItem; x: number; y: number; w: number } | null>(null);
  // Position the tooltip from the pointer, relative to the wrapper.
  const track = (item: TimelineItem) => (e: { clientX: number; clientY: number; currentTarget: Element }) => {
    const box = (e.currentTarget as Element).closest(".timeline")?.getBoundingClientRect();
    if (box) setHover({ item, x: e.clientX - box.left, y: e.clientY - box.top, w: box.width });
  };
  const TIP_W = 260;
  return (
    <div className="timeline" style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Timeline of query start and end times by in-flight slot">
        {ticks.map((t, i) => (
          <g key={t}>
            <line x1={x(t)} y1={top} x2={x(t)} y2={H - 20} stroke={MD.lighterGrey} strokeWidth={1} />
            <text x={x(t)} y={H - 4} textAnchor={i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle"} fontFamily={FONT_MONO} fontSize={11} fill={MD.black}>
              {(t / 1000).toFixed(span > 20000 ? 0 : 1)} s
            </text>
          </g>
        ))}
        {lanes.map((l) => {
          const selected = selectedId !== "" && l.id === selectedId;
          const dimmed = selectedId !== "" && !selected;
          return (
            <rect key={l.id} x={x(l.start)} y={top + l.lane * (laneH + gap)} width={Math.max(1.5, x(l.end) - x(l.start) - 1)} height={laneH}
              fill={l.err ? MD.watermelon : replicaColor(l.r)} fillOpacity={dimmed ? 0.35 : 1}
              stroke={MD.black} strokeOpacity={dimmed ? 0.35 : 1} strokeWidth={selected ? 3 : 1} vectorEffect="non-scaling-stroke" rx={1}
              style={{ cursor: "pointer" }}
              onClick={() => onSelect(selected ? "" : l.id)}
              onMouseEnter={track(l)} onMouseMove={track(l)}
              onMouseLeave={() => setHover(null)} />
          );
        })}
      </svg>
      {hover && (
        <div className="tooltip" role="tooltip" style={{
          position: "absolute", pointerEvents: "none", zIndex: 2, width: TIP_W,
          left: hover.x + 16 + TIP_W > hover.w ? Math.max(0, hover.x - 16 - TIP_W) : hover.x + 16,
          top: hover.y + 16,
        }}>
          <div className="mono" style={{ fontWeight: 700, overflowWrap: "anywhere", marginBottom: 4 }}>{hover.item.name}</div>
          <div className="num" style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "0 12px" }}>
            <span>Duckling</span>
            <span className="mono">
              <span className="swatch" style={{ background: replicaColor(hover.item.r) }} />
              {replicaLabel(hover.item.r)}{hover.item.size ? ` · ${hover.item.size}` : ""}
            </span>
            <span>Start</span><span className="mono">{hover.item.clock} UTC · +{(hover.item.start / 1000).toFixed(2)} s</span>
            <span>Duration</span><span className="mono"><b>{fmtMs(hover.item.end - hover.item.start)}</b></span>
            <span>Exec</span><span className="mono">{fmtMs(hover.item.exec)}</span>
            <span>Wait</span><span className="mono">{fmtMs(hover.item.wait)}</span>
            {hover.item.session && (<><span>Session</span><span className="mono">{hover.item.session}</span></>)}
          </div>
          {hover.item.err && <div style={{ marginTop: 4 }}><span className="tag" style={{ marginLeft: 0 }}>Error</span></div>}
          <div className="secondary" style={{ marginTop: 8 }}>Select to show this query in the table</div>
        </div>
      )}
    </div>
  );
}

// ── Main ──
// Each dropdown entry is one test. md_bench.py runs one test per invocation; older results that
// held several tests under one run_id still list each test separately. The serial test and
// every concurrency level are listed as separate runs.
const entryKey = (runId: string, test: string) => `${runId}|${test}`;

export default function ReadScalingBenchmark() {
  const [entryState, setEntry] = useDiveState<string>("entry", "");
  const [sortKey, setSortKey] = useDiveState<string>("sort", "start");
  const [focusQuery, setFocusQuery] = useDiveState<string>("query", "");
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [explainerOpen, setExplainerOpen] = useState(true);
  const [howOpen, setHowOpen] = useState(true);
  const [exportStatus, setExportStatus] = useState<string>("");
  const [copyStatus, setCopyStatus] = useState<string>("");
  const [closingRow, setClosingRow] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [printing, setPrinting] = useState(false);
  const [pdfStatus, setPdfStatus] = useState("");
  useEffect(() => {
    const done = () => setPrinting(false);
    window.addEventListener("afterprint", done);
    return () => window.removeEventListener("afterprint", done);
  }, []);
  // Expand everything, wait for React to render it, then open the browser's print dialog.
  // A sandboxed frame can ignore print(); it then returns at once, so tell the user what to do.
  const exportPdf = () => {
    setPdfStatus("");
    setPrinting(true);
    setTimeout(() => {
      const t0 = performance.now();
      try {
        window.print();
      } catch {
        /* handled below */
      }
      if (performance.now() - t0 < 250) {
        setPdfStatus("If no print dialog opened, open this Dive in its own browser tab and use File > Print, then Save as PDF.");
      }
      setPrinting(false);
    }, 400);
  };

  const entriesQ = useSQLQuery(`
    SELECT t.run_id, t.test_name, t.concurrency, r.run_label,
           strftime(t.server_first_start_utc, '%Y-%m-%d %H:%M UTC') AS started,
           strftime(t.server_first_start_utc, '%m-%d %H:%M') AS started_short,
           r.database_name, r.user_name, r.csv_file, r.query_count AS csv_queries, r.rounds, r.session_mode, r.is_read_scaling, r.region,
           t.e2e_ms, t.client_wall_ms, t.query_count, t.error_count, t.replicas_used, t.instance_type,
           t.hourly_rate_usd, t.price_usd,
           strftime(t.server_first_start_utc, '%H:%M:%S') AS first_start,
           strftime(t.server_last_end_utc, '%H:%M:%S') AS last_end,
           q.p50, q.p95, q.max_ms
    FROM ${DB}."tests" t
    JOIN ${DB}."runs" r USING (run_id)
    LEFT JOIN (
      SELECT run_id, test_name, quantile_cont(duration_ms, 0.5) AS p50, quantile_cont(duration_ms, 0.95) AS p95,
             max(duration_ms) AS max_ms
      FROM ${DB}."queries" GROUP BY ALL
    ) q USING (run_id, test_name)
    ORDER BY t.server_first_start_utc DESC`);
  const entries = rows(entriesQ).map((r) => ({
    key: entryKey(S(r.run_id), S(r.test_name)), runId: S(r.run_id), name: S(r.test_name), c: N(r.concurrency),
    label: S(r.run_label), started: S(r.started), startedShort: S(r.started_short), db: S(r.database_name), user: S(r.user_name),
    csv: S(r.csv_file), csvQueries: N(r.csv_queries), rounds: N(r.rounds), session: S(r.session_mode), isRs: r.is_read_scaling,
    region: S(r.region),
    e2e: N(r.e2e_ms), wall: N(r.client_wall_ms), n: N(r.query_count), errs: N(r.error_count), reps: N(r.replicas_used),
    inst: S(r.instance_type), rate: r.hourly_rate_usd == null ? null : N(r.hourly_rate_usd),
    price: r.price_usd == null ? null : N(r.price_usd), first: S(r.first_start), last: S(r.last_end),
    p50: N(r.p50), p95: N(r.p95), max: N(r.max_ms),
  }));
  const entry = entries.find((e) => e.key === entryState) ?? entries[0];
  const ready = entry != null;
  const region = entry?.region || "us-east-1";
  const tier = regionTier(entry?.region ?? "");
  const rid = esc(entry?.runId ?? "");
  const tname = esc(entry?.name ?? "");
  const scope = `run_id = '${rid}' AND test_name = '${tname}'`;
  const entryTitle = (e: { name: string; c: number }) => testLabel(e.name, e.c);

  const distQ = useSQLQuery(`
    SELECT replica_num, mode(instance_type) AS size, count(*) AS n, avg(duration_ms) AS avg_ms, avg(wait_ms) AS avg_wait_ms
    FROM ${DB}."queries" WHERE ${scope}
    GROUP BY ALL ORDER BY replica_num NULLS FIRST`, { enabled: ready });
  const dist = rows(distQ).map((r) => ({
    r: r.replica_num == null ? null : N(r.replica_num), size: S(r.size), n: N(r.n), avg: N(r.avg_ms), wait: N(r.avg_wait_ms),
  }));

  // User-to-Duckling affinity: which Duckling(s) each session_name ran on.
  const affinityQ = useSQLQuery(`
    SELECT session_name, replica_num, count(*) AS n
    FROM ${DB}."queries"
    WHERE ${scope} AND session_name IS NOT NULL AND session_name <> ''
    GROUP BY ALL ORDER BY replica_num NULLS FIRST, session_name`, { enabled: ready });
  const affinity = useMemo(() => {
    const pairs = rows(affinityQ).map((r) => ({
      session: S(r.session_name), r: r.replica_num == null ? null : N(r.replica_num), n: N(r.n),
    }));
    const perSession = new Map<string, Set<string>>();
    for (const p of pairs) {
      if (!perSession.has(p.session)) perSession.set(p.session, new Set());
      perSession.get(p.session)!.add(String(p.r));
    }
    const split = new Set([...perSession].filter(([, reps]) => reps.size > 1).map(([s]) => s));
    const columns = new Map<string, { r: number | null; sessions: { session: string; n: number }[] }>();
    for (const p of pairs) {
      const k = String(p.r);
      if (!columns.has(k)) columns.set(k, { r: p.r, sessions: [] });
      columns.get(k)!.sessions.push({ session: p.session, n: p.n });
    }
    const byNumber = (a: string, b: string) => (Number(a) - Number(b)) || a.localeCompare(b);
    for (const c of columns.values()) c.sessions.sort((a, b) => byNumber(a.session, b.session));
    return { sessions: perSession.size, split, columns: [...columns.values()] };
  }, [affinityQ.data]);
  const hasSessions = affinity.sessions > 0;
  const stuck = affinity.sessions - affinity.split.size;

  const timelineQ = useSQLQuery(`
    SELECT CAST(query_id AS VARCHAR) AS qid, query_name, replica_num, error_message IS NOT NULL AS is_err,
           strftime(start_utc, '%H:%M:%S') AS start_clock, exec_ms, wait_ms, instance_type, session_name,
           epoch_ms(start_utc) - min(epoch_ms(start_utc)) OVER () AS s_ms,
           epoch_ms(end_utc) - min(epoch_ms(start_utc)) OVER () AS e_ms
    FROM ${DB}."queries" WHERE ${scope}`, { enabled: ready });

  const sortSql: Record<string, string> = {
    start: "start_utc", duration: "duration_ms DESC", exec: "exec_ms DESC", wait: "wait_ms DESC", replica: "replica_num, start_utc",
  };
  const queriesQ = useSQLQuery(`
    SELECT CAST(query_id AS VARCHAR) AS qid, query_name, replica_num, duckling_id, session_name,
           strftime(start_utc, '%H:%M:%S.%g') AS started, duration_ms, exec_ms, wait_ms, error_message, query_text
    FROM ${DB}."queries"
    WHERE ${scope}
      ${focusQuery ? `AND CAST(query_id AS VARCHAR) = '${esc(focusQuery)}'` : ""}
    ORDER BY ${sortSql[sortKey] ?? "start_utc"}`, { enabled: ready });
  const queryRows = rows(queriesQ);

  // Last 12 runs, oldest first, for the comparison chart.
  const e2eChart = entries.slice(0, 12).reverse().map((e) => ({
    key: e.key, run: `${shortTest(e.name, e.c)} · ${e.startedShort}`, e2e: e.e2e / 1000,
  }));
  const timelineItems = rows(timelineQ).map((r) => ({
    id: S(r.qid), start: N(r.s_ms), end: N(r.e_ms), r: r.replica_num == null ? null : N(r.replica_num), name: S(r.query_name), err: Boolean(r.is_err),
    clock: S(r.start_clock), exec: N(r.exec_ms), wait: N(r.wait_ms), size: S(r.instance_type), session: S(r.session_name),
  }));
  const peakInFlight = useMemo(() => {
    const ev = timelineItems
      .flatMap((i) => [[i.start, 1], [i.end, -1]] as [number, number][])
      .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    let cur = 0, peak = 0;
    for (const [, d] of ev) { cur += d; peak = Math.max(peak, cur); }
    return peak;
  }, [timelineItems]);

  const summaryLoading = entriesQ.isLoading || !ready;
  const noRuns = entriesQ.isSuccess && entries.length === 0;

  const PAGE_SIZE = 20;
  const pageCount = Math.max(1, Math.ceil(queryRows.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount - 1);
  const pageStart = safePage * PAGE_SIZE;
  const pageRows = queryRows.slice(pageStart, pageStart + PAGE_SIZE);
  const goToPage = (n: number) => { setPage(Math.max(0, Math.min(pageCount - 1, n))); setOpenRow(""); setCopyStatus(""); };
  // Page numbers to show: first, last, and the current page with its neighbours.
  const pageNumbers = Array.from({ length: pageCount }, (_, i) => i)
    .filter((i) => i === 0 || i === pageCount - 1 || Math.abs(i - safePage) <= 1);

  const selectEntry = (key: string) => {
    setEntry(key);
    setFocusQuery(undefined as any);
    setOpenRow(null);
    setPage(0);
  };
  // Selecting a timeline bar filters the query table to that query and opens its SQL.
  const selectQuery = (id: string) => {
    setFocusQuery(id || undefined as any);
    setOpenRow(id || null);
    setPage(0);
  };
  const focused = focusQuery ? queryRows.find((q) => S(q.qid) === focusQuery) : undefined;
  const selectedId = openRow !== null ? openRow : focusQuery;

  const doExport = async () => {
    try {
      setExportStatus("Exporting…");
      await queriesQ.exportAs({
        format: "csv", title: "Benchmark queries",
        filename: `md_bench_${(entry?.runId ?? "").slice(0, 8)}_${entry?.name ?? "run"}_queries`,
      });
      setExportStatus("");
    } catch (e) {
      setExportStatus(`Export failed: ${String(e)}`);
    }
  };

  const sortHeader = (key: string, label: string, right = false) => (
    <th key={key} className={`sortable ${right ? "r" : ""}`} onClick={() => { setSortKey(key); setPage(0); }}
      aria-sort={sortKey === key ? (right ? "descending" : "ascending") : "none"}>
      {label}{sortKey === key ? (right ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <div className="page">
      <style>{CSS}</style>

      {/* Header */}
      <header className="header">
        <div className="stack-2">
          <span className="label">Performance benchmarking</span>
          <h1 className="title">MotherDuck query benchmarking</h1>
          <p className="lede">
            End-to-end time, per-query latency, how queries spread across read scaling Ducklings, and the estimated price
            for each benchmark run. Results come from <code className="inline">md_information_schema.query_history</code>,
            collected by <code className="inline">md_bench.py</code>.
          </p>
        </div>
        <div className="stack-2" style={{ alignItems: "flex-start" }}>
          <div className="no-print" style={{ display: "flex", gap: 12, alignItems: "center", alignSelf: "flex-end" }}>
            <button className="btn" onClick={exportPdf} disabled={printing || !ready}>{printing ? "Preparing…" : "Export PDF"}</button>
          </div>
          {pdfStatus && <p className="secondary no-print" role="status" style={{ maxWidth: 320 }}>{pdfStatus}</p>}
          <label className="label" htmlFor="run-select">Benchmark run</label>
          {entry && (
            <span className="print-only mono">{entryTitle(entry)} · {entry.db} · {entry.started}</span>
          )}
          {entriesQ.isLoading ? <div className="skel" style={{ height: 40, width: 320 }} /> : (
            <select id="run-select" className="select" value={entry?.key ?? ""} onChange={(e) => selectEntry(e.target.value)}>
              {entries.map((e) => (
                <option key={e.key} value={e.key}>
                  {entryTitle(e)} · {e.db} · {e.started}
                </option>
              ))}
            </select>
          )}
          {entry && (
            <span className="secondary">
              <span className="mono">{entry.db}</span> as <span className="mono">{entry.user}</span> · {entry.csv},{" "}
              {entry.csvQueries} queries × {entry.rounds} round{entry.rounds === 1 ? "" : "s"} · run {entry.runId.slice(0, 8)}
            </span>
          )}
        </div>
      </header>

      {entriesQ.isError && <QueryError error={entriesQ.error} />}
      {noRuns && (
        <Admonition kind="note" title="No runs yet">
          Run <code className="inline">python md_bench.py</code> to add a benchmark run to <code className="inline">md_benchmarks</code>.
        </Admonition>
      )}
      {entry && entry.isRs === false && (
        <Admonition kind="warning" title="Not a read scaling run">
          None of this run's queries ran on a read scaling Duckling. Check that <code className="inline">md_bench.py</code> used a read scaling token.
        </Admonition>
      )}

      {/* KPIs */}
      <section className="kpis" aria-label="Run summary">
        {[
          { label: "E2E time", value: entry ? fmtSec(entry.e2e) : "", sub: entry ? `${entry.first}–${entry.last} UTC` : "", bg: MD.sky },
          { label: "Queries", value: entry ? entry.n.toLocaleString() : "", sub: entry ? `${entry.errs} error${entry.errs === 1 ? "" : "s"} · ${entryTitle(entry)}` : "", bg: MD.white },
          { label: "Ducklings used", value: entry ? String(entry.reps) : "", sub: entry?.inst ? `${entry.inst} Ducklings` : "Read scaling pool", bg: MD.white },
          { label: "Estimated price", value: entry ? fmtUsd(entry.price) : "", sub: entry?.rate != null ? `${entry.reps} × $${entry.rate.toFixed(2)}/h for the E2E time` : "No rate for this size", bg: MD.sun },
        ].map((k) => (
          <div key={k.label} className="kpi" style={{ background: k.bg }}>
            <span className="label">{k.label}</span>
            {summaryLoading ? <div className="skel" style={{ height: 44, width: "70%" }} /> : <span className="kpi-value">{k.value}</span>}
            <span className="kpi-sub">{k.sub}</span>
          </div>
        ))}
      </section>

      {/* How the benchmark works */}
      <section className="stack-4">
        <div className="section-head">
          <h2 className="section">How the benchmark works</h2>
          <button className="btn btn--secondary" onClick={() => setHowOpen(!howOpen)} aria-expanded={howOpen}>
            {howOpen ? "Collapse" : "Expand"}
          </button>
        </div>
        {(howOpen || printing) && (
          <div className="card card-body">
            <div className="grid-2" style={{ gap: 32 }}>
              <ol className="steps">
                <li>
                  <div>
                    <h4>Load the queries</h4>
                    <p>
                      <code className="inline">md_bench.py</code> reads a CSV with two columns, <code className="inline">query_name</code>{" "}
                      and <code className="inline">query_text</code>. With <code className="inline">--rounds N</code>, the test works
                      through the list N times.
                    </p>
                  </div>
                </li>
                <li>
                  <div>
                    <h4>Tag every query</h4>
                    <p>
                      Before it sends a query, the script adds comment lines with the query name, the concurrency level, and a
                      run ID. The tags stay in <code className="inline">query_history</code>, so each query can be matched to
                      its run afterward.
                    </p>
                  </div>
                </li>
                <li>
                  <div>
                    <h4>Run one test</h4>
                    <p>
                      Each run of the script is one test, set by <code className="inline">--concurrency</code>. Run it again to
                      add another test; serial and concurrent runs never need to happen together. With{" "}
                      <code className="inline">--concurrency 1</code>, one connection runs the queries one at a time, and the next
                      query starts only after the previous one finishes and its results are fetched.
                    </p>
                  </div>
                </li>
                <li>
                  <div>
                    <h4>Keep N queries in flight</h4>
                    <p>
                      With <code className="inline">--concurrency N</code>, the script opens N connections before the clock starts. Each connection takes the
                      next query from a shared queue as soon as its last one finishes, so N queries stay in flight until the
                      queue is empty. Every connection uses the read scaling token, so MotherDuck assigns it a Duckling from the
                      pool.
                    </p>
                  </div>
                </li>
                <li>
                  <div>
                    <h4>Collect the results</h4>
                    <p>
                      With a read/write token, the script waits until every tagged query appears in{" "}
                      <code className="inline">md_information_schema.query_history</code>, reads it in UTC, and copies the rows
                      into <code className="inline">md_benchmarks</code> as a new run.
                    </p>
                  </div>
                </li>
                <li>
                  <div>
                    <h4>Calculate the metrics</h4>
                    <p>
                      E2E time is the last query's end minus the first query's start. The Duckling comes from the number after{" "}
                      <code className="inline">.rs.</code> in <code className="inline">duckling_id</code>. Price is the E2E time
                      in hours, times the Ducklings used, times the hourly rate for their size.
                    </p>
                  </div>
                </li>
              </ol>
              <div className="stack-4">
                <TerminalChrome title="Tagged query">{`-- query_name: ${focused ? S(focused.query_name) : "daily_revenue"}
-- concurrency: ${entry && entry.c > 1 ? `${entry.c} queries` : "1 query"}
-- md_bench_run: ${entry ? entry.runId : "<run id>"}
SELECT ...`}</TerminalChrome>
                <TerminalChrome title="terminal">{`> python md_bench.py \\
    --queries queries.csv \\
    --database ${entry ? entry.db : "<database>"} \\
    --user-name ${entry ? entry.user : "<user_name>"} \\
    --concurrency ${entry ? entry.c : 4} \\
    --rounds 1`}</TerminalChrome>
                <p className="secondary">
                  The script connects through the Postgres endpoint with <code className="inline">psycopg2</code>, not a local
                  DuckDB client. Set the read scaling token in <code className="inline">MOTHERDUCK_READ_SCALING_TOKEN</code> and
                  the read/write token in <code className="inline">MOTHERDUCK_TOKEN</code>. Add{" "}
                  <code className="inline">--session-mode per-worker</code> to give each connection its own{" "}
                  <code className="inline">session_name</code>.
                </p>
              </div>
            </div>
          </div>
        )}
      </section>

      {/* Compare runs */}
      <section className="stack-4">
        <h2 className="section">Compare runs</h2>
        {entriesQ.isError ? <QueryError error={entriesQ.error} /> : (
          <div className="grid-2">
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">All runs</h3>
                <span className="secondary">Select a row to show that run</span>
              </div>
              {summaryLoading ? <div className="card-body"><Skeleton h={140} /></div> : (
                <div className="tbl-wrap scroll" style={{ maxHeight: 400 }}>
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>Run</th><th className="r">E2E</th><th className="r">P50</th><th className="r">P95</th>
                        <th className="r">Queries</th><th className="r">Ducklings</th><th>Affinity</th><th className="r">Price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map((e) => (
                        <tr key={e.key} className={`clickable ${e.key === entry?.key ? "selected" : ""}`} tabIndex={0}
                          onClick={() => selectEntry(e.key)} onKeyDown={(ev) => ev.key === "Enter" && selectEntry(e.key)}>
                          <td>
                            {entryTitle(e)}
                            {e.errs > 0 && <span className="tag">{e.errs} errors</span>}
                            <div className="secondary mono">{e.db} · {e.started}</div>
                          </td>
                          <td className="r"><b>{fmtSec(e.e2e)}</b></td>
                          <td className="r">{fmtMs(e.p50)}</td>
                          <td className="r">{fmtMs(e.p95)}</td>
                          <td className="r">{e.n}</td>
                          <td className="r">{e.reps}</td>
                          <td>{e.session === "csv" ? "session_name" : e.session === "per-worker" ? "Per connection" : "None"}</td>
                          <td className="r">{fmtUsd(e.price)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">E2E time by run</h3>
                <span className="secondary">Seconds, last 12 runs</span>
              </div>
              <div className="card-chart">
                {summaryLoading ? <div style={{ paddingLeft: 16 }}><Skeleton h={220} /></div> : (
                  <ResponsiveContainer width="100%" height={260}>
                    <BarChart data={e2eChart} onClick={(st: any) => { const k = st?.activePayload?.[0]?.payload?.key; if (k) selectEntry(k); }}>
                      <CartesianGrid stroke={MD.lighterGrey} vertical={false} />
                      <XAxis dataKey="run" fontSize={11} fontFamily={FONT_MONO} stroke={MD.black} tickLine={false} interval={0} />
                      <YAxis fontSize={12} fontFamily={FONT_MONO} stroke={MD.black} tickFormatter={(v) => `${v} s`} width={56} />
                      <Tooltip content={<ChartTooltip unit="s" />} cursor={{ fill: MD.sandGrey }} />
                      <Bar dataKey="e2e" name="E2E" stroke={MD.black} strokeWidth={2} radius={[2, 2, 0, 0]} style={{ cursor: "pointer" }}>
                        {e2eChart.map((d) => <Cell key={d.key} fill={d.key === entry?.key ? MD.sky : MD.sandGrey} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          </div>
        )}
      </section>

      {/* Distribution */}
      <section className="stack-4">
        <h2 className="section">How queries spread across Ducklings</h2>
        {distQ.isError ? <QueryError error={distQ.error} /> : (
          <div className="grid-2">
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">
                  {entry ? entryTitle(entry) : "Read scaling pool"}
                  {hasSessions && <span className="tag" style={{ background: MD.sun, verticalAlign: "middle" }}>session_name affinity</span>}
                </h3>
                <span className="secondary">
                  {hasSessions ? "Routed by session_name. Bar length shows each Duckling's share of queries" : "Bar length shows each Duckling's share of queries"}
                </span>
              </div>
              {hasSessions && (
                <div className="filter-strip" style={{ background: `color-mix(in srgb, ${MD.sun} 30%, ${MD.white})`, borderTop: 0, borderBottom: `2px solid ${MD.black}` }}>
                  <span>
                    <span className="label" style={{ marginRight: 8 }}>session_name set</span>
                    Each query's connection carried its CSV row's <code className="inline">session_name</code>, so MotherDuck pinned
                    each of the {affinity.sessions} sessions to one Duckling instead of spreading connections round robin.
                    {affinity.split.size > 0 ? ` ${affinity.split.size} session${affinity.split.size === 1 ? "" : "s"} moved between Ducklings.` : ""}
                  </span>
                </div>
              )}
              <div className="card-body">
                {distQ.isLoading || !ready ? <Skeleton h={240} /> : dist.length === 0 ? <p className="secondary">This run has no queries.</p> : (
                  <PoolDiagram replicas={dist.map((d) => ({ r: d.r, n: d.n, size: d.size }))} total={dist.reduce((a, d) => a + d.n, 0)}
                    connections={entry?.c ?? 1} sessions={affinity.sessions}
                    sessionsByReplica={Object.fromEntries(affinity.columns.map((c) => [String(c.r), c.sessions.length]))} />
                )}
              </div>
            </div>
            <div className="card">
              <div className="card-head">
                <h3 className="card-title">Ducklings in this run</h3>
                <span className="secondary">Average per query</span>
              </div>
              {distQ.isLoading || !ready ? <div className="card-body"><Skeleton h={140} /></div> : (
                <div className="tbl-wrap">
                  <table className="tbl">
                    <thead><tr><th>Duckling</th><th>Size</th><th className="r">Queries</th><th className="r">Duration</th><th className="r">Wait</th></tr></thead>
                    <tbody>
                      {dist.map((d) => (
                        <tr key={S(d.r)}>
                          <td className="mono"><span className="swatch" style={{ background: replicaColor(d.r) }} />{replicaLabel(d.r)}</td>
                          <td>{d.size || "n/a"}</td>
                          <td className="r">{d.n}</td>
                          <td className="r">{fmtMs(d.avg)}</td>
                          <td className="r">{fmtMs(d.wait)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        )}
        {hasSessions && (
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">User-to-Duckling affinity</h3>
              <span className="secondary">Each query ran with its <code className="inline">session_name</code> from the CSV</span>
            </div>
            <div className="card-body stack-4">
              <p>
                <span className="aff-stat">{stuck} of {affinity.sessions} sessions</span>{" "}
                ({affinity.sessions ? Math.round((stuck / affinity.sessions) * 100) : 0}%) ran all of their queries on one
                Duckling.{affinity.split.size > 0 ? ` ${affinity.split.size} moved between Ducklings and are marked in red.` : " Affinity held for every session."}
              </p>
              <div className="affinity">
                {affinity.columns.map((col) => (
                  <div key={String(col.r)} className="aff-col">
                    <div className="aff-head">
                      <span><span className="swatch" style={{ background: replicaColor(col.r) }} />{replicaLabel(col.r)}</span>
                      <span className="secondary">{col.sessions.length} session{col.sessions.length === 1 ? "" : "s"}</span>
                    </div>
                    <div className="aff-chips">
                      {col.sessions.map((s) => (
                        <span key={s.session} className={`chip ${affinity.split.has(s.session) ? "split" : ""}`}
                          title={`${s.session}: ${s.n} quer${s.n === 1 ? "y" : "ies"} on ${replicaLabel(col.r)}`}>
                          {s.session}{s.n > 1 ? ` ×${s.n}` : ""}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              <p className="secondary">
                Each box is a Duckling, and each chip is a session with its query count. With affinity, MotherDuck sends
                every connection with the same <code className="inline">session_name</code> to the same Duckling when it
                can, so a session's cache stays warm. Queries without a session name aren't shown here.
              </p>
            </div>
          </div>
        )}
          <div className="stack-4">
            <div className="section-head">
              <h3 className="card-title">How read scaling works</h3>
              <button className="btn btn--secondary" onClick={() => setExplainerOpen(!explainerOpen)} aria-expanded={explainerOpen}>
                {explainerOpen ? "Collapse" : "Expand"}
              </button>
            </div>
            {(explainerOpen || printing) && (
              <div className="card card-body stack-4">
                <div className="explainer">
                  <div>
                    <h4>A pool of read-only Ducklings</h4>
                    <p>
                      A read scaling token sends your reads to a pool of read-only replicas of your Duckling. The pool holds 4
                      Ducklings by default, and you can raise it to 16. Support can raise it further. In{" "}
                      <code className="inline">query_history</code>, a replica shows up as a <code className="inline">duckling_id</code>{" "}
                      ending in <code className="inline">.rs.N</code>.
                    </p>
                  </div>
                  <div>
                    <h4>Routing happens per connection</h4>
                    <p>
                      Each new connection is assigned a Duckling, and MotherDuck starts more Ducklings as connections grow, up to
                      the pool size. Once the pool is full, new connections are spread round robin across the Ducklings that
                      already exist. <code className="inline">md_bench.py</code> opens one connection per concurrent query slot.
                    </p>
                  </div>
                  <div>
                    <h4>Keep a user on the same Duckling</h4>
                    <p>
                      Connections that send the same <code className="inline">session_name</code> go to the same Duckling when
                      possible, which keeps its cache warm. On the Postgres endpoint, set it with{" "}
                      <code className="inline">options=--session_name=&lt;id&gt;</code>. Use a stable value per user, tenant, or
                      session.
                    </p>
                    <p style={{ marginTop: 8 }}>
                      This helps most when each user or tenant runs repeated queries over their own slice of the data, such as a
                      customer-facing dashboard filtered by tenant, because the Duckling already holds that data in cache. It's
                      optional: without a <code className="inline">session_name</code>, MotherDuck spreads connections across
                      the pool round robin, and queries work the same way.
                    </p>
                  </div>
                  <div>
                    <h4>Replicas are eventually consistent</h4>
                    <p>
                      Ducklings in the pool sync from your read/write Duckling, usually within a few minutes. For fresher data, run{" "}
                      <code className="inline">CREATE SNAPSHOT</code> on the writer, then <code className="inline">REFRESH DATABASES</code>{" "}
                      on the readers. Creating a snapshot interrupts queries running on that database.
                    </p>
                  </div>
                  <div>
                    <h4>What changes in your application</h4>
                    <ul>
                      <li>Your SQL stays the same. You change the token type and the connection settings.</li>
                      <li>Read scaling tokens allow <code className="inline">SELECT</code> only. Writes, creating databases, and attaching or detaching are blocked.</li>
                      <li>Pulse, Standard, and Jumbo Ducklings work in a read scaling pool by default.</li>
                      <li>Aim for about one Duckling per concurrent user.</li>
                    </ul>
                  </div>
                  <div>
                    <h4>Postgres endpoint or DuckDB client</h4>
                    <ul>
                      <li>
                        <b>Postgres endpoint:</b> the default for a backend API that serves a browser app. It works with the Postgres
                        drivers, connection pooling, and auth middleware you already have, and with serverless runtimes. It has no
                        local files, extension loading, or Dual Execution. Set <code className="inline">application_name</code> to
                        label your client.
                      </li>
                      <li>
                        <b>DuckDB client</b> (<code className="inline">duckdb</code> or <code className="inline">@duckdb/node-api</code>):
                        use it when the service also reads local files, runs queries across local and cloud data, or needs direct
                        control of DuckDB. Set <code className="inline">custom_user_agent</code> to label your client.
                      </li>
                      <li>
                        Both work with a read scaling token and the same SQL. This benchmark uses the Postgres endpoint, so its
                        timings include that path.
                      </li>
                    </ul>
                  </div>
                </div>
                <p className="secondary">
                  Learn more in the{" "}
                  <a href="https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/" target="_blank" rel="noopener noreferrer">read scaling guide</a>, the{" "}
                  <a href="https://motherduck.com/docs/sql-reference/postgres-endpoint/" target="_blank" rel="noopener noreferrer">Postgres endpoint reference</a>, and the{" "}
                  <a href="https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/connecting-to-motherduck/#session-names" target="_blank" rel="noopener noreferrer">session names section</a>.
                </p>
              </div>
            )}
          </div>
      </section>

      {/* Query execution details: the timeline and every query, as one graphic */}
      <section className="stack-4">
        <div className="section-head">
          <h2 className="section">Query execution details</h2>
          <button className="btn btn--secondary" onClick={doExport}>Export CSV</button>
        </div>
        {exportStatus && <p className="secondary">{exportStatus}</p>}
        <div className="card">
          <div className="card-head">
            <h3 className="card-title">{entry ? entryTitle(entry) : "Timeline"}</h3>
            <span className="secondary">One lane per in-flight slot, colored by Duckling. Select a bar or a row to see its SQL.</span>
          </div>
          <div className="card-body">
            {timelineQ.isError ? <QueryError error={timelineQ.error} /> :
              timelineQ.isLoading || !ready ? <Skeleton h={160} /> : <Timeline items={timelineItems} selectedId={focusQuery} onSelect={selectQuery} />}
          </div>
          {entry && (
            <div className="status">
              <span>{entryTitle(entry)}</span>
              <span>Peak in flight {peakInFlight}</span>
              <span>Queries {entry.n}</span>
              <span>E2E {fmtSec(entry.e2e)}</span>
              <span>Ducklings {entry.reps}</span>
              {hasSessions && <span>Sessions {affinity.sessions}</span>}
            </div>
          )}
          {focusQuery && (
            <div className="filter-strip no-print">
              <span>
                <span className="label" style={{ marginRight: 8 }}>Filtered to 1 query</span>
                {focused ? (
                  <span className="mono">{S(focused.query_name)} · {replicaLabel(focused.replica_num == null ? null : N(focused.replica_num))}</span>
                ) : <span className="secondary">Selected from the timeline</span>}
              </span>
              <button className="btn btn--secondary btn--small" onClick={() => selectQuery("")}>Show all queries</button>
            </div>
          )}
          {entry && (
            <p className="print-only secondary" style={{ padding: "12px 20px", borderTop: `2px solid ${MD.black}` }}>
              {entry.n} queries ran in this test. Open the Dive to see each query's timings and SQL.
            </p>
          )}
          <div className="no-print" style={{ borderTop: `2px solid ${MD.black}` }}>
            {queriesQ.isError ? <div className="card-body"><QueryError error={queriesQ.error} /></div> :
              queriesQ.isLoading || !ready ? <div className="card-body"><Skeleton h={240} /></div> : (
                <div className="tbl-wrap">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th className="r">#</th>
                        <th>Query</th>
                        {hasSessions && <th>Session</th>}
                        {sortHeader("replica", "Duckling")}
                        {sortHeader("start", "Start (UTC)")}
                        {sortHeader("duration", "Duration", true)}
                        {sortHeader("exec", "Exec", true)}
                        {sortHeader("wait", "Wait", true)}
                      </tr>
                    </thead>
                    <tbody>
                      {pageRows.map((q, idx) => {
                        const i = pageStart + idx;
                        const id = S(q.qid) || String(i);
                        const r = q.replica_num == null ? null : N(q.replica_num);
                        const isSelected = id === selectedId;
                        const isClosing = !isSelected && closingRow === id;
                        const toggle = () => {
                          setCopyStatus("");
                          if (isSelected) {
                            // Keep the row mounted while it slides closed, then remove it.
                            setClosingRow(id);
                            setOpenRow("");
                            setTimeout(() => setClosingRow((c) => (c === id ? null : c)), 170);
                          } else {
                            setOpenRow(id);
                          }
                        };
                        return (
                          <Fragment key={id}>
                            <tr className={`clickable ${isSelected ? "selected" : ""}`} tabIndex={0} aria-expanded={isSelected}
                              onClick={toggle} onKeyDown={(e) => e.key === "Enter" && toggle()}>
                              <td className="r">{i + 1}</td>
                              <td className="mono" style={{ whiteSpace: "normal" }}>
                                {isSelected ? "▾ " : "▸ "}{S(q.query_name)}
                                {q.error_message != null && <span className="tag">Error</span>}
                              </td>
                              {hasSessions && <td className="mono">{S(q.session_name) || "–"}</td>}
                              <td className="mono"><span className="swatch" style={{ background: replicaColor(r) }} />{replicaLabel(r)}</td>
                              <td className="mono">{S(q.started)}</td>
                              <td className="r"><b>{fmtMs(N(q.duration_ms))}</b></td>
                              <td className="r">{fmtMs(N(q.exec_ms))}</td>
                              <td className="r">{fmtMs(N(q.wait_ms))}</td>
                            </tr>
                            {(isSelected || isClosing) && (
                              <tr className="detail">
                                <td colSpan={hasSessions ? 8 : 7} style={{ whiteSpace: "normal" }}>
                                  <div className={`slide ${isClosing ? "closing" : ""}`}><div>
                                  <div className="detail-inner">
                                    {q.error_message != null && (
                                      <Admonition kind="danger" title="Error"><span className="mono">{S(q.error_message)}</span></Admonition>
                                    )}
                                    <div className="codeblock">
                                      <div className="codeblock-bar">
                                        <span className="mono">{S(q.duckling_id)}</span>
                                        <span style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                                          {copyStatus && <span className="secondary" role="status">{copyStatus}</span>}
                                          <button className="btn btn--small" onClick={async () => {
                                            const ok = await copyText(stripTags(S(q.query_text)));
                                            setCopyStatus(ok ? "Copied SQL" : "Copy is blocked here. Select the text and press Cmd+C or Ctrl+C.");
                                          }}>Copy SQL</button>
                                          <button className="btn btn--secondary btn--small" onClick={async () => {
                                            const ok = await copyText(S(q.query_text));
                                            setCopyStatus(ok ? "Copied with benchmark tags" : "Copy is blocked here. Select the text and press Cmd+C or Ctrl+C.");
                                          }}>Copy with tags</button>
                                        </span>
                                      </div>
                                      <pre>{stripTags(S(q.query_text))}</pre>
                                    </div>
                                  </div>
                                  </div></div>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
          </div>
          {queryRows.length > PAGE_SIZE && (
            <nav className="pager" aria-label="Query table pages">
              <span className="mono" style={{ fontVariantNumeric: "tabular-nums" }}>
                {pageStart + 1}–{Math.min(pageStart + PAGE_SIZE, queryRows.length)} of {queryRows.length} queries
              </span>
              <div className="pager-pages">
                <button className="btn btn--secondary btn--small btn--reverse" onClick={() => goToPage(safePage - 1)} disabled={safePage === 0}>
                  Previous
                </button>
                {pageNumbers.map((n, k) => (
                  <Fragment key={n}>
                    {k > 0 && n - pageNumbers[k - 1] > 1 && <span className="mono" aria-hidden="true">…</span>}
                    <button className="btn btn--secondary btn--small" aria-current={n === safePage ? "page" : undefined}
                      aria-label={`Page ${n + 1}`} onClick={() => goToPage(n)}>
                      {n + 1}
                    </button>
                  </Fragment>
                ))}
                <button className="btn btn--secondary btn--small" onClick={() => goToPage(safePage + 1)} disabled={safePage >= pageCount - 1}>
                  Next
                </button>
              </div>
            </nav>
          )}
        </div>
      </section>

      {/* Price */}
      <section className="stack-4">
        <div className="section-head">
          <h2 className="section">Estimated price of the test workload</h2>
          <a href={PRICING_URL} target="_blank" rel="noopener noreferrer">MotherDuck pricing</a>
        </div>
        <div className="grid-2">
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Price for this run</h3>
              <span className="secondary">USD</span>
            </div>
            {entriesQ.isError ? <div className="card-body"><QueryError error={entriesQ.error} /></div> :
              summaryLoading || !entry ? <div className="card-body"><Skeleton h={140} /></div> : (
                <div className="tbl-wrap">
                  <table className="tbl">
                    <tbody>
                      <tr><td>Run</td><td className="r">{entryTitle(entry)}</td></tr>
                      <tr><td>Region</td><td className="r">{entry.region || "us-east-1 (assumed)"}{tier ? ` · tier ${tier}` : ""}</td></tr>
                      <tr><td>Duckling size</td><td className="r">{entry.inst || "n/a"}</td></tr>
                      <tr><td>E2E time</td><td className="r">{fmtSec(entry.e2e)} ({(entry.e2e / 3_600_000).toFixed(4)} h)</td></tr>
                      <tr><td>Ducklings used</td><td className="r">{entry.reps}</td></tr>
                      <tr><td>Hourly rate</td><td className="r">{entry.rate == null ? "n/a" : `$${entry.rate.toFixed(2)}/h`}</td></tr>
                    </tbody>
                    <tfoot>
                      <tr><td>Estimated price</td><td className="r">{fmtUsd(entry.price)}</td></tr>
                    </tfoot>
                  </table>
                </div>
              )}
          </div>
          <div className="stack-4">
            <TerminalChrome title="price.sql">{`price_usd =
  epoch(max(end_time) - min(start_time)) / 3600
  * count(DISTINCT duckling_id)   -- Ducklings used
  * CASE instance_type          -- ${tier ? `tier ${tier} rates, ${region}` : `no rates for ${region}`}
${tier ? (["Standard", "Jumbo", "Mega", "Giga"] as const).map((k) => `      WHEN '${k}'${" ".repeat(9 - k.length)}THEN ${TIER_RATES[tier][k].toFixed(2).padStart(5)}`).join("\n") : "      -- see the pricing page"}
    END`}</TerminalChrome>
            <p className="secondary">
              Each run is priced over its own window, from the first query's start to the last query's end, at the rates
              for the organization's{" "}
              <a href={REGIONS_URL} target="_blank" rel="noopener noreferrer">cloud region</a>. Compare runs above lists the
              price of every run. Duckling sizes without a rate here, such as Pulse, show n/a.
            </p>
          </div>
        </div>
        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Business plan Duckling prices{tier ? ` · ${region}` : ""}</h3>
            <span className="secondary">Bar length shows the hourly price relative to Giga</span>
          </div>
          <div className="card-body stack-4">
            {tier ? <DucklingPrices current={entry?.inst ?? ""} tier={tier} /> : (
              <p>No rates for {region} in this Dive. See the pricing page for that region's rates.</p>
            )}
            <p className="secondary">
              Rates differ per region: US regions are tier 1, Europe tier 2, and Asia Pacific tier 3. Standard, Jumbo, Mega,
              and Giga are billed per second while they run; see{" "}
              <a href={DUCKLING_SIZES_URL} target="_blank" rel="noopener noreferrer">Duckling sizes</a>. Pulse is metered per
              query in compute units, so it doesn't fit the hourly formula above. The Business plan includes read scaling, with
              up to 16 replicas, each backed by its own Duckling. Prices come from the{" "}
              <a href={PRICING_URL} target="_blank" rel="noopener noreferrer">compute pricing docs</a>; check them for current
              rates and for the Lite and Enterprise plans.
            </p>
          </div>
        </div>
      </section>
    </div>
  );
}
