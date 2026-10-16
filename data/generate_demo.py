"""Deterministic demo-data generator for the Estate Command Center.

Produces realistic, Acme-shaped data for every page of the app:
workspaces, queries (with statement metrics), managed/external tables,
principals + grants + risk flags, and governance scores.

Everything here is DEMO data — clearly labelled as such via the top-level
``demo=True`` flag on each payload. Never surface these numbers as if they
were pulled live from system tables. The real system-table SQL lives in
``data/queries.sql`` behind a ``USE_LIVE=false`` flag.

The generator is seeded (``random.seed(4217)``) so the numbers are stable
across runs — KPIs must reconcile to the row-level detail, and the tester /
reviewer needs repeatable figures.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

SEED = 4217


def _stable_hash(s: str) -> int:
    """Process-stable hash for seeding (unlike builtin ``hash`` on str, which is
    salted by PYTHONHASHSEED). Keeps the demo data reproducible across restarts.
    """
    return int.from_bytes(hashlib.md5(s.encode()).digest()[:6], "big")

# Acme Corp — gas transmission & storage. Workspaces modelled on a real-ish
# enterprise footprint: platform/shared, per-BU, and a couple of sandbox/dev.
_WORKSPACES = [
    # (name, bu, base_spend_usd_month, automated_pct, complexity)
    ("DATA-EDP-PLATFORM-PROD", "Platform Engineering", 148_000, 0.71, "Complex"),
    ("DATA-EDP-GAS-PROD", "Gas Transmission", 96_500, 0.63, "Complex"),
    ("DATA-EDP-MARKETS-PROD", "Energy Markets & Trading", 82_300, 0.44, "Complex"),
    ("DATA-ANA-ASSET-PROD", "Asset & Network Analytics", 61_200, 0.58, "Moderate"),
    ("DATA-EDP-METERING-PROD", "Metering & NEM12", 54_800, 0.39, "Moderate"),
    ("DATA-ANA-SCADA-NONPROD", "SCADA / OT Integration", 43_100, 0.52, "Moderate"),
    ("DATA-ANA-FINANCE-PROD", "Finance & FP&A", 28_700, 0.66, "Simple"),
    ("DATA-EDP-BILLING-PROD", "Customer & Billing", 37_900, 0.31, "Moderate"),
    ("DATA-ANA-ESG-NONPROD", "Sustainability & ESG", 19_400, 0.48, "Simple"),
    ("FORE-DATASCIENCE-DEV", "Data Science", 24_600, 0.18, "Complex"),
    ("DATA-ANA-BI-PROD", "Shared BI / AI-BI", 33_200, 0.61, "Moderate"),
    ("DATA-EDP-PLATFORM-DEV", "Platform Engineering", 12_800, 0.12, "Simple"),
]

_PRODUCTS = [
    "Serverless SQL",
    "Serverless Jobs",
    "DLT / Lakeflow",
    "Jobs Classic",
    "All-Purpose Compute",
    "SQL Warehouse (Classic)",
    "Model Serving",
    "Apps",
    "Foundation Model APIs",
    "Predictive Optimization",
]

_WAREHOUSES = [
    "wh-energy-markets-xl",
    "wh-gas-transmission-l",
    "wh-asset-analytics-m",
    "wh-metering-l",
    "wh-shared-bi-m",
    "wh-finance-s",
    "wh-scada-m",
    "wh-billing-l",
]

_USERS = [
    "priya.nathan@acme.com",
    "james.oleary@acme.com",
    "wei.zhang@acme.com",
    "sarah.mckenzie@acme.com",
    "raj.patel@acme.com",
    "hannah.wu@acme.com",
    "daniel.foster@acme.com",
    "svc-nem12-ingest",
    "svc-lakeflow-orchestrator",
    "svc-billing-etl",
]

_CATALOGS = ["acme_prod", "acme_gas", "acme_markets", "acme_metering", "acme_finance", "main"]
_SCHEMAS = ["bronze", "silver", "gold", "billing", "scada", "nem12", "reporting"]
_TABLE_NAMES = [
    "meter_reads_interval", "nem12_raw", "pipeline_flow_hourly", "gas_nominations",
    "settlement_market", "asset_telemetry", "customer_invoices", "network_events",
    "compressor_runtime", "scada_tags", "billing_line_items", "emissions_ledger",
    "trade_positions", "outage_register", "sensor_raw", "gold_daily_kpis",
    "silver_meter_clean", "bronze_scada_ingest", "reporting_exec_summary",
    "curtailment_events",
]

_INSIGHT_TYPES = ["cluster", "resize", "rewrite", "vacuum", "optimize", "convert-to-managed", "enable-PO", "enable-LC"]

_GROUPS = [
    ("data-platform-admins", "AIM", 8),
    ("gas-transmission-analysts", "AIM", 34),
    ("energy-markets-traders", "AIM", 21),
    ("metering-engineers", "SCIM", 17),
    ("finance-fpna", "AIM", 12),
    ("data-science-team", "SCIM", 9),
    ("bi-consumers", "AIM", 88),
    ("scada-ot-readers", "local", 6),
    ("billing-etl-svc", "local", 3),
    ("account users", "AIM", 214),
]

_PRIV = ["USE_CATALOG", "USE_SCHEMA", "SELECT", "MODIFY", "ALL PRIVILEGES", "CREATE", "EXECUTE"]


def _rng() -> random.Random:
    return random.Random(SEED)


def _now() -> datetime:
    return datetime(2026, 7, 1, tzinfo=timezone.utc)


def _health(automated_pct: float) -> str:
    if automated_pct >= 0.60:
        return "Good"
    if automated_pct >= 0.25:
        return "Warning"
    return "Critical"


def _round(x: float, n: int = 0) -> float:
    return round(x, n) if n else round(x)


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------

# Latest-week spike multipliers for the demo abnormal-cost-movement flags
# (feedback #4). One clear critical spike + one warning-level move.
_WS_LASTWEEK_SPIKE: dict[str, float] = {
    "FORE-DATASCIENCE-DEV": 1.9,   # ← sharp spike → critical exception
    "DATA-EDP-MARKETS-PROD": 1.33,       # ← moderate deviation → warning
}


@lru_cache(maxsize=1)
def _workspace_records() -> list[dict[str, Any]]:
    rng = _rng()
    out: list[dict[str, Any]] = []
    for i, (name, bu, spend, automated, complexity) in enumerate(_WORKSPACES):
        automation_gap = max(0.0, 0.75 - automated)
        idle_pct = round(rng.uniform(0.04, 0.34) * (1.4 - automated), 3)
        serverless_share = round(min(0.95, max(0.05, automated + rng.uniform(-0.15, 0.2))), 3)
        # savings = f(automation gap, idle, non-serverless share)
        savings = spend * (0.10 + 0.55 * automation_gap + 0.4 * idle_pct + 0.25 * (1 - serverless_share))
        savings = min(savings, spend * 0.62)
        savings_pct = savings / spend
        health = _health(automated)
        # 12-week sparkline trend
        trend = []
        base = spend / 4.33  # weekly
        for w in range(12):
            drift = 1 + rng.uniform(-0.08, 0.06) + (0.01 * w if health == "Critical" else -0.004 * w)
            trend.append(round(base * drift))
        # Inject a deterministic latest-week spike into a couple of workspaces so the
        # abnormal-cost-movement detection (app._annotate_ws_anomalies, feedback #4)
        # has something real to surface in the demo. Keyed on name so it's stable and
        # reconciles with every other page. Factor multiplies the final week only.
        _spike = _WS_LASTWEEK_SPIKE.get(name)
        if _spike:
            trend[-1] = round(trend[-1] * _spike)
        dbus = round(spend / rng.uniform(0.42, 0.68))
        out.append({
            "workspace_id": f"{3120000000000000 + i * 7013:d}",
            "workspace": name,
            "workspace_name": name,
            "bu": bu,
            "spend_usd_month": round(spend),
            "savings_usd_month": round(savings),
            "savings_pct": round(savings_pct, 3),
            "automated_pct": round(automated, 3),
            "health": health,
            "complexity": complexity,
            "idle_pct": idle_pct,
            "serverless_share": serverless_share,
            "dbus_month": dbus,
            "num_clusters": rng.randint(3, 42),
            "num_warehouses": rng.randint(1, 8),
            "trend": trend,
        })
    return out


def _workspace_detail(rec: dict[str, Any]) -> dict[str, Any]:
    rng = random.Random(SEED + _stable_hash(rec["workspace"]) % 10_000)
    spend = rec["spend_usd_month"]
    # product/SKU mix — weighted by serverless share
    weights = [rng.uniform(0.5, 2.0) for _ in _PRODUCTS]
    total_w = sum(weights)
    product_mix = []
    for p, w in zip(_PRODUCTS, weights):
        pct = w / total_w
        product_mix.append({"product": p, "pct": round(pct, 3), "spend_usd_month": round(spend * pct)})
    product_mix.sort(key=lambda x: -x["spend_usd_month"])
    top_users = sorted(
        [{"user": u, "spend_usd_month": round(spend * rng.uniform(0.02, 0.16))} for u in rng.sample(_USERS, 6)],
        key=lambda x: -x["spend_usd_month"],
    )
    top_jobs = sorted(
        [{"job": f"job-{n}", "spend_usd_month": round(spend * rng.uniform(0.02, 0.12))}
         for n in ["nem12-ingest", "settlement-etl", "scada-bronze", "gold-refresh", "ml-forecast"]],
        key=lambda x: -x["spend_usd_month"],
    )
    top_warehouses = sorted(
        [{"warehouse": w, "spend_usd_month": round(spend * rng.uniform(0.03, 0.2))}
         for w in rng.sample(_WAREHOUSES, 4)],
        key=lambda x: -x["spend_usd_month"],
    )
    # 12-month spend + captured-savings trend for the drill-down chart.
    # Reconciles to the workspace's monthly spend (last month ≈ spend_usd_month)
    # and captured savings ramps up an adoption curve toward savings_usd_month.
    monthly_trend = []
    start = _now() - timedelta(days=30 * 11)
    savings_target = rec["savings_usd_month"]
    health = rec["health"]
    for m in range(12):
        # spend drifts around the monthly figure; Critical workspaces trend up.
        drift = 1 + rng.uniform(-0.10, 0.07) + (0.012 * m if health == "Critical" else -0.006 * m)
        spend_m = round(spend * drift)
        captured = round(savings_target * (0.12 + (0.75 / 11) * m))
        month_label = (start + timedelta(days=30 * m)).strftime("%Y-%m")
        monthly_trend.append({"month": month_label, "spend_usd": spend_m, "savings_usd": captured})
    return {
        **rec,
        "product_mix": product_mix,
        "monthly_trend": monthly_trend,
        "top_users": top_users,
        "top_jobs": top_jobs,
        "top_warehouses": top_warehouses,
        # SPEC §9: cost-by-driver + SKU breakdown + per-driver trend + MoM + DBU-by-SKU
        "cost_drivers": _cost_driver_workspace(rec["workspace"]),
    }


# ---------------------------------------------------------------------------
# Cost by cost-driver (billing_origin_product) + SKU-level breakdown  (SPEC §9)
# ---------------------------------------------------------------------------
#
# cost-by-driver reference: cost is decomposed by `billing_origin_product`
# (the cost-driver) and, one level down, by `sku_name` — and the AU-East SKU
# names literally end in `_AUSTRALIA_EAST`, which is itself a residency signal.
#
# Model (all reconciles to each workspace's ``spend_usd_month``):
#   * DRIVERS         — the billing_origin_product cost-drivers.
#   * _DRIVER_SKUS    — the real AU-East SKU names each driver rolls up from.
#   * per-workspace   — a deterministic driver weight vector (seeded off the
#                       workspace) split further into its SKUs.
#   * monthly trend   — 6 months per driver so the top-5 multi-line + the
#                       "daily/monthly by SKU" stacked views have data, with a
#                       deterministic MoM %-change per driver (spike detection).
#   * DBU by SKU      — a $/DBU rate per driver converts SKU $ into DBUs.
#
# SKU names ending in _AUSTRALIA_EAST match the reference set so the
# framing (and the residency signal) is faithful.

# billing_origin_product cost-drivers (SPEC §9).
_DRIVERS: list[dict[str, Any]] = [
    {"driver": "SQL", "label": "SQL", "weight": 3.0},
    {"driver": "JOBS", "label": "Jobs", "weight": 2.6},
    {"driver": "INTERACTIVE", "label": "Interactive", "weight": 2.0},
    {"driver": "DLT", "label": "DLT / Lakeflow", "weight": 1.6},
    {"driver": "ALL_PURPOSE", "label": "All-Purpose", "weight": 1.4},
    {"driver": "MODEL_SERVING", "label": "Model Serving", "weight": 0.9},
    {"driver": "AI_GATEWAY", "label": "AI Gateway", "weight": 0.55},
    {"driver": "VECTOR_SEARCH", "label": "Vector Search", "weight": 0.45},
    {"driver": "PREDICTIVE_OPTIMIZATION", "label": "Predictive Optimization", "weight": 0.4},
    {"driver": "LAKEBASE", "label": "Lakebase", "weight": 0.35},
    {"driver": "APPS", "label": "Apps", "weight": 0.3},
    {"driver": "DATA_QUALITY_MONITORING", "label": "Data Quality Monitoring", "weight": 0.25},
]

# Real AU-East SKU names per driver. Compute/storage SKUs end in _AUSTRALIA_EAST
# (the residency signal from the reference); egress/connectivity SKUs
# carry the OCEANIA / AUSTRALIA_EAST geography markers.
_DRIVER_SKUS: dict[str, list[tuple[str, float]]] = {
    "SQL": [
        ("PREMIUM_SERVERLESS_SQL_COMPUTE_AUSTRALIA_EAST", 0.72),
        ("PREMIUM_SQL_PRO_COMPUTE_AUSTRALIA_EAST", 0.20),
        ("PREMIUM_SQL_CLASSIC_COMPUTE_AUSTRALIA_EAST", 0.08),
    ],
    "JOBS": [
        ("PREMIUM_JOBS_SERVERLESS_COMPUTE_AUSTRALIA_EAST", 0.66),
        ("PREMIUM_JOBS_COMPUTE_AUSTRALIA_EAST", 0.28),
        ("PREMIUM_JOBS_LIGHT_COMPUTE_AUSTRALIA_EAST", 0.06),
    ],
    "INTERACTIVE": [
        ("PREMIUM_ALL_PURPOSE_SERVERLESS_COMPUTE_AUSTRALIA_EAST", 1.0),
    ],
    "DLT": [
        ("PREMIUM_DLT_ADVANCED_COMPUTE_AUSTRALIA_EAST", 0.58),
        ("PREMIUM_DLT_PRO_COMPUTE_AUSTRALIA_EAST", 0.30),
        ("PREMIUM_DLT_CORE_COMPUTE_AUSTRALIA_EAST", 0.12),
    ],
    "ALL_PURPOSE": [
        ("PREMIUM_ALL_PURPOSE_COMPUTE_AUSTRALIA_EAST", 1.0),
    ],
    "MODEL_SERVING": [
        ("PREMIUM_SERVERLESS_REAL_TIME_INFERENCE_AUSTRALIA_EAST", 0.7),
        ("PREMIUM_MODEL_SERVING_GPU_AUSTRALIA_EAST", 0.3),
    ],
    "AI_GATEWAY": [
        ("PREMIUM_FOUNDATION_MODEL_PAY_PER_TOKEN_AUSTRALIA_EAST", 1.0),
    ],
    "VECTOR_SEARCH": [
        ("PREMIUM_VECTOR_SEARCH_AUSTRALIA_EAST", 1.0),
    ],
    "PREDICTIVE_OPTIMIZATION": [
        ("PREMIUM_PREDICTIVE_OPTIMIZATION_AUSTRALIA_EAST", 1.0),
    ],
    "LAKEBASE": [
        ("PREMIUM_LAKEBASE_CAPACITY_AUSTRALIA_EAST", 1.0),
    ],
    "APPS": [
        ("PREMIUM_APPS_AUSTRALIA_EAST", 1.0),
    ],
    "DATA_QUALITY_MONITORING": [
        ("PREMIUM_LAKEHOUSE_MONITORING_AUSTRALIA_EAST", 1.0),
    ],
}

# $/DBU list rate per driver — converts SKU $ to DBUs for the "DBU by SKU" bar.
_DRIVER_DBU_RATE: dict[str, float] = {
    "SQL": 0.70, "JOBS": 0.30, "INTERACTIVE": 0.55, "DLT": 0.36, "ALL_PURPOSE": 0.55,
    "MODEL_SERVING": 0.082, "AI_GATEWAY": 0.070, "VECTOR_SEARCH": 0.35,
    "PREDICTIVE_OPTIMIZATION": 0.30, "LAKEBASE": 0.22, "APPS": 0.11,
    "DATA_QUALITY_MONITORING": 0.30,
}

_DRIVER_INDEX = {d["driver"]: d for d in _DRIVERS}
_TREND_MONTHS = 6


def _driver_weights_for(workspace: dict[str, Any]) -> dict[str, float]:
    """A deterministic, workspace-specific driver weight vector.

    Base weights are tilted by the workspace shape: more-automated workspaces
    lean serverless SQL/Jobs/DLT; the data-science / sandbox workspaces carry
    more MODEL_SERVING / AI_GATEWAY / VECTOR_SEARCH; low-automation workspaces
    lean ALL_PURPOSE / INTERACTIVE.
    """
    rng = random.Random(SEED + 5501 + (_stable_hash(workspace["workspace"]) % 100_000))
    automated = workspace["automated_pct"]
    bu = workspace["bu"]
    weights: dict[str, float] = {}
    for d in _DRIVERS:
        w = d["weight"] * rng.uniform(0.7, 1.3)
        drv = d["driver"]
        if drv in ("SQL", "JOBS", "DLT"):
            w *= 0.6 + automated  # serverless-heavy for mature workspaces
        if drv in ("ALL_PURPOSE", "INTERACTIVE"):
            w *= 1.6 - automated  # interactive-heavy for immature workspaces
        if drv in ("MODEL_SERVING", "AI_GATEWAY", "VECTOR_SEARCH"):
            w *= 2.4 if ("Science" in bu or "Markets" in bu or "shared" in workspace["workspace"]) else 0.5
        if drv == "LAKEBASE":
            w *= 1.8 if ("Billing" in bu or "Customer" in bu or "Apps" in bu) else 0.6
        weights[drv] = max(0.02, w)
    return weights


@lru_cache(maxsize=1)
def _workspace_driver_spend() -> dict[str, dict[str, float]]:
    """Per-workspace {driver: spend_usd_month}, reconciling to spend_usd_month."""
    out: dict[str, dict[str, float]] = {}
    for w in _workspace_records():
        weights = _driver_weights_for(w)
        total_w = sum(weights.values())
        spend = w["spend_usd_month"]
        alloc = {drv: spend * (wt / total_w) for drv, wt in weights.items()}
        # round while preserving the total (largest-remainder on the biggest driver)
        rounded = {drv: round(v) for drv, v in alloc.items()}
        drift = spend - sum(rounded.values())
        if rounded:
            biggest = max(rounded, key=lambda k: rounded[k])
            rounded[biggest] += drift
        out[w["workspace"]] = rounded
    return out


def _sku_rows_for_spend(driver_spend: dict[str, float]) -> list[dict[str, Any]]:
    """Explode a {driver: $} map into per-SKU rows (driver, sku, $, dbus)."""
    rows: list[dict[str, Any]] = []
    for drv, dspend in driver_spend.items():
        skus = _DRIVER_SKUS.get(drv, [(f"PREMIUM_{drv}_AUSTRALIA_EAST", 1.0)])
        rate = _DRIVER_DBU_RATE.get(drv)
        # round SKU $ preserving the driver total
        raw = {sku: dspend_frac for sku, dspend_frac in skus}
        allocated = {sku: round(dspend_frac * dspend) for sku, dspend_frac in skus}
        drift = round(dspend) - sum(allocated.values())
        if allocated:
            biggest = max(allocated, key=lambda k: allocated[k])
            allocated[biggest] += drift
        for sku, dollars in allocated.items():
            rows.append({
                "driver": drv,
                "driver_label": _DRIVER_INDEX.get(drv, {}).get("label", drv),
                "sku": sku,
                "spend_usd_month": dollars,
                "dbus_month": round(dollars / rate) if rate else 0,
            })
        _ = raw
    return rows


def _driver_summary(driver_spend: dict[str, float]) -> list[dict[str, Any]]:
    """[{driver,label,spend,pct_of_total}] sorted by spend desc."""
    total = sum(driver_spend.values()) or 1
    out = [
        {
            "driver": drv,
            "label": _DRIVER_INDEX.get(drv, {}).get("label", drv),
            "spend_usd_month": round(v),
            "pct_of_total": round(v / total, 4),
        }
        for drv, v in driver_spend.items()
    ]
    out.sort(key=lambda d: -d["spend_usd_month"])
    return out


def _sku_summary(sku_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll per-SKU rows to a table with total_cost + pct_of_total (SPEC §9.2)."""
    agg: dict[str, dict[str, Any]] = {}
    for r in sku_rows:
        a = agg.setdefault(r["sku"], {
            "sku": r["sku"], "driver": r["driver"], "driver_label": r["driver_label"],
            "total_cost": 0, "dbus_month": 0,
        })
        a["total_cost"] += r["spend_usd_month"]
        a["dbus_month"] += r["dbus_month"]
    total = sum(a["total_cost"] for a in agg.values()) or 1
    rows = list(agg.values())
    for a in rows:
        a["pct_of_total"] = round(a["total_cost"] / total, 4)
    rows.sort(key=lambda a: -a["total_cost"])
    return rows


