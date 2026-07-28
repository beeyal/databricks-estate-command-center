"""Unified recommendation hub for the Estate Command Center (SPEC §3.7).

This is the aggregation + scoring engine behind ``/api/recommendations/hub`` —
the single strategic AI-advisor surface. It fuses recommendations from every
cost/usage source into ONE scored, filterable list:

  1. query-level recs   — via ``data.recommender`` over ``demo.get_queries``
  2. tables savings      — external→managed, enable PO/LC, OPTIMIZE/VACUUM
  3. governance gaps     — Warning/Critical governance tiles → recs
  4. access risks        — over-privileged / stale-token / broad-grant → recs
  5. strategic plays     — catalog-driven behavioural/operator plays whose
                           ``trigger`` conditions match the estate metrics
                           (per-BU/team/user inefficient-query $ + counts,
                           untagged-spend %, interactive share, idle warehouses,
                           external-table $, over-privilege counts, MoM spikes).

Every rec is normalised to the SPEC §3.7 shape:

    {
      id, category, priority (P1|P2|P3), title, scope, scope_label,
      est_savings_usd_month, effort, evidence[], what_to_do[],
      features[] ({name}), owner, source
    }

Priority follows the catalog ``priority_model``: score =
normalise($/mo × severity) ÷ effort_weight × risk_multiplier, sorted by
priority tier then $. Feature pills carry just a ``name``.

Pure + deterministic so it runs identically in demo and live modes.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from data import generate_demo as demo
from data import recommender
from data import config as _config

# ---------------------------------------------------------------------------
# Data provider indirection. Defaults to the demo generator; app.py swaps in a
# rec_hub_live.LiveProvider in live mode so the hub aggregates real system-table
# data through the exact same scoring/trigger logic (see rec_hub_live.py for the
# honest-degradation notes on BU / idle / table-hygiene / access / governance).
# ---------------------------------------------------------------------------
_PROVIDER: Any = None

# Live-hub freshness. estate_metrics/_all_recs are @lru_cache'd and the provider
# memoises its own queries, so without a TTL an always-on app container would
# serve the FIRST request's cost data for the whole process lifetime. Rebuild
# when older than this. Demo is deterministic → not subject to the TTL.
_LIVE_TTL_SECONDS = 900  # 15 min
_last_build_monotonic: float | None = None
_last_build_wall_iso: str = ""
_refreshing = False  # a background stale-revalidate rebuild is in flight
import threading as _threading
_refresh_lock = _threading.Lock()  # guards the stale-check/spawn so only ONE rebuild runs


def set_provider(provider: Any) -> None:
    """Install a data provider (or None to reset to demo) and clear the memoised
    rollups so the next build reflects the new source."""
    global _PROVIDER
    _PROVIDER = provider
    estate_metrics.cache_clear()
    _all_recs.cache_clear()
    cost_attribution.cache_clear()


def _refresh_live_if_stale() -> None:
    """When a live provider is active, refresh the cached rollups + provider memo
    once they're older than the TTL, so the hub reflects current system-table data
    instead of a process-lifetime-frozen snapshot. No-op in demo mode.

    STALE-WHILE-REVALIDATE: the rebuild (which re-runs several heavy system-table
    scans, ~30-150s on a large estate) happens in a BACKGROUND thread. The calling
    request keeps serving the existing @lru_cache'd build instantly — so no user
    request ever pays the cold rebuild after the first one. On the very first build
    (nothing cached yet) we rebuild inline so there's data to serve. A guard flag
    prevents piling up concurrent rebuilds."""
    global _last_build_monotonic, _last_build_wall_iso, _refreshing
    if _PROVIDER is None:  # demo — deterministic, no staleness
        return
    import threading
    import time
    from datetime import datetime, timezone

    now = time.monotonic()
    cold = _last_build_monotonic is None
    stale = (not cold) and (now - _last_build_monotonic) >= _LIVE_TTL_SECONDS
    prev_build_monotonic = _last_build_monotonic

    def _rebuild() -> None:
        global _last_build_monotonic, _last_build_wall_iso, _refreshing
        ok = False
        try:
            estate_metrics.cache_clear()
            _all_recs.cache_clear()
            cost_attribution.cache_clear()
            if hasattr(_PROVIDER, "reset_cache"):
                _PROVIDER.reset_cache()
            # Prime the caches so the NEXT request gets the fresh build instantly.
            _all_recs()
            cost_attribution()
            _last_build_monotonic = time.monotonic()
            _last_build_wall_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            ok = True
        finally:
            _refreshing = False
            # On failure, restore the previous debounce timestamp so a broken
            # rebuild doesn't suppress the next refresh attempt for a full TTL
            # (lru_cache lazily repopulates in the meantime, so data stays valid).
            if not ok:
                _last_build_monotonic = prev_build_monotonic

    # Check-then-set under a lock so at most ONE rebuild is ever in flight (the
    # bare _refreshing flag had a check-then-set race between request threads).
    wait_for_build = False
    with _refresh_lock:
        if cold and not _refreshing:
            # Nothing cached yet — build inline (there's no stale copy to serve).
            _refreshing = True
            spawn_inline = True
            spawn_bg = False
        elif cold and _refreshing:
            # A cold build is ALREADY in flight (warmer thread or a sibling
            # request). Don't launch a second full estate scan — wait for the
            # in-flight one to populate the caches, then serve warm.
            spawn_inline = spawn_bg = False
            wait_for_build = True
        elif stale and not _refreshing:
            # Serve the existing (stale) cache to THIS request; refresh in bg.
            _refreshing = True
            _last_build_monotonic = now  # debounce: don't re-trigger until done
            spawn_inline = False
            spawn_bg = True
        else:
            spawn_inline = spawn_bg = False
    if spawn_inline:
        _rebuild()
    elif spawn_bg:
        threading.Thread(target=_rebuild, daemon=True).start()
    elif wait_for_build:
        # Block (bounded) until the concurrent cold build clears the flag so the
        # caller's subsequent _all_recs() hits a populated lru_cache instead of
        # racing its own duplicate build. Capped so a wedged build can't hang us.
        deadline = now + _LIVE_TTL_SECONDS
        while _refreshing and time.monotonic() < deadline:
            time.sleep(0.25)


def live_cached_at() -> str:
    """ISO timestamp of the last live hub (re)build, for an 'as of' label. Blank
    in demo mode."""
    return _last_build_wall_iso if _PROVIDER is not None else ""


def _prov() -> Any:
    """The active data provider — the live provider when set, else the demo
    generator (both expose get_queries / get_tables / get_risks / … )."""
    return _PROVIDER if _PROVIDER is not None else demo


_CATALOG_PATH = Path(__file__).resolve().parent.parent / "reference" / "recommendation_catalog.yaml"

# Effort → weight (catalog priority_model.inputs.effort_weight).
_EFFORT_WEIGHT = {"Low": 1.0, "Med": 2.0, "High": 3.0}

# Standard savings_basis for recs that carry no direct $ (risk-avoidance /
# attribution / governance plays scored via severity × risk, not savings).
_RISK_BASIS = "no direct $ savings — risk-avoidance / attribution play scored via severity x risk multiplier"


def _feature_ref(name: str) -> dict[str, Any]:
    """Build a feature pill ref: {name}."""
    return {"name": name}


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_catalog() -> dict[str, Any]:
    """Load the recommendation catalog (24 grounded plays)."""
    if not _CATALOG_PATH.exists():
        return {"recommendations": [], "priority_model": {}}
    text = _CATALOG_PATH.read_text()
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except Exception:
        # Without PyYAML we cannot reliably parse the nested catalog; the strategic
        # plays degrade to empty and the aggregated recs (query/table/gov/access)
        # still populate the hub.
        return {"recommendations": [], "priority_model": {}}


# ---------------------------------------------------------------------------
# Estate metrics — aggregate the demo data into the signals the catalog
# triggers reference (per-BU/team, per-user, per-workspace, per-warehouse).
# ---------------------------------------------------------------------------

# Which query insight types count as "inefficient query" volume for behavioural
# plays (poor-pruning / spill / full-scan / rewrite patterns).
_INEFFICIENT = {"cluster", "resize", "rewrite", "full-scan"}


@lru_cache(maxsize=1)
def estate_metrics() -> dict[str, Any]:
    """Roll the demo data up into the metrics the strategic-play triggers use.

    Deterministic (the demo generator is seeded). Returns per-BU, per-user,
    per-workspace and per-warehouse aggregates plus estate-wide figures.

    In live mode the installed provider computes this directly from system
    tables (see rec_hub_live.LiveProvider.estate_metrics); the demo aggregation
    below only runs when no live provider is installed.
    """
    if _PROVIDER is not None and hasattr(_PROVIDER, "estate_metrics"):
        return _PROVIDER.estate_metrics()

    workspaces = demo.get_workspaces()
    queries = demo.get_queries()
    tables = demo.get_tables()
    risks = demo.get_risks()
    tag_rows = demo._tagging_by_workspace()
    report = demo.get_governance_report()

    ws_by_name = {w["workspace"]: w for w in workspaces}
    bu_of_ws = {w["workspace"]: w["bu"] for w in workspaces}

    total_spend = sum(w["spend_usd_month"] for w in workspaces)
    total_untagged = sum(r["untagged_usd_month"] for r in tag_rows)

    # --- per-BU + per-team (BU is our proxy for team here) query aggregation --
    bu_agg: dict[str, dict[str, Any]] = {}
    user_agg: dict[str, dict[str, Any]] = {}
    for q in queries:
        bu = bu_of_ws.get(q["workspace"], q["workspace"])
        inefficient = q["insight_type"] in _INEFFICIENT
        cost = q["cost_usd"]
        b = bu_agg.setdefault(bu, {
            "bu": bu, "inefficient_query_count": 0, "inefficient_run_count": 0,
            "query_cost_usd_month": 0.0, "authors": set(), "inefficient_cost": 0.0,
            "full_scan_count": 0, "total_query_count": 0,
        })
        b["total_query_count"] += 1
        b["query_cost_usd_month"] += cost
        b["authors"].add(q["user"])
        if inefficient:
            # Count MONTHLY executions (runs), not the sampled statement row — the
            # 64 demo rows each represent a recurring pattern run ``runs`` times a
            # month, so runs is the honest inefficient-query volume the catalog
            # trigger (> 100) is written against.
            b["inefficient_query_count"] += q["runs"]
            b["inefficient_run_count"] += q["runs"]
            b["inefficient_cost"] += cost
        if "full-scan" in q.get("flags", []):
            b["full_scan_count"] += 1

        u = user_agg.setdefault(q["user"], {
            "user": q["user"], "runs": 0, "query_cost_usd_month": 0.0,
            "full_scan_count": 0, "query_count": 0,
        })
        u["runs"] += q["runs"]
        u["query_cost_usd_month"] += cost
        u["query_count"] += 1
        if "full-scan" in q.get("flags", []):
            u["full_scan_count"] += 1

    for b in bu_agg.values():
        b["distinct_authors"] = len(b["authors"])
        b["authors"] = sorted(b["authors"])
    for u in user_agg.values():
        u["full_scan_share"] = (u["full_scan_count"] / u["query_count"]) if u["query_count"] else 0.0

    # --- per-workspace compute mix (from product-mix drill-down) --------------
    ws_metrics: dict[str, dict[str, Any]] = {}
    for w in workspaces:
        detail = demo.get_workspace_detail(w["workspace_id"])
        mix = {m["product"]: m for m in detail["product_mix"]} if detail else {}

        def spend(product: str) -> float:
            return mix.get(product, {}).get("spend_usd_month", 0.0)

        all_purpose = spend("All-Purpose Compute")
        jobs_classic = spend("Jobs Classic")
        sql_classic = spend("SQL Warehouse (Classic)")
        serverless = spend("Serverless SQL") + spend("Serverless Jobs") + spend("DLT / Lakeflow")
        fmapi = spend("Foundation Model APIs") + spend("Model Serving")
        interactive_share = (all_purpose / w["spend_usd_month"]) if w["spend_usd_month"] else 0.0
        ws_metrics[w["workspace"]] = {
            "workspace": w["workspace"],
            "bu": w["bu"],
            "spend_usd_month": w["spend_usd_month"],
            "all_purpose_cost": round(all_purpose),
            "jobs_classic_cost": round(jobs_classic),
            "sql_classic_cost": round(sql_classic),
            "serverless_cost": round(serverless),
            "classic_cluster_cost": round(all_purpose + jobs_classic + sql_classic),
            "genai_cost": round(fmapi),
            "interactive_share": round(interactive_share, 3),
            "idle_pct": w["idle_pct"],
            "serverless_share": w["serverless_share"],
            "automated_pct": w["automated_pct"],
        }

    # --- idle interactive/SQL headroom, per workspace -------------------------
    # Auto-stop / right-size only recovers cost on INTERACTIVE + SQL-warehouse
    # compute — NOT jobs/DLT/serverless/AI spend (those don't have an idle-warehouse
    # lever). So the addressable base is all-purpose + SQL-classic + serverless-SQL,
    # not the whole workspace spend. Applying the idle % to total workspace spend
    # (as before) massively overstated the saving and mislabeled mixed spend as "an
    # idle SQL warehouse". Carry the addressable base explicitly.
    idle_warehouses = []
    for w in workspaces:
        if w["idle_pct"] < 0.20:
            continue
        m = ws_metrics.get(w["workspace"], {})
        addressable = m.get("all_purpose_cost", 0) + m.get("sql_classic_cost", 0) + m.get("serverless_cost", 0)
        if addressable <= 0:
            continue
        idle_warehouses.append({
            "workspace": w["workspace"], "idle_pct": w["idle_pct"],
            "addressable_usd_month": addressable, "spend_usd_month": w["spend_usd_month"],
        })

    # --- external-table $ opportunity -----------------------------------------
    external_tables = [t for t in tables if t["table_type"] == "EXTERNAL"]
    external_convert_usd = sum(t["convert_saving_usd_month"] for t in external_tables)

    # --- tagging / attribution -------------------------------------------------
    untagged_pct = (total_untagged / total_spend) if total_spend else 0.0

    # --- access risks ----------------------------------------------------------
    over_priv = [r for r in risks if r["risk"] == "over-privileged"]
    stale_tokens = [r for r in risks if r["risk"] == "stale-token"]
    broad_grants = [r for r in risks if r["risk"] == "broad-grant"]
    admin_sprawl = [r for r in risks if r["risk"] == "admin-sprawl"]

    # --- MoM cost-driver spike (per-BU) ---------------------------------------
    # Derive a synthetic MoM change from the workspace 12-month trend so the
    # spike-detection play (SPEC §9) can fire on the fastest-rising workspace.
    spikes: list[dict[str, Any]] = []
    for w in workspaces:
        detail = demo.get_workspace_detail(w["workspace_id"])
        if not detail or len(detail["monthly_trend"]) < 2:
            continue
        prev = detail["monthly_trend"][-2]["spend_usd"]
        last = detail["monthly_trend"][-1]["spend_usd"]
        if prev > 0:
            mom = (last - prev) / prev
            if mom >= 0.10:
                spikes.append({
                    "workspace": w["workspace"], "bu": w["bu"],
                    "mom_pct": round(mom, 3), "spend_usd_month": w["spend_usd_month"],
                })
    spikes.sort(key=lambda s: -s["mom_pct"])

    # --- MoM cost-DRIVER spike (SPEC §9.3) ------------------------------------
    # The reference frames spike detection at the cost-driver level. Fire
    # on any billing_origin_product driver up >= 25% MoM with material $.
    estate_drivers = demo.get_cost_drivers()
    # mom_pct is None for a NEW driver ($0 → spend); treat as a spike (infinite
    # growth) and guard the None so the >= comparison can't crash. Mirrors the
    # live path (data/rec_hub_live.py).
    driver_spikes = [
        {
            "driver": mm["driver"], "label": mm["label"], "mom_pct": mm.get("mom_pct"),
            "is_new": bool(mm.get("is_new")),
            "prev_usd_month": mm["prev_usd_month"], "spend_usd_month": mm["spend_usd_month"],
            "delta_usd": mm["delta_usd"],
        }
        for mm in estate_drivers["mom"]
        if mm["spend_usd_month"] >= 1500
        and (mm.get("is_new") or (mm.get("mom_pct") is not None and mm["mom_pct"] >= 0.25))
    ]
    driver_spikes.sort(key=lambda s: -s["delta_usd"])

    return {
        "total_spend_usd_month": total_spend,
        "total_untagged_usd_month": round(total_untagged),
        "untagged_pct": round(untagged_pct, 3),
        "driver_spikes": driver_spikes,
        "bu_agg": bu_agg,
        "user_agg": user_agg,
        "ws_metrics": ws_metrics,
        "idle_warehouses": idle_warehouses,
        "external_tables": external_tables,
        "external_convert_usd_month": round(external_convert_usd),
        "over_privileged": over_priv,
        "stale_tokens": stale_tokens,
        "broad_grants": broad_grants,
        "admin_sprawl": admin_sprawl,
        "governance": report,
        "tag_rows": tag_rows,
        "spikes": spikes,
        "bu_of_ws": bu_of_ws,
        "ws_by_name": ws_by_name,
    }


# ---------------------------------------------------------------------------
# Strategic-play generator — match catalog triggers against estate metrics
# ---------------------------------------------------------------------------

def _catalog_feature_refs(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Build feature pill refs from a catalog rec's ``features`` list."""
    return [{"name": f.get("name", "")} for f in (rec.get("features", []) or [])]


