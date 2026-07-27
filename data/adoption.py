"""Adoption & Value — who actually uses the platform, how broadly, and which
data assets earn their keep.

Answers "adoption", not "cost": active users (MAU/WAU), activity volume,
feature/product adoption breadth per workspace, Genie/AI uptake, and a Value Map
(usage vs recency, sized by users) that separates gold assets from archive
candidates — inspired by the cost-by-driver value bubble.

Demo (seeded) module. Live mode would read system.access.audit (active users,
queries), system.query.history, system.information_schema.tables (recency) and
system.billing.usage (product breadth per workspace).
"""
from __future__ import annotations

import random
from functools import lru_cache
from typing import Any

_USERS = [
    "priya.nathan@acme.com", "james.oleary@acme.com", "wei.zhang@acme.com",
    "sarah.mckenzie@acme.com", "raj.patel@acme.com", "hannah.wu@acme.com",
    "daniel.foster@acme.com",
]
# Demo user -> team (in live this comes from the customer's user_team_table).
_USER_TEAM: dict[str, str] = {
    "priya.nathan@acme.com": "Energy Markets & Trading",
    "james.oleary@acme.com": "Platform Engineering",
    "wei.zhang@acme.com": "Data Science",
    "sarah.mckenzie@acme.com": "Shared BI / AI-BI",
    "raj.patel@acme.com": "Data Science",
    "hannah.wu@acme.com": "Asset & Network Analytics",
    "daniel.foster@acme.com": "Finance & FP&A",
}
_WORKSPACES = [
    "acme-energy-markets", "acme-shared-bi", "acme-datascience-sandbox",
    "acme-asset-analytics", "acme-metering-nem12", "acme-finance-fpna",
    "acme-gas-transmission",
]
# Products a workspace can adopt (breadth signal).
_PRODUCTS = ["SQL", "Jobs", "DLT", "Model Serving", "Vector Search", "Genie", "AI Functions", "Apps", "DQM"]

# Per-workspace adopted products (which capabilities each team actually uses).
_WS_ADOPTION: dict[str, list[str]] = {
    "acme-energy-markets": ["SQL", "Jobs", "DLT", "Model Serving", "Vector Search", "Genie", "AI Functions"],
    "acme-shared-bi": ["SQL", "Genie", "Apps", "DQM"],
    "acme-datascience-sandbox": ["SQL", "Jobs", "Model Serving", "Vector Search", "Genie", "AI Functions"],
    "acme-asset-analytics": ["SQL", "Jobs", "Genie", "DQM"],
    "acme-metering-nem12": ["SQL", "Jobs", "DLT", "DQM"],
    "acme-finance-fpna": ["SQL", "Genie"],
    "acme-gas-transmission": ["SQL", "Jobs", "DLT"],
}

# Value-map assets: (table, workspace, queries_30d, unique_users, days_since_update)
_ASSETS: list[tuple[str, str, int, int, int]] = [
    ("acme_markets.gold.settlement_market", "acme-energy-markets", 2573, 6, 2),
    ("acme_markets.gold.trade_positions", "acme-energy-markets", 1840, 5, 1),
    ("acme_metering.silver.meter_reads_interval", "acme-metering-nem12", 3120, 4, 3),
    ("acme_prod.gold.reporting_exec_summary", "acme-shared-bi", 1450, 7, 1),
    ("acme_gas.silver.pipeline_flow_hourly", "acme-gas-transmission", 980, 3, 4),
    ("acme_finance.reporting.reporting_exec_summary", "acme-finance-fpna", 720, 4, 2),
    ("acme_prod.billing.customer_invoices", "acme-shared-bi", 610, 3, 5),
    ("acme_gas.silver.compressor_runtime", "acme-asset-analytics", 540, 2, 6),
    ("acme_markets.silver.settlement_stg", "acme-energy-markets", 210, 2, 12),
    ("acme_metering.bronze.nem12_raw", "acme-metering-nem12", 180, 1, 9),
    ("acme_datascience.ml.demand_forecast_scores", "acme-datascience-sandbox", 90, 1, 21),
    ("acme_prod.scada.sensor_raw", "acme-asset-analytics", 45, 1, 34),
    ("acme_gas.gold.gas_nominations_v1", "acme-gas-transmission", 0, 0, 88),
    ("acme_finance.staging.fx_rates_old", "acme-finance-fpna", 0, 0, 141),
    ("acme_markets.gold.settlement_mv_legacy", "acme-energy-markets", 0, 0, 96),
    ("acme_datascience.tmp.scratch_features", "acme-datascience-sandbox", 0, 0, 62),
    ("acme_shared.sandbox.adhoc_2025", "acme-shared-bi", 0, 0, 210),
]

_MONTH = "2026-07"


def _rng() -> random.Random:
    return random.Random(2027)


