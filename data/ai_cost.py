"""AI spend attribution — all the AI/GenAI billing products a team investigates.

Aggregates the AI-family ``billing_origin_product`` lines from
``system.billing.usage`` into the views a platform/ML team actually wants:
spend by product, by serving endpoint / model, by user (owner), by workspace, a
6-month trend, and cost-optimisation flags (idle or always-on GPU endpoints,
pay-per-token vs provisioned throughput). Genie is tracked on its own
[Genie $] page; this page covers the rest of the AI estate.

Demo (seeded) module — live mode would run the equivalent SQL over
``billing_origin_product IN (<AI products>)`` grouped by
``usage_metadata.endpoint_name`` + ``identity_metadata``. List-price $; the
Account Console is the source of truth.
"""
from __future__ import annotations

import hashlib
import random
from functools import lru_cache
from typing import Any

# The AI-family billing_origin_product values (verified present in real
# system.billing.usage) and their friendly labels. Genie is deliberately NOT
# here — it has its own page.
AI_PRODUCTS: list[tuple[str, str]] = [
    ("MODEL_SERVING", "Model Serving"),
    ("AI_GATEWAY", "Foundation Model API"),
    ("VECTOR_SEARCH", "Vector Search"),
    ("AGENT_BRICKS", "Agent Bricks"),
    ("AI_FUNCTIONS", "AI Functions"),
    ("FOUNDATION_MODEL_TRAINING", "Fine-tuning / training"),
]
PRODUCT_LABEL = {c: l for c, l in AI_PRODUCTS}

CAVEATS = [
    "Covers the AI-family billing_origin_product lines (Model Serving, Foundation Model API, Vector Search, Agent Bricks, AI Functions, fine-tuning). Genie is tracked separately on the Genie $ page.",
    "Endpoint + owner come from usage_metadata.endpoint_name and identity_metadata; where an endpoint is shared the owner is the creator, not every caller.",
    "The Databricks Assistant is not metered and does not appear here. Genie (Spaces and Genie Code) is billed as its own product — see the Genie $ tab, not this one.",
    "'Who is calling' comes from system.serving.endpoint_usage (every request's caller, including shared pay-per-token endpoints). Its $ is an ESTIMATE: each endpoint's billed cost split across its callers by token share.",
    "$ is list-price reference (usage_quantity x pricing.effective_list.default); the Account Console is the source of truth. Provisioned-throughput endpoints bill whether or not they serve traffic.",
]

# Seeded serving endpoints / AI workloads: (name, product, workspace, owner,
# monthly_dbus, mode, gpu, scale_to_zero). $ is derived from a per-product $/DBU.
_DBU_RATE = {
    "MODEL_SERVING": 0.082, "AI_GATEWAY": 0.070, "VECTOR_SEARCH": 0.35,
    "AGENT_BRICKS": 0.12, "AI_FUNCTIONS": 0.070, "FOUNDATION_MODEL_TRAINING": 3.0,
}

