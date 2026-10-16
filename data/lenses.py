"""Operational cost lenses (live): the estate's trailing-30-day spend re-expressed
by workload, Classic vs Serverless, environment tier, Production / Non-production
and naming-convention group.

Every lens is computed from ONE grouped billing read (workspace × product ×
serverless), so all of them reconcile to the same total — the demo used to back
the workload + compute lenses with a sample split even in live mode.

Workload is a transparent heuristic, labelled as such in the UI:
  * Jobs spend is split across Batch / Streaming / Interactive / ML-AI by each
    job's name rules (data/workload.py), weighted by that job's cost.
  * Pipeline (DLT) spend is split Streaming vs Batch by whether the pipeline runs
    in continuous mode.
  * Every other product maps by product (SQL / all-purpose → Interactive / Ad-hoc,
    Model Serving / AI → ML / AI, platform services → Other).
"""
from __future__ import annotations

from typing import Any

from data import grouping, workload

_SQL = """
SELECT CAST(u.workspace_id AS STRING) AS workspace_id,
       u.billing_origin_product AS product,
       CASE WHEN u.product_features.is_serverless THEN 1 ELSE 0 END AS serverless,
       SUM(u.usage_quantity * lp.pricing.effective_list.default) AS usd
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
WHERE u.usage_date >= dateadd(day, -30, current_date())
GROUP BY 1, 2, 3
"""

# billing_origin_product code → workload (non-jobs, non-pipeline products).
_PRODUCT_WORKLOAD = {
    "SQL": workload.INTERACTIVE, "ALL_PURPOSE": workload.INTERACTIVE, "INTERACTIVE": workload.INTERACTIVE,
    "NOTEBOOKS": workload.INTERACTIVE,
    "MODEL_SERVING": workload.ML_AI, "AI_GATEWAY": workload.ML_AI, "VECTOR_SEARCH": workload.ML_AI,
    "AGENT_BRICKS": workload.ML_AI, "AI_FUNCTIONS": workload.ML_AI, "FOUNDATION_MODEL_TRAINING": workload.ML_AI,
    "FEATURE_STORE": workload.ML_AI, "GENIE": workload.INTERACTIVE,
    "LAKEFLOW_CONNECT": workload.BATCH,
}


def _bucket_list(spend: dict[str, float], total: float, order: list[str] | None = None) -> list[dict[str, Any]]:
    items = [{"key": k, "spend_usd_month": round(v), "pct": round(v / total, 3) if total else 0.0}
             for k, v in spend.items() if v > 0]
    if order:
        rank = {k: i for i, k in enumerate(order)}
        items.sort(key=lambda x: (rank.get(x["key"], 99), -x["spend_usd_month"]))
    else:
        items.sort(key=lambda x: -x["spend_usd_month"])
    return items


def _shares(rows: list[dict[str, Any]] | None, classify) -> dict[str, float]:
    """Cost-weighted share per workload bucket over job / pipeline rows."""
    acc: dict[str, float] = {}
    for r in rows or []:
        c = float(r.get("cost_usd_month") or 0)
        if c > 0:
            k = classify(r)
            acc[k] = acc.get(k, 0.0) + c
    tot = sum(acc.values())
    return {k: v / tot for k, v in acc.items()} if tot > 0 else {}


def lenses_live(warehouse_id: str, jobs_rows: list[dict[str, Any]] | None = None,
                pipeline_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    from data import live

    rows = live._run(warehouse_id, _SQL, "system.billing.usage (cost lenses)")
    names = live._workspace_names(warehouse_id)

    job_share = _shares(jobs_rows, lambda r: workload.classify_job(r.get("job_name", ""), r.get("compute_type")))
    pipe_share = _shares(pipeline_rows, lambda r: workload.STREAMING if r.get("continuous") else workload.BATCH)

    by_workload: dict[str, float] = {}
    by_compute: dict[str, float] = {}
    by_env: dict[str, float] = {}
    by_tier: dict[str, float] = {}
    by_group: dict[str, float] = {}
    total = 0.0
    for r in rows:
        usd = live._f(r.get("usd"))
        if usd <= 0:
            continue
        total += usd
        product = str(r.get("product") or "")
        name = names.get(str(r.get("workspace_id")), str(r.get("workspace_id")))
        by_compute["Serverless" if live._f(r.get("serverless")) else "Classic"] = (
            by_compute.get("Serverless" if live._f(r.get("serverless")) else "Classic", 0.0) + usd)
        env = grouping.workspace_environment(name)
        by_env[env] = by_env.get(env, 0.0) + usd
        tier = grouping.workspace_tier(name)
        by_tier[tier] = by_tier.get(tier, 0.0) + usd
        grp = grouping.workspace_group(name)
        by_group[grp] = by_group.get(grp, 0.0) + usd
        if product == "JOBS":
            split = job_share or {workload.BATCH: 1.0}
        elif product == "DLT":
            split = pipe_share or {workload.BATCH: 1.0}
        else:
            split = {_PRODUCT_WORKLOAD.get(product, workload.OTHER): 1.0}
        for k, f in split.items():
            by_workload[k] = by_workload.get(k, 0.0) + usd * f

    return {
        "window": "trailing 30 days",
        "total_usd": round(total),
        "by_workload": _bucket_list(by_workload, total, workload.WORKLOADS),
        "by_compute_type": _bucket_list(by_compute, total, ["Classic", "Serverless"]),
        "by_environment": _bucket_list(by_env, total, [grouping.PROD, grouping.NONPROD]),
        "by_env_tier": _bucket_list(by_tier, total,
                                    ["Prod", "Pre-prod", "Test", "Dev", "Sandbox", "Non-prod", grouping.UNCLASSIFIED]),
        "by_group": _bucket_list(by_group, total) if grouping.groups_configured() else [],
        "groups_configured": grouping.groups_configured(),
        "workload_basis": ("Jobs split by job-name rules and pipelines by continuous vs triggered mode"
                           if (job_share or pipe_share) else
                           "Jobs and pipelines counted as Batch (job/pipeline detail not loaded yet)"),
    }
