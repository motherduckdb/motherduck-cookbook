#!/usr/bin/env bash
#
# Deploy the StatsBomb 360 Dive to MotherDuck.
#
# Resolves the Dive by TITLE via MD_LIST_DIVES() -- creating it the first time
# and updating its content on every run after -- so nothing in the repo pins a
# Dive id. The Dive is a single self-contained src/dive.tsx (no build step).
#
#   MOTHERDUCK_TOKEN=... ./scripts/deploy-dive.sh
#   MOTHERDUCK_TOKEN=... DIVE_TITLE="StatsBomb 360 (Preview)" ./scripts/deploy-dive.sh
#
# Required:
#   MOTHERDUCK_TOKEN   Token with read on the statsbomb database.
# Optional:
#   DIVE_TITLE         Dive title to create/update. Default below.
#   SB_DATABASE        MotherDuck database bound to the dive's `statsbomb` alias. Default statsbomb.
#   SB_RESOURCE_URL    Full resource URL for the `statsbomb` alias (overrides SB_DATABASE) --
#                      e.g. the public share md:_share/statsbomb/<id> to skip building.
#
# Requires a DuckDB 1.5.3 CLI on PATH.

set -euo pipefail

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

if [[ -z "${MOTHERDUCK_TOKEN:-}" ]]; then
  echo "MOTHERDUCK_TOKEN is required." >&2
  exit 1
fi
if ! command -v duckdb >/dev/null 2>&1; then
  echo "duckdb CLI is required but was not found on PATH (use a 1.5.3 client)." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIVE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
SOURCE_FILE="${DIVE_DIR}/src/dive.tsx"

DIVE_TITLE="${DIVE_TITLE:-StatsBomb 360 — Match Replay & Passes}"
SB_DATABASE="${SB_DATABASE:-statsbomb}"
RESOURCE_URL="${SB_RESOURCE_URL:-md:${SB_DATABASE}}"

sql_escape() { printf "%s" "$1" | sed "s/'/''/g"; }
DIVE_TITLE_SQL="$(sql_escape "${DIVE_TITLE}")"
SOURCE_FILE_SQL="$(sql_escape "${SOURCE_FILE}")"
RESOURCE_URL_SQL="$(sql_escape "${RESOURCE_URL}")"

# The dive queries the database under the fixed alias `statsbomb`
# (see src/dive.tsx); only the underlying md: database is configurable.
REQUIRED_RESOURCES="[{'url': '${RESOURCE_URL_SQL}', 'alias': 'statsbomb'}]"
CONTENT_SQL="SET VARIABLE dive_content = (SELECT content FROM read_text('${SOURCE_FILE_SQL}'));"

EXISTING_IDS="$(duckdb "md:" -csv -noheader -c \
  "SELECT id FROM MD_LIST_DIVES() WHERE title = '${DIVE_TITLE_SQL}'")"
if [[ -z "${EXISTING_IDS}" ]]; then COUNT=0; else COUNT="$(printf "%s\n" "${EXISTING_IDS}" | wc -l | tr -d ' ')"; fi

if (( COUNT == 0 )); then
  echo "Creating Dive: ${DIVE_TITLE}" >&2
  DIVE_ID="$(duckdb "md:" -csv -noheader -c "
    ${CONTENT_SQL}
    SELECT id FROM MD_CREATE_DIVE(
      title := '${DIVE_TITLE_SQL}',
      content := getvariable('dive_content'),
      required_resources := ${REQUIRED_RESOURCES},
      api_version := 1
    );")"
elif (( COUNT == 1 )); then
  DIVE_ID="${EXISTING_IDS}"
  echo "Updating Dive: ${DIVE_TITLE} (${DIVE_ID})" >&2
  duckdb "md:" -csv -noheader -c "
    ${CONTENT_SQL}
    FROM MD_UPDATE_DIVE_CONTENT(
      id := '${DIVE_ID}'::UUID,
      content := getvariable('dive_content'),
      required_resources := ${REQUIRED_RESOURCES},
      api_version := 1
    );" >/dev/null
else
  echo "Found ${COUNT} Dives titled '${DIVE_TITLE}'. Expected 0 or 1." >&2
  exit 1
fi

echo "https://app.motherduck.com/dives/${DIVE_ID}"