def _strategic_plays() -> list[dict[str, Any]]:
    """Evaluate every catalog trigger against the estate metrics and emit the
    plays that fire, grounded with concrete evidence chips + $.
    """
    m = estate_metrics()
    catalog = _load_catalog()
    by_id = {r["id"]: r for r in catalog.get("recommendations", [])}
    plays: list[dict[str, Any]] = []

    def emit(cat_id: str, *, scope_label: str, est_savings: float,
             evidence: list[str], savings_basis: str, extra_steps: list[str] | None = None,
             risk_multiplier: float = 1.0, severity: float = 1.0,
             frequency: float = 1.0, savings_estimatable: bool = True) -> None:
        rec = by_id.get(cat_id)
        if not rec:
            return
        feature_refs = _catalog_feature_refs(rec)
        what = list(rec.get("what_to_do", []))
        if extra_steps:
            what = extra_steps + what
        plays.append({
            "id": f"{cat_id}::{scope_label}",
            "catalog_id": cat_id,
            "category": rec.get("category", "behavioural"),
            "title": rec.get("title", cat_id),
            "scope": rec.get("scope", "global"),
            "scope_label": scope_label,
            "est_savings_usd_month": round(est_savings),
            # False ⇒ this is a genuine cost play but the $ can't be estimated
            # accurately without an A/B run (e.g. classic→serverless, where net
            # cost can rise). The UI shows "A/B required" rather than a number,
            # and it's excluded from the addressable-savings total. Distinct from
            # est_savings=0 risk/enable plays, which ARE estimatable (as $0).
            "savings_estimatable": savings_estimatable,
            "savings_basis": savings_basis,
            "effort": rec.get("effort", "Med"),
            "evidence": evidence,
            "what_to_do": what,
            "features": feature_refs,
            "owner": rec.get("suggested_owner", ""),
            "source": "strategic",
            # Strategic plays are multi-step programmes (see what_to_do), not a
            # single runnable statement, so there is no copy-paste apply_sql.
            "apply_sql": None,
            "expected_impact": rec.get("expected_impact", ""),
            "notes": rec.get("notes", ""),
            "priority_default": rec.get("priority_default", "P2"),
            "_risk_multiplier": risk_multiplier,
            "_severity": severity,
            "_frequency": frequency,
        })

    # BEH-001/002 are BU/team-framed ("a team drowning in queries", "runaway
    # interactive team") and roll up on the BU dimension. When no BU mapping is
    # configured (live + no workspace_bu_table), bu_of_ws falls back to the
    # workspace name, so these would emit with a misleading workspace-as-BU
    # label. Suppress them entirely in that case — same honesty rule the UI
    # applies by hiding BU columns/filters. Demo always has BUs.
    bu_ok = _config.bu_enabled()

    # --- BEH-001: Genie Space for a team drowning in inefficient queries ------
    # Volume is measured in monthly executions (runs) — the honest measure the
    # catalog trigger (> 100) is written against (each demo statement recurs).
    for bu, b in (m["bu_agg"].items() if bu_ok else []):
        if b["inefficient_query_count"] > 100 and b["query_cost_usd_month"] > 1000 and b["distinct_authors"] <= 10:
            deflected = b["inefficient_cost"] * 0.50  # mid-point of 40-60% deflection
            emit("BEH-001", scope_label=bu, est_savings=deflected,
                 evidence=[
                     f"{b['inefficient_run_count']:,} inefficient runs",
                     f"{b['distinct_authors']} authors",
                     f"${round(b['inefficient_cost']):,}/mo",
                 ],
                 savings_basis=f"50% of ${round(b['inefficient_cost']):,}/mo inefficient-query cost deflected to Genie (40-60% midpoint)",
                 severity=1.3, frequency=1.2)

    # --- BEH-002: cluster policy + timeout for runaway interactive team -------
    # The "runaway interactive" signal in the demo is high all-purpose $ in a
    # workspace with low automation maturity (no policy enforcing bounds).
    for ws, w in (m["ws_metrics"].items() if bu_ok else []):
        interactive_cost = w["all_purpose_cost"]
        if interactive_cost > 2000 and w["automated_pct"] < 0.6:
            saving = interactive_cost * (0.10 + 0.3 * w["idle_pct"])
            emit("BEH-002", scope_label=w["bu"], est_savings=saving,
                 evidence=[
                     f"${w['all_purpose_cost']:,}/mo all-purpose",
                     f"{round(w['automated_pct'] * 100)}% automated (no policy)",
                     f"{round(w['idle_pct'] * 100)}% idle",
                 ],
                 savings_basis=f"${w['all_purpose_cost']:,}/mo all-purpose x (10% policy floor + 30% x {round(w['idle_pct'] * 100)}% idle)",
                 severity=1.2, risk_multiplier=1.1)

    # --- BEH-003: coach power users off SELECT * / full-scan -------------------
    for user, u in sorted(m["user_agg"].items(), key=lambda kv: -kv[1]["query_cost_usd_month"]):
        if u["full_scan_share"] > 0.3 and u["query_cost_usd_month"] > 500 and u["runs"] > 50:
            saving = u["query_cost_usd_month"] * 0.15
            emit("BEH-003", scope_label=user, est_savings=saving,
                 evidence=[
                     f"{round(u['full_scan_share'] * 100)}% full-scan",
                     f"{u['runs']:,} runs",
                     f"${round(u['query_cost_usd_month']):,}/mo",
                 ],
                 savings_basis=f"15% of ${round(u['query_cost_usd_month']):,}/mo query cost from coaching off SELECT * / full-scan",
                 severity=1.0)

    # --- BEH-004: BI reporting off all-purpose onto a SQL warehouse -----------
    for ws, w in m["ws_metrics"].items():
        if w["all_purpose_cost"] > 3000:
            saving = w["all_purpose_cost"] * 0.30
            emit("BEH-004", scope_label=w["workspace"], est_savings=saving,
                 evidence=[
                     f"${w['all_purpose_cost']:,}/mo all-purpose",
                     f"{round(w['interactive_share'] * 100)}% interactive share",
                 ],
                 savings_basis=f"30% of ${w['all_purpose_cost']:,}/mo all-purpose moved onto a right-sized SQL warehouse",
                 severity=1.1)

    # --- CMP-001: evaluate classic/all-purpose jobs for serverless -------------
    # We surface the classic-jobs spend as evidence and recommend an A/B
    # evaluation, but DO NOT estimate a saving: serverless jobs DBUs list ~2x
    # classic and net spend frequently RISES on migration — a real saving can
    # only be established by running the workload on both. (Field + docs research
    # 2026-07-20; go/serverless-migration-planner is internal-only.)
    for ws, w in m["ws_metrics"].items():
        if w["jobs_classic_cost"] > 2000:
            emit("CMP-001", scope_label=w["workspace"], est_savings=0,
                 savings_estimatable=False,
                 evidence=[
                     f"${w['jobs_classic_cost']:,}/mo classic jobs",
                     f"{round(w['automated_pct'] * 100)}% automated",
                 ],
                 savings_basis="Not estimated — serverless jobs DBUs list ~2x classic and net cost can rise; "
                               "quantify with an A/B run of representative jobs before migrating at scale.",
                 severity=1.2, frequency=1.1)

    # --- CMP-002: auto-stop + right-size idle interactive/SQL compute ----------
    # Savings apply to the ADDRESSABLE base (interactive + SQL-warehouse spend),
    # not the whole workspace — auto-stop can't recover jobs/DLT/serverless/AI cost.
    for wh in m["idle_warehouses"]:
        base = wh.get("addressable_usd_month", wh.get("spend_usd_month", 0))
        saving = base * wh["idle_pct"] * 0.35
        if saving < 200:
            continue
        emit("CMP-002", scope_label=wh.get("workspace", wh.get("warehouse", "")), est_savings=saving,
             evidence=[
                 f"{round(wh['idle_pct'] * 100)}% idle (SQL query activity)",
                 f"${round(base):,}/mo interactive + SQL spend",
             ],
             savings_basis=f"${round(base):,}/mo interactive+SQL x {round(wh['idle_pct'] * 100)}% idle x 35% recoverable via auto-stop / right-size",
             severity=1.1, frequency=1.3)

    # --- CMP-004: pools + spot for heavy classic clusters ----------------------
    # Savings NOT estimated: the benefit comes from cheaper Azure spot VMs, and the
    # VM cost (+ a customer's reservation discounts) lives in Azure Billing, not
    # system.billing.usage — which only has DBU list cost. Surface the classic
    # spend + low-serverless signal, but the $ needs Azure Billing to be accurate.
    for ws, w in m["ws_metrics"].items():
        if w["classic_cluster_cost"] > 3000 and w["serverless_share"] < 0.5:
            emit("CMP-004", scope_label=w["workspace"], est_savings=0,
                 savings_estimatable=False,
                 evidence=[
                     f"${w['classic_cluster_cost']:,}/mo classic compute (DBU list)",
                     f"{round(w['serverless_share'] * 100)}% serverless",
                 ],
                 savings_basis="Not estimated — spot/pool savings depend on Azure VM rates and reservation "
                               "coverage, which live in Azure Billing (not system tables). Quantify once Azure "
                               "cost is wired in.",
                 severity=1.0)

    # --- STO-004: convert external tables to managed ---------------------------
    if m["external_convert_usd_month"] > 200 and m["external_tables"]:
        n_ext = len(m["external_tables"])
        emit("STO-004", scope_label="estate", est_savings=m["external_convert_usd_month"],
             evidence=[
                 f"{n_ext} external tables",
                 f"${m['external_convert_usd_month']:,}/mo convert opportunity",
             ],
             savings_basis=f"sum of per-table external→managed convert savings across {n_ext} external tables",
             severity=1.1)

    # --- STO-001: enable PO on managed tables lacking it -----------------------
    tables = _prov().get_tables()
    managed_no_po = [t for t in tables if t["table_type"] == "MANAGED" and not t["po_enabled"]]
    if managed_no_po:
        po_saving = sum(t["po_lc_saving_usd_month"] for t in managed_no_po)
        emit("STO-001", scope_label="estate", est_savings=po_saving,
             evidence=[
                 f"{len(managed_no_po)} managed tables without PO",
                 f"${round(po_saving):,}/mo",
             ],
             savings_basis=f"sum of PO storage (~50%) + query savings across {len(managed_no_po)} managed tables without PO",
             severity=1.2, frequency=1.1)

    # --- STO-002: liquid clustering on poorly-pruned tables --------------------
    lc_candidates = [t for t in tables if t["needs_clustering"]]
    if lc_candidates:
        lc_saving = sum(t.get("po_lc_saving_usd_month", 0) for t in lc_candidates) * 0.6
        emit("STO-002", scope_label="estate", est_savings=lc_saving,
             evidence=[
                 f"{len(lc_candidates)} tables need clustering",
                 f"${round(lc_saving):,}/mo",
             ],
             savings_basis=f"60% of PO/LC savings attributed to clustering across {len(lc_candidates)} candidate tables",
             severity=1.1)

    # --- TAG-001: close the untagged-spend gap ---------------------------------
    if m["untagged_pct"] > 0.20 and m["total_spend_usd_month"] > 5000:
        emit("TAG-001", scope_label="estate", est_savings=0,
             evidence=[
                 f"{round(m['untagged_pct'] * 100)}% untagged",
                 f"${m['total_untagged_usd_month']:,}/mo unattributed",
             ], savings_basis=_RISK_BASIS, severity=1.4, risk_multiplier=1.2)

    # --- TAG-002: serverless budget policies -----------------------------------
    serverless_total = sum(w["serverless_cost"] for w in m["ws_metrics"].values())
    if serverless_total > 2000:
        emit("TAG-002", scope_label="estate", est_savings=0,
             evidence=[
                 f"${round(serverless_total):,}/mo serverless",
                 "budget-policy coverage < 50%",
             ], savings_basis=_RISK_BASIS, severity=1.2, risk_multiplier=1.1)

    # --- TAG-003: account budgets + threshold alerts ---------------------------
    emit("TAG-003", scope_label="account", est_savings=0,
         evidence=[
             "2 of 6 account budgets configured",
             "no threshold alerts on prod",
         ], savings_basis=_RISK_BASIS, severity=1.2, risk_multiplier=1.3)

    # --- TAG-004: investigate a cost-driver that spiked MoM (SPEC §9.3) --------
    for spk in m["driver_spikes"]:
        # mom_pct is None for a NEW driver ($0 → spend) — describe it as "new this
        # month" rather than crashing on None*100 or printing a nonsense percent.
        pct = spk.get("mom_pct")
        headline = (f"{spk['label']} is a new cost driver this month"
                    if spk.get("is_new") or pct is None
                    else f"{spk['label']} +{round(pct * 100)}% MoM")
        emit("TAG-004", scope_label=spk["label"], est_savings=0,
             evidence=[
                 headline,
                 f"+${round(spk['delta_usd']):,}/mo",
                 f"${round(spk['spend_usd_month']):,}/mo now",
             ], savings_basis=_RISK_BASIS, severity=1.3, risk_multiplier=1.3)

    # --- AI-001: cap GenAI spend with Unity AI Gateway -------------------------
    genai_total = sum(w["genai_cost"] for w in m["ws_metrics"].values())
    if genai_total > 1000:
        emit("AI-001", scope_label="estate", est_savings=0,
             evidence=[
                 f"${round(genai_total):,}/mo GenAI + serving",
                 "no per-endpoint rate limits",
             ], savings_basis=_RISK_BASIS, severity=1.2, risk_multiplier=1.3)

    # --- AI-002: control FMAPI cost + guarantee AU residency -------------------
    emit("AI-002", scope_label="account", est_savings=0,
         evidence=[
             "in-app narration must pin an in-region Claude model",
             "cross-geo ON by default for AU geo",
         ], savings_basis=_RISK_BASIS, severity=1.1, risk_multiplier=1.2)

    # --- GOV-001: remediate over-privileged prod access ------------------------
    if m["over_privileged"]:
        principals = sorted({r["principal"] for r in m["over_privileged"]})
        emit("GOV-001", scope_label="estate", est_savings=0,
             evidence=[
                 f"{len(m['over_privileged'])} MODIFY/ALL grants on prod",
                 f"{len(principals)} principals",
             ], savings_basis=_RISK_BASIS, severity=1.4, risk_multiplier=1.5)

    # --- GOV-002: rotate stale PATs --------------------------------------------
    if m["stale_tokens"]:
        emit("GOV-002", scope_label="estate", est_savings=0,
             evidence=[
                 f"{len(m['stale_tokens'])} stale tokens > 90 days",
             ], savings_basis=_RISK_BASIS, severity=1.1, risk_multiplier=1.3)

    # --- GOV-004: enforce cost-governance controls -----------------------------
    gov = m["governance"]
    warn_crit = [t for t in gov["tiles"] if t["status"] in ("Warning", "Critical")]
    if warn_crit:
        emit("GOV-004", scope_label="estate", est_savings=0,
             evidence=[
                 f"{len(warn_crit)} controls Warning/Critical",
                 f"governance score {gov['score']}/100 ({gov['grade']})",
             ], savings_basis=_RISK_BASIS, severity=1.2, risk_multiplier=1.2)

    return plays


