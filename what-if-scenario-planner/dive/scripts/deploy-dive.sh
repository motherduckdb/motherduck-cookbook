#!/bin/sh
# Build the Dive for a deploy target and create/update it in MotherDuck as you.
#
#   dive/scripts/deploy-dive.sh <target>
#
# The target's entry in dive/deploy-targets.json (written by flight/setup.py)
# names the share, the write Flight, the API host and the environment variable
# that holds YOUR token (admin_token_env). With no dive_id there, the Dive is
# created and its id is saved back into the file.
set -eu
TARGET="${1:?usage: deploy-dive.sh <target>}"
DIVE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$DIVE_DIR"
node scripts/build-dive.mjs "$TARGET"
FILE="$DIVE_DIR/build/supply-chain-scenarios.$TARGET.jsx"
cfg() { node -e "const t=require('./deploy-targets.json')[process.argv[1]];process.stdout.write(String(t[process.argv[2]]??''))" "$TARGET" "$1"; }
SHARE="$(cfg share_url)"
DIVE_ID="$(cfg dive_id)"
HOST="$(cfg host)"
TOKEN_ENV="$(cfg admin_token_env)"
TOKEN="$(printenv "$TOKEN_ENV" || true)"
[ -n "$TOKEN" ] || { echo "\$$TOKEN_ENV is not set" >&2; exit 1; }
TITLE='Supply Chain Scenario Modeling (Dive App)'
DESCRIPTION='Supply scenario modeling: is expediting, drop shipping or a subcontractor worth the demand it saves? Supply and demand grid, levers and approvals are saved by the supply-planner-write Flight; Push to ERP queues the orders.'
RESOURCES="[{'url': '$SHARE', 'alias': 'scenario_planner'}]"
if [ -n "$DIVE_ID" ]; then
  SQL="SELECT id::VARCHAR AS id, version FROM MD_UPDATE_DIVE_CONTENT(id := '$DIVE_ID'::UUID, content := getvariable('c'), required_resources := $RESOURCES);"
else
  SQL="SELECT id::VARCHAR AS id FROM MD_CREATE_DIVE(title := '$TITLE', content := getvariable('c'),
    description := '$DESCRIPTION', api_version := 1, required_resources := $RESOURCES);"
fi
run() {
  if [ -n "$HOST" ] && [ "$HOST" != "api.motherduck.com" ]; then
    env -u motherduck_token MOTHERDUCK_TOKEN="$TOKEN" motherduck_host="$HOST" duckdb -csv -noheader "md:" "$@"
  else
    env -u motherduck_token -u motherduck_host MOTHERDUCK_TOKEN="$TOKEN" duckdb -csv -noheader "md:" "$@"
  fi
}
OUT="$(run -c "SET VARIABLE c = (SELECT content FROM read_text('$FILE'));" -c "$SQL")"
echo "$OUT"
if [ -z "$DIVE_ID" ]; then
  NEW_ID="$(echo "$OUT" | tail -1 | cut -d, -f1)"
  node -e "const fs=require('fs');const t=JSON.parse(fs.readFileSync('deploy-targets.json','utf8'));t[process.argv[1]].dive_id=process.argv[2];fs.writeFileSync('deploy-targets.json',JSON.stringify(t,null,2)+'\n')" "$TARGET" "$NEW_ID"
  echo "created Dive $NEW_ID (saved as $TARGET.dive_id in deploy-targets.json)"
fi
