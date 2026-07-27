"""Genie cost attribution — by surface (Code / Spaces / Agents)  (SPEC §13; ALWAYS on).

Encodes the logic of the Genie queries in ``data/queries.sql`` over the seeded
demo dataset (live mode runs the SQL instead). Verified against live
``system.billing.usage`` (an internal test workspace, 2026):

  * Genie is billed under ``billing_origin_product = 'GENIE'`` (never filter by
    SKU — Genie shares the region-suffixed real-time-inference SKU).
  * ``usage_metadata.genie`` = struct {surface, channel, agent_id}, populated on
    ~99.5% of GENIE rows. The SURFACE is the real Code-vs-Spaces signal:
        GENIE_CODE   -> Genie Code (SQL editor / notebook Genie)
        GENIE_ONE    -> Genie Spaces family (Genie One / Databricks One chat)
        GENIE_AGENTS -> Genie in Agent Bricks / agent surface
        NULL         -> unattributed (~0.5%), bucketed as UNKNOWN
    There is NO 'GENIE_SPACES' literal — we group by the raw surface. The column
    is real but NOT in the published billing schema doc, so treat as "may change".
  * ``product_features.ai_bi_genie.*`` is NULL on every row — NOT used.
  * ``identity_metadata.run_as`` = user; ``workspace_id`` = origin workspace
    (both 100% populated on GENIE rows).
  * From 6 Jul 2026 consumption-based, 150 free DBUs of LLM usage per identified
    user per month (SHARED across all surfaces), then billed DBUs.
  * From 20 Jul 2026 the free tier is recorded as the ``GENIE_FREE_USAGE`` SKU
    (DBUs but no list price); billed usage is a regional Serverless Real-Time
    Inference SKU. Both carry billing_origin_product='GENIE'.
  * ``$ = usage_quantity × pricing.effective_list.default`` (list price).
  * Budgets can target Genie (Unity AI Gateway + tag databricks-product:genie)
    but CANNOT be split by surface, and cannot remove the free tier.

Deterministic (seeded) so numbers reconcile across the Genie page, the
Recommendations cost-attribution panel and the Workspaces drill-down. Consumption
pricing only starts 6 Jul 2026, so figures read LIGHT until usage flows.
"""
from __future__ import annotations

import random
from functools import lru_cache
from typing import Any

# Genie list price (USD per DBU) — illustrative list-price reference only, not a
# billed drawdown figure. The Account Console remains the source of truth.
GENIE_DBU_LIST_PRICE_USD = 0.55

# Shared free-tier allowance applied per identified user per calendar month
# BEFORE any paid split (query Q2).
FREE_DBUS_PER_USER_MONTH = 150

# The real surfaces seen in system.billing.usage (usage_metadata.genie.surface),
# with the label the UI shows. Order = display order.
SURFACES: list[tuple[str, str]] = [
    ("GENIE_CODE", "Genie Code"),
    ("GENIE_ONE", "Genie Spaces"),
    ("GENIE_AGENTS", "Genie Agents"),
]
SURFACE_LABEL = {code: label for code, label in SURFACES}

CAVEATS = [
    "Free usage IS now in the billing system tables: as of 20 Jul 2026, free Genie usage is recorded under the GENIE_FREE_USAGE SKU (billing_origin_product='GENIE'). It tracks DBUs but carries NO list price (no system.billing.list_prices row), so it shows as $0 — the app values it at the estate's effective billed Genie rate to show what it would have cost. Free usage consumed BEFORE 20 Jul is not in the tables. See docs.databricks.com 'Monitor and understand your Genie cost'.",
    "Billed Genie usage lands under a regional Serverless Real-Time Inference SKU (e.g. ENTERPRISE_SERVERLESS_REAL_TIME_INFERENCE_*); GENIE_FREE_USAGE is the free tier. Both carry billing_origin_product='GENIE' — filter on that, not on SKU name.",
    "Split is by usage_metadata.genie.surface (GENIE_CODE = Code, GENIE_ONE = Genie One / Databricks One chat, GENIE_AGENTS = agent surface). The surface enum is open, a dedicated Spaces value may appear as usage grows, so the app labels whatever values appear rather than assuming a fixed set.",
    "The 150 free DBUs / identified user / month are shared across all Genie surfaces. Live 'billable $' is usage above that free allowance; 'free absorbed' is the GENIE_FREE_USAGE portion (estate-wide). Demo mode illustrates a free-vs-paid split over synthetic totals, labelled as such.",
    "Budgets can target Genie (Unity AI Gateway + tag databricks-product:genie) but cannot be split by surface, and cannot remove the free tier. Best-practice budget-policy-at-scale guidance: see the Genie pricing FAQ (go/geniepricing or the Databricks FAQ doc).",
    "Consumption pricing from 6 Jul 2026, so figures read light until usage flows.",
    "List-price reference (USD x pricing.effective_list.default); the Account Console is the source of truth.",
]

