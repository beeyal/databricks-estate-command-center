"""Pipelines — cost + health per Lakeflow Spark Declarative Pipeline (formerly
DLT), including the pipelines that back materialized views and streaming tables
created from SQL.

Why a separate view: pipeline compute is billed with
``usage_metadata.dlt_pipeline_id`` set, and often WITHOUT a job id — a pipeline
on its own schedule, or a SQL ``CREATE MATERIALIZED VIEW … SCHEDULE`` refresh.
Those rows never reached the Jobs tab (it keys on job_id), and Query Advisor only
covers SQL warehouses, so a serverless MV refresh had no per-item home. The same
pipeline id also appears on several billing products — DLT, SQL (MV/ST refreshes),
Lakeflow Connect ingestion, Lakebase synced tables, Vector Search sync, Feature
Store — so this view sums them all per pipeline.

Sources (all read-only):
  * system.billing.usage + list_prices — cost per pipeline, trailing 30 days and
    the 30 days before (for the change %), serverless share, products.
  * system.lakeflow.pipelines — name, type (ETL_PIPELINE, MATERIALIZED_VIEW,
    STREAMING_TABLE, INGESTION_PIPELINE, …), owner, serverless / continuous /
    development settings, deleted flag.
  * system.lakeflow.pipeline_update_timeline — updates in the window, failures,
    last update, what triggered it (job task, schedule, SQL refresh, API, user).

Live is FAIL-SOFT like Jobs: if the billing read fails the tab returns an empty
list + an honest message; metadata/updates are best-effort enrichments.
"""
from __future__ import annotations

import hashlib
import random
from functools import lru_cache
from typing import Any

PRICE = "u.usage_quantity * lp.pricing.effective_list.default"
PRICE_JOIN = (
    "LEFT JOIN system.billing.list_prices lp ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name "
    "AND u.usage_start_time >= lp.price_start_time "
    "AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)"
)

_COST_SQL = f"""
SELECT
  CAST(u.workspace_id AS STRING)                                         AS workspace_id,
  CAST(u.usage_metadata.dlt_pipeline_id AS STRING)                       AS pipeline_id,
  SUM(CASE WHEN u.usage_date >= dateadd(day, -30, current_date()) THEN {PRICE} ELSE 0 END) AS cost_30d,
  SUM(CASE WHEN u.usage_date <  dateadd(day, -30, current_date()) THEN {PRICE} ELSE 0 END) AS cost_prev_30d,
  SUM(CASE WHEN u.usage_date >= dateadd(day, -30, current_date()) THEN u.usage_quantity ELSE 0 END) AS dbus_30d,
  SUM(CASE WHEN u.usage_date >= dateadd(day, -30, current_date()) AND u.product_features.is_serverless
           THEN {PRICE} ELSE 0 END)                                      AS serverless_cost_30d,
  to_json(collect_set(u.billing_origin_product))                         AS products,
  MAX(CASE WHEN u.usage_metadata.job_id IS NOT NULL THEN 1 ELSE 0 END)   AS job_attributed
FROM system.billing.usage u
{PRICE_JOIN}
WHERE u.usage_date >= dateadd(day, -60, current_date())
  AND u.usage_metadata.dlt_pipeline_id IS NOT NULL
GROUP BY 1, 2
"""

_META_SQL = """
SELECT CAST(workspace_id AS STRING) AS workspace_id, CAST(pipeline_id AS STRING) AS pipeline_id,
       name, pipeline_type, created_by, run_as,
       settings.serverless AS serverless, settings.continuous AS continuous,
       settings.development AS development,
       CASE WHEN delete_time IS NOT NULL THEN 1 ELSE 0 END AS deleted
FROM system.lakeflow.pipelines
QUALIFY row_number() OVER (PARTITION BY workspace_id, pipeline_id ORDER BY change_time DESC) = 1
"""

