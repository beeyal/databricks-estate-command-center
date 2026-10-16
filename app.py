"""FastAPI entry point for the Estate Command Center.

Serves the built React SPA from ``frontend/dist`` and exposes the demo /
system-table API under ``/api/*``. Designed for deployment as a Databricks App
(uvicorn on port 8000).

Data mode is controlled by the ``USE_LIVE`` env var (default ``false``):
- ``false`` (demo): serves the seeded dataset from ``data/generate_demo.py``.
  Every payload carries ``demo=True`` so the UI can label it clearly.
- ``true`` (live):  reads system tables via the Databricks SDK Statement
  Execution API (see ``data/live.py``), serving each estate page from the
  nightly gold tables (``gold_schema``) when configured, else computing it live.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from data import generate_demo as demo
from data.config import get_features, get_settings, bu_enabled, team_enabled

APP_ROOT = Path(__file__).parent
FRONTEND_DIST = APP_ROOT / "frontend" / "dist"

# Resolved deploy-time settings + feature flags (SPEC §10, §14). Env var >
# config.yaml > default — see data/config.py for the full precedence contract.
_SETTINGS = get_settings()
_FEATURES = get_features()

_USE_LIVE_REQUESTED = bool(_SETTINGS["use_live"])
WAREHOUSE_ID = str(_SETTINGS.get("warehouse_id") or "")
INGEST_SCHEMA = str(_SETTINGS.get("ingest_schema") or "")
# Gold-table serving schema (nightly finops_gold_sync writes here). MANDATORY in
# live mode: the app's reads over system.billing.usage / query.history / lakeflow
# are account-wide (every workspace in the account, tens of millions of rows), so
# running them live per page load is not a production-viable serving path. Gold
# pre-aggregates the whole estate ONCE nightly into small Delta tables the app
# reads in ~1s. The customer sets FINOPS_GOLD_SCHEMA, grants the app/job SP CREATE
# on the catalog (or pre-creates the schema + grants CREATE/MODIFY on it), and the
# nightly finops_gold_sync job creates + populates every table underneath.
GOLD_SCHEMA = str(_SETTINGS.get("gold_schema") or "")
# Escape hatch for constrained evals ONLY (read-only, no writable schema): set
# FINOPS_ALLOW_LIVE_NO_GOLD=true to run live straight off system tables. Every
# page then re-scans account-wide tables per request (slow); the two-tier cache
# softens repeats but a cold/restarted process is very slow. Not for production.
_ALLOW_LIVE_NO_GOLD = str(os.environ.get("FINOPS_ALLOW_LIVE_NO_GOLD") or "").lower() in ("1", "true", "yes")
# Live is the shipped default, but live mode needs a warehouse + a gold schema to
# serve at real scale (account-wide system tables → not viable per-request). If a
# deploy asks for live before those are set, DON'T hard-fail — fall back to demo so
# the app always boots and shows something, and surface WHY on /api/config so the UI
# shows a "finish setup to go live" banner. Setting FINOPS_ALLOW_LIVE_NO_GOLD=true
# runs live off raw system tables without gold (slow; read-only evals only).
_LIVE_SETUP_NOTE = ""
if _USE_LIVE_REQUESTED and not WAREHOUSE_ID:
    _LIVE_SETUP_NOTE = ("Live mode requested but no warehouse is set — showing demo data. "
                        "Set DATABRICKS_WAREHOUSE_ID (a serverless SQL warehouse) + "
                        "FINOPS_GOLD_SCHEMA in app.yaml, grant the app read access, and "
                        "run finops_gold_sync once to go live.")
elif _USE_LIVE_REQUESTED and not GOLD_SCHEMA and not _ALLOW_LIVE_NO_GOLD:
    _LIVE_SETUP_NOTE = ("Live mode requested but no gold schema is set — showing demo data. "
                        "Set FINOPS_GOLD_SCHEMA in app.yaml (the nightly finops_gold_sync job "
                        "pre-aggregates the account-wide system tables into fast serving "
                        "tables — running them live per request isn't viable at scale), grant "
                        "the app/job SP CREATE on the catalog, and unpause finops_gold_sync. "
                        "For a read-only eval with no writable schema, set "
                        "FINOPS_ALLOW_LIVE_NO_GOLD=true (slow; not for production).")
# Effective mode: live only when it can actually serve; otherwise demo (with the note).
USE_LIVE = bool(_USE_LIVE_REQUESTED and not _LIVE_SETUP_NOTE)
# Naming-convention grouping: sample rules in demo (match the seeded workspaces);
# live groups only when FINOPS_WORKSPACE_GROUPS is configured.
from data import grouping as _grouping  # noqa: E402
_grouping.set_demo_mode(not USE_LIVE)
# Dual-currency (SPEC §9): list prices are USD; AUD = USD × FX. Configurable so
# the rate can be pinned per deployment. Clearly labelled in the UI as an
# indicative conversion applied to list-price USD, not a billed AUD figure.
FINOPS_FX_AUD = float(_SETTINGS["fx_rate"])

SOURCE_PATH = str(_SETTINGS.get("source_path") or "")
JOB_RUN_AS = str(_SETTINGS.get("job_run_as") or "")

# In live mode, make the Recommendations hub aggregate real system-table data by
# installing the live provider into rec_hub. Failures still surface as 503 at
# request time (fail-closed); the provider memoises its queries per build.
if USE_LIVE and WAREHOUSE_ID:
    from data import rec_hub
    from data.rec_hub_live import LiveProvider

    rec_hub.set_provider(LiveProvider(WAREHOUSE_ID, GOLD_SCHEMA))


# ---------------------------------------------------------------------------
# Live payload cache — TWO TIERS. The heavy live endpoints (Warehouse Monitor,
# Cluster Efficiency, Governance, rec-hub) build their payload from several
# system.query.history / node_timeline scans per request and are NOT among the
# nightly gold snapshots, so an uncached endpoint re-runs every scan on every
# page load — 60-180s each time on a real estate.
#   Tier 1 (in-process): a fast dict, 1h TTL. Instant for repeat loads within
#     the hour, but LOST on every restart/redeploy.
#   Tier 2 (Lakebase, optional): a persistent key→JSON table (data/store.py) so
#     the payload SURVIVES a restart — the 60-180s cold build then happens ~once
#     total, not on every redeploy. Only active when the Lakebase writeback
#     backend is configured; otherwise it's a no-op and Tier 1 stands alone.
#   STALE-WHILE-REVALIDATE: past the 1h TTL (or on a cold process that finds a
#     persisted-but-stale entry), we SERVE THE STALE payload immediately and
#     rebuild in a background thread — so a user never waits on the cold build
#     once any snapshot exists. A single-flight guard stops duplicate rebuilds.
# Demo mode never uses this (its builders are in-process and instant).
# ---------------------------------------------------------------------------
_LIVE_PAYLOAD_TTL_SECONDS = 3600  # 1h — persistent Lakebase tier makes this cheap
_live_payload_cache: dict[str, tuple[float, Any]] = {}
_live_payload_refreshing: set[str] = set()
_live_payload_lock = __import__("threading").Lock()
# Per-key build locks so a COLD first build is single-flighted: the startup warmer
# and a concurrent first user would otherwise both launch the same 60-180s
# account-wide scan against the one serverless warehouse (thundering herd). The
# first caller builds; the rest block on the key lock, then serve the now-cached
# result instead of re-scanning.
_live_payload_build_locks: dict[str, Any] = {}
# Cache-version salt for the PERSISTENT tier's keys. Bump FINOPS_CACHE_VERSION
# (any new string) to invalidate all previously-persisted payloads at once —
# e.g. after changing reference data (the workspace->BU mapping, tags) that the
# built payloads bake in, so they rebuild fresh instead of serving up-to-an-hour
# stale. Old-version rows simply age out. Default "1".
_CACHE_VERSION = str(os.environ.get("FINOPS_CACHE_VERSION") or "1")


def _persist_key(key: str) -> str:
    return f"payload:v{_CACHE_VERSION}:{key}"


# A fail-soft builder (jobs / warehouses / clusters) returns an EMPTY payload
# carrying a ``message`` when its required read failed (missing grant, timeout).
# Caching that for the full hour — and persisting it — meant the tab kept showing
# the error for up to an hour after the admin fixed the grant. Degraded payloads
# are held only briefly (so a burst of page loads doesn't re-run a failing scan)
# and never persisted.
_DEGRADED_TTL_S = 60.0


def _is_degraded(payload: Any) -> bool:
    from data.gold import is_degraded_payload
    return is_degraded_payload(payload)


def _store_payload(key: str, payload: Any) -> None:
    """Put a freshly built payload into tier 1 (+ persist unless degraded)."""
    import time
    if _is_degraded(payload):
        # Back-date the timestamp so it expires _DEGRADED_TTL_S from now.
        _live_payload_cache[key] = (time.monotonic() - _LIVE_PAYLOAD_TTL_SECONDS + _DEGRADED_TTL_S, payload)
        return
    _live_payload_cache[key] = (time.monotonic(), payload)

    # Persist in the background — the write (a Lakebase upsert or a Delta MERGE)
    # must never add latency to the response that triggered the build.
    def _persist() -> None:
        try:
            from data import store
            store.cache_put(_persist_key(key), payload)
        except Exception:  # noqa: BLE001
            pass
    __import__("threading").Thread(target=_persist, daemon=True).start()


def _live_payload_refresh(key: str, build) -> Any:
    """Run build(), store the result in BOTH tiers, clear the single-flight flag.
    Returns the built payload (or None if the build yields None / raises)."""
    payload = None
    try:
        payload = build()
        if payload is not None:
            _store_payload(key, payload)
    finally:
        with _live_payload_lock:
            _live_payload_refreshing.discard(key)
    return payload


def _spawn_payload_refresh(key: str, build) -> None:
    """Kick a background rebuild for `key` unless one is already in flight."""
    import threading
    with _live_payload_lock:
        if key in _live_payload_refreshing:
            return
        _live_payload_refreshing.add(key)
    threading.Thread(target=_live_payload_refresh, args=(key, build), daemon=True).start()


def _live_payload_cached(key: str, build):
    """Serve `key` from the two-tier cache with 1h TTL + stale-while-revalidate.
    Only used in live mode. A build returning None (e.g. accumulator not ready)
    is NOT cached, so the caller's fallback path stays live until real data
    exists.

    Order: (1) fresh in-process hit → instant. (2) stale in-process hit → serve
    stale, refresh in bg. (3) cold process: try the persistent Lakebase tier —
    if it has ANY snapshot, seed tier 1 and serve it (refresh in bg if stale),
    so a restart never re-pays the cold build. (4) nothing anywhere → build
    inline (the genuine first-ever build)."""
    import time

    now = time.monotonic()
    hit = _live_payload_cache.get(key)
    if hit is not None:
        age = now - hit[0]
        if age < _LIVE_PAYLOAD_TTL_SECONDS:
            return hit[1]                          # (1) fresh
        _spawn_payload_refresh(key, build)         # (2) stale → serve stale + bg refresh
        return hit[1]

    # (3) Cold process — nothing in memory. Try the persistent tier.
    try:
        from data import store
        persisted = store.cache_get(_persist_key(key))
    except Exception:  # noqa: BLE001
        persisted = None
    if persisted is not None and _is_degraded(persisted[0]):
        persisted = None                           # never resurrect a persisted error state
    if persisted is not None:
        payload, age = persisted
        _live_payload_cache[key] = (now, payload)  # seed tier 1
        if age >= _LIVE_PAYLOAD_TTL_SECONDS:
            _spawn_payload_refresh(key, build)     # persisted but stale → bg refresh
        return payload

    # (4) Nothing anywhere — genuine first build (inline), SINGLE-FLIGHTED per key.
    # Grab (or create) this key's build lock, then re-check the cache inside it: if
    # another thread built while we waited, serve its result instead of re-scanning.
    import threading as _threading
    with _live_payload_lock:
        blk = _live_payload_build_locks.get(key)
        if blk is None:
            blk = _threading.Lock()
            _live_payload_build_locks[key] = blk
    with blk:
        hit = _live_payload_cache.get(key)
        if hit is not None and (time.monotonic() - hit[0]) < _LIVE_PAYLOAD_TTL_SECONDS:
            return hit[1]                          # another thread built it while we waited
        payload = build()
        if payload is not None:
            _store_payload(key, payload)
        return payload


def _self_provision() -> None:
    """On startup (live mode), auto-provision what the app needs, in a background
    thread, non-fatal: (1) create the FinOps Genie Space, (2) create the daily
    jobs when a source path is configured (the bundle already creates them
    otherwise). The serving layer is the nightly gold tables (no Postgres to
    provision). Runs detached so nothing blocks the app; a gap just means a
    feature waits."""
    if not (USE_LIVE and WAREHOUSE_ID):
        return
    import threading

    def _run() -> None:
        try:
            from data import provision

            provision.run_provision(
                WAREHOUSE_ID, INGEST_SCHEMA,
                genie=bool(_FEATURES["genie"]),
                ai_narration=bool(_FEATURES["ai_narration"]),
                llm_endpoint=str(_SETTINGS["llm_endpoint"]),
                source_path=SOURCE_PATH, run_as_sp=JOB_RUN_AS,
                gold_schema=GOLD_SCHEMA)
        except Exception:  # noqa: BLE001 - provisioning is best-effort
            pass

    def _warm_live_caches() -> None:
        """Pre-build the heavy live payloads once at startup so the FIRST user
        doesn't pay the cold cost. hub/jobs/governance are GOLD-first (nightly
        precompute), so the warmer reads gold — a fast ~1s Delta read that primes
        the in-process cache — and only computes live if gold is missing (transient
        pre-first-run). Warehouse Monitor + Cluster Efficiency use the daily-grain
        accumulators (gold) with a live fallback. Best-effort + detached: a failure
        just means the first request warms it the normal way. Each build runs in its
        OWN thread so they warm CONCURRENTLY. The single serverless warehouse
        autoscales to absorb the parallel scans."""
        from data import warehouses as wh, clusters as cl, live as _live
        builds = (
            ("hub", lambda: _gold_or_compute(
                "hub", lambda: __import__("data.rec_hub", fromlist=["build_hub"]).build_hub())),
            ("jobs", lambda: _gold_or_compute(
                "jobs", lambda: __import__("data.jobs", fromlist=["jobs_live"]).jobs_live(WAREHOUSE_ID))),
            ("warehouses", lambda: _live_payload_cached(
                "warehouses:30d",
                lambda: (wh.warehouses_from_daily(WAREHOUSE_ID, GOLD_SCHEMA, "30d") if GOLD_SCHEMA else None)
                        or wh.warehouses_live(WAREHOUSE_ID, "30d"))),
            ("clusters", lambda: _live_payload_cached(
                "clusters:30",
                lambda: (cl.clusters_from_daily(WAREHOUSE_ID, GOLD_SCHEMA, 30) if GOLD_SCHEMA else None)
                        or cl.clusters_live(WAREHOUSE_ID, 30))),
            ("governance", lambda: _gold_or_compute(
                "governance", lambda: _live.governance_live(WAREHOUSE_ID, INGEST_SCHEMA))),
            ("pipelines", lambda: _gold_or_compute(
                "pipelines", lambda: __import__("data.pipelines", fromlist=["pipelines_live"]).pipelines_live(WAREHOUSE_ID))),
        )

        def _warm_one(build):
            try:
                build()
            except Exception:  # noqa: BLE001 - warming is best-effort
                pass
        _live.warm_auth()  # resolve credentials once before the concurrent warm-up
        for _label, _build in builds:
            threading.Thread(target=_warm_one, args=(_build,), daemon=True).start()

    threading.Thread(target=_run, daemon=True).start()
    threading.Thread(target=_warm_live_caches, daemon=True).start()

app = FastAPI(title="Estate Command Center", version="1.0.0")


class _GzipExceptStreams:
    """gzip JSON + static responses (the Jobs / Recommendations / Access payloads
    run to several MB of very compressible JSON), but pass the Ask Genie SSE
    stream through untouched — older Starlette GZipMiddleware buffers small SSE
    chunks, which would stall the token-by-token answer."""

    def __init__(self, app_):
        from starlette.middleware.gzip import GZipMiddleware
        self._plain = app_
        self._gz = GZipMiddleware(app_, minimum_size=1024)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and not str(scope.get("path", "")).startswith("/api/genie/ask"):
            await self._gz(scope, receive, send)
        else:
            await self._plain(scope, receive, send)


app.add_middleware(_GzipExceptStreams)


@app.on_event("startup")
def _on_startup() -> None:
    # UI-only deploys have no bundle to create the jobs — self-provision them
    # (and the Genie Space) once, in the background. No-op unless live + a
    # source_path is configured; the CLI/bundle path leaves source_path blank.
    _self_provision()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _multi(param: str) -> set[str] | None:
    """Parse a multi-select filter param (comma-separated) into a set of values,
    or None meaning 'no filter' (empty or the literal 'all'). Lets every
    categorical filter accept one OR many values with the same code path:
        ?workspace=all            → None (all)
        ?workspace=ws-a           → {'ws-a'}
        ?workspace=ws-a,ws-b      → {'ws-a','ws-b'}
    """
    if not param or param == "all":
        return None
    vals = {v.strip() for v in param.split(",") if v.strip() and v.strip() != "all"}
    return vals or None


def _ws_match(row: dict[str, Any], sel: set[str]) -> bool:
    """True if a row belongs to the selected workspace set, matching on either
    the workspace NAME or its ID. Both sides are whitespace-stripped so a
    workspace whose name carries a trailing/leading space (real in the wild —
    e.g. 'Public Sector - Federal - FE ') still matches; ``_multi`` strips the
    incoming filter values, so an un-stripped row value would otherwise never
    match and silently return zero rows."""
    name = str(row.get("workspace") or "").strip()
    wid = str(row.get("workspace_id") or "").strip()
    return name in sel or wid in sel


def _apply_acks(rows: list[dict[str, Any]], *, source: str, key_of, metric_field: str,
                status_filter: str = "open", acks: dict | None = None,
                counts_sink: dict | None = None) -> list[dict[str, Any]]:
    """Overlay acknowledge/ignore state onto a list of alert/rec rows.

    Source-agnostic: `key_of(row)` yields the stable entity_key, `metric_field`
    is the magnitude used for smart re-alert. Annotates each row with
    `ack_status` + `reopened` (+ `ack_reason`/`reopen_reason`) and filters by
    `status_filter`:
      * "open" (default)  → active items: not-acked, in_progress, or auto-reopened.
      * "acknowledged" / "wont_fix" / "in_progress" → just those.
      * "all"             → everything, annotated.
    No-op passthrough (all rows visible, unannotated) when writeback is off — so
    a deployment without the feature behaves exactly as before. `acks` lets a
    caller that already fetched the ack records (e.g. the hub, which also needs
    them for its status counts) pass them in to avoid a second store round-trip.
    """
    from data import store
    # Writeback is a built-in; skip the overlay only when it can't PERSIST (e.g.
    # live+Delta with no writable schema) — then every row just shows as 'open'.
    if not store.operational()[0]:
        return rows
    from datetime import date

    if acks is None:
        acks = store.get_acks(WAREHOUSE_ID, source)
    # ALWAYS copy each row before annotating. In live mode `rows` are dicts held
    # in the shared TTL payload cache (_live_payload_cache); mutating them in place
    # would (a) bleed ack annotations across concurrent requests and (b) leave
    # STALE ack_reason/reopen_reason on a row after its ack is deleted. Copying +
    # writing a full, explicit ack field set each pass keeps the cache pristine.
    if not acks and status_filter in ("open", "all"):
        # Nothing acknowledged yet — fast path, everything is open. Counts stay at
        # their zero-initialised values (nothing to add).
        return [{**r, "ack_status": "open", "reopened": False} for r in rows]
    today = date.today().isoformat()
    out: list[dict[str, Any]] = []
    for src_row in rows:
        r = dict(src_row)  # copy — never mutate the cached source dict
        ek = str(key_of(r))
        ack = acks.get(ek)
        eff = store.effective_status(ack, float(r.get(metric_field) or 0.0), today_iso=today)
        # Write the COMPLETE ack field set every time (defaults included) so no
        # stale key from a prior state can linger.
        r["ack_status"] = eff["status"]
        r["reopened"] = eff.get("reopened", False)
        r["reopen_reason"] = eff.get("reopen_reason", "")
        r["ack_reason"] = ack.get("reason", "") if ack else ""
        r["ack_updated_by"] = ack.get("updated_by", "") if ack else ""
        r["ack_updated_at"] = ack.get("updated_at", "") if ack else ""
        # Status-chip counts, computed from the SAME reopen-aware effective status
        # the filter uses — so a chip's number matches exactly what its list shows.
        # An acked item that auto-reopened (snooze expired / metric worsened) is NOT
        # counted under its stored status (it's surfaced under "open" instead).
        if counts_sink is not None and ack and not eff.get("reopened"):
            st = ack.get("status", "")
            if st in counts_sink:
                counts_sink[st] += 1
        # Filtering.
        if status_filter == "all":
            out.append(r)
        elif status_filter == "open":
            # Active = not hidden (covers not-acked, in_progress, and reopened).
            if not eff.get("hidden"):
                out.append(r)
        else:
            # A specific status chip. Reopened items are shown under "open" only.
            if ack and ack.get("status") == status_filter and not eff.get("reopened"):
                out.append(r)
    return out


def _acks_for(source: str) -> dict | None:
    """Fetch the ack records for a source ONCE per request (None when writeback
    can't persist). Callers pass the result to BOTH _apply_acks(acks=...) and
    _counts_from_acks(), so a single store round-trip powers the overlay + the
    status-filter chip counts (avoids the N+1 of fetching twice)."""
    from data import store
    # Writeback is a built-in; gate on whether it can PERSIST. When it can't (live
    # + Delta + no writable schema) the UI already hides the ack controls via
    # /api/config, so skip the per-request round-trip that would just fail soft to
    # {} and log noise on every alert-tab load.
    if not store.operational()[0]:
        return None
    return store.get_acks(WAREHOUSE_ID, source)


def _new_counts_sink(acks: dict | None) -> dict[str, int] | None:
    """A zero-initialised {acknowledged, wont_fix, in_progress} sink for the
    status-filter chips, or None when writeback is off. Pass it to _apply_acks as
    counts_sink=; _apply_acks fills it using the SAME reopen-aware effective status
    the list uses, so each chip's number matches exactly what its list shows.
    (Was _counts_from_acks, which counted raw ack.status and so over-counted acks
    that had auto-reopened — a chip could read 5 while its list showed 3.)"""
    if acks is None:
        return None
    return {"acknowledged": 0, "wont_fix": 0, "in_progress": 0}


def _wrap(payload: Any, live_source: bool = False) -> dict[str, Any]:
    """Wrap a payload with demo / mode metadata surfaced to the UI.

    ``live_source`` = True means these numbers came from the customer's real
    system tables. In live mode, endpoints that are NOT yet wired for live data
    pass live_source=False and are flagged ``live_pending`` so the UI marks them
    as sample data — we never present seeded numbers as if they were real.
    """
    live_pending = bool(USE_LIVE and not live_source)
    return {
        "demo": (not USE_LIVE) or live_pending,
        "mode": "live" if USE_LIVE else "demo",
        "live_pending": live_pending,
        "data": payload,
    }


def _table_health(t: dict[str, Any]) -> tuple[str, str]:
    """Per-table health (Good / Warning / Critical) from freshness + file
    hygiene + staleness. Data-quality monitoring is deliberately NOT folded in —
    it has its own Data Quality tab (live table health uses freshness + usage
    recency; see data/live.py tables_live)."""
    small = float(t.get("small_file_ratio") or 0)
    stale = float(t.get("stale_ratio") or 0)
    if stale >= 0.5 or (t.get("needs_action") and small >= 0.4):
        return "Critical", f"{round(stale*100)}% stale data, {round(small*100)}% small files"
    if t.get("needs_optimize") or t.get("needs_vacuum") or small >= 0.25:
        return "Warning", f"{round(small*100)}% small files — needs OPTIMIZE/VACUUM"
    return "Good", "fresh, well-compacted, actively maintained"


# Short in-process cache for the per-item live drill-downs and workspace-scoped
# views (workspace detail, principal / securable access, table detail, the
# access tree, and the cost-drivers / Genie $ / AI $ views for ONE workspace).
# These ran a fresh system-table read on EVERY open — re-opening the same drawer
# or flicking back to a workspace re-paid 5-20s each time. 10 min keeps them
# fresh enough for cost/grant data; single-flighted per key; errors aren't cached.
_SHORT_TTL_S = 600.0
_short_cache_store: dict[str, tuple[float, Any]] = {}
_short_cache_locks: dict[str, Any] = {}


def _short_cache(key: str, build):
    import threading
    import time
    hit = _short_cache_store.get(key)
    if hit is not None and time.monotonic() - hit[0] < _SHORT_TTL_S:
        return hit[1]
    with _live_payload_lock:
        lk = _short_cache_locks.setdefault(key, threading.Lock())
    with lk:
        hit = _short_cache_store.get(key)
        if hit is not None and time.monotonic() - hit[0] < _SHORT_TTL_S:
            return hit[1]
        val = build()
        if val is not None:
            if len(_short_cache_store) > 2000:   # bound memory on a huge estate
                _short_cache_store.clear()
            _short_cache_store[key] = (time.monotonic(), val)
        return val


# In-process memo over the nightly gold reads. Every gold-backed request used to
# run a SQL read of <gold>.finops_gold AND decode (gunzip + JSON-parse) the whole
# payload — on EVERY page load and every filter click (filters are applied in
# Python after the read). Gold only changes when the nightly job runs, so holding
# the decoded payload for 10 min removes a warehouse round-trip + multi-MB decode
# from almost every request. A gold miss is held for 60s (then re-checked) so a
# first gold run shows up quickly.
_GOLD_MEMO_TTL_S = 600.0
_GOLD_MISS_TTL_S = 60.0
_gold_memo: dict[str, tuple[float, Any]] = {}


# Gold snapshots written by an OLDER app version lack fields newer code relies
# on (e.g. the AI tab's callers, the jobs' schedule/last-run, the hub's
# bu_of_ws). After an upgrade the nightly job hasn't re-run yet, so treat such a
# snapshot as a miss — the request then computes fresh (once, then cached)
# instead of serving a payload the UI can't fully render.
def _first_row_has(payload: Any, list_key: str | None, field: str) -> bool:
    rows = payload.get(list_key) if (list_key and isinstance(payload, dict)) else payload
    if not isinstance(rows, list):
        return False
    return not rows or field in rows[0]


_GOLD_CURRENT = {
    "ai-cost": lambda p: isinstance(p, dict) and "callers" in p,
    "jobs": lambda p: isinstance(p, dict) and _first_row_has(p, "jobs", "last_run"),
    "hub": lambda p: isinstance(p, dict) and "bu_of_ws" in p,
}


def _read_gold_memo(key: str):
    import time
    from data import live

    now = time.monotonic()
    hit = _gold_memo.get(key)
    if hit is not None:
        ttl = _GOLD_MEMO_TTL_S if hit[1] is not None else _GOLD_MISS_TTL_S
        if now - hit[0] < ttl:
            return hit[1]
    val = live.read_gold(WAREHOUSE_ID, GOLD_SCHEMA, key)
    # A degraded snapshot (written by an older gold job) is treated as a miss so
    # the request computes fresh instead of serving yesterday's error all day.
    if val is not None and _is_degraded(val[0]):
        val = None
    if val is not None and key in _GOLD_CURRENT and not _GOLD_CURRENT[key](val[0]):
        val = None   # written by an older version — compute fresh until the next gold run
    _gold_memo[key] = (time.monotonic(), val)
    return val


def _gold_or_compute(key: str, compute):
    """GOLD-first serve: return the nightly-precomputed payload from
    <GOLD_SCHEMA>.finops_gold when present, else the persistent/in-memory cache,
    else compute it live. Gold is an optimisation only — a missing gold schema /
    row / read error transparently falls through. When NOT gold-backed (e.g.
    tables/queries on an estate with no gold job), the two-tier cache
    (_live_payload_cached) still keeps repeat + post-restart loads fast instead
    of recomputing the live scan every request. Raises live.LiveError (→ 503)
    only if the live compute itself fails. Returns (payload, snapshot_at_iso|"")."""
    if GOLD_SCHEMA:
        hit = _read_gold_memo(key)
        if hit is not None:
            return hit  # (payload, snapshot_at)
    # No gold snapshot — in live mode route the live compute through the
    # persistent two-tier cache so it isn't re-run on every request / restart.
    if USE_LIVE:
        return _live_payload_cached(key, compute), ""
    return compute(), ""


def _cached_or_live(key: str, compute):
    """Serve a single payload gold-first; fail-closed (503) on a live error.
    Used by the estate-heavy single-payload endpoints. ``cached_at`` (when set)
    is the gold snapshot time."""
    from data import live

    try:
        payload, cached_at = _gold_or_compute(key, compute)
        env = _wrap(payload, live_source=True)
        if cached_at:
            env["cached_at"] = cached_at
        return env
    except live.LiveError as e:
        raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")


def _cached_rows_or_live(key: str, compute) -> tuple[list, bool, str]:
    """Like _cached_or_live but returns the row LIST (for endpoints that then
    filter + summarise). Returns (rows, live_source, snapshot_at)."""
    from data import live

    try:
        payload, cached_at = _gold_or_compute(key, compute)
        return [dict(r) for r in payload], True, cached_at
    except live.LiveError as e:
        raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")


def _access_live() -> dict:
    """Gold-or-live Access grant graph (principals / grants / risks)."""
    from data import live

    payload, _ = _gold_or_compute("access-grants", lambda: live.access_grants_live(WAREHOUSE_ID))
    return payload


def _platform_total() -> float | None:
    """Total platform spend this month, for the '% of total spend' context on the
    Genie/AI pages. Demo: the seeded estate total. Live: a cached billing sum.
    Returns None on a live failure (NOT 0.0) — 0.0 would become the denominator of
    the Genie/AI '% of total' and render those as ~100% of a $0 estate. None → the
    caller omits the field and the UI drops the '% of total' framing entirely."""
    if USE_LIVE:
        from data import live

        try:
            total = live.platform_spend_total(WAREHOUSE_ID)
            return total if total and total > 0 else None
        except Exception:  # noqa: BLE001 — best-effort context, never breaks the response
            return None
    return float(demo.get_overview().get("total_spend_usd_month", 0) or 0)


def _attach_platform_total_env(env: dict) -> dict:
    """Attach total_platform_spend_usd_month to a cached response envelope's data,
    WITHOUT mutating the shared cached dict (shallow-copy data first), and only when
    the total is known (None → omit, so the UI drops the '% of total' framing rather
    than dividing by a fabricated 0)."""
    total = _platform_total()
    if total is None:
        return env
    data = env.get("data")
    if isinstance(data, dict):
        env = {**env, "data": {**data, "total_platform_spend_usd_month": total}}
    return env


def _attach_platform_total_payload(payload: dict) -> dict:
    """Same as _attach_platform_total_env but for a freshly-built (non-cached)
    payload dict: mutate in place is safe here, but still omit when total is
    unknown (None) rather than writing a fabricated 0."""
    total = _platform_total()
    if total is not None:
        payload["total_platform_spend_usd_month"] = total
    return payload



# ---------------------------------------------------------------------------
# API — config + overview
# ---------------------------------------------------------------------------

def _effective_genie_space_id() -> str:
    """The Genie Space id the app will ACTUALLY answer with. In live mode with the
    Genie feature on, the app auto-provisions a Space and discovers its id (env var
    usually blank), so report the resolved id — not the raw env — so /api/config
    reflects Genie's real state. Best-effort + fast: never blocks/raises config."""
    env_val = str(_SETTINGS["genie_space_id"])
    if env_val or not USE_LIVE or not _FEATURES.get("genie"):
        return env_val
    try:
        from data import genie_client
        return genie_client._space_id() or ""
    except Exception:  # noqa: BLE001 — config must never break on discovery
        return env_val


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    # Writeback is reported as on only when it can actually persist (see
    # store.operational() — guards the default-on-but-silently-broken trap in
    # live+Delta with no writeback schema configured).
    try:
        from data import store
        _writeback_ok, _writeback_reason = store.operational()
    except Exception as e:  # noqa: BLE001
        _writeback_ok, _writeback_reason = False, f"writeback unavailable: {e}"
    return {
        "app_name": "Estate Command Center",
        "app_short": "Estate CC",
        "mode": "live" if USE_LIVE else "demo",
        "demo": not USE_LIVE,
        # Set when live was REQUESTED (USE_LIVE=true) but the app fell back to demo
        # because the warehouse/gold schema aren't configured yet — the UI shows a
        # "finish setup to go live" banner instead of silently looking like demo was
        # intended. Blank when the mode is what was asked for.
        "live_setup_note": _LIVE_SETUP_NOTE,
        "customer": os.environ.get("FINOPS_CUSTOMER_NAME", "Acme Corp"),
        # Dual-currency toggle config (SPEC §9). USD is the source-of-truth list
        # price; AUD is an indicative conversion at this configurable FX rate.
        "currencies": [
            {"code": "USD", "symbol": "$", "rate": 1.0, "label": "USD (list price)"},
            {"code": "AUD", "symbol": "A$", "rate": FINOPS_FX_AUD, "label": f"AUD (× {FINOPS_FX_AUD} FX)"},
        ],
        "fx_aud": FINOPS_FX_AUD,
        "base_currency": "USD",
        # Effective-cost calibration. Figures are LIST price;
        # effective ≈ list × (1 − effective_discount). 0 ⇒ list only (the UI shows a
        # cost-basis note + how to calibrate, and never invents an effective number).
        "effective_discount": float(_SETTINGS.get("effective_discount") or 0.0),
        # Deploy-time feature flags (SPEC §10). The frontend reads these once and
        # conditionally renders: Ask Genie banner (genie), Data Quality tab (dqm),
        # LLM narration (ai_narration). When a flag is off the matching endpoint
        # is also inert (404). Deterministic recommender + cost-attribution +
        # Genie-cost are ALWAYS on regardless of these flags.
        "features": {
            "genie": bool(_FEATURES["genie"]),
            "ai_narration": bool(_FEATURES["ai_narration"]),
            "dqm": bool(_FEATURES["dqm"]),
            # Writeback (acknowledge/dismiss) is a BUILT-IN capability, not a toggle —
            # it always ships with the app. This field is its OPERATIONAL STATUS: true
            # when it can actually persist, false when it can't (live + Delta backend
            # with no writable schema / grant). The UI shows the ack controls only when
            # true, so a persistence gap hides them with an honest note rather than
            # letting a dismissal silently vanish. Demo + Lakebase are always operational.
            "writeback": _writeback_ok,
        },
        # Why writeback can't persist right now (blank when it's fine). Honest
        # degradation for the built-in capability.
        "writeback_note": ("" if _writeback_ok else _writeback_reason),
        # Genie Space curated over cost/usage tables. Report the EFFECTIVE space id
        # actually used to answer — not just the env var. In live mode the app
        # auto-provisions a Space and resolves its id from <schema>.finops_admin
        # (or by title), so the env var is usually blank even though Ask Genie
        # works. Reporting the raw env var made /api/config claim Genie was
        # unconfigured while it was answering fine (a config-honesty gap, same
        # class as the writeback one). Fall back to the env value off the live path.
        "genie_space_id": _effective_genie_space_id(),
        # Whether the business-unit dimension exists (demo always; live only when
        # workspace_bu_table is configured). The UI hides all BU columns/filters/
        # cost-splits when false (a customer review #16).
        "bu_enabled": bu_enabled(),
        # Whether the user->team dimension exists (demo always; live only when
        # user_team_table is configured). UI hides team columns/rollups when false.
        "team_enabled": team_enabled(),
        # Which panels read live data today (rest show clearly-marked sample
        # data). Lets the UI list live vs sample honestly in live mode.
        "live_wired": sorted(_live_wired()) if USE_LIVE else [],
    }


def _live_wired() -> set[str]:
    from data import live

    return live.WIRED


@app.get("/api/health")
def get_health() -> dict[str, Any]:
    """Preflight for live mode: is the warehouse reachable + can the app read the
    required system tables? Returns a structured report so misconfig surfaces on
    boot, not mid-demo. In demo mode this is a trivial OK."""
    if not USE_LIVE:
        return {"mode": "demo", "ok": True, "checks": []}
    from data import live

    report = live.preflight(WAREHOUSE_ID)
    report["mode"] = "live"
    report["live_wired"] = sorted(live.WIRED)
    return report


@app.get("/api/overview")
def get_overview(workspace: str = Query("all"), time_range: str = Query("30d")) -> dict[str, Any]:
    if USE_LIVE:
        from data import live

        env = _cached_or_live("overview", lambda: live.overview_live(WAREHOUSE_ID))
        ws_rows, _ls, _c = _cached_rows_or_live("workspaces", lambda: live.workspaces_live(WAREHOUSE_ID))
        # scale_window=False in live: the range chip must NOT multiply real
        # month-to-date system-table totals by a crude fraction (that would be a
        # fabricated headline). Workspace scoping (real row sums) still applies.
        env["data"] = _scope_overview(env["data"], ws_rows, workspace, time_range, scale_window=False)
        _filter_attention_acks(env["data"])
        return env
    out = _wrap(_scope_overview(demo.get_overview(), demo.get_workspaces(), workspace, time_range))
    _filter_attention_acks(out["data"])
    return out


def _filter_attention_acks(ov: dict[str, Any]) -> None:
    """Drop acknowledged/ignored items from the landing 'needs attention' list
    (writeback), applying the same smart re-alert rule as everywhere else — a
    reopened item stays. No-op when writeback can't persist. Mutates in place."""
    from data import store
    if not store.operational()[0]:
        return
    items = ov.get("needs_attention") or []
    if not items:
        return
    ov["needs_attention"] = _apply_acks(
        items, source="rec",
        key_of=lambda a: a.get("entity_key", ""), metric_field="est_savings_usd_month",
        status_filter="open",
    )


def _scope_overview(payload: dict[str, Any], ws_rows: list[dict[str, Any]],
                    workspace: str, time_range: str = "30d", scale_window: bool = True) -> dict[str, Any]:
    """Scope the Overview KPIs to a subset of workspaces (multi-select) and/or a
    trailing time window. Workspace scoping recomputes totals from the selected
    workspace rows. The time-window $/DBU scaling is a DEMO-only projection
    (``scale_window``); live mode never rescales real month-to-date totals by a
    constant fraction. ``all`` + ``30d`` → estate-wide, unchanged."""
    sel = _multi(workspace)
    p = dict(payload)
    if sel:
        rows = [w for w in ws_rows if _ws_match(w, sel)]
        spend = sum(w["spend_usd_month"] for w in rows)
        savings = sum(w["savings_usd_month"] for w in rows)
        p["total_spend_usd_month"] = round(spend)
        p["total_savings_usd_month"] = round(savings)
        p["savings_pct"] = round(savings / spend, 3) if spend else 0.0
        p["total_dbus_month"] = round(sum(w.get("dbus_month", 0) for w in rows))
        p["num_workspaces"] = len(rows)
        p["num_critical"] = sum(1 for w in rows if w.get("health") == "Critical")
    factor = _RANGE_FACTORS.get(time_range, 1.0)
    if scale_window and factor < 1.0:
        # Demo only: scale spend/savings/DBU totals to the window (counts stay
        # as-is — a workspace's health is point-in-time, not windowed).
        p["total_spend_usd_month"] = round(p["total_spend_usd_month"] * factor)
        p["total_savings_usd_month"] = round(p["total_savings_usd_month"] * factor)
        p["total_dbus_month"] = round(p["total_dbus_month"] * factor)
    return p


@app.get("/api/cost-drivers")
def get_cost_drivers(workspace: str = Query("all")) -> dict[str, Any]:
    """Cost by cost-driver (billing_origin_product) + SKU breakdown +
    per-driver monthly trend + MoM %-change + DBU-by-SKU (SPEC §9).

    Estate-level for ``all``; per-workspace when a workspace name is passed.

    Live mode reads system.billing.usage via the warehouse (fail-closed). On a
    very large account an estate-wide scan can be slow — the executor polls to
    completion and only fails if it exceeds the hard cap.
    """
    if USE_LIVE:
        from data import live

        # Cache holds the estate ("all") drivers; a specific workspace scopes live.
        if workspace in ("all", ""):
            return _cached_or_live("cost-drivers", lambda: live.cost_drivers_live(WAREHOUSE_ID, None))
        try:
            return _wrap(_short_cache(f"cost-drivers:{workspace}", lambda: live.cost_drivers_live(WAREHOUSE_ID, workspace)), live_source=True)
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    return _wrap(demo.get_cost_drivers(workspace))


# ---------------------------------------------------------------------------
# API — access
# ---------------------------------------------------------------------------

@app.get("/api/access/principals")
def get_principals(
    workspace: str = Query("all"),
    principal_type: str = Query("all"),
    source: str = Query("all"),
    search: str = Query(""),
) -> dict[str, Any]:
    if USE_LIVE:
        from data import live

        try:
            rows = [dict(r) for r in _access_live()["principals"]]
            live_source = True
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    else:
        rows = demo.get_principals()
        live_source = False
    if principal_type != "all":
        rows = [r for r in rows if r["type"] == principal_type]
    if source != "all":
        rows = [r for r in rows if r["source"] == source]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["principal"].lower()]
    return _wrap(rows, live_source=live_source)


