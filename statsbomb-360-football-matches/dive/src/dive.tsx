import { useRef, useEffect, useMemo, useState } from "react";
import { useSQLQuery, useDiveState } from "@motherduck/react-sql-query";
import * as d3 from "d3";

// Port of index.html (animated 360 replay) + passes.html (pass/shot flights +
// shot map) into one Dive. The visuals are bespoke D3/SVG (an animation engine
// and side-on flight rows), so this runs the original D3 against refs inside
// useEffect rather than rebuilding in Recharts. Data is reconstructed from
// statsbomb.marts.replay_events / replay_dots / replay_markers + core.matches
// into the same {match, frames, markers} shape the old match_data.json had.

const N = (v: unknown): number => (v != null ? Number(v) : 0);
const S = (v: unknown): string | null => (v != null ? String(v) : null);
const HOME_COLOR = "#6FC2FF"; // sky  (prep_data.py HOME_COLOR)
const AWAY_COLOR = "#FF7169"; // watermelon (AWAY_COLOR)

const MARKER_STYLE: Record<string, any> = {
  goal:         { color: "#383838", shape: "goal",  label: "Goal",         on: true },
  penalty_goal: { color: "#383838", shape: "goal",  label: "Penalty goal", on: true },
  shot:         { color: "#60726f", shape: "tick",  label: "Shot",         on: true },
  yellow:       { color: "#FFDE00", shape: "card",  label: "Yellow card",  on: true },
  red:          { color: "#FF7169", shape: "card",  label: "Red card",     on: true },
  sub:          { color: "#16AA98", shape: "tick",  label: "Substitution", on: true },
  offside:      { color: "#6FC2FF", shape: "tick",  label: "Offside",      on: true },
  free_kick:    { color: "#6FC2FF", shape: "tick",  label: "Free kick",    on: true },
  corner:       { color: "#16AA98", shape: "tick",  label: "Corner",       on: true },
  throw_in:     { color: "#9db4af", shape: "tick",  label: "Throw-in",     on: true },
  foul:         { color: "#FF7169", shape: "tick",  label: "Foul",         on: true },
};
const PERIOD: Record<number, string> = { 1: "1H", 2: "2H", 3: "ET1", 4: "ET2" };

const CSS = `
.sb360 { --md-sun:#FFDE00; --md-sky:#6FC2FF; --md-garden:#16AA98; --md-watermelon:#FF7169;
  --md-neutral900:#383838; --md-sand:#F4EFEA; --ink:var(--md-neutral900); --muted:#60726f;
  --pitch:#bfeccc; --pitch-border:rgba(255,255,255,.95); --pitch-line:rgba(255,255,255,.9);
  --grid:rgba(255,255,255,.28); --panel:#bfeccc; --border:#cfe6d6;
  background:var(--md-sand); color:var(--ink);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; }
.sb360 * { box-sizing:border-box; }
.sb360 .wrap { max-width:1160px; margin:0 auto; padding:14px 18px 30px; }
.sb360 header.top { display:flex; align-items:baseline; gap:14px; flex-wrap:wrap; margin-bottom:10px; }
.sb360 header.top h1 { font-size:15px; font-weight:600; margin:0; }
.sb360 header.top .score { font-variant-numeric:tabular-nums; font-weight:700; font-size:18px; }
.sb360 header.top .meta { color:var(--muted); font-size:12.5px; }
.sb360 .teamdot { display:inline-block; width:10px; height:10px; border-radius:50%; vertical-align:middle; margin-right:5px; }
.sb360 .tabs { margin-left:auto; display:flex; gap:8px; align-items:center; }
.sb360 .tabs button { font:inherit; font-size:12.5px; border:1px solid var(--border); background:#fff;
  border-radius:7px; padding:5px 11px; cursor:pointer; color:var(--ink); }
.sb360 .tabs button.active { background:var(--md-garden); color:#fff; border-color:var(--md-garden); font-weight:700; }
.sb360 .matchpicker { position:relative; }
.sb360 .matchtitle { font:inherit; font-size:15px; font-weight:600; letter-spacing:.2px; color:var(--ink);
  background:#fff; border:1px solid var(--border); border-radius:8px; padding:6px 12px; cursor:pointer;
  display:inline-flex; align-items:center; gap:10px; box-shadow:0 1px 2px rgba(56,56,56,.06); }
.sb360 .matchtitle:hover { border-color:rgba(22,170,152,.55); background:#f3fbf9; }
.sb360 .matchtitle.open { border-color:var(--md-garden); background:#f3fbf9; }
.sb360 .matchtitle .caret { font-size:13px; line-height:1; color:var(--md-garden); transition:transform .12s; }
.sb360 .matchtitle.open .caret { transform:rotate(180deg); }
.sb360 .picker-panel { position:absolute; top:100%; left:0; margin-top:6px; z-index:20; width:340px;
  background:#fff; border:1px solid var(--border); border-radius:8px; box-shadow:0 12px 30px rgba(56,56,56,.16); padding:8px; }
.sb360 .picker-search { width:100%; font:inherit; font-size:12.5px; padding:6px 8px; border:1px solid var(--border);
  border-radius:6px; margin-bottom:6px; color:var(--ink); outline:none; }
.sb360 .picker-search:focus { border-color:var(--md-garden); }
.sb360 .picker-list { max-height:320px; overflow-y:auto; }
.sb360 .picker-row { display:block; width:100%; text-align:left; background:none; border:none; cursor:pointer; padding:6px 8px; border-radius:6px; }
.sb360 .picker-row:hover { background:#eef8f6; }
.sb360 .picker-row.sel { background:#e6f5f2; }
.sb360 .pr-teams { display:block; font-size:12.5px; font-weight:600; color:var(--ink); }
.sb360 .pr-meta { display:block; font-size:11px; color:var(--muted); }
.sb360 .picker-empty { padding:8px; font-size:12px; color:var(--muted); }
.sb360 .stage { position:relative; background:var(--panel); border:1px solid var(--border);
  border-radius:8px; overflow:hidden; box-shadow:0 14px 34px rgba(56,56,56,.08); }
.sb360 svg#pitch { display:block; width:100%; height:auto; }
.sb360 .dir-label { font-size:11px; fill:var(--muted); font-weight:600; }
.sb360 .pname { font-size:11.5px; font-weight:700; text-anchor:middle; paint-order:stroke;
  stroke:rgba(255,255,255,.92); stroke-width:3.4px; pointer-events:none; }
.sb360 .exiting, .sb360 .hint { pointer-events:none; }
.sb360 .goal-banner { font-size:30px; font-weight:800; text-anchor:middle; letter-spacing:1px;
  paint-order:stroke; stroke:rgba(255,255,255,.92); stroke-width:6px; pointer-events:none; }
.sb360 .side { display:flex; flex-direction:row; align-items:center; justify-content:space-between;
  flex-wrap:wrap; gap:6px 12px; padding:12px 10.86% 6px; }
.sb360 .ctrls { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.sb360 .side button { font:inherit; font-size:12px; border:1px solid var(--border); background:#fff;
  border-radius:7px; padding:6px 11px; cursor:pointer; color:var(--ink); }
.sb360 .side button.primary { background:var(--md-sun); color:var(--ink); border-color:var(--md-sun); font-weight:700; }
.sb360 .speeds { display:flex; gap:4px; }
.sb360 .speeds button { padding:4px 9px; font-size:11px; }
.sb360 .speeds button.active { background:var(--ink); color:#fff; border-color:var(--ink); }
.sb360 .keys-hint { font-size:11px; color:var(--muted); }
.sb360 .trail-ctl { display:inline-flex; align-items:center; gap:7px; font-size:12.5px; color:var(--muted); }
.sb360 .trail-ctl input[type="checkbox"] { accent-color:var(--md-garden); }
.sb360 .trail-ctl input[type="range"] { accent-color:var(--md-garden); width:130px; }
.sb360 .trail-ctl .val { font-variant-numeric:tabular-nums; min-width:64px; }
.sb360 .trail-ctl label { display:inline-flex; align-items:center; gap:5px; cursor:pointer; }
.sb360 .event-label { font-size:12.5px; color:var(--ink); font-variant-numeric:tabular-nums; text-align:right; }
.sb360 .event-label .clk { font-weight:700; }
.sb360 .controls-row { display:flex; align-items:flex-start; justify-content:space-between;
  gap:10px 24px; flex-wrap:wrap; margin-top:12px; }
.sb360 svg#timeline { display:block; width:100%; height:88px; }
.sb360 .axis text { fill:var(--muted); font-size:11px; }
.sb360 .axis line, .sb360 .axis path { stroke:var(--border); }
.sb360 .ht-line { stroke:rgba(22,170,152,.36); stroke-dasharray:3 3; }
.sb360 .playhead line { stroke:var(--ink); stroke-width:1.5; }
.sb360 .playhead circle { fill:var(--ink); cursor:ew-resize; }
.sb360 .marker { cursor:pointer; }
.sb360 .mlegend { flex:1 1 360px; min-width:0; display:flex; gap:8px 14px; flex-wrap:wrap; font-size:12px; color:var(--muted); }
.sb360 .mlegend label { display:inline-flex; align-items:center; gap:6px; cursor:pointer; user-select:none; }
.sb360 .mlegend .swatch { width:11px; height:11px; border-radius:2px; display:inline-block; }
.sb360 .mlegend input { accent-color:var(--md-garden); }
.sb360 .note { color:var(--muted); font-size:11.5px; margin-top:14px; line-height:1.5; }
.sb360 .note .sw { font-weight:700; }
.sb360 .sec { font-size:12.5px; font-weight:700; letter-spacing:.4px; text-transform:uppercase;
  color:var(--muted); margin:18px 2px 8px; }
.sb360 .cols { display:grid; grid-template-columns:1fr 1fr; gap:10px 22px; align-items:start; }
.sb360 .teamhead { font-size:14px; font-weight:700; margin:8px 0 2px; }
.sb360 .teamhead .total { font-weight:400; color:var(--muted); font-size:12px; margin-left:6px; }
.sb360 .anchornote { font-size:11px; color:var(--muted); margin:0 0 8px; }
.sb360 .prow { margin-bottom:10px; }
.sb360 .prow .lbl { display:flex; align-items:baseline; gap:8px; font-size:12.5px; margin:0 2px 3px; }
.sb360 .prow .lbl .nm { font-weight:700; }
.sb360 .prow .lbl .ct { color:var(--muted); font-size:11.5px; font-variant-numeric:tabular-nums; }
.sb360 .panel { background:#fff; border:1px solid var(--border); border-radius:10px; box-shadow:0 6px 16px rgba(56,56,56,.05); }
.sb360 .panel svg { display:block; width:100%; height:auto; }
.sb360 .panel svg.hot { cursor:pointer; }
.sb360 .flight { fill:none; stroke:var(--muted); }
.sb360 .flight.shot { stroke:var(--ink); }
.sb360 .anchor { fill:var(--ink); }
.sb360 .headring { fill:none; stroke:var(--muted); stroke-width:1; opacity:.8; pointer-events:none; }
.sb360 .goalmark { fill:var(--md-sun); stroke:var(--ink); stroke-width:1; pointer-events:none; }
.sb360 .pitchpanel { overflow:hidden; }
.sb360 .pitchbg { fill:var(--pitch); }
.sb360 .spl { fill:none; stroke:var(--pitch-line); stroke-width:1.4; }
.sb360 .pspot { fill:var(--pitch-line); }
.sb360 .shotline { fill:none; }
.sb360 .shotdot { stroke:#fff; stroke-width:1; }
.sb360 .xglegend { display:inline-flex; align-items:flex-end; gap:14px; margin-top:4px; }
.sb360 .xglegend svg { display:block; overflow:visible; }
.sb360 .xglegend .cap { font-size:11px; color:var(--muted); }
.sb360-tip { position:fixed; pointer-events:none; background:#383838; color:#fff; font-size:12px;
  padding:6px 9px; border-radius:6px; opacity:0; transition:opacity .1s; white-space:nowrap;
  z-index:10; transform:translate(-50%,-130%); }
.sb360-tip .dim { opacity:.65; }
`;

