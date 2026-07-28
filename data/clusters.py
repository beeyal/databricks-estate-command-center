"""Cluster Efficiency view — interactive / all-purpose clusters (Increment 5).

Interactive-cluster waste is almost always *idle / oversized / no auto-stop*,
NOT bad SQL — so this is a distinct view from Query Advisor (which is per-query
and warehouse-centric). Built on cluster-level telemetry that system tables DO
expose reliably:
  * system.compute.clusters       — config: node type, min/max workers, autoscale,
                                     auto-termination minutes, DBR, tags, creator.
  * system.compute.node_timeline  — per-node, per-minute CPU / memory utilisation.
  * system.billing.usage          — DBU + $ per cluster (usage_metadata.cluster_id).

What we deliberately do NOT claim: per-query insight or SQL rewrites for cluster
workloads — that telemetry lives in the Spark UI, not system tables. See the
memory note and the design discussion; the tab is honest about this.

Two entry points (mirrors jobs.py / warehouses.py):
  * ``clusters_demo()``              — deterministic Acme-shaped seed.
  * ``clusters_live(warehouse_id)``  — FAIL-SOFT live reader (never raises).

Findings are computed by :func:`_findings`, shared by both, so demo + live label
identically. We surface *observations* (idle %, no auto-stop, autoscale-off,
oversized) and a per-observation, clearly-estimated potential saving — never a
hard promise.
"""
from __future__ import annotations

from typing import Any

# Thresholds — shared by demo + live.
IDLE_CPU_LOW = 0.12          # mean CPU below this ⇒ under-utilised
IDLE_HOURS_HIGH = 0.35       # fraction of uptime spent effectively idle
AUTOSTOP_LONG_MINS = 120     # auto-termination longer than this is a waste risk
OVERSIZED_CPU = 0.20         # low CPU + many workers ⇒ oversized
MANY_WORKERS = 8
# List-price $/DBU proxy for a rough cost when a resolved rate isn't available
# (a labelled estimate, never billed cost). Mirrors warehouses.DBU_HOUR_PROXY_USD.
DBU_PROXY_USD = 0.55


def _findings(c: dict[str, Any]) -> list[dict[str, str]]:
    """Return a list of {code,label,detail} observations for one cluster.

    Each finding is an *observation* with a plain-language detail — never a
    guaranteed saving. Ordered by how actionable/high-impact it usually is.
    """
    out: list[dict[str, str]] = []
    cpu = c.get("cpu_mean", 1.0)
    idle_frac = c.get("idle_frac", 0.0)
    workers_max = c.get("workers_max", 1)
    autoscale = c.get("autoscale", False)
    auto_stop = c.get("auto_stop_mins", 0)
    mem = c.get("mem_mean", 0.0)
    # Whether utilisation telemetry (node_timeline) was actually available for
    # this cluster. When it's absent, live coerces cpu/mem/idle to 0.0 — which
    # would otherwise fire "idle"/"oversized"/"mem-pressure" off MISSING data
    # ("Mean CPU 0%"), fabricating waste findings from absent telemetry. Config-
    # only findings (auto-stop, autoscale) are still valid without utilisation.
    # Defaults True so the demo path (always has util) is unaffected.
    util_known = c.get("util_known", True)

    if auto_stop == 0:
        out.append({"code": "no-auto-stop", "label": "No auto-termination",
                    "detail": "Cluster has no auto-termination — it keeps billing while idle. Set an auto-termination timeout."})
    elif auto_stop >= AUTOSTOP_LONG_MINS:
        out.append({"code": "long-auto-stop", "label": f"Long auto-stop ({auto_stop}m)",
                    "detail": f"Auto-termination is {auto_stop} min — long idle windows still bill. Consider a shorter timeout."})
    if util_known and (idle_frac >= IDLE_HOURS_HIGH or cpu < IDLE_CPU_LOW):
        out.append({"code": "idle", "label": "Under-utilised",
                    "detail": f"Mean CPU {cpu:.0%}, ~{idle_frac:.0%} of uptime near-idle — the cluster is larger or longer-lived than the work needs."})
    if util_known and cpu < OVERSIZED_CPU and workers_max >= MANY_WORKERS:
        out.append({"code": "oversized", "label": "Oversized",
                    "detail": f"{workers_max} max workers at {cpu:.0%} mean CPU — likely over-provisioned; reduce workers or node size."})
    if not autoscale and workers_max >= 2:
        out.append({"code": "no-autoscale", "label": "Autoscaling off",
                    "detail": "Fixed worker count (autoscaling off) — it pays for peak sizing even at low load. Enable autoscaling with a lower minimum."})
    if util_known and mem >= 0.90:
        out.append({"code": "mem-pressure", "label": "Memory pressure",
                    "detail": f"Mean memory {mem:.0%} — sustained pressure risks spill/OOM; a memory-optimised node may help."})
    return out