@app.get("/api/access/grants")
def get_grants(
    principal: str = Query("all"),
    catalog: str = Query("all"),
    privilege: str = Query("all"),
    search: str = Query(""),
) -> dict[str, Any]:
    if USE_LIVE:
        from data import live

        try:
            rows = [dict(r) for r in _access_live()["grants"]]
            live_source = True
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    else:
        rows = demo.get_grants()
        live_source = False
    if principal != "all":
        rows = [r for r in rows if r["principal"] == principal]
    if catalog != "all":
        rows = [r for r in rows if r["catalog"] == catalog]
    if privilege != "all":
        rows = [r for r in rows if r["privilege"] == privilege]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["securable"].lower() or s in r["principal"].lower()]
    return _wrap(rows, live_source=live_source)


@app.get("/api/access/risks")
def get_risks(
    severity: str = Query("all"),
    risk: str = Query("all"),
    search: str = Query(""),
) -> dict[str, Any]:
    if USE_LIVE:
        from data import live

        try:
            rows = [dict(r) for r in _access_live()["risks"]]
            live_source = True
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    else:
        rows = demo.get_risks()
        live_source = False
    if severity != "all":
        rows = [r for r in rows if r["severity"] == severity]
    if risk != "all":
        rows = [r for r in rows if r["risk"] == risk]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["principal"].lower() or s in r["detail"].lower()]
    return _wrap(rows, live_source=live_source)


