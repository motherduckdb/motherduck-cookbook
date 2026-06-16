import { createHmac, timingSafeEqual } from "node:crypto";

// Vercel signs log drain requests as HMAC-SHA1 of the raw body using the
// drain's shared secret. The digest is sent as a lowercase hex string in
// the `x-vercel-signature` header.
export function verifyVercelSignature(
  rawBody: string | Buffer,
  headerSignature: string | undefined,
  secret: string
): boolean {
  if (!headerSignature) return false;

  const expected = createHmac("sha1", secret)
    .update(typeof rawBody === "string" ? Buffer.from(rawBody) : rawBody)
    .digest("hex");

  const a = Buffer.from(expected, "utf8");
  const b = Buffer.from(headerSignature, "utf8");
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}
