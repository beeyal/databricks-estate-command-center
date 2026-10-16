"""Nightly GOLD-table precompute — the app's fast/cheap serving layer.

Why this exists (a customer review #17 + R3 research 2026-07-20): there is NO GA system
table that gives per-table size/file-count in bulk, and scanning system.billing /
running DESCRIBE DETAIL at request time is slow + costs a warehouse spin-up on
every page load. So a scheduled nightly job precomputes everything ONCE into
Delta "gold" tables, and the app reads those instantly (a small serverless SQL
warehouse, sub-second, scale-to-zero between reads). This is the same pattern
Databricks' own internal FinOps dashboards use.

Two gold tables, both in ``<gold_schema>`` (config ``gold_schema``, e.g.
"main.finops_gold"):

  * ``finops_gold``            (key STRING, payload STRING json, snapshot_at TS)
      one row per estate payload key (overview, workspaces, tables, adoption, …).
  * ``finops_gold_table_sizes`` (catalog, schema, table, size_bytes, num_files,
      snapshot_date DATE) — the per-table size census that has no system table.
      Built by iterating information_schema.tables → DESCRIBE DETAIL (metadata
      only, no data scan), parallelised. Feeds Tables sizes + the cold-table
      (large + rarely-read) view (#15).

Run as the scheduled ``finops_gold_sync`` job (see databricks.yml) or by hand:
    python -m data.gold            # needs DATABRICKS_WAREHOUSE_ID + FINOPS_GOLD_SCHEMA

Gold tables are the app's serving layer. (Admin-API data — budgets/tokens/SCIM —
is a separate Delta store written by data/admin_sync.)
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
from typing import Any, Callable

# sys.path bootstrap: when run as a bundle spark_python_task the parent holding
# the `data` package isn't on the path. Serverless runs the file through a
# notebook wrapper where ``__file__`` is UNDEFINED, so derive the file path
# robustly (argv[0] → cwd) instead of assuming __file__ exists.
_here = globals().get("__file__") or (sys.argv[0] if sys.argv and sys.argv[0] else "")
_base = os.path.dirname(os.path.dirname(os.path.abspath(_here))) if _here else os.getcwd()
for _p in (_base, os.path.dirname(_base), os.getcwd(), os.path.dirname(os.getcwd())):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from data import live  # noqa: E402


# Gold payload codec. Large payloads (jobs/tables/hub on big estates) can exceed
# the Statement Execution API's 16 MB statement-text limit when the JSON is inlined
# into an INSERT ... VALUES. So payloads over a threshold are gzip+base64 encoded
# with a sentinel prefix; JSON compresses ~10×, keeping the statement well under
# the cap. live.decode_gold_payload reverses it. Small payloads stay plain JSON so
# existing rows + a hand-inspected table remain readable. Sentinel is defined once
# in live and shared, so the reader and writer can never drift.
def _encode_payload(payload: Any) -> str:
    raw = json.dumps(payload, default=str)
    # Compress well below the 16 MB API limit (the SQL-escaped string is a bit
    # larger than raw, so trigger at ~4 MB to leave generous headroom).
    if len(raw) <= live.GOLD_INLINE_MAX_BYTES:
        return raw
    import base64
    import gzip
    packed = base64.b64encode(gzip.compress(raw.encode("utf-8"), 6)).decode("ascii")
    return live.GOLD_GZIP_SENTINEL + packed


_GOLD_WORKERS = 4


def is_degraded_payload(payload: Any) -> bool:
    """True for a fail-soft builder's error payload: an EMPTY primary list plus a
    ``message`` explaining why (jobs / warehouses / clusters return this shape
    instead of raising). Such payloads must never be cached or snapshotted."""
    if not isinstance(payload, dict) or not payload.get("message"):
        return False
    for k in ("jobs", "pipelines", "warehouses", "clusters"):
        if k in payload:
            return not payload.get(k)
    return False


def _ingest_schema() -> str:
    """Optional admin-sync ingest schema for governance's admin-API panels.
    Blank ⇒ governance still builds (the system-table portion); the admin panels
    then show their 'run finops_admin_sync' notice, same as a live read."""
    return str(os.environ.get("FINOPS_INGEST_SCHEMA") or "")


def _jobs_live(warehouse_id: str) -> Any:
    """Jobs payload for gold — the app's own jobs.jobs_live (unfiltered)."""
    from data import jobs
    return jobs.jobs_live(warehouse_id)