@app.get("/api/access/tree")
def get_access_tree() -> dict[str, Any]:
    """Cascading catalog → schema → table structure for the data-centric filter."""
    if USE_LIVE:
        from data import live

        try:
            return _wrap(_short_cache("access-tree", lambda: live.access_tree_live(WAREHOUSE_ID)), live_source=True)
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    return _wrap(demo.get_access_tree())


def _admin_503(key: str) -> HTTPException:
    """Actionable 503 for an admin dataset. Distinguishes the two real causes:
    (a) FINOPS_INGEST_SCHEMA not configured, vs (b) configured but the job hasn't
    run — so the operator fixes the right thing, not a made-up placeholder."""
    if not INGEST_SCHEMA:
        return HTTPException(
            status_code=503,
            detail=(f"'{key}' needs the admin store, but FINOPS_INGEST_SCHEMA is not set. "
                    "Set FINOPS_INGEST_SCHEMA (e.g. main.finops_cc) in app.yaml, then run the "
                    "finops_admin_sync job. The app fails closed rather than showing sample data."),
        )
    return HTTPException(
        status_code=503,
        detail=(f"'{key}' isn't ingested yet. This comes from an admin API (not system tables) — "
                f"run the finops_admin_sync job to populate {INGEST_SCHEMA}.finops_admin. "
                "Until then this panel is unavailable (the app fails closed rather than showing sample data)."),
    )


@app.get("/api/access/groups")
def get_group_tree() -> dict[str, Any]:
    """Group membership (from the SCIM API, ingested into finops_admin)."""
    if USE_LIVE:
        from data import live

        try:
            got = live.groups_ingested(WAREHOUSE_ID, INGEST_SCHEMA)
        except live.AdminNotIngested as e:
            raise _admin_503(e.key)
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
        wrapped = _wrap(got["groups"], live_source=True)
        wrapped["last_refreshed"] = got["ingested_at"]
        return wrapped
    return _wrap(demo.get_group_tree())


@app.get("/api/access/pats")
def get_pats() -> dict[str, Any]:
    """PAT inventory (from the token-management API, ingested into finops_admin)."""
    if USE_LIVE:
        from data import live

        try:
            got = live.pats_ingested(WAREHOUSE_ID, INGEST_SCHEMA)
        except live.AdminNotIngested as e:
            raise _admin_503(e.key)
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
        wrapped = _wrap(got["pats"], live_source=True)
        wrapped["last_refreshed"] = got["ingested_at"]
        return wrapped
    return _wrap(demo.get_pats())