# ---------------------------------------------------------------------------
# Aggregated recs from the other surfaces (query / table / governance / access)
# ---------------------------------------------------------------------------

# Map a query insight_type → (category, the feature it depends on).
_QUERY_INSIGHT_FEATURE = {
    "cluster": ("storage", "Liquid Clustering (CLUSTER BY on Delta tables)"),
    "resize": ("compute", "Serverless SQL Compute (serverless SQL warehouses)"),
    "rewrite": ("behavioural", "Result cache / Disk cache (SQL warehouse caching)"),
    "vacuum": ("storage", "OPTIMIZE / VACUUM (Delta table maintenance)"),
    "optimize": ("storage", "OPTIMIZE / VACUUM (Delta table maintenance)"),
}

_TABLE_INSIGHT_FEATURE = {
    "convert-to-managed": ("storage", "Unity Catalog managed tables"),
    "enable-PO": ("storage", "Predictive Optimization (auto OPTIMIZE/VACUUM/ANALYZE for UC managed tables)"),
    "enable-LC": ("storage", "CLUSTER BY AUTO (automatic liquid clustering column selection)"),
    "optimize": ("storage", "OPTIMIZE / VACUUM (Delta table maintenance)"),
    "vacuum": ("storage", "OPTIMIZE / VACUUM (Delta table maintenance)"),
    "cluster": ("storage", "Liquid Clustering (CLUSTER BY on Delta tables)"),
}


