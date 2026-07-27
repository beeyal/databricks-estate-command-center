#!/usr/bin/env bash
# FinOps Command Center — one-command deploy to a Databricks App.
# Usage: ./deploy.sh [--profile PROFILE] [--app APP_NAME] [--target TARGET]
#                    [--customise FILE]
#                    [--no-genie] [--no-narration] [--no-ai] [--no-dqm]
#
# Ships in demo mode (USE_LIVE=false); no warehouse or system-table grants are
# required to run the app.
#
# ── Per-customer config (SPEC §14) ─────────────────────────────────────────
# Onboard a customer by editing `customise.yaml` (customer_name, warehouse_id,
# genie_space_id, fx_aud, use_live, llm_endpoint, feature flags,
# catalog_scope) — NO code edits. deploy.sh reads that file and writes the
# matching env into app.yaml (and catalog_scope into config.yaml) so the
# deployed app picks it up. Env always wins over config.yaml (see data/config.py).
#
# ── Feature-flag switches (SPEC §10) — independent + composable ─────────────
# Default: all three on (genie + ai_narration + dqm), unless customise.yaml
# says otherwise. Turn any subset off, in any combination:
#   --no-genie       Ask Genie banner + /api/genie/ask off
#   --no-narration   LLM-rewritten recommendation rationale off
#   --no-ai          both of the above off (shortcut)
#   --no-dqm         Data Quality tab + /api/dqm off
# e.g. `--no-genie` (keep narration), `--no-narration` (keep Genie),
#      `--no-genie --no-dqm`, `--no-ai --no-dqm` (no AI, no DQM), etc.
#
# The CLI switches OVERRIDE customise.yaml for this one run — the features: block
# in customise.yaml is the declarative alternative (set each true/false there).
#
# A disabled feature is inert server-side: its endpoint 404s and no model call
# is made. The deterministic recommender, cost-attribution and Genie $/AI $ cost
# pages are ALWAYS on (they call no model).
set -euo pipefail

PROFILE="${DATABRICKS_PROFILE:-DEFAULT}"
APP_NAME="${FINOPS_APP_NAME:-finops-command-center}"
TARGET=""
CUSTOMISE_FILE="customise.yaml"
OVERRIDE_NO_AI="false"        # both AI features off (genie + narration)
OVERRIDE_NO_GENIE="false"     # just Genie off
OVERRIDE_NO_NARRATION="false" # just the LLM rec-narration off
OVERRIDE_NO_DQM="false"

while [[ $# -gt 0 ]]; do
  case $1 in
    --profile)       PROFILE="$2"; shift 2 ;;
    --app)           APP_NAME="$2"; shift 2 ;;
    --target)        TARGET="$2"; shift 2 ;;
    --customise)     CUSTOMISE_FILE="$2"; shift 2 ;;
    --no-ai)         OVERRIDE_NO_AI="true"; shift ;;
    --no-genie)      OVERRIDE_NO_GENIE="true"; shift ;;
    --no-narration)  OVERRIDE_NO_NARRATION="true"; shift ;;
    --no-dqm)        OVERRIDE_NO_DQM="true"; shift ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done
TARGET_FLAG="${TARGET:+--target $TARGET}"

# ── Read customise.yaml → shell vars (declarative per-customer config) ──────
# Falls back to sensible demo defaults when the file or a key is absent. Emits
# CFG_* lines that we eval; values are shell-quoted so spaces are safe.
eval "$(python3 - "$CUSTOMISE_FILE" <<'PY'
import sys, pathlib, shlex
defaults = {
    "customer_name": "Acme Corp",
    "warehouse_id": "",
    "genie_space_id": "",
    "fx_aud": "1.52",
    "use_live": "false",
    "llm_endpoint": "databricks-claude-sonnet-4-6",
    "rollup_schema": "",
    "gold_schema": "",
    "writeback_schema": "",
    "ingest_schema": "",
    "source_path": "",
    "job_run_as": "",
    "workspace_bu_table": "",
    "user_team_table": "",
}
cfg = {}
p = pathlib.Path(sys.argv[1])
if p.exists():
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text()) or {}
    except Exception as e:
        sys.stderr.write(f"warn: could not parse {p} ({e}); using defaults\n")
        cfg = {}

def g(key, d):
    v = cfg.get(key, d)
    return d if v is None else v