def _driver_trend(driver_spend: dict[str, float], seed_key: str) -> dict[str, Any]:
    """Monthly trend per driver (last _TREND_MONTHS) + MoM %-change.

    The last month equals the current driver spend (reconciles). Earlier months
    drift deterministically; a couple of drivers get an intentional spike in the
    final month so the MoM / spike-detection views have material movers.
    """
    rng = random.Random(SEED + 7700 + (_stable_hash(seed_key) % 100_000))
    start = _now() - timedelta(days=30 * (_TREND_MONTHS - 1))
    months = [(start + timedelta(days=30 * i)).strftime("%Y-%m") for i in range(_TREND_MONTHS)]
    # pick 1-2 drivers to spike in the final month (bias to sizable drivers)
    sizable = [d for d, v in driver_spend.items() if v > 0]
    spike_drivers = set(rng.sample(sizable, k=min(2, len(sizable)))) if sizable else set()

    series: list[dict[str, Any]] = []
    mom: list[dict[str, Any]] = []
    for drv, last in driver_spend.items():
        pts: list[float] = []
        # walk backwards from `last` with small drifts
        cur = float(last)
        back: list[float] = [cur]
        for _ in range(_TREND_MONTHS - 1):
            if drv in spike_drivers:
                # previous month was materially lower ⇒ a spike into `last`
                prev = cur / rng.uniform(1.35, 1.9)
            else:
                prev = cur / (1 + rng.uniform(-0.06, 0.10))
            back.append(max(0.0, prev))
            cur = prev
        pts = list(reversed([round(x) for x in back]))
        prev_m = pts[-2] if len(pts) >= 2 else 0
        last_m = pts[-1]
        # Match the live shape (data/live.py cost_drivers_live): a NEW driver
        # (prev $0 → spend this month) gets mom_pct=None + is_new=True so it's
        # not misreported as 0% growth; ranking is by $ delta.
        is_new = prev_m <= 0 and last_m > 0
        series.append({
            "driver": drv,
            "label": _DRIVER_INDEX.get(drv, {}).get("label", drv),
            "points": [{"month": months[i], "spend_usd": pts[i]} for i in range(_TREND_MONTHS)],
            "spend_usd_month": last_m,
        })
        mom.append({
            "driver": drv,
            "label": _DRIVER_INDEX.get(drv, {}).get("label", drv),
            "prev_usd_month": prev_m,
            "spend_usd_month": last_m,
            "mom_pct": round((last_m - prev_m) / prev_m, 4) if prev_m > 0 else None,
            "is_new": is_new,
            "delta_usd": last_m - prev_m,
        })
    series.sort(key=lambda s: -s["spend_usd_month"])
    mom.sort(key=lambda m: -m["delta_usd"])
    return {"months": months, "series": series, "mom": mom}


def _estate_driver_spend() -> dict[str, float]:
    """Estate-wide {driver: $} = sum across workspaces (reconciles to total)."""
    out: dict[str, float] = {}
    for _ws, dspend in _workspace_driver_spend().items():
        for drv, v in dspend.items():
            out[drv] = out.get(drv, 0.0) + v
    return out


