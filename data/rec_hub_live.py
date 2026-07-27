"""Live data provider for the Recommendations hub (rec_hub.py).

rec_hub.py is written against the demo generator's accessors. Rather than fork
its scoring/trigger logic, this module supplies the SAME accessor surface backed
by real system tables — reusing the already-validated live builders in
``data.live`` plus two extra grouped billing queries (per-workspace compute-mix
and tag-coverage). ``rec_hub.set_provider(LiveProvider(...))`` swaps it in.

HONEST DEGRADATION — what system tables do NOT carry, and how we handle it:
  • Business unit: billing has no BU dimension unless the customer tags for it,
    so BU collapses to the workspace id (plays still fire, scoped per workspace).
  • Idle %: a labelled estimate derived from query.history (active execution
    time vs the wall-clock a warehouse was open — see live._workspace_facts);
    0.0 when query.history isn't readable, so the idle play just won't fire.
  • Table file-hygiene savings: deferred to DESCRIBE-on-drill-down (see
    live._live_table_defaults), so per-table savings recs are empty in live —
    we never fabricate a $ figure.
  • Access risks: only the over-privileged-on-prod signal is derivable from
    information_schema privileges; broad-grant / admin-sprawl / stale-token are
    left empty rather than guessed.
  • Governance report: tiles are computed purely from system tables (tag
    coverage, classification, certification, external-table share). The
    admin-API-only controls (budgets, cluster policy, PAT, alerts) are NOT
    injected here — they stay Preview on the Governance tab.

Strategic-play savings remain LABELLED ESTIMATES from recommendation_catalog.yaml;
going live makes the TRIGGERS fire on real estate metrics, not the $ exact.
"""
from __future__ import annotations

from typing import Any

from data import live
from data.ai_cost import AI_PRODUCTS

# effective list price per usage row, reused across the grouped queries
_PRICE = "u.usage_quantity*lp.pricing.effective_list.default"
_PRICE_JOIN = (
    "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
    "AND u.usage_start_time>=lp.price_start_time "
    "AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time)"
)
# billing_origin_product codes that count as AI/GenAI spend for the genai_cost
# mix. Derived from the SINGLE canonical AI-product list (data/ai_cost.py) so
# the Recommendations-hub genai_cost + AI-001 play always agree with the AI $
# tab (was a hand-maintained set with wrong/nonexistent codes —
# FOUNDATION_MODEL_SERVING/MODEL_TRAINING/FINE_TUNING/AGENT match nothing, and it
# omitted AI_GATEWAY/AGENT_BRICKS/FOUNDATION_MODEL_TRAINING, so FMAPI-heavy
# estates showed genai_cost≈0 and the AI-001 play never fired).
_GENAI_PRODUCTS = tuple(code for code, _label in AI_PRODUCTS)

# query insight types that count as "inefficient" (mirror rec_hub._INEFFICIENT)
_INEFFICIENT = {"cluster", "resize", "rewrite", "full-scan"}


def _tier(value: float, good: float, warn: float, invert: bool = False) -> str:
    """Good/Warning/Critical banding (mirrors demo._tier semantics)."""
    if invert:
        if value <= good:
            return "Good"
        return "Warning" if value <= warn else "Critical"
    if value >= good:
        return "Good"
    return "Warning" if value >= warn else "Critical"