_UPDATES_SQL = """
WITH u AS (
  SELECT CAST(workspace_id AS STRING) AS workspace_id, CAST(pipeline_id AS STRING) AS pipeline_id,
         update_id, update_type, trigger_type, result_state, period_start_time, period_end_time
  FROM system.lakeflow.pipeline_update_timeline
  WHERE period_end_time >= dateadd(day, -30, current_timestamp())
  QUALIFY row_number() OVER (PARTITION BY workspace_id, pipeline_id, update_id ORDER BY period_end_time DESC) = 1
)
SELECT workspace_id, pipeline_id,
       COUNT(*)                                                              AS updates,
       SUM(CASE WHEN result_state IN ('FAILED') THEN 1 ELSE 0 END)           AS failed,
       SUM(CASE WHEN update_type = 'FULL_REFRESH' THEN 1 ELSE 0 END)         AS full_refreshes,
       CAST(MAX(period_end_time) AS STRING)                                  AS last_update,
       max_by(result_state, period_end_time)                                 AS last_result,
       mode(trigger_type)                                                    AS main_trigger,
       AVG(GREATEST(unix_millis(period_end_time) - unix_millis(period_start_time), 0)) / 1000.0 AS avg_update_s
FROM u
WHERE result_state IS NOT NULL
GROUP BY 1, 2
"""

TYPE_LABEL = {
    "ETL_PIPELINE": "ETL pipeline",
    "MATERIALIZED_VIEW": "Materialized view (SQL)",
    "STREAMING_TABLE": "Streaming table (SQL)",
    "INGESTION_PIPELINE": "Lakeflow Connect ingestion",
    "INGESTION_GATEWAY": "Lakeflow Connect gateway",
    "DATABASE_TABLE_SYNC": "Lakebase synced table",
    "METRIC_VIEW": "Metric view",
}
TRIGGER_LABEL = {
    "JOB_TASK": "Job task",
    "API_CALL": "API / SDK",
    "USER_ACTION": "Manual (UI)",
    "DBSQL_REQUEST": "SQL refresh",
    "RETRY_ON_FAILURE": "Retry",
    "SERVICE_UPGRADE": "Platform upgrade",
    "INFRASTRUCTURE_MAINTENANCE": "Maintenance",
    "SETTINGS_CHANGE": "Settings change",
}
PRODUCT_LABEL = {
    "DLT": "Lakeflow pipelines", "SQL": "SQL (MV/ST refresh)", "LAKEFLOW_CONNECT": "Lakeflow Connect",
    "LAKEBASE": "Lakebase sync", "DATABASE": "Lakebase sync", "VECTOR_SEARCH": "Vector Search sync",
    "FEATURE_STORE": "Feature Store", "MODEL_SERVING": "Model Serving", "AI_FUNCTIONS": "AI Functions",
}

# Signal thresholds (observable facts, no savings estimate).
_FAIL_RATE = 0.20
_MIN_UPDATES_FOR_FAIL = 3
_SPIKE_PCT = 0.5
_SPIKE_MIN_USD = 100.0
_FULL_REFRESH_HEAVY = 3


def _reasons(r: dict[str, Any]) -> list[str]:
    out: list[str] = []
    if r["updates_30d"] >= _MIN_UPDATES_FOR_FAIL and r["fail_rate"] >= _FAIL_RATE:
        out.append("failing")
    mom = r.get("mom_pct")
    if mom is not None and mom >= _SPIKE_PCT and r["cost_usd_month"] >= _SPIKE_MIN_USD:
        out.append("cost-spike")
    if r["full_refreshes_30d"] >= _FULL_REFRESH_HEAVY:
        out.append("full-refresh-heavy")
    if r.get("continuous"):
        out.append("continuous")
    if r.get("development"):
        out.append("development-mode")
    if r.get("deleted") and r["cost_usd_month"] > 0:
        out.append("deleted-still-billing")
    return out