def _lenses_live(warehouse_id: str, gold_schema: str) -> Any:
    from data import lenses
    jobs = live.read_gold(warehouse_id, gold_schema, "jobs")
    pipes = live.read_gold(warehouse_id, gold_schema, "pipelines")
    return lenses.lenses_live(warehouse_id, (jobs[0] if jobs else {}).get("jobs"),
                              (pipes[0] if pipes else {}).get("pipelines"))


def _hub_live(warehouse_id: str, gold_schema: str = "") -> Any:
    """Recommendation hub for gold — install the live provider, then build the
    UNFILTERED hub (all defaults). The app applies workspace/priority/category
    filters in-process over this snapshot, so the unfiltered payload is the one
    to cache (mirrors app.py's `_live_payload_cached("hub", …)` unfiltered path)."""
    from data import rec_hub
    from data.rec_hub_live import LiveProvider
    # Built AFTER the other payloads (see run_gold) with the gold schema, so its
    # inputs (workspaces, queries, tables, access, cost drivers, classification)
    # are read from the snapshots this same run just wrote instead of being
    # recomputed a second time.
    rec_hub.set_provider(LiveProvider(warehouse_id, gold_schema))
    return rec_hub.build_hub()


# ---------------------------------------------------------------------------
# Estate payload builders — reuse the app's own live functions verbatim so gold
# and a direct live read produce identical shapes.
# ---------------------------------------------------------------------------
def _payload_builders(warehouse_id: str) -> dict[str, Callable[[], Any]]:
    return {
        "overview": lambda: live.overview_live(warehouse_id),
        "cost-drivers": lambda: live.cost_drivers_live(warehouse_id, None),
        "workspaces": lambda: live.workspaces_live(warehouse_id),
        "tables": lambda: live.tables_live(warehouse_id),
        "queries": lambda: live.queries_live(warehouse_id),          # 7d (also feeds the hub)
        "queries:1d": lambda: live.queries_live(warehouse_id, 1),    # Query Advisor 24h chip (default)
        "queries:30d": lambda: live.queries_live(warehouse_id, 30),  # Query Advisor 30d chip
        "adoption": lambda: live.adoption_live(warehouse_id),
        "classification": lambda: live.classification_live(warehouse_id),
        "dqm": lambda: live.dqm_live(warehouse_id),
        "genie-cost": lambda: live.genie_cost_live(warehouse_id, None),
        "ai-cost": lambda: live.ai_cost_live(warehouse_id, None),
        "access-grants": lambda: live.access_grants_live(warehouse_id),
        # jobs / hub / governance — the three heaviest live scans. Precompute the
        # UNFILTERED payload (the app applies workspace/priority/status filters
        # in-process over the snapshot, exactly as it does off the runtime cache).
        "jobs": lambda: _jobs_live(warehouse_id),
        "pipelines": lambda: __import__("data.pipelines", fromlist=["pipelines_live"]).pipelines_live(warehouse_id),
        "governance": lambda: live.governance_live(warehouse_id, _ingest_schema()),
    }
    # NB: Warehouse Monitor + Cluster Efficiency are NOT snapshot payloads here —
    # they use the DAILY-GRAIN ACCUMULATORS (build_warehouse_daily /
    # build_cluster_daily) instead, so their time filters re-window from history
    # that outlives system-table retention rather than a single frozen window.