def _potential_savings(c: dict[str, Any], findings: list[dict[str, str]]) -> float:
    """Clearly-ESTIMATED $/mo recoverable, from the observations. Conservative:
    a single dominant lever, not additive across every finding (which would
    double-count). Labelled 'estimated' everywhere in the UI."""
    cost = c.get("cost_usd_month", 0.0)
    codes = {f["code"] for f in findings}
    if "no-auto-stop" in codes:
        return round(cost * 0.30, 2)   # idle billing is the biggest single lever
    if "oversized" in codes:
        return round(cost * 0.25, 2)
    if "idle" in codes:
        return round(cost * 0.20, 2)
    if "no-autoscale" in codes:
        return round(cost * 0.12, 2)
    if "long-auto-stop" in codes:
        return round(cost * 0.08, 2)
    return 0.0


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "num_clusters": len(rows),
        "num_with_findings": sum(1 for r in rows if r.get("findings")),
        "num_idle": sum(1 for r in rows if any(f["code"] in ("idle", "no-auto-stop") for f in r.get("findings", []))),
        "num_oversized": sum(1 for r in rows if any(f["code"] == "oversized" for f in r.get("findings", []))),
        "total_cost_usd_month": round(sum(r.get("cost_usd_month", 0) for r in rows), 2),
        "total_potential_savings_usd_month": round(sum(r.get("potential_savings_usd_month", 0) for r in rows), 2),
    }


def _assemble(c: dict[str, Any]) -> dict[str, Any]:
    """Attach findings + estimated savings to a raw cluster row."""
    findings = _findings(c)
    c = dict(c)
    c["findings"] = findings
    c["potential_savings_usd_month"] = _potential_savings(c, findings)
    return c


# ---------------------------------------------------------------------------
# Demo data
# ---------------------------------------------------------------------------

# (name, purpose, node_type, workers_min, workers_max, autoscale, auto_stop, cpu, idle, mem, dbu_rate, ws_index, creator)
_CLUSTER_SEED = [
    ("ds-sandbox-shared", "Data Science sandbox", "Standard_DS4_v2", 2, 12, False, 0, 0.08, 0.62, 0.44, 0.55, 9, "wei.zhang@acme.com"),
    ("ml-training-gpu", "ML training", "Standard_NC6s_v3", 1, 6, True, 120, 0.41, 0.18, 0.71, 0.55, 9, "raj.patel@acme.com"),
    ("adhoc-analytics", "Ad-hoc analytics", "Standard_DS3_v2", 2, 8, False, 60, 0.14, 0.38, 0.52, 0.55, 3, "priya.nathan@acme.com"),
    ("etl-dev-cluster", "ETL dev", "Standard_DS4_v2", 1, 4, True, 30, 0.33, 0.22, 0.66, 0.55, 11, "james.oleary@acme.com"),
    ("scada-notebook", "SCADA notebooks", "Standard_DS3_v2", 1, 10, False, 240, 0.09, 0.55, 0.38, 0.55, 5, "daniel.foster@acme.com"),
    ("finance-adhoc", "Finance ad-hoc", "Standard_DS3_v2", 1, 2, False, 15, 0.28, 0.12, 0.49, 0.55, 6, "sarah.mckenzie@acme.com"),
    ("markets-research", "Markets research", "Standard_E8s_v3", 2, 16, False, 90, 0.11, 0.47, 0.93, 0.55, 2, "hannah.wu@acme.com"),
    ("platform-shared-interactive", "Platform shared", "Standard_DS5_v2", 4, 4, False, 0, 0.19, 0.31, 0.58, 0.55, 0, "priya.nathan@acme.com"),
]