class LiveProvider:
    """Demo-accessor-compatible facade over the live system-table builders.

    All reads are memoised on the instance so one ``build_hub`` call issues each
    underlying query once. Any ``live.LiveError`` propagates so app.py can fail
    closed (503 with the exact grant/config to fix) — never falls back to demo.
    """

    def __init__(self, warehouse_id: str) -> None:
        self.wh = warehouse_id
        self._cache: dict[str, Any] = {}

    def reset_cache(self) -> None:
        """Drop the per-instance query memo so the next hub build re-reads live
        system tables. Called by rec_hub's TTL so an always-on app container
        doesn't serve process-lifetime-frozen cost data."""
        self._cache.clear()

    def _memo(self, key: str, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # --- direct passthroughs to validated live builders ---------------------
    def get_workspaces(self) -> list[dict[str, Any]]:
        return self._memo("ws", lambda: live.workspaces_live(self.wh))

    def get_queries(self) -> list[dict[str, Any]]:
        return self._memo("q", lambda: live.queries_live(self.wh))

    def get_tables(self) -> list[dict[str, Any]]:
        return self._memo("t", lambda: live.tables_live(self.wh))

    def get_cost_drivers(self, workspace: str | None = None) -> dict[str, Any]:
        return self._memo("cd", lambda: live.cost_drivers_live(self.wh))

    # --- risks: map the live over-privileged signal to the demo risk kinds ---
    def get_risks(self) -> list[dict[str, Any]]:
        def build() -> list[dict[str, Any]]:
            raw = live.access_grants_live(self.wh)["risks"]
            out: list[dict[str, Any]] = []
            for r in raw:
                # live emits "Over-privileged on production"; rec_hub keys on the
                # short kind "over-privileged".
                kind = "over-privileged" if "over-privileged" in str(r.get("risk", "")).lower() else None
                if not kind:
                    continue
                # access_grants_live already emits lowercase "critical"/"warning";
                # pass it through (the old `== "High"` check never matched, so every
                # risk silently became "warning").
                sev = str(r.get("severity", "")).lower()
                out.append({
                    "risk": kind, "principal": r.get("principal", ""),
                    "severity": sev if sev in ("critical", "warning") else "warning",
                    "detail": r.get("detail", ""),
                    "recommended_action": r.get("recommended_action", ""),
                })
            return out
        return self._memo("risks", build)

    # --- tagging-by-workspace: real per-workspace tag coverage from billing --
    def _tag_coverage(self) -> dict[str, dict[str, float]]:
        def build() -> dict[str, dict[str, float]]:
            rows = live._run(self.wh,
                "SELECT CAST(u.workspace_id AS STRING) ws, "
                f"SUM({_PRICE}) usd, "
                f"SUM(CASE WHEN u.custom_tags IS NULL OR size(u.custom_tags)=0 THEN {_PRICE} ELSE 0 END) untagged "
                f"FROM system.billing.usage u {_PRICE_JOIN} "
                "WHERE u.usage_date>=date_trunc('MONTH',current_date()) GROUP BY 1",
                "billing (tag coverage)")
            out: dict[str, dict[str, float]] = {}
            for r in rows:
                ws = str(r.get("ws"))
                usd = live._f(r.get("usd")); unt = live._f(r.get("untagged"))
                out[ws] = {"spend": usd, "untagged": unt,
                           "tagging_pct": round(1 - (unt / usd), 3) if usd else 0.0}
            return out
        return self._memo("tagcov", build)

    def tagging_by_workspace(self) -> list[dict[str, Any]]:
        cov = self._tag_coverage()
        # _tag_coverage keys by workspace_id, but cost_attribution matches these
        # rows to workspaces by w["workspace"] — the resolved friendly NAME (see
        # workspaces_live). Resolve id→name here so the match hits; otherwise
        # every workspace defaults to tagging_pct=None/untagged=0 and every BU
        # reads a fake "100% tagged". Keep workspace_id for reference.
        names = live._workspace_names(self.wh)
        return [{"workspace": names.get(ws, ws), "workspace_id": ws,
                 "tagging_pct": v["tagging_pct"],
                 "untagged_usd_month": round(v["untagged"]), "spend_usd_month": round(v["spend"])}
                for ws, v in cov.items()]

    # --- per-workspace compute-mix (one grouped billing × SKU-family query) --
    def _compute_mix(self) -> dict[str, dict[str, float]]:
        def build() -> dict[str, dict[str, float]]:
            genai_in = ",".join(repr(p) for p in _GENAI_PRODUCTS)
            rows = live._run(self.wh,
                "SELECT CAST(u.workspace_id AS STRING) ws, "
                f"SUM(CASE WHEN u.sku_name ILIKE '%ALL_PURPOSE%' THEN {_PRICE} ELSE 0 END) all_purpose, "
                f"SUM(CASE WHEN u.billing_origin_product='JOBS' AND u.sku_name NOT ILIKE '%SERVERLESS%' THEN {_PRICE} ELSE 0 END) jobs_classic, "
                f"SUM(CASE WHEN u.sku_name ILIKE '%SQL%' AND u.sku_name NOT ILIKE '%SERVERLESS%' THEN {_PRICE} ELSE 0 END) sql_classic, "
                f"SUM(CASE WHEN u.sku_name ILIKE '%SERVERLESS%' THEN {_PRICE} ELSE 0 END) serverless, "
                f"SUM(CASE WHEN u.billing_origin_product IN ({genai_in}) THEN {_PRICE} ELSE 0 END) genai "
                f"FROM system.billing.usage u {_PRICE_JOIN} "
                "WHERE u.usage_date>=date_trunc('MONTH',current_date()) GROUP BY 1",
                "billing (compute mix)")
            out: dict[str, dict[str, float]] = {}
            for r in rows:
                out[str(r.get("ws"))] = {k: live._f(r.get(k)) for k in
                    ("all_purpose", "jobs_classic", "sql_classic", "serverless", "genai")}
            return out
        return self._memo("mix", build)

    # --- governance report: system-table tiles only (no admin-API controls) --
    def get_governance_report(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            cls = live.classification_live(self.wh)
            cov = self._tag_coverage()
            total_spend = sum(v["spend"] for v in cov.values()) or 1.0
            total_untagged = sum(v["untagged"] for v in cov.values())
            tagged_pct = round(1 - (total_untagged / total_spend), 3)

            class_cov = cls["classification"]["coverage_pct"]
            cert = next((g for g in cls["governed_tags"] if g["tag_key"] == "system.certification_status"), None)
            cert_cov = cert["coverage_pct"] if cert else 0.0

            # external-table share from information_schema (clean denominator)
            ext = live._run(self.wh,
                "SELECT SUM(CASE WHEN table_type='EXTERNAL' THEN 1 ELSE 0 END) ext, COUNT(*) tot "
                "FROM system.information_schema.tables WHERE table_schema<>'information_schema'",
                "information_schema.tables (external share)")
            tot = live._f(ext[0].get("tot")) if ext else 0.0
            ext_share = (live._f(ext[0].get("ext")) / tot) if tot else 0.0

            tiles = [
                {"metric": "Tagging coverage", "category": "Cost attribution", "weight": 3,
                 "value_pct": tagged_pct, "status": _tier(tagged_pct, 0.8, 0.5),
                 "gap": f"{round((1 - tagged_pct) * 100)}% of spend is untagged",
                 "action": "Enforce cost-attribution tags via serverless budget policies + cluster policies",
                 "ties_to": None},
                {"metric": "Classification coverage", "category": "Data classification", "weight": 2,
                 "value_pct": round(class_cov, 3), "status": _tier(class_cov, 0.6, 0.3),
                 "gap": f"{round(class_cov * 100)}% of tables carry auto-classification tags",
                 "action": "Enable Data Classification on remaining catalogs/schemas",
                 "ties_to": None},
                {"metric": "Certification coverage", "category": "Trust & quality", "weight": 2,
                 "value_pct": round(cert_cov, 3), "status": _tier(cert_cov, 0.5, 0.2),
                 "gap": f"{round(cert_cov * 100)}% of tables carry a certification status tag",
                 "action": "Certify curated gold tables with system.certification_status",
                 "ties_to": None},
                {"metric": "External / unmanaged data", "category": "Storage governance", "weight": 2,
                 "value_pct": round(1 - ext_share, 3), "status": _tier(1 - ext_share, 0.85, 0.6),
                 "gap": f"{round(ext_share * 100)}% of tables are external (miss auto-maintenance)",
                 "action": "Convert high-value external tables to UC managed",
                 "ties_to": "tables"},
            ]
            _SCORE = {"Good": 1.0, "Warning": 0.5, "Critical": 0.0}
            for t in tiles:
                t["score_points"] = _SCORE[t["status"]]
            total_w = sum(t["weight"] for t in tiles) or 1
            score = round((sum(t["weight"] * t["score_points"] for t in tiles) / total_w) * 100)
            grade = "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "E"
            return {
                "score": score, "grade": grade,
                "counts": {s: sum(1 for t in tiles if t["status"] == s) for s in ("Good", "Warning", "Critical")},
                "num_tiles": len(tiles), "tiles": tiles,
                "tagging_by_workspace": self.tagging_by_workspace(),
            }
        return self._memo("gov", build)

    # --- the estate-metrics rollup the strategic-play triggers reference -----
    def estate_metrics(self) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            workspaces = self.get_workspaces()
            queries = self.get_queries()
            tables = self.get_tables()
            risks = self.get_risks()
            cov = self._tag_coverage()
            mix = self._compute_mix()
            report = self.get_governance_report()

            # BU comes from the customer workspace->BU mapping (config
            # workspace_bu_table), which workspaces_live already resolved onto each
            # row's "bu" field. Fall back to the workspace name when a workspace
            # isn't mapped (or no mapping table is configured) so aggregation still
            # groups sensibly — matches the demo path in rec_hub.estate_metrics.
            bu_of_ws = {w["workspace"]: (w.get("bu") or w["workspace"]) for w in workspaces}
            ws_by_name = {w["workspace"]: w for w in workspaces}

            total_spend = sum(w["spend_usd_month"] for w in workspaces)
            total_untagged = sum(v["untagged"] for v in cov.values())
            untagged_pct = (total_untagged / total_spend) if total_spend else 0.0

            # per-BU (=workspace) + per-user query aggregation
            bu_agg: dict[str, dict[str, Any]] = {}
            user_agg: dict[str, dict[str, Any]] = {}
            for q in queries:
                bu = bu_of_ws.get(q["workspace"], q["workspace"])
                inefficient = q.get("insight_type") in _INEFFICIENT
                cost = q.get("cost_usd", 0.0)
                b = bu_agg.setdefault(bu, {
                    "bu": bu, "inefficient_query_count": 0, "inefficient_run_count": 0,
                    "query_cost_usd_month": 0.0, "authors": set(), "inefficient_cost": 0.0,
                    "full_scan_count": 0, "total_query_count": 0,
                })
                b["total_query_count"] += 1
                b["query_cost_usd_month"] += cost
                b["authors"].add(q.get("user", ""))
                if inefficient:
                    b["inefficient_query_count"] += q.get("runs", 0)
                    b["inefficient_run_count"] += q.get("runs", 0)
                    b["inefficient_cost"] += cost
                if "full-scan" in q.get("flags", []):
                    b["full_scan_count"] += 1
                u = user_agg.setdefault(q.get("user", ""), {
                    "user": q.get("user", ""), "runs": 0, "query_cost_usd_month": 0.0,
                    "full_scan_count": 0, "query_count": 0,
                })
                u["runs"] += q.get("runs", 0)
                u["query_cost_usd_month"] += cost
                u["query_count"] += 1
                if "full-scan" in q.get("flags", []):
                    u["full_scan_count"] += 1
            for b in bu_agg.values():
                b["distinct_authors"] = len(b["authors"])
                b["authors"] = sorted(a for a in b["authors"] if a)
            for u in user_agg.values():
                u["full_scan_share"] = (u["full_scan_count"] / u["query_count"]) if u["query_count"] else 0.0

            # per-workspace compute mix + maturity signals
            ws_metrics: dict[str, dict[str, Any]] = {}
            for w in workspaces:
                # _compute_mix keys by workspace_id (CAST(workspace_id AS STRING)),
                # but workspaces_live overwrites w["workspace"] with the resolved
                # friendly NAME — so look up by workspace_id, else every mix lookup
                # misses whenever names resolve and the compute/genai/behavioural
                # plays silently zero out.
                m = mix.get(str(w.get("workspace_id") or w["workspace"]), {})
                all_purpose = m.get("all_purpose", 0.0)
                jobs_classic = m.get("jobs_classic", 0.0)
                sql_classic = m.get("sql_classic", 0.0)
                serverless = m.get("serverless", 0.0)
                genai = m.get("genai", 0.0)
                spend = w["spend_usd_month"]
                ws_metrics[w["workspace"]] = {
                    "workspace": w["workspace"], "bu": (w.get("bu") or w["workspace"]),
                    "spend_usd_month": spend,
                    "all_purpose_cost": round(all_purpose), "jobs_classic_cost": round(jobs_classic),
                    "sql_classic_cost": round(sql_classic), "serverless_cost": round(serverless),
                    "classic_cluster_cost": round(all_purpose + jobs_classic + sql_classic),
                    "genai_cost": round(genai),
                    "interactive_share": round((all_purpose / spend) if spend else 0.0, 3),
                    # idle % is a labelled estimate from query.history (see
                    # live._workspace_facts); 0.0 when query.history isn't readable.
                    "idle_pct": w.get("idle_pct", 0.0),
                    "serverless_share": w.get("serverless_share", 0.0),
                    "automated_pct": w.get("automated_pct", 0.0),
                }

            external_tables = [t for t in tables if t.get("table_type") == "EXTERNAL"]
            external_convert_usd = sum(t.get("convert_saving_usd_month", 0.0) for t in external_tables)

            over_priv = [r for r in risks if r["risk"] == "over-privileged"]

            estate_drivers = self.get_cost_drivers()
            driver_spikes = sorted(
                [{"driver": mm["driver"], "label": mm["label"], "mom_pct": mm["mom_pct"],
                  "prev_usd_month": mm["prev_usd_month"], "spend_usd_month": mm["spend_usd_month"],
                  "delta_usd": mm["delta_usd"]}
                 for mm in estate_drivers.get("mom", [])
                 if mm["mom_pct"] >= 0.25 and mm["spend_usd_month"] >= 1500],
                key=lambda s: -s["delta_usd"])

            return {
                "total_spend_usd_month": total_spend,
                "total_untagged_usd_month": round(total_untagged),
                "untagged_pct": round(untagged_pct, 3),
                "driver_spikes": driver_spikes,
                "bu_agg": bu_agg,
                "user_agg": user_agg,
                "ws_metrics": ws_metrics,
                "external_tables": external_tables,
                "external_convert_usd_month": round(external_convert_usd),
                "over_privileged": over_priv,
                "stale_tokens": [], "broad_grants": [], "admin_sprawl": [],
                "governance": report,
                "tag_rows": self.tagging_by_workspace(),
                "spikes": [],                   # per-workspace MoM not computed live
                "bu_of_ws": bu_of_ws,
                "ws_by_name": ws_by_name,
                # idle-warehouse play fires on workspaces with material estimated idle
                "idle_warehouses": [
                    {"warehouse": w["workspace"], "idle_pct": w.get("idle_pct", 0.0),
                     "spend_usd_month": w["spend_usd_month"]}
                    for w in workspaces if w.get("idle_pct", 0.0) >= 0.20],
            }
        return self._memo("estate", build)
