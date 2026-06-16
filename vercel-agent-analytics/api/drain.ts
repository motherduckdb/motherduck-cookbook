import type { IncomingMessage, ServerResponse } from "node:http";
import { handleDrain } from "../src/handler.js";

export const config = {
  runtime: "nodejs",
};

export default async function handler(
  req: IncomingMessage,
  res: ServerResponse
): Promise<void> {
  if (req.method !== "POST") {
    res.statusCode = 405;
    res.end("method not allowed");
    return;
  }

  const rawBody = await readBody(req);
  const sigHeader = req.headers["x-vercel-signature"];
  const signature = Array.isArray(sigHeader) ? sigHeader[0] : sigHeader;

  const { status, body } = await handleDrain(rawBody, signature);
  res.statusCode = status;
  res.end(body);
}

function readBody(req: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    req.on("data", (c: Buffer) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}