def clusters_demo(days: int = 30) -> list[dict[str, Any]]:
    """Deterministic demo cluster list (with findings + savings, sorted by cost)
    over a ``days`` utilisation window. Additive metrics (uptime, DBUs, cost)
    scale with the window; utilisation (CPU/idle) drifts slightly — a longer
    window captures more idle troughs, so idle_frac rises and mean CPU eases.
    Everything moves with the time filter."""
    import random

    from data import generate_demo as demo

    rng = random.Random(4217)
    workspaces = demo.get_workspaces()
    scale = days / 30.0                       # additive metrics vs the 30d baseline
    idle_shift = min(0.12, (days - 30) / 30.0 * 0.06)   # longer window → more idle seen
    rows: list[dict[str, Any]] = []
    for i, seed in enumerate(_CLUSTER_SEED):
        (name, purpose, node, wmin, wmax, autoscale, auto_stop, cpu, idle, mem, rate, ws_i, creator) = seed
        ws = workspaces[ws_i % len(workspaces)]
        # DBU/hr per node × avg workers × uptime hrs → cost, all scaled to the window.
        avg_workers = (wmin + wmax) / 2 if autoscale else wmax
        uptime_h = rng.uniform(220, 700) * (0.5 if auto_stop else 1.1) * scale
        dbu = round((1 + avg_workers) * rate * uptime_h)
        cost = round(dbu * rng.uniform(0.45, 0.62), 2)
        cpu_w = round(min(1.0, max(0.01, cpu * (1.0 - idle_shift * 0.5))), 3)
        idle_w = round(min(0.98, max(0.0, idle + idle_shift)), 3)
        # 24-bucket CPU-by-hour sketch for the drill-down (window-adjusted mean).
        cpu_timeline = []
        for h in range(24):
            base = cpu_w * (1.7 if 8 <= h <= 18 else 0.4)
            cpu_timeline.append(round(min(1.0, max(0.0, base * rng.uniform(0.6, 1.4))), 3))
        rows.append(_assemble({
            "cluster": name, "cluster_id": f"0{i:03d}-{rng.randint(100000,999999)}-acme{i:02d}",
            "purpose": purpose, "node_type": node,
            "workspace": ws["workspace"], "workspace_id": ws.get("workspace_id", ""),
            "bu": ws.get("bu", ""),
            "workers_min": wmin, "workers_max": wmax, "autoscale": autoscale,
            "auto_stop_mins": auto_stop, "creator": creator,
            "cpu_mean": cpu_w, "mem_mean": mem, "idle_frac": idle_w,
            "uptime_hours_month": round(uptime_h), "dbus_month": dbu,
            "cost_usd_month": cost, "cpu_timeline": cpu_timeline,
            "dbr_version": rng.choice(["14.3 LTS", "15.4 LTS", "16.1", "13.3 LTS"]),
        }))
    rows.sort(key=lambda r: -r["cost_usd_month"])
    return rows


# ---------------------------------------------------------------------------
# Live — compute.clusters + node_timeline + billing. FAIL-SOFT.
# ---------------------------------------------------------------------------

