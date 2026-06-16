import { verifyVercelSignature } from "./signature.js";
import { classify } from "./classify.js";
import { insertRows, type LogRow } from "./db.js";

const DRAIN_SECRET = process.env.VERCEL_DRAIN_SECRET;
if (!DRAIN_SECRET) {
  throw new Error("VERCEL_DRAIN_SECRET is required");
}

const IGNORED_PATH_EXTENSIONS = new Set([
  ".avif",
  ".bmp",
  ".css",
  ".eot",
  ".gif",
  ".ico",
  ".jpeg",
  ".jpg",
  ".js",
  ".map",
  ".mjs",
  ".otf",
  ".png",
  ".svg",
  ".ttf",
  ".tif",
  ".tiff",
  ".webp",
  ".woff",
  ".woff2",
]);

// When true, only rows classified as crawler / agent / human_via_ai are
// persisted. Human direct traffic is dropped. Keeps the table small on
// high-volume sites where you only care about AI traffic.
const BOTS_ONLY = (process.env.BOTS_ONLY ?? "false").toLowerCase() === "true";

export interface HandlerResult {
  status: number;
  body: string;
}

export async function handleDrain(
  rawBody: string,
  signature: string | undefined
): Promise<HandlerResult> {
  if (!verifyVercelSignature(rawBody, signature, DRAIN_SECRET!)) {
    return { status: 401, body: "invalid signature" };
  }

  const rows = parseAndClassify(rawBody);
  const toInsert = BOTS_ONLY ? rows.filter((r) => r.ai_category !== null) : rows;

  if (toInsert.length === 0) {
    return { status: 200, body: `ok 0 of ${rows.length}` };
  }

  try {
    await insertRows(toInsert);
  } catch (err) {
    // 5xx triggers Vercel to retry the batch.
    console.error("insert failed", err);
    return { status: 503, body: "write failed" };
  }

  return { status: 200, body: `ok ${toInsert.length} of ${rows.length}` };
}

function parseAndClassify(body: string): LogRow[] {
  const now = new Date();
  const rows: LogRow[] = [];

  // Vercel log drains deliver NDJSON. Some projects are configured to emit
  // a JSON array instead; accept both.
  const trimmed = body.trim();
  const items: unknown[] = trimmed.startsWith("[")
    ? JSON.parse(trimmed)
    : splitNdjson(trimmed);

  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    const line = item as Record<string, unknown>;

    const userAgent = pickString(line, [
      "proxy.userAgent",
      "userAgent",
      "request.headers.user-agent",
    ]);
    const referer = pickString(line, [
      "proxy.referer",
      "referer",
      "request.headers.referer",
    ]);

    const eventTs = parseTimestamp(line.timestamp) ?? now;
    const { category, name } = classify(userAgent, referer);
    const path = pickString(line, ["proxy.path", "path"]);

    if (shouldSkipPath(path)) {
      continue;
    }

    rows.push({
      event_id: asString(line.id),
      received_at: now,
      event_ts: eventTs,
      event_hour: new Date(Math.floor(eventTs.getTime() / 3_600_000) * 3_600_000),
      project_id: asString(line.projectId),
      deployment_id: asString(line.deploymentId),
      source: asString(line.source),
      host: pickString(line, ["proxy.host", "host"]),
      path,
      method: pickString(line, ["proxy.method", "method"]),
      status_code: pickNumber(line, ["proxy.statusCode", "statusCode"]),
      user_agent: userAgent,
      referer,
      client_ip: anonymizeIp(pickString(line, ["proxy.clientIp", "clientIp"])),
      region: asString(line.region),
      request_id: pickString(line, ["proxy.requestId", "requestId"]),
      ai_category: category,
      ai_name: name,
      raw: JSON.stringify(line),
    });
  }

  return rows;
}

function splitNdjson(s: string): unknown[] {
  const out: unknown[] = [];
  for (const line of s.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try {
      out.push(JSON.parse(t));
    } catch {
      // Skip malformed lines; Vercel retries on 5xx, so a single bad line
      // should not drop the rest of the batch.
    }
  }
  return out;
}

function pathLookup(obj: Record<string, unknown>, path: string): unknown {
  const parts = path.split(".");
  let cur: unknown = obj;
  for (const p of parts) {
    if (cur && typeof cur === "object" && p in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return undefined;
    }
  }
  return cur;
}

function pickString(obj: Record<string, unknown>, paths: string[]): string | null {
  for (const p of paths) {
    const v = pathLookup(obj, p);
    if (typeof v === "string" && v.length > 0) return v;
    if (Array.isArray(v)) {
      for (const item of v) {
        if (typeof item === "string" && item.length > 0) return item;
      }
    }
  }
  return null;
}

function pickNumber(obj: Record<string, unknown>, paths: string[]): number | null {
  for (const p of paths) {
    const v = pathLookup(obj, p);
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && /^\d+$/.test(v)) return Number(v);
  }
  return null;
}

function asString(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function parseTimestamp(v: unknown): Date | null {
  if (typeof v === "number") return new Date(v);
  if (typeof v === "string") {
    const d = new Date(v);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  return null;
}

function shouldSkipPath(path: string | null): boolean {
  if (!path) return false;

  const normalizedPath = path.split("?")[0]?.split("#")[0] ?? path;
  const lowerPath = normalizedPath.toLowerCase();

  if (lowerPath === "/_next/image") {
    return true;
  }

  for (const ext of IGNORED_PATH_EXTENSIONS) {
    if (lowerPath.endsWith(ext)) {
      return true;
    }
  }

  return false;
}

function anonymizeIp(ip: string | null): string | null {
  if (ip === null) return null;

  const parts = ip.split(".");
  if (parts.length !== 4) {
    return ip;
  }

  const octets = parts.map((part) => Number(part));
  if (octets.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) {
    return ip;
  }

  return `${octets[0]}.${octets[1]}.${octets[2]}.0`;
}
