import { DuckDBInstance, DuckDBConnection } from "@duckdb/node-api";

const TOKEN = process.env.MOTHERDUCK_TOKEN;
const { database: DATABASE, schema: SCHEMA } = resolveDestination();
const TABLE = process.env.MD_TABLE ?? "vercel_request_logs";
const HOME = process.env.HOME?.trim() || "/tmp";
const EXTENSION_DIRECTORY =
  process.env.DUCKDB_EXTENSION_DIRECTORY ?? `${HOME}/.duckdb/extensions`;

if (!TOKEN) {
  // Fail fast at cold start rather than on the first request.
  throw new Error("MOTHERDUCK_TOKEN is required");
}

// Vercel's Node runtime can present HOME as unset/empty. MotherDuck needs a
// writable extension cache, so pin both HOME and the extension directory to
// the writable temp volume before DuckDB initializes.
process.env.HOME = HOME;

// Fully qualified target, escaped to survive schema/table names with unusual
// characters. Built once; env vars are read at cold start.
const TARGET = [DATABASE, SCHEMA, TABLE].map(quoteIdent).join(".");

// Module-scoped so warm invocations reuse the MotherDuck connection.
// First invocation pays a ~500 ms-1 s cold start while the extension loads.
let connPromise: Promise<DuckDBConnection> | null = null;

export function getConnection(): Promise<DuckDBConnection> {
  if (!connPromise) {
    connPromise = (async () => {
      // Connect without a default database so CREATE DATABASE IF NOT EXISTS
      // works on the very first cold start.
      const instance = await DuckDBInstance.fromCache(`md:`, {
        motherduck_token: TOKEN!,
        extension_directory: EXTENSION_DIRECTORY,
      });
      const conn = await instance.connect();
      await ensureSchema(conn);
      return conn;
    })().catch((err) => {
      connPromise = null;
      throw err;
    });
  }
  return connPromise;
}

// Idempotent bootstrap: creates the database, schema, table, and
// `ai_requests` view if they are missing. Mirrors sql/01_setup.sql.
async function ensureSchema(conn: DuckDBConnection): Promise<void> {
  const db = quoteIdent(DATABASE);
  const schema = quoteIdent(SCHEMA);
  const table = quoteIdent(TABLE);
  await conn.run(`CREATE DATABASE IF NOT EXISTS ${db}`);
  await conn.run(`USE ${db}`);
  await conn.run(`CREATE SCHEMA IF NOT EXISTS ${schema}`);
  await conn.run(`
    CREATE TABLE IF NOT EXISTS ${db}.${schema}.${table} (
      event_id        VARCHAR,
      received_at     TIMESTAMP,
      event_ts        TIMESTAMP,
      event_hour      TIMESTAMP,
      project_id      VARCHAR,
      deployment_id   VARCHAR,
      source          VARCHAR,
      host            VARCHAR,
      path            VARCHAR,
      method          VARCHAR,
      status_code     INTEGER,
      user_agent      VARCHAR,
      referer         VARCHAR,
      client_ip       VARCHAR,
      region          VARCHAR,
      request_id      VARCHAR,
      ai_category     VARCHAR,
      ai_name         VARCHAR,
      raw             JSON
    )
  `);
  await conn.run(`
    CREATE OR REPLACE VIEW ${db}.${schema}."ai_requests" AS
    SELECT * FROM ${db}.${schema}.${table} WHERE ai_category IS NOT NULL
  `);
}

export interface LogRow {
  event_id: string | null;
  received_at: Date;
  event_ts: Date;
  event_hour: Date;
  project_id: string | null;
  deployment_id: string | null;
  source: string | null;
  host: string | null;
  path: string | null;
  method: string | null;
  status_code: number | null;
  user_agent: string | null;
  referer: string | null;
  client_ip: string | null;
  region: string | null;
  request_id: string | null;
  ai_category: string | null;
  ai_name: string | null;
  raw: string;
}

// MotherDuck native tables do not yet support the DuckDB appender over the
// wire, so we build a single multi-row INSERT per invocation. Vercel log
// drains already batch ~100-1000 rows per POST, so one network round-trip
// per batch is fine.
//
// Column order here MUST match the CREATE TABLE in sql/01_setup.sql.
export async function insertRows(rows: LogRow[]): Promise<void> {
  if (rows.length === 0) return;
  const conn = await getConnection();

  const values = rows.map(
    (r) =>
      `(${[
        sqlStr(r.event_id),
        sqlTs(r.received_at),
        sqlTs(r.event_ts),
        sqlTs(r.event_hour),
        sqlStr(r.project_id),
        sqlStr(r.deployment_id),
        sqlStr(r.source),
        sqlStr(r.host),
        sqlStr(r.path),
        sqlStr(r.method),
        r.status_code === null ? "NULL" : String(r.status_code),
        sqlStr(r.user_agent),
        sqlStr(r.referer),
        sqlStr(r.client_ip),
        sqlStr(r.region),
        sqlStr(r.request_id),
        sqlStr(r.ai_category),
        sqlStr(r.ai_name),
        `${sqlStr(r.raw)}::JSON`,
      ].join(", ")})`
  );

  const stmt = `INSERT INTO ${TARGET} VALUES\n  ${values.join(",\n  ")}`;
  await conn.run(stmt);
}

function sqlStr(v: string | null): string {
  if (v === null) return "NULL";
  return `'${v.replace(/'/g, "''")}'`;
}

function sqlTs(d: Date): string {
  return `TIMESTAMP '${d.toISOString().replace("T", " ").replace("Z", "")}'`;
}

function quoteIdent(id: string): string {
  return `"${id.replace(/"/g, '""')}"`;
}

function resolveDestination(): { database: string; schema: string } {
  const destination = process.env.MD_DESTINATION?.trim();
  if (!destination) {
    return {
      database: process.env.MD_DATABASE ?? "agent_analytics",
      schema: process.env.MD_SCHEMA ?? "raw",
    };
  }

  const parts = destination.split(".");
  if (parts.length !== 2 || parts.some((part) => part.length === 0)) {
    throw new Error(
      "MD_DESTINATION must be in the form <database>.<schema>, e.g. agent_analytics.raw"
    );
  }

  return {
    database: parts[0]!,
    schema: parts[1]!,
  };
}