def team_rollup(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate per-user activity into per-team totals (users + queries), sorted
    by queries. Shared by demo + live. Users without a team are grouped under an
    'Unassigned' bucket so the totals still reconcile. Returns [] if no user has a
    team (the UI then hides the team rollup entirely — a customer #14)."""
    if not any(u.get("team") for u in users):
        return []
    agg: dict[str, dict[str, Any]] = {}
    for u in users:
        team = str(u.get("team") or "Unassigned")
        t = agg.setdefault(team, {"team": team, "users": 0, "queries": 0, "query_hours": 0.0})
        t["users"] += 1
        t["queries"] += int(u.get("queries") or 0)
        t["query_hours"] += float(u.get("query_hours") or 0.0)
    for t in agg.values():
        t["query_hours"] = round(t["query_hours"], 1)
    return sorted(agg.values(), key=lambda t: -t["queries"])


def _quadrant(queries: int, users: int, med_q: float) -> str:
    if queries == 0:
        return "Archive candidate"
    if queries > med_q and users > 1:
        return "Gold asset"
    return "Standard"


@lru_cache(maxsize=1)
def get_adoption() -> dict[str, Any]:
    rng = _rng()
    med_q = sorted(a[2] for a in _ASSETS)[len(_ASSETS) // 2]

    assets = []
    for tbl, ws, q, u, days in _ASSETS:
        assets.append({
            "table": tbl, "workspace": ws,
            "total_queries": q, "unique_users": u, "days_since_update": days,
            "quadrant": _quadrant(q, u, med_q),
        })

    # Per-user activity (queries + query-hours in period, recency). query_hours =
    # cumulative statement duration — a platform-owner monthly cost report tracks it.
    users = []
    for i, u in enumerate(_USERS):
        q = rng.randint(120, 2600)
        avg_q_min = rng.uniform(0.4, 6.0)   # avg minutes per statement
        users.append({
            "user": u,
            "workspace": _WORKSPACES[i % len(_WORKSPACES)],
            "queries": q,
            "query_hours": round(q * avg_q_min / 60.0, 1),
            "last_active_days": rng.choice([0, 0, 1, 1, 2, 3, 5]),
            "team": _USER_TEAM.get(u, ""),
        })
    users.sort(key=lambda x: -x["queries"])
    teams = team_rollup(users)

    # Feature/product adoption per workspace.
    features = []
    for ws in _WORKSPACES:
        adopted = _WS_ADOPTION.get(ws, [])
        features.append({
            "workspace": ws,
            "products": adopted,
            "breadth": len(adopted),
            "genie": "Genie" in adopted,
            "ai": any(p in adopted for p in ("Model Serving", "Vector Search", "AI Functions")),
        })
    features.sort(key=lambda x: -x["breadth"])

    # 6-month monthly-active-users trend (rising adoption). Enterprise-scale base
    # so the platform MAU comfortably exceeds the ~50 identified Genie users —
    # self-serve BI/analytics reach is broader than Genie alone.
    mau_trend = []
    base = 286
    for m in range(6):
        base = round(base * (1 + rng.uniform(0.04, 0.12)))
        mau_trend.append({"month": f"2026-{2 + m:02d}", "active_users": base})

    total_queries = sum(a["total_queries"] for a in assets)
    query_hours = round(sum(u["query_hours"] for u in users), 1)
    genie_adopters = sum(1 for f in features if f["genie"])
    ai_adopters = sum(1 for f in features if f["ai"])
    return {
        "month": _MONTH,
        "assets": assets,
        "users": users,
        "team_rollup": teams,
        "features": features,
        "mau_trend": mau_trend,
        "summary": {
            "mau": mau_trend[-1]["active_users"],
            "wau": round(mau_trend[-1]["active_users"] * 0.62),
            "total_queries_month": total_queries,
            "query_hours_month": query_hours,
            "avg_breadth": round(sum(f["breadth"] for f in features) / len(features), 1),
            "num_products": len(_PRODUCTS),
            "genie_adopters": genie_adopters,
            "ai_adopters": ai_adopters,
            "num_workspaces": len(_WORKSPACES),
            "gold_assets": sum(1 for a in assets if a["quadrant"] == "Gold asset"),
            "archive_candidates": sum(1 for a in assets if a["quadrant"] == "Archive candidate"),
        },
    }


if __name__ == "__main__":
    import json
    p = get_adoption()
    print("=== Adoption & Value — demo self-check ===")
    print("MAU:", p["summary"]["mau"], "| WAU:", p["summary"]["wau"], "| queries:", p["summary"]["total_queries_month"])
    print("gold:", p["summary"]["gold_assets"], "| archive:", p["summary"]["archive_candidates"])
    print("features:", json.dumps(p["features"][0], indent=1))