feats = cfg.get("features") or {}
out = {
    "customer_name": g("customer_name", defaults["customer_name"]),
    "warehouse_id":  g("warehouse_id", defaults["warehouse_id"]),
    "genie_space_id": g("genie_space_id", defaults["genie_space_id"]),
    "fx_aud":        g("fx_aud", defaults["fx_aud"]),
    "use_live":      str(g("use_live", defaults["use_live"])).lower(),
    "llm_endpoint":  g("llm_endpoint", defaults["llm_endpoint"]),
    "rollup_schema": g("rollup_schema", defaults["rollup_schema"]),
    "gold_schema":   g("gold_schema", defaults["gold_schema"]),
    "writeback_schema": g("writeback_schema", defaults["writeback_schema"]),
    "ingest_schema": g("ingest_schema", defaults["ingest_schema"]),
    "source_path":   g("source_path", defaults["source_path"]),
    "job_run_as":    g("job_run_as", defaults["job_run_as"]),
    "workspace_bu_table": g("workspace_bu_table", defaults["workspace_bu_table"]),
    "user_team_table": g("user_team_table", defaults["user_team_table"]),
    "feat_genie":    str(feats.get("genie", True)).lower(),
    "feat_ai_narration": str(feats.get("ai_narration", True)).lower(),
    "feat_dqm":      str(feats.get("dqm", True)).lower(),
}
for k, v in out.items():
    print(f"CFG_{k.upper()}={shlex.quote(str(v))}")
PY
)"

# ── Apply CLI overrides on top of the declarative flags ─────────────────────
# Each switch is independent + composable, so any combination is possible:
#   --no-genie      Genie off (narration + DQM unaffected)
#   --no-narration  LLM rec-narration off
#   --no-ai         both Genie + narration off (shortcut)
#   --no-dqm        Data Quality tab off
# (or set genie / ai_narration / dqm in customise.yaml's features: block).
FEAT_GENIE="$CFG_FEAT_GENIE"
FEAT_AI_NARRATION="$CFG_FEAT_AI_NARRATION"
FEAT_DQM="$CFG_FEAT_DQM"
if [[ "$OVERRIDE_NO_AI" == "true" ]]; then
  FEAT_GENIE="false"; FEAT_AI_NARRATION="false"
fi
if [[ "$OVERRIDE_NO_GENIE" == "true" ]]; then
  FEAT_GENIE="false"
fi
if [[ "$OVERRIDE_NO_NARRATION" == "true" ]]; then
  FEAT_AI_NARRATION="false"
fi
if [[ "$OVERRIDE_NO_DQM" == "true" ]]; then
  FEAT_DQM="false"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  FinOps Command Center — Deploy"
echo "  Profile : $PROFILE"
echo "  App     : $APP_NAME"
echo "  Target  : ${TARGET:-dev (default)}"
echo "  Config  : $CUSTOMISE_FILE  (customer=$CFG_CUSTOMER_NAME)"
echo "  Mode    : $([[ "$CFG_USE_LIVE" == "true" ]] && echo live || echo demo)  fx_aud=$CFG_FX_AUD"
echo "  Features: genie=$FEAT_GENIE  ai_narration=$FEAT_AI_NARRATION  dqm=$FEAT_DQM"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Write a resolved value into app.yaml env. Matches quoted OR unquoted values,
# and appends the entry under `env:` if it is missing.
_set_env() {  # $1 = env name, $2 = value
  python3 - "$1" "$2" <<'PY'
import re, sys, pathlib
name, value = sys.argv[1], sys.argv[2]
p = pathlib.Path("app.yaml"); text = p.read_text()
# Match "- name: NAME\n    value: <anything>" (quoted or bare) and replace value.
pat = re.compile(r'(-\s*name:\s*' + re.escape(name) + r'\s*\n\s*value:\s*).*')
new, n = pat.subn(lambda m: m.group(1) + f'"{value}"', text)
if n == 0:
    if not text.endswith("\n"):
        text += "\n"
    new = text + f'  - name: {name}\n    value: "{value}"\n'
p.write_text(new)
PY
}

# Write catalog_scope (a YAML list) from customise.yaml into config.yaml so the
# app's Access/Tables pages honour the allowlist. Surgically replaces just the
# `catalog_scope:` block so config.yaml keeps its comments + key order. No-op if
# either file is absent or yaml is unavailable.
_sync_catalog_scope() {
  python3 - "$CUSTOMISE_FILE" <<'PY'
import sys, re, pathlib
try:
    import yaml
except Exception:
    sys.exit(0)
src = pathlib.Path(sys.argv[1]); dst = pathlib.Path("config.yaml")
if not src.exists() or not dst.exists():
    sys.exit(0)
cust = yaml.safe_load(src.read_text()) or {}
scope = cust.get("catalog_scope")
if scope is None:
    sys.exit(0)
if scope:
    block = "catalog_scope:\n" + "".join(f"  - {c}\n" for c in scope)
else:
    block = "catalog_scope: []\n"
text = dst.read_text()
# Match the existing catalog_scope key + any list items (indented `- ...`).
pat = re.compile(r'^catalog_scope:.*(?:\n[ \t]+-.*)*\n?', re.MULTILINE)
new, n = pat.subn(block, text, count=1)
if n == 0:  # key missing — append.
    if not text.endswith("\n"):
        text += "\n"
    new = text + block
dst.write_text(new)
print(f"  catalog_scope -> config.yaml ({len(scope)} entr{'y' if len(scope)==1 else 'ies'})")
PY
}

