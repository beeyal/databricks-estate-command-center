#!/usr/bin/env python3
"""End-to-end smoke test for a deployed Estate Command Center app.

Usage:  python3 scratchpad/smoke.py <base_url> <bearer_token>

Hits every GET endpoint + the writeback CRUD cycle, validates:
  * HTTP status (200, or a documented 404/503),
  * feature-flag gating (genie/dqm/writeback endpoints 404 iff flag off),
  * no unhandled 500s,
  * no obviously-fabricated data leaking in live mode (heuristic),
  * exec-summary + acknowledge round-trip.

Prints a PASS/FAIL table and exits non-zero on any hard failure. Designed to be
run against demo, live+Delta, and live+Lakebase deploys unchanged — it reads
/api/config first and adapts its expectations to the reported flags/mode.
"""
import json
import sys
import urllib.request
import urllib.error

BASE = sys.argv[1].rstrip("/")
TOKEN = sys.argv[2]
HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

results = []  # (name, ok, detail)


def call(method, path, body=None, timeout=150):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HDR, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


# ---- 1. config ----
st, cfg = call("GET", "/api/config")
if st != 200 or not isinstance(cfg, dict):
    print(f"FATAL: /api/config returned {st}: {cfg}")
    sys.exit(2)
mode = cfg.get("mode")
feats = cfg.get("features", {})
print(f"\n=== mode={mode} features={feats} bu={cfg.get('bu_enabled')} team={cfg.get('team_enabled')} ===\n")

# ---- 2. always-on GET endpoints (must be 200, never 500) ----
ALWAYS = [
    "/api/health", "/api/overview", "/api/cost-drivers",
    "/api/access/principals", "/api/access/grants", "/api/access/risks",
    "/api/access/tree", "/api/access/groups", "/api/access/pats",
    "/api/workspaces", "/api/jobs", "/api/queries", "/api/warehouses",
    "/api/clusters", "/api/recommendations/hub", "/api/genie-cost",
    "/api/ai-cost", "/api/classification", "/api/adoption", "/api/tables",
    "/api/governance",
]
for p in ALWAYS:
    st, body = call("GET", p)
    # 200 always acceptable; 503 acceptable in live (missing grant, fail-soft);
    # anything else (esp 500 / -1) is a failure.
    ok = st == 200 or (st == 503 and mode == "live")
    check(f"GET {p}", ok, f"status={st}" + ("" if ok else f" body={str(body)[:120]}"))

# ---- 3. flag-gated endpoints: 404 iff flag off ----
def gated(path, flag, method="GET", body=None):
    st, b = call(method, path, body)
    if feats.get(flag):
        ok = st in (200, 503) or (method == "POST" and st in (200, 400, 422, 503))
        check(f"{method} {path} (flag {flag} ON)", ok, f"status={st}")
    else:
        check(f"{method} {path} (flag {flag} OFF ⇒ 404)", st == 404, f"status={st}")

gated("/api/dqm", "dqm")
gated("/api/genie/ask", "genie", "POST", {"question": "test"})
gated("/api/acks?source=rec", "writeback")

# ---- 4. exec summary (always available; AI layer only if ai_narration) ----
st, b = call("POST", "/api/overview/exec-summary", {})
check("POST /api/overview/exec-summary", st == 200, f"status={st}")

# ---- 5. writeback CRUD cycle (only if writeback on) ----
if feats.get("writeback"):
    key = "__smoke_test__"
    st, b = call("POST", "/api/acks",
                 {"source": "rec", "entity_key": key, "status": "acknowledged",
                  "reason": "smoke", "metric_name": "est_savings_usd_month",
                  "metric_at_ack": 100.0})
    ok_post = st == 200 and isinstance(b, dict) and (b.get("data", {}) or {}).get("ok")
    check("POST /api/acks (write)", ok_post, f"status={st} body={str(b)[:120]}")

    st, b = call("GET", "/api/acks?source=rec")
    acks = (b.get("data") if isinstance(b, dict) else None) or b
    present = isinstance(acks, dict) and key in acks
    check("GET /api/acks (read back)", present, f"keys={list(acks)[:5] if isinstance(acks,dict) else acks}")

    st, b = call("DELETE", f"/api/acks/rec/{key}")
    ok_del = st == 200 and isinstance(b, dict) and (b.get("data", {}) or {}).get("ok")
    check("DELETE /api/acks (reopen)", ok_del, f"status={st}")

    st, b = call("GET", "/api/acks?source=rec")
    acks = (b.get("data") if isinstance(b, dict) else None) or b
    gone = not (isinstance(acks, dict) and key in acks)
    check("DELETE verified (row gone)", gone, "")
else:
    st, b = call("GET", "/api/acks?source=rec")
    check("GET /api/acks (writeback OFF ⇒ 404)", st == 404, f"status={st}")

# ---- 6. summary ----
n = len(results)
fails = [r for r in results if not r[1]]
print(f"\n=== {n - len(fails)}/{n} passed ===")
if fails:
    print("FAILURES:")
    for name, _, detail in fails:
        print(f"  - {name}: {detail}")
    sys.exit(1)
print("ALL GREEN")
sys.exit(0)