def _table_apply_sql(t: dict[str, Any], insight: str) -> str | None:
    """Exact runnable statement for a table-level rec, using the row's real
    identifiers (``fqn`` / ``catalog`` / ``schema``). None when the fix is not a
    single statement."""
    fqn = t.get("fqn")
    if not fqn:
        return None
    cat, sch = t.get("catalog"), t.get("schema")
    if insight == "convert-to-managed":
        # DEEP CLONE preserves history and is the recommended path (see
        # generate_demo conversion_path); managed target lets PO/LC kick in.
        return f"CREATE TABLE {fqn}_managed DEEP CLONE {fqn};"
    if insight == "enable-PO":
        # Enable Predictive Optimization at the schema, then turn on auto-LC.
        schema_stmt = f"ALTER SCHEMA {cat}.{sch} ENABLE PREDICTIVE OPTIMIZATION;" if cat and sch else None
        alter_stmt = f"ALTER TABLE {fqn} CLUSTER BY AUTO;"
        return f"{schema_stmt}\n{alter_stmt}" if schema_stmt else alter_stmt
    if insight == "enable-LC":
        return f"ALTER TABLE {fqn} CLUSTER BY AUTO;"
    if insight == "optimize":
        return f"OPTIMIZE {fqn};"
    if insight == "vacuum":
        return f"VACUUM {fqn} RETAIN 168 HOURS;"
    if insight == "cluster":
        # No query predicates at the table level → explicit placeholder rather
        # than an invented column.
        return f"ALTER TABLE {fqn} CLUSTER BY ({recommender._CLUSTER_COL_PLACEHOLDER});"
    return None