def _build_row(*, pipeline_id: str, workspace_id: str, workspace: str, bu: str, name: str,
               pipeline_type: str, owner: str, cost: float, prev: float, dbus: float,
               serverless_cost: float, products: list[str], job_attributed: bool,
               serverless: bool | None, continuous: bool | None, development: bool | None,
               deleted: bool, updates: int, failed: int, full_refreshes: int,
               last_update: str, last_result: str, main_trigger: str,
               avg_update_s: float) -> dict[str, Any]:
    mom = ((cost - prev) / prev) if prev > 0 else None
    row = {
        "pipeline_id": pipeline_id, "workspace_id": workspace_id, "workspace": workspace, "bu": bu,
        "name": name or f"pipeline-{pipeline_id[:8]}",
        "pipeline_type": pipeline_type or "", "type_label": TYPE_LABEL.get(pipeline_type or "", pipeline_type or "—"),
        "owner": owner or "",
        "cost_usd_month": round(cost, 2), "prev_cost_usd_month": round(prev, 2),
        "mom_pct": round(mom, 3) if mom is not None else None,
        "dbus_month": round(dbus, 1),
        "serverless_share": round(serverless_cost / cost, 3) if cost > 0 else (1.0 if serverless else 0.0),
        "serverless": bool(serverless) if serverless is not None else (serverless_cost > 0),
        "products": [PRODUCT_LABEL.get(p, p.title().replace("_", " ")) for p in products],
        "job_attributed": bool(job_attributed),
        "continuous": bool(continuous) if continuous is not None else False,
        "development": bool(development) if development is not None else False,
        "deleted": bool(deleted),
        "updates_30d": int(updates), "failed_30d": int(failed),
        "fail_rate": round(failed / updates, 3) if updates else 0.0,
        "full_refreshes_30d": int(full_refreshes),
        "last_update": (last_update or "")[:16].replace("T", " "),
        "last_result": last_result or "",
        "triggered_by": TRIGGER_LABEL.get(main_trigger or "", (main_trigger or "—").title().replace("_", " ")),
        "avg_update_s": round(avg_update_s, 1),
    }
    row["reasons"] = _reasons(row)
    return row


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(r["cost_usd_month"] for r in rows)
    prev = sum(r.get("prev_cost_usd_month", 0.0) for r in rows)
    serverless_cost = sum(r["cost_usd_month"] * r.get("serverless_share", 0.0) for r in rows)
    by_type: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = by_type.setdefault(r["type_label"], {"type": r["type_label"], "num_pipelines": 0, "cost_usd_month": 0.0})
        b["num_pipelines"] += 1
        b["cost_usd_month"] += r["cost_usd_month"]
    for b in by_type.values():
        b["cost_usd_month"] = round(b["cost_usd_month"], 2)
    return {
        "num_pipelines": len(rows),
        "total_cost_usd_month": round(total, 2),
        "prev_total_cost_usd_month": round(prev, 2),
        "mom_pct": round((total - prev) / prev, 3) if prev > 0 else None,
        "serverless_pct": round(serverless_cost / total, 3) if total > 0 else 0.0,
        "num_failing": sum(1 for r in rows if "failing" in r["reasons"]),
        "num_continuous": sum(1 for r in rows if r.get("continuous")),
        "num_spiking": sum(1 for r in rows if "cost-spike" in r["reasons"]),
        "num_mv_st": sum(1 for r in rows if r["pipeline_type"] in ("MATERIALIZED_VIEW", "STREAMING_TABLE")),
        "mv_st_cost_usd_month": round(sum(r["cost_usd_month"] for r in rows
                                          if r["pipeline_type"] in ("MATERIALIZED_VIEW", "STREAMING_TABLE")), 2),
        "by_type": sorted(by_type.values(), key=lambda x: -x["cost_usd_month"]),
    }


CAVEATS = [
    "Cost is list price (usage × list_prices) over the trailing 30 days, summed across every billing product that carries this pipeline's id — Lakeflow pipelines, SQL materialized-view / streaming-table refreshes, Lakeflow Connect, Lakebase synced tables, Vector Search sync.",
    "A pipeline run as a job task is ALSO counted on the Jobs tab (the same dollars, attributed to the job) — don't add the two tabs together.",
    "Updates, failures and the trigger come from system.lakeflow.pipeline_update_timeline (last 30 days); pipelines with no update history there show 0 updates.",
]


# ---------------------------------------------------------------------------
# Live
# ---------------------------------------------------------------------------

def _empty(message: str) -> dict[str, Any]:
    return {"pipelines": [], "message": message}