@app.get("/api/access/principal/{principal:path}")
def get_principal_access(principal: str) -> dict[str, Any]:
    """Principal-centric view: everything this principal can access, resolved
    through (nested) group membership."""
    if USE_LIVE:
        from data import live

        try:
            detail = _short_cache(f"principal:{principal}", lambda: live.principal_access_live(WAREHOUSE_ID, principal, INGEST_SCHEMA))
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Unknown principal: {principal}")
        return _wrap(detail, live_source=True)
    detail = demo.get_principal_access(principal)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown principal: {principal}")
    return _wrap(detail)


@app.get("/api/access/securable")
def get_securable_access(fqn: str = Query(...)) -> dict[str, Any]:
    """Data-centric view: every grant on a securable + policies in effect.

    Uses a query param (not a path segment) so dotted fully-qualified names
    like ``acme_prod.gold.reporting_exec_summary`` pass through cleanly."""
    if USE_LIVE:
        from data import live

        try:
            return _wrap(_short_cache(f"securable:{fqn}", lambda: live.securable_access_live(WAREHOUSE_ID, fqn)), live_source=True)
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    detail = demo.get_securable_access(fqn)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown securable: {fqn}")
    return _wrap(detail)


# ---------------------------------------------------------------------------
# API — workspaces
# ---------------------------------------------------------------------------

def _annotate_ws_anomalies(rows: list[dict[str, Any]]) -> None:
    """Passive abnormal-cost-movement detection (customer feedback #4).

    Flags workspaces whose most recent week breaks materially from their own
    recent trend, so an unusual spike surfaces without any manual alerting. The
    signal is deliberately simple + explainable (no model): compare the latest
    weekly point to the mean/σ of the preceding weeks in the ``trend`` sparkline
    each workspace already carries (demo + live both populate it).

      deviation% = (latest − baseline_mean) / baseline_mean
      z          = (latest − baseline_mean) / baseline_std

    A workspace is flagged only on an UP move (cost control cares about spikes)
    that is BOTH material (≥25%) AND statistically notable (z≥1.5), so normal
    week-to-week noise on a stable workspace doesn't trip it. Mutates rows in
    place, adding: anomaly(bool), anomaly_pct(signed float), anomaly_severity
    ("warning"|"critical"|""), anomaly_note(str)."""
    for r in rows:
        trend = [float(x) for x in (r.get("trend") or []) if x is not None]
        r["anomaly"] = False
        r["anomaly_pct"] = 0.0
        r["anomaly_severity"] = ""
        r["anomaly_note"] = ""
        if len(trend) < 5:
            continue
        baseline = trend[:-1]
        latest = trend[-1]
        n = len(baseline)
        mean = sum(baseline) / n
        if mean <= 0:
            continue
        var = sum((x - mean) ** 2 for x in baseline) / n
        std = var ** 0.5
        dev = (latest - mean) / mean
        z = (latest - mean) / std if std > 0 else 0.0
        r["anomaly_pct"] = round(dev, 3)
        if dev >= 0.25 and z >= 1.5:
            # Severity is driven by the size of the move (deviation), not σ: on a very
            # stable workspace σ is tiny so even a modest move is many σ — σ is the
            # gate for *flagging*, deviation is the gate for *severity*.
            severity = "critical" if dev >= 0.5 else "warning"
            r["anomaly"] = True
            r["anomaly_severity"] = severity
            r["anomaly_note"] = (
                f"Latest week {'+' if dev >= 0 else ''}{round(dev * 100)}% vs its "
                f"{n}-week average (~{z:.1f}σ) — unusual movement worth checking."
            )


def _annotate_ws_grouping(rows: list[dict[str, Any]]) -> None:
    """Annotate each workspace with its naming-convention group + environment
    (customer feedback #2a / #2b). Config-driven via data/grouping.py; mutates in
    place adding ``group`` (e.g. "EDP Core") and ``environment`` (Production /
    Non-production)."""
    from data import grouping
    mapping = _ws_mapping()
    for r in rows:
        name = r.get("workspace") or r.get("workspace_name") or ""
        r.update(grouping.classify(name, mapping.get(str(r.get("workspace_id") or ""))))


@app.get("/api/workspaces")
def get_workspaces(
    complexity: str = Query("all"),
    health: str = Query("all"),
    search: str = Query(""),
    group: str = Query("all"),
    environment: str = Query("all"),
    env_tier: str = Query("all"),
    platform: str = Query("all"),
) -> dict[str, Any]:
    if USE_LIVE:
        from data import live

        rows, live_source, _c = _cached_rows_or_live("workspaces", lambda: live.workspaces_live(WAREHOUSE_ID))
    else:
        rows = [dict(r) for r in demo.get_workspaces()]   # copy: annotation below mutates
        live_source = False
    # Annotate abnormal cost-movement flags + naming-convention group/environment
    # before filtering so those signals are present regardless of narrowing.
    _annotate_ws_anomalies(rows)
    _annotate_ws_grouping(rows)
    cx = _multi(complexity); hl = _multi(health)
    grp = _multi(group); env = _multi(environment); trf = _multi(env_tier); plf = _multi(platform)
    if grp:
        rows = [r for r in rows if r.get("group") in grp]
    if cx:
        rows = [r for r in rows if r["complexity"] in cx]
    if hl:
        rows = [r for r in rows if r["health"] in hl]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["workspace"].lower() or s in r["bu"].lower()
                or s in str(r.get("platform", "")).lower()]
    # Platform × environment matrix before the platform / environment filters,
    # so it stays a navigator.
    matrix = _platform_env_matrix(rows, cost_key="spend_usd_month")
    bu_matrix = _bu_matrix(rows, cost_key="spend_usd_month")
    if plf:
        rows = [r for r in rows if _platform_match(r, plf)]
    if trf:
        rows = [r for r in rows if r.get("env_tier") in trf]
    if env:
        rows = [r for r in rows if r.get("environment") in env]
    out = _wrap(rows, live_source=live_source)
    out["platform_matrix"] = matrix
    if bu_matrix:
        out["bu_matrix"] = bu_matrix
    return out


@app.get("/api/workspaces/options")
def get_workspace_options() -> dict[str, Any]:
    """LIGHTWEIGHT workspace list for filter dropdowns — just {workspace,
    workspace_id}, so a picker doesn't download the full ~1.8MB /api/workspaces
    payload (4k+ rows with spend/trends/SKU detail) just to show names. In live
    mode this is one small cached query (_workspace_names); demo derives it from
    the seeded set. Registered BEFORE /api/workspaces/{workspace_id} so 'options'
    isn't captured as a workspace id."""
    if USE_LIVE:
        from data import live

        # id -> name (cached, tiny); shape as {workspace, workspace_id} with the
        # friendly name (or id fallback) as the label the dropdowns display.
        try:
            names = _live_payload_cached("workspace-names", lambda: live._workspace_names(WAREHOUSE_ID))
        except Exception as e:  # noqa: BLE001
            # Dropdown-populate only — degrade to an empty picker (the full
            # /api/workspaces payload still carries names). Log the reason so a
            # systemic read failure (e.g. lost SELECT on workspaces_latest) isn't
            # completely invisible, rather than silently returning "no workspaces".
            import logging
            logging.getLogger("finops.app").warning("workspace-options live read failed: %s", e)
            names = {}
        rows = [{"workspace": nm or wid, "workspace_id": wid} for wid, nm in (names or {}).items()]
        return _wrap(rows, live_source=True)
    rows = [{"workspace": w["workspace"], "workspace_id": w.get("workspace_id", "")}
            for w in demo.get_workspaces()]
    return _wrap(rows, live_source=False)


@app.get("/api/workspaces/{workspace_id}")
def get_workspace_detail(workspace_id: str) -> dict[str, Any]:
    live_source = False
    if USE_LIVE:
        from data import live

        # A live-read failure (e.g. missing SELECT on system.billing.usage) is a
        # 503 with the addressable grant — NOT a 404, which would falsely imply
        # the workspace doesn't exist.
        try:
            detail = _short_cache(f"ws-detail:{workspace_id}", lambda: live.workspace_detail_live(WAREHOUSE_ID, workspace_id))
            live_source = True
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    else:
        detail = demo.get_workspace_detail(workspace_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown workspace: {workspace_id}")
    return _wrap(detail, live_source=live_source)


# ---------------------------------------------------------------------------
# API — jobs  (job cost & health, observable facts only; always on)
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def get_jobs(
    compute_type: str = Query("all"),
    why: str = Query("all"),        # all | high-setup-queue | failing | spiking | growing | cost-anomaly
    workspace: str = Query("all"),  # workspace name (or "all")
    workload: str = Query("all"),   # workload type (Batch | Streaming | Interactive / Ad-hoc | ML / AI | Other)
    environment: str = Query("all"),  # Production | Non-production (from the workspace name)
    env_tier: str = Query("all"),     # Dev | Test | Pre-prod | Sandbox | Non-prod | Prod | Unclassified
    schedule: str = Query("all"),     # all | active (scheduled + not paused) | paused | manual
    platform: str = Query("all"),     # platform = workspace name minus its environment word (case-insensitive)
    bu: str = Query("all"),         # business unit (or "all") — from the workspace->BU mapping
    time_range: str = Query("30d"), # run window: 30d | 7d | 24h
    search: str = Query(""),
    limit: int = Query(300),        # list cap; the UI can request 0 = all ("Load all")
    status: str = Query("open"),    # open | acknowledged | wont_fix | in_progress | all (writeback)
) -> dict[str, Any]:
    """Per-job cost + failure/spike/growth/setup-queue signals — observable facts
    only (no serverless-migration recommendation or savings estimate; see
    data/jobs.py docstring for why).

    Deterministic view over run-timeline + billing (calls no model), so always on.
    Demo mode serves the seeded dataset; live mode reads system.lakeflow.job_run_
    timeline + system.billing.usage. Live is FAIL-SOFT: an ungranted table returns
    an empty, well-formed payload with a ``message`` (the tab explains itself
    rather than 503-ing). The KPI ``summary`` reconciles to the filtered rows.
    """
    from data import jobs

    if USE_LIVE:
        # jobs_live runs ~5 heavy system-table scans (~65s cold). GOLD-first:
        # the nightly job precomputes the unfiltered payload, so a gold-backed
        # estate serves it in ~1s; else fall through to the persistent two-tier
        # cache (first build persists + survives restarts). Fail-soft (never raises).
        payload, jobs_cached_at = _gold_or_compute("jobs", lambda: jobs.jobs_live(WAREHOUSE_ID))
        rows = payload.get("jobs", [])
        message = payload.get("message", "")
        live_source = True                       # live mode; message carries any degradation
    else:
        rows = jobs.jobs_demo()
        message = ""
        jobs_cached_at = ""
        live_source = False

    # Time-window scaling (demo): scale run counts + $ to the trailing window so
    # the KPIs react to the chip. Per-run rates (fail %, setup/queue ratio, cost/
    # run) are window-invariant and left unchanged. Live mode reads its own window.
    factor = _RANGE_FACTORS.get(time_range, 1.0)
    if not USE_LIVE and factor < 1.0:
        scaled = []
        for r in rows:
            r = dict(r)
            r["runs"] = max(1, round(r["runs"] * factor))
            r["cost_usd_month"] = round(r["cost_usd_month"] * factor, 2)
            r["failed_run_cost_usd_month"] = round(r.get("failed_run_cost_usd_month", 0) * factor, 2)
            scaled.append(r)
        rows = scaled

    # Copy rows (never mutate the shared cached/gold payload) and annotate each
    # job with its workspace's environment tier / environment / group, plus an
    # operational workload type (batch / streaming / interactive / ML-AI) from the
    # job name + its compute.
    from data import workload as _wl
    rows = _annotate_env(rows)
    for r in rows:
        r["workload"] = _wl.classify_job(r.get("job_name", ""), r.get("compute_type"))

    ct = _multi(compute_type); ws = _multi(workspace); wy = _multi(why); bus = _multi(bu); wlf = _multi(workload)
    enf = _multi(environment); trf = _multi(env_tier); plf = _multi(platform)
    if ct:
        rows = [r for r in rows if r["compute_type"] in ct]
    if wlf:
        rows = [r for r in rows if r.get("workload") in wlf]
    if ws:
        rows = [r for r in rows if _ws_match(r, ws)]
    if bus:
        rows = [r for r in rows if r.get("bu") in bus]
    if wy:
        rows = [r for r in rows if any(w in r["reasons"] for w in wy)]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["job_name"].lower() or s in r["workspace"].lower()
                or s in str(r.get("platform", "")).lower() or s in str(r.get("owner", "")).lower()]

    # Platform × environment matrix — computed BEFORE the platform / environment /
    # tier / schedule filters so it stays a navigator (clicking a cell filters the
    # table; the matrix doesn't collapse to that one cell). Also the non-prod
    # call-out, for the same reason ("Show them" must not hide itself).
    matrix = _platform_env_matrix(rows, cost_key="cost_usd_month", scheduled=True)
    bu_matrix = _bu_matrix(rows, cost_key="cost_usd_month", scheduled=True)
    _np = [r for r in rows if r.get("environment") == "Non-production"]
    nonprod = {
        "num_jobs": len(_np),
        "num_scheduled": sum(1 for r in _np if r.get("schedule_active")),
        "cost_usd_month": round(sum(float(r.get("cost_usd_month") or 0) for r in _np), 2),
        "scheduled_cost_usd_month": round(sum(float(r.get("cost_usd_month") or 0) for r in _np if r.get("schedule_active")), 2),
    }

    if plf:
        rows = [r for r in rows if _platform_match(r, plf)]
    if enf:
        rows = [r for r in rows if r.get("environment") in enf]
    if trf:
        rows = [r for r in rows if r.get("env_tier") in trf]
    if schedule == "active":
        rows = [r for r in rows if r.get("schedule_active")]
    elif schedule == "paused":
        rows = [r for r in rows if r.get("paused") is True]
    elif schedule == "manual":
        rows = [r for r in rows if not r.get("schedule_active") and r.get("paused") is not True]

    # KPI summary is computed BEFORE the ack status-filter, so acknowledging a
    # job (triage) doesn't shrink the estate cost totals — Jobs is a facts view.
    # Only the visible LIST is status-filtered.
    summary = jobs.summarize(rows)
    # Workload roll-up: jobs + cost by workload type.
    _wl_roll: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = _wl_roll.setdefault(r.get("workload", "Other"), {"workload": r.get("workload", "Other"), "num_jobs": 0, "cost_usd_month": 0.0})
        b["num_jobs"] += 1
        b["cost_usd_month"] += float(r.get("cost_usd_month") or 0)
    for b in _wl_roll.values():
        b["cost_usd_month"] = round(b["cost_usd_month"], 2)
    summary["by_workload"] = sorted(_wl_roll.values(), key=lambda x: -x["cost_usd_month"])
    # Per-tier roll-up over the visible (filtered) jobs.
    _tiers: dict[str, dict[str, Any]] = {}
    for r in rows:
        t = _tiers.setdefault(r.get("env_tier") or "Unclassified", {
            "env_tier": r.get("env_tier") or "Unclassified", "environment": r.get("environment") or "",
            "num_jobs": 0, "num_scheduled": 0, "cost_usd_month": 0.0, "runs": 0})
        t["num_jobs"] += 1
        t["num_scheduled"] += 1 if r.get("schedule_active") else 0
        t["cost_usd_month"] += float(r.get("cost_usd_month") or 0)
        t["runs"] += int(r.get("runs") or 0)
    for t in _tiers.values():
        t["cost_usd_month"] = round(t["cost_usd_month"], 2)
    summary["by_env_tier"] = sorted(_tiers.values(), key=lambda x: _TIER_ORDER.get(x["env_tier"], 9))
    summary["nonprod"] = nonprod
    summary["platform_matrix"] = matrix
    if bu_matrix:
        summary["bu_matrix"] = bu_matrix
    acks = _acks_for("job")
    ack_sink = _new_counts_sink(acks)
    rows = _apply_acks(
        rows, source="job",
        key_of=lambda r: f"{r.get('workspace_id','')}:{r.get('job_id','')}",
        metric_field="cost_usd_month", status_filter=status, acks=acks, counts_sink=ack_sink,
    )
    # Cap the shipped LIST to the top jobs WORTH INVESTIGATING. A real estate has
    # tens of thousands of jobs; shipping them all bloated the payload to ~27 MB
    # (multi-second) with no value — nobody scrolls 29k rows. Rank by attention
    # (number of watch reasons first, then cost) — same order as the table's "Why"
    # sort — so the capped list is the most-actionable jobs, not merely the priciest.
    # The KPI summary above is over the FULL filtered set, so totals stay honest.
    # limit=0 means "load all" (the UI's explicit opt-in); the cap is reported in a
    # dedicated field (list_capped), NOT the degradation `message`.
    rows.sort(key=lambda r: (-len(r.get("reasons") or []), -float(r.get("cost_usd_month") or 0)))
    total_matching = len(rows)
    list_capped = None
    if limit and total_matching > limit:
        rows = rows[:limit]
        list_capped = {"shown": limit, "total": total_matching}
    wrapped = _wrap(rows, live_source=live_source)
    wrapped["summary"] = summary
    # Surface the gold snapshot time (when served from gold) so the UI shows "as of
    # <snapshot>" — same honesty contract as the other gold-backed tabs; a stale
    # nightly snapshot is never presented as live-fresh.
    if jobs_cached_at:
        wrapped["cached_at"] = jobs_cached_at
    if list_capped:
        wrapped["list_capped"] = list_capped
    if message:
        wrapped["message"] = message
    if ack_sink is not None:
        wrapped["ack_counts"] = ack_sink
    return wrapped


