#!/usr/bin/env bash
#
# Build and deploy the NBA Box Scores Dive to MotherDuck.
#
# Resolves the Dive by TITLE via MD_LIST_DIVES() — creating it the first time
# and updating its content on every run after — so nothing in the repo pins a
# specific prod Dive id. Run it from anywhere:
#
#   MOTHERDUCK_TOKEN=... ./scripts/deploy-dive.sh
#   MOTHERDUCK_TOKEN=... DIVE_TITLE="NBA Box Scores (Preview)" ./scripts/deploy-dive.sh
#
# Required:
#   MOTHERDUCK_TOKEN   Token with read+write on the source database.
#
# Optional:
#   DIVE_TITLE         Dive title to create/update. Default "NBA Box Scores".
#   NBA_DIVE_DATABASE  MotherDuck database bound to the dive's nba_box_scores_v3
#                      alias. Default nba_box_scores_v3.
#   SKIP_BUILD=1       Deploy the existing dist/dive.jsx without rebuilding.
#
# Requires a DuckDB 1.5.2 CLI on PATH (MotherDuck rejects 1.5.3), plus Node/npm
# for the esbuild bundle step.

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

if [[ -z "${MOTHERDUCK_TOKEN:-}" ]]; then
  echo "MOTHERDUCK_TOKEN is required." >&2
  exit 1
fi

if ! command -v duckdb >/dev/null 2>&1; then
  echo "duckdb CLI is required but was not found on PATH (use a 1.5.2 client)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIVE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUNDLE_FILE="${DIVE_DIR}/dist/dive.jsx"

DIVE_TITLE="${DIVE_TITLE:-NBA Box Scores}"
NBA_DIVE_DATABASE="${NBA_DIVE_DATABASE:-nba_box_scores_v3}"

if [[ "${SKIP_BUILD:-}" != "1" ]]; then
  if ! command -v npm >/dev/null 2>&1; then
    echo "npm is required to build the bundle (or set SKIP_BUILD=1)." >&2
    exit 1
  fi
  echo "Building dist/dive.jsx ..." >&2
  ( cd "${DIVE_DIR}" && npm install --silent && npm run build --silent )
fi

if [[ ! -f "${BUNDLE_FILE}" ]]; then
  echo "Bundle not found: ${BUNDLE_FILE} (run without SKIP_BUILD, or 'npm run build')." >&2
  exit 1
fi

sql_escape() { printf "%s" "$1" | sed "s/'/''/g"; }

DIVE_TITLE_SQL="$(sql_escape "${DIVE_TITLE}")"
BUNDLE_FILE_SQL="$(sql_escape "${BUNDLE_FILE}")"
RESOURCE_URL_SQL="$(sql_escape "md:${NBA_DIVE_DATABASE}")"

# The bundled dive queries the database under the fixed alias nba_box_scores_v3
# (see src/lib/query.ts); only the underlying md: database is configurable.
REQUIRED_RESOURCES="[{'url': '${RESOURCE_URL_SQL}', 'alias': 'nba_box_scores_v3'}]"

CONTENT_SQL="SET VARIABLE dive_content = (SELECT content FROM read_text('${BUNDLE_FILE_SQL}'));"

EXISTING_DIVE_IDS="$(
  duckdb "md:" -csv -noheader -c \
    "SELECT id FROM MD_LIST_DIVES() WHERE title = '${DIVE_TITLE_SQL}'"
)"

if [[ -z "${EXISTING_DIVE_IDS}" ]]; then
  EXISTING_DIVE_COUNT=0
else
  EXISTING_DIVE_COUNT="$(printf "%s\n" "${EXISTING_DIVE_IDS}" | wc -l | tr -d ' ')"
fi

if (( EXISTING_DIVE_COUNT == 0 )); then
  echo "Creating Dive: ${DIVE_TITLE}" >&2
  DIVE_ID="$(
    duckdb "md:" -csv -noheader -c "
      ${CONTENT_SQL}
      SELECT id FROM MD_CREATE_DIVE(
        title := '${DIVE_TITLE_SQL}',
        content := getvariable('dive_content'),
        required_resources := ${REQUIRED_RESOURCES},
        api_version := 1
      );
    "
  )"
elif (( EXISTING_DIVE_COUNT == 1 )); then
  DIVE_ID="${EXISTING_DIVE_IDS}"
  echo "Updating Dive: ${DIVE_TITLE} (${DIVE_ID})" >&2
  duckdb "md:" -csv -noheader -c "
    ${CONTENT_SQL}
    FROM MD_UPDATE_DIVE_CONTENT(
      id := '${DIVE_ID}'::UUID,
      content := getvariable('dive_content'),
      required_resources := ${REQUIRED_RESOURCES},
      api_version := 1
    );
  " >/dev/null
else
  echo "Found ${EXISTING_DIVE_COUNT} Dives titled '${DIVE_TITLE}'. Expected 0 or 1." >&2
  exit 1
fi

echo "https://app.motherduck.com/dives/${DIVE_ID}"