def pipelines_live(warehouse_id: str) -> dict[str, Any]:
    """Real per-pipeline cost + health. Fail-soft: empty list + honest message if
    the billing read fails; metadata + update history are best-effort."""
    from concurrent.futures import ThreadPoolExecutor
    import json

    from data import live

    live.warm_auth()
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="pipelines-live") as pool:
        f_cost = pool.submit(live._run, warehouse_id, _COST_SQL, "system.billing.usage (pipeline cost)")
        f_meta = pool.submit(live._run, warehouse_id, _META_SQL, "system.lakeflow.pipelines")
        f_upd = pool.submit(live._run, warehouse_id, _UPDATES_SQL, "system.lakeflow.pipeline_update_timeline")
        f_names = pool.submit(live._workspace_names, warehouse_id)
        f_bu = pool.submit(live._workspace_bu, warehouse_id)
        try:
            cost_rows = f_cost.result()
        except Exception as e:  # noqa: BLE001 — fail soft
            d = str(getattr(e, "detail", e))
            if "INSUFFICIENT_PERMISSIONS" in d.upper() or "does not have" in d:
                return _empty("Pipelines needs read access to system.billing.usage + list_prices "
                              f"(GRANT USE SCHEMA, SELECT ON SCHEMA system.billing). (live read failed — {d})")
            return _empty(f"Pipelines couldn't read pipeline cost from system.billing.usage. (live read failed — {d})")
        meta: dict[tuple[str, str], dict[str, Any]] = {}
        meta_note = ""
        try:
            for m in f_meta.result():
                meta[(str(m.get("workspace_id")), str(m.get("pipeline_id")))] = m
        except Exception as e:  # noqa: BLE001
            meta_note = ("Pipeline names/types need SELECT on system.lakeflow.pipelines "
                         f"(GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow) — showing ids. ({str(getattr(e, 'detail', e))[:160]})")
        upd: dict[tuple[str, str], dict[str, Any]] = {}
        try:
            for u in f_upd.result():
                upd[(str(u.get("workspace_id")), str(u.get("pipeline_id")))] = u
        except Exception:  # noqa: BLE001 — update history is optional
            upd = {}
        try:
            ws_names = f_names.result()
        except Exception:  # noqa: BLE001
            ws_names = {}
        try:
            bu_by_ws = f_bu.result()
        except Exception:  # noqa: BLE001
            bu_by_ws = {}

    rows: list[dict[str, Any]] = []
    for c in cost_rows:
        ws = str(c.get("workspace_id") or "")
        pid = str(c.get("pipeline_id") or "")
        cost = live._f(c.get("cost_30d"))
        prev = live._f(c.get("cost_prev_30d"))
        u = upd.get((ws, pid), {})
        if cost <= 0 and prev <= 0 and not u:
            continue
        m = meta.get((ws, pid), {})
        try:
            products = json.loads(c.get("products") or "[]")
        except Exception:  # noqa: BLE001
            products = []

        def _b(v: Any) -> bool | None:
            if v is None:
                return None
            return str(v).lower() in ("true", "1")

        rows.append(_build_row(
            pipeline_id=pid, workspace_id=ws, workspace=ws_names.get(ws, ws),
            bu=(bu_by_ws.get(ws, {}).get("bu") or "—"),
            name=str(m.get("name") or ""), pipeline_type=str(m.get("pipeline_type") or ""),
            owner=str(m.get("run_as") or m.get("created_by") or ""),
            cost=cost, prev=prev, dbus=live._f(c.get("dbus_30d")),
            serverless_cost=live._f(c.get("serverless_cost_30d")), products=[str(p) for p in products],
            job_attributed=bool(live._f(c.get("job_attributed"))),
            serverless=_b(m.get("serverless")), continuous=_b(m.get("continuous")),
            development=_b(m.get("development")), deleted=bool(live._f(m.get("deleted"))),
            updates=int(live._f(u.get("updates"))), failed=int(live._f(u.get("failed"))),
            full_refreshes=int(live._f(u.get("full_refreshes"))),
            last_update=str(u.get("last_update") or ""), last_result=str(u.get("last_result") or ""),
            main_trigger=str(u.get("main_trigger") or ""), avg_update_s=live._f(u.get("avg_update_s")),
        ))
    rows.sort(key=lambda r: -r["cost_usd_month"])
    return {"pipelines": rows, "message": "", "note": meta_note}


# ---------------------------------------------------------------------------
# Demo (seeded, deterministic)
# ---------------------------------------------------------------------------