def _annotate_env(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy each row and add the workspace's environment tier (Dev / Test /
    Pre-prod / Prod …), Production / Non-production environment and
    naming-convention group, from data/grouping.py. Copies so the shared cached /
    gold payload rows are never mutated across concurrent requests."""
    from data import grouping
    mapping = _ws_mapping()
    out = []
    for r in rows:
        r = dict(r)
        m = mapping.get(str(r.get("workspace_id") or ""))
        r.update(grouping.classify(str(r.get("workspace") or ""), m))
        # BU applied at request time too, so editing the mapping table shows up
        # within minutes (was baked into the nightly snapshot until the next run).
        if m and m.get("bu"):
            r["bu"] = m["bu"]
        out.append(r)
    return out


def _ws_mapping() -> dict[str, dict[str, str]]:
    """workspace_id -> {bu, owner, platform, environment} from the optional
    customer mapping table (live only; memoised in live._workspace_bu). Its
    platform / environment columns override what the names imply."""
    if not USE_LIVE:
        return {}
    try:
        from data import live
        return live._workspace_bu(WAREHOUSE_ID)
    except Exception:  # noqa: BLE001
        return {}


_TIER_ORDER = {"Dev": 0, "Test": 1, "Pre-prod": 2, "Sandbox": 3, "Non-prod": 4, "Prod": 5, "Unclassified": 6}


def _platform_match(row: dict[str, Any], sel: set[str]) -> bool:
    """Platform filter, case-insensitive (``Forecasting`` and ``forecasting`` are
    the same platform even if workspaces were named inconsistently)."""
    p = str(row.get("platform") or "").lower()
    return any(p == x.lower() for x in sel)


def _platform_env_matrix(rows: list[dict[str, Any]], cost_key: str, scheduled: bool = False,
                         count_key: str = "count", row_key: str = "platform") -> dict[str, Any]:
    """Platform (rows) × environment tier (columns) roll-up for the "pick a
    platform, see its Dev / Test / Pre-prod / Prod" view. Each cell: cost, number
    of items (jobs or workspaces), number of workspaces, and — for jobs — how many
    are on an active schedule. Platforms are grouped case-insensitively; sorted by
    non-production cost (the clean-up lens), then total. Only platforms spanning
    2+ tiers or carrying non-prod cost are flagged ``multi_env``."""
    plats: dict[str, dict[str, Any]] = {}
    tiers_present: set[str] = set()
    for r in rows:
        label = str(r.get(row_key) or "").strip()
        if not label or label == "—":
            label = "Unmapped" if row_key == "bu" else str(r.get("workspace") or "—")
        key = label.lower()
        tier = r.get("env_tier") or "Unclassified"
        tiers_present.add(tier)
        p = plats.setdefault(key, {"platform": label, "cells": {}, "total_cost": 0.0, "nonprod_cost": 0.0,
                                   "nonprod_scheduled": 0, count_key: 0, "_ws": set()})
        c = p["cells"].setdefault(tier, {"cost": 0.0, count_key: 0, "scheduled": 0, "_ws": set()})
        cost = float(r.get(cost_key) or 0)
        c["cost"] += cost
        c[count_key] += 1
        c["_ws"].add(str(r.get("workspace") or ""))
        p["_ws"].add(str(r.get("workspace") or ""))
        p["total_cost"] += cost
        p[count_key] += 1
        nonprod = r.get("environment") == "Non-production"
        if scheduled and r.get("schedule_active"):
            c["scheduled"] += 1
            if nonprod:
                p["nonprod_scheduled"] += 1
        if nonprod:
            p["nonprod_cost"] += cost
    out_rows = []
    for p in plats.values():
        for c in p["cells"].values():
            c["num_workspaces"] = len(c.pop("_ws"))
            c["cost"] = round(c["cost"], 2)
        p["workspaces"] = sorted(w for w in p.pop("_ws") if w)
        p["num_workspaces"] = len(p["workspaces"])
        p["total_cost"] = round(p["total_cost"], 2)
        p["nonprod_cost"] = round(p["nonprod_cost"], 2)
        p["multi_env"] = len(p["cells"]) > 1
        out_rows.append(p)
    out_rows.sort(key=lambda p: (-p["nonprod_cost"], -p["total_cost"]))
    tiers = sorted(tiers_present, key=lambda t: _TIER_ORDER.get(t, 9))
    totals = {t: round(sum(p["cells"].get(t, {}).get("cost", 0.0) for p in out_rows), 2) for t in tiers}
    # Coverage: how the environment / platform were worked out, by share of cost —
    # so the page can say plainly how much rests on naming conventions alone.
    total_cost = sum(float(r.get(cost_key) or 0) for r in rows) or 0.0

    def _share(field: str, value: str) -> float:
        if total_cost <= 0:
            return 0.0
        return round(sum(float(r.get(cost_key) or 0) for r in rows if r.get(field) == value) / total_cost, 3)

    coverage = {
        "env_unclassified": _share("env_source", "none"),
        "env_from_mapping": _share("env_source", "mapping"),
        "env_from_name": _share("env_source", "name"),
        "platform_from_mapping": _share("platform_source", "mapping"),
        "platform_from_rule": _share("platform_source", "rule"),
        "platform_from_name": _share("platform_source", "name"),
    }
    return {"row_key": row_key, "tiers": tiers, "rows": out_rows, "tier_totals": totals,
            "num_platforms": len(out_rows), "num_multi_env": sum(1 for p in out_rows if p["multi_env"]),
            "coverage": coverage}


def _bu_matrix(rows: list[dict[str, Any]], cost_key: str, scheduled: bool = False) -> dict[str, Any] | None:
    """Same grid with business units as rows — only when a BU mapping exists."""
    if not any((r.get("bu") or "—") not in ("", "—") for r in rows):
        return None
    return _platform_env_matrix(rows, cost_key=cost_key, scheduled=scheduled, row_key="bu")


@app.get("/api/pipelines")
def get_pipelines(
    workspace: str = Query("all"),
    pipeline_type: str = Query("all"),   # ETL_PIPELINE | MATERIALIZED_VIEW | STREAMING_TABLE | ...
    environment: str = Query("all"),     # Production | Non-production
    env_tier: str = Query("all"),        # Dev | Test | Pre-prod | Sandbox | Non-prod | Prod | Unclassified
    why: str = Query("all"),             # failing | cost-spike | full-refresh-heavy | continuous | ...
    bu: str = Query("all"),
    platform: str = Query("all"),        # workspace name minus its environment word
    search: str = Query(""),
    limit: int = Query(300),             # list cap; 0 = all ("Load all")
    status: str = Query("open"),         # open | acknowledged | wont_fix | in_progress | all (writeback)
) -> dict[str, Any]:
    """Per-pipeline cost + health: Lakeflow pipelines AND the pipelines behind SQL
    materialized views / streaming tables, Lakeflow Connect ingestion and Lakebase
    synced tables — anything billed with a pipeline id. Observable facts only (no
    savings estimate). Live is gold-first + fail-soft like Jobs."""
    from data import pipelines

    message = ""
    note = ""
    cached_at = ""
    if USE_LIVE:
        payload, cached_at = _gold_or_compute("pipelines", lambda: pipelines.pipelines_live(WAREHOUSE_ID))
        rows = payload.get("pipelines", [])
        message = payload.get("message", "")
        note = payload.get("note", "")
        live_source = True
    else:
        rows = pipelines.pipelines_demo()
        live_source = False

    rows = _annotate_env(rows)
    ws = _multi(workspace); pt = _multi(pipeline_type); en = _multi(environment)
    tr = _multi(env_tier); wy = _multi(why); bus = _multi(bu); plf = _multi(platform)
    if ws:
        rows = [r for r in rows if _ws_match(r, ws)]
    if pt:
        rows = [r for r in rows if r.get("pipeline_type") in pt or r.get("type_label") in pt]
    matrix = _platform_env_matrix(rows, cost_key="cost_usd_month")
    bu_matrix = _bu_matrix(rows, cost_key="cost_usd_month")
    if plf:
        rows = [r for r in rows if _platform_match(r, plf)]
    if en:
        rows = [r for r in rows if r.get("environment") in en]
    if tr:
        rows = [r for r in rows if r.get("env_tier") in tr]
    if bus:
        rows = [r for r in rows if r.get("bu") in bus]
    if wy:
        rows = [r for r in rows if any(w in (r.get("reasons") or []) for w in wy)]
    if search:
        q = search.lower()
        rows = [r for r in rows if q in str(r.get("name", "")).lower() or q in str(r.get("pipeline_id", "")).lower()
                or q in str(r.get("owner", "")).lower()]
    summary = pipelines.summarize(rows)   # facts view: KPIs before the ack/status overlay
    summary["platform_matrix"] = matrix
    if bu_matrix:
        summary["bu_matrix"] = bu_matrix
    acks = _acks_for("pipeline")
    ack_sink = _new_counts_sink(acks)
    rows = _apply_acks(
        rows, source="pipeline",
        key_of=lambda r: f"{r.get('workspace_id','')}:{r.get('pipeline_id','')}",
        metric_field="cost_usd_month", status_filter=status, acks=acks, counts_sink=ack_sink,
    )
    rows.sort(key=lambda r: (-len(r.get("reasons") or []), -float(r.get("cost_usd_month") or 0)))
    total = len(rows)
    capped = None
    if limit and total > limit:
        rows = rows[:limit]
        capped = {"shown": limit, "total": total}
    wrapped = _wrap(rows, live_source=live_source)
    wrapped["summary"] = summary
    wrapped["caveats"] = pipelines.CAVEATS
    if cached_at:
        wrapped["cached_at"] = cached_at
    if capped:
        wrapped["list_capped"] = capped
    if message:
        wrapped["message"] = message
    if note:
        wrapped["note"] = note
    if ack_sink is not None:
        wrapped["ack_counts"] = ack_sink
    return wrapped


@app.get("/api/jobs/{workspace_id}/{job_id}")
def get_job_detail(workspace_id: str, job_id: str) -> dict[str, Any]:
    """Lazy per-job drill-down: recent terminal runs + termination codes for ONE
    job. The list endpoint leaves these empty (not bulk-scannable estate-wide);
    fetched here on row-expand via a cheap single-job scan. Demo mode already
    ships them inline, so this is only wired for live; fail-soft to empty lists."""
    from data import jobs

    if USE_LIVE:
        return _wrap(jobs.job_detail_live(WAREHOUSE_ID, workspace_id, job_id), live_source=True)
    return _wrap({"recent_runs": [], "termination_codes": []}, live_source=False)


# ---------------------------------------------------------------------------
# API — queries + recommendations
# ---------------------------------------------------------------------------

@app.get("/api/queries")
def get_queries(
    workspace: str = Query("all"),
    warehouse: str = Query("all"),
    time_range: str = Query("24h"),
    p95_threshold: float = Query(0.0),
    flag: str = Query("all"),
    insight_type: str = Query("all"),
    search: str = Query(""),
    status: str = Query("open"),    # open | acknowledged | wont_fix | in_progress | all (writeback)
) -> dict[str, Any]:
    """Query Advisor rows — enriched by the deterministic recommender.

    ``time_range`` scopes the run window; demo data models the trailing window
    by scaling run counts so KPIs shift with the selected range (the underlying
    per-run metrics are stable). Live mode would push the window into the
    system.query.history predicate instead.
    """
    if USE_LIVE:
        from data import live

        # Map the UI time-range chip to a trailing-day window + a per-window cache
        # key, so 24h/7d/30d actually re-scope the query.history read in live (was
        # a fixed 7d ignoring the chip). Unknown values fall back to 7d.
        _days = {"24h": 1, "7d": 7, "30d": 30}.get(time_range, 7)
        # Gold holds the 7-day window under "queries" (the hub reads that key too)
        # and the 24h / 30d windows under "queries:1d" / "queries:30d". The old
        # "queries:{n}d" key never matched gold, so Query Advisor always ran a
        # full live query.history scan (~20s+) on a cold process.
        _qkey = "queries" if _days == 7 else f"queries:{_days}d"
        rows, live_source, _c = _cached_rows_or_live(
            _qkey, lambda: live.queries_live(WAREHOUSE_ID, _days))
    else:
        rows = [dict(r) for r in demo.get_queries()]
        rows = _scale_runs_for_range(rows, time_range)
        live_source = False
    ws = _multi(workspace); wh = _multi(warehouse); fl = _multi(flag); it = _multi(insight_type)
    if ws:
        rows = [r for r in rows if _ws_match(r, ws)]
    if wh:
        rows = [r for r in rows if r["warehouse"] in wh or r.get("warehouse_id") in wh]
    if p95_threshold:
        rows = [r for r in rows if r["p95_s"] >= p95_threshold]
    if fl:
        rows = [r for r in rows if any(f in r["flags"] for f in fl)]
    if it:
        rows = [r for r in rows if r["insight_type"] in it]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["query_text"].lower() or s in r["user"].lower() or s in r["warehouse"].lower()]
    # Acknowledge/ignore overlay (writeback). Query `id` is regenerated each run,
    # so key on the STABLE (insight_type + target) pair — acking "rewrite on
    # acme.silver.reads" persists across recomputes. Magnitude = est savings.
    acks = _acks_for("query")
    ack_sink = _new_counts_sink(acks)
    rows = _apply_acks(
        rows, source="query",
        key_of=lambda r: f"{r.get('insight_type','')}::{r.get('target_table') or r.get('warehouse','')}",
        metric_field="est_savings_usd_month", status_filter=status, acks=acks, counts_sink=ack_sink,
    )
    wrapped = _wrap(rows, live_source=live_source)
    if ack_sink is not None:
        wrapped["ack_counts"] = ack_sink
    return wrapped


# Approximate share of a monthly run count that falls in each trailing window.
_RANGE_FACTORS = {"1h": 1.0 / 720, "6h": 6.0 / 720, "24h": 24.0 / 720, "7d": 7.0 / 30, "30d": 1.0, "custom": 1.0}

# Cluster utilisation window → trailing days (Cluster Efficiency time filter).
_RANGE_DAYS_CLUSTERS = {"7d": 7, "14d": 14, "30d": 30, "60d": 60, "90d": 90}


def _scale_runs_for_range(rows: list[dict[str, Any]], time_range: str) -> list[dict[str, Any]]:
    """Scale monthly runs + cost to the selected trailing window.

    Keeps per-run statement metrics (p95, pruning, spill) unchanged — those are
    properties of the query, not the window — while runs, cost and the derived
    est. savings scale down so the KPI row reacts to the time-range chips.
    """
    factor = _RANGE_FACTORS.get(time_range, 1.0)
    if factor >= 1.0:
        return rows
    out: list[dict[str, Any]] = []
    for r in rows:
        r = dict(r)
        r["runs"] = max(1, round(r["runs"] * factor))
        r["cost_usd"] = round(r["cost_usd"] * factor, 2)
        r["est_savings_usd_month"] = round(r["est_savings_usd_month"] * factor, 2)
        out.append(r)
    return out


class RewriteRequest(BaseModel):
    """Body for POST /api/queries/rewrite — the query row to rewrite. We accept
    the whole row the UI already holds so demo mode needs no server-side lookup;
    live mode uses ``statement_id`` to recover the full (untruncated) SQL."""
    id: int | None = None
    statement_id: str | None = None
    query_text: str = ""
    target_table: str | None = None
    pruning_efficiency: float = 0.0
    bytes_read: float = 0.0
    spill_gb: float = 0.0
    p95_s: float = 0.0
    runs: int = 0


@app.post("/api/queries/rewrite")
def post_query_rewrite(req: RewriteRequest) -> dict[str, Any]:
    """AI-proposed, EXPLAIN-validated rewrite for one query (Increment 3).

    On-demand (not computed for every row): the drill-down calls this when the
    operator asks for a rewrite. Live mode generates the rewrite with the
    in-region model (``FINOPS_LLM_ENDPOINT``) and validates it with EXPLAIN
    against the customer's own warehouse — a rewrite that doesn't bind to the
    real schema is rejected, never shown as runnable. Demo mode returns a
    deterministic illustrative rewrite (payload is flagged ``demo``).
    """
    from data import rewrite

    q = req.model_dump()
    # AI rewrite is part of the ai_narration feature — when it's off, return the
    # deterministic "unavailable" result (with the next-steps still populated),
    # in BOTH live and demo, so a disabled feature is never operable. The UI also
    # hides the panel, but gate the endpoint too (defence in depth).
    if not _FEATURES["ai_narration"]:
        return _wrap(rewrite.propose_rewrite(q, generate=None, explain=None), live_source=USE_LIVE)
    if USE_LIVE:
        from data import live

        try:
            payload = live.rewrite_live(WAREHOUSE_ID, q)
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=str(e))
        return _wrap(payload, live_source=True)
    return _wrap(rewrite.demo_rewrite(q))


@app.post("/api/overview/exec-summary")
def post_exec_summary() -> dict[str, Any]:
    """On-demand AI 'what changed this month & why' narrative for the landing
    view. OPTIONAL enhancement — gated on ai_narration. When the flag is OFF this
    returns status='unavailable' (no model call, no error) in both live and demo,
    so a deployment without AI simply never shows the narrative. The pure-SQL
    summary on the landing view is complete on its own regardless.

    Recomputes the overview server-side (cheap, gold-cached in live) and feeds its
    already-computed figures to the model — it interprets, never fetches."""
    from data import exec_summary

    # Feature-gated: AI off → honest unavailable, no model call (defence in depth;
    # the button is also hidden client-side).
    if not _FEATURES["ai_narration"]:
        return _wrap({"status": exec_summary.STATUS_UNAVAILABLE, "narrative": ""}, live_source=USE_LIVE)

    if USE_LIVE:
        from data import live

        try:
            ov = _gold_or_compute("overview", lambda: live.overview_live(WAREHOUSE_ID))[0]
            payload = live.exec_summary_live(WAREHOUSE_ID, ov)
        except Exception as e:  # noqa: BLE001 — narrative is optional, never 5xx the page
            payload = {"status": exec_summary.STATUS_ERROR, "narrative": "", "error": str(e)[:200]}
        return _wrap(payload, live_source=True)

    # Demo + AI on: a deterministic illustrative narrative over the seeded overview
    # (clearly demo via the envelope), so AEs running an AI-on demo see it work.
    ov = demo.get_overview()
    def _m(n: float) -> str:
        n = float(n or 0)
        return f"${n/1e6:.1f}M" if abs(n) >= 1e6 else (f"${n/1e3:.0f}K" if abs(n) >= 1e3 else f"${n:.0f}")
    def _p(x) -> str:
        return "n/a" if x is None else f"{round(float(x)*100)}%"
    up = "up" if ov.get("spend_mom_delta_usd", 0) >= 0 else "down"
    momp = abs(round(ov.get("spend_mom_pct", 0) * 100))
    qd = ov.get("queries_30d_delta_pct", 0)
    top = (ov.get("cost_drivers", {}).get("drivers") or [{}])[0]
    topname = top.get("label") or top.get("driver") or "the top product"
    # Richer, INTERPRETIVE 4-paragraph briefing (demo stand-in for the live model
    # output) — deliberately fuller than the deterministic baseline so an AI-on
    # deployment clearly gets more than the always-on summary.
    demo_narr = "\n\n".join([
        f"Cost & what's moving — the estate is spending about {_m(ov['total_spend_usd_month'])} this month, {up} "
        f"{momp}% versus the same point last month, led by {topname} at {_p(top.get('pct_of_total'))} of spend. "
        f"With compute {_p(ov.get('serverless_share'))} serverless and {_p(ov.get('automated_pct'))} automated, a "
        f"meaningful slice of workload still runs on interactive/classic compute — the usual first place a "
        f"{up}ward trend originates. Watch whether {topname} keeps climbing next month or this was a one-off.",
        f"Adoption & value — {ov.get('mau',0)} people used the platform in the last 30 days "
        f"({ov.get('wau',0)} in the last 7), running {ov.get('queries_30d',0):,} queries over "
        f"{round(ov.get('query_hours_30d',0)):,} compute-hours, with query volume {'up' if qd>=0 else 'down'} "
        f"{abs(round(qd*100))}% versus the prior 30 days. Teams draw on {ov.get('num_products',0)} products on average, "
        f"and {ov.get('genie_adopters',0)} workspaces are on Genie with {ov.get('ai_adopters',0)} on other AI — healthy "
        f"breadth. Usage moving broadly in step with spend is the efficient pattern; spend rising faster than usage "
        f"would be the flag to chase.",
        f"Health & governance — {ov.get('num_critical',0)} workspaces are in critical health and need attention, and "
        f"{_p(ov.get('untagged_spend_pct'))} of spend is untagged, which is the main gap in being able to attribute "
        f"cost back to teams. Closing the tagging gap is the cheapest way to make the rest of this picture "
        f"accountable, since every other number here can then be split by owner.",
        f"Where to focus — there's an estimated {_m(ov.get('addressable_savings_usd_month', ov['total_savings_usd_month']))}/mo "
        f"of optimisation opportunity (~{round(ov['savings_pct']*100)}% of spend), but treat that as a directional "
        f"headroom signal from the automation/serverless gaps rather than a committed number — the Recommendations tab "
        f"has the specific, evidence-backed plays. Start with the highest-value item in the needs-attention list below. "
        f"(Illustrative narrative over sample data.)",
    ])
    return _wrap({"status": exec_summary.STATUS_OK, "narrative": demo_narr, "prompt_used": False})


# ---------------------------------------------------------------------------
# API — acknowledge / ignore (writeback tier; gated by features.writeback).
# When the flag is OFF these all 404 and no store is touched — the app stays
# read-only. Source-agnostic: works for every alert/rec surface (rec, job,
# warehouse, cluster, table, query, dqm, gov) keyed on (source, entity_key).
# ---------------------------------------------------------------------------

class AckRequest(BaseModel):
    source: str
    entity_key: str
    status: str = "acknowledged"       # acknowledged | wont_fix | in_progress
    reason: str = ""
    metric_name: str = ""
    metric_at_ack: float = 0.0         # snapshot magnitude for smart re-alert
    snooze_days: int | None = None


@app.get("/api/acks")
def get_acks(source: str = Query("")) -> dict[str, Any]:
    """All ack records (optionally for one source) as {entity_key: record}.
    Writeback is a built-in — this returns the records (empty dict when the store
    is empty or can't currently persist; the UI reads /api/config for the reason)."""
    from data import store

    return _wrap(store.get_acks(WAREHOUSE_ID, source or None), live_source=USE_LIVE)


def _acting_user(request: Request) -> str:
    """Resolve WHO is acting, for the ack audit trail. A Databricks App runs
    behind a proxy that injects the signed-in user's identity as forwarded
    headers — capture it so `updated_by` is a real person, not the generic
    'app' service principal. Falls back to 'app' when absent (e.g. local/demo)."""
    h = request.headers
    return (h.get("X-Forwarded-Email")
            or h.get("X-Forwarded-User")
            or h.get("X-Forwarded-Preferred-Username")
            or "app")


@app.post("/api/acks")
def post_ack(req: AckRequest, request: Request) -> dict[str, Any]:
    """Acknowledge / ignore / mark-in-progress one alert. The magnitude is
    snapshotted from the client-supplied metric_at_ack (the value the row is
    showing right now) so smart re-alert can compare later."""
    from data import store

    rec = req.model_dump()
    # Stamp the acting user so the acknowledge/ignore action trail is auditable
    # (who dismissed which alert) — a load-bearing claim for a governance tool.
    rec["updated_by"] = _acting_user(request)
    if req.snooze_days:
        from datetime import date, timedelta
        rec["snooze_until"] = (date.today() + timedelta(days=int(req.snooze_days))).isoformat()
    ok = store.put_ack(WAREHOUSE_ID, rec)
    if not ok:
        raise HTTPException(status_code=503, detail="Could not save acknowledgement (writeback store unavailable).")
    return _wrap({"ok": True}, live_source=USE_LIVE)


@app.delete("/api/acks/{source}/{entity_key:path}")
def delete_ack(source: str, entity_key: str) -> dict[str, Any]:
    """Reopen an acknowledged/ignored item (removes its ack record)."""
    from data import store

    ok = store.delete_ack(WAREHOUSE_ID, source, entity_key)
    if not ok:
        # Don't report success on a failed store write — the user would think the
        # alert was reopened when it's still suppressed. Mirror post_ack.
        raise HTTPException(status_code=503, detail="Could not reopen (writeback store unavailable).")
    return _wrap({"ok": True}, live_source=USE_LIVE)


@app.get("/api/warehouses")
def get_warehouses(
    workspace: str = Query("all"),
    wh_type: str = Query("all"),      # all | Serverless | Pro | Classic
    why: str = Query("all"),          # all | capacity-bound | slow | spilling | idle
    time_range: str = Query("30d"),
    search: str = Query(""),
    status: str = Query("open"),      # open | acknowledged | wont_fix | in_progress | all (writeback)
) -> dict[str, Any]:
    """SQL Warehouse monitor (Increment 4) — per-warehouse throughput, latency,
    queueing, spill, cost + hourly timeline. Observable facts only (no model),
    always on. Demo serves the seeded dataset; live reads system.query.history
    (fail-soft: an ungranted table returns an empty payload with a message)."""
    from data import warehouses as wh

    if USE_LIVE:
        # Prefer the accumulated daily table (<gold>.finops_gold_warehouse_daily):
        # it re-windows ANY range (incl. beyond query.history retention) with
        # correct additive/ratio math + exact live p50/p95. Falls back to the live
        # scan when the accumulator isn't built yet (before the first gold run).
        payload = _live_payload_cached(
            f"warehouses:{time_range}",
            lambda: (wh.warehouses_from_daily(WAREHOUSE_ID, GOLD_SCHEMA, time_range) if GOLD_SCHEMA else None)
                    or wh.warehouses_live(WAREHOUSE_ID, time_range))
        rows = payload.get("warehouses", [])
        message = payload.get("message", "")
        total_active = payload.get("total_active", 0)
        truncated = bool(payload.get("truncated"))
        live_source = True
    else:
        rows = wh.warehouses_demo()
        message = ""
        total_active = 0
        truncated = False
        live_source = False

    # Time-window scaling (demo): runs + cost scale to the trailing window;
    # per-run rates (p95, queue ratio) are window-invariant.
    factor = _RANGE_FACTORS.get(time_range, 1.0)
    if not USE_LIVE and factor < 1.0:
        scaled = []
        for r in rows:
            r = dict(r)
            r["runs"] = max(1, round(r["runs"] * factor))
            r["cost_usd_month"] = round(r["cost_usd_month"] * factor, 2)
            scaled.append(r)
        rows = scaled

    ws = _multi(workspace); ty = _multi(wh_type); wy = _multi(why)
    if ws:
        rows = [r for r in rows if _ws_match(r, ws)]
    if ty:
        # Only filter rows that actually carry a type (demo). system.query.history
        # doesn't expose warehouse type, so live rows have type="" — don't silently
        # drop them all; a row with no known type passes the type filter.
        rows = [r for r in rows if not r.get("type") or r["type"] in ty]
    if wy:
        rows = [r for r in rows if any(x in r.get("reasons", []) for x in wy)]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["warehouse"].lower() or s in r["workspace"].lower()]
    # Acknowledge/ignore overlay (writeback). Filter by the requested status
    # (default 'open' hides acked, matching Recommendations); key on warehouse_id,
    # magnitude = cost.
    # Summary before ack-filter (facts view — acking shouldn't shrink KPI totals).
    summary = wh.summarize(rows)
    acks = _acks_for("warehouse")
    ack_sink = _new_counts_sink(acks)
    rows = _apply_acks(
        rows, source="warehouse", key_of=lambda r: r.get("warehouse_id", ""),
        metric_field="cost_usd_month", status_filter=status, acks=acks, counts_sink=ack_sink,
    )
    wrapped = _wrap(rows, live_source=live_source)
    wrapped["summary"] = summary
    # Surface the top-N cap honestly: shown = post-filter row count, total_active =
    # the universe (distinct active warehouses) so the UI can say "top N of M"
    # instead of implying the visible rows are the whole estate.
    wrapped["shown"] = len(rows)
    if total_active:
        wrapped["total_active"] = total_active
        wrapped["truncated"] = bool(truncated)
    if message:
        wrapped["message"] = message
    if ack_sink is not None:
        wrapped["ack_counts"] = ack_sink
    return wrapped


