#!/usr/bin/env bash
# Fetches the Univer UMD bundles the supply grid needs (sheets, formulas, number
# formats) into dive/vendor-cache/, pinned to one version, for
# scripts/build-vendor.mjs to pack into src/univer-vendor.generated.ts.
#   npm run vendor     (= this script + build-vendor.mjs)
set -euo pipefail

UNIVER_VERSION="${UNIVER_VERSION:-0.25.1}"
RXJS_VERSION="${RXJS_VERSION:-7.8.1}"
REDI_VERSION="${REDI_VERSION:-1.1.1}"

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/../vendor-cache"
mkdir -p "$OUT"

fetch() {
  local url="$1" dest="$2"
  if [[ -s "$OUT/$dest" ]]; then
    return
  fi
  echo "  fetch  $dest"
  curl -sfL "$url" -o "$OUT/$dest"
}

fetch "https://unpkg.com/rxjs@$RXJS_VERSION/dist/bundles/rxjs.umd.min.js" rxjs.js
fetch "https://unpkg.com/@wendellhu/redi@$REDI_VERSION/dist/umd/index.js" redi.js
fetch "https://unpkg.com/@wendellhu/redi@$REDI_VERSION/dist/umd/react-bindings/index.js" redi-react.js
fetch "https://unpkg.com/@univerjs/themes@$UNIVER_VERSION/lib/umd/index.js" themes.js
fetch "https://unpkg.com/@univerjs/protocol@$UNIVER_VERSION/lib/umd/index.js" protocol.js

for pkg in core telemetry rpc design engine-render engine-formula drawing ui docs docs-ui \
    sheets sheets-ui sheets-formula sheets-formula-ui sheets-numfmt sheets-numfmt-ui; do
  fetch "https://unpkg.com/@univerjs/$pkg@$UNIVER_VERSION/lib/umd/index.js" "$pkg.js"
done
for pkg in core engine-formula ui docs-ui sheets sheets-ui sheets-formula sheets-numfmt; do
  fetch "https://unpkg.com/@univerjs/$pkg@$UNIVER_VERSION/lib/umd/facade.js" "$pkg.facade.js"
done
for pkg in design ui docs-ui sheets sheets-ui sheets-formula-ui sheets-numfmt-ui; do
  fetch "https://unpkg.com/@univerjs/$pkg@$UNIVER_VERSION/lib/umd/locale/en-US.js" "$pkg.enUS.js"
done
for pkg in design ui docs-ui sheets-ui sheets-formula-ui sheets-numfmt-ui; do
  fetch "https://unpkg.com/@univerjs/$pkg@$UNIVER_VERSION/lib/index.css" "$pkg.css"
done
echo "[fetch-vendor] Univer $UNIVER_VERSION in $OUT"