def _table_savings_basis(t: dict[str, Any], insight: str) -> str:
    """One-sentence derivation of a table rec's ``est_savings_usd_month`` from
    the row's savings breakdown (mirrors generate_demo's table economics)."""
    scan = t.get("scan_cost_usd_month", 0.0)
    if insight == "convert-to-managed":
        return (
            f"OPTIMIZE (${t.get('optimize_saving_usd_month', 0):,.0f}) + "
            f"VACUUM (${t.get('vacuum_saving_usd_month', 0):,.0f}) + "
            f"${scan:,.0f}/mo scan x 0.35 auto-LC uplift, captured automatically by PO"
        )
    if insight in ("enable-PO", "enable-LC"):
        return (
            f"~50% storage on {t.get('size_gb', 0):,.0f} GB (x $0.023/GB/mo) + "
            f"scan uplift on ${scan:,.0f}/mo scan from CLUSTER BY AUTO"
        )
    if insight == "optimize":
        return f"${scan:,.0f}/mo scan x {t.get('small_file_ratio', 0):.2f} small-file ratio x 0.60 compaction gain"
    if insight == "vacuum":
        return f"{t.get('reclaimable_gb', 0):,.0f} GB reclaimable stale bytes x $0.023/GB/mo storage"
    return "estimated from table statistics (see assumptions block)"


