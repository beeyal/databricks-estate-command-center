"""SQL Warehouse monitor (Increment 4).

Per-warehouse operational health for SQL warehouses (incl. serverless SQL):
throughput, latency, queueing, spill, cost, top users/sources, and an hourly
query-volume timeline for the drill-down heatmap. Observable facts only — no
model, always on.

Two entry points, mirroring ``data/jobs.py``:
  * ``warehouses_demo()``            — deterministic, Acme-shaped seeded data.
  * ``warehouses_live(warehouse_id)``— reads system.query.history (+ billing) and
                                       is FAIL-SOFT: an ungranted table yields an
                                       empty, well-formed payload with a
                                       ``message`` rather than 503-ing the tab.

Both funnel through :func:`_classify` so the "why watch this" flags and the
summary are computed identically in demo and live.

Live system tables mirrored:
  * system.query.history — per-query warehouse_id, durations, queued-at-capacity,
    read/written bytes, spill, executed_by, statement source. Aggregated per
    warehouse + per hour.

Cost is a duration-based PROXY (query-hours × DBU_HOUR_PROXY_USD), NOT billed $:
system.query.history exposes no billed cost per warehouse, and billing.usage does
not carry a per-warehouse breakdown that cleanly reconciles here. The proxy is
labelled "estimated, not billed" at every point of use in the UI (column tooltip,
KPI hint, tab note); the Account Console Usage dashboard is the source of truth
for actual warehouse cost.
"""
from __future__ import annotations

from typing import Any

# Watch-reason thresholds — shared by demo + live so both label identically.
QUEUE_HIGH = 0.15        # >15% of runtime waiting at capacity ⇒ capacity-bound
P95_SLOW_S = 45.0        # p95 latency ceiling before "slow"
SPILL_HIGH_GB = 25.0     # sustained spill ⇒ undersized
IDLE_LOW_RUNS = 20       # very few runs but non-trivial cost ⇒ idle/over-provisioned
IDLE_COST = 400.0        # $/mo above which a near-idle warehouse is worth flagging
# $/DBU-hour proxy when a resolved $ rate isn't available (labelled estimate,
# never billed cost). Canonical value lives in live.DBU_HOUR_PROXY_USD; mirrored
# here as a module constant because it's interpolated into SQL in this file
# (live is imported lazily inside functions to avoid a circular import).
DBU_HOUR_PROXY_USD = 0.55  # keep in sync with live.DBU_HOUR_PROXY_USD


def _classify(w: dict[str, Any]) -> list[str]:
    """Derive the 'why watch this warehouse' flags from its metrics."""
    reasons: list[str] = []
    if w.get("queued_ratio", 0) >= QUEUE_HIGH:
        reasons.append("capacity-bound")
    if w.get("p95_s", 0) >= P95_SLOW_S:
        reasons.append("slow")
    if w.get("spill_gb", 0) >= SPILL_HIGH_GB:
        reasons.append("spilling")
    # "idle" compares a MONTHLY run count against a MONTHLY cost threshold. cost_usd_month
    # is 30-day-normalised at every time-range chip, so the run count must be too — else
    # at the 24h/7d chips a genuinely-active warehouse (e.g. 15 runs today ≈ 450/mo) trips
    # runs<=20 and gets a false "idle → downsize". Use runs_30d (the normalised count) when
    # present; demo/30d windows set runs == runs_30d so behaviour is unchanged there.
    runs_monthly = w.get("runs_30d", w.get("runs", 0))
    if runs_monthly <= IDLE_LOW_RUNS and w.get("cost_usd_month", 0) >= IDLE_COST:
        reasons.append("idle")
    return reasons


# One tier up / down the T-shirt sizes, so "size up" advice can name the next
# size instead of a vague "increase". Serverless uses the same size ladder.
_SIZE_LADDER = [
    "2X-Small", "X-Small", "Small", "Medium", "Large", "X-Large",
    "2X-Large", "3X-Large", "4X-Large",
]


def _next_size_up(size: str) -> str | None:
    try:
        i = _SIZE_LADDER.index(size)
    except ValueError:
        return None
    return _SIZE_LADDER[i + 1] if i + 1 < len(_SIZE_LADDER) else None


def _next_size_down(size: str) -> str | None:
    try:
        i = _SIZE_LADDER.index(size)
    except ValueError:
        return None
    return _SIZE_LADDER[i - 1] if i > 0 else None