@app.get("/api/clusters")
def get_clusters(
    workspace: str = Query("all"),
    finding: str = Query("all"),   # all | no-auto-stop | idle | oversized | no-autoscale | ...
    time_range: str = Query("30d"),
    search: str = Query(""),
    status: str = Query("open"),   # open | acknowledged | wont_fix | in_progress | all (writeback)
) -> dict[str, Any]:
    """Cluster Efficiency view (Increment 5) — interactive/all-purpose cluster
    idle/oversize/auto-stop/autoscale observations + clearly-estimated potential
    savings over the selected utilisation window. Built on compute.clusters +
    node_timeline + billing (NOT per-query; cluster query telemetry isn't in
    system tables). Fail-soft in live mode."""
    from data import clusters as cl

    days = _RANGE_DAYS_CLUSTERS.get(time_range, 30)
    if USE_LIVE:
        # Prefer the accumulated daily utilisation table (re-windowed over the
        # selected window, history beyond node_timeline retention) joined to the
        # latest config; fall back to the live 30d scan before the first gold run.
        payload = _live_payload_cached(
            f"clusters:{days}",
            lambda: (cl.clusters_from_daily(WAREHOUSE_ID, GOLD_SCHEMA, days) if GOLD_SCHEMA else None)
                    or cl.clusters_live(WAREHOUSE_ID, days))
        rows = payload.get("clusters", [])
        message = payload.get("message", "")
        total_active = payload.get("total_active", 0)
        truncated = bool(payload.get("truncated"))
        live_source = True
    else:
        rows = cl.clusters_demo(days)
        message = ""
        total_active = 0
        truncated = False
        live_source = False

    ws = _multi(workspace); fi = _multi(finding)
    if ws:
        rows = [r for r in rows if _ws_match(r, ws)]
    if fi:
        rows = [r for r in rows if any(f["code"] in fi for f in r.get("findings", []))]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["cluster"].lower() or s in r["workspace"].lower()
                or s in r.get("creator", "").lower()]
    # Acknowledge/ignore overlay (writeback). Filter by requested status
    # (default 'open'); key on cluster_id, magnitude = potential savings.
    # Summary before ack-filter (facts view — acking shouldn't shrink KPI totals).
    summary = cl.summarize(rows)
    acks = _acks_for("cluster")
    ack_sink = _new_counts_sink(acks)
    rows = _apply_acks(
        rows, source="cluster", key_of=lambda r: r.get("cluster_id", ""),
        metric_field="potential_savings_usd_month", status_filter=status, acks=acks, counts_sink=ack_sink,
    )
    wrapped = _wrap(rows, live_source=live_source)
    wrapped["summary"] = summary
    wrapped["shown"] = len(rows)
    if total_active:
        wrapped["total_active"] = total_active
        wrapped["truncated"] = bool(truncated)
    if message:
        wrapped["message"] = message
    if ack_sink is not None:
        wrapped["ack_counts"] = ack_sink
    return wrapped