# Scalar + string config → app.yaml env (env wins over config.yaml).
_set_env FINOPS_CUSTOMER_NAME     "$CFG_CUSTOMER_NAME"
_set_env USE_LIVE                 "$CFG_USE_LIVE"
_set_env DATABRICKS_WAREHOUSE_ID  "$CFG_WAREHOUSE_ID"
_set_env FINOPS_FX_AUD            "$CFG_FX_AUD"
_set_env GENIE_SPACE_ID           "$CFG_GENIE_SPACE_ID"
_set_env FINOPS_LLM_ENDPOINT      "$CFG_LLM_ENDPOINT"
_set_env FINOPS_ROLLUP_SCHEMA     "$CFG_ROLLUP_SCHEMA"
_set_env FINOPS_GOLD_SCHEMA       "$CFG_GOLD_SCHEMA"
_set_env FINOPS_WRITEBACK_SCHEMA  "$CFG_WRITEBACK_SCHEMA"
_set_env FINOPS_INGEST_SCHEMA     "$CFG_INGEST_SCHEMA"
_set_env FINOPS_SOURCE_PATH       "$CFG_SOURCE_PATH"
_set_env FINOPS_JOB_RUN_AS        "$CFG_JOB_RUN_AS"
_set_env FINOPS_WORKSPACE_BU_TABLE "$CFG_WORKSPACE_BU_TABLE"
_set_env FINOPS_USER_TEAM_TABLE   "$CFG_USER_TEAM_TABLE"
# Feature flags (customise.yaml + any --no-ai / --no-dqm override).
_set_env FINOPS_FEATURE_GENIE        "$FEAT_GENIE"
_set_env FINOPS_FEATURE_AI_NARRATION "$FEAT_AI_NARRATION"
_set_env FINOPS_FEATURE_DQM          "$FEAT_DQM"
# List config → config.yaml.
_sync_catalog_scope

echo "[1/3] Building frontend (React + Vite)…"
# Only run `npm install` when dependencies are actually missing. In locked-down
# environments the npm registry can be unreachable (proxied/blackholed), which
# makes an unconditional `npm install` hang forever. If node_modules is already
# present (e.g. vendored or symlinked), skip straight to the build.
# Force a fresh install regardless with FORCE_NPM_INSTALL=1.
(
  cd frontend
  if [ "${FORCE_NPM_INSTALL:-0}" = "1" ] || [ ! -d node_modules ]; then
    echo "  installing npm dependencies…"
    npm install
  else
    echo "  node_modules present — skipping npm install (set FORCE_NPM_INSTALL=1 to force)"
  fi
  npm run build
)

echo "[2/3] Deploying bundle (App resource + source sync)…"
databricks bundle deploy --profile "$PROFILE" ${TARGET_FLAG}

echo "[3/3] Deploying + starting app…"
WS_USER=$(databricks current-user me --profile "$PROFILE" --output json 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin).get("userName",""))')
BUNDLE_FILES="/Workspace/Users/${WS_USER}/.bundle/finops-command-center/files"
databricks apps start "$APP_NAME" --profile "$PROFILE" 2>/dev/null || true
databricks apps deploy "$APP_NAME" --source-code-path "$BUNDLE_FILES" --profile "$PROFILE"

# Provision AI features immediately (Genie Space create-if-missing + LLM
# endpoint check), so Ask Genie works out of the box without waiting for the
# first finops_admin_sync run. Only when live + an ingest schema is set + genie
# on. Non-fatal — a provisioning hiccup never fails the deploy; the daily job
# retries it anyway. Runs as the deployer (needs Genie create + write to the
# ingest schema); the app auto-discovers the created space id from finops_admin.
if [[ "$CFG_USE_LIVE" == "true" && -n "$CFG_INGEST_SCHEMA" && "$FEAT_GENIE" == "true" ]]; then
  echo "[3.5/3] Provisioning AI features (Genie Space + LLM endpoint check)…"
  DATABRICKS_CONFIG_PROFILE="$PROFILE" DATABRICKS_WAREHOUSE_ID="$CFG_WAREHOUSE_ID" \
    FINOPS_INGEST_SCHEMA="$CFG_INGEST_SCHEMA" FINOPS_FEATURE_GENIE="$FEAT_GENIE" \
    FINOPS_FEATURE_AI_NARRATION="$FEAT_AI_NARRATION" FINOPS_LLM_ENDPOINT="$CFG_LLM_ENDPOINT" \
    python3 -m data.provision "$CFG_WAREHOUSE_ID" "$CFG_INGEST_SCHEMA" || \
    echo "  (provisioning skipped/failed — the finops_admin_sync job will retry it)"
fi

URL=$(databricks apps get "$APP_NAME" --profile "$PROFILE" 2>/dev/null \
  | grep -o 'https://[^"]*databricksapps\.com[^"]*' | head -1 || echo "")
echo ""
echo "  ✓ Deployed. Open: ${URL:-run: databricks apps get $APP_NAME --profile $PROFILE}"