@lru_cache(maxsize=1)
def _overview_lenses() -> dict[str, Any]:
    """AEMO's operational cost lenses (Chris feedback #2a/#2b/#2c/#2e), all
    reconciling to the same estate total:

      * by_workload      — batch / streaming / interactive / ML-AI / other, from
                           the cost-driver → workload map (data/workload.py).
      * by_compute_type  — Classic vs Serverless, split from the SKU names
                           (SERVERLESS_* SKUs vs the rest).
      * by_environment   — Production vs Non-production, summed over workspaces.
      * by_group         — EDP Core / EDP Edge / Forecasting …, summed over
                           workspaces (naming-convention groups).
    """
    from data import workload as wl
    from data import grouping

    driver_spend = _estate_driver_spend()
    total = sum(driver_spend.values()) or 1.0

    wl_spend: dict[str, float] = {}
    comp: dict[str, float] = {"Serverless": 0.0, "Classic": 0.0}
    for code, spend in driver_spend.items():
        label = _DRIVER_INDEX.get(code, {}).get("label", code)
        bucket = wl.classify_product(label)
        wl_spend[bucket] = wl_spend.get(bucket, 0.0) + spend
        for sku, share in _DRIVER_SKUS.get(code, [(code, 1.0)]):
            key = "Serverless" if "SERVERLESS" in sku.upper() else "Classic"
            comp[key] += spend * share
    comp_total = sum(comp.values()) or 1.0

    env_spend: dict[str, float] = {}
    grp_spend: dict[str, float] = {}
    for w in _workspace_records():
        name = w["workspace"]
        env_spend[grouping.workspace_environment(name)] = (
            env_spend.get(grouping.workspace_environment(name), 0.0) + w["spend_usd_month"])
        grp_spend[grouping.workspace_group(name)] = (
            grp_spend.get(grouping.workspace_group(name), 0.0) + w["spend_usd_month"])
    ws_total = sum(env_spend.values()) or 1.0

    return {
        "by_workload": sorted(
            [{"key": k, "spend_usd_month": round(v), "pct": round(v / total, 3)} for k, v in wl_spend.items()],
            key=lambda x: -x["spend_usd_month"]),
        "by_compute_type": sorted(
            [{"key": k, "spend_usd_month": round(v), "pct": round(v / comp_total, 3)} for k, v in comp.items()],
            key=lambda x: -x["spend_usd_month"]),
        "by_environment": sorted(
            [{"key": k, "spend_usd_month": round(v), "pct": round(v / ws_total, 3)} for k, v in env_spend.items()],
            key=lambda x: -x["spend_usd_month"]),
        "by_group": sorted(
            [{"key": k, "spend_usd_month": round(v), "pct": round(v / ws_total, 3)} for k, v in grp_spend.items()],
            key=lambda x: -x["spend_usd_month"]),
    }


def get_overview_lenses() -> dict[str, Any]:
    """Public demo entry point for the operational cost lenses."""
    return _overview_lenses()


@lru_cache(maxsize=1)
def _cost_driver_estate() -> dict[str, Any]:
    """Estate-level cost-by-driver + SKU + trend + MoM + DBU-by-SKU payload."""
    estate = _estate_driver_spend()
    sku_rows: list[dict[str, Any]] = []
    for _ws, dspend in _workspace_driver_spend().items():
        sku_rows.extend(_sku_rows_for_spend(dspend))
    sku_table = _sku_summary(sku_rows)
    trend = _driver_trend(estate, "estate")
    total = sum(estate.values())
    return {
        "total_spend_usd_month": round(total),
        "drivers": _driver_summary(estate),
        "sku_breakdown": sku_table,
        "dbu_by_sku": [
            {"sku": r["sku"], "driver": r["driver"], "driver_label": r["driver_label"],
             "dbus_month": r["dbus_month"]}
            for r in sku_table if r["dbus_month"] > 0
        ],
        "trend": trend,
        "mom": trend["mom"],
    }


def _cost_driver_workspace(workspace: str) -> dict[str, Any] | None:
    """Per-workspace cost-by-driver + SKU + trend + MoM + DBU-by-SKU."""
    dspend = _workspace_driver_spend().get(workspace)
    if dspend is None:
        return None
    sku_rows = _sku_rows_for_spend(dspend)
    sku_table = _sku_summary(sku_rows)
    trend = _driver_trend(dspend, workspace)
    total = sum(dspend.values())
    return {
        "workspace": workspace,
        "total_spend_usd_month": round(total),
        "drivers": _driver_summary(dspend),
        "sku_breakdown": sku_table,
        "dbu_by_sku": [
            {"sku": r["sku"], "driver": r["driver"], "driver_label": r["driver_label"],
             "dbus_month": r["dbus_month"]}
            for r in sku_table if r["dbus_month"] > 0
        ],
        "trend": trend,
        "mom": trend["mom"],
    }


# ---------------------------------------------------------------------------
# Queries (Query Advisor)
# ---------------------------------------------------------------------------

# Query "shapes" — realistic statement patterns. Each carries a metric *profile*
# so the raw statement metrics (pruning, spill, bytes, small-files, skew, queueing)
# are internally consistent with the SQL. The recommender (data/recommender.py)
# then classifies + scores each row deterministically from these raw metrics — we
# do NOT hand-label the insight_type here, so demo data behaves like real data.
_QUERY_SHAPES = [
    {
        "sql": "SELECT * FROM acme_metering.silver.meter_reads_interval WHERE read_date > '2026-06-01'",
        "table": "acme_metering.silver.meter_reads_interval", "src": "SQL",
        # SELECT * over a huge unclustered table — full scan, weak pruning.
        "bytes_gb": (400, 900), "prune": (0.03, 0.18), "spill_gb": (0, 6),
        "small_files": (0.05, 0.2), "queued": (0.0, 0.1),
    },
    {
        "sql": "SELECT m.nmi, m.kwh, n.zone FROM acme_metering.bronze.nem12_raw m JOIN acme_gas.gold.gas_nominations n ON m.nmi = n.nmi",
        "table": "acme_metering.bronze.nem12_raw", "src": "JOB",
        # Large skewed join that spills to disk — resize candidate.
        "bytes_gb": (150, 520), "prune": (0.2, 0.5), "spill_gb": (60, 210),
        "small_files": (0.1, 0.3), "queued": (0.05, 0.25),
    },
    {
        "sql": "SELECT nmi, SUM(kwh) FROM acme_metering.silver.meter_reads_interval GROUP BY nmi",
        "table": "acme_metering.silver.meter_reads_interval", "src": "SQL",
        # Full-table aggregate, poor pruning on a big table — clustering candidate.
        "bytes_gb": (120, 480), "prune": (0.08, 0.26), "spill_gb": (2, 18),
        "small_files": (0.05, 0.2), "queued": (0.0, 0.12),
    },
    {
        "sql": "SELECT * FROM acme_markets.gold.settlement_market ORDER BY interval_ts DESC",
        "table": "acme_markets.gold.settlement_market", "src": "NB",
        # Unbounded ORDER BY spills; also capacity-bound during market close.
        "bytes_gb": (200, 640), "prune": (0.15, 0.4), "spill_gb": (80, 220),
        "small_files": (0.05, 0.25), "queued": (0.2, 0.45),
    },
    {
        "sql": "SELECT COUNT(*) FROM acme_prod.scada.sensor_raw WHERE tag_id LIKE '%COMP%'",
        "table": "acme_prod.scada.sensor_raw", "src": "SQL",
        # Tiny bytes but thousands of tiny files — OPTIMIZE candidate.
        "bytes_gb": (8, 45), "prune": (0.3, 0.6), "spill_gb": (0, 3),
        "small_files": (0.55, 0.92), "queued": (0.0, 0.15),
    },
    {
        "sql": "SELECT * FROM acme_finance.reporting.reporting_exec_summary",
        "table": "acme_finance.reporting.reporting_exec_summary", "src": "SQL",
        # SELECT * on a modest table with weak pruning — rewrite candidate.
        "bytes_gb": (55, 160), "prune": (0.2, 0.42), "spill_gb": (0, 5),
        "small_files": (0.1, 0.3), "queued": (0.0, 0.1),
    },
    {
        "sql": "MERGE INTO acme_gas.silver.pipeline_flow_hourly t USING staging s ON t.pk = s.pk WHEN MATCHED THEN UPDATE SET *",
        "table": "acme_gas.silver.pipeline_flow_hourly", "src": "DLT",
        # Frequent MERGE churning many small files + stale versions — vacuum/optimize.
        "bytes_gb": (30, 120), "prune": (0.35, 0.6), "spill_gb": (0, 8),
        "small_files": (0.5, 0.85), "queued": (0.0, 0.18),
        "stale": (0.4, 0.7),
    },
    {
        "sql": "SELECT customer_id, invoice_total FROM acme_prod.billing.customer_invoices WHERE region = 'VIC'",
        "table": "acme_prod.billing.customer_invoices", "src": "JOB",
        # Skewed predicate on unclustered table — clustering candidate.
        "bytes_gb": (90, 300), "prune": (0.1, 0.28), "spill_gb": (4, 30),
        "small_files": (0.1, 0.3), "queued": (0.05, 0.2),
    },
    {
        "sql": "SELECT * FROM acme_markets.gold.trade_positions WHERE book = 'GAS-EAST' AND asof = current_date()",
        "table": "acme_markets.gold.trade_positions", "src": "SQL",
        # Cross-region read (source in another geo) + capacity-bound.
        "bytes_gb": (60, 200), "prune": (0.25, 0.5), "spill_gb": (0, 10),
        "small_files": (0.1, 0.3), "queued": (0.22, 0.5),
    },
    {
        "sql": "SELECT AVG(flow_rate) FROM acme_gas.silver.compressor_runtime GROUP BY compressor_id, hour",
        "table": "acme_gas.silver.compressor_runtime", "src": "JOB",
        # Wide group-by scan with weak pruning + moderate spill — cluster candidate.
        "bytes_gb": (140, 460), "prune": (0.06, 0.24), "spill_gb": (20, 90),
        "small_files": (0.1, 0.3), "queued": (0.0, 0.15),
    },
    {
        # A materialized-view refresh whose ideal fix is to migrate the MV/ST onto
        # a DBSQL-backed standalone serverless pipeline.
        "sql": "CREATE OR REFRESH MATERIALIZED VIEW acme_markets.gold.settlement_mv AS SELECT * FROM acme_markets.gold.settlement_market",
        "table": "acme_markets.gold.settlement_market", "src": "DLT",
        "bytes_gb": (120, 380), "prune": (0.1, 0.28), "spill_gb": (2, 12),
        "small_files": (0.5, 0.8), "queued": (0.05, 0.2),
        "stale": (0.4, 0.65),
    },
]


@lru_cache(maxsize=1)
def _query_records() -> list[dict[str, Any]]:
    """Generate realistic raw statement metrics, then compute insights.

    We generate ONLY the observable metrics here (bytes read, pruning, spill,
    p95, small-file ratio, skew, queueing) and delegate all recommendation
    reasoning to ``data.recommender`` so the demo behaves like live system-table
    data driven through the same deterministic engine.
    """
    from data import recommender  # local import to avoid a cycle at module load

    rng = _rng()
    ws = _workspace_records()
    out: list[dict[str, Any]] = []
    for i in range(64):
        shape = rng.choice(_QUERY_SHAPES)
        w = rng.choice(ws)
        runs = rng.randint(12, 4200)
        p50 = round(rng.uniform(0.4, 40), 1)
        p95 = round(p50 * rng.uniform(1.8, 6.5), 1)
        bytes_read = round(rng.uniform(*shape["bytes_gb"]) * 1e9)
        prune = round(rng.uniform(*shape["prune"]), 3)
        spill_gb = round(rng.uniform(*shape["spill_gb"]), 1)
        small_file_ratio = round(rng.uniform(*shape["small_files"]), 3)
        queued_ratio = round(rng.uniform(*shape["queued"]), 3)
        stale_ratio = round(rng.uniform(*shape.get("stale", (0.02, 0.3))), 3)
        # Monthly cost model: runs × per-run compute-seconds × serverless $/DBU-sec
        # rate, plus a spill re-read penalty. Rates tuned so the query workload is a
        # plausible slice of the ~$642K/mo total (heavy queries land in the low
        # thousands $/mo each).
        cost = round(runs * p95 * rng.uniform(0.004, 0.02) + spill_gb * runs * 3e-4, 2)

        raw = {
            "id": i + 1,
            "statement_id": f"01f0{rng.randint(10**9, 10**10 - 1):x}",
            "query_text": shape["sql"],
            "target_table": shape["table"],
            "src": shape["src"],
            "warehouse": rng.choice(_WAREHOUSES),
            "workspace": w["workspace"],
            "bu": w.get("bu", ""),
            "user": rng.choice(_USERS),
            "runs": runs,
            "p50_s": p50,
            "p95_s": p95,
            "bytes_read": bytes_read,
            "pruning_efficiency": prune,
            "spill_gb": spill_gb,
            "small_file_ratio": small_file_ratio,
            "queued_ratio": queued_ratio,
            "stale_ratio": stale_ratio,
            "cost_usd": cost,
            "applied": rng.random() < 0.12,
        }
        out.append(recommender.enrich_query(raw))
    out.sort(key=lambda q: -q["impact"])
    return out


