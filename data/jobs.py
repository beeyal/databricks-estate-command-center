"""Jobs tab — job cost & health (observable facts only).

This backs the ``/api/jobs`` endpoint. Like the other always-on cost surfaces
(genie-cost, ai-cost, adoption, classification) it ships BOTH:

  * ``jobs_demo()``          — a deterministic, Acme-shaped seeded dataset so the
                               tab demos offline with USE_LIVE=false.
  * ``jobs_live(warehouse)`` — the real system-table reader (USE_LIVE=true).

Customer feedback that shaped this tab: a platform team running 50,000+ jobs on
on-demand (classic) clusters with poor visibility. The four things they need to
see, per job — ALL of which are measured facts from the system tables, not
model estimates:

  1. Biggest-cost jobs — list-price cost + duration trend, compute type,
     streaming / DLT flagged.
  2. Failing jobs / rising failure rate (incl. long-running streaming jobs).
  3. Run-time spikes vs a job's own baseline (median).
  4. Jobs growing in duration / cost over time.

Plus, per job, the observed setup+queue share of wall-clock — a factual signal
(how much of each run is spent starting up / waiting for compute rather than
executing), surfaced WITHOUT any migration recommendation or savings claim.

DELIBERATELY NOT CLAIMED (2026-07-20, after field + docs research): this tab does
NOT flag "serverless-migration candidates" and does NOT estimate serverless
savings. Databricks has no official serverless-candidate advisor or savings
calculator; serverless jobs DBUs list ~2x classic and internal data shows the
majority of classic→serverless migrations INCREASE net spend. Savings can only
be established by an A/B run of the specific workload, so we present the facts
and let the SA reason about them rather than printing a number we can't stand
behind. See go/serverless-migration-planner (internal, "do not use in front of
customers") for the sanctioned estimation path.

Everything demo is clearly labelled ``demo=True`` upstream (app._wrap); we never
present seeded numbers as real. Any $ figure is measured list-price cost.

--- Live system-table facts this mirrors (see jobs_live) ---
  * system.lakeflow.job_run_timeline: one row per run state-period; the terminal
    row has result_state IS NOT NULL. Native duration columns
    (setup/queue/execution/cleanup/run _duration_seconds) are populated on/after
    ~Dec 2025; older rows are NULL (we COALESCE to 0 — a proxy via
    job_task_run_timeline first-task start is possible but omitted here).
    warmup_wait = setup + queue ; setup_queue_ratio = warmup_wait / run_duration
    (a factual share of wall-clock, not a recommendation). Take the terminal row
    per run via QUALIFY ROW_NUMBER() ... = 1.
  * Failing: result_state IN ('FAILED','TIMED_OUT','CANCELED'); 7d vs 30d fail
    rate for "rising".
  * Spike: run duration vs median(dur) per job; ratio > ~2 in the last 3 days.
  * Growth: regr_slope(duration, day) per job over the trailing 30 days.
  * Cost: system.billing.usage joined on workspace_id + usage_metadata.job_id,
    priced via system.billing.list_prices. usage_metadata.job_id is populated
    ONLY for jobs-compute + serverless — all-purpose-cluster jobs have NO
    job-attributed billing (they'd need cluster_id attribution).
  * Compute type: billing_origin_product ('JOBS'/'ALL_PURPOSE'/'DLT') +
    product_features.is_serverless. Streaming: trigger_type='CONTINUOUS'.
    DLT: usage_metadata.dlt_pipeline_id IS NOT NULL.
"""
from __future__ import annotations

import hashlib
import random
import re
import statistics
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

SEED = 4217


def _stable_hash(s: str) -> int:
    """Process-stable hash (builtin hash on str is PYTHONHASHSEED-salted)."""
    return int.from_bytes(hashlib.md5(s.encode()).digest()[:6], "big")


