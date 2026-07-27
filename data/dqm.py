"""Data Quality Monitoring — Lakehouse Monitoring inventory (SPEC §12).

Gated by ``features.dqm``. When on, shows the tables already being monitored by
Databricks Lakehouse Monitoring, plus the FinOps tie-in: the
``DATA_QUALITY_MONITORING`` billing line and which monitors drive it, with a
"prune monitors on low-value tables" hint.

Live mode would detect existing monitors via the monitor metadata /
information_schema; demo mode (default) returns the representative set below.

Monitor types (Lakehouse Monitoring):
  * Snapshot   — profile the whole table each refresh (dimension/reference data)
  * TimeSeries — profile over a timestamp column with a granularity (fact data)
  * Inference  — model-quality + drift on a model's input/output log table
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

# DATA_QUALITY_MONITORING list price per DBU (illustrative list-price reference;
# Account Console is source of truth). Monitor refreshes run on serverless.
DQM_DBU_LIST_PRICE_USD = 0.55

# Representative monitors (SPEC §12 asks for ~8–12). Each: the monitored table,
# monitor type, refresh schedule, freshness, drift/quality status, #metrics, and
# the monthly refresh DBUs that drive the DATA_QUALITY_MONITORING billing line.
# ``value`` is a coarse business-value signal used for the prune hint.
_MONITORS: list[dict[str, Any]] = [
    {
        "catalog": "acme_prod", "schema": "gold", "table": "gold_daily_kpis",
        "monitor_type": "Snapshot", "workspace": "acme-shared-bi",
        "schedule": "0 6 * * *", "schedule_label": "Daily 06:00",
        "last_refresh_hours": 5, "num_metrics": 34,
        "null_pct": 0.4, "drift_psi": 0.06, "drift_ks": 0.03, "anomalies": 0,
        "alerts": 1, "alert_status": "OK", "dbus_month": 42.0, "value": "high",
    },
    {
        "catalog": "acme_metering", "schema": "silver", "table": "silver_meter_clean",
        "monitor_type": "TimeSeries", "workspace": "acme-metering-nem12",
        "schedule": "0 */4 * * *", "schedule_label": "Every 4h",
        "last_refresh_hours": 2, "num_metrics": 51,
        "null_pct": 1.8, "drift_psi": 0.22, "drift_ks": 0.14, "anomalies": 3,
        "alerts": 2, "alert_status": "TRIGGERED", "dbus_month": 138.0, "value": "high",
    },
    {
        "catalog": "acme_metering", "schema": "bronze", "table": "nem12_raw",
        "monitor_type": "TimeSeries", "workspace": "acme-metering-nem12",
        "schedule": "0 */6 * * *", "schedule_label": "Every 6h",
        "last_refresh_hours": 3, "num_metrics": 28,
        "null_pct": 4.6, "drift_psi": 0.11, "drift_ks": 0.07, "anomalies": 1,
        "alerts": 1, "alert_status": "OK", "dbus_month": 96.0, "value": "medium",
    },
    {
        "catalog": "acme_markets", "schema": "gold", "table": "settlement_market",
        "monitor_type": "TimeSeries", "workspace": "acme-energy-markets",
        "schedule": "0 5 * * *", "schedule_label": "Daily 05:00",
        "last_refresh_hours": 6, "num_metrics": 44,
        "null_pct": 0.2, "drift_psi": 0.31, "drift_ks": 0.19, "anomalies": 5,
        "alerts": 3, "alert_status": "TRIGGERED", "dbus_month": 121.0, "value": "high",
    },
    {
        "catalog": "acme_markets", "schema": "silver", "table": "trade_positions",
        "monitor_type": "Snapshot", "workspace": "acme-energy-markets",
        "schedule": "0 5 * * *", "schedule_label": "Daily 05:00",
        "last_refresh_hours": 30, "num_metrics": 22,
        "null_pct": 0.9, "drift_psi": 0.08, "drift_ks": 0.05, "anomalies": 0,
        "alerts": 0, "alert_status": "OK", "dbus_month": 34.0, "value": "medium",
    },
    {
        "catalog": "acme_gas", "schema": "silver", "table": "pipeline_flow_hourly",
        "monitor_type": "TimeSeries", "workspace": "acme-gas-transmission",
        "schedule": "0 */2 * * *", "schedule_label": "Every 2h",
        "last_refresh_hours": 1, "num_metrics": 39,
        "null_pct": 0.6, "drift_psi": 0.05, "drift_ks": 0.02, "anomalies": 0,
        "alerts": 1, "alert_status": "OK", "dbus_month": 152.0, "value": "high",
    },
    {
        "catalog": "acme_prod", "schema": "scada", "table": "asset_telemetry",
        "monitor_type": "TimeSeries", "workspace": "acme-scada-ot",
        "schedule": "0 */12 * * *", "schedule_label": "Every 12h",
        "last_refresh_hours": 40, "num_metrics": 47,
        "null_pct": 2.3, "drift_psi": 0.14, "drift_ks": 0.09, "anomalies": 2,
        "alerts": 1, "alert_status": "OK", "dbus_month": 88.0, "value": "medium",
    },
    {
        "catalog": "acme_finance", "schema": "gold", "table": "customer_invoices",
        "monitor_type": "Snapshot", "workspace": "acme-customer-billing",
        "schedule": "0 7 * * 1", "schedule_label": "Weekly Mon 07:00",
        "last_refresh_hours": 96, "num_metrics": 19,
        "null_pct": 0.1, "drift_psi": 0.03, "drift_ks": 0.01, "anomalies": 0,
        "alerts": 0, "alert_status": "OK", "dbus_month": 12.0, "value": "low",
    },
    {
        "catalog": "acme_datascience", "schema": "ml", "table": "churn_model_inference_log",
        "monitor_type": "Inference", "workspace": "acme-datascience-sandbox",
        "schedule": "0 3 * * *", "schedule_label": "Daily 03:00",
        "last_refresh_hours": 8, "num_metrics": 62,
        "null_pct": 0.0, "drift_psi": 0.27, "drift_ks": 0.17, "anomalies": 4,
        "alerts": 2, "alert_status": "TRIGGERED", "dbus_month": 174.0, "value": "high",
    },
    {
        "catalog": "acme_datascience", "schema": "ml", "table": "demand_forecast_scores",
        "monitor_type": "Inference", "workspace": "acme-datascience-sandbox",
        "schedule": "0 3 * * *", "schedule_label": "Daily 03:00",
        "last_refresh_hours": 200, "num_metrics": 40,
        "null_pct": 0.0, "drift_psi": 0.04, "drift_ks": 0.02, "anomalies": 0,
        "alerts": 0, "alert_status": "OK", "dbus_month": 58.0, "value": "low",
    },
    {
        "catalog": "acme_prod", "schema": "reporting", "table": "reporting_exec_summary",
        "monitor_type": "Snapshot", "workspace": "acme-shared-bi",
        "schedule": "0 8 * * *", "schedule_label": "Daily 08:00",
        "last_refresh_hours": 320, "num_metrics": 15,
        "null_pct": 0.3, "drift_psi": 0.02, "drift_ks": 0.01, "anomalies": 0,
        "alerts": 0, "alert_status": "OK", "dbus_month": 9.0, "value": "low",
    },
    {
        "catalog": "acme_sustainability", "schema": "gold", "table": "emissions_ledger",
        "monitor_type": "Snapshot", "workspace": "acme-sustainability",
        "schedule": "0 7 * * 1", "schedule_label": "Weekly Mon 07:00",
        "last_refresh_hours": 60, "num_metrics": 24,
        "null_pct": 1.1, "drift_psi": 0.09, "drift_ks": 0.05, "anomalies": 1,
        "alerts": 1, "alert_status": "OK", "dbus_month": 16.0, "value": "medium",
    },
]

# Freshness thresholds (hours) relative to the monitor's cadence. A monitor is
# "Stale" when its last refresh is well past what its schedule implies.
_STALE_HOURS = {
    "Every 2h": 6, "Every 4h": 12, "Every 6h": 18, "Every 12h": 36,
    "Daily 03:00": 30, "Daily 05:00": 30, "Daily 06:00": 30, "Daily 08:00": 30,
    "Weekly Mon 07:00": 200,
}


def _freshness(m: dict[str, Any]) -> str:
    threshold = _STALE_HOURS.get(m["schedule_label"], 30)
    hrs = m["last_refresh_hours"]
    if hrs <= threshold * 0.5:
        return "Fresh"
    if hrs <= threshold:
        return "OK"
    return "Stale"


def _quality_status(m: dict[str, Any]) -> str:
    """Drift/quality status (SPEC §12): PSI/KS drift, anomalies, null %."""
    if m["drift_psi"] >= 0.25 or m["anomalies"] >= 4 or m["null_pct"] >= 4.0:
        return "Critical"
    if m["drift_psi"] >= 0.10 or m["anomalies"] >= 1 or m["null_pct"] >= 2.0:
        return "Warning"
    return "Good"


@lru_cache(maxsize=1)
def _monitor_rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in _MONITORS:
        fqn = f"{m['catalog']}.{m['schema']}.{m['table']}"
        cost = round(m["dbus_month"] * DQM_DBU_LIST_PRICE_USD, 2)
        # A monitor is "low value" (prune candidate) when it is on a low-value
        # table AND either stale or quiet (no anomalies / alerts) — i.e. paying
        # for monitoring that is not surfacing anything actionable.
        freshness = _freshness(m)
        quiet = m["anomalies"] == 0 and m["alert_status"] == "OK"
        prune_candidate = m["value"] == "low" and (freshness == "Stale" or quiet)
        out.append({
            "fqn": fqn,
            "catalog": m["catalog"],
            "schema": m["schema"],
            "table": m["table"],
            "monitor_type": m["monitor_type"],
            "workspace": m["workspace"],
            # Auto-generated metric + drift tables + dashboard (Lakehouse Monitoring)
            "profile_metrics_table": f"{fqn}_profile_metrics",
            "drift_metrics_table": f"{fqn}_drift_metrics",
            "dashboard_url": f"/sql/dashboards/lhm-{m['table'].replace('_', '-')}",
            "schedule": m["schedule"],
            "schedule_label": m["schedule_label"],
            "last_refresh_hours": m["last_refresh_hours"],
            "freshness": freshness,
            "num_metrics": m["num_metrics"],
            "null_pct": m["null_pct"],
            "drift_psi": m["drift_psi"],
            "drift_ks": m["drift_ks"],
            "anomalies": m["anomalies"],
            "alerts": m["alerts"],
            "alert_status": m["alert_status"],
            "quality_status": _quality_status(m),
            "dbus_month": m["dbus_month"],
            "cost_usd_month": cost,
            "value": m["value"],
            "prune_candidate": prune_candidate,
        })
    out.sort(key=lambda r: -r["cost_usd_month"])
    return out


def get_dqm(
    monitor_type: str = "all",
    quality: str = "all",
    freshness: str = "all",
    search: str = "",
) -> dict[str, Any]:
    """The DQM payload for ``/api/dqm``. Only reachable when features.dqm is on
    (the endpoint 404s otherwise — enforced in app.py)."""
    rows = _monitor_rows()
    if monitor_type != "all":
        rows = [r for r in rows if r["monitor_type"] == monitor_type]
    if quality != "all":
        rows = [r for r in rows if r["quality_status"] == quality]
    if freshness != "all":
        rows = [r for r in rows if r["freshness"] == freshness]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["fqn"].lower() or s in r["workspace"].lower()]

    all_rows = _monitor_rows()
    total_cost = round(sum(r["cost_usd_month"] for r in all_rows), 2)
    total_dbus = round(sum(r["dbus_month"] for r in all_rows), 1)
    prune = [r for r in all_rows if r["prune_candidate"]]
    prune_savings = round(sum(r["cost_usd_month"] for r in prune), 2)

    # FinOps tie-in: which workspaces drive the DATA_QUALITY_MONITORING cost.
    by_ws: dict[str, dict[str, Any]] = {}
    for r in all_rows:
        w = by_ws.setdefault(r["workspace"], {"workspace": r["workspace"], "num_monitors": 0, "cost_usd_month": 0.0, "dbus_month": 0.0})
        w["num_monitors"] += 1
        w["cost_usd_month"] += r["cost_usd_month"]
        w["dbus_month"] += r["dbus_month"]
    by_workspace = sorted(
        ({**w, "cost_usd_month": round(w["cost_usd_month"], 2), "dbus_month": round(w["dbus_month"], 1)} for w in by_ws.values()),
        key=lambda w: -w["cost_usd_month"],
    )

    return {
        "monitors": rows,
        "summary": {
            "num_monitors": len(all_rows),
            "num_visible": len(rows),
            "num_critical": sum(1 for r in all_rows if r["quality_status"] == "Critical"),
            "num_warning": sum(1 for r in all_rows if r["quality_status"] == "Warning"),
            "num_stale": sum(1 for r in all_rows if r["freshness"] == "Stale"),
            "num_alerts_triggered": sum(1 for r in all_rows if r["alert_status"] == "TRIGGERED"),
            "dqm_cost_usd_month": total_cost,
            "dqm_dbus_month": total_dbus,
            "num_prune_candidates": len(prune),
            "prune_savings_usd_month": prune_savings,
        },
        "by_workspace": by_workspace,
        "prune_candidates": [
            {"fqn": r["fqn"], "workspace": r["workspace"], "cost_usd_month": r["cost_usd_month"],
             "freshness": r["freshness"], "value": r["value"], "anomalies": r["anomalies"]}
            for r in prune
        ],
        "billing_line": "DATA_QUALITY_MONITORING",
        "dbu_list_price_usd": DQM_DBU_LIST_PRICE_USD,
        "caveat": "Refresh DBUs run on serverless; list-price reference, Account Console is source of truth.",
    }


if __name__ == "__main__":
    payload = get_dqm()
    s = payload["summary"]
    print("=== DQM — demo self-check ===")
    print(f"Monitors        : {s['num_monitors']}")
    print(f"Critical/Warning: {s['num_critical']}/{s['num_warning']}")
    print(f"Stale           : {s['num_stale']}")
    print(f"DQM cost/mo     : ${s['dqm_cost_usd_month']:,.2f}")
    print(f"Prune candidates: {s['num_prune_candidates']} (save ${s['prune_savings_usd_month']:,.2f}/mo)")