# ---------------------------------------------------------------------------
# Tables (Storage & Tables)
# ---------------------------------------------------------------------------
#
# Savings economics (SPEC §3.4). All figures are ESTIMATES; every table record
# carries an ``assumptions`` block so the UI can show the working. Constants:
#
#   STORAGE_USD_GB_MONTH  ~ cloud object-storage hot-tier list price (blended).
#   OPTIMIZE saving   ≈ scan_reduction_from_compaction × scan_cost_on_table
#                       where scan_reduction ≈ small_file_ratio × COMPACTION_GAIN.
#   VACUUM saving     ≈ stale_bytes × STORAGE_USD_GB_MONTH
#                       (stale_bytes = size_gb × stale_ratio).
#   CONVERT→MANAGED   unlocks PO ⇒ ongoing auto OPTIMIZE/VACUUM/ANALYZE cost
#                       avoidance; modelled as (optimize+vacuum saving) that PO
#                       captures automatically + a scan uplift from auto-LC.
#   ENABLE PO / LC    (managed, PO off) ⇒ PO ~50% storage + >2x query; LC ~95%
#                       faster scans on over-partitioned tables.
# ---------------------------------------------------------------------------

STORAGE_USD_GB_MONTH = 0.023          # blended ADLS Gen2 hot LRS list $/GB/mo
COMPACTION_GAIN = 0.60                # fraction of small-file scan waste OPTIMIZE removes
PO_STORAGE_SAVING = 0.50              # PO ~50% storage reduction (compaction + vacuum)
PO_QUERY_SPEEDUP = 2.0                # PO >2x query speed
LC_SCAN_SAVING = 0.95                 # LC up to 95% faster scans on over-partitioned tables
CONVERT_SCAN_UPLIFT = 0.35            # auto-LC scan reduction unlocked by going managed
OVER_PARTITION_FILES = 3000           # partitioned + many files ⇒ likely over-partitioned
TARGET_FILE_MB = 128                  # healthy avg file size target

@lru_cache(maxsize=1)
def _table_records() -> list[dict[str, Any]]:
    rng = _rng()
    out: list[dict[str, Any]] = []
    idx = 0
    for cat in _CATALOGS:
        for _ in range(rng.randint(3, 6)):
            schema = rng.choice(_SCHEMAS)
            name = rng.choice(_TABLE_NAMES)
            fqn = f"{cat}.{schema}.{name}_{idx}"
            table_type = "EXTERNAL" if rng.random() < 0.42 else "MANAGED"
            size_bytes = round(rng.uniform(2, 4200) * 1e9)
            num_files = rng.randint(40, 82_000)
            avg_file_mb = round((size_bytes / max(1, num_files)) / 1e6, 1)
            small_file_ratio = round(min(0.95, max(0.0, 1 - (avg_file_mb / TARGET_FILE_MB))), 3)
            partitioned = rng.random() < 0.5
            clustered = (table_type == "MANAGED") and rng.random() < 0.35
            po_enabled = (table_type == "MANAGED") and rng.random() < 0.4
            lc_enabled = clustered
            scan_cost = round(size_bytes / 1e9 * rng.uniform(0.02, 0.22), 2)
            storage_gb = size_bytes / 1e9
            stale_ratio = round(rng.uniform(0.05, 0.55), 3)

            # Over-partitioned = partitioned AND lots of files AND small files —
            # the classic case LC (CLUSTER BY AUTO) fixes.
            over_partitioned = bool(partitioned and num_files > OVER_PARTITION_FILES and small_file_ratio > 0.4)

            # --- reclaimable storage (VACUUM removes stale/tombstoned bytes) ---
            reclaimable_gb = round(storage_gb * stale_ratio, 2)
            reclaimable_usd_month = round(reclaimable_gb * STORAGE_USD_GB_MONTH, 2)

            # --- savings components (all estimated) ---
            # OPTIMIZE: compaction removes small-file scan waste.
            optimize_saving = round(scan_cost * small_file_ratio * COMPACTION_GAIN, 2)
            # VACUUM: stale bytes × storage $/GB/mo.
            vacuum_saving = reclaimable_usd_month
            # CONVERT→MANAGED (external only): unlocks PO auto-maintenance
            # (captures the optimize+vacuum saving automatically) + auto-LC scan uplift.
            convert_saving = round(
                (optimize_saving + vacuum_saving) + scan_cost * CONVERT_SCAN_UPLIFT, 2
            ) if table_type == "EXTERNAL" else 0.0
            # ENABLE PO/LC (managed without PO): PO storage + LC scan uplift.
            po_lc_saving = round(
                storage_gb * STORAGE_USD_GB_MONTH * PO_STORAGE_SAVING
                + scan_cost * (LC_SCAN_SAVING if over_partitioned else 0.35),
                2,
            ) if (table_type == "MANAGED" and not po_enabled) else 0.0

            # Headline est. savings = the recommended action's saving (avoids
            # double counting). Also expose OPTIMIZE+VACUUM as its own figure.
            optimize_vacuum_saving = round(optimize_saving + vacuum_saving, 2)
            rec = _table_recommendation(
                table_type, small_file_ratio, po_enabled, lc_enabled, stale_ratio, over_partitioned, partitioned
            )
            if rec["insight_type"] == "convert-to-managed":
                est_savings = convert_saving
            elif rec["insight_type"] in ("enable-PO", "enable-LC"):
                est_savings = po_lc_saving if po_lc_saving else optimize_vacuum_saving
            else:  # optimize / vacuum on a managed table
                est_savings = optimize_vacuum_saving
            est_savings = round(est_savings, 2)

            needs_optimize = small_file_ratio > 0.4
            needs_vacuum = stale_ratio > 0.35
            # Match the LIVE predicate exactly (live.py tables_live): a large table
            # that is neither partitioned nor clustered benefits from Liquid
            # Clustering. Was a different heuristic (over_partitioned OR managed-
            # without-LC/PO), which flagged a different set than live on the same data.
            needs_clustering = (not partitioned and not clustered and size_bytes >= 50e9)
            needs_action = bool(
                needs_optimize or needs_vacuum or needs_clustering or table_type == "EXTERNAL"
            )
            # Usage recency (from lineage in live) + cold-storage flag (#15):
            # large + rarely read = VACUUM/retention/archive candidate.
            reads_30d = rng.choice([0, 0, 0, 4, 12, 40, 180, 900, 2600])
            days_since_read = rng.choice([0, 1, 2, 5, 12]) if reads_30d else rng.choice([34, 61, 96, 141, 210])
            cold = bool((days_since_read > 30 or reads_30d == 0) and size_bytes >= 1e9)
            # --- usage profile (Chris feedback #6): frequency tier, 6-week read
            # trend, business-critical flag, and a data watermark / last-update. ---
            if reads_30d == 0 or cold:
                usage_tier = "Cold"
            elif reads_30d >= 180 and days_since_read <= 7:
                usage_tier = "Hot"
            else:
                usage_tier = "Warm"
            # 6-week read trend, reconciling to reads_30d (~last 4-5 weeks of it),
            # with mild drift so the sparkline reads naturally.
            _wk = reads_30d / 4.33 if reads_30d else 0
            usage_trend = [max(0, round(_wk * (1 + rng.uniform(-0.25, 0.25)))) for _ in range(6)] if reads_30d else [0] * 6
            # Business-critical: heavily-used curated/serving tables (gold/reporting)
            # or a hot table read very frequently — the assets to protect in any
            # optimisation work.
            business_critical = bool(
                (schema in ("gold", "reporting") and usage_tier in ("Hot", "Warm"))
                or (usage_tier == "Hot" and reads_30d >= 900)
            )
            # Watermark / last data update: hot tables written recently, cold tables
            # stale. Distinct from last_optimize/last_vacuum (maintenance ops).
            _last_update_days = rng.choice([0, 1, 2]) if usage_tier == "Hot" else (
                rng.choice([1, 3, 7, 14]) if usage_tier == "Warm" else rng.choice([30, 60, 120, 240]))
            last_updated = (_now() - timedelta(days=_last_update_days)).date().isoformat()

            out.append({
                "fqn": fqn,
                "catalog": cat,
                "schema": schema,
                "table": f"{name}_{idx}",
                "table_type": table_type,
                "size_bytes": size_bytes,
                "size_gb": round(storage_gb, 2),
                "num_files": num_files,
                "avg_file_mb": avg_file_mb,
                "small_file_ratio": small_file_ratio,
                "last_optimize": (_now() - timedelta(days=rng.randint(1, 180))).date().isoformat(),
                "last_vacuum": (_now() - timedelta(days=rng.randint(1, 240))).date().isoformat(),
                "partitioned": partitioned,
                "over_partitioned": over_partitioned,
                "clustered": clustered,
                "po_enabled": po_enabled,
                "lc_enabled": lc_enabled,
                "stale_ratio": stale_ratio,
                "reclaimable_gb": reclaimable_gb,
                "reclaimable_usd_month": reclaimable_usd_month,
                "scan_cost_usd_month": scan_cost,
                # savings breakdown (all estimated)
                "optimize_saving_usd_month": optimize_saving,
                "vacuum_saving_usd_month": vacuum_saving,
                "convert_saving_usd_month": convert_saving,
                "po_lc_saving_usd_month": po_lc_saving,
                "est_savings_usd_month": est_savings,
                # action flags
                "needs_optimize": needs_optimize,
                "needs_vacuum": needs_vacuum,
                "needs_clustering": needs_clustering,
                "needs_action": needs_action,
                # usage recency + cold-storage (#15)
                "reads_30d": reads_30d,
                "days_since_read": days_since_read,
                "cold": cold,
                # usage profile + lifecycle (Chris feedback #6)
                "usage_tier": usage_tier,
                "usage_trend": usage_trend,
                "business_critical": business_critical,
                "last_updated": last_updated,
                # recommendation
                "recommendation": rec["insight_type"],
                "rationale": rec["rationale"],
                "next_steps": rec["next_steps"],
                "benefits": rec.get("benefits", []),
                "conversion_path": rec.get("conversion_path"),
                "caveat": rec.get("caveat"),
            })
            idx += 1
    out.sort(key=lambda t: -t["est_savings_usd_month"])
    return out


