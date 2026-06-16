import { useState, useEffect } from "react";
import { useSQLQuery } from "@motherduck/react-sql-query";
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";


export const REQUIRED_DATABASES = [
  { type: 'database', path: 'md:my_db', alias: 'my_db' }
];


const N = (v) => (v != null ? Number(v) : 0);

const TARGET_OPTIONS = ["motherduck.com", "duckdb.org"];
const MART_DATABASE = `"__DBT_DATABASE__"`;
const MART_SCHEMA = `"__DBT_MART_SCHEMA__"`;
const mart = (table) => `${MART_DATABASE}.${MART_SCHEMA}."${table}"`;

export default function DomainBacklinksDive() {
  // Persisted filter state
  const [targetDomain, setTargetDomain] = useState(() => {
    try {
      const v = localStorage.getItem("dive.backlinks.target");
      return TARGET_OPTIONS.includes(v) ? v : "motherduck.com";
    } catch { return "motherduck.com"; }
  });
  const [selectedLinking, setSelectedLinking] = useState(() => {
    try { return localStorage.getItem("dive.backlinks.linking") || ""; } catch { return ""; }
  });
  const PAGE_SIZE = 15;
  const [page, setPage] = useState(0);
  const STORIES_PAGE_SIZE = 10;
  const [storiesPage, setStoriesPage] = useState(0);
  const [storiesSortBy, setStoriesSortBy] = useState(() => {
    try { return localStorage.getItem("dive.backlinks.storiesSort") || "points"; } catch { return "points"; }
  });
  const storiesSortClause = storiesSortBy === "date" ? "created_at DESC" : "score DESC";
  const [sortBy, setSortBy] = useState(() => {
    try { return localStorage.getItem("dive.backlinks.sort") || "pagerank"; } catch { return "pagerank"; }
  });
  const sortColumn = sortBy === "harmonic" ? "linking_harmonicc_rank" : "linking_pagerank_rank";

  // Reset pagination when target or sort changes
  useEffect(() => { setPage(0); }, [targetDomain, sortBy]);
  useEffect(() => { setStoriesPage(0); }, [targetDomain, selectedLinking, storiesSortBy]);

  useEffect(() => {
    try { localStorage.setItem("dive.backlinks.target", targetDomain); } catch {}
  }, [targetDomain]);
  useEffect(() => {
    try { localStorage.setItem("dive.backlinks.sort", sortBy); } catch {}
  }, [sortBy]);
  useEffect(() => {
    try { localStorage.setItem("dive.backlinks.storiesSort", storiesSortBy); } catch {}
  }, [storiesSortBy]);
  useEffect(() => {
    try { localStorage.setItem("dive.backlinks.linking", selectedLinking); } catch {}
  }, [selectedLinking]);

  // ===== Queries =====

  // KPI summary for the target domain
  const kpiQuery = useSQLQuery(`
    WITH bl AS (
      SELECT COUNT(*) AS backlink_count,
             MIN(linking_pagerank_rank) AS top_pr_rank,
             arg_min(linking_domain, linking_pagerank_rank) AS top_pr_domain
      FROM ${mart("mart_domain_backlinks")}
      WHERE target_domain = '${targetDomain}'
    ),
    hn AS (
      SELECT COUNT(*) AS story_count,
             COALESCE(SUM(score), 0) AS total_score
      FROM ${mart("mart_hackernews_domain_stories")}
      WHERE domain = '${targetDomain}'
    )
    SELECT * FROM bl, hn
  `);

  // Total count for pagination
  const countQuery = useSQLQuery(`
    SELECT COUNT(*) AS total
    FROM ${mart("mart_domain_backlinks")}
    WHERE target_domain = '${targetDomain}'
  `);

  // Global HN data range — used in the masthead
  const dataRangeQuery = useSQLQuery(`
    SELECT
      strftime(MIN(created_at), '%Y') AS min_year,
      strftime(MAX(created_at), '%Y') AS max_year
    FROM ${mart("mart_hackernews_domain_stories")}
  `);

  // Paginated backlinks — annotated with whether the linking domain has HN stories
  const backlinksQuery = useSQLQuery(`
    SELECT
      b.linking_domain,
      b.linking_pagerank_rank,
      b.linking_harmonicc_rank,
      b.linking_host_count,
      COALESCE(h.story_count, 0) AS hn_story_count
    FROM ${mart("mart_domain_backlinks")} b
    LEFT JOIN (
      SELECT domain, COUNT(*) AS story_count
      FROM ${mart("mart_hackernews_domain_stories")}
      GROUP BY 1
    ) h ON h.domain = b.linking_domain
    WHERE b.target_domain = '${targetDomain}'
    ORDER BY b.${sortColumn} ASC NULLS LAST
    LIMIT ${PAGE_SIZE} OFFSET ${page * PAGE_SIZE}
  `);

  // HN story trend over time — target vs selected linking domain.
  // Month range is derived from the two selected domains' actual data span so it
  // covers the full mart (2020 → latest), not a hardcoded year.
  const trendQuery = useSQLQuery(`
    WITH bounds AS (
      SELECT
        date_trunc('month', MIN(created_at)) AS min_month,
        date_trunc('month', MAX(created_at)) AS max_month
      FROM ${mart("mart_hackernews_domain_stories")}
      WHERE domain IN ('${targetDomain}', '${selectedLinking}')
    ),
    months AS (
      SELECT t.d AS month_date, strftime(t.d, '%Y-%m') AS month
      FROM bounds, generate_series(bounds.min_month, bounds.max_month, INTERVAL 1 MONTH) AS t(d)
    )
    SELECT
      m.month,
      COALESCE(SUM(CASE WHEN s.domain = '${targetDomain}' THEN s.score END), 0) AS target_score,
      COALESCE(COUNT(CASE WHEN s.domain = '${targetDomain}' THEN 1 END), 0) AS target_count,
      COALESCE(SUM(CASE WHEN s.domain = '${selectedLinking}' THEN s.score END), 0) AS linking_score,
      COALESCE(COUNT(CASE WHEN s.domain = '${selectedLinking}' THEN 1 END), 0) AS linking_count
    FROM months m
    LEFT JOIN ${mart("mart_hackernews_domain_stories")} s
      ON date_trunc('month', s.created_at) = m.month_date
      AND s.domain IN ('${targetDomain}', '${selectedLinking}')
    GROUP BY m.month, m.month_date
    ORDER BY m.month_date
  `, { enabled: !!selectedLinking });

  // Total story count for pagination (filtered by target + selected linking domain)
  const storiesCountQuery = useSQLQuery(`
    SELECT COUNT(*) AS total
    FROM ${mart("mart_hackernews_domain_stories")}
    WHERE domain IN ('${targetDomain}', '${selectedLinking}')
  `, { enabled: !!selectedLinking });

  // Top HN stories from both target and selected linking domain
  const storiesQuery = useSQLQuery(`
    SELECT
      domain,
      title,
      url,
      story_id,
      score,
      author,
      strftime(created_at, '%Y-%m-%d') AS date_str
    FROM ${mart("mart_hackernews_domain_stories")}
    WHERE domain IN ('${targetDomain}', '${selectedLinking}')
    ORDER BY ${storiesSortClause}
    LIMIT ${STORIES_PAGE_SIZE} OFFSET ${storiesPage * STORIES_PAGE_SIZE}
  `, { enabled: !!selectedLinking });

  // Auto-default selectedLinking to the top-PageRank linking domain that has HN stories
  useEffect(() => {
    if (!selectedLinking && Array.isArray(backlinksQuery.data) && backlinksQuery.data.length > 0) {
      const withHN = backlinksQuery.data.find(r => N(r.hn_story_count) > 0);
      if (withHN) setSelectedLinking(withHN.linking_domain);
      else setSelectedLinking(backlinksQuery.data[0].linking_domain);
    }
  }, [backlinksQuery.data, selectedLinking]);

  // Derived data
  const kpi = Array.isArray(kpiQuery.data) && kpiQuery.data[0] ? kpiQuery.data[0] : null;
  const backlinks = Array.isArray(backlinksQuery.data) ? backlinksQuery.data : [];
  const trendData = Array.isArray(trendQuery.data) ? trendQuery.data.map(r => ({
    month: r.month,
    target_score: N(r.target_score),
    linking_score: N(r.linking_score),
    target_count: N(r.target_count),
    linking_count: N(r.linking_count),
  })) : [];
  const stories = Array.isArray(storiesQuery.data) ? storiesQuery.data : [];
  const totalCount = Array.isArray(countQuery.data) && countQuery.data[0] ? N(countQuery.data[0].total) : 0;
  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));
  const storiesTotalCount = Array.isArray(storiesCountQuery.data) && storiesCountQuery.data[0] ? N(storiesCountQuery.data[0].total) : 0;
  const storiesTotalPages = Math.max(1, Math.ceil(storiesTotalCount / STORIES_PAGE_SIZE));
  const dataRange = Array.isArray(dataRangeQuery.data) && dataRangeQuery.data[0]
    ? (dataRangeQuery.data[0].min_year === dataRangeQuery.data[0].max_year
        ? dataRangeQuery.data[0].min_year
        : `${dataRangeQuery.data[0].min_year}–${dataRangeQuery.data[0].max_year}`)
    : "…";

  // ===== MotherDuck brand tokens (modern brutalism) =====
  const MONO = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" };
  const LABEL = { ...MONO, fontSize: "10px", letterSpacing: "0.14em", textTransform: "uppercase" };
  const LABEL_LG = { ...MONO, fontSize: "11px", letterSpacing: "0.14em", textTransform: "uppercase" };
  const TAB = { fontVariantNumeric: "tabular-nums" };
  // Primary palette
  const SAND = "#F4EFEA";
  const SNOW = "#F8F7F7";
  const INK = "#383838";
  const MUTED = "#6a6a6a";
  // Secondary palette
  const SKY = "#6FC2FF";
  const GARDEN = "#16AA98";
  const SUN = "#FFDE00";
  const WATERMELON = "#FF7169";
  // Roles
  const ACCENT = WATERMELON;
  const ACCENT_2 = GARDEN;
  // Brutalist tokens
  const SHADOW = `6px 6px 0 ${INK}`;
  const SHADOW_SM = `4px 4px 0 ${INK}`;
  const RADIUS = "12px";
  const RADIUS_SM = "10px";
  const BORDER = `2px solid ${INK}`;
  const RULE_HAIR = "1px solid rgba(56,56,56,0.12)";

  // KPI card configuration
  const kpiCards = [
    { label: "Linking domains", bg: WATERMELON, fg: INK, value: kpi ? N(kpi.backlink_count).toLocaleString() : null },
    { label: "Top backlink",    bg: SKY,        fg: INK, value: kpi ? (kpi.top_pr_domain || "—") : null, sub: kpi ? `PageRank #${N(kpi.top_pr_rank).toLocaleString()}` : null, isText: true },
    { label: "HN stories",      bg: SUN,        fg: INK, value: kpi ? N(kpi.story_count).toLocaleString() : null },
    { label: "HN total score",  bg: GARDEN,     fg: SNOW, value: kpi ? N(kpi.total_score).toLocaleString() : null },
  ];

  // Brutalist button hover handlers
  const lift = (e, lifted) => {
    e.currentTarget.style.transform = lifted ? "translate(-1px,-1px)" : "";
    e.currentTarget.style.boxShadow = lifted ? `5px 5px 0 ${INK}` : SHADOW_SM;
  };

  return (
    <div style={{ background: SAND, minHeight: "100vh", color: INK }}>
      <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "20px 32px 56px" }}>

        {/* ====== Masthead (compact) ====== */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingBottom: "14px", borderBottom: BORDER }}>
          <span style={{ ...LABEL_LG, fontWeight: 700, color: INK }}>§ Domain Analytics</span>
          <span style={{ ...LABEL_LG, color: MUTED }}>Common Crawl · Hacker News · {dataRange}</span>
        </div>

        {/* ====== Hero (tight, title + selector inline) ====== */}
        <div className="grid grid-cols-12 gap-6" style={{ marginTop: "24px", marginBottom: "32px", alignItems: "end" }}>
          <div className="col-span-8">
            <h1 style={{ fontSize: "52px", fontWeight: 700, lineHeight: 0.95, letterSpacing: "-0.03em", color: INK, margin: 0 }}>
              Backlinks &amp;<br />HN coverage.
            </h1>
            <p style={{ color: MUTED, fontSize: "14px", lineHeight: 1.5, maxWidth: "540px", margin: "14px 0 0 0" }}>
              Who links into the target, what their PageRank looks like, and how the
              target&rsquo;s Hacker News footprint compares against a chosen referrer.
            </p>
          </div>
          <div className="col-span-4" style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "10px" }}>
            <span style={{ ...LABEL_LG, color: MUTED }}>Select target</span>
            <div style={{ display: "flex", flexDirection: "column", gap: "10px", alignItems: "flex-end" }}>
              {TARGET_OPTIONS.map((opt) => {
                const active = targetDomain === opt;
                return (
                  <button
                    key={opt}
                    onClick={() => { setTargetDomain(opt); setSelectedLinking(""); }}
                    style={{
                      background: active ? WATERMELON : SNOW,
                      color: INK,
                      border: BORDER,
                      borderRadius: RADIUS_SM,
                      padding: "9px 16px",
                      boxShadow: SHADOW_SM,
                      ...MONO,
                      fontSize: "13px",
                      fontWeight: 700,
                      cursor: "pointer",
                      transition: "transform 0.1s, box-shadow 0.1s",
                      minWidth: "170px",
                      textAlign: "center",
                    }}
                    onMouseEnter={(e) => lift(e, true)}
                    onMouseLeave={(e) => lift(e, false)}
                  >{opt}</button>
                );
              })}
            </div>
          </div>
        </div>

        {/* ====== KPI grid (bold colored cards with offset shadows) ====== */}
        <div className="grid grid-cols-4 gap-5" style={{ marginBottom: "64px" }}>
          {kpiCards.map((card, i) => (
            <div key={i} style={{
              background: card.bg,
              border: BORDER,
              borderRadius: RADIUS,
              boxShadow: SHADOW,
              padding: "20px 22px",
              minHeight: "140px",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
            }}>
              <div style={{ ...LABEL, color: card.fg, fontWeight: 700 }}>{card.label}</div>
              {kpiQuery.isLoading || card.value === null ? (
                <div style={{ height: "44px", width: "60%", background: "rgba(56,56,56,0.12)", borderRadius: "4px" }} />
              ) : card.isText ? (
                <div>
                  <div style={{ fontSize: "22px", fontWeight: 700, lineHeight: 1.05, color: card.fg, letterSpacing: "-0.01em" }}>{card.value}</div>
                  {card.sub && <div style={{ ...LABEL, color: card.fg, marginTop: "6px", ...TAB, opacity: 0.75 }}>{card.sub}</div>}
                </div>
              ) : (
                <div style={{ fontSize: "44px", fontWeight: 700, lineHeight: 1, letterSpacing: "-0.02em", ...TAB, color: card.fg }}>{card.value}</div>
              )}
            </div>
          ))}
        </div>

        {/* ====== Section 01: Linking domains (paginated) ====== */}
        <div style={{ marginBottom: "72px" }}>
          <div style={{ marginBottom: "20px", display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "16px", flexWrap: "wrap" }}>
            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: "12px", marginBottom: "6px" }}>
                <span style={{ ...LABEL_LG, color: WATERMELON, fontWeight: 700 }}>§ 01</span>
                <span style={{ ...LABEL_LG, color: MUTED }}>Click a row to compare</span>
              </div>
              <h2 style={{ fontSize: "30px", fontWeight: 700, color: INK, letterSpacing: "-0.02em", lineHeight: 1.1, margin: 0 }}>
                Linking domains
              </h2>
            </div>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" }}>
              <span style={{ ...LABEL, color: MUTED }}>Sort by</span>
              <div style={{ display: "flex", gap: "6px" }}>
                {[
                  { id: "pagerank", label: "PageRank" },
                  { id: "harmonic", label: "Harmonic" },
                ].map((opt) => {
                  const active = sortBy === opt.id;
                  return (
                    <button
                      key={opt.id}
                      onClick={() => setSortBy(opt.id)}
                      style={{
                        background: active ? WATERMELON : SNOW,
                        color: INK,
                        border: BORDER,
                        borderRadius: RADIUS_SM,
                        padding: "7px 14px",
                        boxShadow: SHADOW_SM,
                        ...MONO,
                        fontSize: "11px",
                        fontWeight: 700,
                        letterSpacing: "0.05em",
                        cursor: "pointer",
                        transition: "transform 0.1s, box-shadow 0.1s",
                      }}
                      onMouseEnter={(e) => lift(e, true)}
                      onMouseLeave={(e) => lift(e, false)}
                    >{opt.label}</button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Brutalist card containing the table */}
          <div style={{ background: SNOW, border: BORDER, borderRadius: RADIUS, boxShadow: SHADOW, overflow: "hidden" }}>
            {backlinksQuery.isLoading ? (
              <div style={{ padding: "12px" }}>
                {[0,1,2,3,4,5,6,7].map(i => <div key={i} style={{ height: "42px", background: "rgba(56,56,56,0.05)", marginBottom: "4px", borderRadius: "4px" }} />)}
              </div>
            ) : (
              <table className="w-full" style={{ borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: `2px solid ${INK}`, background: SAND }}>
                    <th style={{ ...LABEL, color: INK, textAlign: "left",  padding: "12px 16px", width: "56px", fontWeight: 700 }}>Nº</th>
                    <th style={{ ...LABEL, color: INK, textAlign: "left",  padding: "12px 8px", fontWeight: 700 }}>Linking domain</th>
                    <th style={{ ...LABEL, color: sortBy === "pagerank" ? INK : MUTED, textAlign: "right", padding: "12px 8px", fontWeight: 700 }}>PageRank{sortBy === "pagerank" ? " ↓" : ""}</th>
                    <th style={{ ...LABEL, color: sortBy === "harmonic" ? INK : MUTED, textAlign: "right", padding: "12px 8px", fontWeight: 700 }}>Harmonic{sortBy === "harmonic" ? " ↓" : ""}</th>
                    <th style={{ ...LABEL, color: INK, textAlign: "right", padding: "12px 8px", fontWeight: 700 }}>Hosts</th>
                    <th style={{ ...LABEL, color: INK, textAlign: "right", padding: "12px 16px", width: "70px", fontWeight: 700 }}>HN</th>
                  </tr>
                </thead>
                <tbody>
                  {backlinks.map((row, i) => {
                    const isSelected = row.linking_domain === selectedLinking;
                    const rowNum = page * PAGE_SIZE + i + 1;
                    return (
                      <tr
                        key={i}
                        onClick={() => setSelectedLinking(row.linking_domain)}
                        style={{
                          borderBottom: i < backlinks.length - 1 ? RULE_HAIR : "none",
                          cursor: "pointer",
                          background: isSelected ? "rgba(255,113,105,0.18)" : "transparent",
                          transition: "background 0.1s",
                        }}
                        onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "rgba(255,113,105,0.07)"; }}
                        onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
                      >
                        <td style={{ padding: "14px 16px", ...MONO, fontSize: "11px", color: MUTED, ...TAB, position: "relative" }}>
                          {isSelected && <span style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: "4px", background: WATERMELON }} />}
                          {String(rowNum).padStart(3, "0")}
                        </td>
                        <td style={{ padding: "14px 8px", fontSize: "15px", color: INK, fontWeight: isSelected ? 700 : 500 }}>
                          <span style={{ display: "inline-flex", alignItems: "center", gap: "10px" }}>
                            <span>{row.linking_domain}</span>
                            <a
                              href={`https://www.google.com/search?q=${encodeURIComponent(`site:${row.linking_domain} ${targetDomain}`)}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              title={`Search Google: site:${row.linking_domain} ${targetDomain}`}
                              style={{
                                ...MONO,
                                fontSize: "10px",
                                fontWeight: 700,
                                letterSpacing: "0.1em",
                                color: MUTED,
                                textDecoration: "none",
                                padding: "3px 7px",
                                border: "1px solid rgba(56,56,56,0.25)",
                                borderRadius: "4px",
                                lineHeight: 1.2,
                                whiteSpace: "nowrap",
                                transition: "all 0.1s",
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.color = INK;
                                e.currentTarget.style.borderColor = INK;
                                e.currentTarget.style.background = WATERMELON;
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.color = MUTED;
                                e.currentTarget.style.borderColor = "rgba(56,56,56,0.25)";
                                e.currentTarget.style.background = "transparent";
                              }}
                            >GOOGLE ↗</a>
                          </span>
                        </td>
                        <td style={{ padding: "14px 8px", ...MONO, fontSize: "13px", textAlign: "right", color: sortBy === "pagerank" ? INK : MUTED, ...TAB, fontWeight: sortBy === "pagerank" ? 600 : 400 }}>
                          {N(row.linking_pagerank_rank).toLocaleString()}
                        </td>
                        <td style={{ padding: "14px 8px", ...MONO, fontSize: "13px", textAlign: "right", color: sortBy === "harmonic" ? INK : MUTED, ...TAB, fontWeight: sortBy === "harmonic" ? 600 : 400 }}>
                          {N(row.linking_harmonicc_rank).toLocaleString()}
                        </td>
                        <td style={{ padding: "14px 8px", ...MONO, fontSize: "13px", textAlign: "right", color: MUTED, ...TAB }}>
                          {N(row.linking_host_count).toLocaleString()}
                        </td>
                        <td style={{ padding: "14px 16px", ...MONO, fontSize: "13px", textAlign: "right", color: N(row.hn_story_count) > 0 ? INK : "#bdbdbd", ...TAB, fontWeight: N(row.hn_story_count) > 0 ? 600 : 400 }}>
                          {N(row.hn_story_count) > 0 ? N(row.hn_story_count) : "·"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* Pagination controls (brutalist) */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "20px", flexWrap: "wrap", gap: "12px" }}>
            <span style={{ ...LABEL_LG, color: MUTED, ...TAB }}>
              {countQuery.isLoading || totalCount === 0 ? "…" : `Showing ${(page * PAGE_SIZE + 1).toLocaleString()}–${Math.min((page + 1) * PAGE_SIZE, totalCount).toLocaleString()} of ${totalCount.toLocaleString()}`}
            </span>
            <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
              <button
                disabled={page === 0}
                onClick={() => setPage(p => Math.max(0, p - 1))}
                style={{
                  background: SNOW, color: INK, border: BORDER, borderRadius: RADIUS_SM,
                  padding: "9px 16px", boxShadow: page === 0 ? "none" : SHADOW_SM,
                  ...MONO, fontSize: "12px", fontWeight: 700,
                  cursor: page === 0 ? "not-allowed" : "pointer",
                  opacity: page === 0 ? 0.4 : 1,
                  transition: "transform 0.1s, box-shadow 0.1s",
                }}
                onMouseEnter={(e) => { if (page !== 0) lift(e, true); }}
                onMouseLeave={(e) => { if (page !== 0) lift(e, false); }}
              >← PREV</button>
              <span style={{ ...LABEL_LG, color: INK, ...TAB, padding: "0 6px", fontWeight: 700 }}>
                {page + 1} / {totalPages}
              </span>
              <button
                disabled={page >= totalPages - 1}
                onClick={() => setPage(p => p + 1)}
                style={{
                  background: SNOW, color: INK, border: BORDER, borderRadius: RADIUS_SM,
                  padding: "9px 16px", boxShadow: page >= totalPages - 1 ? "none" : SHADOW_SM,
                  ...MONO, fontSize: "12px", fontWeight: 700,
                  cursor: page >= totalPages - 1 ? "not-allowed" : "pointer",
                  opacity: page >= totalPages - 1 ? 0.4 : 1,
                  transition: "transform 0.1s, box-shadow 0.1s",
                }}
                onMouseEnter={(e) => { if (page < totalPages - 1) lift(e, true); }}
                onMouseLeave={(e) => { if (page < totalPages - 1) lift(e, false); }}
              >NEXT →</button>
            </div>
          </div>
        </div>

        {/* ====== Section 02: HN trend ====== */}
        <div style={{ marginBottom: "72px" }}>
          <div style={{ marginBottom: "20px" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: "12px", marginBottom: "6px" }}>
              <span style={{ ...LABEL_LG, color: WATERMELON, fontWeight: 700 }}>§ 02</span>
              <span style={{ ...LABEL_LG, color: MUTED }}>{selectedLinking ? `${targetDomain} vs ${selectedLinking} · monthly total score` : "Select a referrer to compare"}</span>
            </div>
            <h2 style={{ fontSize: "30px", fontWeight: 700, color: INK, letterSpacing: "-0.02em", lineHeight: 1.1, margin: 0 }}>
              HN score over time
            </h2>
          </div>

          {/* Legend */}
          <div style={{ display: "flex", gap: "24px", marginBottom: "14px" }}>
            <span style={{ ...LABEL, color: INK, display: "flex", alignItems: "center", gap: "8px", fontWeight: 700 }}>
              <span style={{ width: "22px", height: "4px", background: WATERMELON, display: "inline-block", borderRadius: "2px" }} />
              {targetDomain}
            </span>
            {selectedLinking && (
              <span style={{ ...LABEL, color: INK, display: "flex", alignItems: "center", gap: "8px", fontWeight: 700 }}>
                <span style={{ width: "22px", height: "4px", background: GARDEN, display: "inline-block", borderRadius: "2px" }} />
                {selectedLinking}
              </span>
            )}
          </div>

          <div style={{ background: SNOW, border: BORDER, borderRadius: RADIUS, boxShadow: SHADOW, padding: "20px" }}>
            {!selectedLinking ? (
              <div style={{ height: "300px", color: MUTED, fontSize: "14px", display: "flex", alignItems: "center", justifyContent: "center" }}>
                Select a linking domain in section 01
              </div>
            ) : trendQuery.isLoading ? (
              <div style={{ height: 300, background: "rgba(56,56,56,0.05)", borderRadius: "8px" }} />
            ) : (
              <ResponsiveContainer width="100%" height={300}>
                <LineChart data={trendData} margin={{ top: 8, right: 24, bottom: 8, left: 0 }}>
                  <CartesianGrid stroke="rgba(56,56,56,0.08)" vertical={false} strokeDasharray="0" />
                  <XAxis dataKey="month" tick={{ fontSize: 10, fill: MUTED, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }} stroke={INK} tickLine={false} />
                  <YAxis tick={{ fontSize: 10, fill: MUTED, fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace" }} stroke="rgba(56,56,56,0.15)" tickLine={false} axisLine={false} tickFormatter={(v) => Number(v).toLocaleString()} />
                  <Tooltip
                    contentStyle={{ background: SNOW, border: BORDER, borderRadius: RADIUS_SM, fontSize: "12px", padding: "10px 12px", boxShadow: SHADOW_SM }}
                    labelStyle={{ ...LABEL, color: MUTED, marginBottom: "6px" }}
                    itemStyle={{ padding: 0 }}
                    formatter={(value, name, props) => {
                      const isTarget = props.dataKey === "target_score";
                      const count = isTarget ? props.payload.target_count : props.payload.linking_count;
                      return [`${N(value).toLocaleString()} pts · ${N(count)} stories`, name];
                    }}
                  />
                  <Line type="linear" dataKey="target_score" name={targetDomain} stroke={WATERMELON} strokeWidth={3} dot={{ r: 4, fill: WATERMELON, stroke: INK, strokeWidth: 1.5 }} activeDot={{ r: 6, fill: WATERMELON, stroke: INK, strokeWidth: 2 }} />
                  <Line type="linear" dataKey="linking_score" name={selectedLinking} stroke={GARDEN} strokeWidth={3} dot={{ r: 4, fill: GARDEN, stroke: INK, strokeWidth: 1.5 }} activeDot={{ r: 6, fill: GARDEN, stroke: INK, strokeWidth: 2 }} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* ====== Section 03: Top stories ====== */}
        <div style={{ marginBottom: "48px" }}>
          <div style={{ marginBottom: "20px", display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "16px", flexWrap: "wrap" }}>
            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: "12px", marginBottom: "6px" }}>
                <span style={{ ...LABEL_LG, color: WATERMELON, fontWeight: 700 }}>§ 03</span>
                <span style={{ ...LABEL_LG, color: MUTED }}>{storiesSortBy === "date" ? "Most recent HN posts from target & referrer" : "Highest-scoring HN posts from target & referrer"}</span>
              </div>
              <h2 style={{ fontSize: "30px", fontWeight: 700, color: INK, letterSpacing: "-0.02em", lineHeight: 1.1, margin: 0 }}>
                Top stories
              </h2>
            </div>
            <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "6px" }}>
              <span style={{ ...LABEL, color: MUTED }}>Sort by</span>
              <div style={{ display: "flex", gap: "6px" }}>
                {[
                  { id: "points", label: "Points" },
                  { id: "date", label: "Date" },
                ].map((opt) => {
                  const active = storiesSortBy === opt.id;
                  return (
                    <button
                      key={opt.id}
                      onClick={() => setStoriesSortBy(opt.id)}
                      style={{
                        background: active ? WATERMELON : SNOW,
                        color: INK,
                        border: BORDER,
                        borderRadius: RADIUS_SM,
                        padding: "7px 14px",
                        boxShadow: SHADOW_SM,
                        ...MONO,
                        fontSize: "11px",
                        fontWeight: 700,
                        letterSpacing: "0.05em",
                        cursor: "pointer",
                        transition: "transform 0.1s, box-shadow 0.1s",
                      }}
                      onMouseEnter={(e) => lift(e, true)}
                      onMouseLeave={(e) => lift(e, false)}
                    >{opt.label}</button>
                  );
                })}
              </div>
            </div>
          </div>

          {!selectedLinking ? (
            <div style={{ background: SNOW, border: BORDER, borderRadius: RADIUS, boxShadow: SHADOW, padding: "32px", color: MUTED, fontSize: "14px" }}>
              Select a linking domain in section 01.
            </div>
          ) : storiesQuery.isLoading ? (
            <div style={{ background: SNOW, border: BORDER, borderRadius: RADIUS, boxShadow: SHADOW, padding: "12px" }}>
              {[0,1,2,3,4,5].map(i => <div key={i} style={{ height: "58px", background: "rgba(56,56,56,0.05)", marginBottom: "4px", borderRadius: "4px" }} />)}
            </div>
          ) : stories.length === 0 ? (
            <div style={{ background: SNOW, border: BORDER, borderRadius: RADIUS, boxShadow: SHADOW, padding: "32px", color: MUTED, fontSize: "14px" }}>
              No HN stories for {targetDomain} or {selectedLinking}.
            </div>
          ) : (
            <ol style={{ listStyle: "none", padding: 0, margin: 0, background: SNOW, border: BORDER, borderRadius: RADIUS, boxShadow: SHADOW, overflow: "hidden" }}>
              {stories.map((s, i) => {
                const isTarget = s.domain === targetDomain;
                const dotColor = isTarget ? WATERMELON : GARDEN;
                return (
                  <li key={i} style={{ borderBottom: i < stories.length - 1 ? RULE_HAIR : "none", padding: "16px 20px" }}>
                    <div className="grid grid-cols-12 gap-4" style={{ alignItems: "baseline" }}>
                      <div className="col-span-1" style={{ ...LABEL, color: MUTED, ...TAB, fontWeight: 600 }}>
                        {String(storiesPage * STORIES_PAGE_SIZE + i + 1).padStart(2, "0")}
                      </div>
                      <div className="col-span-2" style={{ ...LABEL, color: INK, display: "flex", alignItems: "center", gap: "8px", fontWeight: 700 }}>
                        <span style={{ width: "10px", height: "10px", background: dotColor, borderRadius: "50%", display: "inline-block", flexShrink: 0, border: `1.5px solid ${INK}` }} />
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.domain}</span>
                      </div>
                      <div className="col-span-7">
                        <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap" }}>
                          {s.url ? (
                            <a
                              href={s.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              style={{ color: INK, fontSize: "15px", textDecoration: "none", borderBottom: "1.5px solid transparent", lineHeight: 1.4, fontWeight: 500 }}
                              onMouseEnter={(e) => { e.currentTarget.style.borderBottomColor = WATERMELON; }}
                              onMouseLeave={(e) => { e.currentTarget.style.borderBottomColor = "transparent"; }}
                            >{s.title}</a>
                          ) : (
                            <span style={{ color: INK, fontSize: "15px", lineHeight: 1.4, fontWeight: 500 }}>{s.title}</span>
                          )}
                          {s.story_id && (
                            <a
                              href={`https://news.ycombinator.com/item?id=${s.story_id}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              title="Open HN discussion"
                              style={{
                                ...MONO,
                                fontSize: "10px",
                                fontWeight: 700,
                                letterSpacing: "0.1em",
                                color: MUTED,
                                textDecoration: "none",
                                padding: "3px 7px",
                                border: "1px solid rgba(56,56,56,0.25)",
                                borderRadius: "4px",
                                lineHeight: 1.2,
                                whiteSpace: "nowrap",
                                transition: "all 0.1s",
                              }}
                              onMouseEnter={(e) => {
                                e.currentTarget.style.color = INK;
                                e.currentTarget.style.borderColor = INK;
                                e.currentTarget.style.background = SUN;
                              }}
                              onMouseLeave={(e) => {
                                e.currentTarget.style.color = MUTED;
                                e.currentTarget.style.borderColor = "rgba(56,56,56,0.25)";
                                e.currentTarget.style.background = "transparent";
                              }}
                            >HN ↗</a>
                          )}
                        </div>
                        <div style={{ ...LABEL, color: MUTED, marginTop: "6px" }}>by {s.author}</div>
                      </div>
                      <div className="col-span-1" style={{ textAlign: "right", ...MONO, fontSize: "15px", color: storiesSortBy === "points" ? INK : MUTED, fontWeight: storiesSortBy === "points" ? 700 : 500, ...TAB }}>
                        {N(s.score).toLocaleString()}
                      </div>
                      <div className="col-span-1" style={{ textAlign: "right", ...LABEL, color: storiesSortBy === "date" ? INK : MUTED, fontWeight: storiesSortBy === "date" ? 700 : 400, ...TAB }}>
                        {s.date_str}
                      </div>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}

          {/* Pagination controls (brutalist) */}
          {selectedLinking && (
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "20px", flexWrap: "wrap", gap: "12px" }}>
              <span style={{ ...LABEL_LG, color: MUTED, ...TAB }}>
                {storiesCountQuery.isLoading || storiesTotalCount === 0 ? "…" : `Showing ${(storiesPage * STORIES_PAGE_SIZE + 1).toLocaleString()}–${Math.min((storiesPage + 1) * STORIES_PAGE_SIZE, storiesTotalCount).toLocaleString()} of ${storiesTotalCount.toLocaleString()}`}
              </span>
              <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
                <button
                  disabled={storiesPage === 0}
                  onClick={() => setStoriesPage(p => Math.max(0, p - 1))}
                  style={{
                    background: SNOW, color: INK, border: BORDER, borderRadius: RADIUS_SM,
                    padding: "9px 16px", boxShadow: storiesPage === 0 ? "none" : SHADOW_SM,
                    ...MONO, fontSize: "12px", fontWeight: 700,
                    cursor: storiesPage === 0 ? "not-allowed" : "pointer",
                    opacity: storiesPage === 0 ? 0.4 : 1,
                    transition: "transform 0.1s, box-shadow 0.1s",
                  }}
                  onMouseEnter={(e) => { if (storiesPage !== 0) lift(e, true); }}
                  onMouseLeave={(e) => { if (storiesPage !== 0) lift(e, false); }}
                >← PREV</button>
                <span style={{ ...LABEL_LG, color: INK, ...TAB, padding: "0 6px", fontWeight: 700 }}>
                  {storiesPage + 1} / {storiesTotalPages}
                </span>
                <button
                  disabled={storiesPage >= storiesTotalPages - 1}
                  onClick={() => setStoriesPage(p => p + 1)}
                  style={{
                    background: SNOW, color: INK, border: BORDER, borderRadius: RADIUS_SM,
                    padding: "9px 16px", boxShadow: storiesPage >= storiesTotalPages - 1 ? "none" : SHADOW_SM,
                    ...MONO, fontSize: "12px", fontWeight: 700,
                    cursor: storiesPage >= storiesTotalPages - 1 ? "not-allowed" : "pointer",
                    opacity: storiesPage >= storiesTotalPages - 1 ? 0.4 : 1,
                    transition: "transform 0.1s, box-shadow 0.1s",
                  }}
                  onMouseEnter={(e) => { if (storiesPage < storiesTotalPages - 1) lift(e, true); }}
                  onMouseLeave={(e) => { if (storiesPage < storiesTotalPages - 1) lift(e, false); }}
                >NEXT →</button>
              </div>
            </div>
          )}
        </div>

        {/* ====== Footer ====== */}
        <div style={{ borderTop: BORDER, paddingTop: "12px", display: "flex", justifyContent: "space-between", ...LABEL_LG, color: MUTED }}>
          <span>__DBT_DATABASE__.__DBT_MART_SCHEMA__</span>
          <span>End — Domain Analytics</span>
        </div>

      </div>
    </div>
  );
}