def _cpu_by_hour(warehouse_id: str, days: int = 30) -> dict[str, list[float]]:
    """Mean CPU utilisation by hour-of-day (0–23) per interactive cluster, from
    system.compute.node_timeline. Returns {cluster_id: [24 floats]} — a dense
    24-element list (hours with no samples are 0.0, which is the real "cluster
    idle in that hour" signal, not a gap). Fail-soft to {} so a missing
    node_timeline grant just hides the drill-down chart rather than failing the
    tab. Lookback is capped at node_timeline's ~30d retention even for longer
    windows — the hour-of-day shape is stable and gold/daily paths ask for more."""
    from data import live

    lookback = min(max(1, int(days)), 30)
    try:
        rows = live._run(
            warehouse_id,
            "WITH cfg AS ("
            "  SELECT cluster_id FROM system.compute.clusters "
            "  WHERE cluster_source IN ('UI','API') AND delete_time IS NULL "
            "  QUALIFY row_number() OVER (PARTITION BY workspace_id, cluster_id ORDER BY change_time DESC)=1"
            ") "
            "SELECT nt.cluster_id cid, hour(nt.start_time) h, "
            "AVG(nt.cpu_user_percent+nt.cpu_system_percent)/100.0 cpu "
            "FROM system.compute.node_timeline nt "
            "JOIN cfg ON cfg.cluster_id = nt.cluster_id "
            f"WHERE nt.start_time >= dateadd(day,-{lookback},current_date()) "
            "AND nt.start_time < current_date() "
            "GROUP BY nt.cluster_id, hour(nt.start_time)",
            "system.compute.node_timeline (cpu by hour)",
        )
    except Exception:  # noqa: BLE001 — missing grant / table → chart hidden
        return {}
    tl: dict[str, list[float]] = {}
    for r in rows:
        cid = str(r.get("cid") or "")
        if not cid:
            continue
        h = int(live._f(r.get("h")))
        if 0 <= h <= 23:
            tl.setdefault(cid, [0.0] * 24)[h] = round(live._f(r.get("cpu")), 3)
    return tl