def _table_recommendation(
    ttype: str, small_files: float, po: bool, lc: bool, stale: float,
    over_partitioned: bool, partitioned: bool,
) -> dict[str, Any]:
    if ttype == "EXTERNAL":
        return {
            "insight_type": "convert-to-managed",
            "rationale": (
                "External table — convert to UC managed to unlock Predictive Optimization "
                "(auto OPTIMIZE/VACUUM/ANALYZE), Liquid Clustering, and catalog-managed commits."
            ),
            "benefits": [
                "Predictive Optimization: automatic OPTIMIZE / VACUUM / ANALYZE (no manual jobs)",
                "Liquid Clustering via CLUSTER BY AUTO — replaces manual partitioning",
                "Catalog-managed commits — coordinated writes, safer concurrent access",
                "~50% storage reduction + >2x query speed once PO is active (estimated)",
            ],
            "conversion_path": {
                "recommended": "DEEP CLONE",
                "steps": [
                    "-- Option A: DEEP CLONE (preserves history, fastest)",
                    "CREATE TABLE <catalog>.<schema>.<table>_managed",
                    "  DEEP CLONE <catalog>.<schema>.<external_table>;",
                    "",
                    "-- Option B: CTAS (fresh managed table)",
                    "CREATE TABLE <catalog>.<schema>.<table>_managed",
                    "  AS SELECT * FROM <catalog>.<schema>.<external_table>;",
                    "",
                    "-- Then enable auto-maintenance on the managed table:",
                    "ALTER TABLE <catalog>.<schema>.<table>_managed CLUSTER BY AUTO;",
                    "ALTER TABLE <catalog>.<schema>.<table>_managed",
                    "  SET TBLPROPERTIES ('delta.predictiveOptimization' = 'ENABLE');",
                ],
            },
            "next_steps": [
                "DEEP CLONE (or CTAS) the external table into a UC managed table",
                "Repoint downstream jobs / views to the managed table",
                "Enable Predictive Optimization + CLUSTER BY AUTO on the managed table",
                "Verify auto OPTIMIZE/VACUUM runs in system.storage / PO system tables",
            ],
        }
    if not po:
        base = (
            "Managed table without Predictive Optimization — enable PO + CLUSTER BY AUTO "
            "(PO ~50% storage savings and >2x query speed; Liquid Clustering replaces partitioning"
        )
        base += (
            ", ~95% faster scans on this over-partitioned table)."
            if over_partitioned
            else ")."
        )
        return {
            "insight_type": "enable-PO",
            "rationale": base,
            "benefits": [
                "Predictive Optimization: ~50% storage savings + >2x query speed (estimated)",
                "CLUSTER BY AUTO: auto column selection; ~95% faster scans on over-partitioned tables",
                "No manual OPTIMIZE/VACUUM/ANALYZE scheduling — PO runs on serverless jobs compute",
            ],
            "caveat": (
                "DLT / Lakeflow Declarative Pipelines caveat: CLUSTER BY AUTO requires DBR 15.4 LTS+ "
                "AND Predictive Optimization enabled. There is a known validation bug — enable PO at the "
                "target CATALOG/SCHEMA level BEFORE pipeline validation (pipeline-created tables don't "
                "reliably inherit PO). If validation still fails, raise an Azure support case."
            ),
            "next_steps": [
                "ALTER TABLE ... SET TBLPROPERTIES ('delta.predictiveOptimization' = 'ENABLE')",
                "ALTER TABLE ... CLUSTER BY AUTO",
                "For DLT: enable PO on the target catalog/schema first (known validation bug)",
                "Monitor auto-maintenance in the PO system tables",
            ],
        }
    if small_files > 0.4:
        return {
            "insight_type": "optimize",
            "rationale": "High small-file ratio — run OPTIMIZE to compact files and reduce scan cost.",
            "benefits": ["Fewer files ⇒ faster scans", "Lower scan $/mo", "PO will keep it compacted going forward"],
            "next_steps": ["Run OPTIMIZE", f"Verify avg file size >= {TARGET_FILE_MB}MB", "Confirm PO is scheduled"],
        }
    if stale > 0.35:
        return {
            "insight_type": "vacuum",
            "rationale": "Significant stale/tombstoned bytes — run VACUUM to reclaim storage.",
            "benefits": ["Reclaims stale storage $", "Smaller table footprint", "PO will VACUUM automatically going forward"],
            "next_steps": ["Run VACUUM (retain 7 days)", "Confirm reclaim in system.storage", "Rely on PO going forward"],
        }
    return {
        "insight_type": "enable-LC",
        "rationale": "Well-maintained managed table — confirm Liquid Clustering (CLUSTER BY AUTO) on key predicates for continued pruning.",
        "benefits": ["Sustained pruning as data grows", "No partition maintenance", "PO auto-selects clustering keys"],
        "next_steps": ["Review query predicates", "CLUSTER BY AUTO if not set", "Monitor pruning efficiency"],
    }


def _tables_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """KPI aggregation for the Tables page (reconciles to the row set)."""
    n = len(rows)
    managed = sum(1 for r in rows if r["table_type"] == "MANAGED")
    # Count EXTERNAL explicitly (not n − managed): live now passes VIEW/other types
    # through, and counting every non-MANAGED row as external inflated num_external
    # and deflated pct_managed on any estate with views. A view is neither managed
    # nor external, so the managed/external split is over MANAGED+EXTERNAL only.
    external = sum(1 for r in rows if r["table_type"] == "EXTERNAL")
    tables_me = managed + external
    reclaimable_gb = round(sum(r["reclaimable_gb"] for r in rows), 2)
    reclaimable_usd = round(sum(r["reclaimable_usd_month"] for r in rows), 2)
    return {
        "num_tables": n,
        "num_managed": managed,
        "num_external": external,
        "pct_managed": round(managed / tables_me, 3) if tables_me else 0.0,
        "reclaimable_gb": reclaimable_gb,
        "reclaimable_usd_month": reclaimable_usd,
        "num_need_optimize": sum(1 for r in rows if r["needs_optimize"]),
        "num_need_clustering": sum(1 for r in rows if r["needs_clustering"]),
    }


# ---------------------------------------------------------------------------
# Access — bidirectional grant graph (principals, groups, entitlements,
# object grants, effective resolution, policies, PATs, risks)
# ---------------------------------------------------------------------------
#
# Data model (mirrors the real system tables it stands in for):
#   * principals              — users / service principals / groups
#   * group membership        — direct edges, incl. nested groups; resolved
#                               transitively for "effective" access
#   * entitlements            — workspace-level capabilities (workspace-access,
#                               allow-cluster-create, databricks-sql-access, …)
#   * grants                  — UC object grants (catalog/schema/table/volume/
#                               function), privilege, DIRECT vs INHERITED, and
#                               granted directly-to-principal vs via a group
#   * policies                — row filters / column masks / ABAC in effect
#   * workspace assignments   — which workspaces + USER/ADMIN
#   * compute ACLs            — SQL-warehouse + cluster-policy grants
#   * PAT inventory           — personal access tokens + age
#
# Real sources (documented in queries.sql):
#   system.information_schema.{catalog,schema,table,volume,routine}_privileges,
#   system.access.audit (last-access), account SCIM/groups + workspace-
#   assignments APIs. Effective privilege = union of direct grants +
#   grants inherited from parent securables + grants held via (nested) groups.

# --- Group membership graph (nested groups included) ------------------------
# key = group, value = list of member principals (users, SPs, or sub-groups).
_GROUP_MEMBERS: dict[str, list[str]] = {
    "data-platform-admins": [
        "priya.nathan@acme.com", "james.oleary@acme.com", "svc-lakeflow-orchestrator",
    ],
    "gas-transmission-analysts": [
        "wei.zhang@acme.com", "daniel.foster@acme.com", "scada-ot-readers",
    ],
    "energy-markets-traders": [
        "raj.patel@acme.com", "sarah.mckenzie@acme.com",
    ],
    "metering-engineers": [
        "hannah.wu@acme.com", "svc-nem12-ingest",
    ],
    "finance-fpna": [
        "sarah.mckenzie@acme.com",
    ],
    "data-science-team": [
        "wei.zhang@acme.com", "hannah.wu@acme.com",
    ],
    # bi-consumers is a broad umbrella that nests several BU groups.
    "bi-consumers": [
        "gas-transmission-analysts", "energy-markets-traders", "finance-fpna",
        "daniel.foster@acme.com",
    ],
    "scada-ot-readers": [
        "daniel.foster@acme.com",
    ],
    "billing-etl-svc": [
        "svc-billing-etl",
    ],
    # account users is the implicit all-workspace group (PUBLIC-like).
    "account users": [
        "bi-consumers", "data-platform-admins", "metering-engineers",
        "data-science-team", "billing-etl-svc",
    ],
}

_ALL_ENTITLEMENTS = [
    "workspace-access",
    "databricks-sql-access",
    "allow-cluster-create",
    "allow-instance-pool-create",
]

# Per-group entitlement + workspace-assignment templates (the "policy" side).
_GROUP_ENTITLEMENTS: dict[str, list[str]] = {
    "data-platform-admins": ["workspace-access", "databricks-sql-access", "allow-cluster-create", "allow-instance-pool-create"],
    "gas-transmission-analysts": ["workspace-access", "databricks-sql-access"],
    "energy-markets-traders": ["workspace-access", "databricks-sql-access", "allow-cluster-create"],
    "metering-engineers": ["workspace-access", "databricks-sql-access", "allow-cluster-create"],
    "finance-fpna": ["workspace-access", "databricks-sql-access"],
    "data-science-team": ["workspace-access", "databricks-sql-access", "allow-cluster-create"],
    "bi-consumers": ["workspace-access", "databricks-sql-access"],
    "scada-ot-readers": ["workspace-access"],
    "billing-etl-svc": ["workspace-access", "allow-cluster-create"],
    "account users": ["workspace-access"],
}

_GROUP_WORKSPACES: dict[str, list[tuple[str, str]]] = {
    "data-platform-admins": [("DATA-EDP-PLATFORM-PROD", "ADMIN"), ("DATA-EDP-GAS-PROD", "ADMIN"), ("DATA-EDP-MARKETS-PROD", "ADMIN")],
    "gas-transmission-analysts": [("DATA-EDP-GAS-PROD", "USER"), ("DATA-ANA-ASSET-PROD", "USER")],
    "energy-markets-traders": [("DATA-EDP-MARKETS-PROD", "USER"), ("DATA-ANA-BI-PROD", "USER")],
    "metering-engineers": [("DATA-EDP-METERING-PROD", "USER"), ("DATA-EDP-BILLING-PROD", "USER")],
    "finance-fpna": [("DATA-ANA-FINANCE-PROD", "USER"), ("DATA-ANA-BI-PROD", "USER")],
    "data-science-team": [("FORE-DATASCIENCE-DEV", "USER")],
    "bi-consumers": [("DATA-ANA-BI-PROD", "USER")],
    "scada-ot-readers": [("DATA-ANA-SCADA-NONPROD", "USER")],
    "billing-etl-svc": [("DATA-EDP-BILLING-PROD", "USER")],
    "account users": [],
}


def _uc_securables() -> list[dict[str, Any]]:
    """Build the securable universe: catalogs, schemas, tables, volumes,
    functions — hierarchical so grants can be inherited down the tree."""
    rng = _rng()
    secs: list[dict[str, Any]] = []
    for cat in _CATALOGS:
        secs.append({"type": "catalog", "fqn": cat, "catalog": cat, "parent": None})
        schemas = rng.sample(_SCHEMAS, k=rng.randint(3, 5))
        for sch in schemas:
            sfqn = f"{cat}.{sch}"
            secs.append({"type": "schema", "fqn": sfqn, "catalog": cat, "parent": cat})
            for _ in range(rng.randint(2, 4)):
                name = rng.choice(_TABLE_NAMES)
                tfqn = f"{cat}.{sch}.{name}_{len(secs)}"
                secs.append({"type": "table", "fqn": tfqn, "catalog": cat, "parent": sfqn})
            # a volume + a function per schema for breadth
            secs.append({"type": "volume", "fqn": f"{sfqn}.vol_{len(secs)}", "catalog": cat, "parent": sfqn})
            secs.append({"type": "function", "fqn": f"{sfqn}.fn_{len(secs)}", "catalog": cat, "parent": sfqn})
    return secs


# Which privileges make sense on which securable type.
_PRIV_BY_TYPE = {
    "catalog": ["USE_CATALOG", "CREATE_SCHEMA", "ALL PRIVILEGES"],
    "schema": ["USE_SCHEMA", "CREATE_TABLE", "SELECT", "MODIFY", "ALL PRIVILEGES"],
    "table": ["SELECT", "MODIFY", "ALL PRIVILEGES"],
    "volume": ["READ_VOLUME", "WRITE_VOLUME"],
    "function": ["EXECUTE"],
}


@lru_cache(maxsize=1)
def group_members(group: str) -> list[str]:
    """Direct members (may include sub-groups)."""
    return list(_GROUP_MEMBERS.get(group, []))


@lru_cache(maxsize=1)
def _all_groups() -> set[str]:
    return set(_GROUP_MEMBERS.keys())


def _resolve_effective_groups(principal: str, _seen: frozenset[str] = frozenset()) -> set[str]:
    """All groups a principal belongs to, transitively (nested groups)."""
    if principal in _seen:
        return set()
    seen = _seen | {principal}
    out: set[str] = set()
    for g, members in _GROUP_MEMBERS.items():
        if principal in members:
            out.add(g)
            out |= _resolve_effective_groups(g, seen)
    return out


