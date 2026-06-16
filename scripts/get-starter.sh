#!/bin/bash
# get-starter.sh - Download a specific starter project from motherduck-cookbook repository
#
# Usage:
#   curl -fsSL https://get.motherduck.com | bash -s <starter-name>
#
# This script uses git sparse checkout to download only the selected starter folder
# without cloning the entire repository.

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
REPO="motherduckdb/motherduck-cookbook"
REPO_URL="https://github.com/${REPO}.git"
# Default to main, but can be overridden via BRANCH env var for PR testing.
# When testing a PR branch, fetch the script from that branch too (the
# get.motherduck.com redirector always serves the main branch version):
#   BRANCH=my-branch curl -fsSL \
#     https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/my-branch/scripts/get-starter.sh \
#     | bash -s <starter-name>
BRANCH="${BRANCH:-main}"

# Top-level folders that are not starter projects and should be hidden
# from the list. Everything else in the repo root is treated as a starter.
# Flight Plans live one level down under flight-plans/ and are added separately.
EXCLUDED_DIRS=(
  "scripts"
  "landing"
  "datasets"
  "theming"
  "flight-plans"
  ".devcontainer"
  ".github"
)

AVAILABLE_STARTERS=()

# Fetch the list of starter folders dynamically from the GitHub API.
# Populates AVAILABLE_STARTERS. Returns non-zero if fetch fails.
fetch_starters() {
  local api_url="https://api.github.com/repos/${REPO}/contents?ref=${BRANCH}"
  local raw
  raw=$(curl -fsSL "${api_url}" 2>/dev/null) || return 1

  # Parse pretty-printed JSON: capture "name" then check the following "type": "dir"
  local dirs
  dirs=$(echo "${raw}" | awk -F'"' '
    /"name":/ { name = $4 }
    /"type":/ { if ($4 == "dir" && name != "") { print name; name = "" } }
  ')

  AVAILABLE_STARTERS=()
  local dir excluded ex
  while IFS= read -r dir; do
    [ -z "${dir}" ] && continue
    excluded=false
    for ex in "${EXCLUDED_DIRS[@]}"; do
      if [ "${dir}" = "${ex}" ]; then
        excluded=true
        break
      fi
    done
    [ "${excluded}" = "false" ] && AVAILABLE_STARTERS+=("${dir}")
  done <<< "${dirs}"

  # Flight Plans live under flight-plans/. Add their short names too, so a user
  # can ask for "dbt-ingestion-s3" and get flight-plans/dbt-ingestion-s3.
  local fp_raw fp_dirs fp_dir
  fp_raw=$(curl -fsSL "https://api.github.com/repos/${REPO}/contents/flight-plans?ref=${BRANCH}" 2>/dev/null) || fp_raw=""
  if [ -n "${fp_raw}" ]; then
    fp_dirs=$(echo "${fp_raw}" | awk -F'"' '
      /"name":/ { name = $4 }
      /"type":/ { if ($4 == "dir" && name != "") { print name; name = "" } }
    ')
    while IFS= read -r fp_dir; do
      [ -z "${fp_dir}" ] && continue
      AVAILABLE_STARTERS+=("${fp_dir}")
    done <<< "${fp_dirs}"
  fi

  [ ${#AVAILABLE_STARTERS[@]} -gt 0 ]
}

# Function to print available starters
list_starters() {
  if [ ${#AVAILABLE_STARTERS[@]} -eq 0 ]; then
    echo -e "${YELLOW}Could not fetch the list of starter projects from GitHub.${NC}"
    echo "Browse them at: https://github.com/${REPO}"
    return
  fi
  echo -e "${BLUE}Available starter projects:${NC}"
  echo ""
  for starter in "${AVAILABLE_STARTERS[@]}"; do
    echo "  - ${starter}"
  done
  echo ""
}

# Function to check if a starter exists
starter_exists() {
  local starter=$1
  for available in "${AVAILABLE_STARTERS[@]}"; do
    if [ "$available" = "$starter" ]; then
      return 0
    fi
  done
  return 1
}

# Get starter name from argument
STARTER_NAME=${1:-}

# Try to populate AVAILABLE_STARTERS from the GitHub API. If this fails
# (offline, rate-limited, etc.), we fall back to trusting the user's
# argument and let the sparse-checkout step be the source of truth.
STARTERS_FETCHED=true
fetch_starters || STARTERS_FETCHED=false

# If no starter name provided, show usage
if [ -z "$STARTER_NAME" ]; then
  echo -e "${YELLOW}MotherDuck examples - Get a starter project by name${NC}"
  echo ""
  echo "Usage:"
  echo "  curl -fsSL https://get.motherduck.com | bash -s <starter-name>"
  echo ""
  echo "Or download and run locally:"
  echo "  ./scripts/get-starter.sh <starter-name>"
  echo ""
  list_starters
  echo "Example:"
  echo "  curl -fsSL https://get.motherduck.com | bash -s dbt-ai-prompt"
  exit 1
fi

# Check if starter exists (only when we were able to fetch the list)
if [ "$STARTERS_FETCHED" = "true" ] && ! starter_exists "$STARTER_NAME"; then
  echo -e "${RED}Error: Starter project '${STARTER_NAME}' not found.${NC}"
  echo ""
  list_starters
  exit 1
fi

# Check if directory already exists
if [ -d "$STARTER_NAME" ]; then
  echo -e "${YELLOW}Warning: Directory '${STARTER_NAME}' already exists.${NC}"
  read -p "Do you want to remove it and continue? (y/N) " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 1
  fi
  rm -rf "$STARTER_NAME"
fi

echo -e "${BLUE}Fetching starter project: ${STARTER_NAME}${NC}"
echo ""

# Clone with minimal history and sparse checkout
# Note: --sparse flag enables sparse-checkout but we still need to configure it
git clone --depth 1 --filter=blob:none --sparse -b "${BRANCH}" "${REPO_URL}" "${STARTER_NAME}.tmp" || {
  echo -e "${RED}Error: Failed to clone repository.${NC}"
  echo "Make sure you have git installed and internet connectivity."
  echo "If using a PR branch, set BRANCH env var: BRANCH=your-branch-name curl ..."
  exit 1
}

cd "${STARTER_NAME}.tmp"
# Configure sparse checkout to only get the starter folder. The starter may be a
# top-level folder or a Flight Plan under flight-plans/, so fetch both candidates.
git sparse-checkout set "${STARTER_NAME}" "flight-plans/${STARTER_NAME}"
# Checkout the files (sparse checkout doesn't auto-checkout)
git checkout "${BRANCH}" 2>/dev/null || git checkout HEAD

# Resolve where the starter actually lives: top level, or under flight-plans/.
STARTER_PATH="${STARTER_NAME}"
if [ ! -d "${STARTER_PATH}" ] && [ -d "flight-plans/${STARTER_NAME}" ]; then
  STARTER_PATH="flight-plans/${STARTER_NAME}"
fi

# Check if the starter directory exists after sparse checkout
if [ ! -d "${STARTER_PATH}" ]; then
  echo -e "${RED}Error: Starter project directory '${STARTER_NAME}' not found in repository.${NC}"
  echo "Available directories:"
  ls -la
  cd ..
  rm -rf "${STARTER_NAME}.tmp"
  exit 1
fi

# Create final directory in parent
mkdir -p "../${STARTER_NAME}"

# Move all contents (including hidden files) from the starter folder to the final
# location, always landing in a top-level folder named after the short name.
find "${STARTER_PATH}" -mindepth 1 -maxdepth 1 -exec mv {} "../${STARTER_NAME}/" \;
rmdir "${STARTER_PATH}" 2>/dev/null || true
rmdir "flight-plans" 2>/dev/null || true

# Remove git history
rm -rf .git

# Move back to parent directory and clean up
cd ..
rm -rf "${STARTER_NAME}.tmp"

# Final check
if [ -d "$STARTER_NAME" ] && [ "$(ls -A $STARTER_NAME 2>/dev/null)" ]; then
  echo -e "${GREEN}✓ Starter project '${STARTER_NAME}' ready!${NC}"
  echo ""
  echo "Next steps:"
  echo "  cd ${STARTER_NAME}"
  echo "  # Follow the README.md for setup instructions"
else
  echo -e "${RED}Error: Failed to extract starter project '${STARTER_NAME}'.${NC}"
  exit 1
fi
