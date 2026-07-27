"""Admin-API ingestion job for the Estate Command Center.

Everything the app needs that is NOT in the system tables comes from admin REST
APIs (budgets, budget policies, cluster policies, PATs, alerts, SCIM groups,
workspace assignments, Lakehouse-Monitoring configs). This job pulls them via
the Databricks SDK on a schedule and lands each dataset as a JSON payload row in
``<schema>.finops_admin (key, payload, ingested_at)`` — a robust, schema-light
Delta store. The app reads those rows as REAL data and surfaces ``ingested_at``
as the "Last refreshed" time in tooltips.

Deployed as the daily ``finops_admin_sync`` job (see databricks.yml). Each
dataset is pulled independently and failures are captured per-key in the
``_status`` payload — a missing permission on one API never blocks the others,
and the app fails closed (shows "run finops_admin_sync") for any key with no row.

PRIVILEGES: the job's service principal needs **account admin** (budgets,
budget policies, SCIM groups, workspace assignments) and **workspace admin**
(token management, alerts, quality monitors). This is heavier than the
read-only system-table access the app itself needs — provision a dedicated SP.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable

# When this runs as a bundle spark_python_task, the parent (…/files) that holds
# the `data` package isn't on sys.path. Serverless runs the file through a
# notebook wrapper where ``__file__`` is UNDEFINED, so derive the path robustly
# (argv[0] → cwd) rather than assuming __file__ exists.
_here = globals().get("__file__") or (sys.argv[0] if sys.argv and sys.argv[0] else "")
_base = os.path.dirname(os.path.dirname(os.path.abspath(_here))) if _here else os.getcwd()
for _p in (_base, os.path.dirname(_base), os.getcwd(), os.path.dirname(os.getcwd())):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from data import live  # noqa: E402 — import after the sys.path bootstrap above


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(obj: Any) -> dict[str, Any]:
    """Best-effort convert an SDK dataclass to a plain dict."""
    if obj is None:
        return {}
    if hasattr(obj, "as_dict"):
        try:
            return obj.as_dict()
        except Exception:
            pass
    return {k: v for k, v in vars(obj).items() if not k.startswith("_")} if hasattr(obj, "__dict__") else {}


def _sql_str(s: str) -> str:
    """Single-quote-escape a string for inline SQL."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "''") + "'"