_ENDPOINTS: list[dict[str, Any]] = [
    # name, product, workspace, owner, dbus, mode, gpu, scale_to_zero
    {"name": "fraud-scoring-rt", "product": "MODEL_SERVING", "workspace": "acme-energy-markets", "owner": "wei.zhang@acme.com", "dbus": 5200, "mode": "Provisioned", "gpu": True, "scale_to_zero": False},
    {"name": "demand-forecast", "product": "MODEL_SERVING", "workspace": "acme-asset-analytics", "owner": "daniel.foster@acme.com", "dbus": 2100, "mode": "Provisioned", "gpu": False, "scale_to_zero": False},
    {"name": "doc-summariser-llm", "product": "AI_GATEWAY", "workspace": "acme-shared-bi", "owner": "sarah.mckenzie@acme.com", "dbus": 3400, "mode": "Pay-per-token", "gpu": False, "scale_to_zero": True},
    {"name": "support-copilot", "product": "AI_GATEWAY", "workspace": "acme-datascience-sandbox", "owner": "raj.patel@acme.com", "dbus": 2600, "mode": "Pay-per-token", "gpu": False, "scale_to_zero": True},
    {"name": "trade-docs-index", "product": "VECTOR_SEARCH", "workspace": "acme-energy-markets", "owner": "priya.nathan@acme.com", "dbus": 1400, "mode": "Standard", "gpu": False, "scale_to_zero": False},
    {"name": "kb-semantic-index", "product": "VECTOR_SEARCH", "workspace": "acme-shared-bi", "owner": "raj.patel@acme.com", "dbus": 900, "mode": "Standard", "gpu": False, "scale_to_zero": False},
    {"name": "ops-triage-agent", "product": "AGENT_BRICKS", "workspace": "acme-datascience-sandbox", "owner": "wei.zhang@acme.com", "dbus": 1250, "mode": "Standard", "gpu": False, "scale_to_zero": True},
    {"name": "meter-anomaly-agent", "product": "AGENT_BRICKS", "workspace": "acme-metering-nem12", "owner": "wei.zhang@acme.com", "dbus": 640, "mode": "Standard", "gpu": False, "scale_to_zero": True},
    {"name": "ai_classify() batch", "product": "AI_FUNCTIONS", "workspace": "acme-shared-bi", "owner": "hannah.wu@acme.com", "dbus": 520, "mode": "Serverless", "gpu": False, "scale_to_zero": True},
    {"name": "ai_extract() pipeline", "product": "AI_FUNCTIONS", "workspace": "acme-gas-transmission", "owner": "james.oleary@acme.com", "dbus": 380, "mode": "Serverless", "gpu": False, "scale_to_zero": True},
    {"name": "forecast-ft-run", "product": "FOUNDATION_MODEL_TRAINING", "workspace": "acme-datascience-sandbox", "owner": "daniel.foster@acme.com", "dbus": 210, "mode": "Job", "gpu": True, "scale_to_zero": False},
    {"name": "legacy-sentiment-ep", "product": "MODEL_SERVING", "workspace": "acme-finance-fpna", "owner": "sarah.mckenzie@acme.com", "dbus": 780, "mode": "Provisioned", "gpu": True, "scale_to_zero": False},
]

_MONTH = "2026-07"   # demo anchor month (seeded data is dated to this)
_TREND_MONTHS = 6