def _query_recs() -> list[dict[str, Any]]:
    """Roll query-level recs up to a per-BU behavioural rec so the hub is not
    flooded with 60+ single-query rows. Groups by (BU, insight_type), keeps the
    query-level detail as evidence, dedups against the strategic plays via a
    distinct source tag.
    """
    m = estate_metrics()
    queries = _prov().get_queries()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for q in queries:
        bu = m["bu_of_ws"].get(q["workspace"], q["workspace"])
        key = (bu, q["insight_type"])
        g = grouped.setdefault(key, {
            "bu": bu, "insight_type": q["insight_type"], "count": 0,
            "savings": 0.0, "cost": 0.0, "runs": 0,
            "sample_sql": q["query_text"],
            "sample_target": q.get("target_table", ""),
        })
        g["count"] += 1
        g["savings"] += q["est_savings_usd_month"]
        g["cost"] += q["cost_usd"]
        g["runs"] += q["runs"]

    recs: list[dict[str, Any]] = []
    for (bu, insight), g in grouped.items():
        if g["savings"] < 50:
            continue
        category, feature = _QUERY_INSIGHT_FEATURE.get(insight, ("behavioural", "Serverless SQL Compute (serverless SQL warehouses)"))
        feat = _feature_ref(feature)
        feature_refs = [feat]
        title = {
            "cluster": f"Apply Liquid Clustering to {bu}'s poorly-pruned query hotspots",
            "resize": f"Resize / split the memory-bound queries in {bu}",
            "rewrite": f"Rewrite {bu}'s SELECT * / full-scan queries (project + predicate)",
            "vacuum": f"Run VACUUM on {bu}'s stale-version query targets",
            "optimize": f"OPTIMIZE {bu}'s small-file-bloated query targets",
        }.get(insight, f"Optimize {bu}'s {insight} query pattern")
        # apply_sql is the runnable statement for the group's representative
        # query (built via the same recommender helper); resize/rewrite have no
        # single statement so this is None and the guidance stays in what_to_do.
        sample_q = {"query_text": g["sample_sql"], "target_table": g["sample_target"]}
        recs.append({
            "id": f"QRY-{insight}::{bu}",
            "catalog_id": None,
            "category": category,
            "title": title,
            # bu falls back to the workspace name when no BU mapping is
            # configured; label the scope honestly as 'workspace' in that case so
            # we don't imply a BU dimension the customer hasn't defined.
            "scope": "bu" if _config.bu_enabled() else "workspace",
            "scope_label": bu,
            "est_savings_usd_month": round(g["savings"]),
            "savings_basis": f"sum of per-query recommender estimates across {g['count']} {insight} queries (runs x per-run cost x efficiency gain)",
            "effort": "Low" if insight in ("rewrite", "optimize", "vacuum") else "Med",
            "evidence": [
                f"{g['count']} queries",
                f"{g['runs']:,} runs",
                f"${round(g['cost']):,}/mo cost",
            ],
            "what_to_do": list(recommender._NEXT_STEPS.get(insight, [])),
            "features": feature_refs,
            "owner": "Data engineering lead",
            "source": "query",
            "apply_sql": recommender.apply_sql_for_query(sample_q, insight),
            "expected_impact": "Query-level estimate from the deterministic recommender (SPEC §4); scales with runs × per-run cost × efficiency gain.",
            "notes": None,
            "priority_default": None,
        })
    return recs