_DEMO_WORKSPACES = [
    "DATA-EDP-PLATFORM-PROD", "DATA-EDP-MARKETS-PROD", "DATA-EDP-METERING-PROD",
    "DATA-ANA-ASSET-PROD", "DATA-ANA-ASSET-PPD", "FORE-DATASCIENCE-DEV",
]
_DEMO_NAMES = [
    ("bronze_meter_reads_ingest", "ETL_PIPELINE"), ("silver_nem12_intervals", "ETL_PIPELINE"),
    ("gold_settlement_daily", "MATERIALIZED_VIEW"), ("mv_dispatch_prices_5min", "MATERIALIZED_VIEW"),
    ("st_scada_events", "STREAMING_TABLE"), ("sap_pm_workorders_ingest", "INGESTION_PIPELINE"),
    ("mv_asset_health_scores", "MATERIALIZED_VIEW"), ("outage_events_cdc", "ETL_PIPELINE"),
    ("weather_forecast_load", "ETL_PIPELINE"), ("mv_finance_cost_allocation", "MATERIALIZED_VIEW"),
    ("customer_lookup_sync", "DATABASE_TABLE_SYNC"), ("st_market_bids", "STREAMING_TABLE"),
    ("gas_flow_telemetry", "ETL_PIPELINE"), ("mv_esg_emissions_monthly", "MATERIALIZED_VIEW"),
    ("feature_load_forecast", "ETL_PIPELINE"), ("salesforce_accounts_ingest", "INGESTION_PIPELINE"),
]
_DEMO_TRIGGERS = ["JOB_TASK", "DBSQL_REQUEST", "API_CALL", "USER_ACTION"]


@lru_cache(maxsize=1)
def _demo_rows() -> list[dict[str, Any]]:
    rng = random.Random(int.from_bytes(hashlib.md5(b"pipelines-demo").digest()[:6], "big"))
    rows: list[dict[str, Any]] = []
    for i, (name, ptype) in enumerate(_DEMO_NAMES):
        ws = _DEMO_WORKSPACES[i % len(_DEMO_WORKSPACES)]
        cost = round(rng.uniform(150, 9_000), 2)
        prev = round(cost * rng.uniform(0.55, 1.25), 2)
        updates = rng.randint(4, 720) if ptype != "STREAMING_TABLE" else rng.randint(1, 6)
        failed = rng.randint(0, max(1, updates // 6)) if i % 5 == 0 else rng.randint(0, 1)
        continuous = ptype == "STREAMING_TABLE" or name.endswith("_cdc")
        serverless = ptype in ("MATERIALIZED_VIEW", "STREAMING_TABLE") or i % 3 == 0
        trigger = "DBSQL_REQUEST" if ptype in ("MATERIALIZED_VIEW", "STREAMING_TABLE") else _DEMO_TRIGGERS[i % len(_DEMO_TRIGGERS)]
        products = ["SQL"] if ptype in ("MATERIALIZED_VIEW", "STREAMING_TABLE") else (
            ["LAKEFLOW_CONNECT"] if ptype == "INGESTION_PIPELINE" else (
                ["LAKEBASE"] if ptype == "DATABASE_TABLE_SYNC" else ["DLT"]))
        rows.append(_build_row(
            pipeline_id=hashlib.md5(name.encode()).hexdigest()[:8] + "-demo",
            workspace_id=str(1000 + (i % len(_DEMO_WORKSPACES))), workspace=ws, bu="—",
            name=name, pipeline_type=ptype, owner=f"svc-{ws.split('-')[1].lower()}@example.com",
            cost=cost, prev=prev, dbus=round(cost / 0.42, 1),
            serverless_cost=cost if serverless else 0.0, products=products,
            job_attributed=trigger == "JOB_TASK", serverless=serverless, continuous=continuous,
            development=(i == 11), deleted=(i == 13),
            updates=updates, failed=failed, full_refreshes=4 if i in (2, 9) else rng.randint(0, 1),
            last_update=f"2026-07-{28 - (i % 9):02d} {(3 + i) % 24:02d}:{(i * 7) % 60:02d}",
            last_result="FAILED" if failed and i % 5 == 0 else "COMPLETED",
            main_trigger=trigger, avg_update_s=round(rng.uniform(40, 1800), 1),
        ))
    rows.sort(key=lambda r: -r["cost_usd_month"])
    return rows


def pipelines_demo() -> list[dict[str, Any]]:
    return [dict(r) for r in _demo_rows()]


if __name__ == "__main__":
    rows = pipelines_demo()
    s = summarize(rows)
    print("=== Pipelines demo self-check ===")
    print(f"Pipelines : {s['num_pipelines']}  total ${s['total_cost_usd_month']:,.0f}/mo  "
          f"serverless {s['serverless_pct']:.0%}  failing {s['num_failing']}  MV/ST ${s['mv_st_cost_usd_month']:,.0f}")
    assert s["num_pipelines"] == len(_DEMO_NAMES)
    assert abs(sum(b["cost_usd_month"] for b in s["by_type"]) - s["total_cost_usd_month"]) < 1
    print("OK")