def _ensure_admin_table(warehouse_id: str, schema: str) -> None:
    """Create the finops_admin Delta store, then BEST-EFFORT enable PO/LC. Kept
    separate + wrapped: `CLUSTER BY AUTO` at CREATE hard-fails on a metastore
    without Predictive Optimization (CLUSTER_BY_AUTO_REQUIRES_PREDICTIVE_
    OPTIMIZATION, ES-1558331), which would abort the whole admin-sync before any
    dataset is written."""
    # Auto-create the schema (best-effort) so the customer only needs CREATE on
    # the catalog, not a pre-made schema (mirrors gold + writeback).
    try:
        live._run(warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {schema}", "ensure admin schema")
    except Exception:  # noqa: BLE001 — schema may pre-exist / be admin-managed
        pass
    live._run(warehouse_id,
        f"CREATE TABLE IF NOT EXISTS {schema}.finops_admin "
        "(key STRING, payload STRING, ingested_at TIMESTAMP) USING DELTA",
        "ensure finops_admin")
    try:
        live._run(warehouse_id,
            f"ALTER TABLE {schema}.finops_admin SET TBLPROPERTIES ('delta.predictiveOptimization' = 'ENABLE')",
            "enable PO on finops_admin")
        live._run(warehouse_id, f"ALTER TABLE {schema}.finops_admin CLUSTER BY AUTO", "enable LC on finops_admin")
    except Exception:  # noqa: BLE001 — PO/LC unavailable here; plain table still works
        pass


# ---------------------------------------------------------------------------
# Per-dataset pulls. Each returns a JSON-serialisable payload dict. They accept
# the already-built account/workspace clients so we construct each client once.
# ---------------------------------------------------------------------------

def _pull_budgets(a: Any) -> dict[str, Any]:
    """Account Console budgets (spend thresholds + alerts)."""
    items = [_as_dict(b) for b in a.budgets.list()]
    configured = len(items)
    with_alerts = sum(1 for b in items
                      for al in (b.get("budget_configuration", b).get("alert_configurations", []) or []) if al)
    return {"budgets": items, "count": configured, "with_alerts": with_alerts}


def _pull_budget_policies(a: Any) -> dict[str, Any]:
    """Serverless budget policies (tag + cap serverless compute)."""
    items = [_as_dict(p) for p in a.budget_policy.list()]
    return {"policies": items, "count": len(items)}


def _pull_cluster_policy_coverage(w: Any) -> dict[str, Any]:
    """Cluster-policy coverage = share of all-purpose/job clusters bound to a
    cost-control policy."""
    policies = [_as_dict(p) for p in w.cluster_policies.list()]
    clusters = [_as_dict(c) for c in w.clusters.list()]
    total = len(clusters)
    bound = sum(1 for c in clusters if c.get("policy_id"))
    return {
        "policies": policies, "num_policies": len(policies),
        "total_clusters": total, "bound_clusters": bound,
        "coverage_pct": round(bound / total, 3) if total else 0.0,
    }


def _pull_tokens(w: Any) -> dict[str, Any]:
    """All personal access tokens across the workspace (token management API)."""
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    out: list[dict[str, Any]] = []
    for t in w.token_management.list():
        d = _as_dict(t)
        created = d.get("creation_time") or 0
        age_days = int((now_ms - created) / 86_400_000) if created else 0
        out.append({
            "token_id": d.get("token_id", ""), "comment": d.get("comment", "") or "(no comment)",
            "created_by": d.get("created_by_username", "") or d.get("owner_id", ""),
            "creation_time": created, "expiry_time": d.get("expiry_time", 0),
            "age_days": age_days, "stale": age_days > 90,
        })
    stale = sum(1 for t in out if t["stale"])
    return {"tokens": out, "count": len(out), "stale": stale}


def _pull_alerts(w: Any) -> dict[str, Any]:
    """SQL alerts (spend-anomaly / threshold alerts wired on queries)."""
    items = [_as_dict(al) for al in w.alerts.list()]
    return {"alerts": items, "count": len(items)}


def _pull_groups(a: Any) -> dict[str, Any]:
    """Account SCIM groups + members + entitlements (nested membership)."""
    out: list[dict[str, Any]] = []
    for g in a.groups.list():
        d = _as_dict(g)
        members = [{"display": m.get("display", ""), "value": m.get("value", ""), "ref": m.get("ref", "")}
                   for m in (d.get("members") or [])]
        ents = [e.get("value", "") for e in (d.get("entitlements") or [])]
        out.append({"group": d.get("display_name", ""), "id": d.get("id", ""),
                    "member_count": len(members), "members": members, "entitlements": ents})
    return {"groups": out, "count": len(out)}


def _pull_workspace_assignments(a: Any) -> dict[str, Any]:
    """Per-workspace principal → permission assignments (account API)."""
    out: list[dict[str, Any]] = []
    for ws in a.workspaces.list():
        wsid = getattr(ws, "workspace_id", None)
        if not wsid:
            continue
        try:
            for pa in a.workspace_assignment.list(workspace_id=wsid):
                d = _as_dict(pa)
                principal = d.get("principal", {}) or {}
                out.append({
                    "workspace_id": str(wsid),
                    "principal": principal.get("user_name") or principal.get("group_name")
                                 or principal.get("service_principal_name") or "",
                    "permissions": d.get("permissions", []),
                })
        except Exception:
            continue
    return {"assignments": out, "count": len(out)}


def _pull_dqm_monitors(w: Any, warehouse_id: str) -> dict[str, Any]:
    """Lakehouse-Monitoring monitor configs. There is no list-all; we look up
    monitored tables from system.data_quality_monitoring and fetch each config
    (best-effort — skipped tables are fine)."""
    try:
        tables = live._run(warehouse_id,
            "SELECT DISTINCT concat_ws('.',catalog_name,schema_name,table_name) fqn "
            "FROM system.data_quality_monitoring.table_results LIMIT 200",
            "dqm monitored tables")
    except live.LiveError:
        return {"monitors": [], "count": 0, "note": "system.data_quality_monitoring not readable"}
    out: list[dict[str, Any]] = []
    for r in tables:
        fqn = str(r.get("fqn") or "")
        try:
            m = _as_dict(w.quality_monitors.get(table_name=fqn))
            out.append({"table": fqn, "status": m.get("status", ""),
                        "monitor_type": (m.get("inference_log") and "inference")
                                        or (m.get("time_series") and "time_series")
                                        or (m.get("snapshot") and "snapshot") or "unknown",
                        "schedule": m.get("schedule", {})})
        except Exception:
            continue
    return {"monitors": out, "count": len(out)}


# key → (scope, pull fn). scope tells run_admin_sync which client to pass.
_DATASETS: dict[str, tuple[str, Callable[..., dict[str, Any]]]] = {
    "budgets": ("account", _pull_budgets),
    "budget_policies": ("account", _pull_budget_policies),
    "cluster_policy_coverage": ("workspace", _pull_cluster_policy_coverage),
    "tokens": ("workspace", _pull_tokens),
    "alerts": ("workspace", _pull_alerts),
    "groups": ("account", _pull_groups),
    "workspace_assignments": ("account", _pull_workspace_assignments),
    "dqm_monitors": ("warehouse", _pull_dqm_monitors),
}


def run_admin_sync(warehouse_id: str, schema: str) -> dict[str, Any]:
    """Pull every admin-API dataset and land it in ``<schema>.finops_admin``.

    Returns a per-key status dict. Each dataset is independent: a failure
    (e.g. missing permission) is recorded and skipped, never blocking others.
    """
    from databricks.sdk import AccountClient, WorkspaceClient

    # Clean, addressable exits for missing/malformed config — never a downstream
    # traceback (mirrors gold.run_gold).
    if not warehouse_id:
        raise SystemExit("set DATABRICKS_WAREHOUSE_ID — the admin store is written via a SQL warehouse")
    if not schema:
        raise SystemExit("set FINOPS_INGEST_SCHEMA (e.g. main.finops_cc) — the admin "
                         "store is a Delta <schema>.finops_admin table")
    if not live._SCHEMA_RE.match(schema):
        raise SystemExit(f"FINOPS_INGEST_SCHEMA must be a catalog.schema identifier, got: {schema!r}")
    _ensure_admin_table(warehouse_id, schema)

    # Build clients once. Account client may be unavailable if the SP isn't an
    # account admin — account-scoped datasets then record an error and skip.
    try:
        w = WorkspaceClient()
    except Exception as e:  # pragma: no cover - env dependent
        w = None
        ws_err = str(e)[:200]
    else:
        ws_err = ""
    try:
        a = AccountClient()
    except Exception as e:
        a = None
        acc_err = str(e)[:200]
    else:
        acc_err = ""

    status: dict[str, Any] = {}
    ingested = _now()
    for key, (scope, fn) in _DATASETS.items():
        try:
            if scope == "account":
                if a is None:
                    raise RuntimeError(f"account client unavailable: {acc_err}")
                payload = fn(a)
            elif scope == "warehouse":
                if w is None:
                    raise RuntimeError(f"workspace client unavailable: {ws_err}")
                payload = fn(w, warehouse_id)
            else:
                if w is None:
                    raise RuntimeError(f"workspace client unavailable: {ws_err}")
                payload = fn(w)
            _write(warehouse_id, schema, key, payload, ingested)
            status[key] = {"ok": True, "count": payload.get("count", 0)}
        except Exception as e:
            status[key] = {"ok": False, "error": str(e)[:200]}

    # Provision the AI features so they work out of the box (idempotent):
    # create the FinOps Genie Space if missing + verify the LLM
    # endpoint, persisting the result under key 'provision'. Gated by the
    # feature flags the app was deployed with.
    from data import provision

    try:
        prov = provision.run_provision(
            warehouse_id, schema,
            genie=str(os.environ.get("FINOPS_FEATURE_GENIE", "true")).lower() == "true",
            ai_narration=str(os.environ.get("FINOPS_FEATURE_AI_NARRATION", "false")).lower() == "true",
            llm_endpoint=os.environ.get("FINOPS_LLM_ENDPOINT", "databricks-claude-sonnet-4-6"))
        if prov:
            status["provision"] = {k: v.get("ok") for k, v in prov.items()}
    except Exception as e:  # noqa: BLE001 - provisioning is best-effort, never fatal to the sync
        print(f"  [warn] provisioning step failed: {str(e)[:150]}")
        status["provision"] = {"ok": False, "error": str(e)[:200]}

    _write(warehouse_id, schema, "_status", {"status": status}, ingested)
    return status


def _write(warehouse_id: str, schema: str, key: str, payload: dict[str, Any], ingested: str) -> None:
    """Upsert one admin dataset into the Delta ``<schema>.finops_admin`` store —
    the table the app reads admin-API data from."""
    # The table is ensured once up-front by run_admin_sync (and by provision) —
    # not re-ensured here on every one of the ~9 dataset writes.
    body = _sql_str(json.dumps(payload, default=str))
    live._run(warehouse_id, f"DELETE FROM {schema}.finops_admin WHERE key={_sql_str(key)}", f"clear {key}")
    live._run(warehouse_id,
        f"INSERT INTO {schema}.finops_admin (key, payload, ingested_at) "
        f"VALUES ({_sql_str(key)}, {body}, TIMESTAMP{_sql_str(ingested)})", f"write {key}")


def _load_app_yaml_env() -> None:
    """Load the app's own app.yaml env into os.environ so this job shares the
    app's config (warehouse, ingest schema, flags). app.yaml sits one level up
    from data/admin_sync.py in the synced bundle files. Only sets keys that
    aren't already in the environment."""
    from pathlib import Path

    # __file__ is undefined under the serverless notebook wrapper — reuse _base
    # resolved at import time (falls back to cwd).
    p = Path(_base) / "app.yaml"
    if not p.exists():
        return
    try:
        import yaml

        doc = yaml.safe_load(p.read_text()) or {}
        for item in doc.get("env", []) or []:
            name, val = item.get("name"), item.get("value")
            if name and name not in os.environ and val is not None:
                os.environ[name] = str(val)
    except Exception:  # noqa: BLE001 - best-effort; fall back to whatever env exists
        pass


if __name__ == "__main__":  # job entry point
    # Positional args [warehouse_id, ingest_schema] win if given; otherwise read
    # the app's app.yaml so the job uses exactly the app's config.
    _load_app_yaml_env()
    wh = sys.argv[1] if len(sys.argv) > 1 else (
        os.environ.get("DATABRICKS_WAREHOUSE_ID") or os.environ.get("FINOPS_WAREHOUSE_ID", ""))
    sch = sys.argv[2] if len(sys.argv) > 2 else (
        os.environ.get("FINOPS_INGEST_SCHEMA") or os.environ.get("FINOPS_ROLLUP_SCHEMA", ""))
    print("admin sync →", sch)
    for k, v in run_admin_sync(wh, sch).items():
        print(f"  {k:24} {v}")