def _direct_members_of_type(group: str, kind: str) -> list[str]:
    return [m for m in _GROUP_MEMBERS.get(group, []) if (m in _all_groups()) == (kind == "group")]


@lru_cache(maxsize=1)
def _principal_records() -> list[dict[str, Any]]:
    rng = _rng()
    out: list[dict[str, Any]] = []
    grants_by_p = _grants_by_principal()
    ent_by_p = _entitlements_by_principal()
    for name, source, members in _GROUPS:
        out.append({
            "principal": name,
            "type": "group",
            "source": source,
            "status": "active",
            "last_active": (_now() - timedelta(days=rng.randint(0, 30))).date().isoformat(),
            "num_entitlements": len(_GROUP_ENTITLEMENTS.get(name, [])),
            "num_grants": len(grants_by_p.get(name, [])),
            "member_count": members,
            "num_direct_members": len(_GROUP_MEMBERS.get(name, [])),
        })
    for u in _USERS:
        is_sp = u.startswith("svc-")
        out.append({
            "principal": u,
            "type": "service_principal" if is_sp else "user",
            "source": "SCIM" if is_sp else ("AIM" if not is_sp else "SCIM"),
            "status": "active" if rng.random() < 0.9 else "inactive",
            "last_active": (_now() - timedelta(days=rng.randint(0, 120))).date().isoformat(),
            "num_entitlements": len(ent_by_p.get(u, [])),
            "num_grants": len(grants_by_p.get(u, [])),
            "member_count": None,
            "num_direct_members": None,
        })
    return out


@lru_cache(maxsize=1)
def _grant_records() -> list[dict[str, Any]]:
    """The DIRECT grant edges in the graph: (grantee-principal, securable,
    privilege). Grantees are mostly groups (best practice); a realistic slice
    are direct-to-user (flagged as a risk elsewhere)."""
    rng = _rng()
    secs = _uc_securables()
    groups = [g[0] for g in _GROUPS]
    users = list(_USERS)
    out: list[dict[str, Any]] = []
    gid = 0

    def emit(principal: str, ptype: str, sec: dict[str, Any], priv: str, granted_via: str = "direct") -> None:
        nonlocal gid
        out.append({
            "id": gid,
            "principal": principal,
            "principal_type": ptype,
            "securable_type": sec["type"],
            "securable": sec["fqn"],
            "catalog": sec["catalog"],
            "parent": sec["parent"],
            "privilege": priv,
            "granted_via": granted_via,  # 'direct' | 'group'
        })
        gid += 1

    # 1. Anchored, business-shaped grants so both modes read realistically.
    anchored = [
        ("gas-transmission-analysts", "acme_gas", "USE_CATALOG"),
        ("gas-transmission-analysts", "acme_gas.silver", "SELECT"),
        ("gas-transmission-analysts", "acme_gas.gold", "SELECT"),
        ("energy-markets-traders", "acme_markets", "USE_CATALOG"),
        ("energy-markets-traders", "acme_markets.gold", "SELECT"),
        ("energy-markets-traders", "acme_markets.silver", "MODIFY"),
        ("metering-engineers", "acme_metering", "USE_CATALOG"),
        ("metering-engineers", "acme_metering.bronze", "MODIFY"),
        ("metering-engineers", "acme_metering.nem12", "ALL PRIVILEGES"),
        ("finance-fpna", "acme_finance", "USE_CATALOG"),
        ("finance-fpna", "acme_finance.reporting", "SELECT"),
        ("bi-consumers", "acme_prod", "USE_CATALOG"),
        ("bi-consumers", "acme_prod.gold", "SELECT"),
        ("data-platform-admins", "acme_prod", "ALL PRIVILEGES"),
        ("data-platform-admins", "acme_gas", "ALL PRIVILEGES"),
        ("data-science-team", "acme_prod.silver", "SELECT"),
        ("scada-ot-readers", "acme_gas.scada", "SELECT"),
        # direct-to-user (deliberate risk material)
        ("raj.patel@acme.com", "acme_markets.gold", "MODIFY"),
        ("wei.zhang@acme.com", "acme_prod.gold", "ALL PRIVILEGES"),
        ("daniel.foster@acme.com", "acme_gas.gold", "MODIFY"),
        # broad PUBLIC-like grant
        ("account users", "acme_prod.gold", "SELECT"),
    ]
    by_fqn = {s["fqn"]: s for s in secs}
    for principal, fqn, priv in anchored:
        sec = by_fqn.get(fqn)
        if sec is None:
            # synthesise a schema securable if the sampled tree missed it
            cat = fqn.split(".")[0]
            sec = {"type": "schema" if "." in fqn else "catalog", "fqn": fqn, "catalog": cat, "parent": cat if "." in fqn else None}
        ptype = "group" if principal in _all_groups() else ("service_principal" if principal.startswith("svc-") else "user")
        emit(principal, ptype, sec, priv)

    # 2. A spread of additional grants for volume/coverage.
    for _ in range(70):
        grantee_is_group = rng.random() < 0.72
        principal = rng.choice(groups) if grantee_is_group else rng.choice(users)
        sec = rng.choice(secs)
        priv = rng.choice(_PRIV_BY_TYPE[sec["type"]])
        ptype = "group" if grantee_is_group else ("service_principal" if principal.startswith("svc-") else "user")
        emit(principal, ptype, sec, priv)

    return out


@lru_cache(maxsize=1)
def _grants_by_principal() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for g in _grant_records():
        out.setdefault(g["principal"], []).append(g)
    return out


@lru_cache(maxsize=1)
def _entitlements_by_principal() -> dict[str, list[str]]:
    """Direct entitlements per principal.

    Groups carry the template entitlements; users/SPs get a small direct set
    plus inherit their groups' entitlements at resolution time."""
    rng = _rng()
    out: dict[str, list[str]] = {}
    for g, ents in _GROUP_ENTITLEMENTS.items():
        out[g] = list(ents)
    for u in _USERS:
        # SPs typically only get workspace-access directly; humans a bit more
        base = ["workspace-access"]
        if not u.startswith("svc-") and rng.random() < 0.5:
            base.append("databricks-sql-access")
        out[u] = base
    return out


@lru_cache(maxsize=1)
def _pat_records() -> list[dict[str, Any]]:
    rng = _rng()
    out: list[dict[str, Any]] = []
    pid = 0
    # a handful of humans + SPs hold PATs of varying age
    holders = ["priya.nathan@acme.com", "james.oleary@acme.com", "raj.patel@acme.com",
               "svc-nem12-ingest", "svc-billing-etl", "wei.zhang@acme.com"]
    for h in holders:
        age = rng.randint(12, 410)
        out.append({
            "id": pid,
            "principal": h,
            "comment": rng.choice(["ci-pipeline", "adhoc-notebook", "bi-tool-connector", "etl-job", "legacy-script"]),
            "age_days": age,
            "last_used_days": rng.randint(0, min(age, 120)),
            "stale": age > 90,
        })
        pid += 1
    return out


# --- Effective-access resolution (used by principal-centric mode) -----------

def _effective_entitlements(principal: str) -> list[dict[str, Any]]:
    """Direct + group-inherited entitlements with provenance."""
    ent_map = _entitlements_by_principal()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()  # dedup by entitlement; direct provenance wins
    for e in ent_map.get(principal, []):
        if e not in seen:
            rows.append({"entitlement": e, "via": "direct", "source_group": None})
            seen.add(e)
    for grp in sorted(_resolve_effective_groups(principal)):
        for e in ent_map.get(grp, []):
            if e not in seen:
                rows.append({"entitlement": e, "via": "group", "source_group": grp})
                seen.add(e)
    return rows