// ============================================================================
// Replay (port of index.html setup), scoped to `root` (the replay container).
// ============================================================================
function renderReplay(root: any, tip: any, data: any): () => void {
  const M = data.match, frames = data.frames, markers = data.markers;
  const showTip = (html: string, x: number, y: number) =>
    tip.html(html).style("left", x + "px").style("top", y + "px").style("opacity", 1);
  const hideTip = () => tip.style("opacity", 0);

  root.select("#note").html(
    `Player positions are from StatsBomb 360 broadcast tracking, so only players visible to the camera appear, ` +
    `and only the on-ball player is named (other dots glide via frame-to-frame position matching, not true identity). ` +
    `Coordinates de-normalized to a fixed pitch: <b style="color:${M.home_color}">${M.home}</b> attacks right, ` +
    `<b style="color:${M.away_color}">${M.away}</b> attacks left, all match. ${frames.length} tracked events.`
  );

  const W = 1280, H = 720, PAD = 26;
  const fh = H - PAD * 2;
  const fw = fh * (120 / 80);
  const ox = (W - fw) / 2, oy = PAD;
  const X = d3.scaleLinear().domain([0, 120]).range([ox, ox + fw]);
  const Y = d3.scaleLinear().domain([0, 80]).range([oy, oy + fh]);
  const sLen = (v: number) => (fw / 120) * v;

  const svg = root.select("#pitch");
  svg.selectAll("*").remove();
  const gPitch = svg.append("g");
  svg.insert("rect", ":first-child").attr("width", W).attr("height", H).attr("fill", "var(--panel)");
  gPitch.append("rect").attr("x", ox).attr("y", oy).attr("width", fw).attr("height", fh)
    .attr("fill", "var(--pitch)").attr("stroke", "var(--pitch-border)").attr("stroke-width", 2).attr("rx", 3);
  const line = (x1: number, y1: number, x2: number, y2: number) => gPitch.append("line")
    .attr("x1", X(x1)).attr("y1", Y(y1)).attr("x2", X(x2)).attr("y2", Y(y2))
    .attr("stroke", "var(--pitch-line)").attr("stroke-width", 1.6);
  for (let c = 1; c < 6; c++) gPitch.append("line")
    .attr("x1", X(c * 20)).attr("y1", Y(0)).attr("x2", X(c * 20)).attr("y2", Y(80))
    .attr("stroke", "var(--grid)").attr("stroke-width", 1);
  for (let r = 1; r < 5; r++) gPitch.append("line")
    .attr("x1", X(0)).attr("y1", Y(r * 16)).attr("x2", X(120)).attr("y2", Y(r * 16))
    .attr("stroke", "var(--grid)").attr("stroke-width", 1);
  line(60, 0, 60, 80);
  gPitch.append("circle").attr("cx", X(60)).attr("cy", Y(40)).attr("r", sLen(10))
    .attr("fill", "none").attr("stroke", "var(--pitch-line)").attr("stroke-width", 1.6);
  [[0, 18, 18, 62], [102, 120, 18, 62]].forEach(([x1, x2, y1, y2]) => {
    gPitch.append("rect").attr("x", X(x1)).attr("y", Y(y1)).attr("width", sLen(x2 - x1)).attr("height", sLen(y2 - y1))
      .attr("fill", "none").attr("stroke", "var(--pitch-line)").attr("stroke-width", 1.6);
  });
  [[0, 6, 30, 50], [114, 120, 30, 50]].forEach(([x1, x2, y1, y2]) => {
    gPitch.append("rect").attr("x", X(x1)).attr("y", Y(y1)).attr("width", sLen(x2 - x1)).attr("height", sLen(y2 - y1))
      .attr("fill", "none").attr("stroke", "var(--pitch-line)").attr("stroke-width", 1.6);
  });
  [12, 108].forEach((px) => gPitch.append("circle").attr("cx", X(px)).attr("cy", Y(40)).attr("r", 2).attr("fill", "var(--pitch-line)"));
  gPitch.append("circle").attr("cx", X(60)).attr("cy", Y(40)).attr("r", 2).attr("fill", "var(--pitch-line)");
  [[0, 36, 0, 44], [120, 36, 120, 44]].forEach(([x1, y1, x2, y2]) => line(x1, y1, x2, y2));
  svg.append("text").attr("class", "dir-label").attr("x", ox + fw - 4).attr("y", oy + fh + 16)
    .attr("text-anchor", "end").attr("fill", M.home_color).text(`${M.home} attacks this way →`);
  svg.append("text").attr("class", "dir-label").attr("x", ox + 4).attr("y", oy + fh + 16)
    .attr("text-anchor", "start").attr("fill", M.away_color).text(`← ${M.away} attacks this way`);

  const eventLabel = root.select("#eventLabel");
  const gTrail = svg.append("g"), gHints = svg.append("g"), gVector = svg.append("g");
  const gPlayers = svg.append("g"), gBall = svg.append("g"), gLabels = svg.append("g"), gGoal = svg.append("g");
  const colorFor = (t: string) => (t === M.home ? M.home_color : M.away_color);
  const ballDot = gBall.append("circle").attr("r", 5).attr("fill", "#fff").attr("stroke", "#383838")
    .attr("stroke-width", 2).attr("cx", X(frames[0].b[0])).attr("cy", Y(frames[0].b[1]));

  const tl = root.select("#timeline");
  tl.selectAll("*").remove();
  const TW = 1160, TH = 88, tlPad = { l: 8, r: 8, top: 30, bottom: 22 };
  tl.attr("viewBox", `0 0 ${TW} ${TH}`);
  const maxMin = (d3.max(frames, (f: any) => f.m + f.s / 60) as number) + 0.5;
  const T = d3.scaleLinear().domain([0, maxMin]).range([tlPad.l, TW - tlPad.r]);
  frames.forEach((f: any) => { f.tmin = f.m + f.s / 60; f.t = f.m * 60 + f.s; });
  frames.forEach((f: any, i: number) => {
    f.bEff = (f.ob && i > 0) ? (frames[i - 1].be || frames[i - 1].bEff)
      : (f.ty === "Shot" && f.be ? f.be : f.b);
  });
  const axisG = tl.append("g").attr("class", "axis").attr("transform", `translate(0,${TH - tlPad.bottom})`);
  axisG.call(d3.axisBottom(T).tickValues([0, 15, 30, 45, 60, 75, 90]).tickFormat((d: any) => d + "'"));
  const p2start = d3.min(frames.filter((f: any) => f.pd === 2), (f: any) => f.tmin);
  if (p2start != null) tl.append("line").attr("class", "ht-line")
    .attr("x1", T(p2start)).attr("x2", T(p2start)).attr("y1", tlPad.top - 8).attr("y2", TH - tlPad.bottom);

  const markerG = tl.append("g");
  function drawMarkers() {
    const active = markers.filter((m: any) => MARKER_STYLE[m.k] && MARKER_STYLE[m.k].on);
    const sel = markerG.selectAll(".marker").data(active, (d: any, i: number) => d.k + "-" + d.m + "-" + d.s + "-" + i);
    sel.exit().remove();
    const enter = sel.enter().append("g").attr("class", "marker");
    enter.merge(sel).each(function (this: any, d: any) {
      const g = d3.select(this); g.selectAll("*").remove();
      const x = T(d.m + d.s / 60), st = MARKER_STYLE[d.k];
      const baseY = TH - tlPad.bottom;
      if (st.shape === "goal") {
        g.append("circle").attr("cx", x).attr("cy", tlPad.top - 2).attr("r", 6)
          .attr("fill", colorFor(d.tm)).attr("stroke", "#fff").attr("stroke-width", 1.5);
        g.append("line").attr("x1", x).attr("x2", x).attr("y1", tlPad.top + 4).attr("y2", baseY)
          .attr("stroke", colorFor(d.tm)).attr("stroke-width", 1.2);
      } else if (st.shape === "card") {
        g.append("rect").attr("x", x - 3).attr("y", tlPad.top + 2).attr("width", 6).attr("height", 9)
          .attr("fill", st.color).attr("stroke", "#0003");
        g.append("line").attr("x1", x).attr("x2", x).attr("y1", tlPad.top + 11).attr("y2", baseY)
          .attr("stroke", st.color).attr("stroke-width", 1);
      } else {
        g.append("line").attr("x1", x).attr("x2", x).attr("y1", tlPad.top + 8).attr("y2", baseY)
          .attr("stroke", st.color).attr("stroke-width", 2);
      }
      g.append("rect").attr("x", x - 5).attr("y", tlPad.top - 8).attr("width", 10).attr("height", baseY - tlPad.top + 8)
        .attr("fill", "transparent")
        .on("mousemove", (e: any) => showTip(`<b>${d.lbl}</b><br>${d.m}'${String(d.s).padStart(2, "0")} · ${d.tm || ""}`, e.clientX, e.clientY))
        .on("mouseleave", hideTip)
        .on("click", () => { goTo(nearestFrameByTime(d.m + d.s / 60)); });
    });
  }

  const phG = tl.append("g").attr("class", "playhead");
  const phLine = phG.append("line").attr("y1", tlPad.top - 10).attr("y2", TH - tlPad.bottom + 4);
  const phDot = phG.append("circle").attr("r", 6).attr("cy", tlPad.top - 10);
  const placePlayhead = (t: number) => { phLine.attr("x1", T(t)).attr("x2", T(t)); phDot.attr("cx", T(t)); };
  phDot.call(d3.drag().on("drag", (e: any) => {
    const t = T.invert(Math.max(tlPad.l, Math.min(TW - tlPad.r, e.x)));
    goTo(nearestFrameByTime(t));
  }) as any);
  tl.on("click", (e: any) => {
    const [mx] = d3.pointer(e);
    if (mx >= tlPad.l && mx <= TW - tlPad.r && e.target.tagName !== "rect") goTo(nearestFrameByTime(T.invert(mx)));
  });
  function nearestFrameByTime(t: number) {
    let best = 0, bd = Infinity;
    for (let i = 0; i < frames.length; i++) { const d = Math.abs(frames[i].tmin - t); if (d < bd) { bd = d; best = i; } }
    return best;
  }

  const legend = root.select("#legend");
  legend.selectAll("*").remove();
  Object.entries(MARKER_STYLE).forEach(([k, st]: any) => {
    if (!markers.some((m: any) => m.k === k)) return;
    const lab = legend.append("label");
    lab.append("input").attr("type", "checkbox").property("checked", st.on)
      .on("change", function (this: any) { st.on = this.checked; drawMarkers(); });
    lab.append("span").attr("class", "swatch").style("background", st.color);
    lab.append("span").text(st.label);
  });

  let cur = 0, playing = false, mult = 1, timer: any = null;
  let trailOn = true, trailLen = 50;
  const MATCH_DIST2 = 35 * 35, NAMED_ANON_DIST2 = 15 * 15;
  const FADE_OUT = [1, 0.5, 0.2, 0.08], FADE_IN = [0, 0.45, 0.25, 0.12];
  const LOOKAHEAD = FADE_IN.length - 1;
  const dotOpacity = (r: any) => (r.age >= 0 ? FADE_OUT[r.age] : FADE_IN[-r.age]);
  let registry: any[] = [], regK = -1, regT: number | null = null, nextId = 1;

  function greedyMatch(entries: any[], players: any[]) {
    const out = new Array(players.length).fill(null);
    for (const team of [M.home, M.away]) {
      const prev = entries.filter((r) => r.t === team);
      const cand = players.map((p, i) => ({ p, i })).filter((o) => o.p.t === team);
      const pairs: any[] = [];
      for (const r of prev) for (const o of cand) {
        if (r.nm && o.p.nm && r.nm !== o.p.nm) continue;
        const exact = !!(r.nm && o.p.nm);
        const dx = r.x - o.p.p[0], dy = r.y - o.p.p[1], d2 = dx * dx + dy * dy;
        const cap2 = (o.p.nm && !r.nm) ? NAMED_ANON_DIST2 : MATCH_DIST2;
        if (d2 <= cap2) pairs.push({ r, o, d2, exact });
      }
      pairs.sort((a, b) => (b.exact - a.exact) || (a.d2 - b.d2));
      const usedPrev = new Set(), usedNew = new Set();
      for (const pr of pairs) {
        if (usedPrev.has(pr.r.id) || usedNew.has(pr.o.i)) continue;
        usedPrev.add(pr.r.id); usedNew.add(pr.o.i); out[pr.o.i] = pr.r;
      }
    }
    return out;
  }
  function nameDot(r: any, nm: string) {
    if (!nm) return;
    for (const o of registry) if (o !== r && o.nm === nm) o.nm = null;
    r.nm = nm;
  }
  const withNames = (pp: any[], pl: string) => (pl ? pp.map((p) => (p.a ? Object.assign({ nm: pl }, p) : p)) : pp);

  function stepRegistry(f: any) {
    if (regT !== null && f.t !== regT) for (const r of registry) if (r.age >= 0) r.age++;
    regT = f.t;
    if (f.pp) {
      const pls = withNames(f.pp, f.pl);
      const matched = greedyMatch(registry, pls);
      pls.forEach((p: any, i: number) => {
        let r = matched[i];
        if (!r) { r = { id: nextId++, t: p.t }; registry.push(r); }
        r.x = p.p[0]; r.y = p.p[1]; r.age = 0; r.a = p.a; r.k = p.k;
        if (p.nm) nameDot(r, p.nm); else r.nm = null;
      });
    } else if (f.pl && f.b) {
      for (const r of registry) r.a = false;
      const m = greedyMatch(registry, [{ p: f.b, t: f.tm, nm: f.pl }]);
      let r = m[0];
      if (!r) { r = { id: nextId++, t: f.tm, k: false }; registry.push(r); }
      r.x = f.b[0]; r.y = f.b[1]; r.age = 0; r.a = true;
      nameDot(r, f.pl);
    }
    registry = registry.filter((r) => r.age < FADE_OUT.length);
  }
  function refreshIncoming(k: number) {
    const liveGhost = registry.filter((r) => r.age >= 0);
    const prevInc = registry.filter((r) => r.age < 0);
    const virtual = liveGhost.map((r) => ({ id: r.id, x: r.x, y: r.y, t: r.t, nm: r.nm }));
    const found: any[] = [];
    let d = 1;
    for (let i = k + 1; i < frames.length && i <= k + 2 * LOOKAHEAD; i++) {
      const nf = frames[i];
      if (nf.pd !== frames[k].pd) break;
      if (i > k + 1 && nf.t !== frames[i - 1].t) d++;
      if (d > LOOKAHEAD) break;
      const pls = nf.pp ? withNames(nf.pp, nf.pl) : (nf.pl && nf.b ? [{ p: nf.b, t: nf.tm, nm: nf.pl }] : null);
      if (!pls) continue;
      const matched = greedyMatch(virtual, pls);
      pls.forEach((p: any, j: number) => {
        if (matched[j]) { matched[j].x = p.p[0]; matched[j].y = p.p[1]; matched[j].nm = p.nm || null; }
        else { found.push({ p: p.p, t: p.t, d, nm: p.nm }); virtual.push({ id: "v" + virtual.length, x: p.p[0], y: p.p[1], t: p.t, nm: p.nm }); }
      });
    }
    const matched = greedyMatch(prevInc, found.map((c) => ({ p: c.p, t: c.t, nm: c.nm })));
    const fresh = found.map((c, i) => {
      const r = matched[i] || { id: nextId++, t: c.t };
      r.x = c.p[0]; r.y = c.p[1]; r.age = -c.d; if (c.nm) r.nm = c.nm; return r;
    });
    registry = liveGhost.concat(fresh);
  }
  function syncRegistry(k: number) {
    if (k === regK) return;
    if (regK >= 0 && k === regK + 1 && frames[k].pd === frames[regK].pd) {
      stepRegistry(frames[k]);
    } else {
      const before = registry.filter((r) => r.age >= 0);
      registry = []; regT = null;
      for (let i = Math.max(0, k - 2 * FADE_OUT.length); i <= k; i++) {
        if (frames[i].pd !== frames[k].pd) { registry = []; regT = null; continue; }
        stepRegistry(frames[i]);
      }
      const adopt = greedyMatch(before, registry.map((r) => ({ p: [r.x, r.y], t: r.t, nm: r.nm })));
      registry.forEach((r, i) => { if (adopt[i]) r.id = adopt[i].id; });
    }
    refreshIncoming(k); regK = k;
  }
  function renderTrail(k: number) {
    if (!trailOn) { gTrail.selectAll("line").remove(); return; }
    const segs: any[] = [];
    const start = Math.max(0, k - trailLen);
    for (let i = start; i < k; i++) {
      const a = frames[i], b = frames[i + 1];
      if (a.pd !== b.pd) continue;
      if (a.bEff[0] === b.bEff[0] && a.bEff[1] === b.bEff[1]) continue;
      const age = k - i;
      segs.push({ a: a.bEff, b: b.bEff, t: a.pt || a.tm, f: 1 - age / trailLen });
    }
    gTrail.selectAll("line").data(segs).join("line")
      .attr("x1", (d: any) => X(d.a[0])).attr("y1", (d: any) => Y(d.a[1]))
      .attr("x2", (d: any) => X(d.b[0])).attr("y2", (d: any) => Y(d.b[1]))
      .attr("stroke", (d: any) => colorFor(d.t)).attr("stroke-linecap", "round")
      .attr("stroke-width", (d: any) => 0.8 + 2.2 * d.f).attr("opacity", (d: any) => 0.55 * d.f);
  }
  function glide(sel: any, ax: string, ay: string, fx: any, fy: any, dur: number, ease: any) {
    sel.filter(function (this: any, d: any) {
      const x = fx(d), y = fy(d), same = this.__gx === x && this.__gy === y;
      this.__gx = x; this.__gy = y; return !same;
    }).transition("move").duration(dur).ease(ease).attr(ax, fx).attr(ay, fy);
  }
  const KICKED = new Set(["Pass", "Shot", "Clearance", "Goal Keeper"]);
  const RESTARTS = new Set(["Corner", "Free Kick", "Goal Kick", "Kick Off", "Throw-in"]);

  function render(k: number, dur = 160) {
    const f = frames[k];
    const ease = playing ? d3.easeLinear : d3.easeCubicOut;
    syncRegistry(k);
    const sel = gPlayers.selectAll("circle.player").data(registry, (d: any) => d.id);
    sel.exit().remove();
    const ent = sel.enter().append("circle").attr("class", "player")
      .attr("cx", (d: any) => X(d.x)).attr("cy", (d: any) => Y(d.y)).attr("opacity", 0)
      .on("mousemove", function (e: any, d: any) {
        const fr = frames[cur];
        const who = (d.age === 0 && d.a && fr.pl)
          ? `<b>${fr.pl}</b><br>${d.t}${d.k ? " · goalkeeper" : ""} · on the ball`
          : `<b>${d.t}</b> ${d.k ? "goalkeeper" : "player"}<br><span class="dim">unnamed in 360 data</span>`;
        showTip(who, e.clientX, e.clientY);
      })
      .on("mouseleave", hideTip);
    ent.merge(sel)
      .attr("pointer-events", (d: any) => (d.age === 0 ? null : "none"))
      .attr("fill", (d: any) => colorFor(d.t)).attr("stroke", "none")
      .call((s: any) => s.transition("fade").duration(dur).ease(ease)
        .attr("opacity", dotOpacity).attr("r", (d: any) => (d.age === 0 && d.a ? 9 : 7)))
      .call((s: any) => glide(s, "cx", "cy", (d: any) => X(d.x), (d: any) => Y(d.y), dur, ease));

    const prev = k > 0 && frames[k - 1].pd === f.pd ? frames[k - 1] : null;
    const ballEase = (f.ty === "Shot" && f.be) || (prev && KICKED.has(prev.ty)) ? d3.easeCubicOut : ease;
    glide(ballDot, "cx", "cy", () => X(f.bEff[0]), () => Y(f.bEff[1]), dur, ballEase);
    gVector.selectAll("line").remove();
    if (f.be) gVector.append("line")
      .attr("x1", X(f.b[0])).attr("y1", Y(f.b[1])).attr("x2", X(f.be[0])).attr("y2", Y(f.be[1]))
      .attr("stroke", colorFor(f.tm)).attr("stroke-width", 1.8).attr("stroke-dasharray", "3 3").attr("opacity", 0.9);

    const hints: any[] = [];
    if (f.ty === "Pass" && f.be) {
      let mover: any = null, bd = 15 * 15;
      for (const r of registry) {
        if (r.t !== f.tm || (r.a && r.age === 0)) continue;
        const dx = r.x - f.be[0], dy = r.y - f.be[1], d2 = dx * dx + dy * dy;
        if (d2 < bd) { bd = d2; mover = [r.x, r.y]; }
      }
      hints.push({ x: f.be[0], y: f.be[1], t: f.tm, from: f.po ? null : mover });
    }
    const hsel = gHints.selectAll("g.hint").data(hints);
    hsel.exit().remove();
    const hent = hsel.enter().append("g").attr("class", "hint");
    hent.append("line"); hent.append("circle").attr("r", 7).attr("fill-opacity", 0.15).attr("stroke-dasharray", "3 3")
      .attr("cx", (d: any) => X(d.x)).attr("cy", (d: any) => Y(d.y));
    const hm = hent.merge(hsel);
    hm.select("circle").attr("fill", (d: any) => colorFor(d.t)).attr("stroke", (d: any) => colorFor(d.t))
      .transition().duration(dur).ease(ease).attr("cx", (d: any) => X(d.x)).attr("cy", (d: any) => Y(d.y));
    hm.select("line").attr("stroke", (d: any) => colorFor(d.t)).attr("stroke-width", 1.6).attr("stroke-dasharray", "4 3")
      .attr("opacity", (d: any) => (d.from ? 0.75 : 0))
      .attr("x1", (d: any) => X(d.from ? d.from[0] : d.x)).attr("y1", (d: any) => Y(d.from ? d.from[1] : d.y))
      .attr("x2", (d: any) => X(d.x)).attr("y2", (d: any) => Y(d.y));

    renderTrail(k);

    const labels: any[] = [];
    if (f.pl) labels.push({ key: f.pl, pos: f.b, text: f.pl, team: f.tm });
    if (f.ty === "Pass" && f.pr && f.be) labels.push({ key: f.pr, pos: f.be, text: "→ " + f.pr, team: f.tm });
    const lx = (d: any) => Math.max(ox + 36, Math.min(ox + fw - 36, X(d.pos[0])));
    const ly = (d: any) => Math.max(oy + 14, Y(d.pos[1]) - 14);
    const lsel = gLabels.selectAll("text.pname:not(.exiting)").data(labels, (d: any) => d.key);
    lsel.exit().classed("exiting", true).interrupt("fade").interrupt("move")
      .transition().duration(120).attr("opacity", 0).remove();
    const lent = lsel.enter().append("text").attr("class", "pname").attr("x", lx).attr("y", ly).attr("opacity", 0);
    lent.merge(lsel).text((d: any) => d.text).attr("fill", (d: any) => colorFor(d.team))
      .call((s: any) => s.transition("fade").duration(dur).ease(ease).attr("opacity", 1))
      .call((s: any) => glide(s, "x", "y", lx, ly, dur, ease));

    let goal: any = null;
    for (let j = k; j >= 0 && j >= k - 6; j--) {
      const g = frames[j];
      if (g.pd !== f.pd || f.t - g.t > 6) break;
      if (g.gl) { goal = { f: g, dt: f.t - g.t }; break; }
    }
    const gsel = gGoal.selectAll("text.goal-banner").data(goal ? [goal] : []);
    gsel.exit().transition("fade").duration(300).attr("opacity", 0).remove();
    const gent = gsel.enter().append("text").attr("class", "goal-banner")
      .attr("x", ox + fw / 2).attr("y", oy + 44).attr("opacity", 0);
    if (goal) gent.merge(gsel).text(`GOAL · ${goal.f.pl}`).attr("fill", colorFor(goal.f.tm))
      .transition("fade").duration(Math.min(dur, 400)).attr("opacity", Math.max(0, 1 - goal.dt / 7));

    const tyLabel = (f.ty === "Pass" && f.pst && RESTARTS.has(f.pst)) ? f.pst : f.ty;
    const outcome = f.po ? ` · ${f.po === "Out" ? "out of play" : f.po.toLowerCase()}` : "";
    const clockStr = `${f.m}'${String(f.s).padStart(2, "0")}"`;
    const evStr = f.gl ? `GOAL! · ${f.pl} (${f.tm})`
      : `${tyLabel}${f.pl ? " · " + f.pl : ""}${f.pr ? " → " + f.pr : ""}${outcome} (${f.tm})`;
    eventLabel.html("");
    eventLabel.append("span").attr("class", "clk").text(clockStr);
    eventLabel.append("span").text(" · " + evStr);
    placePlayhead(f.tmin);
  }
  function goTo(k: number) { cur = Math.max(0, Math.min(frames.length - 1, k)); render(cur); }
  function delayFor(k: number) {
    let dt = Math.min(3, Math.max(0.04, frames[k + 1].t - frames[k].t));
    if (frames[k].gl) dt = Math.max(dt, 2.5);
    return (dt * 1000) / mult;
  }
  function scheduleNext() {
    if (cur >= frames.length - 1) { stop(); return; }
    const d = delayFor(cur);
    timer = d3.timeout(() => { cur++; render(cur, Math.min(d, 1500)); scheduleNext(); }, d);
  }
  function start() {
    if (cur >= frames.length - 1) cur = 0;
    playing = true; root.select("#play").text("❚❚ Pause"); scheduleNext();
  }
  function stop() { playing = false; root.select("#play").text("▶ Play"); if (timer) { timer.stop(); timer = null; } }
  function restartTimer() { if (playing) { timer.stop(); scheduleNext(); } }
  const isReceiptIntoCarry = (k: number) => {
    const f = frames[k], n = frames[k + 1];
    return !!(f && n && f.ty === "Ball Receipt*" && n.ty === "Carry" && n.pl === f.pl);
  };
  root.select("#play").on("click", () => (playing ? stop() : start()));
  function stepForward() {
    stop(); goTo(cur + 1);
    if (isReceiptIntoCarry(cur)) { const at = cur; d3.timeout(() => { if (!playing && cur === at) goTo(at + 1); }, 350); }
  }
  function stepBackward() { stop(); let k = cur - 1; if (isReceiptIntoCarry(k)) k--; goTo(k); }
  root.selectAll("#speeds button").on("click", function (this: any) {
    mult = +this.dataset.mult;
    root.selectAll("#speeds button").classed("active", false);
    d3.select(this).classed("active", true);
    restartTimer();
  });
  const keyHandler = (e: any) => {
    if (e.target && e.target.tagName === "INPUT") return;
    if (e.key === "ArrowRight") { e.preventDefault(); stepForward(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); stepBackward(); }
  };
  d3.select(window).on("keydown.sb360", keyHandler);
  root.select("#trailOn").on("change", function (this: any) { trailOn = this.checked; renderTrail(cur); });
  root.select("#trailLen").on("input", function (this: any) {
    trailLen = +this.value; root.select("#trailVal").text(`${trailLen} events`); renderTrail(cur);
  });

  drawMarkers();
  render(0);

  return () => {
    if (timer) { timer.stop(); timer = null; }
    d3.select(window).on("keydown.sb360", null);
    hideTip();
  };
}