def _table_recs() -> list[dict[str, Any]]:
    """Per-table savings recs (external→managed, PO/LC, OPTIMIZE/VACUUM), rolled
    to the highest-value tables so the hub stays operator-readable.
    """
    tables = _prov().get_tables()
    recs: list[dict[str, Any]] = []
    for t in tables:
        if not t["needs_action"] or t["est_savings_usd_month"] < 100:
            continue
        insight = t["recommendation"]
        category, feature = _TABLE_INSIGHT_FEATURE.get(insight, ("storage", "OPTIMIZE / VACUUM (Delta table maintenance)"))
        feat = _feature_ref(feature)
        title = {
            "convert-to-managed": f"Convert {t['table']} to a UC managed table",
            "enable-PO": f"Enable Predictive Optimization on {t['table']}",
            "enable-LC": f"Apply CLUSTER BY AUTO on {t['table']}",
            "optimize": f"OPTIMIZE {t['table']} (compact small files)",
            "vacuum": f"VACUUM {t['table']} (reclaim stale versions)",
            "cluster": f"Cluster {t['table']} on filter/join columns",
        }.get(insight, f"Optimize {t['table']}")
        recs.append({
            "id": f"TBL-{insight}::{t['fqn']}",
            "catalog_id": None,
            "category": category,
            "title": title,
            "scope": "global",
            "scope_label": t["fqn"],
            "est_savings_usd_month": round(t["est_savings_usd_month"]),
            "savings_basis": _table_savings_basis(t, insight),
            "effort": "High" if insight == "convert-to-managed" else "Low",
            "evidence": [
                t["table_type"],
                f"{t['size_gb']:.0f} GB",
                f"${round(t['scan_cost_usd_month']):,}/mo scan",
            ],
            "what_to_do": t["next_steps"],
            "features": [feat],
            "owner": "Data platform lead" if insight == "convert-to-managed" else "Data engineering lead",
            "source": "table",
            "apply_sql": _table_apply_sql(t, insight),
            "expected_impact": t["rationale"],
            "notes": t.get("caveat"),
            "priority_default": None,
        })
    return recs


def _governance_recs() -> list[dict[str, Any]]:
    """Turn Warning/Critical governance tiles into hub recs (SPEC §3.7)."""
    report = _prov().get_governance_report()
    recs: list[dict[str, Any]] = []
    for tile in report["tiles"]:
        if tile["status"] not in ("Warning", "Critical"):
            continue
        recs.append({
            "id": f"GOVTILE::{tile['metric']}",
            "catalog_id": None,
            "category": "access" if tile.get("category") == "Access & identity" else "tagging" if tile.get("category") == "Cost attribution" else "governance",
            "title": f"Close governance gap: {tile['metric']}",
            "scope": "global",
            "scope_label": tile.get("category", "governance"),
            "est_savings_usd_month": 0,
            "savings_basis": _RISK_BASIS,
            "effort": "Med",
            "evidence": [tile["gap"]],
            "what_to_do": [tile["action"]],
            "features": [],
            "owner": "FinOps / platform admin",
            "source": "governance",
            # Governance remediation is a console/config action, not a single
            # statement — the concrete step is in what_to_do.
            "apply_sql": None,
            "expected_impact": f"Governance tile scored {tile['status']}; closing it improves the estate posture.",
            "notes": None,
            "priority_default": "P1" if tile["status"] == "Critical" else "P2",
            "_severity": 1.3 if tile["status"] == "Critical" else 1.0,
            "_risk_multiplier": 1.3 if tile["status"] == "Critical" else 1.1,
        })
    return recs


def _access_recs() -> list[dict[str, Any]]:
    """Turn access risk flags into least-privilege / hygiene recs."""
    risks = _prov().get_risks()
    recs: list[dict[str, Any]] = []
    # de-dup by (risk-kind) so the hub gets one operator play per risk class
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for r in risks:
        by_kind.setdefault(r["risk"], []).append(r)
    KIND_META = {
        "over-privileged": ("access", "Remediate over-privileged prod grants (least-privilege via groups)", "Security / UC admin", 1.5, 1.4),
        "direct-to-user": ("access", "Move direct-to-user grants onto scoped groups", "UC admin", 1.2, 1.1),
        "broad-grant": ("access", "Replace broad account-users / PUBLIC grants with scoped groups", "Security / UC admin", 1.4, 1.3),
        "admin-sprawl": ("access", "Reduce standing admin — adopt just-in-time elevation", "Security admin", 1.2, 1.1),
        "stale-token": ("access", "Rotate / revoke stale PATs and tokens", "Security / workspace admin", 1.3, 1.1),
        "external-write": ("access", "Confirm least-privilege on external-location writes", "UC admin", 1.2, 1.1),
    }
    for kind, group in by_kind.items():
        meta = KIND_META.get(kind)
        if not meta:
            continue
        category, title, owner, risk_mult, sev = meta
        principals = sorted({g["principal"] for g in group})
        recs.append({
            "id": f"ACCESS::{kind}",
            "catalog_id": None,
            "category": category,
            "title": title,
            "scope": "global",
            "scope_label": f"{len(principals)} principal(s)",
            "est_savings_usd_month": 0,
            "savings_basis": _RISK_BASIS,
            "effort": "Med" if kind in ("over-privileged", "broad-grant") else "Low",
            "evidence": [f"{len(group)} {kind} finding(s)", ", ".join(principals[:3]) + ("…" if len(principals) > 3 else "")],
            "what_to_do": [group[0]["recommended_action"]],
            "features": [{"name": "system.information_schema (privilege resolution)"}],
            "owner": owner,
            "source": "access",
            # REVOKE/rotation targets vary per principal+securable; the exact
            # remediation is per-finding in what_to_do, not one blanket statement.
            "apply_sql": None,
            "expected_impact": "Security / governance risk-avoidance play; no direct $ but high risk if unaddressed.",
            "notes": None,
            "priority_default": None,
            "_severity": sev,
            "_risk_multiplier": risk_mult,
        })
    return recs


# ---------------------------------------------------------------------------
# Scoring + priority (catalog priority_model)
# ---------------------------------------------------------------------------