def _effective_workspaces(principal: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    groups = {principal} | _resolve_effective_groups(principal) if principal in _all_groups() else _resolve_effective_groups(principal)
    # a group's own assignment applies to itself too
    if principal in _all_groups():
        groups = {principal} | _resolve_effective_groups(principal)
    for grp in sorted(groups):
        for ws, perm in _GROUP_WORKSPACES.get(grp, []):
            if ws not in seen:
                rows.append({"workspace": ws, "permission": perm, "source_group": None if grp == principal else grp})
                seen.add(ws)
    return rows


def _effective_grants(principal: str) -> list[dict[str, Any]]:
    """Object grants held directly + via (nested) groups, with inheritance
    down the securable tree annotated."""
    grants_by_p = _grants_by_principal()
    groups = _resolve_effective_groups(principal)
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def push(g: dict[str, Any], via_group: str | None) -> None:
        key = (g["securable"], g["privilege"])
        if key in seen:
            return
        seen.add(key)
        rows.append({
            "securable": g["securable"],
            "securable_type": g["securable_type"],
            "catalog": g["catalog"],
            "privilege": g["privilege"],
            "granted_via": "direct" if via_group is None else "group",
            "source_group": via_group,
            # a catalog/schema-level grant is inherited by children
            "inherited": g["securable_type"] in ("catalog", "schema"),
        })

    for g in grants_by_p.get(principal, []):
        push(g, None)
    for grp in sorted(groups):
        for g in grants_by_p.get(grp, []):
            push(g, grp)
    return rows


def _securable_grants(securable: str) -> list[dict[str, Any]]:
    """Who can access this securable? Direct grants on it + grants inherited
    from its parent catalog/schema."""
    secs = {s["fqn"]: s for s in _uc_securables()}
    grants_by_sec: dict[str, list[dict[str, Any]]] = {}
    for g in _grant_records():
        grants_by_sec.setdefault(g["securable"], []).append(g)

    rows: list[dict[str, Any]] = []
    # chain of ancestors (self, parent, grandparent…)
    chain = [securable]
    cur = secs.get(securable)
    while cur and cur.get("parent"):
        chain.append(cur["parent"])
        cur = secs.get(cur["parent"])

    for i, fqn in enumerate(chain):
        for g in grants_by_sec.get(fqn, []):
            rows.append({
                "id": g["id"],
                "principal": g["principal"],
                "principal_type": g["principal_type"],
                "privilege": g["privilege"],
                "granted_via": g["granted_via"],
                "inherited": i > 0,  # inherited if it came from an ancestor
                "inherited_from": None if i == 0 else fqn,
            })
    return rows


def _securable_policies(securable: str) -> list[dict[str, Any]]:
    """Row filters / column masks / ABAC policies in effect on a securable.
    Deterministic from the fqn so the same table always shows the same set
    (builtin hash() is PYTHONHASHSEED-salted → not stable across restarts)."""
    h = _stable_hash(securable)
    pols: list[dict[str, Any]] = []
    if "gold" in securable or "reporting" in securable or (h % 3 == 0):
        pols.append({
            "kind": "row_filter", "name": "bu_scope_filter",
            "detail": "Row filter restricts rows to the caller's business unit (is_account_group_member('<bu>-analysts'))",
        })
    if "billing" in securable or "customer" in securable or "invoice" in securable or (h % 4 == 0):
        pols.append({
            "kind": "column_mask", "name": "pii_mask",
            "detail": "Column mask redacts customer_id / email unless caller is in finance-fpna or data-platform-admins",
        })
    if "market" in securable or "trade" in securable or (h % 5 == 0):
        pols.append({
            "kind": "abac", "name": "sensitivity_high",
            "detail": "ABAC policy denies SELECT on columns tagged sensitivity=high unless principal carries clearance=markets",
        })
    return pols


@lru_cache(maxsize=1)
def _risk_records() -> list[dict[str, Any]]:
    rng = _rng()
    grants = _grant_records()
    pats = _pat_records()
    risks: list[dict[str, Any]] = []
    rid = 0
    seen: set[tuple[str, str, str]] = set()

    def add(principal: str, kind: str, severity: str, detail: str, action: str) -> None:
        nonlocal rid
        key = (principal, kind, detail)
        if key in seen:
            return
        seen.add(key)
        risks.append({
            "id": rid, "principal": principal, "risk": kind, "severity": severity,
            "detail": detail, "recommended_action": action,
        })
        rid += 1

    for g in grants:
        # Over-privileged = any MODIFY / ALL PRIVILEGES grant (not just prod) —
        # matches the live rule (access_grants_live), which flags it on any
        # catalog. Prod / ALL PRIVILEGES drives CRITICAL severity, others WARNING.
        # (Was prod-only, which silently dropped real over-privilege on non-prod
        # catalogs and diverged from live.)
        if g["privilege"] in ("MODIFY", "ALL PRIVILEGES"):
            prod_named = "prod" in g["catalog"] or "prod" in g["securable"]
            sev = "critical" if (g["privilege"] == "ALL PRIVILEGES" or prod_named) else "warning"
            add(g["principal"], "over-privileged", sev,
                f"{g['privilege']} on {g['securable']}",
                "Downgrade to SELECT or scope via a dedicated group")
        if g["granted_via"] == "direct" and g["principal_type"] == "user" and g["privilege"] in ("MODIFY", "ALL PRIVILEGES"):
            add(g["principal"], "direct-to-user", "warning",
                f"Direct {g['privilege']} grant on {g['securable']} (should be via a group)",
                "Move grant to an appropriate group")
    add("account users", "broad-grant", "critical",
        "SELECT granted to `account users` (PUBLIC-like) on acme_prod.gold",
        "Replace with a scoped consumer group (e.g. bi-consumers)")
    add("data-platform-admins", "admin-sprawl", "warning",
        f"{_GROUPS[0][2]} members with ALL PRIVILEGES on acme_prod + acme_gas (metastore-adjacent)",
        "Reduce standing admin; adopt just-in-time elevation")
    for p in pats:
        if p["stale"]:
            add(p["principal"], "stale-token", "warning",
                f"Personal access token '{p['comment']}' age {p['age_days']} days (last used {p['last_used_days']}d ago)",
                "Rotate or revoke; adopt scoped / short-lived tokens")
    add("svc-billing-etl", "external-write", "warning",
        "WRITE_VOLUME + write access to external location abfss://acme-billing@...",
        "Confirm least-privilege; prefer UC managed tables")
    return risks


# ---------------------------------------------------------------------------
# Governance
# ---------------------------------------------------------------------------

def _tier(val: float, good: float, warn: float, invert: bool = False) -> str:
    """Score a metric into Good / Warning / Critical.

    Non-inverted: higher is better (>= good ⇒ Good). Inverted: lower is
    better (<= good ⇒ Good) — used for "bad" metrics like external-data %
    or over-privileged counts.
    """
    if invert:
        if val <= good:
            return "Good"
        if val <= warn:
            return "Warning"
        return "Critical"
    if val >= good:
        return "Good"
    if val >= warn:
        return "Warning"
    return "Critical"


# Per-tile score points contributed towards the 0-100 governance score.
_STATUS_SCORE = {"Good": 1.0, "Warning": 0.5, "Critical": 0.0}


# Manually-tuned tagging coverage per workspace (demo). Keyed by workspace
# name; anything not listed falls back to the overall figure. Kept internally
# consistent with automation maturity — more mature workspaces tag better.
def _tagging_by_workspace() -> list[dict[str, Any]]:
    ws = _workspace_records()
    rng = random.Random(SEED + 991)
    rows: list[dict[str, Any]] = []
    for w in ws:
        # tagging correlates loosely with automation maturity
        base = 0.30 + 0.55 * w["automated_pct"] + rng.uniform(-0.08, 0.08)
        cov = round(min(0.98, max(0.08, base)), 3)
        untagged = round(w["spend_usd_month"] * (1 - cov))
        rows.append({
            "workspace": w["workspace"],
            "bu": w["bu"],
            "spend_usd_month": w["spend_usd_month"],
            "tagging_pct": cov,
            "untagged_usd_month": untagged,
            "status": _tier(cov, 0.8, 0.5),
        })
    rows.sort(key=lambda r: -r["untagged_usd_month"])
    return rows


def _governance_tiles() -> list[dict[str, Any]]:
    """Twelve+ scored governance tiles. Each carries a category, weight, and —
    where relevant — the source page it ties to.

    Figures reconcile to the other pages' demo data:
    - external-data %, PO/LC adoption ← _table_records()
    - over-privileged / PAT counts    ← _risk_records()
    - untagged spend                  ← _workspace_records()
    """
    ws = _workspace_records()
    tables = _table_records()
    risks = _risk_records()
    tag_rows = _tagging_by_workspace()

    n_tables = len(tables)
    n_ext = sum(1 for t in tables if t["table_type"] == "EXTERNAL")
    n_managed = n_tables - n_ext
    pct_ext = n_ext / n_tables
    # PO / LC adoption expressed against MANAGED tables (external can't have them)
    po_adopt = (sum(1 for t in tables if t["po_enabled"]) / n_managed) if n_managed else 0.0
    lc_adopt = (sum(1 for t in tables if t["lc_enabled"]) / n_managed) if n_managed else 0.0
    # Distinct PRINCIPALS with an over-privileged risk (not record count) — matches
    # the tile label ("N principals hold …") and the live tile's measure.
    over_priv = len({r["principal"] for r in risks if r["risk"] == "over-privileged"})
    stale_tokens = sum(1 for r in risks if r["risk"] == "stale-token")
    total_spend = sum(w["spend_usd_month"] for w in ws)

    # weighted overall tagging coverage (weighted by spend) + total untagged $
    total_untagged = sum(r["untagged_usd_month"] for r in tag_rows)
    tagging = 1 - (total_untagged / total_spend) if total_spend else 0.0

    n_ws = len(ws)

    # budget policies rolled out to the 4 most mature workspaces (demo)
    n_budget = 4
    budget_cov = n_budget / n_ws

    def t(status: str) -> str:
        return status

    tiles = [
        {"metric": "Tagging coverage", "category": "Cost attribution", "weight": 3,
         "value_pct": round(tagging, 3), "status": _tier(tagging, 0.8, 0.5),
         "gap": f"{round((1 - tagging) * 100)}% of spend untagged across {n_ws} workspaces",
         "action": "Enforce tag policies at account + serverless budget policies",
         "ties_to": "workspaces"},
        {"metric": "Untagged spend", "category": "Cost attribution", "weight": 2,
         "value_usd": round(total_untagged), "status": _tier(tagging, 0.8, 0.5),
         "gap": f"${round(total_untagged):,}/mo cannot be attributed to a BU / cost centre",
         "action": "Apply mandatory cost-centre + environment tags; block untagged compute",
         "ties_to": "workspaces"},
        {"metric": "Budget-policy coverage", "category": "Budgets & alerts", "weight": 2,
         "value_pct": round(budget_cov, 3), "status": _tier(budget_cov, 0.75, 0.4),
         "gap": f"{n_ws - n_budget} of {n_ws} workspaces without a serverless budget policy",
         "action": "Roll out serverless budget policies",
         "ties_to": "workspaces"},
        {"metric": "Account-level budgets", "category": "Budgets & alerts", "weight": 2,
         "value_count": 2, "status": _tier(2, 4, 1),
         "gap": "2 of a planned 6 account budgets configured; no threshold alerts on prod",
         "action": "Configure Account Console budgets + email alert thresholds",
         "ties_to": "overview"},
        {"metric": "Alert / anomaly setup", "category": "Budgets & alerts", "weight": 2,
         "value_pct": 0.25, "status": _tier(0.25, 0.7, 0.4),
         "gap": "No spend-anomaly alerts; 3 unreviewed cost spikes in system.billing.usage",
         "action": "Wire SQL alerts on billing.usage + subscribe FinOps + BU owners",
         "ties_to": "overview"},
        {"metric": "Predictive Optimization adoption", "category": "Storage optimisation", "weight": 3,
         "value_pct": round(po_adopt, 3), "status": _tier(po_adopt, 0.7, 0.4),
         "gap": f"{n_managed - sum(1 for tb in tables if tb['po_enabled'])} of {n_managed} managed tables without auto-maintenance",
         "action": "Enable PO (auto OPTIMIZE/VACUUM/ANALYZE) at catalog level",
         "ties_to": "tables"},
        {"metric": "Liquid Clustering adoption", "category": "Storage optimisation", "weight": 2,
         "value_pct": round(lc_adopt, 3), "status": _tier(lc_adopt, 0.6, 0.3),
         "gap": f"Only {sum(1 for tb in tables if tb['lc_enabled'])} of {n_managed} managed tables use CLUSTER BY; over-partitioned tables remain",
         "action": "Apply CLUSTER BY AUTO on hot tables (enable PO on target schema first)",
         "ties_to": "tables"},
        {"metric": "External / unmanaged data", "category": "Storage optimisation", "weight": 2,
         "value_pct": round(pct_ext, 3), "status": _tier(pct_ext, 0.15, 0.35, invert=True),
         "gap": f"{n_ext} of {n_tables} tables are EXTERNAL — miss PO, LC & catalog-managed commits",
         "action": "Convert external tables to UC managed (DEEP CLONE / CTAS)",
         "ties_to": "tables"},
        {"metric": "Certification coverage", "category": "Data governance", "weight": 1,
         "value_pct": 0.41, "status": _tier(0.41, 0.7, 0.35),
         "gap": "59% of key gold tables lack a certified badge",
         "action": "Apply system.certification_status governed tag via a rule engine",
         "ties_to": "tables"},
        {"metric": "Lineage coverage", "category": "Data governance", "weight": 1,
         "value_pct": 0.78, "status": _tier(0.78, 0.85, 0.6),
         "gap": "External + notebook-driven writes leave lineage gaps",
         "action": "Adopt Bring-Your-Own-Lineage for hybrid/external jobs",
         "ties_to": "tables"},
        {"metric": "Cluster-policy coverage", "category": "Compute controls", "weight": 2,
         "value_pct": 0.62, "status": _tier(0.62, 0.8, 0.5),
         "gap": "Interactive clusters can launch without a cost-control policy",
         "action": "Attach mandatory cluster policies (max DBU, auto-terminate, node caps)",
         "ties_to": "workspaces"},
        {"metric": "Over-privileged principals", "category": "Access & identity", "weight": 3,
         "value_count": over_priv, "status": _tier(over_priv, 0, 3, invert=True),
         "gap": f"{over_priv} principals hold MODIFY / ALL PRIVILEGES grants (broad write access)",
         "action": "Downgrade to SELECT; scope writes via dedicated groups",
         "ties_to": "access"},
        {"metric": "PAT / token risk", "category": "Access & identity", "weight": 2,
         "value_count": stale_tokens, "status": _tier(stale_tokens, 0, 2, invert=True),
         "gap": f"{stale_tokens} long-lived personal access tokens older than 90 days",
         "action": "Rotate / revoke; adopt scoped short-lived tokens (OAuth)",
         "ties_to": "access"},
    ]

    # attach the numeric score contribution per tile
    for tile in tiles:
        tile["score_points"] = _STATUS_SCORE[tile["status"]]
    return tiles


def _governance_report() -> dict[str, Any]:
    """Full governance posture payload: overall score + graded tiles +
    per-workspace tagging breakdown + summary counts.
    """
    tiles = _governance_tiles()
    total_w = sum(x["weight"] for x in tiles)
    weighted = sum(x["weight"] * x["score_points"] for x in tiles)
    score = round((weighted / total_w) * 100) if total_w else 0
    if score >= 80:
        grade = "A"
    elif score >= 70:
        grade = "B"
    elif score >= 55:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "E"
    counts = {
        "Good": sum(1 for x in tiles if x["status"] == "Good"),
        "Warning": sum(1 for x in tiles if x["status"] == "Warning"),
        "Critical": sum(1 for x in tiles if x["status"] == "Critical"),
    }
    return {
        "score": score,
        "grade": grade,
        "counts": counts,
        "num_tiles": len(tiles),
        "tiles": tiles,
        "tagging_by_workspace": _tagging_by_workspace(),
    }


# ---------------------------------------------------------------------------
# Overview aggregation
# ---------------------------------------------------------------------------

def _overview(workspaces: list[dict[str, Any]]) -> dict[str, Any]:
    total_spend = sum(w["spend_usd_month"] for w in workspaces)
    total_savings = sum(w["savings_usd_month"] for w in workspaces)
    total_dbus = sum(w["dbus_month"] for w in workspaces)
    critical = sum(1 for w in workspaces if w["health"] == "Critical")
    # Untagged-spend % is DERIVED from the same per-workspace tagging source the
    # governance Tagging-coverage tile uses (1 − spend-weighted tagging), so the
    # exec summary and that tile can never drift. Was a frozen 0.44 literal.
    _tag_rows = _tagging_by_workspace()
    _tag_spend = sum(r["spend_usd_month"] * r["tagging_pct"] for r in _tag_rows)
    _tag_total = sum(r["spend_usd_month"] for r in _tag_rows)
    untagged_spend_pct = round(1 - (_tag_spend / _tag_total), 3) if _tag_total else 0.0

    # spend + savings trend over 12 weeks (aggregate)
    trend = []
    start = _now() - timedelta(weeks=11)
    for wk in range(12):
        spend_wk = sum(w["trend"][wk] for w in workspaces)
        # savings captured grows over time (adoption curve)
        captured = total_savings * (0.15 + 0.06 * wk) / 4.33
        trend.append({
            "week": (start + timedelta(weeks=wk)).date().isoformat(),
            "spend_usd": round(spend_wk),
            "savings_usd": round(captured),
        })

    # top opportunities cross-page
    q = _query_records()[:3]
    t = _table_records()[:3]
    r = [x for x in _risk_records() if x["severity"] == "critical"][:3]
    top = []
    for x in q:
        top.append({"type": "query", "insight": x["insight_type"], "target": x["warehouse"],
                    "est_savings_usd_month": x["est_savings_usd_month"],
                    "detail": x["insight_rationale"]})
    for x in t:
        top.append({"type": "table", "insight": x["recommendation"], "target": x["fqn"],
                    "est_savings_usd_month": x["est_savings_usd_month"], "detail": x["rationale"]})
    for x in r:
        top.append({"type": "access", "insight": x["risk"], "target": x["principal"],
                    "est_savings_usd_month": 0, "detail": x["detail"]})
    top.sort(key=lambda o: -o["est_savings_usd_month"])

    # --- Exec-summary extras (landing view) — derived from the seeded data so
    #     demo mode shows the same enriched account summary as live. Pure
    #     aggregation; no AI/Genie.
    # MoM spend: last complete week-pair as a proxy delta (demo has weekly trend).
    cur_spend = trend[-1]["spend_usd"] if trend else total_spend
    prev_spend = trend[-2]["spend_usd"] if len(trend) >= 2 else cur_spend
    spend_mom_delta = cur_spend - prev_spend
    # MAU / queries with a small illustrative prior-period delta.
    try:
        from data.adoption import get_adoption as _get_adoption
        adopt = _get_adoption()
        _asu = adopt["summary"]
        mau = int(_asu.get("mau", 0))
        queries_30d = int(_asu.get("total_queries_month", 0))
        query_hours_30d = round(float(_asu.get("query_hours_month", 0)), 0)
        wau = int(_asu.get("wau", 0))
        avg_breadth = round(float(_asu.get("avg_breadth", 0)), 1)
        num_products = int(_asu.get("num_products", 0))
        genie_adopters = int(_asu.get("genie_adopters", 0))
        ai_adopters = int(_asu.get("ai_adopters", 0))
    except Exception:
        mau = queries_30d = query_hours_30d = wau = num_products = genie_adopters = ai_adopters = 0
        avg_breadth = 0.0
    # Spend-weighted automation / serverless mix + untagged share from the ws set.
    _tw = total_spend or 1
    automated_pct = round(sum(w.get("automated_pct", 0) * w["spend_usd_month"] for w in workspaces) / _tw, 3)
    serverless_share = round(sum(w.get("serverless_share", 0) * w["spend_usd_month"] for w in workspaces) / _tw, 3)
    # Rolled savings = the estate savings estimate; needs-attention = top cost
    # opportunities + the biggest MoM driver spike, each deep-linkable.
    _type_tab = {"query": "queries", "table": "tables", "access": "access"}
    attention = [
        {"kind": "optimization", "title": f"{o['insight']} · {o['target']}",
         "detail": o.get("detail", ""), "est_savings_usd_month": o["est_savings_usd_month"],
         "tab": _type_tab.get(o["type"], "recommendations")}
        for o in top[:3] if o["est_savings_usd_month"] > 0
    ]
    cd = _cost_driver_estate()
    # Cost-driver MoM entries carry mom_pct (fraction, or None for a NEW driver) +
    # is_new + delta_usd (see _cost_driver_estate). Rank by $ delta so a new driver
    # (mom_pct=None) can surface; guard the None before formatting a percentage.
    mom_list = sorted(cd.get("mom", []), key=lambda d: -(d.get("delta_usd") or 0))
    if mom_list:
        d0 = mom_list[0]
        pct0 = d0.get("mom_pct")
        is_new0 = bool(d0.get("is_new"))
        if (is_new0 and (d0.get("delta_usd") or 0) >= 500) or (pct0 is not None and pct0 >= 0.15):
            label0 = d0.get("label") or d0.get("driver")
            title0 = (f"{label0} is a new cost driver this month" if is_new0
                      else f"{label0} spend up {round(pct0 * 100)}% MoM")
            attention.insert(0, {
                "kind": "spend-spike",
                "title": title0,
                "detail": "Fastest-growing cost driver this month",
                "est_savings_usd_month": 0, "tab": "overview"})
    return {
        "total_spend_usd_month": total_spend,
        "total_savings_usd_month": total_savings,
        "savings_pct": round(total_savings / total_spend, 3),
        "savings_estimated": False,
        "total_dbus_month": total_dbus,
        "num_workspaces": len(workspaces),
        "num_critical": critical,
        "trend": trend,
        "top_opportunities": top[:8],
        # SPEC §9: estate-level cost-by-driver + top-5 driver trend + MoM spikes
        "cost_drivers": cd,
        # Exec-summary extras
        "spend_mtd_usd": cur_spend,
        "spend_prev_mtd_usd": prev_spend,
        "spend_mom_delta_usd": spend_mom_delta,
        "spend_mom_pct": round(spend_mom_delta / prev_spend, 3) if prev_spend else 0.0,
        "mau": mau,
        "mau_delta": max(1, round(mau * 0.08)),
        "wau": wau,
        "queries_30d": queries_30d,
        "queries_30d_delta_pct": 0.12,
        "query_hours_30d": query_hours_30d,
        "avg_breadth": avg_breadth,
        "num_products": num_products,
        "genie_adopters": genie_adopters,
        "ai_adopters": ai_adopters,
        "automated_pct": automated_pct,
        "serverless_share": serverless_share,
        "untagged_spend_pct": untagged_spend_pct,  # derived from _tagging_by_workspace (can't drift from the governance tile)
        "addressable_savings_usd_month": total_savings,
        "num_p1": len(attention),
        "needs_attention": attention[:4],
    }


# ---------------------------------------------------------------------------
# Filtering helpers (applied by the API layer)
# ---------------------------------------------------------------------------

def _filter_workspace(records: list[dict[str, Any]], workspace: str | None) -> list[dict[str, Any]]:
    if not workspace or workspace in ("all", ""):
        return records
    return [r for r in records if r.get("workspace") == workspace]


# ---------------------------------------------------------------------------
# Public API — consumed by app.py
# ---------------------------------------------------------------------------

def get_workspaces() -> list[dict[str, Any]]:
    return _workspace_records()


def get_workspace_detail(workspace_id: str) -> dict[str, Any] | None:
    for w in _workspace_records():
        if w["workspace_id"] == workspace_id or w["workspace"] == workspace_id:
            return _workspace_detail(w)
    return None


def get_queries() -> list[dict[str, Any]]:
    return _query_records()


def get_tables() -> list[dict[str, Any]]:
    return _table_records()


def get_tables_summary(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """KPI summary for the Tables page. Pass a filtered row set to make KPIs
    reflect the active filters; defaults to the full table set."""
    return _tables_summary(rows if rows is not None else _table_records())


def get_table_detail(fqn: str) -> dict[str, Any] | None:
    for t in _table_records():
        if t["fqn"] == fqn:
            return t
    return None


def get_principals() -> list[dict[str, Any]]:
    return _principal_records()


def get_grants() -> list[dict[str, Any]]:
    return _grant_records()


def get_risks() -> list[dict[str, Any]]:
    return _risk_records()


# --- Access: richer bidirectional accessors --------------------------------

def get_catalogs() -> list[str]:
    return list(_CATALOGS)


def get_access_tree() -> dict[str, Any]:
    """Cascading workspace → catalog → schema → table structure for the
    data-centric filter bar. Also exposes securable types for the picker."""
    secs = _uc_securables()
    catalogs: dict[str, dict[str, list[str]]] = {}
    for s in secs:
        if s["type"] == "schema":
            catalogs.setdefault(s["catalog"], {})
        if s["type"] == "table":
            cat, sch = s["fqn"].split(".")[0], s["fqn"].split(".")[1]
            catalogs.setdefault(cat, {}).setdefault(sch, []).append(s["fqn"])
    # ensure every catalog present even if it had only non-table securables
    for c in _CATALOGS:
        catalogs.setdefault(c, {})
    workspaces = [w["workspace"] for w in _workspace_records()]
    return {"workspaces": workspaces, "catalogs": catalogs}


def get_group_tree() -> list[dict[str, Any]]:
    """Group membership tree (direct edges; sub-groups expandable client-side)."""
    out: list[dict[str, Any]] = []
    groups = {g[0]: g for g in _GROUPS}
    all_groups = _all_groups()
    for name, source, members in _GROUPS:
        children = _GROUP_MEMBERS.get(name, [])
        out.append({
            "group": name,
            "source": source,
            "member_count": members,
            "direct_members": [
                {"principal": m, "type": "group" if m in all_groups else ("service_principal" if m.startswith("svc-") else "user")}
                for m in children
            ],
            "entitlements": _GROUP_ENTITLEMENTS.get(name, []),
            "workspaces": [{"workspace": w, "permission": p} for (w, p) in _GROUP_WORKSPACES.get(name, [])],
        })
    _ = groups  # silence linters
    return out


def get_principal_access(principal: str) -> dict[str, Any] | None:
    """Everything a principal can access — entitlements, workspace
    assignments, and object grants — resolved through (nested) groups."""
    pmap = {p["principal"]: p for p in _principal_records()}
    if principal not in pmap:
        return None
    p = pmap[principal]
    grants = _effective_grants(principal)
    return {
        "principal": principal,
        "type": p["type"],
        "source": p["source"],
        "status": p["status"],
        "member_of": sorted(_resolve_effective_groups(principal)),
        "entitlements": _effective_entitlements(principal),
        "workspaces": _effective_workspaces(principal),
        "grants": grants,
        "num_grants": len(grants),
        "pats": [t for t in _pat_records() if t["principal"] == principal],
    }


def get_securable_access(securable: str) -> dict[str, Any] | None:
    """Everyone who can access a securable + policies in effect."""
    secs = {s["fqn"]: s for s in _uc_securables()}
    sec = secs.get(securable)
    grants = _securable_grants(securable)
    if sec is None and not grants:
        return None
    return {
        "securable": securable,
        "securable_type": sec["type"] if sec else "table",
        "catalog": (sec["catalog"] if sec else securable.split(".")[0]),
        "grants": grants,
        "policies": _securable_policies(securable),
        "num_grants": len(grants),
    }


def get_pats() -> list[dict[str, Any]]:
    return _pat_records()


def get_governance() -> list[dict[str, Any]]:
    return _governance_tiles()


def get_governance_report() -> dict[str, Any]:
    return _governance_report()


def get_overview() -> dict[str, Any]:
    return _overview(_workspace_records())


# --- Cost drivers / SKU breakdown (SPEC §9) --------------------------------

def get_cost_drivers(workspace: str | None = None) -> dict[str, Any]:
    """Estate-level (default) or per-workspace cost-by-driver + SKU + trend +
    MoM + DBU-by-SKU payload. Returns the estate view for ``all``/None."""
    if not workspace or workspace in ("all", ""):
        return _cost_driver_estate()
    ws = _cost_driver_workspace(workspace)
    return ws if ws is not None else _cost_driver_estate()


if __name__ == "__main__":
    import json
    ov = get_overview()
    print("=== Estate Command Center — demo data self-check ===")
    print(f"Workspaces      : {len(get_workspaces())}")
    print(f"Queries         : {len(get_queries())}")
    print(f"Tables          : {len(get_tables())}")
    print(f"Principals      : {len(get_principals())}")
    print(f"Grants          : {len(get_grants())}")
    print(f"Risks           : {len(get_risks())}")
    print(f"Governance tiles: {len(get_governance())}")
    print(f"Total spend/mo  : ${ov['total_spend_usd_month']:,}")
    print(f"Savings/mo      : ${ov['total_savings_usd_month']:,} ({ov['savings_pct']*100:.1f}%)")
    print(f"Critical ws     : {ov['num_critical']}")
    print(json.dumps(ov["top_opportunities"][0], indent=2))