def _current_month() -> str:
    """Current YYYY-MM (live mode) — avoids a hardcoded month drifting stale."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _rng() -> random.Random:
    return random.Random(4242)


@lru_cache(maxsize=1)
def _rows() -> list[dict[str, Any]]:
    rng = _rng()
    out: list[dict[str, Any]] = []
    for e in _ENDPOINTS:
        dbus = round(e["dbus"] * (1 + rng.uniform(-0.05, 0.05)), 1)
        rate = _DBU_RATE.get(e["product"], 0.1)
        out.append({
            **e,
            "product_label": PRODUCT_LABEL.get(e["product"], e["product"]),
            "dbus_month": dbus,
            "list_usd_month": round(dbus * rate, 2),
        })
    out.sort(key=lambda r: -r["list_usd_month"])
    return out


def _flag_for(e: dict[str, Any]) -> dict[str, Any] | None:
    """Cost-optimisation flags a team would want surfaced. Uses ``is False`` so
    an unknown (None) scale-to-zero — as in live mode where billing doesn't
    expose it — never produces a false flag."""
    if e["gpu"] and e["scale_to_zero"] is False and e["product"] == "MODEL_SERVING":
        # always-on GPU endpoint — biggest lever
        return {"endpoint": e["name"], "workspace": e["workspace"],
                "issue": "Always-on GPU endpoint — enable scale-to-zero or right-size the GPU",
                "saving_usd_month": round(e["list_usd_month"] * 0.45, 2)}
    if e["mode"] == "Provisioned" and e["scale_to_zero"] is False and e["list_usd_month"] < 100:
        return {"endpoint": e["name"], "workspace": e["workspace"],
                "issue": "Low-traffic provisioned endpoint billing 24x7 — switch to pay-per-token or scale-to-zero",
                "saving_usd_month": round(e["list_usd_month"] * 0.6, 2)}
    return None


def _trend(total_usd: float, seed_key: str) -> dict[str, Any]:
    """Simple deterministic 6-month AI-spend trend (rising into the last month)."""
    # Stable digest (builtin hash() on str is PYTHONHASHSEED-salted → the demo
    # trend would change shape on every process restart).
    rng = random.Random(int.from_bytes(hashlib.md5(seed_key.encode()).digest()[:6], "big"))
    months: list[str] = []
    base = total_usd
    pts: list[float] = [round(base)]
    cur = base
    for _ in range(_TREND_MONTHS - 1):
        cur = cur / (1 + rng.uniform(0.02, 0.14))  # AI spend generally rising
        pts.append(round(max(0.0, cur)))
    pts.reverse()
    y, m = 2026, 2
    for _ in range(_TREND_MONTHS):
        months.append(f"{y}-{m:02d}")
        m += 1
    return {"months": months, "points": pts}


def assemble(rows_all: list[dict[str, Any]], workspace: str | None = None,
             live: bool = False) -> dict[str, Any]:
    """Build the AI-spend payload from endpoint rows. Shared by demo (seeded
    rows) and live (rows from system.billing.usage), so both reconcile.

    live=True: the 6-month trend is a SEEDED illustration (fine for the demo) that
    must NOT be shown against real billing — so we return an empty trend and the
    current month, and the UI hides the chart rather than implying fake history.

    Each row: {name, product, product_label, workspace, owner, dbus_month,
    list_usd_month, mode, gpu, scale_to_zero}."""
    scoped = None if not workspace or workspace in ("all", "") else workspace
    rows = [r for r in rows_all if not scoped or r["workspace"] == scoped]

    total_usd = round(sum(r["list_usd_month"] for r in rows), 2)
    total_dbus = round(sum(r["dbus_month"] for r in rows), 1)

    # A "row" is an (endpoint name × product × workspace × owner) tuple — the live
    # SQL groups by all four, so ONE endpoint used in N workspaces / by M owners
    # is N×M rows. "Endpoints" counts must therefore be DISTINCT endpoint NAMES,
    # not row counts (counting rows reported 23k "endpoints" for 6.5k real ones).
    # by product — distinct endpoint names per product
    prod: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = prod.setdefault(r["product"], {"code": r["product"], "label": r["product_label"], "list_usd": 0.0, "dbus": 0.0, "names": set()})
        p["list_usd"] += r["list_usd_month"]
        p["dbus"] += r["dbus_month"]
        p["names"].add(r["name"])
    by_product = sorted(
        [{"code": p["code"], "label": p["label"], "list_usd": round(p["list_usd"], 2),
          "dbus": round(p["dbus"], 1), "endpoints": len(p["names"]),
          "pct": round(p["list_usd"] / total_usd, 4) if total_usd else 0.0} for p in prod.values()],
        key=lambda x: -x["list_usd"],
    )

    # by workspace — distinct endpoint names per workspace
    ws: dict[str, dict[str, Any]] = {}
    for r in rows:
        w = ws.setdefault(r["workspace"], {"workspace": r["workspace"], "list_usd": 0.0, "names": set()})
        w["list_usd"] += r["list_usd_month"]
        w["names"].add(r["name"])
    by_workspace = sorted(
        [{"workspace": w["workspace"], "list_usd": round(w["list_usd"], 2), "endpoints": len(w["names"])} for w in ws.values()],
        key=lambda x: -x["list_usd"],
    )

    # by user (owner) — distinct endpoint names per owner
    usr: dict[str, dict[str, Any]] = {}
    for r in rows:
        u = usr.setdefault(r["owner"], {"user": r["owner"], "list_usd": 0.0, "names": set(), "prod": {}})
        u["list_usd"] += r["list_usd_month"]
        u["names"].add(r["name"])
        u["prod"][r["product_label"]] = u["prod"].get(r["product_label"], 0.0) + r["list_usd_month"]
    by_user = sorted(
        [{"user": u["user"], "list_usd": round(u["list_usd"], 2), "endpoints": len(u["names"]),
          "top_product": max(u["prod"], key=u["prod"].get) if u["prod"] else None} for u in usr.values()],
        key=lambda x: -x["list_usd"],
    )

    endpoints = [{
        "name": r["name"], "product": r["product"], "product_label": r["product_label"],
        "workspace": r["workspace"], "owner": r["owner"], "list_usd_month": r["list_usd_month"],
        "dbus_month": r["dbus_month"], "mode": r["mode"], "gpu": r["gpu"], "scale_to_zero": r["scale_to_zero"],
    } for r in rows]

    flags = [f for f in (_flag_for(r) for r in rows) if f]
    flags.sort(key=lambda f: -f["saving_usd_month"])

    gpu_usd = round(sum(r["list_usd_month"] for r in rows if r["gpu"]), 2)

    return {
        "month": _current_month() if live else _MONTH,
        "workspace": scoped or "all",
        "by_product": by_product,
        "by_workspace": by_workspace,
        "by_user": by_user,
        "endpoints": endpoints,
        "flags": flags,
        # No fabricated history against real billing — empty trend in live; the
        # UI shows the chart only when points exist.
        "trend": {"months": [], "points": []} if live else _trend(total_usd, scoped or "all"),
        "summary": {
            "total_list_usd_month": total_usd,
            "total_dbus_month": total_dbus,
            # DISTINCT endpoint names, not row count (rows are endpoint×ws×owner).
            "num_endpoints": len({r["name"] for r in rows}),
            "num_products": len(by_product),
            "distinct_users": len({r["owner"] for r in rows}),
            "num_workspaces": len({r["workspace"] for r in rows}),
            "top_product": by_product[0]["label"] if by_product else None,
            "gpu_usd_month": gpu_usd,
            "flagged_savings_usd_month": round(sum(f["saving_usd_month"] for f in flags), 2),
        },
        "caveats": CAVEATS,
    }


# Sample callers for demo mode: who actually sends requests to the endpoints
# (live reads system.serving.endpoint_usage). Shared endpoints have several
# callers besides their owner — exactly the people billing alone never shows.
_DEMO_CALLERS = [
    ("raj.patel@acme.com", ["support-copilot", "doc-summariser-llm"], 0.22),
    ("sarah.mckenzie@acme.com", ["doc-summariser-llm"], 0.15),
    ("hannah.wu@acme.com", ["ai_classify() batch", "doc-summariser-llm"], 0.12),
    ("tom.nguyen@acme.com", ["support-copilot"], 0.11),
    ("wei.zhang@acme.com", ["ops-triage-agent", "fraud-scoring-rt"], 0.10),
    ("priya.nathan@acme.com", ["trade-docs-index", "support-copilot"], 0.09),
    ("a7c1e0d2-5f3b-4e8a-9c21-0b6d4f8e2a11", ["meter-anomaly-agent"], 0.08),
    ("james.oleary@acme.com", ["ai_extract() pipeline"], 0.07),
    ("daniel.foster@acme.com", ["demand-forecast"], 0.06),
]


def _demo_callers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    total = float(payload.get("summary", {}).get("total_list_usd_month") or 0)
    out = []
    for who, eps, share in _DEMO_CALLERS:
        out.append({
            "requester": who, "requests": int(share * 182_000), "tokens": int(share * 410_000_000),
            "est_usd_month": round(total * share, 2), "endpoints": eps, "num_endpoints": len(eps),
            "workspaces": [], "is_service_principal": "@" not in who,
        })
    return out


def get_ai_cost(workspace: str | None = None) -> dict[str, Any]:
    """AI-spend payload for /api/ai-cost (demo mode). Live mode calls ``assemble``
    directly with endpoint rows from system.billing.usage."""
    out = assemble(_rows(), workspace)
    out["callers"] = _demo_callers(out)
    out["callers_note"] = ""
    return out


if __name__ == "__main__":
    import json
    p = get_ai_cost()
    print("=== AI cost — demo self-check ===")
    print("total $:", p["summary"]["total_list_usd_month"], "| endpoints:", p["summary"]["num_endpoints"])
    print("by product:", json.dumps(p["by_product"], indent=2))
    print("flags:", json.dumps(p["flags"], indent=2))