def advise(w: dict[str, Any]) -> dict[str, Any]:
    """Turn one warehouse's real metrics into actionable advice for the
    drill-down. Deterministic and explainable — every recommendation names the
    measured signal that triggered it and the concrete lever to pull. Priority
    mirrors $ leverage: fix queueing/spill (perf + cost) before trimming idle,
    and only recommend right-sizing DOWN when nothing is under pressure.

    Returns ``{recommendation, rationale, next_steps[]}``. When nothing crosses
    a threshold the recommendation is an explicit, honest "healthy — no change".
    """
    reasons = w.get("reasons") or _classify(w)
    size = str(w.get("size") or "")
    queue = w.get("queued_ratio", 0.0)
    spill = w.get("spill_gb", 0.0)
    p95 = w.get("p95_s", 0.0)
    runs = int(w.get("runs", 0))
    cost = w.get("cost_usd_month", 0.0)
    auto_stop = int(w.get("auto_stop_mins", 0) or 0)
    peak = int(w.get("peak_concurrency", 0) or 0)
    up = _next_size_up(size)
    down = _next_size_down(size)

    # 1) Spilling — undersized memory. Sizing up is the direct fix.
    if "spilling" in reasons:
        return {
            "recommendation": "resize-up",
            "rationale": (
                f"Sustained disk spill ({spill:.0f} GB) — the warehouse is memory-undersized, so "
                f"the engine re-reads spilled data every run, inflating both latency and cost."
            ),
            "next_steps": [
                f"Size up one tier{f' ({size} → {up})' if up else ''} to add memory and eliminate the spill",
                "Re-check spill after a day; if it's gone but the warehouse is now under-utilised, trim back one tier",
                "For a few heavy outliers, consider isolating them on their own warehouse instead of sizing the whole fleet up",
            ],
        }
    # 2) Capacity-bound — queueing at peak. Add a cluster (scale-out) first;
    #    sizing up doesn't relieve concurrency pressure, more clusters do.
    if "capacity-bound" in reasons:
        return {
            "recommendation": "add-cluster",
            "rationale": (
                f"{queue:.0%} of runtime is spent queueing at peak (concurrency reached {peak}) — "
                f"the warehouse is a bottleneck when load is high. More clusters relieve concurrency; "
                f"sizing up does not."
            ),
            "next_steps": [
                "Raise the max clusters (multi-cluster / auto-scaling) so it scales out at peak",
                "Confirm queueing falls at the busy hours shown in the hourly volume chart",
                "If p95 is also high on single queries, size up as well — queueing and slow-per-query are different problems",
            ],
        }
    # 3) Slow tail with no spill/queue — likely per-query inefficiency, not the
    #    warehouse. Point to the Query Advisor, don't just throw hardware at it.
    if "slow" in reasons:
        return {
            "recommendation": "investigate-queries",
            "rationale": (
                f"High p95 latency ({p95:.0f}s) without spill or queueing — the slow tail is more likely "
                f"inefficient queries (low pruning, SELECT *) than an undersized warehouse."
            ),
            "next_steps": [
                "Open the Query Advisor filtered to this warehouse and fix the top-impact queries first",
                "Only size up if the slow queries are already well-optimised and still memory-bound",
                "Watch that sizing up doesn't just raise cost without moving p95",
            ],
        }
    # 4) Idle / over-provisioned — few runs but real cost. Auto-stop + size down.
    if "idle" in reasons:
        steps = []
        if auto_stop == 0 or auto_stop > 10:
            steps.append(
                f"Set a tighter auto-stop ({'currently off' if auto_stop == 0 else f'currently {auto_stop}m'} → 5–10m) so it stops billing when idle"
            )
        if down:
            steps.append(f"Right-size down one tier ({size} → {down}) — the load doesn't need this size")
        steps.append("Or consolidate this workload onto a shared warehouse if it's rarely used")
        return {
            "recommendation": "downsize-or-autostop",
            "rationale": (
                f"Only {runs:,} runs but ~{cost:,.0f}/mo — the warehouse is over-provisioned for its "
                f"load, paying for idle capacity."
            ),
            "next_steps": steps,
        }
    # 5) Nothing crossed a threshold — say so honestly rather than inventing work.
    tips = ["No sizing, scaling, or idle issue detected from the current window — leave it as is."]
    if auto_stop == 0:
        tips.append("Auto-stop is off — even a healthy warehouse should set one (e.g. 10m) to avoid idle billing.")
    tips.append("Re-check after a busier period or a wider time window if load is seasonal.")
    return {
        "recommendation": "healthy",
        "rationale": "Within thresholds on queueing, spill, latency and utilisation for this window.",
        "next_steps": tips,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """KPI summary reconciling to the (filtered) rows."""
    n = len(rows)
    total_cost = round(sum(r.get("cost_usd_month", 0) for r in rows), 2)
    total_runs = sum(r.get("runs", 0) for r in rows)
    # Estate queue ratio, DURATION-weighted. queued_ratio is queued_ms/duration_ms
    # per warehouse, so the true estate figure is Σqueued_ms/Σduration_ms — i.e.
    # weight each warehouse's ratio by its total DURATION, not its run count.
    # We don't carry raw ms onto the row, so use p50_s*runs as a duration proxy
    # (a few long-running, heavily-queued warehouses would otherwise be diluted
    # by many fast, un-queued ones under run-count weighting). Falls back to
    # run-weighting, then a plain mean, if the proxy is unavailable.
    dur_w = sum(max(r.get("p50_s", 0.0), 0.0) * r.get("runs", 0) for r in rows)
    if dur_w > 0:
        est_queue = round(sum(r.get("queued_ratio", 0) * max(r.get("p50_s", 0.0), 0.0) * r.get("runs", 0) for r in rows) / dur_w, 3)
    elif total_runs:
        est_queue = round(sum(r.get("queued_ratio", 0) * r.get("runs", 0) for r in rows) / total_runs, 3)
    else:
        est_queue = round(sum(r.get("queued_ratio", 0) for r in rows) / n, 3) if n else 0.0
    return {
        "num_warehouses": n,
        "num_capacity_bound": sum(1 for r in rows if "capacity-bound" in r.get("reasons", [])),
        "num_idle": sum(1 for r in rows if "idle" in r.get("reasons", [])),
        "num_spilling": sum(1 for r in rows if "spilling" in r.get("reasons", [])),
        "total_cost_usd_month": total_cost,
        "total_runs": total_runs,
        "est_queue_ratio": est_queue,
        "biggest_queue_ratio": round(max((r.get("queued_ratio", 0) for r in rows), default=0), 3),
    }


# ---------------------------------------------------------------------------
# Demo data — deterministic, Acme-shaped
# ---------------------------------------------------------------------------

# (name, type, size, base_runs/day, base_p50_s, workspace-index)
_WH_SEED = [
    ("wh-energy-markets-xl", "Serverless", "2X-Large", 4200, 3.1, 2),
    ("wh-gas-transmission-l", "Pro", "Large", 2600, 4.4, 1),
    ("wh-asset-analytics-m", "Serverless", "Medium", 1800, 2.2, 3),
    ("wh-metering-l", "Pro", "Large", 3100, 5.8, 4),
    ("wh-shared-bi-m", "Serverless", "Medium", 5400, 1.9, 10),
    ("wh-finance-s", "Classic", "Small", 640, 6.2, 6),
    ("wh-scada-m", "Pro", "Medium", 1200, 3.6, 5),
    ("wh-billing-l", "Pro", "Large", 2200, 7.1, 7),
]

# $/mo per size (list-price-ish, for a plausible demo).
_SIZE_COST = {"Small": 1_900, "Medium": 4_200, "Large": 9_800, "2X-Large": 21_500}
_QUERY_SOURCES = ["SQL editor", "Notebook", "Scheduled job", "DBSQL dashboard", "Power BI", "Tableau", "API / JDBC"]


def _mk_row(idx: int, seed: tuple, workspaces: list[dict[str, Any]], rng) -> dict[str, Any]:
    name, wtype, size, runs_day, p50, ws_i = seed
    ws = workspaces[ws_i % len(workspaces)]
    runs = int(runs_day * 30 * rng.uniform(0.85, 1.15))
    # Serverless queues less; classic/pro queue more when busy.
    q_base = {"Serverless": 0.04, "Pro": 0.12, "Classic": 0.22}[wtype]
    queued = round(min(0.6, q_base * rng.uniform(0.6, 2.4)), 3)
    p95 = round(p50 * rng.uniform(3.0, 9.0), 1)
    spill = round(max(0.0, rng.gauss(8, 14)), 1)
    cost = round(_SIZE_COST[size] * rng.uniform(0.7, 1.25) * (0.8 if wtype == "Serverless" else 1.0), 2)
    users = rng.randint(4, 60)
    # Hourly query-volume timeline (24 buckets) — business-hours-weighted.
    peak = runs / 22.0
    timeline = []
    for h in range(24):
        if 7 <= h <= 19:
            factor = 0.6 + 0.9 * rng.uniform(0.7, 1.3)
        else:
            factor = 0.12 * rng.uniform(0.4, 1.6)
        timeline.append(round(peak * factor))
    peak_concurrency = max(2, round(max(timeline) / 12 * rng.uniform(0.8, 1.4)))
    top_users = sorted(
        [{"user": u, "runs": rng.randint(20, max(21, runs // 6))}
         for u in rng.sample(_USERS_POOL, min(5, len(_USERS_POOL)))],
        key=lambda x: -x["runs"],
    )
    src_w = [rng.uniform(0.5, 3) for _ in _QUERY_SOURCES]
    tot = sum(src_w)
    top_sources = sorted(
        [{"source": s, "pct": round(w / tot, 3), "runs": round(runs * w / tot)}
         for s, w in zip(_QUERY_SOURCES, src_w)],
        key=lambda x: -x["runs"],
    )[:5]
    row = {
        "warehouse": name,
        "warehouse_id": f"wh{4100000000000000 + idx * 8087:d}"[:16],
        "workspace": ws["workspace"],
        "workspace_id": ws.get("workspace_id", ""),
        "bu": ws.get("bu", ""),
        "type": wtype,
        "size": size,
        "auto_stop_mins": rng.choice([10, 15, 30, 60, 120]),
        "runs": runs,
        "p50_s": round(p50, 1),
        "p95_s": p95,
        "queued_ratio": queued,
        "spill_gb": spill,
        "bytes_read": int(runs * rng.uniform(0.4, 4.0) * 1e9),
        "distinct_users": users,
        "cost_usd_month": cost,
        "peak_concurrency": peak_concurrency,
        "timeline": timeline,
        "top_users": top_users,
        "top_sources": top_sources,
    }
    row["reasons"] = _classify(row)
    row["advice"] = advise(row)
    return row


_USERS_POOL = [
    "priya.nathan@acme.com", "james.oleary@acme.com", "wei.zhang@acme.com",
    "sarah.mckenzie@acme.com", "raj.patel@acme.com", "hannah.wu@acme.com",
    "daniel.foster@acme.com", "svc-lakeflow-orchestrator", "svc-billing-etl",
]


def warehouses_demo() -> list[dict[str, Any]]:
    """Deterministic demo warehouse list (classified + sorted by cost)."""
    import random

    from data import generate_demo as demo

    rng = random.Random(4217)
    workspaces = demo.get_workspaces()
    rows = [_mk_row(i, seed, workspaces, rng) for i, seed in enumerate(_WH_SEED)]
    rows.sort(key=lambda r: -r["cost_usd_month"])
    return rows


# ---------------------------------------------------------------------------
# Live — system.query.history (+ billing). FAIL-SOFT (never raises).
# ---------------------------------------------------------------------------

# Map the UI time-range chip to a trailing-window day count for the live query
# so the selector actually changes the window (it was a no-op before).
_RANGE_DAYS = {"1h": 1, "6h": 1, "24h": 1, "7d": 7, "30d": 30, "custom": 30}


def warehouses_live(warehouse_id: str, time_range: str = "30d") -> dict[str, Any]:
    """Read real warehouse operational metrics over the selected trailing window.
    Returns ``{"warehouses": [...], "summary": {...}, "message": str}``. On any
    live failure returns an empty, well-formed payload with an explanatory
    message — the tab explains itself rather than 503-ing."""
    from data import live

    days = _RANGE_DAYS.get(time_range, 30)
    try:
        rows = live._run(
            warehouse_id,
            # Aggregate query.history per warehouse over the trailing window.
            "SELECT compute.warehouse_id wh, CAST(any_value(workspace_id) AS STRING) ws, "
            "COUNT(*) runs, "
            "approx_percentile(execution_duration_ms,0.5)/1000 p50, "
            "approx_percentile(execution_duration_ms,0.95)/1000 p95, "
            "SUM(waiting_at_capacity_duration_ms)/NULLIF(SUM(total_duration_ms),0) queued, "
            "SUM(spilled_local_bytes)/1e9 spill_gb, "
            "SUM(read_bytes) bytes_read, "
            "approx_count_distinct(executed_by) users, "
            f"SUM(total_duration_ms)/3600000.0 * {DBU_HOUR_PROXY_USD} cost_proxy "
            "FROM system.query.history "
            f"WHERE start_time>=dateadd(day,-{days},current_date()) "
            "AND start_time < current_date() "  # exclude the incomplete current day
            "AND compute.warehouse_id IS NOT NULL "
            "GROUP BY compute.warehouse_id "
            "ORDER BY cost_proxy DESC LIMIT 100",
            "system.query.history (warehouses)",
        )
    except live.LiveError as e:
        return {"warehouses": [], "summary": summarize([]),
                "message": f"Warehouse metrics need SELECT on system.query.history — {e.detail}"}

    wh_names = live._warehouse_names(warehouse_id)
    wh_meta = live._warehouse_meta(warehouse_id)
    ws_names = live._workspace_names(warehouse_id)
    try:
        bu_by_ws = live._workspace_bu(warehouse_id)   # workspace -> {"bu","owner"}; {} if unmapped
    except Exception:  # noqa: BLE001
        bu_by_ws = {}
    # Per-warehouse hourly timeline (best-effort; degrade to []). Averaged per
    # day so the heatmap shows a typical day, matching the demo's single-day shape.
    timelines = _live_timelines(warehouse_id, days)
    top_users = _live_top_users(warehouse_id, days)
    top_sources = _live_top_sources(warehouse_id, days)
    peak_conc = _live_peak_concurrency(warehouse_id, days)
    out: list[dict[str, Any]] = []
    for r in rows:
        wh = str(r.get("wh") or "")
        ws = str(r.get("ws") or "")
        meta = wh_meta.get(wh, {})
        row = {
            "warehouse": wh_names.get(wh, wh), "warehouse_id": wh,
            "workspace": ws_names.get(ws, ws), "workspace_id": ws,
            "bu": bu_by_ws.get(ws, {}).get("bu") or "",
            "type": meta.get("type", ""), "size": meta.get("size", ""),
            "auto_stop_mins": meta.get("auto_stop_mins", 0),
            "runs": int(live._f(r.get("runs"))),
            # 30-day-normalised run count for the monthly "idle" test in _classify
            # (the displayed `runs` stays the real windowed count).
            "runs_30d": int(round(live._f(r.get("runs")) * (30.0 / max(1, days)))),
            "p50_s": round(live._f(r.get("p50")), 1),
            "p95_s": round(live._f(r.get("p95")), 1),
            "queued_ratio": round(live._f(r.get("queued")), 3),
            "spill_gb": round(live._f(r.get("spill_gb")), 1),
            "bytes_read": int(live._f(r.get("bytes_read"))),
            "distinct_users": int(live._f(r.get("users"))),
            # Normalise the window-scoped proxy to a 30-DAY run-rate so the field
            # is genuinely per-month at every range chip (was a raw 1/7/30-day
            # figure mislabelled "_month", which also broke the monthly IDLE_COST
            # threshold in _classify at the 24h/7d windows).
            "cost_usd_month": round(live._f(r.get("cost_proxy")) * (30.0 / max(1, days)), 2),
            "peak_concurrency": peak_conc.get(wh, 0),
            "timeline": timelines.get(wh, []),
            "top_users": top_users.get(wh, []),
            "top_sources": top_sources.get(wh, []),
        }
        row["reasons"] = _classify(row)
        row["advice"] = advise(row)
        out.append(row)
    total_active = _active_warehouse_count(warehouse_id, days)
    return {"warehouses": out, "summary": summarize(out),
            "total_active": total_active, "shown": len(out),
            "truncated": bool(total_active and total_active > len(out)),
            "message": ""}


def _active_warehouse_count(warehouse_id: str, days: int) -> int:
    """Distinct warehouses with any query activity in the window — the TRUE
    universe behind the top-100-by-cost list. Lets the UI say "top 100 of N"
    instead of implying the shown 100 are the whole estate. Best-effort → 0."""
    from data import live
    try:
        r = live._run(warehouse_id,
            "SELECT approx_count_distinct(compute.warehouse_id) n FROM system.query.history "
            f"WHERE start_time>=dateadd(day,-{days},current_date()) AND start_time<current_date() "
            "AND compute.warehouse_id IS NOT NULL", "warehouse universe count")
        return int(live._f(r[0].get("n"))) if r else 0
    except Exception:  # noqa: BLE001 — best-effort; 0 ⇒ UI just omits the note
        return 0


def warehouses_from_daily(warehouse_id: str, gold_schema: str, time_range: str = "30d") -> dict[str, Any] | None:
    """Re-window the accumulated daily table (<gold>.finops_gold_warehouse_daily)
    over the selected window — additive facts SUM, ratios divide summed
    numerator/denominator (never average pre-divided ratios), and p50/p95 come
    live for the same window (exact — percentiles can't be re-aggregated). The
    daily grain also lets a window look back further than query.history retains
    IF a range beyond retention is requested; the UI chips currently top out at
    30d (see _RANGE_DAYS), so today this mainly serves those windows from the
    nightly gold accumulator instead of a live scan.

    Returns None (caller falls back to warehouses_live) when the accumulator
    isn't available yet, so the tab still works before the first gold run."""
    from data import live

    if not live._SCHEMA_RE.match(gold_schema or ""):
        return None
    days = _RANGE_DAYS.get(time_range, 30)
    fqn = f"{gold_schema}.finops_gold_warehouse_daily"
    try:
        rows = live._run(
            warehouse_id,
            "SELECT warehouse_id wh, any_value(workspace_id) ws, SUM(runs) runs, "
            "SUM(duration_ms) duration_ms, SUM(queued_ms) queued_ms, "
            "SUM(spilled_bytes) spilled_bytes, SUM(read_bytes) read_bytes, "
            "MAX(distinct_users) users, SUM(cost_proxy) cost_proxy "
            f"FROM {fqn} WHERE usage_date >= dateadd(day,-{days},current_date()) "
            "GROUP BY warehouse_id ORDER BY cost_proxy DESC LIMIT 100",
            "gold warehouse_daily (re-window)",
        )
    except Exception:  # noqa: BLE001 — accumulator absent → caller uses live
        return None
    if not rows:
        return None

    # Exact p50/p95 for the window, live (percentiles don't re-aggregate). Bounded
    # by query.history retention: a window beyond it yields 0.0 percentiles (which
    # would suppress the "slow" flag) — not reachable via the ≤30d UI chips today.
    pctl = _live_percentiles(warehouse_id, days)
    wh_names = live._warehouse_names(warehouse_id)
    wh_meta = live._warehouse_meta(warehouse_id)
    ws_names = live._workspace_names(warehouse_id)
    try:
        bu_by_ws = live._workspace_bu(warehouse_id)   # workspace -> {"bu","owner"}; {} if unmapped
    except Exception:  # noqa: BLE001
        bu_by_ws = {}
    timelines = _live_timelines(warehouse_id, days)
    top_users = _live_top_users(warehouse_id, days)
    top_sources = _live_top_sources(warehouse_id, days)
    peak_conc = _live_peak_concurrency(warehouse_id, days)
    out: list[dict[str, Any]] = []
    for r in rows:
        wh = str(r.get("wh") or "")
        ws = str(r.get("ws") or "")
        dur = live._f(r.get("duration_ms"))
        p = pctl.get(wh, {})
        meta = wh_meta.get(wh, {})
        row = {
            "warehouse": wh_names.get(wh, wh), "warehouse_id": wh,
            "workspace": ws_names.get(ws, ws), "workspace_id": ws,
            "bu": bu_by_ws.get(ws, {}).get("bu") or "",
            "type": meta.get("type", ""), "size": meta.get("size", ""),
            "auto_stop_mins": meta.get("auto_stop_mins", 0),
            "runs": int(live._f(r.get("runs"))),
            "runs_30d": int(round(live._f(r.get("runs")) * (30.0 / max(1, days)))),
            "p50_s": round(p.get("p50", 0.0), 1),
            "p95_s": round(p.get("p95", 0.0), 1),
            # ratio = summed numerator / summed denominator (correct re-windowing)
            "queued_ratio": round(live._f(r.get("queued_ms")) / dur, 3) if dur else 0.0,
            "spill_gb": round(live._f(r.get("spilled_bytes")) / 1e9, 1),
            "bytes_read": int(live._f(r.get("read_bytes"))),
            "distinct_users": int(live._f(r.get("users"))),
            # Normalise summed-window proxy to a 30-day run-rate (see warehouses_live).
            "cost_usd_month": round(live._f(r.get("cost_proxy")) * (30.0 / max(1, days)), 2),
            "peak_concurrency": peak_conc.get(wh, 0),
            "timeline": timelines.get(wh, []),
            "top_users": top_users.get(wh, []), "top_sources": top_sources.get(wh, []),
        }
        row["reasons"] = _classify(row)
        row["advice"] = advise(row)
        out.append(row)
    total_active = _active_warehouse_count(warehouse_id, days)
    return {"warehouses": out, "summary": summarize(out),
            "total_active": total_active, "shown": len(out),
            "truncated": bool(total_active and total_active > len(out)),
            "message": ""}


def _live_percentiles(warehouse_id: str, days: int) -> dict[str, dict[str, float]]:
    """Exact p50/p95 (seconds) per warehouse over the window, live from
    query.history. Best-effort → {} (rows then show 0s for the percentile cols)."""
    from data import live

    try:
        rows = live._run(
            warehouse_id,
            "SELECT compute.warehouse_id wh, "
            "approx_percentile(execution_duration_ms,0.5)/1000 p50, "
            "approx_percentile(execution_duration_ms,0.95)/1000 p95 "
            "FROM system.query.history "
            f"WHERE start_time>=dateadd(day,-{max(1, days)},current_date()) "
            "AND start_time < current_date() "  # exclude the incomplete current day
            "AND compute.warehouse_id IS NOT NULL GROUP BY compute.warehouse_id",
            "query.history (window percentiles)",
        )
    except live.LiveError:
        return {}
    return {str(r.get("wh") or ""): {"p50": live._f(r.get("p50")), "p95": live._f(r.get("p95"))}
            for r in rows}


def _live_peak_concurrency(warehouse_id: str, days: int = 30) -> dict[str, int]:
    """Per-warehouse peak concurrency ≈ the max number of queries running in any
    one-minute bucket, from system.query.history. Approximated with a single
    grouped scan (queries counted in their start-minute bucket) rather than a
    full sweep-line over start/end events — the sweep-line version scanned the
    table twice with an unbounded window over every event across the whole
    estate and timed the endpoint out. A real measured fact (was hardcoded 0).
    Best-effort → {} on failure (the drill-down tile then shows 0 honestly)."""
    from data import live

    days = max(1, days)
    try:
        rows = live._run(
            warehouse_id,
            "SELECT wh, MAX(c) peak FROM ("
            "  SELECT compute.warehouse_id wh, date_trunc('MINUTE', start_time) m, COUNT(*) c "
            "  FROM system.query.history "
            f"  WHERE start_time>=dateadd(day,-{days},current_date()) AND start_time < current_date() "
            "    AND compute.warehouse_id IS NOT NULL "
            "  GROUP BY compute.warehouse_id, date_trunc('MINUTE', start_time)"
            ") GROUP BY wh",
            "system.query.history (peak concurrency)",
        )
    except live.LiveError:
        return {}
    return {str(r.get("wh") or ""): int(live._f(r.get("peak"))) for r in rows}


def _live_timelines(warehouse_id: str, days: int = 7) -> dict[str, list[int]]:
    """Per-warehouse 24-bucket hour-of-day query-volume histogram, averaged per
    day over the window so it reads as a *typical day* (matching the demo's
    single-day shape). Best-effort — returns {} on failure (the drill-down
    heatmap just shows no data)."""
    from data import live

    days = max(1, days)
    try:
        rows = live._run(
            warehouse_id,
            "SELECT compute.warehouse_id wh, hour(start_time) hr, COUNT(*) c "
            "FROM system.query.history "
            f"WHERE start_time>=dateadd(day,-{days},current_date()) "
            "AND start_time < current_date() "  # exclude incomplete current day
            "AND compute.warehouse_id IS NOT NULL "
            "GROUP BY compute.warehouse_id, hour(start_time)",
            "system.query.history (timeline)",
        )
    except live.LiveError:
        return {}
    tl: dict[str, list[int]] = {}
    for r in rows:
        wh = str(r.get("wh") or "")
        hr = int(live._f(r.get("hr")))
        if wh not in tl:
            tl[wh] = [0] * 24
        if 0 <= hr < 24:
            tl[wh][hr] = round(live._f(r.get("c")) / days)  # per-day average
    return tl


def _live_top_users(warehouse_id: str, days: int = 30, per_wh: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Top users by run count per warehouse (drill-down). Best-effort → {}."""
    from data import live

    try:
        rows = live._run(
            warehouse_id,
            "SELECT wh, usr, runs FROM (SELECT compute.warehouse_id wh, executed_by usr, COUNT(*) runs, "
            "ROW_NUMBER() OVER (PARTITION BY compute.warehouse_id ORDER BY COUNT(*) DESC) rn "
            "FROM system.query.history "
            f"WHERE start_time>=dateadd(day,-{max(1, days)},current_date()) AND start_time < current_date() "
            "AND compute.warehouse_id IS NOT NULL AND executed_by IS NOT NULL "
            "GROUP BY compute.warehouse_id, executed_by) "
            f"WHERE rn <= {per_wh}",
            "system.query.history (top users)",
        )
    except live.LiveError:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        wh = str(r.get("wh") or "")
        out.setdefault(wh, []).append({"user": str(r.get("usr") or ""), "runs": int(live._f(r.get("runs")))})
    return out


def _live_top_sources(warehouse_id: str, days: int = 30, per_wh: int = 5) -> dict[str, list[dict[str, Any]]]:
    """Top query sources per warehouse from query_source (job/dashboard/notebook/
    alert/…), else statement_type. Best-effort → {}."""
    from data import live

    try:
        rows = live._run(
            warehouse_id,
            "SELECT wh, src, runs, wh_total FROM (SELECT compute.warehouse_id wh, "
            "CASE WHEN query_source.job_info.job_id IS NOT NULL THEN 'Job / scheduled' "
            "     WHEN query_source.legacy_dashboard_id IS NOT NULL OR query_source.dashboard_id IS NOT NULL THEN 'Dashboard' "
            "     WHEN query_source.alert_id IS NOT NULL THEN 'Alert' "
            "     WHEN query_source.notebook_id IS NOT NULL THEN 'Notebook' "
            "     WHEN query_source.sql_query_id IS NOT NULL THEN 'SQL editor' "
            "     ELSE COALESCE(client_application, 'Other') END src, "
            "COUNT(*) runs, "
            # Denominator = ALL sources for the warehouse (not just the top-5), so
            # pct is a true share-of-all — matches demo. Without this, dividing by
            # the top-5 sum overstated each source's share (summed to ~1.0).
            "SUM(COUNT(*)) OVER (PARTITION BY compute.warehouse_id) wh_total, "
            "ROW_NUMBER() OVER (PARTITION BY compute.warehouse_id ORDER BY COUNT(*) DESC) rn "
            "FROM system.query.history "
            f"WHERE start_time>=dateadd(day,-{max(1, days)},current_date()) AND start_time < current_date() "
            "AND compute.warehouse_id IS NOT NULL "
            "GROUP BY 1, 2) "
            f"WHERE rn <= {per_wh}",
            "system.query.history (top sources)",
        )
    except live.LiveError:
        # query_source struct may not exist in every metastore → fall back to none.
        return {}
    by_wh: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        wh = str(r.get("wh") or "")
        tot = live._f(r.get("wh_total")) or 1
        by_wh.setdefault(wh, []).append({
            "source": str(r.get("src") or "Other"),
            "runs": int(live._f(r.get("runs"))),
            "pct": round(live._f(r.get("runs")) / tot, 3),  # share of ALL runs
        })
    return by_wh


if __name__ == "__main__":
    import json

    rows = warehouses_demo()
    print("=== warehouses demo self-check ===")
    print(f"warehouses: {len(rows)}")
    print(json.dumps(summarize(rows), indent=2))
    print("sample:", json.dumps({k: v for k, v in rows[0].items() if k not in ("timeline", "top_users", "top_sources")}, indent=2))
    assert all("reasons" in r for r in rows)
    assert all(len(r["timeline"]) == 24 for r in rows)
    # Every row carries actionable drill-down advice, and a flagged warehouse
    # never advises "healthy" (would contradict its own reason chips).
    assert all(r.get("advice") and r["advice"]["next_steps"] for r in rows)
    assert all(
        not (r["reasons"] and r["advice"]["recommendation"] == "healthy") for r in rows
    ), "a flagged warehouse must not be advised healthy"
    print("advice sample:", json.dumps(rows[0]["advice"], indent=2))
    print("self-check OK")