def _sql_str(s: str) -> str:
    """Single-quote-escape for embedding in a SQL string literal."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _ensure_managed_delta(warehouse_id: str, fqn: str, cols: str) -> None:
    """CREATE a managed Delta table, then BEST-EFFORT enable Predictive
    Optimization + auto Liquid Clustering. The PO/LC step is separate + wrapped:
    `CLUSTER BY AUTO` in the CREATE hard-fails on a metastore where PO isn't
    enabled (CLUSTER_BY_AUTO_REQUIRES_PREDICTIVE_OPTIMIZATION, ES-1558331) — that
    would abort the whole job. This way the table always gets created; PO/LC is a
    bonus where available."""
    live._run(warehouse_id, f"CREATE TABLE IF NOT EXISTS {fqn} ({cols}) USING DELTA",
              f"ensure {fqn}")
    try:
        live._run(warehouse_id,
                  f"ALTER TABLE {fqn} SET TBLPROPERTIES ('delta.predictiveOptimization' = 'ENABLE')",
                  f"enable PO on {fqn}")
        live._run(warehouse_id, f"ALTER TABLE {fqn} CLUSTER BY AUTO", f"enable LC on {fqn}")
    except Exception:  # noqa: BLE001 — PO/LC not available here; the plain table still works
        pass


# ---------------------------------------------------------------------------
# Table-size census (the piece with no system table — R3). Iterate
# information_schema.tables, DESCRIBE DETAIL each (metadata-only, no data scan),
# in parallel, into finops_gold_table_sizes.
# ---------------------------------------------------------------------------
_SIZE_WORKERS = 12          # parallel DESCRIBE DETAIL calls
_SIZE_TABLE_LIMIT = 5000    # safety cap on a very large estate


def _list_tables(warehouse_id: str) -> list[tuple[str, str, str]]:
    # Exclude system/samples catalogs (they're not customer data and mostly fail
    # DESCRIBE DETAIL → wasted calls), matching tables_live. Deterministic ORDER
    # BY so the 5000-row cap picks the SAME tables run-to-run (was arbitrary).
    rows = live._run(warehouse_id,
        "SELECT table_catalog c, table_schema s, table_name t "
        "FROM system.information_schema.tables "
        "WHERE table_type IN ('MANAGED','EXTERNAL') "
        "AND table_schema <> 'information_schema' "
        "AND table_catalog NOT IN ('system','samples') "
        "ORDER BY table_catalog, table_schema, table_name "
        f"LIMIT {_SIZE_TABLE_LIMIT}",
        "information_schema.tables (size census inventory)")
    return [(str(r.get("c")), str(r.get("s")), str(r.get("t"))) for r in rows]


def _describe_size(warehouse_id: str, cat: str, sch: str, tbl: str) -> dict[str, Any] | None:
    """DESCRIBE DETAIL one table → size + files. None on any failure (non-Delta,
    ungranted, dropped) — those tables are simply skipped from the census."""
    try:
        det = live._run(warehouse_id, f"DESCRIBE DETAIL `{cat}`.`{sch}`.`{tbl}`", "DESCRIBE DETAIL (size)")
    except Exception:
        return None
    if not det:
        return None
    d = det[0]
    # partitionColumns / clusteringColumns come back as JSON-ish strings like
    # "[]" or '["col"]'; treat any non-empty list as present. PO is in properties.
    def _has_list(v: Any) -> bool:
        s = str(v or "").strip()
        return s not in ("", "[]", "null", "None")
    props = str(d.get("properties") or "")
    po = ("delta.predictiveoptimization" in props.lower() and "disable" not in props.lower())
    return {
        "catalog": cat, "schema": sch, "table": tbl,
        "size_bytes": round(live._f(d.get("sizeInBytes"))),
        "num_files": int(live._f(d.get("numFiles"))),
        "partitioned": _has_list(d.get("partitionColumns")),
        "clustered": _has_list(d.get("clusteringColumns")) or str(d.get("clusterByAuto")).lower() == "true",
        "po_enabled": po,
    }


def build_table_sizes(warehouse_id: str, gold_schema: str) -> dict[str, Any]:
    """Rebuild finops_gold_table_sizes for today. Returns a status dict."""
    live._BATCH = True
    tables = _list_tables(warehouse_id)
    sizes: list[dict[str, Any]] = []
    # Parallelise the per-table DESCRIBE DETAIL — metadata-only, so it's I/O-bound
    # on the warehouse, not a data scan. One bad table never fails the batch.
    with concurrent.futures.ThreadPoolExecutor(max_workers=_SIZE_WORKERS) as ex:
        futs = [ex.submit(_describe_size, warehouse_id, c, s, t) for (c, s, t) in tables]
        for f in concurrent.futures.as_completed(futs):
            r = f.result()
            if r:
                sizes.append(r)

    # Managed Delta + best-effort PO/LC (so the app's own tables follow the
    # managed + auto-maintenance guidance it recommends, without bricking the job
    # on a PO-less metastore).
    _ensure_managed_delta(warehouse_id, f"{gold_schema}.finops_gold_table_sizes",
        "catalog STRING, schema STRING, table STRING, size_bytes BIGINT, "
        "num_files BIGINT, partitioned BOOLEAN, clustered BOOLEAN, po_enabled BOOLEAN, "
        "snapshot_date DATE")
    # Add the new columns to a pre-existing table (best-effort; each wrapped so an
    # "already exists" error is a harmless no-op — some runtimes reject the
    # IF NOT EXISTS form, so we rely on the try/except instead).
    for col in ("partitioned BOOLEAN", "clustered BOOLEAN", "po_enabled BOOLEAN"):
        try:
            live._run(warehouse_id,
                f"ALTER TABLE {gold_schema}.finops_gold_table_sizes ADD COLUMN {col}",
                f"add {col}")
        except Exception:  # noqa: BLE001
            pass
    # Idempotent for the day: clear today's snapshot then insert. History for
    # prior days is retained → free growth-over-time trends.
    live._run(warehouse_id,
        f"DELETE FROM {gold_schema}.finops_gold_table_sizes WHERE snapshot_date = current_date()",
        "clear today's sizes")
    # Batch inserts to keep the statement count sane on large estates.
    B = 500
    for i in range(0, len(sizes), B):
        chunk = sizes[i:i + B]
        values = ", ".join(
            f"({_sql_str(r['catalog'])}, {_sql_str(r['schema'])}, {_sql_str(r['table'])}, "
            f"{r['size_bytes']}, {r['num_files']}, {str(r['partitioned']).lower()}, "
            f"{str(r['clustered']).lower()}, {str(r['po_enabled']).lower()}, current_date())"
            for r in chunk)
        live._run(warehouse_id,
            f"INSERT INTO {gold_schema}.finops_gold_table_sizes "
            "(catalog, schema, table, size_bytes, num_files, partitioned, clustered, po_enabled, snapshot_date) "
            f"VALUES {values}", f"write sizes {i}-{i + len(chunk)}")
    return {"tables_seen": len(tables), "tables_sized": len(sizes)}


# ---------------------------------------------------------------------------
# Daily-grain accumulators (Warehouse Monitor + Cluster Efficiency).
#
# Unlike the finops_gold payloads (which are full-snapshot trailing-window
# aggregates, replaced each night), these tables ACCUMULATE one row per entity
# per day and keep history. That gives the time filters real depth AND lets the
# app look back further than system.query.history / system.compute.node_timeline
# themselves retain — the whole point of an accumulator.
#
# Re-windowing rules the reader relies on (see live.warehouse_daily_agg /
# cluster_daily_agg):
#   * ADDITIVE facts (runs, cost, DBUs, bytes, spill) → SUM over the window.
#   * RATIOS are stored as numerator + denominator per day (queued_ms/duration_ms;
#     cpu_num/idle_num over sample_count) → SUM both, divide once. Never store a
#     pre-divided daily ratio (averaging ratios is wrong).
#   * PERCENTILES (p50/p95) are NOT stored — they can't be re-aggregated from
#     daily values. The reader computes them live for the picked window (exact).
#
# Idempotent for the day: we recompute the trailing REFRESH_DAYS days each run
# (catches late-arriving usage rows) and delete-then-insert exactly those days,
# leaving all older history intact.
# ---------------------------------------------------------------------------
_ACCUM_REFRESH_DAYS = 2   # re-derive the last N days each run (late-arrival catch)


def build_warehouse_daily(warehouse_id: str, gold_schema: str) -> dict[str, Any]:
    """Accumulate one row per (warehouse, workspace, day) of additive facts +
    ratio numerators/denominators from system.query.history. Idempotent for the
    refreshed days; older days retained."""
    fqn = f"{gold_schema}.finops_gold_warehouse_daily"
    _ensure_managed_delta(warehouse_id, fqn,
        "usage_date DATE, warehouse_id STRING, workspace_id STRING, runs BIGINT, "
        "duration_ms BIGINT, queued_ms BIGINT, spilled_bytes BIGINT, read_bytes BIGINT, "
        "distinct_users BIGINT, cost_proxy DOUBLE")
    # Delete the window we're about to rewrite, then insert it fresh.
    live._run(warehouse_id,
        f"DELETE FROM {fqn} WHERE usage_date >= dateadd(day,-{_ACCUM_REFRESH_DAYS},current_date())",
        "clear warehouse_daily window")
    live._run(warehouse_id,
        f"INSERT INTO {fqn} "
        "SELECT CAST(start_time AS DATE) usage_date, compute.warehouse_id, "
        "CAST(workspace_id AS STRING) workspace_id, COUNT(*) runs, "
        "SUM(total_duration_ms) duration_ms, SUM(waiting_at_capacity_duration_ms) queued_ms, "
        "SUM(spilled_local_bytes) spilled_bytes, SUM(read_bytes) read_bytes, "
        "approx_count_distinct(executed_by) distinct_users, "
        f"SUM(total_duration_ms)/3600000.0*{live.DBU_HOUR_PROXY_USD} cost_proxy "
        "FROM system.query.history "
        f"WHERE start_time >= dateadd(day,-{_ACCUM_REFRESH_DAYS},current_date()) "
        # Exclude the incomplete CURRENT day — a partial today would land as a
        # near-zero daily row and dip every trend chart. Only complete days.
        "AND start_time < current_date() "
        "AND compute.warehouse_id IS NOT NULL "
        "GROUP BY CAST(start_time AS DATE), compute.warehouse_id, CAST(workspace_id AS STRING)",
        "write warehouse_daily")
    return {"table": fqn}


def build_cluster_daily(warehouse_id: str, gold_schema: str) -> dict[str, Any]:
    """Accumulate one row per (cluster, day) of node_timeline CPU/mem numerators
    + sample counts (for correct re-windowed averages) and daily billing DBUs.
    Cluster CONFIG stays in the snapshot payload; this is the utilisation history."""
    from data.clusters import IDLE_CPU_LOW  # single source of the idle cutoff
    idle_pct = int(IDLE_CPU_LOW * 100)
    fqn = f"{gold_schema}.finops_gold_cluster_daily"
    _ensure_managed_delta(warehouse_id, fqn,
        "usage_date DATE, cluster_id STRING, cpu_sum DOUBLE, mem_sum DOUBLE, "
        "idle_samples BIGINT, samples BIGINT, uptime_min BIGINT, dbus DOUBLE")
    live._run(warehouse_id,
        f"DELETE FROM {fqn} WHERE usage_date >= dateadd(day,-{_ACCUM_REFRESH_DAYS},current_date())",
        "clear cluster_daily window")
    # Utilisation from node_timeline: store SUMs + sample counts so the reader can
    # form correct mean CPU/mem and idle-fraction over any window. uptime_min =
    # DISTINCT minutes with >=1 active node that day (size-independent wall-clock
    # uptime — one node_timeline row per node per minute, so COUNT(*) overcounts
    # multi-node clusters; the reader sums uptime_min across days).
    live._run(warehouse_id,
        f"INSERT INTO {fqn} "
        "SELECT CAST(nt.start_time AS DATE) usage_date, nt.cluster_id, "
        "SUM(nt.cpu_user_percent+nt.cpu_system_percent)/100.0 cpu_sum, "
        "SUM(nt.mem_used_percent)/100.0 mem_sum, "
        f"SUM(CASE WHEN (nt.cpu_user_percent+nt.cpu_system_percent) < {idle_pct} THEN 1 ELSE 0 END) idle_samples, "
        "COUNT(*) samples, "
        "COUNT(DISTINCT nt.start_time) uptime_min, "
        "COALESCE(MAX(c.dbus),0) dbus "
        "FROM system.compute.node_timeline nt "
        "LEFT JOIN (SELECT usage_metadata.cluster_id cid, CAST(usage_start_time AS DATE) d, "
        "  SUM(usage_quantity) dbus FROM system.billing.usage "
        f"  WHERE usage_start_time >= dateadd(day,-{_ACCUM_REFRESH_DAYS},current_date()) "
        "  AND usage_start_time < current_date() "
        "  AND usage_metadata.cluster_id IS NOT NULL GROUP BY 1,2) c "
        "  ON c.cid = nt.cluster_id AND c.d = CAST(nt.start_time AS DATE) "
        f"WHERE nt.start_time >= dateadd(day,-{_ACCUM_REFRESH_DAYS},current_date()) "
        # Exclude the incomplete current day (partial → near-zero daily row).
        "AND nt.start_time < current_date() "
        "GROUP BY CAST(nt.start_time AS DATE), nt.cluster_id",
        "write cluster_daily")
    return {"table": fqn}


# ---------------------------------------------------------------------------
# Full nightly run: estate payloads + table sizes.
# ---------------------------------------------------------------------------
def run_gold(warehouse_id: str, gold_schema: str) -> dict[str, str]:
    if not warehouse_id:
        raise SystemExit("set DATABRICKS_WAREHOUSE_ID")
    if not gold_schema:
        raise SystemExit("set FINOPS_GOLD_SCHEMA (e.g. main.finops_gold)")
    # Validate the schema identifier before interpolating it into DDL/DML (matches
    # the reader's guard in live._gold_table_sizes; fail-closed on a bad value).
    if not live._SCHEMA_RE.match(gold_schema):
        raise SystemExit(f"FINOPS_GOLD_SCHEMA must be a catalog.schema identifier, got: {gold_schema!r}")
    live._BATCH = True  # tolerate long estate-wide scans in the batch job

    status: dict[str, str] = {}

    # The initial CREATE is the one step before the per-key try-loop, so guard it
    # with an addressable message rather than a raw traceback if the SP lacks
    # CREATE on the gold schema.
    try:
        # Auto-create the schema (best-effort) so the customer doesn't have to
        # pre-create it — they only need CREATE on the catalog. Mirrors the
        # self-provisioning writeback + Lakebase backends.
        try:
            live._run(warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {gold_schema}", "gold: ensure schema")
        except Exception:  # noqa: BLE001 — schema may pre-exist / be admin-managed
            pass
        _ensure_managed_delta(warehouse_id, f"{gold_schema}.finops_gold",
            "key STRING, payload STRING, snapshot_at TIMESTAMP")
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            f"cannot create {gold_schema}.finops_gold — the job's service principal needs "
            f"CREATE on the catalog (to make the schema) + CREATE/MODIFY on {gold_schema}. "
            f"Underlying error: {str(e)[:160]}")

    def _build_and_write(key: str, build: Callable[[], Any]) -> str:
        try:
            payload = build()
            # A fail-soft builder returns an EMPTY payload + message when its
            # required read failed (missing grant, result-size limit, timeout).
            # Writing that as the snapshot made the app serve the error all day —
            # even after the grant was fixed. Keep the previous good snapshot.
            if is_degraded_payload(payload):
                return f"skipped (kept previous snapshot): {str(payload.get('message'))[:160]}"
            body = _sql_str(_encode_payload(payload))
            # One atomic MERGE instead of DELETE-then-INSERT: no window where the
            # key is missing (a request in that window fell through to a full live
            # scan) and one statement instead of two.
            live._run(warehouse_id,
                f"MERGE INTO {gold_schema}.finops_gold t "
                f"USING (SELECT {_sql_str(key)} AS key, {body} AS payload, current_timestamp() AS snapshot_at) s "
                "ON t.key = s.key "
                "WHEN MATCHED THEN UPDATE SET t.payload = s.payload, t.snapshot_at = s.snapshot_at "
                "WHEN NOT MATCHED THEN INSERT (key, payload, snapshot_at) VALUES (s.key, s.payload, s.snapshot_at)",
                f"write {key}")
            return "ok"
        except Exception as e:  # noqa: BLE001 — one bad key never fails the run
            return f"skipped: {str(e)[:120]}"

    # The payload builds are independent, so run a few at once (the batch job
    # used to build all 14 one after another). Bounded so we don't flood a small
    # warehouse; each build already parallelises its own reads where it can.
    builders = _payload_builders(warehouse_id)
    live.warm_auth()
    with concurrent.futures.ThreadPoolExecutor(max_workers=_GOLD_WORKERS, thread_name_prefix="gold") as ex:
        futs = {key: ex.submit(_build_and_write, key, build) for key, build in builders.items()}
        for key in builders:
            status[key] = futs[key].result()
    # The Recommendations hub aggregates the payloads above, so build it last,
    # reading them back from gold.
    status["hub"] = _build_and_write("hub", lambda: _hub_live(warehouse_id, gold_schema))
    # Cost lenses use the jobs + pipelines payloads for the workload split.
    status["lenses"] = _build_and_write("lenses", lambda: _lenses_live(warehouse_id, gold_schema))

    try:
        sz = build_table_sizes(warehouse_id, gold_schema)
        status["table_sizes"] = f"ok ({sz['tables_sized']}/{sz['tables_seen']} sized)"
    except Exception as e:  # noqa: BLE001
        status["table_sizes"] = f"skipped: {str(e)[:120]}"

    # Daily-grain accumulators for the time-filtered tabs (history that outlives
    # system-table retention). Each is independent + wrapped so one never fails
    # the run.
    for name, fn in (("warehouse_daily", build_warehouse_daily),
                     ("cluster_daily", build_cluster_daily)):
        try:
            fn(warehouse_id, gold_schema)
            status[name] = "ok"
        except Exception as e:  # noqa: BLE001
            status[name] = f"skipped: {str(e)[:120]}"

    return status


def _load_app_yaml_env() -> None:
    """Load the app's app.yaml env into os.environ so the job shares the app's
    config (warehouse + gold_schema). Mirrors admin_sync; only sets keys not
    already present."""
    from pathlib import Path

    # __file__ is undefined under the serverless notebook wrapper — reuse the
    # _base resolved at import time (falls back to cwd).
    p = Path(_base) / "app.yaml"
    if not p.exists():
        return
    try:
        import yaml

        doc = yaml.safe_load(p.read_text()) or {}
        for item in doc.get("env", []) or []:
            name, val = item.get("name"), item.get("value")
            if name and name not in os.environ and val is not None:
                os.environ[name] = str(val)
    except Exception:  # noqa: BLE001 - best-effort
        pass


if __name__ == "__main__":
    _load_app_yaml_env()
    # gold_schema always comes from config/env (never a positional) — the UI
    # self-provision path passes [warehouse_id, ingest_schema], so a positional
    # arg here would be the WRONG schema. warehouse_id may be positional or env.
    wh = sys.argv[1] if len(sys.argv) > 1 else (
        os.environ.get("DATABRICKS_WAREHOUSE_ID") or os.environ.get("FINOPS_WAREHOUSE_ID", ""))
    sch = os.environ.get("FINOPS_GOLD_SCHEMA", "")
    print(f"Building gold tables in {sch} via warehouse {wh} …")
    results = run_gold(wh, sch)
    for k, v in results.items():
        print(f"  {k:16} {v}")
    # Exit non-zero if EVERY payload failed/skipped, so the scheduled job surfaces
    # a hard failure to alerting instead of silently reporting success. (A partial
    # skip — e.g. the optional DQM grant — is tolerated: at least one "ok" passes.)
    oks = sum(1 for v in results.values() if str(v).startswith("ok"))
    if results and oks == 0:
        raise SystemExit("gold sync: every payload failed — see the statuses above")