# Per-user Genie activity seed: (user, workspace, {surface: dbus}) for the month.
# Pre-allowance TOTAL DBUs. Engineers lean GENIE_CODE (in-editor); analysts lean
# GENIE_ONE (Spaces / Databricks One); DS/agent builders add GENIE_AGENTS.
#
# The population is deliberately shaped like a REAL, maturing rollout: a small
# set of named power / moderate users who sit ABOVE the 150 free DBU/user/month
# allowance (they carry almost all the billed Genie spend), plus a broad long
# tail of light business users on Databricks One / Genie Spaces who each stay
# UNDER the allowance (their usage is absorbed by the free tier, nil billed).
# Net effect: FREE-ABSORBED > BILLED — the healthy shape you want to show when
# self-serve adoption is spreading faster than its cost.

# --- Named power / moderate users (the interesting Genie leaderboard) ---------
_NAMED_ACTIVITY: list[tuple[str, str, dict[str, float]]] = [
    # Data Science — Genie Code + Agent Bricks power users (top billed)
    ("wei.zhang@acme.com", "acme-datascience-sandbox", {"GENIE_CODE": 480, "GENIE_ONE": 70, "GENIE_AGENTS": 210}),
    ("daniel.foster@acme.com", "acme-datascience-sandbox", {"GENIE_CODE": 300, "GENIE_ONE": 40, "GENIE_AGENTS": 120}),
    # Energy Markets — a Spaces-heavy analyst who also builds agents
    ("priya.nathan@acme.com", "acme-energy-markets", {"GENIE_CODE": 220, "GENIE_ONE": 300, "GENIE_AGENTS": 60}),
    ("james.oleary@acme.com", "acme-energy-markets", {"GENIE_CODE": 90, "GENIE_ONE": 240}),
    # Asset analytics — moderate mixed users
    ("raj.patel@acme.com", "acme-asset-analytics", {"GENIE_CODE": 70, "GENIE_ONE": 180, "GENIE_AGENTS": 30}),
    ("hannah.wu@acme.com", "acme-asset-analytics", {"GENIE_CODE": 30, "GENIE_ONE": 200}),
    # Shared BI — a Spaces-dominant self-serve lead just over the allowance
    ("sarah.mckenzie@acme.com", "acme-shared-bi", {"GENIE_CODE": 20, "GENIE_ONE": 250}),
]

