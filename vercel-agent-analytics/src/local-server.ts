// Tiny local HTTP harness for iterating without deploying.
// Run with: npm run dev

import { createServer } from "node:http";
import { handleDrain } from "./handler.js";

const PORT = Number(process.env.PORT ?? 8787);

const server = createServer(async (req, res) => {
  if (req.method !== "POST") {
    res.writeHead(405).end("method not allowed");
    return;
  }

  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  const body = Buffer.concat(chunks).toString("utf8");
  const sig = asString(req.headers["x-vercel-signature"]);

  const { status, body: out } = await handleDrain(body, sig);
  res.writeHead(status).end(out);
});

server.listen(PORT, () => console.log(`collector listening on :${PORT}`));

function asString(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}