// ============================================================================
// Passes & Shots (port of passes.html setup), scoped to `root`.
// ============================================================================
function renderPasses(root: any, tip: any, data: any): () => void {
  const M = data.match;
  const showTip = (html: string, x: number, y: number) =>
    tip.html(html).style("left", x + "px").style("top", y + "px").style("opacity", 1);
  const hideTip = () => tip.style("opacity", 0);

  // ---- shot map
  const S_PAD = { l: 16, r: 16, t: 30, b: 16 };
  const S_IW = 430, S_IH = S_IW * (40 / 80);
  const S_VW = S_IW + S_PAD.l + S_PAD.r, S_VH = S_IH + S_PAD.t + S_PAD.b;
  const S_UPX = S_IW / 80;
  const shotY = d3.scaleLinear().domain([0, 80]).range([S_PAD.l, S_PAD.l + S_IW]);
  const shotDepth = d3.scaleLinear().domain([0, 40]).range([S_PAD.t, S_PAD.t + S_IH]);
  const sPX = (y: number) => shotY(y);
  const sPY = (depth: number) => shotDepth(depth);
  const sProj = ([x, y]: number[]) => [sPX(y), sPY(120 - x)];
  const S_ON = new Set(["Goal", "Saved", "Saved to Post"]);
  const S_BLOCKED = new Set(["Blocked"]);
  let rShot: any;

  const shotcols = root.select("#shotcols"); shotcols.selectAll("*").remove();
  const shots = data.frames.filter((f: any) => f.ty === "Shot" && f.b && f.pd <= 4).map((f: any) => {
    const flip = f.tm !== M.home;
    const canon = (p: any) => (p ? (flip ? [120 - p[0], 80 - p[1]] : [p[0], p[1]]) : null);
    const dist = f.be ? Math.hypot(f.be[0] - f.b[0], f.be[1] - f.b[1]) : 0;
    return { ...f, bc: canon(f.b), bec: canon(f.be), dist, goal: f.so === "Goal",
      cls: S_BLOCKED.has(f.so) ? "blocked" : S_ON.has(f.so) ? "on" : "off" };
  });
  rShot = d3.scaleSqrt().domain([0, d3.max(shots, (s: any) => s.xg || 0) as number]).range([2, 13]);

  function drawShotPitch(svg: any) {
    svg.append("rect").attr("class", "pitchbg").attr("x", 0).attr("y", 0).attr("width", S_VW).attr("height", S_VH);
    const g = svg.append("g");
    const rect = (d0: number, d1: number, y0: number, y1: number) => g.append("rect").attr("class", "spl")
      .attr("x", sPX(y0)).attr("y", sPY(d0)).attr("width", sPX(y1) - sPX(y0)).attr("height", sPY(d1) - sPY(d0));
    g.append("path").attr("class", "spl")
      .attr("d", `M${sPX(0)},${sPY(0)} L${sPX(0)},${sPY(40)} L${sPX(80)},${sPY(40)} L${sPX(80)},${sPY(0)}`);
    g.append("line").attr("class", "spl").attr("x1", sPX(0)).attr("y1", sPY(0)).attr("x2", sPX(80)).attr("y2", sPY(0));
    rect(0, 18, 18, 62); rect(0, 6, 30, 50);
    g.append("rect").attr("class", "spl").attr("stroke-width", 2)
      .attr("x", sPX(36)).attr("y", sPY(0) - 2 * S_UPX).attr("width", sPX(44) - sPX(36)).attr("height", 2 * S_UPX);
    g.append("circle").attr("class", "pspot").attr("cx", sPX(40)).attr("cy", sPY(12)).attr("r", 1.8);
    const cx = sPX(40), cy = sPY(12), rp = 10 * S_UPX, edge = sPY(18), pts: number[][] = [];
    for (let a = 0; a <= 360; a += 2) {
      const px = cx + rp * Math.cos((a * Math.PI) / 180), py = cy + rp * Math.sin((a * Math.PI) / 180);
      if (py >= edge) pts.push([px, py]);
    }
    if (pts.length) g.append("path").attr("class", "spl").attr("d", "M" + pts.map((p) => p.join(",")).join("L"));
  }
  function renderShotPanel(col: any, list: any[], color: string) {
    const svg = col.append("div").attr("class", "panel pitchpanel").append("svg").attr("viewBox", `0 0 ${S_VW} ${S_VH}`);
    drawShotPitch(svg);
    const hits: any[] = [];
    for (const s of list) {
      const [x0, y0] = sProj(s.bc);
      const stroke = s.goal ? "var(--md-sun)" : color;
      const dash = s.cls === "off" ? "1 5" : s.cls === "blocked" ? "5 4" : null;
      let lineSel: any = null;
      if (s.bec) {
        const [x1, y1] = sProj(s.bec);
        lineSel = svg.append("path").attr("class", "shotline").attr("d", `M${x0},${y0} L${x1},${y1}`)
          .attr("stroke", stroke).attr("stroke-width", 1.6)
          .attr("stroke-linecap", s.cls === "off" ? "round" : "butt")
          .attr("stroke-dasharray", dash).attr("opacity", 0.9);
      }
      const r = rShot(s.xg || 0);
      const dot = svg.append("circle").attr("class", "shotdot").attr("cx", x0).attr("cy", y0).attr("r", r)
        .attr("fill", stroke).attr("opacity", s.goal ? 1 : 0.9);
      const pts = [{ x: x0, y: y0 }];
      if (lineSel) {
        const node = lineSel.node(), len = node.getTotalLength();
        for (let l = 6; l < len; l += 6) pts.push(node.getPointAtLength(l));
        pts.push(node.getPointAtLength(len));
      }
      hits.push({ s, line: lineSel, dot, r, pts });
    }
    let active: any = null;
    const clear = () => {
      if (!active) return;
      if (active.line) active.line.attr("stroke-width", 1.6).attr("opacity", 0.9);
      active.dot.attr("r", active.r).attr("stroke", "#fff").attr("stroke-width", 1).attr("opacity", active.s.goal ? 1 : 0.9);
      active = null; svg.classed("hot", false); hideTip();
    };
    svg.on("mousemove", function (this: any, ev: any) {
      const [mx, my] = d3.pointer(ev, this);
      let best: any = null, bestD = 9 * 9;
      for (const h of hits) for (const pt of h.pts) {
        const d = (pt.x - mx) ** 2 + (pt.y - my) ** 2; if (d < bestD) { bestD = d; best = h; }
      }
      if (best !== active) {
        clear();
        if (best) {
          active = best;
          if (best.line) best.line.attr("stroke-width", 3).attr("opacity", 1).raise();
          best.dot.attr("r", best.r + 1.5).attr("stroke", "var(--ink)").attr("stroke-width", 1.4).attr("opacity", 1).raise();
          svg.classed("hot", true);
        }
      }
      if (active) showTip(shotTipHtml(active.s), ev.clientX, ev.clientY);
    }).on("mouseleave", clear);
  }
  function shotTipHtml(s: any) {
    const meters = Math.round(s.dist * 0.875);
    const when = `${s.m}' <span class="dim">${PERIOD[s.pd] || ""}</span>`;
    const outcome = s.goal ? "<b>GOAL</b>" : (s.so || "Shot");
    const how = [`xG ${(s.xg || 0).toFixed(2)}`, s.bp || null, s.bec ? `${meters} m` : null].filter(Boolean).join(" · ");
    return `<b>${when}</b> ${s.pl || ""}<br>${outcome} · ${how}`;
  }
  for (const [team, color] of [[M.home, M.home_color], [M.away, M.away_color]] as any) {
    const list = shots.filter((s: any) => s.tm === team);
    const goals = list.filter((s: any) => s.goal).length;
    const xg = d3.sum(list, (s: any) => s.xg || 0);
    const col = shotcols.append("div");
    col.append("div").attr("class", "teamhead").html(
      `<span class="teamdot" style="background:${color}"></span>${team}` +
      `<span class="total">${list.length} shots · ${goals} goal${goals === 1 ? "" : "s"} · ${xg.toFixed(2)} xG</span>`);
    renderShotPanel(col, list, color);
  }
  const snote = root.select("#shotnote");
  snote.html(
    `Every shot at the spot it was struck, on each team's attacking third with the goal at the top. ` +
    `Dot area is <b>expected goals (xG)</b>, StatsBomb's chance-quality model. ` +
    `The line runs toward where the ball ended: <span class="sw">solid</span> on target, ` +
    `<span class="sw">dotted</span> off target, <span class="sw">dashed</span> blocked. ` +
    `<span class="sw" style="color:#caa500">Goals</span> are sun-yellow. ` +
    `${shots.length} shots, ${shots.filter((s: any) => s.goal).length} goals.`);
  const leg = snote.append("span").attr("class", "xglegend");
  const legSvg = leg.append("svg").attr("width", 150).attr("height", 34);
  let lx = 4;
  for (const v of [0.05, 0.2, 0.35]) {
    legSvg.append("circle").attr("cx", lx + 13).attr("cy", 18).attr("r", rShot(v))
      .attr("fill", "none").attr("stroke", "var(--muted)").attr("stroke-width", 1.2);
    legSvg.append("text").attr("x", lx + 13).attr("y", 33).attr("text-anchor", "middle").attr("class", "cap").text(v);
    lx += 46;
  }

  // ---- pass fingerprints
  const VW = 560, VH = 70, AX_L = 16, AX_R = VW - 16, BASE = VH - 14;
  const PEAK: Record<string, number> = { "Ground Pass": 0, "Low Pass": 13, "High Pass": 30 };
  const DIR_COLOR: Record<string, string> = { forward: "#16AA98", backward: "#FF7169", sideways: "#5b9ec9" };
  const cols = root.select("#cols"); cols.selectAll("*").remove();
  const acts = data.frames.filter((f: any) => (f.ty === "Pass" || f.ty === "Shot") && f.be && f.pd <= 4).map((f: any) => {
    const dx = f.be[0] - f.b[0], dy = f.be[1] - f.b[1], dist = Math.hypot(dx, dy);
    const cos = dist ? (dx * (f.tm === M.home ? 1 : -1)) / dist : 0;
    const dir = cos >= 0.5 ? "forward" : cos <= -0.5 ? "backward" : "sideways";
    return { ...f, dist, dir, shot: f.ty === "Shot" };
  });
  const byPlayer = d3.group(acts, (a: any) => a.tm, (a: any) => a.pl);
  const distScale = d3.scaleLinear().domain([0, d3.max(acts, (a: any) => a.dist) as number]).range([0, AX_R - AX_L]);

  function passTipHtml(a: any) {
    const meters = Math.round(a.dist * 0.875);
    const when = `${a.m}' <span class="dim">${PERIOD[a.pd] || ""}</span>`;
    const what = a.shot ? `Shot · ${a.so === "Goal" ? "<b>GOAL</b>" : (a.so || "")}`
      : a.po ? `Pass · <span class="dim">${a.po}</span>` : `Pass → ${a.pr || "?"}`;
    const how = [`${meters} m`, a.shot ? null : ((a.ph || "").replace(" Pass", "").toLowerCase() || null),
      a.bp || null, `<b style="color:${DIR_COLOR[a.dir]}">${a.dir}</b>`].filter(Boolean).join(" · ");
    return `<b>${when}</b> ${what}<br>${how}`;
  }
  function renderRow(col: any, p: any, color: string) {
    const nPass = p.list.filter((a: any) => !a.shot).length, nShot = p.list.length - nPass;
    const row = col.append("div").attr("class", "prow");
    row.append("div").attr("class", "lbl").html(
      `<span class="nm" style="color:${color}">${p.name}</span>` +
      `<span class="ct">${nPass} passes${nShot ? ` · ${nShot} shot${nShot > 1 ? "s" : ""}` : ""}</span>`);
    const svg = row.append("div").attr("class", "panel").append("svg").attr("viewBox", `0 0 ${VW} ${VH}`);
    svg.append("circle").attr("class", "anchor").attr("cx", AX_L).attr("cy", BASE).attr("r", 3.5);
    svg.append("circle").attr("class", "anchor").attr("cx", AX_R).attr("cy", BASE).attr("r", 3.5);
    const hits: any[] = [];
    for (const a of p.list) {
      const fromRight = a.bp === "Left Foot";
      const x0 = fromRight ? AX_R : AX_L;
      const x1 = x0 + (fromRight ? -1 : 1) * distScale(a.dist);
      let peak: number, yEnd = BASE;
      if (a.shot) { yEnd = BASE - Math.min(a.bez || 0, 6) * 5; peak = Math.max((BASE - yEnd) * 0.6, a.bez ? 6 : 0); }
      else peak = PEAK[a.ph] ?? 0;
      const path = peak
        ? `M${x0},${BASE} Q${(x0 + x1) / 2},${Math.min(BASE, yEnd) - 2 * peak} ${x1},${yEnd}`
        : `M${x0},${BASE} L${x1},${yEnd}`;
      const incomplete = !a.shot && !!a.po;
      const lineSel = svg.append("path").attr("class", "flight" + (a.shot ? " shot" : "")).attr("d", path)
        .attr("stroke-width", a.shot ? 1.6 : 1.2).attr("stroke-dasharray", a.shot ? null : "5 4")
        .attr("opacity", a.shot ? 0.8 : incomplete ? 0.22 : 0.55);
      if (!a.shot && a.bp && a.bp !== "Right Foot" && a.bp !== "Left Foot")
        svg.append("circle").attr("class", "headring").attr("cx", x0).attr("cy", BASE).attr("r", 6);
      if (a.shot && a.so === "Goal")
        svg.append("circle").attr("class", "goalmark").attr("cx", x1).attr("cy", yEnd).attr("r", 4);
      const node = lineSel.node(), len = node.getTotalLength(), pts: any[] = [];
      for (let l = 0; l <= len; l += 6) pts.push(node.getPointAtLength(l));
      pts.push(node.getPointAtLength(len));
      hits.push({ a, line: lineSel, pts, restWidth: a.shot ? 1.6 : 1.2, restOpacity: a.shot ? 0.8 : incomplete ? 0.22 : 0.55 });
    }
    let active: any = null;
    const clear = () => {
      if (!active) return;
      active.line.attr("stroke", null).attr("stroke-width", active.restWidth).attr("opacity", active.restOpacity);
      active = null; svg.classed("hot", false); hideTip();
    };
    svg.on("mousemove", function (this: any, ev: any) {
      const [mx, my] = d3.pointer(ev, this);
      let best: any = null, bestD = 7 * 7;
      for (const h of hits) for (const pt of h.pts) {
        const d = (pt.x - mx) ** 2 + (pt.y - my) ** 2; if (d < bestD) { bestD = d; best = h; }
      }
      if (best !== active) {
        clear();
        if (best) {
          active = best;
          best.line.attr("stroke", DIR_COLOR[best.a.dir]).attr("stroke-width", 2.4).attr("opacity", 1).raise();
          svg.selectAll(".goalmark").raise(); svg.classed("hot", true);
        }
      }
      if (active) showTip(passTipHtml(active.a), ev.clientX, ev.clientY);
    }).on("mouseleave", clear);
  }
  for (const [team, color] of [[M.home, M.home_color], [M.away, M.away_color]] as any) {
    const players = Array.from(byPlayer.get(team) || [], ([name, list]: any) => ({ name, list }))
      .sort((a: any, b: any) => b.list.length - a.list.length || d3.ascending(a.name, b.name));
    const col = cols.append("div");
    col.append("div").attr("class", "teamhead").html(
      `<span class="teamdot" style="background:${color}"></span>${team}` +
      `<span class="total">${d3.sum(players, (p: any) => p.list.length)} passes + shots</span>`);
    col.append("p").attr("class", "anchornote").text("left anchor: right foot, head & other · right anchor: left foot");
    for (const p of players) renderRow(col, p, color);
  }
  root.select("#passnote").html(
    `Every pass (dashed) and shot (solid) seen from the side: line length is distance on one shared scale, ` +
    `flat lines are ground passes, arcs are lofted; shots rise to the ball's recorded end height. ` +
    `Faint lines are incomplete or out of play; a ring at the anchor marks headers/other body parts; ` +
    `<span style="color:#FFDE00">●</span> caps a goal. Colored ` +
    `<span class="sw" style="color:${DIR_COLOR.forward}">forward</span>, ` +
    `<span class="sw" style="color:${DIR_COLOR.sideways}">sideways</span> or ` +
    `<span class="sw" style="color:${DIR_COLOR.backward}">backward</span>. ${acts.length} passes and shots.`);

  return () => hideTip();
}