# --- Long tail of light business users (Databricks One / Genie Spaces) --------
# Broad self-serve reach across the business; each mostly UNDER the 150 free
# DBU/user allowance, so absorbed by the free tier. Generated deterministically.
_LIGHT_FIRST = [
    "oliver", "charlotte", "liam", "amelia", "noah", "isla", "william", "mia",
    "jack", "grace", "henry", "chloe", "thomas", "ava", "lachlan", "zoe",
    "ethan", "ruby", "cooper", "matilda", "hunter", "evie", "archie", "harper",
    "leo", "willow", "max", "scarlett", "riley", "ella", "jasper", "sienna",
    "hugo", "poppy", "angus", "freya", "elijah", "aria", "harvey", "lily",
    "marcus", "hana", "darcy", "indira", "samuel", "noor",
]
_LIGHT_LAST = [
    "thompson", "nguyen", "kelly", "brown", "singh", "walker", "reid", "chen",
    "murphy", "dawson", "hughes", "kaur", "morrison", "pierce", "foley", "tran",
    "ellis", "bianchi", "obrien", "kumar", "fraser", "romano", "doyle", "haddad",
    "lawson", "yildiz", "novak", "costa", "bauer", "petrov", "sharma", "webb",
    "carter", "day", "flynn", "gomez", "hart", "iqbal", "jones", "khan",
    "lombardi", "mills", "park", "quinn", "rossi", "shaw",
]
# Business workspaces the light users self-serve in (weighted toward BI + the
# operational teams — where Databricks One / Genie Spaces adoption spreads).
_LIGHT_WORKSPACES = [
    "acme-shared-bi", "acme-shared-bi", "acme-shared-bi",
    "acme-energy-markets", "acme-energy-markets",
    "acme-asset-analytics", "acme-asset-analytics",
    "acme-metering-nem12", "acme-metering-nem12",
    "acme-finance-fpna", "acme-finance-fpna",
    "acme-gas-transmission",
    "acme-sustainability",
    "acme-customer-billing",
]


def _light_activity() -> list[tuple[str, str, dict[str, float]]]:
    """Deterministically generate the light-business-user long tail. Each user
    is a single-workspace self-serve Genie Spaces user; the large majority stay
    under the 150 free DBU allowance (fully absorbed), with a handful of "just
    tipped over" moderate users for realism."""
    rng = random.Random(7788)
    out: list[tuple[str, str, dict[str, float]]] = []
    n = min(len(_LIGHT_FIRST), len(_LIGHT_LAST))
    for i in range(n):
        user = f"{_LIGHT_FIRST[i]}.{_LIGHT_LAST[i]}@acme.com"
        ws = _LIGHT_WORKSPACES[i % len(_LIGHT_WORKSPACES)]
        # ~1 in 8 is an analyst/engineer who writes some SQL in-editor (Code);
        # the rest are pure Spaces / Databricks One consumers.
        if i % 8 == 0:
            code = rng.randint(30, 110)
            one = rng.randint(10, 45)
        else:
            one = rng.randint(15, 130)
            code = rng.choice([0, 0, 0, 0, 10, 20, 30])
        # A small "just over the allowance" moderate cohort (~1 in 9).
        if i % 9 == 4:
            one += rng.randint(60, 140)
        surf: dict[str, float] = {"GENIE_ONE": one}
        if code:
            surf["GENIE_CODE"] = code
        out.append((user, ws, surf))
    return out


_ACTIVITY: list[tuple[str, str, dict[str, float]]] = _NAMED_ACTIVITY + _light_activity()

_MONTH = "2026-07"


def _rng() -> random.Random:
    return random.Random(1313)


@lru_cache(maxsize=1)
def _ground_truth_rows() -> list[dict[str, Any]]:
    """Query Q1 — ground-truth Genie usage by month × workspace × user × surface
    (source of truth for who / where / which surface). Demo grain collapses
    usage_date to the month."""
    rng = _rng()
    rows: list[dict[str, Any]] = []
    for user, workspace, surface_dbus in _ACTIVITY:
        for surface, _label in SURFACES:
            dbus = surface_dbus.get(surface, 0)
            if dbus <= 0:
                continue
            jittered = round(dbus * (1 + rng.uniform(-0.06, 0.06)), 1)
            rows.append({
                "usage_month": _MONTH,
                "workspace": workspace,
                "user_identity": user,
                "surface": surface,
                "label": SURFACE_LABEL[surface],
                "total_dbus": jittered,
                "total_list_cost_usd": round(jittered * GENIE_DBU_LIST_PRICE_USD, 2),
            })
    return rows