@app.get("/api/recommendations/hub")
def get_recommendations_hub(
    workspace: str = Query("all"),
    priority: str = Query("all"),   # all | P1 | P2 | P3
    category: str = Query("all"),   # behavioural | compute | storage | tagging | genai | access | governance
    scope: str = Query("all"),      # global | workspace | bu | team | user | account
    status: str = Query("open"),    # open | acknowledged | wont_fix | in_progress | all (writeback)
) -> dict[str, Any]:
    """Unified AI-advisor hub (SPEC §3.7).

    Aggregates recommendations from every cost/usage source — query-level
    (recommender.py), tables savings, governance gaps, access risks — plus the
    catalog-driven strategic-play generator (matches ``recommendation_catalog``
    triggers against the estate metrics). Returns one scored, filterable list
    with a cost-attribution rollup.
    """
    from data import rec_hub

    live_source = False
    # The hub's cost is the estate BUILD (_all_recs + cost_attribution), not the
    # per-request filtering. The UNFILTERED view is the default page load and what
    # the startup warmer hits, so route it through the persistent two-tier cache
    # (_live_payload_cached) — on a cold process after a restart it serves the
    # Lakebase snapshot in ~1s and the background refresh re-primes rec_hub's own
    # lru_caches, so filtered calls warm up too. Filtered requests build directly
    # (fast once those caches are primed).
    unfiltered = workspace == "all" and priority == "all" and category == "all" and scope == "all"
    _build_hub = lambda: rec_hub.build_hub(  # noqa: E731
        workspace=workspace, priority=priority, category=category, scope=scope,
    )
    if USE_LIVE:
        from data import live

        # DATA-HONESTY GUARD: the live rec-hub provider is only installed at boot
        # when USE_LIVE *and* a warehouse id are both set (see the set_provider
        # block near the top). If it isn't installed (e.g. USE_LIVE=true but
        # DATABRICKS_WAREHOUSE_ID unset/typo'd), rec_hub would silently fall back
        # to its DEMO generator — and we'd wrap it live_source=True, presenting
        # seeded sample recommendations as the customer's real estate. Refuse
        # instead: fail closed like every other live tab does in this state.
        if rec_hub._PROVIDER is None:
            raise HTTPException(
                status_code=503,
                detail="live read failed — recommendations: no live provider "
                       "(set DATABRICKS_WAREHOUSE_ID for USE_LIVE=true)")
        try:
            # Always serve the UNFILTERED hub GOLD-first (nightly precompute), then
            # the persistent two-tier cache, then a live build — and apply any
            # filters to that snapshot in-process. Filtered requests used to call
            # build_hub() directly, which on a cold process re-ran the whole
            # ~2-3 min estate build on every filter click.
            base, hub_cached_at = _gold_or_compute("hub", lambda: rec_hub.build_hub())
            payload = base if unfiltered else rec_hub.filter_hub(
                base, workspace=workspace, priority=priority, category=category, scope=scope)
            live_source = True
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    else:
        payload = _build_hub()
        hub_cached_at = ""
    # Overlay acknowledge/ignore state (writeback tier). Each rec's stable id is
    # its entity_key; est_savings is the magnitude for smart re-alert. No-op when
    # writeback is off. Default status='open' hides acked items (unless reopened).
    # Fetch the ack records ONCE and reuse for both the overlay and the status
    # counts below — two get_acks() calls each mint a Lakebase token + connection.
    acks = _acks_for("rec")
    ack_sink = _new_counts_sink(acks)
    recs = _apply_acks(
        payload["recommendations"], source="rec",
        key_of=lambda r: r.get("id", ""), metric_field="est_savings_usd_month",
        status_filter=status, acks=acks, counts_sink=ack_sink,
    )
    wrapped = _wrap(recs, live_source=live_source)
    wrapped["summary"] = payload["summary"]
    wrapped["attribution"] = payload["attribution"]
    # Ack counts by status for the filter chips (only when writeback on).
    if ack_sink is not None:
        wrapped["ack_counts"] = ack_sink
    # Gold snapshot time (when the unfiltered hub was served from gold) → UI "as
    # of" honesty, so a stale nightly snapshot isn't shown as live-fresh.
    if hub_cached_at:
        wrapped["cached_at"] = hub_cached_at
    return wrapped


# ---------------------------------------------------------------------------
# API — Genie cost attribution  (SPEC §13; ALWAYS on — calls no model)
# ---------------------------------------------------------------------------

@app.get("/api/genie-cost")
def get_genie_cost(workspace: str = Query("all")) -> dict[str, Any]:
    """Genie Code vs Genie Spaces spend, per workspace × user × subtype.

    Deterministic attribution over billing data (SPEC §13). NOT a feature flag —
    it calls no model, so it is always on regardless of features.genie /
    ai_narration. Backed by the three queries in data/queries.sql (encoded in
    data/genie_cost.py for demo mode). List-price $, dual-currency aware in the
    UI; caveats travel with the payload.

    Live mode (USE_LIVE=true): reads system.billing.usage via the configured
    warehouse. Fail-closed — a read error surfaces as HTTP 503 with the exact
    grant/config to fix; it NEVER falls back to demo data.
    """
    from data import genie_cost

    if USE_LIVE:
        from data import live

        # Estate-wide ("all") serves from the nightly gold snapshot (instant);
        # a specific workspace scopes live. Mirrors /api/cost-drivers so the
        # heavy billing scan runs once nightly, not on every page load.
        if workspace in ("all", ""):
            env = _cached_or_live("genie-cost", lambda: live.genie_cost_live(WAREHOUSE_ID, None))
            return _attach_platform_total_env(env)
        try:
            payload = dict(_short_cache(f"genie-cost:{workspace}", lambda: live.genie_cost_live(WAREHOUSE_ID, workspace)))
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
        return _wrap(_attach_platform_total_payload(payload), live_source=True)
    payload = _attach_platform_total_payload(genie_cost.get_genie_cost(workspace))
    return _wrap(payload)


@app.get("/api/ai-cost")
def get_ai_cost(workspace: str = Query("all")) -> dict[str, Any]:
    """AI spend across the AI-family billing products (Model Serving, Foundation
    Model API, Vector Search, Agent Bricks, AI Functions, fine-tuning) — by
    product, endpoint, owner, workspace, trend, + cost-optimisation flags.
    Deterministic billing attribution (calls no model), so always on. Genie is
    on its own page.

    Live mode reads the AI-family billing products via the warehouse (fail-closed
    — 503 with the exact fix, never demo fallback)."""
    from data import ai_cost

    if USE_LIVE:
        from data import live

        # Estate-wide ("all") serves from the nightly gold snapshot; a specific
        # workspace scopes live. Mirrors /api/genie-cost + /api/cost-drivers.
        if workspace in ("all", ""):
            env = _cached_or_live("ai-cost", lambda: live.ai_cost_live(WAREHOUSE_ID, None))
            return _attach_platform_total_env(env)
        try:
            payload = dict(_short_cache(f"ai-cost:{workspace}", lambda: live.ai_cost_live(WAREHOUSE_ID, workspace)))
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
        return _wrap(_attach_platform_total_payload(payload), live_source=True)
    payload = _attach_platform_total_payload(ai_cost.get_ai_cost(workspace))
    return _wrap(payload)


@app.get("/api/classification")
def get_classification() -> dict[str, Any]:
    """Governed tags coverage + automatic Data Classification (PII/sensitive
    detection + scan cost). Deterministic governance view, always on.

    Live: governed tags from system.information_schema.{table,column}_tags,
    classification from the auto-applied class.* column tags, scan cost from the
    DATA_CLASSIFICATION billing line. Fail-closed."""
    if USE_LIVE:
        from data import live

        return _cached_or_live("classification", lambda: live.classification_live(WAREHOUSE_ID))
    from data import classification

    return _wrap(classification.get_classification())


@app.get("/api/adoption")
def get_adoption(
    workspace: str = Query("all"),
    time_range: str = Query("30d"),
) -> dict[str, Any]:
    """Adoption & Value — active users (MAU/WAU), activity volume + query-hours,
    feature/product adoption breadth per workspace, Genie/AI uptake, and a Value
    Map (usage vs recency, sized by users) separating gold assets from archive
    candidates. Always on. Supports workspace + time-period filters (a customer ask —
    replaces a manual monthly cost report).

    Live: active users + queries + query-hours from system.query.history, feature
    breadth + Genie/AI uptake from billing, value map from
    system.access.table_lineage. Fail-closed."""
    if USE_LIVE:
        from data import live

        env = _cached_or_live("adoption", lambda: live.adoption_live(WAREHOUSE_ID))
        # scale_window=False: don't multiply real system-table activity by a
        # crude window fraction in live (workspace filtering still applies).
        env["data"] = _scope_adoption(env["data"], workspace, time_range, scale_window=False)
        return env
    from data import adoption

    return _wrap(_scope_adoption(adoption.get_adoption(), workspace, time_range))


def _scope_adoption(payload: dict[str, Any], workspace: str, time_range: str,
                    scale_window: bool = True) -> dict[str, Any]:
    """Filter an adoption payload to one workspace (or all) and (DEMO only, via
    ``scale_window``) scale the period totals to the selected time window,
    recomputing the team rollup + summary so every number reconciles to what's
    visible. Live never rescales real activity by a constant fraction. Pure
    post-processing so demo + gold-cached live share one code path."""
    from data import adoption as _adoption

    p = dict(payload)
    factor = _RANGE_FACTORS.get(time_range, 1.0) if scale_window else 1.0
    # Multi-select workspace (comma-joined). _multi returns None for empty/"all".
    sel = _multi(workspace) or set()
    def _keep(v: Any) -> bool:
        return not sel or v in sel

    users = [dict(u) for u in p.get("users", []) if _keep(u.get("workspace"))]
    features = [f for f in p.get("features", []) if _keep(f.get("workspace"))]
    assets = [a for a in p.get("assets", []) if _keep(a.get("workspace"))]
    if factor < 1.0:
        for u in users:
            u["queries"] = max(1, round(int(u.get("queries") or 0) * factor))
            u["query_hours"] = round(float(u.get("query_hours") or 0.0) * factor, 1)
    p["users"] = users
    p["features"] = features
    p["assets"] = assets
    p["team_rollup"] = _adoption.team_rollup(users)

    # Recompute the summary against the scoped rows so KPIs reconcile.
    s = dict(p.get("summary", {}))
    q_month = sum(int(u.get("queries") or 0) for u in users)
    qh_month = round(sum(float(u.get("query_hours") or 0.0) for u in users), 1)
    genie_adopters = sum(1 for f in features if f.get("genie"))
    ai_adopters = sum(1 for f in features if f.get("ai"))
    if sel:
        # Scoped to the selected workspace(s): every total reflects ONLY those
        # rows (zero when they have no adoption activity — never fall back to
        # estate-wide).
        n_users = len({u.get("user") for u in users})
        s["mau"] = n_users
        s["wau"] = min(n_users, int(s.get("wau") or 0))
        s["num_workspaces"] = len({f.get("workspace") for f in features})
        s["total_queries_month"] = q_month
        s["query_hours_month"] = qh_month
    else:
        # Estate-wide: KEEP the payload's real total_queries/query_hours (an
        # honest COUNT(*)/SUM over ALL of query.history in live) — do NOT
        # substitute the sum over `users`, which is capped at the TOP-50 users
        # and would badly undercount a large estate (e.g. 400 users → only the
        # top 50's slice shown). Only apply the demo-mode window factor.
        if factor < 1.0 and s.get("mau"):
            s["mau"] = max(1, round(int(s.get("mau") or 0) * factor))
        s["total_queries_month"] = round(int(s.get("total_queries_month") or 0) * factor)
        s["query_hours_month"] = round(float(s.get("query_hours_month") or 0.0) * factor, 1)
    s["genie_adopters"] = genie_adopters
    s["ai_adopters"] = ai_adopters
    s["avg_breadth"] = round(sum(f["breadth"] for f in features) / len(features), 1) if features else 0.0
    p["summary"] = s
    return p