// ============================================================================
// Dive component
// ============================================================================
export default function StatsBomb360() {
  const [matchId, setMatchId] = useDiveState<number>("match", 3869117);
  const [view, setView] = useDiveState<"replay" | "passes">("view", "replay");
  const replayRef = useRef<HTMLDivElement>(null);
  const passesRef = useRef<HTMLDivElement>(null);
  const tipRef = useRef<HTMLDivElement>(null);

  const matchesQ = useSQLQuery(`
    SELECT match_id, competition, season, stage, stadium, match_date,
           home_team, away_team, home_score, away_score
    FROM "statsbomb"."core"."matches" WHERE has_360
    ORDER BY competition, match_date, match_id
  `);
  const matches = Array.isArray(matchesQ.data) ? matchesQ.data : [];
  const sel = matches.find((m: any) => N(m.match_id) === N(matchId)) || matches[0];

  // searchable match picker (the title itself is the trigger)
  const [pickerOpen, setPickerOpen] = useState(false);
  const [q, setQ] = useState("");
  const pickerRef = useRef<HTMLDivElement>(null);
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return matches;
    return matches.filter((m: any) =>
      `${m.home_team} ${m.away_team} ${m.competition} ${m.season} ${m.stage}`.toLowerCase().includes(needle));
  }, [matches, q]);
  useEffect(() => {
    if (!pickerOpen) return;
    const onDoc = (e: any) => { if (pickerRef.current && !pickerRef.current.contains(e.target)) setPickerOpen(false); };
    const onKey = (e: any) => { if (e.key === "Escape") setPickerOpen(false); };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey); };
  }, [pickerOpen]);

  const eventsQ = useSQLQuery(`
    SELECT idx, minute, second, period, type, team, possession_team, ob, goal, player,
           pass_recipient, pass_outcome, pass_type, pass_height, body_part, shot_outcome,
           shot_end_z, b_x, b_y, be_x, be_y
    FROM "statsbomb"."marts"."replay_events" WHERE match_id = ${N(matchId)} ORDER BY idx
  `, { enabled: !!matchId });
  const dotsQ = useSQLQuery(`
    SELECT idx, dot, team, actor, keeper, x, y
    FROM "statsbomb"."marts"."replay_dots" WHERE match_id = ${N(matchId)} ORDER BY idx, dot
  `, { enabled: !!matchId });
  const markersQ = useSQLQuery(`
    SELECT minute, second, period, kind, team, player, label, b_x, b_y
    FROM "statsbomb"."marts"."replay_markers" WHERE match_id = ${N(matchId)} ORDER BY period, minute, second
  `, { enabled: !!matchId });
  const xgQ = useSQLQuery(`
    SELECT idx, shot_xg FROM "statsbomb"."core"."stg_events"
    WHERE match_id = ${N(matchId)} AND type = 'Shot'
  `, { enabled: !!matchId });

  const data = useMemo(() => {
    if (!sel) return null;
    if (!Array.isArray(eventsQ.data) || !Array.isArray(dotsQ.data) || !Array.isArray(markersQ.data)) return null;
    if (eventsQ.data.length === 0) return null;
    const xgByIdx: Record<number, number | null> = {};
    (Array.isArray(xgQ.data) ? xgQ.data : []).forEach((r: any) => { xgByIdx[N(r.idx)] = r.shot_xg != null ? N(r.shot_xg) : null; });
    const ppByIdx: Record<number, any[]> = {};
    for (const d of dotsQ.data as any[]) {
      const k = N(d.idx);
      (ppByIdx[k] = ppByIdx[k] || []).push({ p: [N(d.x), N(d.y)], t: String(d.team), a: !!d.actor, k: !!d.keeper });
    }
    const frames = (eventsQ.data as any[]).map((e) => {
      const i = N(e.idx);
      return {
        i, m: N(e.minute), s: N(e.second), pd: N(e.period), ty: S(e.type),
        tm: S(e.team), pt: S(e.possession_team), ob: e.ob ? 1 : undefined, gl: e.goal ? 1 : undefined,
        pl: S(e.player), pr: S(e.pass_recipient), po: S(e.pass_outcome), pst: S(e.pass_type),
        ph: S(e.pass_height), bp: S(e.body_part), so: S(e.shot_outcome),
        bez: e.shot_end_z != null ? N(e.shot_end_z) : null, xg: xgByIdx[i] ?? null,
        b: [N(e.b_x), N(e.b_y)], be: e.be_x != null ? [N(e.be_x), N(e.be_y)] : null, pp: ppByIdx[i] || null,
      };
    });
    const markers = (markersQ.data as any[]).map((m) => ({
      m: N(m.minute), s: N(m.second), pd: N(m.period), k: String(m.kind),
      tm: S(m.team), pl: S(m.player), lbl: String(m.label), b: m.b_x != null ? [N(m.b_x), N(m.b_y)] : null,
    }));
    const match = {
      home: String(sel.home_team), away: String(sel.away_team),
      home_score: N(sel.home_score), away_score: N(sel.away_score),
      home_color: HOME_COLOR, away_color: AWAY_COLOR,
      comp: `${sel.competition} ${sel.season}`, stage: String(sel.stage),
      date: String(sel.match_date), stadium: sel.stadium != null ? String(sel.stadium) : "",
    };
    return { match, frames, markers };
  }, [sel, eventsQ.data, dotsQ.data, markersQ.data, xgQ.data]);

  useEffect(() => {
    if (!data || !tipRef.current) return;
    const tip = d3.select(tipRef.current);
    if (view === "replay" && replayRef.current) return renderReplay(d3.select(replayRef.current), tip, data);
    if (view === "passes" && passesRef.current) return renderPasses(d3.select(passesRef.current), tip, data);
  }, [data, view]);

  const M = data?.match;
  const loading = matchesQ.isLoading || eventsQ.isLoading || dotsQ.isLoading;

  return (
    <div className="sb360">
      <style>{CSS}</style>
      <div className="wrap">
        <header className="top">
          <div className="matchpicker" ref={pickerRef}>
            <button className={"matchtitle" + (pickerOpen ? " open" : "")} onClick={() => setPickerOpen((o) => !o)} disabled={!matches.length}>
              {M ? `${M.home} vs ${M.away}` : "Loading…"}<span className="caret">▼</span>
            </button>
            {pickerOpen && (
              <div className="picker-panel">
                <input className="picker-search" autoFocus value={q} onChange={(e) => setQ(e.target.value)}
                  placeholder="Search team, competition…" />
                <div className="picker-list">
                  {filtered.map((m: any) => (
                    <button key={String(m.match_id)}
                      className={"picker-row" + (N(m.match_id) === N(matchId) ? " sel" : "")}
                      onClick={() => { setMatchId(N(m.match_id)); setPickerOpen(false); setQ(""); }}>
                      <span className="pr-teams">{m.home_team} {N(m.home_score)}-{N(m.away_score)} {m.away_team}</span>
                      <span className="pr-meta">{m.competition} · {m.stage} · {m.match_date}</span>
                    </button>
                  ))}
                  {filtered.length === 0 && <div className="picker-empty">No matches</div>}
                </div>
              </div>
            )}
          </div>
          {M && (
            <span className="score">
              <span className="teamdot" style={{ background: HOME_COLOR }} />{M.home_score}
              {" – "}{M.away_score}
              <span className="teamdot" style={{ background: AWAY_COLOR, marginLeft: 6 }} />
            </span>
          )}
          {M && <span className="meta">{M.comp ? M.comp + " · " : ""}{M.stage} · {M.date} · {M.stadium}</span>}
          <span className="tabs">
            <button className={view === "replay" ? "active" : ""} onClick={() => setView("replay")}>Replay</button>
            <button className={view === "passes" ? "active" : ""} onClick={() => setView("passes")}>Passes &amp; Shots</button>
          </span>
        </header>

        {!data && <p className="note">Loading match data…</p>}

        {view === "replay" && (
          <div ref={replayRef} style={{ display: data ? "block" : "none" }}>
            <div className="stage">
              <div className="side side-left">
                <div className="ctrls">
                  <button className="primary" id="play">▶ Play</button>
                  <span className="speeds" id="speeds">
                    <button data-mult="1" className="active">1×</button>
                    <button data-mult="2">2×</button>
                    <button data-mult="4">4×</button>
                  </span>
                  <span className="keys-hint">← → to step</span>
                </div>
                <span className="event-label" id="eventLabel" />
              </div>
              <svg id="pitch" viewBox="0 0 1280 720" preserveAspectRatio="xMidYMid meet" />
            </div>
            <svg id="timeline" />
            <div className="controls-row">
              <div className="mlegend" id="legend" />
              <span className="trail-ctl">
                <label><input type="checkbox" id="trailOn" defaultChecked /> Ball trail</label>
                <input type="range" id="trailLen" min={4} max={120} defaultValue={50} />
                <span className="val" id="trailVal">50 events</span>
              </span>
            </div>
            <p className="note" id="note" />
          </div>
        )}

        {view === "passes" && (
          <div ref={passesRef} style={{ display: data ? "block" : "none" }}>
            <div className="sec">Shots</div>
            <div className="cols" id="shotcols" />
            <p className="note" id="shotnote" />
            <div className="sec">Passes</div>
            <div className="cols" id="cols" />
            <p className="note" id="passnote" />
          </div>
        )}
      </div>
      <div className="sb360-tip" ref={tipRef} />
    </div>
  );
}