def clusters_live(warehouse_id: str, days: int = 30) -> dict[str, Any]:
    """Read interactive (all-purpose) cluster efficiency from system tables over
    the trailing ``days`` window. Returns ``{"clusters": [...], "summary": {...},
    "message": str}``; fail-soft."""
    from data import live

    days = max(1, int(days))
    try:
        # Latest config per all-purpose cluster + windowed cost + mean CPU/mem
        # from node_timeline. LEFT JOINs so a missing node_timeline/billing grant
        # degrades gracefully to nulls rather than dropping the cluster.
        rows = live._run(
            warehouse_id,
            # system.compute.clusters is an SCD table (a new row per config
            # change), so take the LATEST row per cluster via change_time and
            # skip deleted clusters. Column is worker_node_type (no _id).
            "WITH cfg AS ("
            "  SELECT cluster_id, cluster_name name, worker_node_type node, "
            "         min_autoscale_workers wmin, "
            # Fixed-size clusters leave the autoscale columns NULL and carry the
            # size in worker_count; autoscaling clusters carry the range. Take
            # whichever is present so oversized/no-autoscale findings can fire.
            "         COALESCE(max_autoscale_workers, worker_count) wmax, "
            # Emit 1/0 (not a boolean) — the Statement Execution API returns every
            # cell as a STRING, and bool("false") is truthy. Read back via _f so
            # the no-autoscale finding can actually fire in live mode.
            "         CASE WHEN max_autoscale_workers IS NOT NULL "
            "           AND max_autoscale_workers > COALESCE(min_autoscale_workers,0) "
            "           THEN 1 ELSE 0 END autoscale, "
            "         auto_termination_minutes auto_stop, dbr_version dbr, "
            "         owned_by creator, CAST(workspace_id AS STRING) ws "
            "  FROM system.compute.clusters "
            "  WHERE cluster_source IN ('UI','API') AND delete_time IS NULL "
            "  QUALIFY row_number() OVER (PARTITION BY workspace_id, cluster_id ORDER BY change_time DESC) = 1"
            "), util AS ("
            "  SELECT cluster_id, AVG(cpu_user_percent+cpu_system_percent)/100.0 cpu, "
            "         AVG(mem_used_percent)/100.0 mem, "
            # idle threshold ties to IDLE_CPU_LOW (the finding uses the same cut)
            f"         SUM(CASE WHEN (cpu_user_percent+cpu_system_percent) < {int(IDLE_CPU_LOW * 100)} THEN 1 ELSE 0 END)/NULLIF(COUNT(*),0) idle, "
            "         COUNT(*) samples, "  # >0 ⇒ real telemetry (distinguishes true 0% CPU from no data)
            # Wall-clock uptime = DISTINCT minutes with >=1 active node (one row per
            # node per minute). COUNT(DISTINCT start_time) is size-independent — the
            # old samples/(wmax+1) assumed the cluster always ran at MAX workers, so
            # an autoscaling cluster that mostly ran small was under-reported ~Nx.
            "         COUNT(DISTINCT start_time) uptime_min "
            "  FROM system.compute.node_timeline "
            f"  WHERE start_time>=dateadd(day,-{days},current_date()) "
            "  AND start_time < current_date() "  # exclude incomplete current day
            "  GROUP BY cluster_id"
            "), cost AS ("
            "  SELECT usage_metadata.cluster_id cid, SUM(usage_quantity) dbus "
            "  FROM system.billing.usage "
            f"  WHERE usage_start_time>=dateadd(day,-{days},current_date()) "
            "  AND usage_start_time < current_date() "  # exclude incomplete current day
            "  AND usage_metadata.cluster_id IS NOT NULL GROUP BY usage_metadata.cluster_id"
            ") "
            "SELECT cfg.cluster_id cid, cfg.name, cfg.node, cfg.wmin, cfg.wmax, cfg.autoscale, cfg.auto_stop, "
            "cfg.dbr, cfg.creator, cfg.ws, util.cpu, util.mem, util.idle, util.samples, util.uptime_min, cost.dbus "
            "FROM cfg LEFT JOIN util ON cfg.cluster_id=util.cluster_id "
            "LEFT JOIN cost ON cfg.cluster_id=cost.cid "
            "ORDER BY cost.dbus DESC NULLS LAST LIMIT 200",
            "system.compute.clusters (efficiency)",
        )
    except live.LiveError as e:
        return {"clusters": [], "summary": summarize([]),
                "message": f"Cluster efficiency needs SELECT on system.compute.clusters + node_timeline — {e.detail}"}

    ws_names = live._workspace_names(warehouse_id)
    try:
        bu_by_ws = live._workspace_bu(warehouse_id)   # workspace -> {"bu","owner"}; {} if unmapped
    except Exception:  # noqa: BLE001
        bu_by_ws = {}
    tlmap = _cpu_by_hour(warehouse_id, days)
    out: list[dict[str, Any]] = []
    for r in rows:
        wmin = int(live._f(r.get("wmin")))
        wmax = int(live._f(r.get("wmax")))
        dbus = live._f(r.get("dbus"))
        ws = str(r.get("ws") or "")
        # Wall-clock uptime = DISTINCT minutes with >=1 active node / 60 (size-
        # independent — correct for autoscaling clusters). samples>0 also tells
        # findings that utilisation is REAL (vs a LEFT JOIN miss → 0.0).
        samples = live._f(r.get("samples"))
        util_known = samples > 0
        uptime_h = round(live._f(r.get("uptime_min")) / 60.0) if samples else 0
        out.append(_assemble({
            "cluster": str(r.get("name") or ""), "cluster_id": str(r.get("cid") or ""),
            "purpose": "", "node_type": str(r.get("node") or ""),
            "workspace": ws_names.get(ws, ws), "workspace_id": ws,
            "bu": bu_by_ws.get(ws, {}).get("bu") or "",
            "workers_min": wmin, "workers_max": wmax,
            # _f handles the API's stringified "1"/"0" (bool("0") would be True).
            "autoscale": bool(live._f(r.get("autoscale"))),
            "auto_stop_mins": int(live._f(r.get("auto_stop"))),
            "creator": str(r.get("creator") or ""),
            "cpu_mean": round(live._f(r.get("cpu")), 3),
            "mem_mean": round(live._f(r.get("mem")), 3),
            "idle_frac": round(live._f(r.get("idle")), 3),
            "util_known": util_known,
            "uptime_hours_month": uptime_h,
            "dbus_month": round(dbus),
            # $ proxy from DBUs when a $ rate isn't resolved; see DBU_PROXY_USD.
            "cost_usd_month": round(dbus * DBU_PROXY_USD, 2),
            "cpu_timeline": tlmap.get(str(r.get("cid") or ""), []),
            "dbr_version": str(r.get("dbr") or ""),
        }))
    total_active = _active_cluster_count(warehouse_id, days)
    return {"clusters": out, "summary": summarize(out),
            "total_active": total_active, "shown": len(out),
            "truncated": bool(total_active and total_active > len(out)), "message": ""}