def surface_label(surface: str) -> str:
    """Friendly label for a raw surface value. The surface enum is open, so
    prettify unknowns (e.g. a future GENIE_SPACES -> 'Genie Spaces') rather than
    dropping them."""
    if surface in SURFACE_LABEL:
        return SURFACE_LABEL[surface]
    if not surface or surface.upper() == "UNKNOWN":
        return "Unknown"
    pretty = surface.replace("GENIE_", "").replace("_", " ").title().strip()
    return f"Genie {pretty}" if pretty else "Unknown"


def _rows_with_paid(gt_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Query Q2 — attach paid-after-allowance DBUs + $ to each ground-truth row.

    The 150 free DBUs are a per-identified-USER monthly allowance, shared across
    surfaces and workspaces, so we compute each user's paid FRACTION from their
    total DBUs across everything, then apply that fraction per row.
    """
    user_total: dict[str, float] = {}
    for r in gt_rows:
        user_total[r["user_identity"]] = user_total.get(r["user_identity"], 0.0) + r["total_dbus"]
    paid_frac = {
        u: (max(t - FREE_DBUS_PER_USER_MONTH, 0.0) / t if t else 0.0)
        for u, t in user_total.items()
    }
    out: list[dict[str, Any]] = []
    for r in gt_rows:
        f = paid_frac.get(r["user_identity"], 0.0)
        out.append({
            "workspace": r["workspace"],
            "workspace_id": r.get("workspace_id", ""),
            "user_identity": r["user_identity"],
            "surface": r["surface"],
            "label": r["label"],
            "total_dbus": r["total_dbus"],
            "total_list_cost_usd": r["total_list_cost_usd"],
            "paid_dbus": round(r["total_dbus"] * f, 1),
            "paid_list_cost_usd": round(r["total_list_cost_usd"] * f, 2),
        })
    return out


def _rows_billable(gt_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Live path: the PAID system.billing.usage rows passed in (the regional
    Serverless Real-Time Inference SKU, with GENIE_FREE_USAGE already excluded)
    ARE the billable usage above the 150 free DBU/user/month allowance. So paid ==
    the billed amount; we do NOT subtract the free allowance again (that would
    under-report the real charge). Free usage is handled separately via the
    GENIE_FREE_USAGE SKU and surfaced as free_absorbed_usd."""
    return [{
        "workspace": r["workspace"], "workspace_id": r.get("workspace_id", ""),
        "user_identity": r["user_identity"],
        "surface": r["surface"], "label": r["label"],
        "total_dbus": r["total_dbus"], "total_list_cost_usd": r["total_list_cost_usd"],
        "paid_dbus": r["total_dbus"], "paid_list_cost_usd": r["total_list_cost_usd"],
    } for r in gt_rows]


def _workspace_totals_from(full_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Query Q3 — per-workspace totals (distinct users, DBUs, list/paid $, and
    per-surface DBUs) from rows that already carry paid figures."""
    by_ws: dict[str, dict[str, Any]] = {}
    for r in full_rows:
        ws = by_ws.setdefault(r["workspace"], {
            "workspace": r["workspace"], "workspace_id": r.get("workspace_id", ""),
            "users": set(),
            "total_dbus": 0.0, "paid_dbus": 0.0,
            "list_usd": 0.0, "paid_usd": 0.0, "surface_dbus": {},
        })
        ws["users"].add(r["user_identity"])
        ws["total_dbus"] += r["total_dbus"]
        ws["paid_dbus"] += r["paid_dbus"]
        ws["list_usd"] += r["total_list_cost_usd"]
        ws["paid_usd"] += r["paid_list_cost_usd"]
        ws["surface_dbus"][r["label"]] = ws["surface_dbus"].get(r["label"], 0.0) + r["total_dbus"]
    out: list[dict[str, Any]] = []
    for ws in by_ws.values():
        out.append({
            "workspace": ws["workspace"],
            "workspace_id": ws.get("workspace_id", ""),
            "distinct_users": len(ws["users"]),
            "total_dbus": round(ws["total_dbus"], 1),
            "paid_dbus": round(ws["paid_dbus"], 1),
            "surface_dbus": {k: round(v, 1) for k, v in ws["surface_dbus"].items()},
            "total_list_cost_usd": round(ws["list_usd"], 2),
            "paid_list_cost_usd": round(ws["paid_usd"], 2),
        })
    out.sort(key=lambda r: -r["total_list_cost_usd"])
    return out


def assemble(gt_rows: list[dict[str, Any]], workspace: str | None = None,
             billable_input: bool = False,
             free_absorbed_usd: float | None = None,
             free_dbus_total: float | None = None,
             free_rate_is_proxy: bool = False) -> dict[str, Any]:
    """Build the full Genie-cost payload from ground-truth rows. The SINGLE
    source of truth shared by demo (seeded rows) and live (rows from
    system.billing.usage), so both modes produce identical shapes + reconcile.

    Each ground-truth row: {usage_month, workspace, user_identity, surface,
    label, total_dbus, total_list_cost_usd}.

    ``billable_input`` — set TRUE for live (system.billing.usage): the PAID rows
    passed in are the billable charge (above the free allowance), so we do not
    re-apply the free split to them. FALSE (demo) applies an illustrative
    free-vs-paid split over synthetic pre-allowance totals.

    ``free_absorbed_usd`` / ``free_dbus_total`` — live only: the REAL free usage
    from the GENIE_FREE_USAGE SKU (present in system tables from 20 Jul 2026),
    valued at the estate's effective billed Genie rate. None when unavailable
    (pre-20-Jul estate / no free usage / scoped to one workspace) → UI shows n/a.
    """
    scoped = None if not workspace or workspace in ("all", "") else workspace
    full = _rows_billable(gt_rows) if billable_input else _rows_with_paid(gt_rows)
    # Scope matches EITHER the friendly name or the raw workspace_id, so callers
    # can pass whichever they hold (the Workspaces drill-down passes the name; a
    # direct id also works). Prevents the id-vs-name mismatch that showed $0.
    def _match(r: dict[str, Any]) -> bool:
        return not scoped or r.get("workspace") == scoped or r.get("workspace_id") == scoped
    breakdown = sorted(
        [r for r in full if _match(r)],
        key=lambda r: -r["total_list_cost_usd"],
    )
    by_ws = _workspace_totals_from(full)
    if scoped:
        by_ws = [w for w in by_ws if w["workspace"] == scoped or w.get("workspace_id") == scoped]

    total_dbus = round(sum(r["total_dbus"] for r in breakdown), 1)
    total_list = round(sum(r["total_list_cost_usd"] for r in breakdown), 2)
    total_paid = round(sum(r["paid_list_cost_usd"] for r in breakdown), 2)
    users = {r["user_identity"] for r in breakdown}
    surface_totals = _surface_totals(breakdown)
    month = next((r["usage_month"] for r in gt_rows if r.get("usage_month")), _MONTH)

    return {
        "month": month,
        "workspace": scoped or "all",
        "dbu_list_price_usd": GENIE_DBU_LIST_PRICE_USD,
        "free_dbus_per_user_month": FREE_DBUS_PER_USER_MONTH,
        "breakdown": breakdown,
        "surface_totals": surface_totals,
        "by_workspace": by_ws,
        "by_user": _user_totals(breakdown),
        "summary": {
            "total_dbus": total_dbus,
            "total_list_cost_usd": total_list,
            "paid_list_cost_usd": total_paid,
            # Free absorbed: demo derives it from the illustrative split; live now
            # gets a REAL figure from the GENIE_FREE_USAGE SKU (20 Jul 2026+),
            # passed in as free_absorbed_usd — but only estate-wide (the free SKU
            # total doesn't cleanly decompose per workspace), so it's None when
            # scoped. None → UI shows "n/a" rather than a fabricated 0.
            "free_absorbed_usd": (
                (None if scoped else free_absorbed_usd) if billable_input
                else round(total_list - total_paid, 2)
            ),
            "free_dbus_total": None if (scoped or not billable_input) else free_dbus_total,
            # True when free_absorbed_usd was valued at the generic list-price proxy
            # (no billed Genie rows this month to derive an effective rate) — the UI
            # then says "list-price proxy", not "effective billed rate".
            "free_rate_is_proxy": bool(free_rate_is_proxy) if (billable_input and not scoped) else False,
            "billable_only": billable_input,
            # Free usage is now recorded in system.billing.usage (GENIE_FREE_USAGE
            # SKU) from 20 Jul 2026 — true in live when we actually found it.
            "free_usage_in_system_tables": (free_absorbed_usd is not None) if billable_input else False,
            "distinct_users": len(users),
            "num_workspaces": len({r["workspace"] for r in breakdown}),
            "num_surfaces": len(surface_totals),
        },
        "caveats": CAVEATS,
    }


def _surface_totals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll a breakdown row set up to per-surface totals (DBUs, list $, paid $)."""
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        a = agg.setdefault(r["surface"], {
            "surface": r["surface"], "label": r["label"],
            "dbus": 0.0, "list_usd": 0.0, "paid_usd": 0.0,
        })
        a["dbus"] += r["total_dbus"]
        a["list_usd"] += r["total_list_cost_usd"]
        a["paid_usd"] += r["paid_list_cost_usd"]
    total_list = sum(a["list_usd"] for a in agg.values()) or 1.0
    # Stable display order for the known surfaces, then any open-enum extras by
    # spend desc — never drop a surface just because it is not in SURFACES.
    order = {label: i for i, (_c, label) in enumerate(SURFACES)}
    ranked = sorted(
        agg.values(),
        key=lambda a: (order.get(a["label"], len(order)), -a["list_usd"]),
    )
    return [
        {
            "surface": a["surface"], "label": a["label"],
            "dbus": round(a["dbus"], 1),
            "list_usd": round(a["list_usd"], 2),
            "paid_usd": round(a["paid_usd"], 2),
            "pct": round(a["list_usd"] / total_list, 4),
        }
        for a in ranked
    ]


def _user_totals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-user rollup for the Genie page leaderboard (across workspaces)."""
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        u = agg.setdefault(r["user_identity"], {
            "user_identity": r["user_identity"],
            "total_dbus": 0.0, "list_usd": 0.0, "paid_usd": 0.0,
            "workspaces": set(), "surface_dbus": {},
        })
        u["total_dbus"] += r["total_dbus"]
        u["list_usd"] += r["total_list_cost_usd"]
        u["paid_usd"] += r["paid_list_cost_usd"]
        u["workspaces"].add(r["workspace"])
        u["surface_dbus"][r["label"]] = u["surface_dbus"].get(r["label"], 0.0) + r["total_dbus"]
    out = []
    for u in agg.values():
        top_surface = max(u["surface_dbus"], key=u["surface_dbus"].get) if u["surface_dbus"] else None
        out.append({
            "user_identity": u["user_identity"],
            "total_dbus": round(u["total_dbus"], 1),
            "total_list_cost_usd": round(u["list_usd"], 2),
            "paid_list_cost_usd": round(u["paid_usd"], 2),
            "num_workspaces": len(u["workspaces"]),
            "top_surface": top_surface,
        })
    out.sort(key=lambda r: -r["total_list_cost_usd"])
    return out


def get_genie_cost(workspace: str | None = None) -> dict[str, Any]:
    """The Genie-spend payload for ``/api/genie-cost`` (demo mode). Always on
    (SPEC §13) — calls no model, deterministic attribution over billing data.
    Live mode calls ``assemble`` directly with rows from system.billing.usage."""
    return assemble(_ground_truth_rows(), workspace)


if __name__ == "__main__":
    import json

    payload = get_genie_cost()
    print("=== Genie cost attribution — demo self-check ===")
    print(f"Breakdown rows : {len(payload['breakdown'])}")
    print(f"Workspaces     : {payload['summary']['num_workspaces']}")
    print(f"Distinct users : {payload['summary']['distinct_users']}")
    print(f"Total list $   : ${payload['summary']['total_list_cost_usd']:,.2f}")
    print(f"Paid list $    : ${payload['summary']['paid_list_cost_usd']:,.2f}")
    print("Surface totals :", json.dumps(payload["surface_totals"], indent=2))
