import { Client, type QueryResult } from "pg";
import { DurableObject } from "cloudflare:workers";

export interface Env {
  MOTHERDUCK_HOST: string;
  MOTHERDUCK_DB: string;
  MOTHERDUCK_TOKEN: string;
  DUCKOFFEE_SHARE: string;
  ASSETS: Fetcher;
  VOTES: DurableObjectNamespace<VoteTracker>;
}

// Lat/lon for the cities present in the duckoffee.locations table.
// The sample data has city names but no coordinates, so we geocode here.
const CITY_COORDS: Record<string, [number, number]> = {
  Amsterdam: [4.9041, 52.3676],
  Barcelona: [2.1734, 41.3851],
  Berlin: [13.405, 52.52],
  "Cape Town": [18.4241, -33.9249],
  Istanbul: [28.9784, 41.0082],
  London: [-0.1276, 51.5072],
  Milan: [9.19, 45.4642],
  "New York": [-74.006, 40.7128],
  Paris: [2.3522, 48.8566],
  "San Francisco": [-122.4194, 37.7749],
  "Sao Paulo": [-46.6333, -23.5505],
  Sydney: [151.2093, -33.8688],
  Tokyo: [139.6917, 35.6895],
  Vienna: [16.3738, 48.2082],
};

// Candidate cities where Duckoffee could open next. Chosen for geographic
// spread and to not overlap with the existing `duckoffee.locations` set.
type Candidate = { id: string; name: string; country: string; lon: number; lat: number };
const CANDIDATES: Candidate[] = [
  { id: "mexico-city", name: "Mexico City", country: "Mexico", lon: -99.1332, lat: 19.4326 },
  { id: "toronto", name: "Toronto", country: "Canada", lon: -79.3832, lat: 43.6532 },
  { id: "buenos-aires", name: "Buenos Aires", country: "Argentina", lon: -58.3816, lat: -34.6037 },
  { id: "lisbon", name: "Lisbon", country: "Portugal", lon: -9.1393, lat: 38.7223 },
  { id: "copenhagen", name: "Copenhagen", country: "Denmark", lon: 12.5683, lat: 55.6761 },
  { id: "dubai", name: "Dubai", country: "UAE", lon: 55.2708, lat: 25.2048 },
  { id: "mumbai", name: "Mumbai", country: "India", lon: 72.8777, lat: 19.076 },
  { id: "bangkok", name: "Bangkok", country: "Thailand", lon: 100.5018, lat: 13.7563 },
  { id: "seoul", name: "Seoul", country: "South Korea", lon: 126.978, lat: 37.5665 },
  { id: "lagos", name: "Lagos", country: "Nigeria", lon: 3.3792, lat: 6.5244 },
];
const CANDIDATE_IDS = new Set(CANDIDATES.map((c) => c.id));