def _now() -> datetime:
    return datetime(2026, 7, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Classification thresholds — shared by demo + live so both label identically.
# ---------------------------------------------------------------------------
# A HIGH setup+queue share is surfaced as a neutral fact (chip), NOT a migration
# recommendation — see module docstring on why we don't call these "candidates".
HIGH_SETUP_QUEUE_RATIO = 0.35   # ≥35% of wall-clock spent on setup + queue → flag as a fact
# Below this average run duration the setup/queue ratio is unreliable: setup/
# queue/cleanup are integer-SECOND columns, so on sub-second runs a 1s rounding
# floor dominates the (precise, ms) duration and inflates the ratio. Don't flag
# high-setup-queue for jobs whose average run is shorter than this.
SETUP_QUEUE_MIN_DUR_S = 5.0
FAILING_RATE = 0.15         # ≥15% of runs failed in 30d
RISING_FAIL_DELTA = 0.05    # 7d fail-rate at least this much above 30d → "rising"
SPIKE_RATIO = 2.0           # recent (3d) avg duration ≥2× the job's median → spike
GROWTH_PCT = 0.15           # ≥15% duration growth over the trend window → growing
# Minimum 30-day run count before the STATISTICAL trend signals (spiking / growing)
# may fire. A spike (recent-vs-median) or a regression trend needs enough runs to be
# real, not noise — a 2-run job can't be "growing". Also keeps the badge consistent
# with the duration sparkline, which needs several days of points to draw.
MIN_RUNS_FOR_TREND = 10

# Compute-type labels (mirror billing_origin_product + is_serverless).
COMPUTE_ALL_PURPOSE = "All-Purpose"   # on-demand interactive cluster; NO job-attributed billing live
COMPUTE_JOBS_CLASSIC = "Jobs (classic)"  # classic job cluster
COMPUTE_SERVERLESS = "Serverless"
COMPUTE_DLT = "DLT"
COMPUTE_UNKNOWN = "Unattributed"      # no billing row joined — type genuinely unknown (don't guess)
_CLASSIC = {COMPUTE_ALL_PURPOSE, COMPUTE_JOBS_CLASSIC, COMPUTE_DLT}  # DLT here = classic DLT


# ---------------------------------------------------------------------------
# Row assembly + classification (pure — used by demo AND live)
# ---------------------------------------------------------------------------

def _why(compute_type: str, setup_queue_ratio: float, fail_30d: float, fail_7d: float,
         spike_ratio: float, growth_pct: float, runs: int = 999) -> list[str]:
    """The "why" chips for a job row: spiking / growing / high-setup-queue /
    failing. Each is an observed fact about the job. A single job can carry
    several.

    ``runs`` (30-day run count) gates the STATISTICAL signals: 'spiking' (recent
    avg vs median) and 'growing' (a 30-day regression trend) are meaningless on a
    handful of runs — a job with 2 runs can't have a trend or a spike, only noise.
    Require MIN_RUNS_FOR_TREND observations before either can fire, so the badge
    reflects a real pattern (and the duration sparkline it's shown next to actually
    has points to draw). Default 999 keeps existing callers/tests unaffected."""
    reasons: list[str] = []
    if compute_type in _CLASSIC and setup_queue_ratio >= HIGH_SETUP_QUEUE_RATIO:
        reasons.append("high-setup-queue")
    if fail_30d >= FAILING_RATE or (fail_7d - fail_30d) >= RISING_FAIL_DELTA:
        reasons.append("failing")
    if runs >= MIN_RUNS_FOR_TREND and spike_ratio >= SPIKE_RATIO:
        reasons.append("spiking")
    if runs >= MIN_RUNS_FOR_TREND and growth_pct >= GROWTH_PCT:
        reasons.append("growing")
    return reasons


def _add_run_deltas(recent: list[dict[str, Any]]) -> None:
    """Annotate each recent run (newest-first) with a run-to-run delta vs the
    NEXT-OLDER run: duration Δ%, and whether the result state flipped
    (e.g. SUCCEEDED→FAILED). Mutates in place; shared by demo + live so the
    drill-in shows 'this run vs the last' at a glance."""
    for i, r in enumerate(recent):
        prev = recent[i + 1] if i + 1 < len(recent) else None
        if prev and prev.get("duration_s"):
            d, prev_dur = float(r.get("duration_s") or 0), float(prev["duration_s"])
            r["duration_delta_pct"] = round((d - prev_dur) / prev_dur, 3) if prev_dur else 0.0
        else:
            r["duration_delta_pct"] = 0.0
        r["result_changed"] = bool(prev and r.get("result_state") != prev.get("result_state"))
        r["regressed"] = bool(prev and r.get("result_state") != "SUCCEEDED" and prev.get("result_state") == "SUCCEEDED")


def _build_row(*, job_id: str, workspace_id: str, workspace: str, bu: str,
               job_name: str, compute_type: str, runs: int, fails_30d: int,
               fails_7d: int, runs_7d: int, avg_warmup_s: float, avg_exec_s: float,
               avg_cleanup_s: float, median_dur_s: float, recent_dur_s: float,
               growth_pct: float, streaming: bool, dlt: bool, cost_usd_month: float,
               trend: list[float], detail: dict[str, Any]) -> dict[str, Any]:
    """Assemble one classified JobRow. warmup_wait = setup + queue;
    setup_queue_ratio = warmup_wait / run_duration (a factual share). Both demo +
    live funnel through here so the shape and the labelling rules are identical."""
    avg_warmup_s = max(0.0, avg_warmup_s)
    avg_exec_s = max(0.0, avg_exec_s)
    avg_dur_s = avg_warmup_s + avg_exec_s + max(0.0, avg_cleanup_s)
    setup_queue_ratio = (avg_warmup_s / avg_dur_s) if avg_dur_s > 0 else 0.0
    fail_rate_30d = (fails_30d / runs) if runs else 0.0
    fail_rate_7d = (fails_7d / runs_7d) if runs_7d else 0.0
    spike_ratio = (recent_dur_s / median_dur_s) if median_dur_s > 0 else 1.0

    # setup_s/queue_s/cleanup_s come from INTEGER-second columns while avg_dur_s
    # is the precise ms delta. For a sub-second run, setup_duration_seconds can
    # round UP to 1s while the true duration is ~0.5s, pushing setup_queue_ratio
    # spuriously toward 1.0 and mis-flagging a fast, healthy classic job as
    # "high setup+queue". Only trust the ratio (and the derived flag/reason) when
    # the average run is materially longer than that rounding floor.
    ratio_reliable = avg_dur_s >= SETUP_QUEUE_MIN_DUR_S
    reasons = _why(compute_type, setup_queue_ratio if ratio_reliable else 0.0,
                   fail_rate_30d, fail_rate_7d, spike_ratio, growth_pct, runs=runs)
    high_setup_queue = ratio_reliable and compute_type in _CLASSIC and setup_queue_ratio >= HIGH_SETUP_QUEUE_RATIO

    # Cost per run — exact from monthly cost ÷ runs.
    cost_per_run = (cost_usd_month / runs) if runs else 0.0
    # Estimated $ spent on runs that failed this month. Approximates a failed run
    # as costing the job's average run cost (failed runs often die mid-execution,
    # so this is an UPPER-ish estimate) — labelled as an estimate in the UI.
    failed_run_cost_usd_month = cost_per_run * fails_30d
    # Month-over-month cost change, exact from the last two points of the 6-month
    # cost trend (built by the caller). 0 when the trend isn't available (live).
    ct = [p.get("cost_usd", 0.0) for p in detail.get("cost_trend", [])] if isinstance(detail, dict) else []
    if len(ct) >= 2 and ct[-2] > 0:
        mom_cost_delta_usd = ct[-1] - ct[-2]
        mom_cost_pct = mom_cost_delta_usd / ct[-2]
    else:
        mom_cost_delta_usd = 0.0
        mom_cost_pct = 0.0
    # Cost anomaly: this month's cost vs the MEDIAN of the prior months (its own
    # baseline). Flags a job that suddenly costs far more than it usually does —
    # more robust than MoM alone (a one-off prior spike won't mask it). Ratio ≥2×
    # baseline AND a material absolute jump (≥$200) = anomaly.
    prior = ct[:-1] if len(ct) >= 3 else []
    baseline = statistics.median(prior) if prior else 0.0
    cost_anomaly_ratio = (ct[-1] / baseline) if baseline > 0 else 0.0
    cost_anomaly = bool(baseline > 0 and cost_anomaly_ratio >= 2.0 and (ct[-1] - baseline) >= 200)
    if cost_anomaly and "cost-anomaly" not in reasons:
        reasons.append("cost-anomaly")

    return {
        "job_id": job_id,
        "workspace_id": workspace_id,
        "workspace": workspace,
        "bu": bu,
        "job_name": job_name,
        "compute_type": compute_type,
        "is_serverless": compute_type == COMPUTE_SERVERLESS,
        "streaming": bool(streaming),
        "dlt": bool(dlt),
        "runs": int(runs),
        "runs_7d": int(runs_7d),
        "fail_rate": round(fail_rate_30d, 3),
        "fail_rate_7d": round(fail_rate_7d, 3),
        "fail_rate_rising": (fail_rate_7d - fail_rate_30d) >= RISING_FAIL_DELTA,
        "avg_duration_s": round(avg_dur_s, 1),
        "setup_queue_ratio": round(setup_queue_ratio, 3),
        "high_setup_queue": high_setup_queue,
        "median_duration_s": round(median_dur_s, 1),
        "recent_duration_s": round(recent_dur_s, 1),
        "spike_ratio": round(spike_ratio, 2),
        "growth_pct": round(growth_pct, 3),
        "cost_usd_month": round(cost_usd_month, 2),
        "cost_per_run": round(cost_per_run, 2),
        "failed_run_cost_usd_month": round(failed_run_cost_usd_month, 2),
        "mom_cost_delta_usd": round(mom_cost_delta_usd, 2),
        "mom_cost_pct": round(mom_cost_pct, 3),
        "cost_anomaly": cost_anomaly,
        "cost_anomaly_ratio": round(cost_anomaly_ratio, 2),
        "reasons": reasons,
        "trend": [round(t, 1) for t in trend],
        "detail": detail,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """KPI + rollup summary reconciled to the given (already-filtered) rows —
    mirrors the /api/tables summary contract so numbers match the visible table."""
    num_jobs = len(rows)
    num_failing = sum(1 for r in rows if "failing" in r["reasons"])
    num_growing = sum(1 for r in rows if "growing" in r["reasons"])
    num_cost_anomalies = sum(1 for r in rows if r.get("cost_anomaly"))
    failed_run_cost = round(sum(r["failed_run_cost_usd_month"] for r in rows), 2)
    total_cost = round(sum(r["cost_usd_month"] for r in rows), 2)

    by_compute: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = by_compute.setdefault(r["compute_type"], {"compute_type": r["compute_type"], "num_jobs": 0, "cost_usd_month": 0.0})
        b["num_jobs"] += 1
        b["cost_usd_month"] += r["cost_usd_month"]
    by_compute_list = sorted(by_compute.values(), key=lambda x: -x["cost_usd_month"])
    for b in by_compute_list:
        b["cost_usd_month"] = round(b["cost_usd_month"], 2)

    return {
        "num_jobs": num_jobs,
        "num_failing": num_failing,
        "num_growing": num_growing,
        "num_streaming": sum(1 for r in rows if r["streaming"]),
        "num_dlt": sum(1 for r in rows if r["dlt"]),
        "num_spiking": sum(1 for r in rows if "spiking" in r["reasons"]),
        "num_cost_anomalies": num_cost_anomalies,
        "failed_run_cost_usd_month": failed_run_cost,
        "total_cost_usd_month": total_cost,
        "by_compute": by_compute_list,
    }


# ---------------------------------------------------------------------------
# Demo generator
# ---------------------------------------------------------------------------
#
# ~50 Acme jobs across the demo workspaces, guaranteeing a mix that exercises
# every archetype the tab is built to surface: high setup+queue-share jobs,
# failing / rising-failure jobs, long-running streaming failures, run-time
# spikes, jobs growing over time, big-cost jobs, and healthy baseline jobs
# (both serverless and classic).

_ARCHETYPES = (
    ["high_setup_queue"] * 9
    + ["high_wait"] * 4
    + ["failing"] * 6
    + ["streaming_failing"] * 2
    + ["spiking"] * 5
    + ["growing"] * 5
    + ["big_cost"] * 5
    + ["healthy_serverless"] * 6
    + ["dlt"] * 4
    + ["healthy_classic"] * 6
)  # 52 jobs

_JOB_STUBS = [
    "nem12-ingest", "settlement-etl", "scada-bronze-loader", "gold-daily-refresh",
    "meter-reads-compaction", "gas-nominations-sync", "market-settlement-calc",
    "asset-telemetry-rollup", "customer-invoice-gen", "network-events-stream",
    "compressor-runtime-agg", "emissions-ledger-daily", "trade-position-mark",
    "outage-register-sync", "sensor-raw-ingest", "curtailment-detector",
    "forecast-ml-scoring", "fpna-actuals-load", "esg-report-build", "bi-extract-refresh",
    "pipeline-flow-hourly", "silver-meter-clean", "billing-line-recon", "nem13-backfill",
    "dr-event-replay", "tariff-calc-batch", "hedge-position-eod", "grid-topology-sync",
    "smart-meter-dedup", "consumption-forecast", "anomaly-scan", "load-profile-agg",
    "wholesale-price-load", "asset-health-score", "leak-detection-ml", "carbon-intensity-calc",
    "customer-churn-score", "network-loss-calc", "settlement-reconcile", "meter-event-cdc",
    "gas-flow-balance", "storage-inventory-sync", "demand-response-opt", "voltage-quality-scan",
    "interval-data-validate", "portfolio-var-calc", "outage-notify-stream", "green-cert-tally",
    "renewable-mix-report", "tou-billing-run", "grid-frequency-monitor", "asset-depreciation-run",
]

_TERM_CODES = [
    "DRIVER_UNRESPONSIVE", "RESOURCE_EXHAUSTED", "USER_CANCELED", "MAX_CONCURRENT_RUNS_REACHED",
    "SPARK_ERROR", "CLOUD_PROVIDER_LAUNCH_FAILURE", "INTERNAL_ERROR", "TASK_TIMED_OUT",
]


def _metrics(archetype: str, rng: random.Random) -> dict[str, Any]:
    """Seeded raw metrics per archetype (all times in seconds). Returns the
    normalized inputs _build_row expects, keeping demo + live shapes aligned."""
    # defaults — a middling classic job
    compute_type = rng.choice([COMPUTE_JOBS_CLASSIC, COMPUTE_ALL_PURPOSE])
    warmup = rng.uniform(45, 110)
    exec_s = rng.uniform(180, 520)
    cleanup = rng.uniform(2, 12)
    runs = rng.randint(120, 900)
    fail_30 = rng.uniform(0.005, 0.03)
    fail_7 = fail_30 * rng.uniform(0.7, 1.3)
    spike = rng.uniform(1.0, 1.4)
    growth = rng.uniform(-0.05, 0.08)
    streaming = False
    dlt = False
    hourly = rng.uniform(9, 22)  # $/cluster-hour (multi-node classic)

    if archetype == "high_setup_queue":
        # short classic jobs dominated by cluster spin-up / queue — surfaced as a
        # fact (high setup+queue share), not a migration recommendation.
        compute_type = rng.choice([COMPUTE_ALL_PURPOSE, COMPUTE_JOBS_CLASSIC])
        warmup = rng.uniform(150, 360)
        exec_s = rng.uniform(60, 200)
        runs = rng.randint(600, 4200)   # frequent, small
        fail_30 = rng.uniform(0.005, 0.04)
        fail_7 = fail_30 * rng.uniform(0.8, 1.2)
        hourly = rng.uniform(10, 26)
    elif archetype == "high_wait":
        compute_type = rng.choice([COMPUTE_ALL_PURPOSE, COMPUTE_JOBS_CLASSIC])
        warmup = rng.uniform(120, 240)
        exec_s = rng.uniform(180, 320)
        runs = rng.randint(300, 1500)
    elif archetype == "failing":
        compute_type = rng.choice([COMPUTE_JOBS_CLASSIC, COMPUTE_SERVERLESS])
        warmup = rng.uniform(30, 140)
        exec_s = rng.uniform(240, 900)
        runs = rng.randint(150, 1200)
        fail_30 = rng.uniform(0.18, 0.42)
        fail_7 = min(0.9, fail_30 + rng.uniform(0.04, 0.16))   # rising
        hourly = rng.uniform(12, 30)
    elif archetype == "streaming_failing":
        compute_type = rng.choice([COMPUTE_JOBS_CLASSIC, COMPUTE_SERVERLESS])
        streaming = True
        warmup = rng.uniform(60, 160)
        exec_s = rng.uniform(9_000, 54_000)  # hours-long continuous run periods
        cleanup = rng.uniform(5, 30)
        runs = rng.randint(20, 120)
        fail_30 = rng.uniform(0.12, 0.30)
        fail_7 = min(0.9, fail_30 + rng.uniform(0.03, 0.14))
        hourly = rng.uniform(18, 44)
    elif archetype == "spiking":
        compute_type = rng.choice([COMPUTE_JOBS_CLASSIC, COMPUTE_ALL_PURPOSE, COMPUTE_SERVERLESS])
        warmup = rng.uniform(40, 160)
        exec_s = rng.uniform(300, 700)
        runs = rng.randint(200, 1400)
        spike = rng.uniform(2.4, 4.2)   # last-3-day avg well above median
        hourly = rng.uniform(11, 26)
    elif archetype == "growing":
        compute_type = rng.choice([COMPUTE_JOBS_CLASSIC, COMPUTE_ALL_PURPOSE, COMPUTE_SERVERLESS])
        warmup = rng.uniform(40, 150)
        exec_s = rng.uniform(280, 640)
        runs = rng.randint(250, 1600)
        growth = rng.uniform(0.20, 0.65)   # duration creeping up over the window
        hourly = rng.uniform(11, 28)
    elif archetype == "big_cost":
        compute_type = rng.choice([COMPUTE_JOBS_CLASSIC, COMPUTE_ALL_PURPOSE])
        warmup = rng.uniform(60, 220)
        exec_s = rng.uniform(1_200, 3_600)
        runs = rng.randint(400, 2600)
        hourly = rng.uniform(30, 70)       # big multi-node clusters
    elif archetype == "healthy_serverless":
        compute_type = COMPUTE_SERVERLESS
        warmup = rng.uniform(3, 18)        # warm-pool / perf-optimized serverless start (seconds)
        exec_s = rng.uniform(120, 640)
        runs = rng.randint(200, 1800)
        fail_30 = rng.uniform(0.002, 0.02)
        fail_7 = fail_30 * rng.uniform(0.6, 1.1)
        hourly = rng.uniform(8, 20)
    elif archetype == "dlt":
        dlt = True
        compute_type = rng.choice([COMPUTE_DLT, COMPUTE_SERVERLESS])
        if compute_type == COMPUTE_SERVERLESS:
            warmup = rng.uniform(4, 20)
        else:
            warmup = rng.uniform(80, 240)
        exec_s = rng.uniform(400, 1800)
        runs = rng.randint(120, 900)
        hourly = rng.uniform(14, 34)
    elif archetype == "healthy_classic":
        compute_type = rng.choice([COMPUTE_JOBS_CLASSIC, COMPUTE_ALL_PURPOSE])
        warmup = rng.uniform(30, 90)
        exec_s = rng.uniform(400, 1100)    # long enough that warm-up is a small share
        runs = rng.randint(120, 700)
        hourly = rng.uniform(10, 24)

    avg_dur = warmup + exec_s + cleanup
    median_dur = avg_dur / max(1.0, spike) if spike > 1.0 else avg_dur * rng.uniform(0.9, 1.02)
    recent_dur = median_dur * spike
    cost = (runs * (avg_dur / 3600.0)) * hourly
    return {
        "compute_type": compute_type, "warmup": warmup, "exec_s": exec_s, "cleanup": cleanup,
        "runs": runs, "fail_30": fail_30, "fail_7": fail_7, "median_dur": median_dur,
        "recent_dur": recent_dur, "growth": growth, "streaming": streaming, "dlt": dlt,
        "cost": cost,
    }


def _detail(m: dict[str, Any], rng: random.Random, streaming: bool) -> dict[str, Any]:
    """Rich drill-in payload for the expandable row: warm-up vs exec split,
    12-week duration trend, 6-month cost trend, recent runs, termination codes."""
    warmup, exec_s = m["warmup"], m["exec_s"]
    runs, cost = m["runs"], m["cost"]
    growth, spike = m["growth"], m["recent_dur"] / max(1.0, m["median_dur"])
    fail_30 = m["fail_30"]

    # 12-week avg-duration trend: ramps with growth, jumps at the end for spikes.
    base = warmup + exec_s
    weeks = []
    start = _now() - timedelta(weeks=11)
    for w in range(12):
        drift = 1 + (growth * (w / 11.0)) + rng.uniform(-0.05, 0.05)
        if spike >= SPIKE_RATIO and w >= 10:
            drift *= 1 + (spike - 1) * (0.5 if w == 10 else 1.0)
        wk_runs = max(1, round((runs / 4.33) * rng.uniform(0.8, 1.2)))
        wk_fail = round(wk_runs * fail_30 * rng.uniform(0.6, 1.5))
        weeks.append({
            "week": (start + timedelta(weeks=w)).strftime("%Y-%m-%d"),
            "avg_s": round(base * drift, 1),
            "runs": wk_runs,
            "fails": min(wk_runs, wk_fail),
        })

    # 6-month cost trend (rising for growing/big jobs). ~1 in 6 jobs gets a
    # genuine COST ANOMALY: a sharp jump in the latest month vs a stable baseline
    # (so the anomaly detector + KPI have something real to surface in the demo).
    anomaly = rng.random() < 0.16
    months = []
    mstart = _now() - timedelta(days=30 * 5)
    for i in range(6):
        drift = 1 + (growth * (i / 5.0)) + rng.uniform(-0.06, 0.06)
        if anomaly and i == 5:
            drift *= rng.uniform(2.4, 4.0)   # latest-month spike vs its own baseline
        months.append({
            "month": (mstart + timedelta(days=30 * i)).strftime("%Y-%m"),
            "cost_usd": round(cost * drift, 2),
        })

    # A handful of recent runs (terminal rows).
    recent = []
    fail_states = ["FAILED", "TIMED_OUT", "CANCELED"]
    for i in range(6):
        failed = rng.random() < (m["fail_7"] if i < 3 else fail_30)
        w = warmup * rng.uniform(0.7, 1.4)
        e = exec_s * rng.uniform(0.6, (spike if (spike >= SPIKE_RATIO and i == 0) else 1.3))
        d = w + e
        recent.append({
            "run_id": str(6_600_000_000 + rng.randint(0, 9_999_999)),
            "date": (_now() - timedelta(hours=6 * i + rng.randint(0, 4))).strftime("%Y-%m-%d %H:%M"),
            "result_state": rng.choice(fail_states) if failed else "SUCCEEDED",
            "duration_s": round(d, 1),
            "warmup_s": round(w, 1),
            "wait_ratio": round(w / d, 3) if d else 0.0,
        })
    _add_run_deltas(recent)

    term_codes: list[dict[str, Any]] = []
    if fail_30 >= 0.05 or streaming:
        picks = rng.sample(_TERM_CODES, k=rng.randint(2, 3))
        for c in picks:
            term_codes.append({"code": c, "count": rng.randint(2, max(3, round(runs * fail_30 * 0.5)))})
        term_codes.sort(key=lambda x: -x["count"])

    return {
        "warmup_breakdown": {
            "setup_s": round(warmup * 0.62, 1),
            "queue_s": round(warmup * 0.38, 1),
            "exec_s": round(exec_s, 1),
            "cleanup_s": round(m["cleanup"], 1),
        },
        "duration_trend": weeks,
        "cost_trend": months,
        "recent_runs": recent,
        "termination_codes": term_codes,
    }


@lru_cache(maxsize=1)
def _job_records() -> list[dict[str, Any]]:
    from data import generate_demo as demo

    workspaces = demo.get_workspaces()
    rng = random.Random(SEED + 991)
    stubs = list(_JOB_STUBS)
    rng.shuffle(stubs)

    rows: list[dict[str, Any]] = []
    for i, archetype in enumerate(_ARCHETYPES):
        ws = workspaces[i % len(workspaces)]
        # seed each job deterministically off its identity so figures are stable.
        stub = stubs[i % len(stubs)]
        job_name = stub if i < len(stubs) else f"{stub}-{i // len(stubs) + 1}"
        jrng = random.Random(SEED + _stable_hash(f"{ws['workspace']}::{job_name}::{archetype}"))
        m = _metrics(archetype, jrng)

        # 12-week sparkline of avg duration (matches detail trend head-line shape).
        base = m["warmup"] + m["exec_s"]
        trend: list[float] = []
        for w in range(12):
            drift = 1 + (m["growth"] * (w / 11.0)) + jrng.uniform(-0.05, 0.05)
            if (m["recent_dur"] / max(1.0, m["median_dur"])) >= SPIKE_RATIO and w >= 10:
                drift *= 1.5 if w == 10 else 2.0
            trend.append(base * drift)

        runs = m["runs"]
        runs_7d = max(1, round(runs / 4.33))
        fails_30d = round(runs * m["fail_30"])
        fails_7d = round(runs_7d * m["fail_7"])

        job_id = str(4_100_000_000 + _stable_hash(f"{ws['workspace']}::{job_name}") % 89_000_000)
        detail = _detail(m, jrng, m["streaming"])
        detail["archetype"] = archetype  # internal aid; harmless to surface

        rows.append(_build_row(
            job_id=job_id, workspace_id=ws["workspace_id"], workspace=ws["workspace"], bu=ws["bu"],
            job_name=job_name, compute_type=m["compute_type"], runs=runs, fails_30d=fails_30d,
            fails_7d=fails_7d, runs_7d=runs_7d, avg_warmup_s=m["warmup"], avg_exec_s=m["exec_s"],
            avg_cleanup_s=m["cleanup"], median_dur_s=m["median_dur"], recent_dur_s=m["recent_dur"],
            growth_pct=m["growth"], streaming=m["streaming"], dlt=m["dlt"], cost_usd_month=m["cost"],
            trend=trend, detail=detail,
        ))

    rows.sort(key=lambda r: -r["cost_usd_month"])
    return rows


def jobs_demo() -> list[dict[str, Any]]:
    """Deterministic demo JobRow list (already classified + sorted by cost)."""
    return [dict(r) for r in _job_records()]


# ---------------------------------------------------------------------------
# Live reader (USE_LIVE=true) — FAIL-SOFT.
# ---------------------------------------------------------------------------
# Unlike the fail-CLOSED live readers in data/live.py, the Jobs tab must never
# crash: if a required table is ungranted we return a well-formed EMPTY payload
# with a clear ``message`` so the tab renders an explanation instead of a 503.

_RUN_METRICS_SQL = """
WITH terminal AS (
  SELECT
    workspace_id, job_id, run_id, run_name, trigger_type, result_state, termination_code,
    -- REAL total run duration in seconds from the millisecond timestamp delta.
    -- run_duration_seconds is a LONG (integer seconds) and reads 0 for sub-second
    -- runs and often for real runs, which zeroed the duration/warmup tiles for
    -- ~97% of jobs. The ms delta is precise and always populated.
    GREATEST(unix_millis(period_end_time) - unix_millis(period_start_time), 0) / 1000.0 AS dur_s,
    COALESCE(setup_duration_seconds, 0)   AS setup_s,
    COALESCE(queue_duration_seconds, 0)   AS queue_s,
    COALESCE(cleanup_duration_seconds, 0) AS cleanup_s,
    period_end_time
  FROM system.lakeflow.job_run_timeline
  WHERE result_state IS NOT NULL
    AND period_end_time >= dateadd(day, -30, current_timestamp())
  QUALIFY row_number() OVER (PARTITION BY workspace_id, job_id, run_id ORDER BY period_end_time DESC) = 1
), split AS (
  -- exec absorbs whatever the real duration isn't setup/queue/cleanup, so the
  -- split always sums to the real duration (exec-dominant when the sub-second
  -- setup/queue/cleanup columns are 0/unreported — honest, never a fake ratio).
  SELECT *,
    (setup_s + queue_s)                                          AS warmup_s,
    GREATEST(dur_s - setup_s - queue_s - cleanup_s, 0)           AS exec_s
  FROM terminal
)
SELECT
  CAST(workspace_id AS STRING)                                         AS workspace_id,
  CAST(job_id AS STRING)                                               AS job_id,
  MAX(run_name)                                                        AS run_name,
  MAX(trigger_type)                                                    AS trigger_type,
  COUNT(*)                                                             AS runs,
  SUM(CASE WHEN result_state IN ('FAILED','TIMED_OUT','CANCELED') THEN 1 ELSE 0 END) AS fails,
  SUM(CASE WHEN period_end_time >= dateadd(day,-7,current_timestamp()) THEN 1 ELSE 0 END) AS runs_7d,
  SUM(CASE WHEN period_end_time >= dateadd(day,-7,current_timestamp())
             AND result_state IN ('FAILED','TIMED_OUT','CANCELED') THEN 1 ELSE 0 END) AS fails_7d,
  AVG(warmup_s)                                                        AS avg_warmup,
  AVG(setup_s)                                                         AS avg_setup,
  AVG(queue_s)                                                         AS avg_queue,
  AVG(exec_s)                                                          AS avg_exec,
  AVG(cleanup_s)                                                       AS avg_cleanup,
  approx_percentile(dur_s, 0.5)                                       AS median_dur,
  AVG(CASE WHEN period_end_time >= dateadd(day,-3,current_timestamp()) THEN dur_s END) AS recent_dur,
  regr_slope(dur_s, unix_timestamp(period_end_time)/86400.0)          AS growth_slope,
  MAX(CASE WHEN trigger_type = 'CONTINUOUS' THEN 1 ELSE 0 END)         AS streaming
FROM split
GROUP BY 1, 2
HAVING COUNT(*) > 0
"""

_COST_SQL = """
SELECT
  CAST(u.workspace_id AS STRING)                             AS workspace_id,
  CAST(u.usage_metadata.job_id AS STRING)                    AS job_id,
  MAX(u.billing_origin_product)                              AS product,
  MAX(CASE WHEN u.product_features.is_serverless THEN 1 ELSE 0 END)          AS is_serverless,
  MAX(CASE WHEN u.usage_metadata.dlt_pipeline_id IS NOT NULL THEN 1 ELSE 0 END) AS is_dlt,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)  AS cost
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
-- Trailing 30 days (NOT month-to-date): matches the runs window in
-- _RUN_METRICS_SQL so cost_per_run = cost / runs divides comparable windows.
-- Month-to-date over 30d runs deflated cost_per_run ~15x at the start of a month.
WHERE u.usage_date >= dateadd(day, -30, current_date())
  AND u.usage_metadata.job_id IS NOT NULL
GROUP BY 1, 2
"""

# Per-job MONTHLY cost over the last 6 months — one aggregate query feeding the
# cost-trend + cost-anomaly detection in live mode (a job costing far more this
# month than its own baseline). Cheap: grouped, 6 months, no per-job scan.
_COST_TREND_SQL = """
SELECT
  CAST(u.workspace_id AS STRING)                             AS workspace_id,
  CAST(u.usage_metadata.job_id AS STRING)                    AS job_id,
  date_format(date_trunc('MONTH', u.usage_date), 'yyyy-MM')  AS month,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)  AS cost
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
-- Exclude the incomplete current calendar month (like every other trend query,
-- e.g. cost-drivers / ws-monthly), so the MoM + cost-anomaly comparison is
-- full-month vs full-month. Including the partial month showed a false MoM
-- decline early each month and suppressed anomalies.
WHERE u.usage_date >= add_months(date_trunc('MONTH', current_date()), -6)
  AND u.usage_date < date_trunc('MONTH', current_date())
  AND u.usage_metadata.job_id IS NOT NULL
GROUP BY 1, 2, 3
"""

# Per-job WEEKLY median run duration (last ~8 complete weeks) → the "Dur trend"
# sparkline. Duration is the MILLISECOND timestamp delta (period_end - start),
# The "Dur trend" sparkline plots the SAME series the spiking/growing signals are
# computed from — DAILY median run-duration over the last 30 days (the signal
# window: growing = regr_slope over 30d; spiking = last-3-day avg vs median). So a
# recent spike shows as the last points rising and a 30-day upward drift is visible
# — the chart always corroborates the badge. A daily grain over ALL ~36k jobs blows
# the 25 MB inline result limit, so this query is SCOPED to a job-id list (the
# attention-ranked top-N that the list actually ships) — {job_filter} is injected as
# `AND job_id IN (...)`. dur_ms (not run_duration_seconds — a LONG that's 0 for
# sub-second runs); median per day so one outlier run doesn't dominate a day's point.
_DURATION_TREND_SQL_TMPL = """
WITH terminal AS (
  SELECT workspace_id, job_id, run_id,
    MAX(period_end_time) AS ended,
    SUM(GREATEST(unix_millis(period_end_time) - unix_millis(period_start_time), 0)) AS dur_ms
  FROM system.lakeflow.job_run_timeline
  WHERE result_state IS NOT NULL
    AND period_end_time >= dateadd(day, -30, current_date())
    {job_filter}
  GROUP BY 1, 2, 3
)
SELECT
  CAST(workspace_id AS STRING)                          AS workspace_id,
  CAST(job_id AS STRING)                                AS job_id,
  date_format(date_trunc('DAY', ended), 'yyyy-MM-dd')   AS day,
  approx_percentile(dur_ms, 0.5)                        AS dur_ms
FROM terminal
GROUP BY 1, 2, 3
"""

# Fallback without the newer product_features.is_serverless / usage_metadata.
# dlt_pipeline_id sub-structs (absent in some metastores → hard schema error).
# Keeps cost + product so jobs still get $ and a JOBS/DLT/ALL_PURPOSE type; the
# serverless/dlt refinements just aren't available. Prevents the whole estate
# defaulting to one bogus compute type when those structs are missing.
_COST_SQL_NOFEATURES = """
SELECT
  CAST(u.workspace_id AS STRING)                             AS workspace_id,
  CAST(u.usage_metadata.job_id AS STRING)                    AS job_id,
  MAX(u.billing_origin_product)                              AS product,
  0                                                          AS is_serverless,
  0                                                          AS is_dlt,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)  AS cost
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
-- Trailing 30 days to match the runs window (see _COST_SQL).
WHERE u.usage_date >= dateadd(day, -30, current_date())
  AND u.usage_metadata.job_id IS NOT NULL
GROUP BY 1, 2
"""


def _compute_type(product: str, is_serverless: bool, is_dlt: bool, has_cost_row: bool = True) -> str:
    if is_serverless:
        return COMPUTE_SERVERLESS
    if is_dlt or product == "DLT":
        return COMPUTE_DLT
    if product == "JOBS":
        return COMPUTE_JOBS_CLASSIC
    if product == "ALL_PURPOSE":
        return COMPUTE_ALL_PURPOSE
    # No billing row joined (or an unrecognised product) → don't GUESS "All-Purpose"
    # (that's the "everything managed"-class mislabel). Mark it Unattributed so the
    # whole estate can't silently collapse into one bogus bucket when the cost
    # query fails or a job's billing hasn't landed yet.
    return COMPUTE_UNKNOWN if not has_cost_row else COMPUTE_ALL_PURPOSE


def _empty(message: str) -> dict[str, Any]:
    return {"jobs": [], "message": message}


def _metrics_failure_message(detail: str) -> str:
    """Honest reason the Jobs run-metrics read failed. The old message always
    blamed a missing grant — including when the real error was the 25 MiB inline
    result limit on a large estate, which sent customers re-running grants they
    already had."""
    d = (detail or "").lower()
    if "insufficient_permissions" in d or "permission_denied" in d or "does not have" in d:
        return (
            "Jobs tab needs read access to system.lakeflow.job_run_timeline. "
            "Ask an admin to grant the app: GRANT USE CATALOG ON CATALOG system TO `<app-service-principal>`; "
            "GRANT USE SCHEMA, SELECT ON SCHEMA system.lakeflow TO `<app-service-principal>`. "
            f"(live read failed — {detail})"
        )
    if "table_or_view_not_found" in d or "does not exist" in d:
        return ("Jobs tab couldn't find system.lakeflow.job_run_timeline — the lakeflow system schema "
                f"may not be enabled on this metastore. (live read failed — {detail})")
    if "still running after" in d:
        return ("Jobs tab timed out reading system.lakeflow.job_run_timeline — the estate is too large for an "
                "on-demand scan on this warehouse. Run the nightly gold sync job (it pre-computes this tab) "
                f"or use a larger SQL warehouse. (live read failed — {detail})")
    return f"Jobs tab couldn't read system.lakeflow.job_run_timeline. (live read failed — {detail})"


def jobs_live(warehouse_id: str) -> dict[str, Any]:
    """Real jobs from system.lakeflow.job_run_timeline (+ billing cost). FAIL-SOFT:
    returns {"jobs": [...], "message": ""} on success, or an empty list + an
    actionable message if a required table is ungranted — never raises, never
    crashes the tab.

    Returns row objects in the SAME shape as jobs_demo() so the frontend is
    identical in both modes (detail arrays are left empty in live for now)."""
    from data import live  # lazy — demo mode never needs the SDK

    # The five independent reads (run metrics, cost, cost trend, workspace names,
    # BU mapping) used to run one after another — each a full system-table scan
    # behind its own poll loop. They don't depend on each other, so run them
    # concurrently: the tab now costs roughly the slowest one, not the sum.
    from concurrent.futures import ThreadPoolExecutor

    def _cost_rows() -> list[dict[str, Any]]:
        try:
            return live._run(warehouse_id, _COST_SQL, "system.billing.usage (jobs cost)")
        except live.LiveError:
            return live._run(warehouse_id, _COST_SQL_NOFEATURES, "system.billing.usage (jobs cost, no features)")

    live.warm_auth()
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix="jobs-live") as pool:
        f_metrics = pool.submit(live._run, warehouse_id, _RUN_METRICS_SQL, "system.lakeflow.job_run_timeline")
        f_cost = pool.submit(_cost_rows)
        f_trend = pool.submit(live._run, warehouse_id, _COST_TREND_SQL, "system.billing.usage (jobs cost trend)")
        f_names = pool.submit(live._workspace_names, warehouse_id)
        f_bu = pool.submit(live._workspace_bu, warehouse_id)

        # 1) Run metrics (required). If this fails, the tab has nothing to show —
        #    return an empty, well-formed payload with an honest reason. Only a
        #    real permission error gets the "grant" advice; anything else (result
        #    size, timeout, warehouse stopped) says what actually happened.
        try:
            metric_rows = f_metrics.result()
        except Exception as e:  # noqa: BLE001 - fail soft
            return _empty(_metrics_failure_message(getattr(e, "detail", str(e))))

        # 2) Cost (best-effort). Missing billing grant just means $0 attribution —
        #    the run-health signals still render.
        cost_by_job: dict[tuple[str, str], dict[str, Any]] = {}
        try:
            for c in f_cost.result():
                cost_by_job[(str(c.get("workspace_id")), str(c.get("job_id")))] = {
                    "cost": live._f(c.get("cost")),
                    "product": str(c.get("product") or ""),
                    "is_serverless": bool(live._f(c.get("is_serverless"))),
                    "is_dlt": bool(live._f(c.get("is_dlt"))),
                }
        except Exception:  # noqa: BLE001 - cost is optional
            cost_by_job = {}

        # Per-job monthly cost trend (best-effort) → cost-anomaly detection live.
        trend_by_job: dict[tuple[str, str], list[dict[str, Any]]] = {}
        try:
            for c in f_trend.result():
                key = (str(c.get("workspace_id")), str(c.get("job_id")))
                trend_by_job.setdefault(key, []).append(
                    {"month": str(c.get("month") or ""), "cost_usd": round(live._f(c.get("cost")), 2)})
            for v in trend_by_job.values():
                v.sort(key=lambda x: x["month"])   # oldest→newest, as _build_row expects
        except Exception:  # noqa: BLE001 - trend is optional
            trend_by_job = {}

        # Friendly workspace names (fail-soft to id) so the UI shows name + id (#13),
        # and the BU per workspace from the customer mapping: a job inherits its
        # workspace's BU. {} when no mapping is configured (BU stays "—").
        try:
            ws_names = f_names.result()
        except Exception:  # noqa: BLE001
            ws_names = {}
        try:
            bu_by_ws = f_bu.result()
        except Exception:  # noqa: BLE001
            bu_by_ws = {}

    # The "Dur trend" sparkline is fetched AFTER row assembly + attention-ranking,
    # scoped to just the top-N job ids the list actually ships (see below) — a daily
    # grain over all ~36k jobs blows the inline result limit. Rows are built with an
    # empty trend here and back-filled once we know which jobs survive the cap.
    dur_trend_by_job: dict[tuple[str, str], list[float]] = {}
    dur_trend_objs_by_job: dict[tuple[str, str], list[dict[str, Any]]] = {}

    rows: list[dict[str, Any]] = []
    for r in metric_rows:
        ws = str(r.get("workspace_id") or "")
        jid = str(r.get("job_id") or "")
        c = cost_by_job.get((ws, jid), {})
        streaming = bool(live._f(r.get("streaming")))
        dlt = bool(c.get("is_dlt"))
        # has_cost_row=False when this job didn't join a billing row → Unattributed,
        # not a guessed "All-Purpose" (avoids the whole-estate mislabel if the cost
        # query failed or billing hasn't landed).
        compute_type = _compute_type(str(c.get("product") or ""), bool(c.get("is_serverless")), dlt,
                                     has_cost_row=bool(c))

        runs = int(live._f(r.get("runs")))
        runs_7d = int(live._f(r.get("runs_7d")))
        avg_warmup = live._f(r.get("avg_warmup"))
        avg_exec = live._f(r.get("avg_exec"))
        avg_cleanup = live._f(r.get("avg_cleanup"))
        median_dur = live._f(r.get("median_dur"))
        recent_dur = live._f(r.get("recent_dur")) or median_dur
        # growth_slope is Δseconds/day, fit by regr_slope over the SAME 30-day
        # window _RUN_METRICS_SQL scans — so project across 30 days, not 60
        # (×60 doubled the growth and over-flagged jobs as "growing").
        slope = live._f(r.get("growth_slope"))
        growth_pct = (slope * 30.0 / median_dur) if median_dur > 0 else 0.0
        cost = live._f(c.get("cost"))

        detail = {
            "warmup_breakdown": {
                # REAL setup / queue seconds from job_run_timeline (was a 62/38 guess).
                "setup_s": round(live._f(r.get("avg_setup")), 1),
                "queue_s": round(live._f(r.get("avg_queue")), 1),
                "exec_s": round(avg_exec, 1), "cleanup_s": round(avg_cleanup, 1),
            },
            # duration_trend + cost_trend both populated live now; recent_runs
            # stays deferred (per-run detail isn't bulk-scannable).
            "duration_trend": dur_trend_objs_by_job.get((ws, jid), []),
            "cost_trend": trend_by_job.get((ws, jid), []),
            "recent_runs": [], "termination_codes": [],
        }
        dur_trend = dur_trend_by_job.get((ws, jid), [])

        rows.append(_build_row(
            job_id=jid, workspace_id=ws, workspace=ws_names.get(ws, ws),
            bu=(bu_by_ws.get(ws, {}).get("bu") or "—"),
            job_name=str(r.get("run_name") or f"job-{jid}"), compute_type=compute_type,
            runs=runs, fails_30d=int(live._f(r.get("fails"))), fails_7d=int(live._f(r.get("fails_7d"))),
            runs_7d=runs_7d, avg_warmup_s=avg_warmup, avg_exec_s=avg_exec, avg_cleanup_s=avg_cleanup,
            median_dur_s=median_dur, recent_dur_s=recent_dur, growth_pct=growth_pct,
            streaming=streaming, dlt=dlt, cost_usd_month=cost, trend=dur_trend, detail=detail,
        ))

    rows.sort(key=lambda x: -x["cost_usd_month"])

    # DUR-TREND (scoped): fetch the DAILY duration series — the exact grain the
    # spiking/growing signals use, so the sparkline always corroborates the badge.
    # A daily grain over all ~36k jobs blows the 25 MB inline result limit, so we
    # fetch it only for the jobs that will actually SHIP — ranked by the SAME
    # attention order the app/UI uses to pick the visible rows: most watch-signals
    # first, then cost. (Ranking by cost alone missed the multi-signal-but-low-cost
    # jobs that sort to the TOP of the list — exactly the prominent rows the user
    # sees first — leaving them with an empty sparkline.) job_id list is validated
    # (digits only) before interpolation and the IN-list is hard-capped.
    _TREND_JOBS = 1500
    ranked = sorted(rows, key=lambda r: (-len(r.get("reasons") or []), -float(r.get("cost_usd_month") or 0)))
    trend_ids = [str(r["job_id"]) for r in ranked if _JOB_ID_RE.match(str(r["job_id"]))][:_TREND_JOBS]
    if trend_ids:
        id_list = ",".join(trend_ids)
        sql = _DURATION_TREND_SQL_TMPL.format(job_filter=f"AND CAST(job_id AS STRING) IN ({id_list})")
        try:
            tmp: dict[tuple[str, str], list[tuple[str, float]]] = {}
            for c in live._run(warehouse_id, sql, "job_run_timeline (duration trend, scoped)"):
                key = (str(c.get("workspace_id")), str(c.get("job_id")))
                tmp.setdefault(key, []).append((str(c.get("day") or ""), live._f(c.get("dur_ms")) / 1000.0))
            for key, pts in tmp.items():
                pts.sort(key=lambda x: x[0])             # oldest→newest
                dur_trend_by_job[key] = [round(v, 3) for _d, v in pts]
                dur_trend_objs_by_job[key] = [{"week": d, "avg_s": round(v, 3)} for d, v in pts]
        except Exception:  # noqa: BLE001 - trend is optional; leave empty on failure
            dur_trend_by_job = {}
            dur_trend_objs_by_job = {}
        # Back-fill the sparkline + drill-down trend onto the rows we fetched for.
        for r in rows:
            key = (str(r["workspace_id"]), str(r["job_id"]))
            if key in dur_trend_by_job:
                r["trend"] = dur_trend_by_job[key]
                d = r.get("detail")
                if isinstance(d, dict):
                    d["duration_trend"] = dur_trend_objs_by_job.get(key, [])

    # PAYLOAD TRIM (perf): the per-job duration/cost trend ARRAYS are only ever
    # rendered in the drill-down, but shipping them inline for every one of
    # ~29k jobs bloated the live payload to ~30 MB. The scalar MoM/anomaly signals
    # the LIST needs were already computed in _build_row, so we now drop the arrays
    # from the shipped detail and let the drill-down fetch them lazily per job
    # (job_detail_live). `has_trends` tells the UI whether history exists so it can
    # decide to fetch vs show "not enough history". warmup_breakdown stays (tiny,
    # feeds the detail stat tiles). Demo mode keeps arrays inline (small + no
    # per-job endpoint round-trip needed).
    for r in rows:
        d = r.get("detail")
        if isinstance(d, dict):
            d["has_trends"] = bool(d.get("duration_trend") or d.get("cost_trend"))
            d["duration_trend"] = []
            d["cost_trend"] = []
    return {"jobs": rows, "message": ""}


# ---------------------------------------------------------------------------
# Per-job drill-down (LIVE) — recent_runs + termination_codes for ONE job.
# The list endpoint deliberately leaves these empty (per-run detail isn't
# bulk-scannable across the whole estate), so they're fetched lazily on expand:
# a single-job filter on job_run_timeline is cheap. Fail-soft → empty lists.
# ---------------------------------------------------------------------------
_JOB_ID_RE = re.compile(r"^[0-9]{1,20}$")


def job_detail_live(warehouse_id: str, workspace_id: str, job_id: str, limit: int = 8) -> dict[str, Any]:
    """Recent terminal runs + top termination codes for one job. Returns
    {"recent_runs": [...], "termination_codes": [...]} — empty lists on any
    failure or bad id (the drill-down then shows an honest empty-state)."""
    from data import live

    ws = live._safe_ws_id(workspace_id)
    if not ws or not _JOB_ID_RE.match(str(job_id or "")):
        return {"recent_runs": [], "termination_codes": []}
    lim = max(1, min(int(limit), 25))
    try:
        rows = live._run(
            warehouse_id,
            "WITH terminal AS ("
            "  SELECT run_id, period_end_time, result_state, termination_code, "
            "    GREATEST(unix_millis(period_end_time)-unix_millis(period_start_time),0)/1000.0 dur_s, "
            "    COALESCE(setup_duration_seconds,0)+COALESCE(queue_duration_seconds,0) warmup_s "
            "  FROM system.lakeflow.job_run_timeline "
            f"  WHERE CAST(workspace_id AS STRING)='{ws}' AND CAST(job_id AS STRING)='{job_id}' "
            "    AND result_state IS NOT NULL "
            "  QUALIFY row_number() OVER (PARTITION BY run_id ORDER BY period_end_time DESC)=1"
            ") "
            f"SELECT * FROM terminal ORDER BY period_end_time DESC LIMIT {lim}",
            "job_run_timeline (recent runs)",
        )
    except live.LiveError:
        return {"recent_runs": [], "termination_codes": []}
    recent = []
    for r in rows:
        dur = live._f(r.get("dur_s")); warm = live._f(r.get("warmup_s"))
        recent.append({
            "run_id": str(r.get("run_id") or ""),
            "date": str(r.get("period_end_time") or "")[:16],
            "result_state": str(r.get("result_state") or ""),
            "duration_s": round(dur, 1), "warmup_s": round(warm, 1),
            "wait_ratio": round(warm / dur, 3) if dur else 0.0,
        })
    _add_run_deltas(recent)
    # Termination codes across the failed runs shown (a real distribution, not a
    # separate scan — keeps the drill-down to one query).
    codes: dict[str, int] = {}
    for r in rows:
        st = str(r.get("result_state") or "")
        code = str(r.get("termination_code") or "")
        if st != "SUCCEEDED" and code:
            codes[code] = codes.get(code, 0) + 1
    term = sorted(({"code": c, "count": n} for c, n in codes.items()), key=lambda x: -x["count"])

    # Per-job duration + cost trends — scoped to THIS job (cheap), so the
    # drill-down renders the same charts the list used to ship inline. The list
    # payload no longer carries these arrays (perf: ~30 MB → ~3 MB on a large
    # estate); they're fetched here on expand instead. Best-effort: a missing
    # grant / no history just yields an empty trend and the UI shows "not enough
    # history", never an error.
    duration_trend: list[dict[str, Any]] = []
    try:
        for c in live._run(
            warehouse_id,
            "WITH terminal AS ("
            "  SELECT run_id, MAX(period_end_time) AS ended, "
            "    SUM(GREATEST(unix_millis(period_end_time)-unix_millis(period_start_time),0)) AS dur_ms "
            "  FROM system.lakeflow.job_run_timeline "
            f"  WHERE CAST(workspace_id AS STRING)='{ws}' AND CAST(job_id AS STRING)='{job_id}' "
            "    AND result_state IS NOT NULL "
            "    AND period_end_time >= dateadd(day, -56, current_date()) "
            "    AND period_end_time <  date_trunc('WEEK', current_date()) "
            "  GROUP BY 1"
            ") "
            "SELECT date_format(date_trunc('WEEK', ended),'yyyy-MM-dd') AS week, "
            "  approx_percentile(dur_ms, 0.5) AS dur_ms "
            "FROM terminal GROUP BY 1 ORDER BY 1",
            "job_run_timeline (one-job duration trend)",
        ):
            duration_trend.append({"week": str(c.get("week") or ""), "avg_s": round(live._f(c.get("dur_ms")) / 1000.0, 3)})
    except live.LiveError:
        duration_trend = []

    cost_trend: list[dict[str, Any]] = []
    try:
        for c in live._run(
            warehouse_id,
            "SELECT date_format(date_trunc('MONTH', u.usage_date),'yyyy-MM') AS month, "
            "  SUM(u.usage_quantity * lp.pricing.effective_list.default) AS cost "
            "FROM system.billing.usage u "
            "LEFT JOIN system.billing.list_prices lp "
            "  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name "
            " AND u.usage_start_time >= lp.price_start_time "
            " AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time) "
            "WHERE u.usage_date >= add_months(date_trunc('MONTH', current_date()), -6) "
            "  AND u.usage_date < date_trunc('MONTH', current_date()) "
            f"  AND CAST(u.workspace_id AS STRING)='{ws}' AND CAST(u.usage_metadata.job_id AS STRING)='{job_id}' "
            "GROUP BY 1 ORDER BY 1",
            "billing.usage (one-job cost trend)",
        ):
            cost_trend.append({"month": str(c.get("month") or ""), "cost_usd": round(live._f(c.get("cost")), 2)})
    except live.LiveError:
        cost_trend = []

    return {
        "recent_runs": recent,
        "termination_codes": term,
        "duration_trend": duration_trend,
        "cost_trend": cost_trend,
    }


if __name__ == "__main__":
    import json

    rows = jobs_demo()
    s = summarize(rows)
    print("=== Jobs demo self-check ===")
    print(f"Jobs                : {s['num_jobs']}")
    print(f"Failing             : {s['num_failing']}")
    print(f"Growing (MoM)       : {s['num_growing']}")
    print(f"Streaming / DLT     : {s['num_streaming']} / {s['num_dlt']}")
    print(f"Failed-run $/mo     : ${s['failed_run_cost_usd_month']:,.0f}")
    print(f"Total job cost/mo   : ${s['total_cost_usd_month']:,.0f}")
    print("Top job:")
    print(json.dumps({k: rows[0][k] for k in ("job_name", "workspace", "compute_type", "runs",
          "cost_per_run", "failed_run_cost_usd_month", "mom_cost_pct", "cost_usd_month", "reasons")}, indent=2))