def _score_and_prioritise(recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assign a priority_score + P1/P2/P3 tier per the catalog priority_model.

    score = normalise($/mo × severity) ÷ effort_weight × risk_multiplier.
    """
    max_savings = max((r["est_savings_usd_month"] for r in recs), default=0) or 1

    for r in recs:
        savings = r["est_savings_usd_month"]
        norm_savings = savings / max_savings
        severity = r.get("_severity", 1.0)
        risk_mult = r.get("_risk_multiplier", 1.0)
        effort_w = _EFFORT_WEIGHT.get(r["effort"], 2.0)
        # risk-avoidance recs (savings 0 but high risk) still score via severity×risk
        base = (norm_savings * severity) if savings > 0 else (0.15 * severity)
        score = (base / effort_w) * risk_mult
        r["priority_score"] = round(score, 4)

        # Tier assignment. Honour an explicit catalog priority_default when
        # present; otherwise derive from score + effort + risk.
        default = r.get("priority_default")
        if default in ("P1", "P2", "P3"):
            tier = default
        else:
            high_savings = norm_savings >= 0.5
            low_effort = r["effort"] in ("Low", "Med")
            high_risk = risk_mult >= 1.3
            if (high_savings and low_effort) or high_risk:
                tier = "P1"
            elif norm_savings >= 0.2 or r["effort"] == "Med":
                tier = "P2"
            else:
                tier = "P3"
        r["priority"] = tier
        # clean internal scratch fields
        for k in ("_severity", "_risk_multiplier", "_frequency", "priority_default"):
            r.pop(k, None)

    tier_rank = {"P1": 0, "P2": 1, "P3": 2}
    recs.sort(key=lambda r: (tier_rank[r["priority"]], -r["est_savings_usd_month"], -r["priority_score"]))
    return recs


# ---------------------------------------------------------------------------
# Cost-attribution rollup
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def cost_attribution() -> dict[str, Any]:
    """Spend by workspace / BU / tag-coverage + untagged %, top cost drivers, and
    the tagging + budget-policy plays to close attribution gaps (SPEC §3.7).

    @lru_cache'd (like estate_metrics/_all_recs, and cleared with them): it's pure
    in-memory rollup over the memoised estate metrics, but it ran on EVERY hub
    request (every filter change) and was O(workspaces × tag_rows) — ~16M ops on a
    4k-workspace estate. Caching + the dict lookup below cut it to a one-time cost.
    """
    m = estate_metrics()
    workspaces = _prov().get_workspaces()
    tag_rows = m["tag_rows"]
    # Index tag_rows by workspace once (was a nested `next(...)` scan per workspace
    # → O(n²)).
    tag_by_ws = {r["workspace"]: r for r in tag_rows}

    by_workspace = sorted(
        [
            {
                "workspace": w["workspace"],
                "bu": w["bu"],
                "spend_usd_month": w["spend_usd_month"],
                "tagging_pct": (tag_by_ws.get(w["workspace"]) or {}).get("tagging_pct"),
                "untagged_usd_month": (tag_by_ws.get(w["workspace"]) or {}).get("untagged_usd_month", 0),
            }
            for w in workspaces
        ],
        key=lambda x: -x["spend_usd_month"],
    )

    # roll up by BU
    bu_map: dict[str, dict[str, Any]] = {}
    for w in by_workspace:
        b = bu_map.setdefault(w["bu"], {"bu": w["bu"], "spend_usd_month": 0, "untagged_usd_month": 0})
        b["spend_usd_month"] += w["spend_usd_month"]
        b["untagged_usd_month"] += w["untagged_usd_month"]
    by_bu = sorted(bu_map.values(), key=lambda x: -x["spend_usd_month"])
    for b in by_bu:
        b["tagged_pct"] = round(1 - (b["untagged_usd_month"] / b["spend_usd_month"]), 3) if b["spend_usd_month"] else 0.0

    # cost drivers — the real billing_origin_product model (SPEC §9). Uses the
    # cost-driver estate rollup (drivers already carry pct_of_total).
    estate = _prov().get_cost_drivers()
    drivers = [
        {"driver": d["label"], "driver_code": d["driver"],
         "spend_usd_month": d["spend_usd_month"], "pct_of_total": d["pct_of_total"]}
        for d in estate["drivers"]
    ]

    return {
        "total_spend_usd_month": m["total_spend_usd_month"],
        "total_untagged_usd_month": m["total_untagged_usd_month"],
        "untagged_pct": m["untagged_pct"],
        "by_workspace": by_workspace,
        "by_bu": by_bu,
        "cost_drivers": drivers,
        # spikes now reflect the per-DRIVER MoM movers (SPEC §9.3), not just the
        # per-workspace month-total drift, so the spike-detection story is about
        # cost-drivers as the reference frames it.
        "spikes": m["spikes"],
        "driver_spikes": m["driver_spikes"],
    }


# ---------------------------------------------------------------------------
# Public: the unified hub payload
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _all_recs() -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    recs.extend(_strategic_plays())
    recs.extend(_query_recs())
    recs.extend(_table_recs())
    recs.extend(_governance_recs())
    recs.extend(_access_recs())
    return _score_and_prioritise(recs)


def build_hub(
    *,
    workspace: str = "all",
    priority: str = "all",
    category: str = "all",
    scope: str = "all",
) -> dict[str, Any]:
    """Assemble the unified, scored, filterable recommendation hub.

    Returns the rec list + KPI summary + category/priority counts + the
    cost-attribution rollup. Filters are applied to the rec list; the summary
    reflects the filtered list so the KPIs reconcile to what is on screen.
    """
    _refresh_live_if_stale()   # TTL: don't serve process-lifetime-frozen live data
    recs = [dict(r) for r in _all_recs()]

    # workspace filter — match on the scope_label (BU/workspace/table) or by the
    # BU that a workspace belongs to.
    if workspace and workspace != "all":
        m = estate_metrics()
        bu = m["bu_of_ws"].get(workspace)
        recs = [
            r for r in recs
            if r["scope_label"] == workspace
            or (bu and r["scope_label"] == bu)
            or r["scope"] in ("global", "account")  # estate-wide recs always apply
        ]
    if priority and priority != "all":
        recs = [r for r in recs if r["priority"] == priority]
    if category and category != "all":
        recs = [r for r in recs if r["category"] == category]
    if scope and scope != "all":
        recs = [r for r in recs if r["scope"] == scope]

    # summary KPIs over the filtered list
    total_savings = sum(r["est_savings_usd_month"] for r in recs)
    num_p1 = sum(1 for r in recs if r["priority"] == "P1")
    cat_counts: dict[str, int] = {}
    prio_counts: dict[str, int] = {"P1": 0, "P2": 0, "P3": 0}
    cat_savings: dict[str, float] = {}
    for r in recs:
        cat_counts[r["category"]] = cat_counts.get(r["category"], 0) + 1
        prio_counts[r["priority"]] = prio_counts.get(r["priority"], 0) + 1
        cat_savings[r["category"]] = cat_savings.get(r["category"], 0.0) + r["est_savings_usd_month"]
    top_category = max(cat_savings.items(), key=lambda kv: kv[1])[0] if cat_savings else None

    attribution = cost_attribution()

    return {
        "recommendations": recs,
        "summary": {
            "total_addressable_savings_usd_month": round(total_savings),
            # The total sums every lever's estimate; some levers target the SAME
            # dollars at different granularity (e.g. an estate storage play + the
            # per-table recs under it, or Genie-deflection + per-query rewrites),
            # so it's a NON-ADDITIVE upper bound, not a bankable sum. The UI must
            # label it as such (see savings_is_upper_bound).
            "savings_is_upper_bound": True,
            "num_p1": num_p1,
            "num_recs": len(recs),
            "untagged_spend_usd_month": attribution["total_untagged_usd_month"],
            "untagged_pct": attribution["untagged_pct"],
            "top_category": top_category,
            "category_counts": cat_counts,
            "priority_counts": prio_counts,
            "category_savings": {k: round(v) for k, v in cat_savings.items()},
        },
        "attribution": attribution,
        "cached_at": live_cached_at(),
    }