def _active_cluster_count(warehouse_id: str, days: int) -> int:
    """Distinct interactive (UI/API) clusters with node_timeline activity in the
    window — the TRUE universe behind the top-200-by-DBU list, so the UI can say
    "top 200 of N" instead of implying the shown 200 are all clusters. Best-effort
    → 0 (UI then omits the note)."""
    from data import live
    try:
        r = live._run(warehouse_id,
            "SELECT approx_count_distinct(cluster_id) n FROM system.compute.node_timeline "
            f"WHERE start_time>=dateadd(day,-{days},current_date()) AND start_time<current_date()",
            "cluster universe count")
        return int(live._f(r[0].get("n"))) if r else 0
    except Exception:  # noqa: BLE001 — best-effort
        return 0


# Config-only query (no util/cost joins) — reused by the accumulator reader.
_CFG_SQL = (
    "SELECT cluster_id cid, cluster_name name, worker_node_type node, "
    "min_autoscale_workers wmin, COALESCE(max_autoscale_workers, worker_count) wmax, "
    "CASE WHEN max_autoscale_workers IS NOT NULL "
    "  AND max_autoscale_workers > COALESCE(min_autoscale_workers,0) THEN 1 ELSE 0 END autoscale, "
    "auto_termination_minutes auto_stop, dbr_version dbr, owned_by creator, "
    "CAST(workspace_id AS STRING) ws "
    "FROM system.compute.clusters WHERE cluster_source IN ('UI','API') AND delete_time IS NULL "
    "QUALIFY row_number() OVER (PARTITION BY workspace_id, cluster_id ORDER BY change_time DESC) = 1"
)


