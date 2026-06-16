#!/usr/bin/env bash
#
# Deploy a MotherDuck Dive from a local dives/<name>/ folder.
#
# Usage:
#   ./scripts/deploy-dive.sh backlinks-hn
#   PREVIEW_BRANCH=my-branch ./scripts/deploy-dive.sh backlinks-hn
#
# Required:
#   MOTHERDUCK_TOKEN must be set for the DuckDB CLI MotherDuck connection.
#
# Optional:
#   MOTHERDUCK_DATABASE defaults to md: and can be set to another MotherDuck
#   connection string if needed.
#   DBT_DUCKDB_PATH defaults to md:my_db and must match the dbt build database.
#   DBT_SCHEMA defaults to dbt_dev and must match the schema used for dbt build.

set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ./scripts/deploy-dive.sh <dive-name>

Deploys dives/<dive-name>/dive.tsx to MotherDuck.

Environment:
  MOTHERDUCK_TOKEN      Required MotherDuck token.
  MOTHERDUCK_DATABASE   Optional DuckDB connection string. Defaults to md:.
  DBT_DUCKDB_PATH       Optional dbt database path. Defaults to md:my_db.
  DBT_SCHEMA            Optional dbt base schema. Defaults to dbt_dev.
  PREVIEW_BRANCH        Optional branch name for preview Dive titles.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

DIVE_NAME="${1:-}"
if [[ -z "${DIVE_NAME}" ]]; then
  usage
  exit 1
fi

if [[ -z "${MOTHERDUCK_TOKEN:-}" ]]; then
  echo "MOTHERDUCK_TOKEN is required." >&2
  exit 1
fi

if ! command -v duckdb >/dev/null 2>&1; then
  echo "duckdb CLI is required but was not found on PATH." >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required but was not found on PATH." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIVE_DIR="${REPO_ROOT}/dives/${DIVE_NAME}"
MANIFEST_FILE="${DIVE_DIR}/dive-manifest.json"

if [[ ! -d "${DIVE_DIR}" ]]; then
  echo "Dive folder not found: ${DIVE_DIR}" >&2
  exit 1
fi

if [[ ! -f "${MANIFEST_FILE}" ]]; then
  echo "Dive manifest not found: ${MANIFEST_FILE}" >&2
  exit 1
fi

SOURCE_BASENAME="$(jq -r '.source // "dive.tsx"' "${MANIFEST_FILE}")"
SOURCE_FILE="${DIVE_DIR}/${SOURCE_BASENAME}"
TITLE="$(jq -er '.title' "${MANIFEST_FILE}")"
DESCRIPTION="$(jq -r '.description // ""' "${MANIFEST_FILE}")"

if [[ ! -f "${SOURCE_FILE}" ]]; then
  echo "Dive source not found: ${SOURCE_FILE}" >&2
  exit 1
fi

if [[ -n "${PREVIEW_BRANCH:-}" ]]; then
  DEPLOY_TITLE="${TITLE}:${PREVIEW_BRANCH} (Preview)"
else
  DEPLOY_TITLE="${TITLE}"
fi

sql_escape() {
  printf "%s" "$1" | sed "s/'/''/g"
}

DUCKDB_DATABASE="${MOTHERDUCK_DATABASE:-md:}"
DBT_DATABASE_PATH="${DBT_DUCKDB_PATH:-md:my_db}"
DBT_DATABASE="${DBT_DATABASE_PATH#md:}"
DBT_DATABASE="${DBT_DATABASE:-my_db}"
DBT_BASE_SCHEMA="${DBT_SCHEMA:-dbt_dev}"
DBT_MART_SCHEMA="${DBT_BASE_SCHEMA}_mart"
DEPLOY_TITLE_SQL="$(sql_escape "${DEPLOY_TITLE}")"
DESCRIPTION_SQL="$(sql_escape "${DESCRIPTION}")"
SOURCE_FILE_SQL="$(sql_escape "${SOURCE_FILE}")"
DBT_DATABASE_SQL="$(sql_escape "${DBT_DATABASE}")"
DBT_MART_SCHEMA_SQL="$(sql_escape "${DBT_MART_SCHEMA}")"

EXISTING_DIVE_IDS="$(
  duckdb "${DUCKDB_DATABASE}" -csv -noheader -c \
    "SELECT id FROM MD_LIST_DIVES() WHERE title = '${DEPLOY_TITLE_SQL}'"
)"

if [[ -z "${EXISTING_DIVE_IDS}" ]]; then
  EXISTING_DIVE_COUNT=0
else
  EXISTING_DIVE_COUNT="$(printf "%s\n" "${EXISTING_DIVE_IDS}" | wc -l | tr -d ' ')"
fi

CONTENT_SQL="
SET VARIABLE dive_content = (
  SELECT replace(
    replace(
      regexp_replace(
        content,
        'export const REQUIRED_DATABASES\\s*=\\s*\\[[\\s\\S]*?\\];\\s*',
        '',
        'g'
      ),
      '__DBT_DATABASE__',
      '${DBT_DATABASE_SQL}'
    ),
    '__DBT_MART_SCHEMA__',
    '${DBT_MART_SCHEMA_SQL}'
  )
  FROM read_text('${SOURCE_FILE_SQL}')
);
"

if (( EXISTING_DIVE_COUNT == 0 )); then
  echo "Creating Dive: ${DEPLOY_TITLE}" >&2
  DIVE_ID="$(
    duckdb "${DUCKDB_DATABASE}" -csv -noheader -c "
      ${CONTENT_SQL}
      SELECT id
      FROM MD_CREATE_DIVE(
        title := '${DEPLOY_TITLE_SQL}',
        content := getvariable('dive_content'),
        description := '${DESCRIPTION_SQL}'
      );
    "
  )"
elif (( EXISTING_DIVE_COUNT == 1 )); then
  DIVE_ID="${EXISTING_DIVE_IDS}"
  echo "Updating Dive: ${DEPLOY_TITLE} (${DIVE_ID})" >&2
  duckdb "${DUCKDB_DATABASE}" -csv -noheader -c "
    ${CONTENT_SQL}
    FROM MD_UPDATE_DIVE_CONTENT(
      id := '${DIVE_ID}'::UUID,
      content := getvariable('dive_content')
    );
    FROM MD_UPDATE_DIVE_METADATA(
      id := '${DIVE_ID}'::UUID,
      title := '${DEPLOY_TITLE_SQL}',
      description := '${DESCRIPTION_SQL}'
    );
  " >/dev/null
else
  echo "Found ${EXISTING_DIVE_COUNT} Dives with title '${DEPLOY_TITLE}'. Expected 0 or 1." >&2
  exit 1
fi

echo "https://app.motherduck.com/dives/${DIVE_ID}"
