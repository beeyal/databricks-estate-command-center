#!/usr/bin/env bash
# Test helper: fetch a live API endpoint from a deployed FinOps app.
# Usage: FINOPS_APP_URL=https://<your-app>.databricksapps.com \
#        FINOPS_PROFILE=<your-cli-profile> \
#        scripts/apiget.sh /api/jobs "workspace=all&time_range=30d"
# Prints the JSON body. Token is minted fresh each call.
set -euo pipefail
BASE="${FINOPS_APP_URL:?set FINOPS_APP_URL to your deployed app URL}"
PROFILE="${FINOPS_PROFILE:?set FINOPS_PROFILE to your databricks CLI profile}"
EP="${1:?usage: apiget.sh /api/<endpoint> [querystring]}"
QS="${2:-}"
TOKEN=$(databricks auth token -p "$PROFILE" 2>/dev/null | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
URL="$BASE$EP"
[ -n "$QS" ] && URL="$URL?$QS"
curl -s --max-time 110 -H "Authorization: Bearer $TOKEN" "$URL"