def clusters_from_daily(warehouse_id: str, gold_schema: str, days: int = 30) -> dict[str, Any] | None:
    """Re-window cluster utilisation from the accumulated daily table
    (<gold>.finops_gold_cluster_daily) over ``days`` and join the latest config.
    Averages are formed from SUMmed numerators / SUMmed sample counts (correct
    re-windowing), never by averaging daily averages. Gives the tab history
    deeper than node_timeline retention. Returns None → caller uses clusters_live."""
    from data import live

    if not live._SCHEMA_RE.match(gold_schema or ""):
        return None
    fqn = f"{gold_schema}.finops_gold_cluster_daily"
    try:
        # DRIVE from the daily table (the clusters that actually ran in the
        # window) and take the top 200 by DBUs — NOT an arbitrary LIMIT-200 slice
        # of the full config table (which would only accidentally overlap the
        # clusters that ran, dropping most cost). We then enrich with config.
        util = live._run(
            warehouse_id,
            "SELECT cluster_id cid, SUM(cpu_sum)/NULLIF(SUM(samples),0) cpu, "
            "SUM(mem_sum)/NULLIF(SUM(samples),0) mem, "
            "SUM(idle_samples)/NULLIF(SUM(samples),0) idle, SUM(dbus) dbus, SUM(samples) samples, "
            "SUM(uptime_min) uptime_min "
            f"FROM {fqn} WHERE usage_date >= dateadd(day,-{max(1, days)},current_date()) "
            "GROUP BY cluster_id ORDER BY dbus DESC NULLS LAST LIMIT 200",
            "gold cluster_daily (re-window)",
        )
    except Exception:  # noqa: BLE001 — accumulator absent → caller uses live
        return None
    if not util:
        return None
    umap = {str(u.get("cid") or ""): u for u in util}
    # Config for exactly the clusters that ran (interactive only). Keyed by cid.
    try:
        cfg_rows = live._run(warehouse_id, _CFG_SQL, "cluster config (for daily join)")
    except live.LiveError:
        return None
    cfgmap = {str(r.get("cid") or ""): r for r in cfg_rows}

    ws_names = live._workspace_names(warehouse_id)
    try:
        bu_by_ws = live._workspace_bu(warehouse_id)   # workspace -> {"bu","owner"}; {} if unmapped
    except Exception:  # noqa: BLE001
        bu_by_ws = {}
    # node_timeline is queried directly (independent of the gold table, which
    # stores only per-date CPU sums, not hour-of-day buckets).
    tlmap = _cpu_by_hour(warehouse_id, days)
    out: list[dict[str, Any]] = []
    for cid, u in umap.items():
        r = cfgmap.get(cid)
        if r is None:
            continue  # not an interactive (UI/API) cluster → not in scope for this tab
        dbus = live._f(u.get("dbus"))
        ws = str(r.get("ws") or "")
        # Wall-clock uptime = DISTINCT active minutes / 60 (from gold uptime_min,
        # size-independent). The old samples/(wmax+1) assumed the cluster always ran
        # at MAX workers, under-reporting autoscaling clusters by ~Nx.
        samples = live._f(u.get("samples"))
        wmax = int(live._f(r.get("wmax")))
        uptime_h = round(live._f(u.get("uptime_min")) / 60.0) if samples else 0
        out.append(_assemble({
            "cluster": str(r.get("name") or ""), "cluster_id": cid,
            "purpose": "", "node_type": str(r.get("node") or ""),
            "workspace": ws_names.get(ws, ws), "workspace_id": ws,
            "bu": bu_by_ws.get(ws, {}).get("bu") or "",
            "workers_min": int(live._f(r.get("wmin"))), "workers_max": wmax,
            "autoscale": bool(live._f(r.get("autoscale"))),
            "auto_stop_mins": int(live._f(r.get("auto_stop"))),
            "creator": str(r.get("creator") or ""),
            "cpu_mean": round(live._f(u.get("cpu")), 3),
            "mem_mean": round(live._f(u.get("mem")), 3),
            "idle_frac": round(live._f(u.get("idle")), 3),
            "util_known": samples > 0,  # real telemetry vs no samples → skip CPU findings
            "uptime_hours_month": uptime_h, "dbus_month": round(dbus),
            "cost_usd_month": round(dbus * DBU_PROXY_USD, 2),
            "cpu_timeline": tlmap.get(cid, []), "dbr_version": str(r.get("dbr") or ""),
        }))
    out.sort(key=lambda x: -x["cost_usd_month"])
    total_active = _active_cluster_count(warehouse_id, days)
    return {"clusters": out, "summary": summarize(out),
            "total_active": total_active, "shown": len(out),
            "truncated": bool(total_active and total_active > len(out)), "message": ""}


if __name__ == "__main__":
    import json

    rows = clusters_demo()
    print("=== clusters demo self-check ===")
    print(f"clusters: {len(rows)}")
    print(json.dumps(summarize(rows), indent=2))
    for r in rows[:3]:
        print(f"  {r['cluster']:28s} ${r['cost_usd_month']:>9,.0f}  save~${r['potential_savings_usd_month']:>8,.0f}  "
              f"{[f['code'] for f in r['findings']]}")
    assert all("findings" in r for r in rows)
    assert any(r["findings"] for r in rows), "expected at least one cluster with findings"

    # Regression: a fixed-size, low-CPU, many-worker cluster must surface both
    # oversized and no-autoscale (the live-mode bug where a stringified "0"
    # autoscale flag read as truthy and suppressed no-autoscale).
    fixed = _assemble({"autoscale": False, "workers_max": 16, "workers_min": 16,
                       "cpu_mean": 0.05, "idle_frac": 0.6, "auto_stop_mins": 60,
                       "mem_mean": 0.4, "cost_usd_month": 2000})
    codes = {f["code"] for f in fixed["findings"]}
    assert {"oversized", "no-autoscale"} <= codes, codes
    print("self-check OK")