async function withClient<T>(env: Env, fn: (c: Client) => Promise<T>): Promise<T> {
  const connectionString = `postgresql://anyusername:${env.MOTHERDUCK_TOKEN}@${env.MOTHERDUCK_HOST}:5432/${env.MOTHERDUCK_DB}?sslmode=require`;
  const client = new Client({ connectionString });
  await client.connect();
  try {
    await client.query(`ATTACH IF NOT EXISTS '${env.DUCKOFFEE_SHARE}' AS duckoffee`);
    return await fn(client);
  } finally {
    await client.end();
  }
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const DATA_CACHE_TTL_SECONDS = 15 * 60;

async function cached(
  req: Request,
  ctx: ExecutionContext,
  produce: () => Promise<Response>,
): Promise<Response> {
  const cache = caches.default;
  const cacheKey = new Request(req.url, { method: "GET" });
  const hit = await cache.match(cacheKey);
  if (hit) return hit;

  const fresh = await produce();
  if (fresh.ok) {
    const toStore = new Response(fresh.clone().body, fresh);
    toStore.headers.set("cache-control", `public, max-age=${DATA_CACHE_TTL_SECONDS}`);
    ctx.waitUntil(cache.put(cacheKey, toStore));
  }
  return fresh;
}

async function handleLocations(env: Env): Promise<Response> {
  const result: QueryResult = await withClient(env, (c) =>
    c.query(`
      SELECT
        l.location_id,
        l.location_name,
        l.city,
        l.country,
        l.venue_type,
        COALESCE(sum(o.order_total), 0) AS revenue,
        count(o.order_id) AS orders
      FROM duckoffee.locations l
      LEFT JOIN duckoffee.orders o ON o.location_id = l.location_id
      GROUP BY ALL
      ORDER BY l.location_name
    `),
  );

  const rows = result.rows.map((row: any) => {
    const coords = CITY_COORDS[row.city] ?? null;
    return {
      location_id: Number(row.location_id),
      location_name: row.location_name,
      city: row.city,
      country: row.country,
      venue_type: row.venue_type,
      revenue: Number(row.revenue),
      orders: Number(row.orders),
      lon: coords?.[0] ?? null,
      lat: coords?.[1] ?? null,
    };
  });

  return json(rows);
}

async function handleSales(env: Env, url: URL): Promise<Response> {
  const locationParam = url.searchParams.get("location_id");
  const daysParam = url.searchParams.get("days") ?? "90";

  const days = Math.min(Math.max(parseInt(daysParam, 10) || 90, 7), 365);
  const locationId = locationParam ? parseInt(locationParam, 10) : null;
  if (locationParam && (locationId === null || Number.isNaN(locationId))) {
    return json({ error: "Invalid location_id" }, 400);
  }

  const result: QueryResult = await withClient(env, (c) =>
    c.query(
      `
      SELECT
        date_trunc('day', ordered_at)::DATE AS day,
        round(sum(order_total), 2) AS revenue,
        count(*)::INTEGER AS orders
      FROM duckoffee.orders
      WHERE ordered_at >= (
        SELECT max(ordered_at) - ($1::INTEGER * INTERVAL '1 day') FROM duckoffee.orders
      )
        AND ($2::BIGINT IS NULL OR location_id = $2::BIGINT)
      GROUP BY 1
      ORDER BY 1
      `,
      [days, locationId],
    ),
  );

  const rows = result.rows.map((r: any) => ({
    day: r.day instanceof Date ? r.day.toISOString().slice(0, 10) : String(r.day),
    revenue: Number(r.revenue),
    orders: Number(r.orders),
  }));

  return json({ days, location_id: locationId, series: rows });
}

async function handleSummary(env: Env, url: URL): Promise<Response> {
  const locationParam = url.searchParams.get("location_id");
  const locationId = locationParam ? parseInt(locationParam, 10) : null;
  if (locationParam && (locationId === null || Number.isNaN(locationId))) {
    return json({ error: "Invalid location_id" }, 400);
  }

  const result: QueryResult = await withClient(env, (c) =>
    c.query(
      `
      SELECT
        count(*)::INTEGER AS orders,
        round(sum(order_total), 2) AS revenue,
        round(avg(order_total), 2) AS avg_order
      FROM duckoffee.orders
      WHERE $1::BIGINT IS NULL OR location_id = $1::BIGINT
      `,
      [locationId],
    ),
  );

  const top: QueryResult = await withClient(env, (c) =>
    c.query(
      `
      SELECT oi.product_name, sum(oi.quantity)::INTEGER AS sold
      FROM duckoffee.order_items oi
      JOIN duckoffee.orders o ON o.order_id = oi.order_id
      WHERE $1::BIGINT IS NULL OR o.location_id = $1::BIGINT
      GROUP BY 1
      ORDER BY sold DESC
      LIMIT 5
      `,
      [locationId],
    ),
  );

  return json({
    location_id: locationId,
    ...result.rows[0],
    top_products: top.rows.map((r: any) => ({
      product_name: r.product_name,
      sold: Number(r.sold),
    })),
  });
}

async function handleVotesGet(req: Request, env: Env): Promise<Response> {
  const url = new URL(req.url);
  const sessionId = url.searchParams.get("session_id");
  const stub = env.VOTES.get(env.VOTES.idFromName("global"));
  const { tallies, total, yourVote } = await stub.snapshot(sessionId);
  const candidates = CANDIDATES.map((c) => ({ ...c, votes: tallies[c.id] ?? 0 }));
  return json({ candidates, total_votes: total, your_vote: yourVote });
}

async function handleVoteCast(req: Request, env: Env): Promise<Response> {
  const body = (await req.json().catch(() => ({}))) as {
    session_id?: string;
    candidate_id?: string;
  };
  const sessionId = body.session_id;
  const candidateId = body.candidate_id;
  if (!sessionId || typeof sessionId !== "string" || sessionId.length > 64) {
    return json({ error: "Missing or invalid session_id" }, 400);
  }
  if (!candidateId || !CANDIDATE_IDS.has(candidateId)) {
    return json({ error: "Invalid candidate_id" }, 400);
  }
  const stub = env.VOTES.get(env.VOTES.idFromName("global"));
  await stub.cast(sessionId, candidateId);
  return json({ ok: true, your_vote: candidateId });
}

export default {
  async fetch(req: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(req.url);

    try {
      if (url.pathname === "/api/locations") {
        return await cached(req, ctx, () => handleLocations(env));
      }
      if (url.pathname === "/api/sales") {
        return await cached(req, ctx, () => handleSales(env, url));
      }
      if (url.pathname === "/api/summary") {
        return await cached(req, ctx, () => handleSummary(env, url));
      }
      if (url.pathname === "/api/votes" && req.method === "GET") {
        return await handleVotesGet(req, env);
      }
      if (url.pathname === "/api/votes" && req.method === "POST") {
        return await handleVoteCast(req, env);
      }
    } catch (err) {
      return json({ error: "Query failed", detail: String(err) }, 502);
    }

    return env.ASSETS.fetch(req);
  },
};

type TallyRow = {
  candidate_id: string;
  c: number;
  [key: string]: SqlStorageValue;
};

type MyVoteRow = {
  candidate_id: string;
  [key: string]: SqlStorageValue;
};

export class VoteTracker extends DurableObject<Env> {
  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    ctx.storage.sql.exec(`
      CREATE TABLE IF NOT EXISTS votes (
        session_id TEXT PRIMARY KEY,
        candidate_id TEXT NOT NULL,
        cast_at INTEGER NOT NULL
      )
    `);
  }

  async cast(sessionId: string, candidateId: string): Promise<void> {
    this.ctx.storage.sql.exec(
      `INSERT INTO votes (session_id, candidate_id, cast_at)
       VALUES (?, ?, ?)
       ON CONFLICT(session_id) DO UPDATE SET
         candidate_id = excluded.candidate_id,
         cast_at = excluded.cast_at`,
      sessionId,
      candidateId,
      Date.now(),
    );
  }

  async snapshot(
    sessionId: string | null,
  ): Promise<{ tallies: Record<string, number>; total: number; yourVote: string | null }> {
    const rows = this.ctx.storage.sql
      .exec<TallyRow>(`SELECT candidate_id, count(*) AS c FROM votes GROUP BY candidate_id`)
      .toArray();

    const tallies: Record<string, number> = {};
    let total = 0;
    for (const r of rows) {
      const n = Number(r.c);
      tallies[r.candidate_id] = n;
      total += n;
    }

    let yourVote: string | null = null;
    if (sessionId) {
      const mine = this.ctx.storage.sql
        .exec<MyVoteRow>(
          `SELECT candidate_id FROM votes WHERE session_id = ? LIMIT 1`,
          sessionId,
        )
        .toArray();
      if (mine.length > 0) yourVote = mine[0].candidate_id;
    }

    return { tallies, total, yourVote };
  }
}
