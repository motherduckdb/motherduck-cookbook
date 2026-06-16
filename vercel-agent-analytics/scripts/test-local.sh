#!/usr/bin/env bash
# Post the sample payload to a local or deployed collector.
#
# Usage:
#   ./test-local.sh                         # defaults to http://localhost:8787/api/drain
#   ./test-local.sh https://<project>.vercel.app/api/drain
#
# Requires VERCEL_DRAIN_SECRET so we can sign the body exactly the way
# Vercel would sign a real log drain delivery.

set -euo pipefail

URL="${1:-http://localhost:8787/api/drain}"
PAYLOAD_FILE="$(dirname "$0")/sample-payload.ndjson"

if [[ -z "${VERCEL_DRAIN_SECRET:-}" ]]; then
  echo "VERCEL_DRAIN_SECRET is required" >&2
  exit 1
fi

SIG=$(openssl dgst -sha1 -hmac "$VERCEL_DRAIN_SECRET" -hex < "$PAYLOAD_FILE" | awk '{print $2}')

curl -sS -X POST "$URL" \
  -H "content-type: application/x-ndjson" \
  -H "x-vercel-signature: $SIG" \
  --data-binary "@$PAYLOAD_FILE"
echo
