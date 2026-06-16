/* global d3, topojson */

const WORLD_TOPO = "https://cdn.jsdelivr.net/npm/world-atlas@2/countries-110m.json";
const VOTE_POLL_MS = 5_000;

const state = {
  locations: [],
  candidates: [],
  totalVotes: 0,
  yourVote: null,
  selectedLocationId: null,
  sessionId: sessionIdForThisTab(),
};

function sessionIdForThisTab() {
  const key = "duckoffee:session";
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(key, id);
  }
  return id;
}

function formatMoney(n) {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(n);
}

function formatMoneyPrecise(n) {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(n);
}

async function fetchJSON(url, init) {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function init() {
  const mapEl = document.getElementById("map");
  const { projection, path, svg } = setupMap(mapEl);

  const world = await fetch(WORLD_TOPO).then((r) => r.json());
  drawWorld(svg, world, path);

  // Cafes + sales come from MotherDuck. If that call fails (bad token, outage,
  // etc.) we still want the map and the voting UI to work, so handle the
  // failure here instead of letting it abort init().
  try {
    state.locations = await fetchJSON("/api/locations");
    drawCafes(svg, projection);
    await refreshSummaryAndChart();
  } catch (err) {
    console.warn("cafes/sales unavailable", err);
    showStatsError();
  }

  await refreshVotes();
  setInterval(refreshVotes, VOTE_POLL_MS);

  document.getElementById("clear-filter").addEventListener("click", () => {
    selectLocation(null);
  });

  window.addEventListener("resize", () => {
    mapEl.innerHTML = "";
    const rebuilt = setupMap(mapEl);
    drawWorld(rebuilt.svg, world, rebuilt.path);
    drawCafes(rebuilt.svg, rebuilt.projection);
    drawCandidates(rebuilt.svg, rebuilt.projection);
    projectionRef.current = rebuilt.projection;
    svgRef.current = rebuilt.svg;
  });
}

function showStatsError() {
  const title = document.getElementById("stats-title");
  if (title) title.textContent = "Sales unavailable";
  const list = document.getElementById("top-products");
  if (list) {
    list.innerHTML =
      '<li style="list-style:none;padding:0;color:var(--md-ink-soft)">Cafe data could not be loaded. Check the Worker logs and your MotherDuck token, then refresh.</li>';
  }
}

const projectionRef = { current: null };
const svgRef = { current: null };

function setupMap(mapEl) {
  const width = mapEl.clientWidth;
  const height = mapEl.clientHeight;

  const svg = d3
    .select(mapEl)
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`)
    .attr("preserveAspectRatio", "xMidYMid meet");

  const projection = d3
    .geoNaturalEarth1()
    .scale(width / 6.3)
    .translate([width / 2, height / 1.85]);

  const path = d3.geoPath(projection);

  projectionRef.current = projection;
  svgRef.current = svg;

  return { projection, path, svg };
}

function drawWorld(svg, world, path) {
  const countries = topojson.feature(world, world.objects.countries);
  svg
    .append("g")
    .selectAll("path")
    .data(countries.features)
    .enter()
    .append("path")
    .attr("class", "country")
    .attr("d", path);

  const graticule = d3.geoGraticule();
  svg.append("path").datum(graticule).attr("class", "graticule").attr("d", path);
}

function drawCafes(svg, projection) {
  svg.selectAll(".cafe-layer").remove();
  const g = svg.append("g").attr("class", "cafe-layer");
  const tooltip = ensureTooltip();

  g.selectAll("circle")
    .data(state.locations.filter((d) => d.lat != null && d.lon != null))
    .enter()
    .append("circle")
    .attr("class", (d) =>
      d.location_id === state.selectedLocationId ? "cafe cafe--selected" : "cafe",
    )
    .attr("cx", (d) => projection([d.lon, d.lat])[0])
    .attr("cy", (d) => projection([d.lon, d.lat])[1])
    .attr("r", 6)
    .on("mouseenter", (event, d) => {
      tooltip.style.opacity = "1";
      tooltip.innerHTML = `<strong>${d.location_name}</strong><br>${d.city}, ${d.country} &middot; ${formatMoney(d.revenue)}`;
      positionTooltip(tooltip, event);
    })
    .on("mousemove", (event) => positionTooltip(tooltip, event))
    .on("mouseleave", () => {
      tooltip.style.opacity = "0";
    })
    .on("click", (_event, d) => {
      selectLocation(d.location_id === state.selectedLocationId ? null : d.location_id);
    });
}

function drawCandidates(svg, projection) {
  svg.selectAll(".candidate-layer").remove();
  if (!state.candidates.length) return;

  const g = svg.append("g").attr("class", "candidate-layer");
  const tooltip = ensureTooltip();
  const maxVotes = Math.max(1, ...state.candidates.map((c) => c.votes));

  const nodes = state.candidates.map((c) => {
    const [x, y] = projection([c.lon, c.lat]);
    return { ...c, x, y, radius: 7 + 9 * Math.sqrt(c.votes / maxVotes) };
  });

  g.selectAll(".candidate-pulse")
    .data(nodes.filter((n) => n.id === state.yourVote))
    .enter()
    .append("circle")
    .attr("class", "candidate-pulse")
    .attr("cx", (d) => d.x)
    .attr("cy", (d) => d.y)
    .attr("r", (d) => d.radius);

  const groups = g
    .selectAll(".candidate")
    .data(nodes)
    .enter()
    .append("g")
    .attr("class", (d) =>
      d.id === state.yourVote ? "candidate candidate--picked" : "candidate",
    )
    .attr("transform", (d) => `translate(${d.x},${d.y})`)
    .on("mouseenter", (event, d) => {
      tooltip.style.opacity = "1";
      const label = d.votes === 1 ? "vote" : "votes";
      const hint = d.id === state.yourVote ? " &middot; your pick" : " &middot; click to vote";
      tooltip.innerHTML = `<strong>${d.name}</strong><br>${d.country} &middot; ${d.votes} ${label}${hint}`;
      positionTooltip(tooltip, event);
    })
    .on("mousemove", (event) => positionTooltip(tooltip, event))
    .on("mouseleave", () => {
      tooltip.style.opacity = "0";
    })
    .on("click", (_event, d) => castVote(d.id));

  groups
    .append("circle")
    .attr("class", "candidate__dot")
    .attr("r", (d) => d.radius);

  groups
    .append("text")
    .attr("class", "candidate__label")
    .attr("text-anchor", "middle")
    .attr("dy", "0.35em")
    .text((d) => d.votes);
}

function ensureTooltip() {
  let tip = document.querySelector(".tooltip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "tooltip";
    document.body.appendChild(tip);
  }
  return tip;
}

function positionTooltip(tip, event) {
  tip.style.left = `${event.pageX}px`;
  tip.style.top = `${event.pageY}px`;
}

function selectLocation(locationId) {
  state.selectedLocationId = locationId;
  const svg = svgRef.current;
  if (svg) {
    svg
      .selectAll(".cafe")
      .attr("class", (d) =>
        d.location_id === state.selectedLocationId ? "cafe cafe--selected" : "cafe",
      );
  }
  const cleared = document.getElementById("clear-filter");
  cleared.hidden = locationId == null;
  const title = document.getElementById("stats-title");
  const loc = state.locations.find((l) => l.location_id === locationId);
  title.textContent = loc ? `${loc.location_name} (${loc.city})` : "Global sales";
  refreshSummaryAndChart();
}

function setStatsLoading(isLoading) {
  const loader = document.getElementById("stats-loader");
  if (!loader) return;
  loader.classList.toggle("is-visible", isLoading);
  loader.setAttribute("aria-hidden", isLoading ? "false" : "true");
}

async function refreshSummaryAndChart() {
  const q = state.selectedLocationId ? `?location_id=${state.selectedLocationId}` : "";
  setStatsLoading(true);
  try {
    const [summary, sales] = await Promise.all([
      fetchJSON(`/api/summary${q}`),
      fetchJSON(`/api/sales${q}${q ? "&" : "?"}days=90`),
    ]);
    renderSummary(summary);
    renderChart(sales.series);
  } catch (err) {
    console.error("failed to load data", err);
  } finally {
    setStatsLoading(false);
  }
}

function renderSummary(s) {
  document.getElementById("stat-orders").textContent = new Intl.NumberFormat().format(s.orders ?? 0);
  document.getElementById("stat-revenue").textContent = formatMoney(s.revenue ?? 0);
  document.getElementById("stat-avg").textContent = formatMoneyPrecise(s.avg_order ?? 0);

  const list = document.getElementById("top-products");
  list.innerHTML = "";
  (s.top_products || []).forEach((p) => {
    const li = document.createElement("li");
    li.innerHTML = `<strong>${p.product_name}</strong> &middot; ${new Intl.NumberFormat().format(p.sold)} sold`;
    list.appendChild(li);
  });
}

function renderChart(series) {
  const host = document.getElementById("chart");
  host.innerHTML = "";
  if (!series || series.length === 0) return;

  const width = host.clientWidth;
  const height = host.clientHeight;
  const margin = { top: 10, right: 10, bottom: 24, left: 48 };
  const innerW = width - margin.left - margin.right;
  const innerH = height - margin.top - margin.bottom;

  const svg = d3
    .select(host)
    .append("svg")
    .attr("viewBox", `0 0 ${width} ${height}`);

  const g = svg.append("g").attr("transform", `translate(${margin.left},${margin.top})`);

  const parsed = series.map((d) => ({ day: new Date(d.day), revenue: +d.revenue }));

  const x = d3.scaleTime()
    .domain(d3.extent(parsed, (d) => d.day))
    .range([0, innerW]);
  const y = d3.scaleLinear()
    .domain([0, d3.max(parsed, (d) => d.revenue) || 0])
    .nice()
    .range([innerH, 0]);

  g.append("g")
    .attr("class", "chart-axis")
    .attr("transform", `translate(0,${innerH})`)
    .call(d3.axisBottom(x).ticks(Math.min(6, parsed.length)).tickSizeOuter(0));

  g.append("g")
    .attr("class", "chart-axis")
    .call(d3.axisLeft(y).ticks(4).tickFormat((v) => formatMoney(v)));

  const area = d3.area()
    .x((d) => x(d.day))
    .y0(innerH)
    .y1((d) => y(d.revenue))
    .curve(d3.curveMonotoneX);

  const line = d3.line()
    .x((d) => x(d.day))
    .y((d) => y(d.revenue))
    .curve(d3.curveMonotoneX);

  g.append("path").datum(parsed).attr("class", "chart-area").attr("d", area);
  g.append("path").datum(parsed).attr("class", "chart-line").attr("d", line);

  g.selectAll(".chart-point")
    .data(parsed)
    .enter()
    .append("circle")
    .attr("class", "chart-point")
    .attr("cx", (d) => x(d.day))
    .attr("cy", (d) => y(d.revenue))
    .attr("r", 2);
}

async function refreshVotes() {
  try {
    const data = await fetchJSON(`/api/votes?session_id=${encodeURIComponent(state.sessionId)}`);
    state.candidates = data.candidates || [];
    state.totalVotes = data.total_votes || 0;
    state.yourVote = data.your_vote || null;
    document.getElementById("vote-count").textContent =
      new Intl.NumberFormat().format(state.totalVotes);
    renderLeaderboard();
    if (svgRef.current && projectionRef.current) {
      drawCandidates(svgRef.current, projectionRef.current);
    }
  } catch (err) {
    console.warn("vote refresh failed", err);
  }
}

async function castVote(candidateId) {
  state.yourVote = candidateId;
  try {
    await fetch("/api/votes", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, candidate_id: candidateId }),
    });
    await refreshVotes();
  } catch (err) {
    console.warn("vote failed", err);
  }
}

function renderLeaderboard() {
  const list = document.getElementById("leaderboard");
  const meta = document.getElementById("leaderboard-meta");
  if (!list || !meta) return;
  list.innerHTML = "";

  const sorted = [...state.candidates].sort(
    (a, b) => b.votes - a.votes || a.name.localeCompare(b.name),
  );
  const max = Math.max(1, ...sorted.map((c) => c.votes));

  sorted.forEach((c, i) => {
    const pct = (c.votes / max) * 100;
    const isPick = c.id === state.yourVote;
    const li = document.createElement("li");
    li.className = isPick ? "leaderboard__row leaderboard__row--picked" : "leaderboard__row";
    li.innerHTML = `
      <span class="leaderboard__rank">${i + 1}</span>
      <span class="leaderboard__name">${c.name}<span class="leaderboard__country"> &middot; ${c.country}</span></span>
      <span class="leaderboard__bar"><span class="leaderboard__bar-fill" style="width:${pct}%"></span></span>
      <span class="leaderboard__votes">${c.votes}</span>
    `;
    li.addEventListener("click", () => castVote(c.id));
    list.appendChild(li);
  });

  if (state.totalVotes === 0) {
    meta.textContent = "Be the first to vote.";
  } else if (state.yourVote) {
    const picked = state.candidates.find((c) => c.id === state.yourVote);
    meta.textContent = picked ? `You picked ${picked.name}.` : "Your vote is in.";
  } else {
    meta.textContent = `${state.totalVotes} vote${state.totalVotes === 1 ? "" : "s"} so far.`;
  }
}

init().catch((err) => {
  console.error("init failed", err);
  document.getElementById("map").innerHTML =
    '<p style="padding:1rem">Could not load the map. Check the Worker logs.</p>';
});