# ---------------------------------------------------------------------------
# API — Ask Genie  (SPEC §11; gated by features.genie — inert when off)
# ---------------------------------------------------------------------------

class GenieAskRequest(BaseModel):
    question: str


@app.post("/api/genie/ask")
async def genie_ask(req: GenieAskRequest) -> Any:
    """Stream a Genie answer (SSE) grounded in the cost/usage data.

    Gated by features.genie: when the flag is OFF the endpoint is INERT — it
    returns 404 and makes NO Genie/model call (SPEC §10). When ON:
      * live  — call the Genie Conversation API against genie_space_id.
      * demo  — return a canned streamed answer from data.genie_answers,
                grounded in the seeded dataset.

    Mirrors GridSense's SSE contract: a `meta` event (matched question + chart +
    follow-ups + source) then `token` events, then `done`.
    """
    if not _FEATURES["genie"]:
        # Inert server-side when the flag is off — no Genie/model call happens.
        return JSONResponse({"detail": "Genie feature disabled"}, status_code=404)

    from data import genie_answers, genie_client

    # LIVE mode must NEVER serve the canned demo answer engine — that's demo
    # content leaking into a real deployment. Two live paths only:
    #   (a) a Genie Space IS configured → stream the real Genie answer;
    #   (b) it ISN'T → an honest "not configured" message (no chart, no demo
    #       badge), telling the operator to set genie_space_id.
    # The demo answer engine (with its synthetic chart + numbers) is used ONLY
    # in demo mode.
    if USE_LIVE:
        if not genie_client.is_available():
            async def stream_unconfigured() -> Any:
                meta = {
                    "asked_question": req.question,
                    "source": "unavailable",
                    "chart": None,
                    "followups": [],
                }
                yield f"data: {json.dumps({'type': 'meta', 'payload': meta})}\n\n"
                msg = (
                    "Ask Genie isn't configured for this deployment yet. Set a Genie "
                    "Space id (genie_space_id / GENIE_SPACE_ID) on a Space that has "
                    "your cost & usage tables, and the app will answer here. Meanwhile "
                    "the tabs above (Overview, Cost, Workspaces, Query Advisor, "
                    "Recommendations) cover the same questions from live system tables."
                )
                for word in msg.split(" "):
                    yield f"data: {json.dumps({'type': 'token', 'token': word + ' '})}\n\n"
                    await asyncio.sleep(0.01)
                yield f"data: {json.dumps({'type': 'done'})}\n\n"
            return StreamingResponse(stream_unconfigured(), media_type="text/event-stream")

        # Genie configured: stream the real answer; on any error be honest, don't
        # fall back to a fabricated demo answer.
        try:
            live_text = await asyncio.to_thread(lambda: genie_client.ask(req.question))
        except Exception:  # noqa: BLE001
            live_text = None
        async def stream_live() -> Any:
            ok = bool(live_text)
            meta = {
                "asked_question": req.question,
                "source": "genie" if ok else "error",
                "chart": None,
                "followups": [],
            }
            yield f"data: {json.dumps({'type': 'meta', 'payload': meta})}\n\n"
            text = live_text if ok else (
                "Genie couldn't answer that just now (the Space returned no result "
                "or timed out). Try rephrasing, or use the tabs above for the same "
                "data from live system tables."
            )
            for word in text.split(" "):
                yield f"data: {json.dumps({'type': 'token', 'token': word + ' '})}\n\n"
                await asyncio.sleep(0.012)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return StreamingResponse(stream_live(), media_type="text/event-stream")

    # DEMO mode — the grounded canned answer engine (with its synthetic chart).
    result = genie_answers.answer(req.question)
    answer_text: str = result["text"]

    async def stream() -> Any:
        meta = {k: v for k, v in result.items() if k != "text"}
        meta["asked_question"] = req.question
        meta["source"] = "synthetic"
        meta["caveat"] = genie_answers.CAVEAT
        yield f"data: {json.dumps({'type': 'meta', 'payload': meta})}\n\n"
        for word in answer_text.split(" "):
            yield f"data: {json.dumps({'type': 'token', 'token': word + ' '})}\n\n"
            await asyncio.sleep(0.015)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# API — Data Quality Monitoring  (SPEC §12; gated by features.dqm — 404 when off)
# ---------------------------------------------------------------------------

@app.get("/api/dqm")
def get_dqm(
    monitor_type: str = Query("all"),
    quality: str = Query("all"),
    freshness: str = Query("all"),
    search: str = Query(""),
) -> dict[str, Any]:
    """Lakehouse Monitoring inventory + the DATA_QUALITY_MONITORING cost tie-in.

    Gated by features.dqm: when OFF the endpoint is ABSENT/INERT — 404, no data
    read (SPEC §10, §12). When ON, returns the monitored tables (demo mode: a
    representative set) plus the billing tie-in + prune hint.
    """
    if not _FEATURES["dqm"]:
        return JSONResponse({"detail": "Data Quality Monitoring feature disabled"}, status_code=404)

    live_source = False
    dqm_cached_at = ""
    if USE_LIVE:
        from data import live

        try:
            # Serve the monitor inventory from the nightly gold snapshot; the
            # filters below are applied in Python, so gold + live are identical.
            payload, dqm_cached_at = _gold_or_compute("dqm", lambda: live.dqm_live(WAREHOUSE_ID))
            live_source = True
        except live.LiveError as e:
            d = e.detail or ""
            if "INSUFFICIENT_PERMISSIONS" in d or "SELECT" in d or "does not exist" in d.lower():
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Data Quality tab needs read access to the Data Quality Monitoring results table. "
                        "Ask an account admin to grant the app SELECT on the TABLE (not the schema): "
                        "GRANT SELECT ON TABLE system.data_quality_monitoring.table_results "
                        "TO `<app-service-principal>`. A schema-level grant fails with "
                        "\"User does not have MANAGE on Schema\" even for metastore admins — "
                        "Databricks owns that schema and only delegates MANAGE on the table. "
                        "Until then this tab is unavailable (the app fails closed rather than showing sample data)."
                    ),
                )
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    else:
        from data import dqm

        payload = dqm.get_dqm(search=search)
    # Multi-select filtering applied uniformly here (so live + demo behave the
    # same and accept one-or-many values).
    mons = payload["monitors"]
    mt = _multi(monitor_type); ql = _multi(quality); fr = _multi(freshness)
    if mt:
        mons = [m for m in mons if m.get("monitor_type") in mt]
    if ql:
        mons = [m for m in mons if m.get("quality_status") in ql]
    if fr:
        mons = [m for m in mons if m.get("freshness") in fr]
    if search:
        s = search.lower()
        mons = [m for m in mons if s in str(m.get("fqn", "")).lower()]
    wrapped = _wrap(mons, live_source=live_source)
    wrapped["summary"] = payload["summary"]
    wrapped["by_workspace"] = payload["by_workspace"]
    wrapped["prune_candidates"] = payload["prune_candidates"]
    wrapped["billing_line"] = payload["billing_line"]
    wrapped["caveat"] = payload["caveat"]
    if dqm_cached_at:
        wrapped["cached_at"] = dqm_cached_at
    return wrapped


# ---------------------------------------------------------------------------
# API — tables
# ---------------------------------------------------------------------------

@app.get("/api/tables")
def get_tables(
    table_type: str = Query("all"),
    catalog: str = Query("all"),
    recommendation: str = Query("all"),
    needs_action: str = Query("all"),
    search: str = Query(""),
    status: str = Query("open"),    # open | acknowledged | wont_fix | in_progress | all (writeback)
    limit: int = Query(500),        # list cap; 0 = all ("Load all"). Summary covers every match.
) -> dict[str, Any]:
    if USE_LIVE:
        from data import live

        rows, live_source, _c = _cached_rows_or_live("tables", lambda: live.tables_live(WAREHOUSE_ID))
    else:
        rows = [dict(r, **{"health": _table_health(r)[0], "health_reason": _table_health(r)[1]}) for r in demo.get_tables()]
        live_source = False
    # Every catalog in the (unfiltered) estate, for the filter dropdown — the
    # shipped list is capped, so the UI can't derive the options from rows.
    all_catalogs = sorted({str(r.get("catalog")) for r in rows if r.get("catalog")})
    tt = _multi(table_type); cat = _multi(catalog); rec = _multi(recommendation)
    if tt:
        rows = [r for r in rows if r["table_type"] in tt]
    if cat:
        rows = [r for r in rows if r["catalog"] in cat]
    if rec:
        rows = [r for r in rows if r["recommendation"] in rec]
    if needs_action != "all":
        if needs_action == "needs-action":
            rows = [r for r in rows if r["needs_action"]]
        elif needs_action == "optimize":
            rows = [r for r in rows if r["needs_optimize"]]
        elif needs_action == "vacuum":
            rows = [r for r in rows if r["needs_vacuum"]]
        elif needs_action == "clustering":
            rows = [r for r in rows if r["needs_clustering"]]
        elif needs_action == "cold":
            rows = [r for r in rows if r.get("cold")]
    if search:
        s = search.lower()
        rows = [r for r in rows if s in r["fqn"].lower()]
    # Acknowledge/ignore overlay (writeback). Filter by requested status
    # (default 'open'); key on the table fqn; magnitude = size_gb (a table growing
    # materially past ack-time re-alerts).
    # Summary before ack-filter (facts view — acking shouldn't shrink KPI totals).
    summary = demo.get_tables_summary(rows)
    acks = _acks_for("table")
    ack_sink = _new_counts_sink(acks)
    rows = _apply_acks(
        rows, source="table", key_of=lambda r: r.get("fqn", ""),
        metric_field="size_gb", status_filter=status, acks=acks, counts_sink=ack_sink,
    )
    capped = None
    if limit and len(rows) > limit:
        # Keep the most relevant first: rows needing action, then largest.
        rows = sorted(rows, key=lambda r: (not r.get("needs_action"), -float(r.get("size_gb") or 0)))
        capped = {"shown": limit, "total": len(rows)}
        rows = rows[:limit]
    payload = _wrap(rows, live_source=live_source)
    payload["summary"] = summary
    payload["catalogs"] = all_catalogs
    if capped:
        payload["list_capped"] = capped
    if ack_sink is not None:
        payload["ack_counts"] = ack_sink
    return payload


@app.get("/api/tables/{fqn:path}")
def get_table_detail(fqn: str) -> dict[str, Any]:
    live_source = False
    if USE_LIVE:
        from data import live

        try:
            detail = _short_cache(f"table:{fqn}", lambda: live.table_detail_live(WAREHOUSE_ID, fqn))
            live_source = True
        except live.LiveError as e:
            # A live read FAILURE (e.g. ungranted DESCRIBE) is not "table doesn't
            # exist" — surface it as 503 with the cause, matching get_workspace_detail.
            # A 404 here would falsely tell the user the table is gone.
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    else:
        detail = demo.get_table_detail(fqn)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Unknown table: {fqn}")
    return _wrap(detail, live_source=live_source)


# ---------------------------------------------------------------------------
# API — governance
# ---------------------------------------------------------------------------

@app.get("/api/governance")
def get_governance(status: str = Query("all")) -> dict[str, Any]:
    live_source = False
    if USE_LIVE:
        from data import live

        try:
            # Governance aggregates several heavy scans (classification + per-ws tag
            # coverage + external-table share + admin tiles) — ~109s cold on the
            # a large live estate. GOLD-first: the nightly job precomputes it, so a
            # gold-backed estate serves in ~1s; else fall through to the persistent
            # two-tier cache. The cheap status filter still applies per-request below.
            report, gov_cached_at = _gold_or_compute(
                "governance", lambda: live.governance_live(WAREHOUSE_ID, INGEST_SCHEMA))
            live_source = True
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
    else:
        report = demo.get_governance_report()
        gov_cached_at = ""
    st = _multi(status)
    if st:
        report = {**report, "tiles": [t for t in report["tiles"] if t["status"] in st]}
    wrapped = _wrap(report, live_source=live_source)
    wrapped["last_refreshed"] = report.get("last_refreshed", "")
    wrapped["pending_admin"] = report.get("pending_admin", [])
    # Gold snapshot time (when served from gold) → UI "as of" honesty.
    if gov_cached_at:
        wrapped["cached_at"] = gov_cached_at
    return wrapped


# ---------------------------------------------------------------------------
# API — operational cost lenses
# ---------------------------------------------------------------------------

@app.get("/api/overview/lenses")
def get_overview_lenses() -> dict[str, Any]:
    """Cost re-expressed the way a platform team operates: by workload type,
    Classic vs Serverless, Production / Non-production, environment tier and
    naming-convention group. Live: one trailing-30-day billing read so every lens
    reconciles to the same total (gold-first, cached). Demo: the seeded split."""
    if USE_LIVE:
        from data import lenses, live

        def _build():
            jobs_p, _ = _gold_or_compute("jobs", lambda: __import__("data.jobs", fromlist=["jobs_live"]).jobs_live(WAREHOUSE_ID))
            pipes_p, _ = _gold_or_compute("pipelines", lambda: __import__("data.pipelines", fromlist=["pipelines_live"]).pipelines_live(WAREHOUSE_ID))
            return lenses.lenses_live(WAREHOUSE_ID, (jobs_p or {}).get("jobs"), (pipes_p or {}).get("pipelines"))

        try:
            payload, cached_at = _gold_or_compute("lenses", _build)
        except live.LiveError as e:
            raise HTTPException(status_code=503, detail=f"live read failed — {e.source}: {e.detail}")
        env = _wrap(payload, live_source=True)
        if cached_at:
            env["cached_at"] = cached_at
        return env
    from data import grouping
    out = dict(demo.get_overview_lenses())
    out.setdefault("groups_configured", grouping.groups_configured())
    out.setdefault("window", "this month (sample)")
    return _wrap(out, live_source=False)


# ---------------------------------------------------------------------------
# Static frontend (must be registered AFTER the API routes)
# ---------------------------------------------------------------------------

_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

if FRONTEND_DIST.exists():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIST / "index.html"), headers=_NO_CACHE_HEADERS)

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> Any:
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        target = FRONTEND_DIST / full_path
        if target.is_file():
            return FileResponse(str(target), headers=_NO_CACHE_HEADERS)
        return FileResponse(str(FRONTEND_DIST / "index.html"), headers=_NO_CACHE_HEADERS)

else:
    @app.get("/")
    def placeholder() -> dict[str, str]:
        return {
            "status": "frontend build not found",
            "hint": "Run `cd frontend && npm run build` then restart the server.",
        }
