"""Live-mode executor (USE_LIVE=true).

Runs the validated SQL from ``data/queries.sql`` against the customer's own SQL
warehouse and maps the result rows into the SAME payload shapes the demo
generators return, so the frontend is identical in both modes.

Design rules (the safety contract):
  * READ-ONLY. Only SELECT / DESCRIBE run here. No DDL/DML anywhere.
  * FAIL-CLOSED. On any error (missing grant, warehouse down, bad column) we
    raise ``LiveError`` — the endpoint surfaces a clear message. We NEVER fall
    back to demo data in live mode, because mislabelled data is worse than an
    honest error.
  * Auth is the deployed Databricks App's own service principal via the SDK
    default credential chain (no PATs, no user creds baked in).

Only the endpoints listed in ``WIRED`` read live data today; everything else is
still served as clearly-marked sample data by app.py until wired here.
"""
from __future__ import annotations

import re
import time
from functools import lru_cache
from typing import Any

from data import ai_cost, genie_cost, recommender

# Endpoints that read real data in live mode. Grows as we wire more.
WIRED = {"genie-cost", "ai-cost", "cost-drivers", "workspaces", "overview", "dqm", "classification",
         "adoption", "queries", "tables", "access-principals", "access-grants", "access-risks",
         "recommendations", "warehouses", "clusters", "jobs"}

# Hard ceiling on how long we poll a single live query before failing closed.
# Live requests stay tight (150s); the roll-up job flips _BATCH so slow
# estate-wide scans (e.g. privileges on a huge account) can complete off-peak.
_MAX_WAIT_SECONDS = 150
_BATCH_MAX_WAIT_SECONDS = 1200
_BATCH = False

# Statement ids from system.query.history are UUID-ish hex-and-dash tokens.
# We validate request-supplied ids against this shape before interpolating them
# into SQL (defence against injection — quote-doubling isn't safe on Spark).
_STATEMENT_ID_RE = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{0,63}$")

# Databricks workspace ids are numeric. Any request-supplied workspace id that
# reaches raw SQL interpolation must match this before use — on Spark, backslash
# escaping defeats simple quote-wrapping, so we allowlist the shape instead.
_WORKSPACE_ID_RE = re.compile(r"^[0-9]{1,20}$")


def _safe_ws_id(value: str | None) -> str | None:
    """Return a request-supplied workspace id only if it's a plain numeric id,
    else None. Callers treat None as 'no scope filter' (fail-soft to the estate
    view) rather than erroring — and never interpolate an unvalidated value."""
    if value is None:
        return None
    v = str(value).strip()
    return v if _WORKSPACE_ID_RE.match(v) else None


class LiveError(Exception):
    """A live read failed. Carries the system object + a human-readable detail
    so the UI can tell the operator exactly what to grant/fix."""

    def __init__(self, source: str, detail: str):
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")


@lru_cache(maxsize=1)
def _client():  # lazy import so demo mode never needs the SDK
    try:
        from databricks.sdk import WorkspaceClient
    except Exception as e:  # pragma: no cover
        raise LiveError("databricks-sdk", f"SDK not available: {e}")
    return WorkspaceClient()


# INLINE results are capped by the Statement Execution API at 25 MiB — a big
# estate-wide scan (50k job runs, a wide grant graph) trips this with
# "Inline byte limit exceeded". When it does, we transparently re-run the SAME
# statement with EXTERNAL_LINKS disposition, which streams the full result via
# presigned URLs and has no such cap. Substring the API uses in that error:
_INLINE_LIMIT_MARKERS = ("inline byte limit", "disposition=inline", "result size of at most")


def _poll_to_success(w, resp, source: str):
    """Poll a submitted statement to SUCCEEDED (or fail closed). Returns the final response."""
    deadline = time.time() + (_BATCH_MAX_WAIT_SECONDS if _BATCH else _MAX_WAIT_SECONDS)
    while True:
        state_str = getattr(getattr(resp.status, "state", None), "value", str(getattr(resp.status, "state", "")))
        if state_str == "SUCCEEDED":
            return resp
        if state_str in ("FAILED", "CANCELED", "CLOSED"):
            msg = getattr(getattr(resp.status, "error", None), "message", "") or state_str
            raise LiveError(source, f"query did not succeed ({state_str}): {msg}")
        if time.time() > deadline:
            wait_s = _BATCH_MAX_WAIT_SECONDS if _BATCH else _MAX_WAIT_SECONDS
            raise LiveError(source, f"query still running after {wait_s}s — the estate is too large for a live scan; enable the nightly gold roll-up")
        time.sleep(3)
        try:
            resp = w.statement_execution.get_statement(resp.statement_id)
        except Exception as e:  # transient SDK/network error → fail-closed as LiveError, not a raw 500
            raise LiveError(source, f"polling failed: {e}")


def _run(warehouse_id: str, sql: str, source: str) -> list[dict[str, Any]]:
    """Execute one read-only statement and return rows as dicts. Fail-closed.

    Tries INLINE first (cheapest path); on the API's inline byte-limit error,
    retries with EXTERNAL_LINKS so large results still return complete."""
    if not warehouse_id:
        raise LiveError("warehouse", "no DATABRICKS_WAREHOUSE_ID configured for live mode")
    w = _client()
    try:
        resp = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id, statement=sql, wait_timeout="30s",
        )
        resp = _poll_to_success(w, resp, source)
    except LiveError as e:
        # Inline results are capped; fall back to EXTERNAL_LINKS for the same SQL.
        if any(m in str(e).lower() for m in _INLINE_LIMIT_MARKERS):
            return _run_external_links(w, warehouse_id, sql, source)
        raise
    except Exception as e:
        raise LiveError(source, f"query failed to execute: {e}")

    manifest = resp.manifest
    result = resp.result
    cols = [c.name for c in manifest.schema.columns] if manifest and manifest.schema else []
    rows = [dict(zip(cols, row)) for row in ((result.data_array if result and result.data_array else []) or [])]

    # Follow result chunks beyond the first inline one. Without this, large
    # results (e.g. the 50k-job run-metrics query, or a big grant graph) silently
    # truncate to the first chunk → misleading totals. Cap the chunk count so a
    # runaway result still fails closed rather than looping forever.
    stmt_id = resp.statement_id
    nxt = getattr(result, "next_chunk_index", None)
    chunks = 0
    while nxt is not None and chunks < 200:
        chunks += 1
        try:
            ch = w.statement_execution.get_statement_result_chunk_n(stmt_id, nxt)
        except Exception as e:  # noqa: BLE001
            # DATA HONESTY: a mid-paging failure means the result is INCOMPLETE.
            # Returning the rows gathered so far would silently under-report every
            # downstream SUM()/count (wrong dollar figures presented as complete) —
            # the opposite of this tool's contract. Fail closed (503) instead, so
            # the tab shows an honest "read failed" rather than a quiet undercount.
            raise LiveError(source, f"result paging failed after {chunks} chunk(s): {e}")
        rows.extend(dict(zip(cols, row)) for row in ((ch.data_array if ch and ch.data_array else []) or []))
        nxt = getattr(ch, "next_chunk_index", None)
    # A result still pointing to more chunks after the 200-chunk safety cap is
    # also incomplete — fail closed rather than serve a truncated total.
    if nxt is not None:
        raise LiveError(source, f"result exceeded the {chunks}-chunk paging cap — refine the query window")
    return rows


def _run_external_links(w, warehouse_id: str, sql: str, source: str) -> list[dict[str, Any]]:
    """Re-run a statement with EXTERNAL_LINKS disposition and stream the full
    result from presigned URLs (no inline size cap). JSON_ARRAY format → each
    link yields a list-of-lists we zip against the manifest columns."""
    import json as _json
    import urllib.request
    from databricks.sdk.service.sql import Disposition, Format

    try:
        resp = w.statement_execution.execute_statement(
            warehouse_id=warehouse_id, statement=sql, wait_timeout="30s",
            disposition=Disposition.EXTERNAL_LINKS, format=Format.JSON_ARRAY,
        )
        resp = _poll_to_success(w, resp, source)
    except LiveError:
        raise
    except Exception as e:
        raise LiveError(source, f"query failed to execute (external links): {e}")

    manifest = resp.manifest
    cols = [c.name for c in manifest.schema.columns] if manifest and manifest.schema else []

    def _fetch(url: str) -> list:
        # Presigned URLs are pre-authenticated — no auth header (and adding one
        # can cause the object store to reject the request). Fail closed on error.
        with urllib.request.urlopen(url, timeout=60) as fh:  # noqa: S310 (trusted DBSQL-issued URL)
            return _json.loads(fh.read().decode("utf-8")) or []

    rows: list[dict[str, Any]] = []
    stmt_id = resp.statement_id
    result = resp.result
    chunks = 0
    while result is not None:
        for link in (getattr(result, "external_links", None) or []):
            url = getattr(link, "external_link", None)
            if not url:
                continue
            for row in _fetch(url):
                rows.append(dict(zip(cols, row)))
        nxt = getattr(result, "next_chunk_index", None)
        if nxt is None:
            break
        chunks += 1
        if chunks >= 500:
            raise LiveError(source, f"external-links result exceeded the {chunks}-chunk cap — refine the query window")
        try:
            result = w.statement_execution.get_statement_result_chunk_n(stmt_id, nxt)
        except Exception as e:  # noqa: BLE001
            raise LiveError(source, f"external-links paging failed after {chunks} chunk(s): {e}")
    return rows


# Single source of truth for the $/DBU-hour proxy used when a resolved $ rate
# isn't available (warehouses / clusters / gold accumulator / query cost). A
# labelled ESTIMATE (~US list serverless-SQL rate), never a billed figure —
# retuning it here keeps every "cost_usd" proxy consistent. (Note: daily gold
# rows written before a retune keep their write-time rate.)
DBU_HOUR_PROXY_USD = 0.55


def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _arr(v: Any) -> list:
    """Coerce a SQL ARRAY cell to a Python list. The Statement Execution API
    serialises array columns (e.g. collect_set) as a JSON STRING, so iterating
    the raw value yields characters, not elements. Parse the string; pass a real
    list through; anything else → []."""
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            import json as _json
            out = _json.loads(v)
            return out if isinstance(out, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


# Gold payload codec (shared with data/gold.py's writer). Payloads larger than
# GOLD_INLINE_MAX_BYTES of JSON are stored gzip+base64 behind this sentinel so the
# INSERT statement text stays under the Statement Execution API's 16 MB limit.
GOLD_GZIP_SENTINEL = "gzip64:"
GOLD_INLINE_MAX_BYTES = 4_000_000  # ~4 MB raw JSON → inline; larger → compress


def decode_gold_payload(raw: Any):
    """Decode a gold ``payload`` cell: gzip+base64 when it carries the sentinel,
    else plain JSON (or an already-parsed value). Returns the payload object, or
    None if it can't be decoded."""
    import json as _json
    if isinstance(raw, str) and raw.startswith(GOLD_GZIP_SENTINEL):
        import base64
        import gzip
        try:
            packed = raw[len(GOLD_GZIP_SENTINEL):]
            return _json.loads(gzip.decompress(base64.b64decode(packed)).decode("utf-8"))
        except Exception:  # noqa: BLE001 — corrupt/partial row → treat as absent
            return None
    try:
        return _json.loads(raw) if isinstance(raw, str) else raw
    except Exception:  # noqa: BLE001
        return None


def read_gold(warehouse_id: str, gold_schema: str, key: str) -> tuple[Any, str] | None:
    """Read one precomputed estate payload from the nightly gold table
    (<gold_schema>.finops_gold). Returns (payload, snapshot_at_iso) or None when
    the gold schema isn't configured, the table/row is absent, or the read fails
    — the caller then computes the payload live (demo, or transiently in live mode
    before the first gold run / between a stale key and the next run). Fail-soft:
    a missing key never errors. NB gold is REQUIRED for live mode (app.py boot
    guard) — this transient fallback is a resilience path, not the serving path."""
    # Validate the (config-sourced) schema identifier before interpolation, to
    # match the file's allowlist-everywhere contract (_gold_table_sizes already
    # does this for the same value).
    if not gold_schema or not _SCHEMA_RE.match(gold_schema):
        return None
    try:
        rows = _run(warehouse_id,
            f"SELECT payload, CAST(snapshot_at AS STRING) snapshot_at "
            f"FROM {gold_schema}.finops_gold WHERE key = {key!r}",
            f"gold read {key}")
    except Exception:  # noqa: BLE001 — no gold table yet / ungranted → compute live
        return None
    if not rows:
        return None
    raw = rows[0].get("payload")
    if raw is None:
        return None
    payload = decode_gold_payload(raw)
    if payload is None:
        return None
    return payload, str(rows[0].get("snapshot_at") or "")


def read_admin(warehouse_id: str, schema: str, key: str) -> dict[str, Any] | None:
    """Read one admin-sync dataset by key from the Delta ``<schema>.finops_admin``
    store the daily finops_admin_sync job writes. Returns {"payload",
    "ingested_at"} or None when absent, so the caller fails closed with
    'run finops_admin_sync'."""
    # Validate the (config-sourced) schema identifier before interpolation.
    if not schema or not _SCHEMA_RE.match(schema):
        return None
    try:
        rows = _run(warehouse_id,
            f"SELECT payload, CAST(ingested_at AS STRING) ingested_at FROM {schema}.finops_admin WHERE key = {key!r}",
            f"{schema}.finops_admin")
    except LiveError:
        return None
    if not rows:
        return None
    import json
    try:
        payload = json.loads(rows[0].get("payload") or "null")
    except Exception:
        return None
    if payload is None:
        return None
    return {"payload": payload, "ingested_at": str(rows[0].get("ingested_at") or "")}


class AdminNotIngested(Exception):
    """Raised when an ingested dataset the caller needs isn't present yet."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)


def _require_admin(warehouse_id: str, schema: str, key: str) -> dict[str, Any]:
    """read_admin or raise AdminNotIngested (→ app returns an actionable 503)."""
    got = read_admin(warehouse_id, schema, key)
    if got is None:
        raise AdminNotIngested(key)
    return got


_PLATFORM_TOTAL_CACHE: dict[str, tuple[float, float]] = {}   # wh -> (value, monotonic_ts)
_PLATFORM_TOTAL_TTL = 3600.0   # 1h — this is a month-to-date sum, not point-in-time


def platform_spend_total(warehouse_id: str) -> float:
    """Total platform spend this month (all products) — for the '% of total
    spend' context on the Genie/AI pages. TTL-cached (one heavy scan) rather than
    @lru_cache'd for the process lifetime: an always-on app would otherwise freeze
    this month-to-date total on first call and, after month rollover, show last
    month's figure as if current."""
    now = time.monotonic()
    hit = _PLATFORM_TOTAL_CACHE.get(warehouse_id)
    if hit and (now - hit[1]) < _PLATFORM_TOTAL_TTL:
        return hit[0]
    rows = _run(warehouse_id,
        "SELECT SUM(u.usage_quantity*lp.pricing.effective_list.default) usd FROM system.billing.usage u "
        "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
        "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
        "WHERE u.usage_date>=date_trunc('MONTH',current_date())", "system.billing.usage (platform total)")
    val = round(_f(rows[0].get("usd")) if rows else 0.0, 2)
    _PLATFORM_TOTAL_CACHE[warehouse_id] = (val, now)
    return val


# ---------------------------------------------------------------------------
# Preflight — a cheap health check so misconfig surfaces on boot, not mid-demo.
# ---------------------------------------------------------------------------
_PREFLIGHT_TABLES = [
    ("system.billing.usage", "SELECT 1 FROM system.billing.usage LIMIT 1"),
    ("system.billing.list_prices", "SELECT 1 FROM system.billing.list_prices LIMIT 1"),
]


def preflight(warehouse_id: str) -> dict[str, Any]:
    """Check the warehouse + read access to each required system table. Returns
    a structured report (never raises) so /api/health can render it."""
    checks: list[dict[str, Any]] = []
    for name, sql in _PREFLIGHT_TABLES:
        try:
            _run(warehouse_id, sql, name)
            checks.append({"name": name, "ok": True, "error": None})
        except LiveError as e:
            checks.append({"name": name, "ok": False, "error": e.detail})
    return {"ok": all(c["ok"] for c in checks), "warehouse_id": warehouse_id, "checks": checks}


# ---------------------------------------------------------------------------
# Genie $ — the first fully-wired live endpoint (queries.sql Q1-Q3).
# ---------------------------------------------------------------------------
# Genie usage per user × workspace × surface, this month. INCLUDES the free tier
# (GENIE_FREE_USAGE SKU, present from 20 Jul 2026 — Databricks "Monitor and
# understand your Genie cost"). We deliberately include it: excluding it made
# free-tier-heavy surfaces (Genie Agents, Genie Spaces — which for many estates
# run almost entirely inside the 150 free DBUs/user allowance) show 0 DBUs and $0,
# i.e. "nobody uses Agents", which is FALSE and misleads the operator. So:
#   * total_dbus  = ALL Genie DBUs on the surface (real usage — the honest number)
#   * billed_dbus = usage on the priced Serverless Real-Time Inference SKU only
#   * free_dbus   = usage on the un-priced GENIE_FREE_USAGE SKU
#   * billed_list = list $ of the billed rows (free rows have NO list_prices match,
#                   so the LEFT JOIN yields NULL → contributes 0 automatically)
# genie_cost_live values free_dbus at the estate's effective billed rate to get a
# per-surface "list $"; the billed portion stays the actual charge (paid $). A
# free-absorbed surface then reads: big DBUs, big list $, $0 billable — the truth.
_GENIE_GROUND_TRUTH_SQL = """
SELECT
  date_format(date_trunc('MONTH', u.usage_date), 'yyyy-MM')   AS usage_month,
  CAST(u.workspace_id AS STRING)                              AS workspace,
  COALESCE(u.identity_metadata.run_as, u.identity_metadata.created_by, 'shared') AS user_identity,
  COALESCE(u.usage_metadata.genie.surface, 'UNKNOWN')         AS surface,
  SUM(u.usage_quantity)                                       AS total_dbus,
  SUM(CASE WHEN u.sku_name = 'GENIE_FREE_USAGE' THEN u.usage_quantity ELSE 0 END) AS free_dbus,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)   AS billed_list
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud
 AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
WHERE u.billing_origin_product = 'GENIE'
  AND u.usage_date >= date_trunc('MONTH', current_date())
GROUP BY 1, 2, 3, 4
"""


# Fallback that never references the newish usage_metadata.genie.surface struct
# (unpopulated in some regions/metastores — a hard schema error there, not just a
# null). Lets the Genie tab still render per-user/workspace totals.
_GENIE_GROUND_TRUTH_SQL_NOSURFACE = """
SELECT
  date_format(date_trunc('MONTH', u.usage_date), 'yyyy-MM')   AS usage_month,
  CAST(u.workspace_id AS STRING)                              AS workspace,
  COALESCE(u.identity_metadata.run_as, u.identity_metadata.created_by, 'shared') AS user_identity,
  'UNKNOWN'                                                   AS surface,
  SUM(u.usage_quantity)                                       AS total_dbus,
  SUM(CASE WHEN u.sku_name = 'GENIE_FREE_USAGE' THEN u.usage_quantity ELSE 0 END) AS free_dbus,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)   AS billed_list
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
WHERE u.billing_origin_product = 'GENIE'
  AND u.usage_date >= date_trunc('MONTH', current_date())
GROUP BY 1, 2, 3, 4
"""


# NB: free-tier absorbed usage used to be a separate query (_GENIE_FREE_USAGE_SQL)
# that excluded the free SKU from the main breakdown. That's now folded into
# _GENIE_GROUND_TRUTH_SQL (free_dbus column) so free-absorbed surfaces show real
# usage instead of $0 — genie_cost_live derives free_absorbed_usd from those rows.


def genie_cost_live(warehouse_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Real Genie cost by surface/user/workspace this month. Fetches ALL rows
    (the free allowance is per-user across workspaces) then scopes in Python via
    the shared assembler, so demo and live produce identical shapes.

    Degrades gracefully: if the usage_metadata.genie.surface field isn't present
    (near-zero Genie usage / newer field not in this metastore), fall back to
    surface-less totals so the tab renders instead of 503-ing (a customer review)."""
    try:
        raw = _run(warehouse_id, _GENIE_GROUND_TRUTH_SQL, "system.billing.usage (Genie)")
    except LiveError:
        raw = _run(warehouse_id, _GENIE_GROUND_TRUTH_SQL_NOSURFACE, "system.billing.usage (Genie, no surface)")
    # Resolve workspace IDs → friendly names (like every other tab via
    # _workspace_names). Without this the breakdown keys on the raw ID, so the
    # Workspaces drill-down — which scopes by the workspace NAME — matches nothing
    # and shows a false "$0 / no billable Genie spend" (fail-soft to {} → id kept).
    ws_names = _workspace_names(warehouse_id)
    # Effective billed Genie rate this month ($/DBU) = Σ billed list $ / Σ billed
    # DBUs (billed = total − free). Used to value the free DBUs on each surface so
    # a free-absorbed surface still shows a real "list $" (what it WOULD cost),
    # while its billable $ stays 0. Falls back to the generic proxy if there are no
    # billed rows to derive a rate from (flagged so the UI labels it as a proxy).
    _sum_total = sum(_f(r.get("total_dbus")) for r in raw)
    _sum_free = sum(_f(r.get("free_dbus")) for r in raw)
    _sum_billed_list = sum(_f(r.get("billed_list")) for r in raw)
    _billed_dbus_all = _sum_total - _sum_free
    if _billed_dbus_all > 0:
        eff_rate = _sum_billed_list / _billed_dbus_all
        rate_is_proxy = False
    else:
        eff_rate = DBU_HOUR_PROXY_USD
        rate_is_proxy = True
    gt_rows: list[dict[str, Any]] = []
    for r in raw:
        surface = str(r.get("surface") or "UNKNOWN")
        month = str(r.get("usage_month") or "")
        ws_id = str(r.get("workspace") or "")
        total_dbus = _f(r.get("total_dbus"))
        free_dbus = _f(r.get("free_dbus"))
        billed_list = _f(r.get("billed_list"))          # actual charge (paid)
        # List value of ALL usage on this surface: the billed rows' real list $
        # plus the free rows valued at the effective billed rate.
        total_list = billed_list + free_dbus * eff_rate
        gt_rows.append({
            "usage_month": month,
            "workspace": ws_names.get(ws_id, ws_id),
            "workspace_id": ws_id,
            "user_identity": str(r.get("user_identity") or ""),
            "surface": surface,
            "label": genie_cost.surface_label(surface),
            "total_dbus": round(total_dbus, 1),
            "total_list_cost_usd": round(total_list, 2),
            # Billed (paid) portion — carried explicitly so the assembler doesn't
            # assume paid == total (which was true only while we excluded free).
            "billed_dbus": round(total_dbus - free_dbus, 1),
            "billed_list_cost_usd": round(billed_list, 2),
        })
    # Free absorbed (GENIE_FREE_USAGE SKU, from 20 Jul 2026) — derived directly
    # from the rows we already fetched (no second query needed): the free DBUs
    # valued at the effective billed rate. None if there was no free usage this
    # month (pre-20-Jul estate) → the UI shows "n/a", not a fabricated 0.
    free_dbus_total: float | None = round(_sum_free, 1) if _sum_free > 0 else None
    free_absorbed_usd: float | None = (
        round(_sum_free * eff_rate, 2) if _sum_free > 0 else None)
    free_rate_is_proxy = rate_is_proxy and _sum_free > 0

    # billable_input=True: rows carry explicit billed_* fields (the charge above
    # the free allowance), so the assembler uses those for paid $ rather than
    # assuming paid == total (which stopped being true once we folded the free
    # tier into total_dbus/total_list to fix the "Agents/Spaces = $0" bug).
    return genie_cost.assemble(gt_rows, workspace, billable_input=True,
                               free_absorbed_usd=free_absorbed_usd,
                               free_dbus_total=free_dbus_total,
                               free_rate_is_proxy=free_rate_is_proxy)


# ---------------------------------------------------------------------------
# AI $ — all AI billing products (queries.sql AI aggregation).
# ---------------------------------------------------------------------------
_AI_SQL = """
SELECT
  COALESCE(u.usage_metadata.endpoint_name, u.usage_metadata.app_name, CONCAT('(', u.billing_origin_product, ')')) AS name,
  u.billing_origin_product                                    AS product,
  CAST(u.workspace_id AS STRING)                              AS workspace,
  COALESCE(u.identity_metadata.run_as, u.identity_metadata.created_by, 'shared') AS owner,
  MAX(u.product_features.model_serving.offering_type)         AS ms_offering,
  MAX(CASE WHEN u.product_features.serverless_gpu.workload_type IS NOT NULL THEN 1 ELSE 0 END) AS gpu,
  SUM(u.usage_quantity)                                       AS dbus,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)   AS list_cost
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
WHERE u.billing_origin_product IN ('MODEL_SERVING','AI_GATEWAY','VECTOR_SEARCH','AGENT_BRICKS','AI_FUNCTIONS','FOUNDATION_MODEL_TRAINING')
  AND u.usage_date >= date_trunc('MONTH', current_date())
GROUP BY 1, 2, 3, 4
HAVING SUM(u.usage_quantity) > 0
"""

# Fallback that references NONE of the newish product_features/usage_metadata
# sub-structs (serverless_gpu, model_serving, app_name) — those aren't present in
# every metastore/region, and a missing one is a hard schema error, not a null.
# Without this the whole AI $ tab 503s blank (the exact class of the Genie-empty
# bug). Names fall back to the billing product; mode/gpu default to unknown.
_AI_SQL_NOFEATURES = """
SELECT
  COALESCE(u.usage_metadata.endpoint_name, CONCAT('(', u.billing_origin_product, ')')) AS name,
  u.billing_origin_product                                    AS product,
  CAST(u.workspace_id AS STRING)                              AS workspace,
  COALESCE(u.identity_metadata.run_as, u.identity_metadata.created_by, 'shared') AS owner,
  CAST(NULL AS STRING)                                        AS ms_offering,
  0                                                           AS gpu,
  SUM(u.usage_quantity)                                       AS dbus,
  SUM(u.usage_quantity * lp.pricing.effective_list.default)   AS list_cost
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.cloud = lp.cloud AND u.sku_name = lp.sku_name
 AND u.usage_start_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
WHERE u.billing_origin_product IN ('MODEL_SERVING','AI_GATEWAY','VECTOR_SEARCH','AGENT_BRICKS','AI_FUNCTIONS','FOUNDATION_MODEL_TRAINING')
  AND u.usage_date >= date_trunc('MONTH', current_date())
GROUP BY 1, 2, 3, 4
HAVING SUM(u.usage_quantity) > 0
"""

_AI_MODE = {  # model_serving.offering_type -> friendly billing mode
    "PROVISIONED_THROUGHPUT": "Provisioned",
    "PAY_PER_TOKEN": "Pay-per-token",
}


def ai_cost_live(warehouse_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Real AI spend by product/endpoint/owner/workspace this month. Fields
    billing doesn't expose (scale-to-zero) are left unknown, so cost flags that
    depend on them simply don't fire — never a false positive.

    Degrades gracefully: if the newer product_features sub-structs
    (serverless_gpu / model_serving / app_name) aren't present in this metastore,
    fall back to a struct-free query so the AI tab renders instead of 503-ing."""
    try:
        raw = _run(warehouse_id, _AI_SQL, "system.billing.usage (AI)")
    except LiveError:
        raw = _run(warehouse_id, _AI_SQL_NOFEATURES, "system.billing.usage (AI, no features)")
    ws_names = _workspace_names(warehouse_id)
    rows: list[dict[str, Any]] = []
    for r in raw:
        product = str(r.get("product") or "")
        ws_id = str(r.get("workspace") or "")
        rows.append({
            "name": str(r.get("name") or "(unknown)"),
            "product": product,
            "product_label": ai_cost.PRODUCT_LABEL.get(product, product.title().replace("_", " ")),
            "workspace": ws_names.get(ws_id, ws_id),
            "workspace_id": ws_id,
            "owner": str(r.get("owner") or "shared"),
            "dbus_month": round(_f(r.get("dbus")), 1),
            "list_usd_month": round(_f(r.get("list_cost")), 2),
            # mode/gpu are UNKNOWN when the offering/gpu struct isn't present (the
            # no-features fallback SQL selects them NULL). Emit "Unknown"/None rather
            # than asserting a concrete "Standard"/False — labelling a provisioned-
            # throughput endpoint "Standard" (or a GPU endpoint non-GPU) would be a
            # fabricated fact. The UI shows "—" for these.
            "mode": _AI_MODE.get(str(r.get("ms_offering") or ""), None) if r.get("ms_offering") else None,
            "gpu": bool(_f(r.get("gpu"))) if r.get("gpu") is not None else None,
            "scale_to_zero": None,  # not exposed in billing — unknown, so no false flags
        })
    rows.sort(key=lambda r: -r["list_usd_month"])
    return ai_cost.assemble(rows, workspace, live=True)


# ---------------------------------------------------------------------------
# Cost drivers — spend by billing_origin_product + SKU + 6-month trend.
# Powers the cost-by-driver / trend / spike charts on Overview + Workspaces.
# ---------------------------------------------------------------------------
_PRODUCT_LABEL = {
    "SQL": "SQL", "JOBS": "Jobs", "INTERACTIVE": "Interactive", "DLT": "DLT / Lakeflow",
    "ALL_PURPOSE": "All-Purpose", "MODEL_SERVING": "Model Serving", "AI_GATEWAY": "AI Gateway",
    "VECTOR_SEARCH": "Vector Search", "PREDICTIVE_OPTIMIZATION": "Predictive Optimization",
    "LAKEBASE": "Lakebase", "APPS": "Apps", "DATA_QUALITY_MONITORING": "Data Quality Monitoring",
    "GENIE": "Genie", "DATABASE": "Database", "AI_FUNCTIONS": "AI Functions",
    "AGENT_BRICKS": "Agent Bricks", "NETWORKING": "Networking",
}


def _plabel(code: str) -> str:
    return _PRODUCT_LABEL.get(code, code.title().replace("_", " "))


def cost_drivers_live(warehouse_id: str, workspace: str | None = None) -> dict[str, Any]:
    """Real cost-by-driver (billing_origin_product) + SKU breakdown + 6-month
    trend + MoM, from system.billing.usage. Estate-wide by default; scoped when
    a workspace_id is passed."""
    # workspace scopes on the numeric workspace_id column; validate the shape
    # before interpolation (injection guard) and fail-soft to the estate view.
    scoped = _safe_ws_id(None if not workspace or workspace in ("all", "") else workspace)
    wsf = f" AND u.workspace_id = '{scoped}'" if scoped else ""
    price = ("LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
             "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time)")

    # Current-month drivers
    drv = _run(warehouse_id,
               f"SELECT u.billing_origin_product AS product, SUM(u.usage_quantity) dbus, "
               f"SUM(u.usage_quantity*lp.pricing.effective_list.default) usd FROM system.billing.usage u {price} "
               f"WHERE u.usage_date>=date_trunc('MONTH',current_date()){wsf} GROUP BY 1", "cost-drivers (product)")
    total = sum(_f(r.get("usd")) for r in drv) or 1.0
    drivers = sorted(
        [{"driver": str(r["product"]), "label": _plabel(str(r["product"])),
          "spend_usd_month": round(_f(r.get("usd"))), "pct_of_total": round(_f(r.get("usd")) / total, 4)} for r in drv],
        key=lambda d: -d["spend_usd_month"])

    # Current-month SKU breakdown (cap to keep payload sane)
    sku = _run(warehouse_id,
               f"SELECT u.sku_name AS sku, u.billing_origin_product AS product, "
               f"SUM(u.usage_quantity*lp.pricing.effective_list.default) usd, SUM(u.usage_quantity) dbus "
               f"FROM system.billing.usage u {price} WHERE u.usage_date>=date_trunc('MONTH',current_date()){wsf} "
               f"GROUP BY 1,2 ORDER BY usd DESC LIMIT 40", "cost-drivers (sku)")
    sku_total = sum(_f(r.get("usd")) for r in sku) or 1.0
    sku_breakdown = [{"sku": str(r["sku"]), "driver": str(r["product"]), "driver_label": _plabel(str(r["product"])),
                      "total_cost": round(_f(r.get("usd"))), "dbus_month": round(_f(r.get("dbus"))),
                      "pct_of_total": round(_f(r.get("usd")) / sku_total, 4)} for r in sku]
    dbu_by_sku = [{"sku": s["sku"], "driver": s["driver"], "driver_label": s["driver_label"], "dbus_month": s["dbus_month"]}
                  for s in sku_breakdown if s["dbus_month"] > 0]

    # 6-month trend by product. Exclude the current, INCOMPLETE month — it would
    # plot as a future/partial end-point AND make MoM (last vs prev) compare a
    # part-month against a full month. Trend + MoM use the last COMPLETE month.
    tr = _run(warehouse_id,
              f"SELECT date_format(date_trunc('MONTH',u.usage_date),'yyyy-MM') m, u.billing_origin_product product, "
              f"SUM(u.usage_quantity*lp.pricing.effective_list.default) usd FROM system.billing.usage u {price} "
              f"WHERE u.usage_date>=add_months(date_trunc('MONTH',current_date()),-6){wsf} "
              f"AND u.usage_date < date_trunc('MONTH',current_date()) GROUP BY 1,2", "cost-drivers (trend)")
    months = sorted({str(r["m"]) for r in tr})
    by_prod: dict[str, dict[str, float]] = {}
    for r in tr:
        by_prod.setdefault(str(r["product"]), {})[str(r["m"])] = _f(r.get("usd"))
    series, mom = [], []
    for code, mm in by_prod.items():
        pts = [{"month": m, "spend_usd": round(mm.get(m, 0.0))} for m in months]
        last = pts[-1]["spend_usd"] if pts else 0
        prev = pts[-2]["spend_usd"] if len(pts) >= 2 else 0
        series.append({"driver": code, "label": _plabel(code), "points": pts, "spend_usd_month": last})
        # mom_pct: a genuinely NEW driver (prev=$0, last>$0) is infinite growth, not
        # 0% — reporting 0 hid the fastest-growing spend from the top-movers sort.
        # Flag it with is_new + a sentinel high pct so it ranks at the top (where a
        # brand-new cost spike belongs), and let the UI badge it as "new" rather
        # than print a nonsensical percentage.
        is_new = prev <= 0 and last > 0
        mom.append({"driver": code, "label": _plabel(code), "prev_usd_month": prev, "spend_usd_month": last,
                    "mom_pct": (round((last - prev) / prev, 4) if prev > 0 else None),
                    "is_new": is_new, "delta_usd": last - prev})
    series.sort(key=lambda s: -s["spend_usd_month"])
    # Sort by absolute $ delta desc so both new drivers (mom_pct=None) and existing
    # ones rank by real movement — a new $10k driver beats a +5% on $2k. (Sorting
    # by mom_pct alone dropped new drivers to the bottom because their pct was 0.)
    mom.sort(key=lambda m: -m["delta_usd"])

    return {
        "workspace": scoped or "all",
        "total_spend_usd_month": round(sum(d["spend_usd_month"] for d in drivers)),
        "drivers": drivers,
        "sku_breakdown": sku_breakdown,
        "dbu_by_sku": dbu_by_sku,
        "trend": {"months": months, "series": series},
        "mom": mom,   # top-level — the UI reads cost_drivers.mom (not trend.mom)
    }


# ---------------------------------------------------------------------------
# Workspaces + Overview — per-workspace billing facts + a LABELLED savings
# estimate (savings is a recommendation output, not a billing fact, so live
# derives it transparently from real signals: serverless-shift + automation
# headroom). Non-billing detail (BU, idle %, sparkline) is left blank rather
# than faked.
# ---------------------------------------------------------------------------
def _health(automated: float) -> str:
    return "Good" if automated >= 0.6 else "Warning" if automated >= 0.25 else "Critical"


def _savings_pct(automated: float, serverless: float) -> float:
    """Transparent, real-signal-driven estimate of optimisation headroom:
    low automation + low serverless share = more opportunity. Clamped 5-45%."""
    return max(0.05, min(0.45, 0.30 * (1 - automated) + 0.15 * (1 - serverless)))


def _workspace_facts(warehouse_id: str) -> list[dict[str, Any]]:
    """Per-workspace billing facts + derived health/est-savings."""
    # Core spend/dbu facts + optional cluster/warehouse counts (the latter read
    # usage_metadata sub-structs that aren't in every metastore). If the count
    # columns cause a schema error, retry WITHOUT them so Overview + Workspaces
    # still render (they're #clusters/#warehouses detail, not core numbers).
    _core = ("SELECT CAST(u.workspace_id AS STRING) ws, "
             "SUM(u.usage_quantity) dbus, "
             "SUM(u.usage_quantity*lp.pricing.effective_list.default) usd, "
             "SUM(CASE WHEN u.sku_name ILIKE '%SERVERLESS%' THEN u.usage_quantity ELSE 0 END) sl_dbus, "
             "SUM(CASE WHEN u.billing_origin_product IN ('JOBS','DLT') THEN u.usage_quantity ELSE 0 END) auto_dbus, ")
    _tail = ("FROM system.billing.usage u "
             "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
             "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
             "WHERE u.usage_date>=date_trunc('MONTH',current_date()) GROUP BY 1 HAVING SUM(u.usage_quantity)>0")
    try:
        rows = _run(warehouse_id,
            _core + "COUNT(DISTINCT u.usage_metadata.cluster_id) clusters, "
                    "COUNT(DISTINCT u.usage_metadata.warehouse_id) whs " + _tail,
            "system.billing.usage (workspaces)")
    except LiveError:
        rows = _run(warehouse_id,
            _core + "0 clusters, 0 whs " + _tail,
            "system.billing.usage (workspaces, no compute-id counts)")

    # Idle % — honest best-effort from query.history: per (warehouse, day) the
    # wall-clock the warehouse was "open" (last query end − first query start)
    # vs the seconds actually executing. idle = 1 − active/open. It measures
    # in-session idle (a warehouse left running between queries), not 24h idle,
    # so it is a labelled estimate, not a billing fact.
    idle_by_ws: dict[str, float] = {}
    try:
        idle_rows = _run(warehouse_id,
            "WITH per AS (SELECT CAST(workspace_id AS STRING) ws, compute.warehouse_id wh, "
            "  date_trunc('DAY',start_time) d, SUM(execution_duration_ms)/1000.0 active_s, "
            "  unix_timestamp(MAX(end_time))-unix_timestamp(MIN(start_time)) window_s "
            "  FROM system.query.history WHERE start_time>=dateadd(day,-30,current_date()) "
            "  AND compute.warehouse_id IS NOT NULL GROUP BY 1,2,3) "
            "SELECT ws, SUM(active_s) active_s, SUM(window_s) window_s FROM per GROUP BY 1",
            "system.query.history (idle)")
        for r in idle_rows:
            win = _f(r.get("window_s"))
            act = _f(r.get("active_s"))
            if win > 0:
                idle_by_ws[str(r.get("ws"))] = max(0.0, min(1.0, 1 - (act / win)))
    except LiveError:
        idle_by_ws = {}   # query.history not readable — leave idle unknown (0.0)

    # Optional customer workspace->BU/owner mapping. {} when not configured, so
    # bu falls back to "" and the UI (gated on config.bu_enabled) hides BU (#16).
    bu_by_ws = _workspace_bu(warehouse_id)

    out: list[dict[str, Any]] = []
    for r in rows:
        dbus = _f(r.get("dbus")); usd = _f(r.get("usd"))
        serverless = (_f(r.get("sl_dbus")) / dbus) if dbus else 0.0
        automated = (_f(r.get("auto_dbus")) / dbus) if dbus else 0.0
        spct = _savings_pct(automated, serverless)
        ws_id = str(r.get("ws"))
        idle = round(idle_by_ws.get(ws_id, 0.0), 3)
        bu_row = bu_by_ws.get(ws_id, {})
        out.append({
            "workspace_id": ws_id, "workspace": ws_id,
            "bu": bu_row.get("bu", ""),
            "owner": bu_row.get("owner", ""),
            "spend_usd_month": round(usd), "savings_usd_month": round(usd * spct),
            "savings_pct": round(spct, 3), "savings_estimated": True,
            "automated_pct": round(automated, 3), "health": _health(automated),
            # Vocabulary must match the Complexity filter dropdown (and demo mode):
            # Complex / Moderate / Simple — NOT High/Medium/Low (which made every
            # filter selection return zero rows).
            "complexity": "Complex" if usd > 50000 else "Moderate" if usd > 10000 else "Simple",
            "idle_pct": idle, "idle_estimated": True, "serverless_share": round(serverless, 3),
            "dbus_month": round(dbus), "num_clusters": int(_f(r.get("clusters"))),
            "num_warehouses": int(_f(r.get("whs"))), "trend": [],
        })
    out.sort(key=lambda w: -w["spend_usd_month"])
    return out


def _workspace_names(warehouse_id: str) -> dict[str, str]:
    """workspace_id -> workspace_name from system.access.workspaces_latest (PuPr).
    Fail-soft: returns {} if the table is ungranted or system.access isn't enabled
    (deleted workspaces are absent, so historical ids fall back to the id)."""
    try:
        rows = _run(warehouse_id,
            "SELECT CAST(workspace_id AS STRING) ws, workspace_name FROM system.access.workspaces_latest",
            "workspace names")
        return {str(r["ws"]): str(r.get("workspace_name") or r["ws"]) for r in rows if r.get("ws") is not None}
    except Exception:
        return {}


def _warehouse_names(warehouse_id: str) -> dict[str, str]:
    """warehouse_id -> warehouse_name from system.compute.warehouses (GA, SCD2 —
    take the latest row per warehouse). Fail-soft to {}."""
    try:
        rows = _run(warehouse_id,
            "SELECT warehouse_id, warehouse_name FROM system.compute.warehouses "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY warehouse_id ORDER BY change_time DESC)=1",
            "warehouse names")
        return {str(r["warehouse_id"]): str(r.get("warehouse_name") or r["warehouse_id"]) for r in rows if r.get("warehouse_id") is not None}
    except Exception:
        return {}


def _warehouse_meta(warehouse_id: str) -> dict[str, dict[str, Any]]:
    """warehouse_id -> {type, size, auto_stop_mins} from system.compute.warehouses
    (latest row per warehouse). Fills the Warehouse Monitor type/size/auto-stop
    columns that were previously blank. Fail-soft to {}."""
    try:
        rows = _run(warehouse_id,
            "SELECT warehouse_id, warehouse_type, warehouse_size, auto_stop_minutes "
            "FROM system.compute.warehouses "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY warehouse_id ORDER BY change_time DESC)=1",
            "warehouse meta")
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            wid = r.get("warehouse_id")
            if wid is None:
                continue
            wt = str(r.get("warehouse_type") or "").title()   # SERVERLESS→Serverless, PRO→Pro
            out[str(wid)] = {
                "type": {"Serverless": "Serverless", "Pro": "Pro", "Classic": "Classic"}.get(wt, wt),
                "size": str(r.get("warehouse_size") or "").title(),  # SMALL→Small
                "auto_stop_mins": int(_f(r.get("auto_stop_minutes"))),
            }
        return out
    except Exception:
        return {}


# A UC table name is catalog.schema.table — plain identifiers + dots only. We
# validate before interpolating (the name comes from customer config, never a
# request) so a fat-fingered value fails closed instead of running odd SQL.
_UC_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
# catalog.schema (2-part) identifier — for gold_schema before interpolation.
_SCHEMA_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")


def _workspace_bu(warehouse_id: str) -> dict[str, dict[str, str]]:
    """workspace_id -> {"bu", "owner"} from the customer-maintained mapping table
    (config workspace_bu_table). Returns {} when the table isn't configured, the
    identifier is malformed, or a read fails (ungranted/missing) — the caller then
    omits the BU dimension (fail-soft; no crash). The only retried failure is a
    missing optional `owner` column; a non-column error is NOT retried (no point
    re-running the same failing read) and isn't masked as the no-owner case.

    Expected columns: workspace_id, bu, and (optional) owner."""
    from data import config

    table = str(config.get_settings().get("workspace_bu_table") or "").strip()
    if not table:
        return {}
    if not _UC_TABLE_RE.match(table):
        return {}
    try:
        rows = _run(warehouse_id,
            f"SELECT CAST(workspace_id AS STRING) ws, bu, owner FROM {table}",
            "workspace->BU mapping")
    except LiveError as e:
        # The `owner` column is OPTIONAL — retry without it ONLY when the failure
        # looks like a missing-column error. For a genuine grant/missing-table error
        # there's no point re-running the same failing read, so fail soft to {} (the
        # caller hides BU) — but DON'T mask it as the no-owner retry.
        detail = (getattr(e, "detail", "") or "").upper()
        missing_owner = "OWNER" in detail or "UNRESOLVED_COLUMN" in detail or "COLUMN" in detail
        if not missing_owner:
            return {}
        try:
            rows = _run(warehouse_id,
                f"SELECT CAST(workspace_id AS STRING) ws, bu, CAST(NULL AS STRING) owner FROM {table}",
                "workspace->BU mapping (no owner)")
        except Exception:  # noqa: BLE001 — still unreadable → fail soft
            return {}
    out: dict[str, dict[str, str]] = {}
    for r in rows:
        ws = r.get("ws")
        if ws is None:
            continue
        out[str(ws)] = {"bu": str(r.get("bu") or ""), "owner": str(r.get("owner") or "")}
    return out


def _user_team(warehouse_id: str) -> dict[str, str]:
    """user -> team from the customer-maintained mapping table (config
    user_team_table). Returns {} when unset/malformed/ungranted — the caller then
    omits the team dimension. Expected columns: user, team."""
    from data import config

    table = str(config.get_settings().get("user_team_table") or "").strip()
    if not table or not _UC_TABLE_RE.match(table):
        return {}
    try:
        # Backtick `user` — it's a reserved niladic function (current_user) in
        # Spark SQL; unquoted it resolves to the session user for every row and
        # silently blanks the whole team dimension even with a populated table.
        # Alias to stable output names so r.get() below is unambiguous.
        rows = _run(warehouse_id,
            f"SELECT `user` AS u, `team` AS team FROM {table}", "user->team mapping")
    except Exception:
        return {}
    out: dict[str, str] = {}
    for r in rows:
        u = r.get("u")
        if u is None:
            continue
        out[str(u)] = str(r.get("team") or "")
    return out


def workspaces_live(warehouse_id: str, workspace: str | None = None) -> list[dict[str, Any]]:
    facts = _workspace_facts(warehouse_id)
    names = _workspace_names(warehouse_id)
    trends = _ws_weekly_trends(warehouse_id)   # bulk: {ws_id: [weekly $]}
    for w in facts:
        # keep workspace_id as the stable key; the display field `workspace` holds
        # the friendly name (falling back to the id), and workspace_name mirrors it
        # so name-primary + id-secondary works identically to demo (a customer review #13).
        w["workspace_name"] = names.get(w["workspace_id"], w["workspace_id"])
        w["workspace"] = w["workspace_name"]
        w["trend"] = trends.get(w["workspace_id"], [])
    scoped = None if not workspace or workspace in ("all", "") else workspace
    return [w for w in facts if not scoped or w["workspace"] == scoped or w["workspace_id"] == scoped]


def _ws_weekly_trends(warehouse_id: str, weeks: int = 12) -> dict[str, list[float]]:
    """ONE bulk query → {workspace_id: [weekly $ oldest→newest]} for the Workspaces
    trend sparkline (was empty in live). Excludes the current incomplete week.
    Fail-soft to {}."""
    try:
        rows = _run(warehouse_id,
            "SELECT CAST(u.workspace_id AS STRING) ws, "
            "date_format(date_trunc('WEEK',u.usage_date),'yyyy-MM-dd') wk, "
            "SUM(u.usage_quantity*lp.pricing.effective_list.default) usd "
            "FROM system.billing.usage u LEFT JOIN system.billing.list_prices lp "
            "ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name AND u.usage_start_time>=lp.price_start_time "
            "AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
            f"WHERE u.usage_date>=dateadd(WEEK,-{weeks},current_date()) "
            "AND u.usage_date < date_trunc('WEEK',current_date()) "
            "GROUP BY 1,2 ORDER BY 1,2",
            "system.billing.usage (workspace weekly trend)")
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, list[float]] = {}
    for r in rows:
        ws = str(r.get("ws") or "")
        if ws:
            out.setdefault(ws, []).append(round(_f(r.get("usd"))))
    return out


def _ws_monthly_trend(warehouse_id: str, ws_id: str, rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Last-12-month spend + implied captured-savings for one workspace. Fail-soft."""
    try:
        rows = _run(warehouse_id,
            "SELECT date_format(u.usage_date,'yyyy-MM') month, "
            "SUM(u.usage_quantity*lp.pricing.effective_list.default) usd "
            "FROM system.billing.usage u LEFT JOIN system.billing.list_prices lp "
            "ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name AND u.usage_start_time>=lp.price_start_time "
            "AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
            f"WHERE CAST(u.workspace_id AS STRING)='{_safe_ws_id(ws_id) or ''}' "
            "AND u.usage_date>=add_months(date_trunc('MONTH',current_date()),-11) "
            # Exclude the current, incomplete month (plots as an end-of-trend dip).
            "AND u.usage_date < date_trunc('MONTH',current_date()) GROUP BY 1 ORDER BY 1",
            "ws monthly trend")
        spct = float(rec.get("savings_pct", 0.0) or 0.0)
        return [{"month": str(r["month"]), "spend_usd": round(_f(r.get("usd"))),
                 "savings_usd": round(_f(r.get("usd")) * spct)} for r in rows]
    except Exception:
        return []


def workspace_detail_live(warehouse_id: str, workspace_id: str) -> dict[str, Any] | None:
    """Live drill-down for one workspace. Returns None ONLY when the workspace is
    genuinely absent from the estate (a real 404); every sub-view fails soft to
    empty so a real workspace always renders. Fixes the a customer 'Unknown workspace'
    404 (the detail route previously always read demo data)."""
    rows = workspaces_live(warehouse_id, workspace_id)
    if not rows:
        return None
    rec = rows[0]
    try:
        cost_drivers = cost_drivers_live(warehouse_id, workspace_id)
    except Exception:
        cost_drivers = {}
    product_mix = [
        {"product": d.get("label", d.get("driver")), "pct": d.get("pct_of_total", 0.0),
         "spend_usd_month": d.get("spend_usd_month", 0)}
        for d in (cost_drivers.get("drivers") or [])
    ]
    tops = _ws_top_lists(warehouse_id, workspace_id)
    # Genie spend for THIS workspace. Scope by workspace_id (not name): the raw
    # id always matches the billing rows, whereas the friendly name only resolves
    # when system.access.workspaces_latest is granted — scoping by name there gave
    # a false "$0 / no billable Genie spend" in the drill-down. Fail-soft to None.
    genie: dict[str, Any] | None = None
    try:
        gsum = (genie_cost_live(warehouse_id, workspace_id) or {}).get("summary", {})
        if gsum:
            genie = {
                "total_list_cost_usd": gsum.get("total_list_cost_usd", 0),
                "paid_list_cost_usd": gsum.get("paid_list_cost_usd", 0),
                "total_dbus": gsum.get("total_dbus", 0),
                "distinct_users": gsum.get("distinct_users", 0),
                "num_surfaces": gsum.get("num_surfaces", 0),
            }
    except Exception:  # noqa: BLE001 — drill-down must render even if Genie read fails
        genie = None
    return {
        **rec,
        "product_mix": product_mix,
        "monthly_trend": _ws_monthly_trend(warehouse_id, workspace_id, rec),
        "top_users": tops.get("top_users", []),
        "top_jobs": tops.get("top_jobs", []),
        "top_warehouses": tops.get("top_warehouses", []),
        "cost_drivers": cost_drivers,
        "genie": genie,
        "detail_estimated": True,
    }


def _ws_top_lists(warehouse_id: str, ws_id: str) -> dict[str, list[dict[str, Any]]]:
    """Top users / jobs / warehouses by $ for one workspace this month, from
    system.billing.usage. Each is best-effort → [] on failure (never blocks the
    drill-down). Fills the previously-empty 'Top … by $' panels."""
    sid = _safe_ws_id(ws_id)
    if not sid:
        return {}
    price = ("LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
             "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time)")
    base = (f"FROM system.billing.usage u {price} WHERE CAST(u.workspace_id AS STRING)='{sid}' "
            "AND u.usage_date>=date_trunc('MONTH',current_date())")
    out: dict[str, list[dict[str, Any]]] = {}
    try:
        ur = _run(warehouse_id,
            "SELECT COALESCE(u.identity_metadata.run_as, u.identity_metadata.created_by, '(shared / service)') usr, "
            f"SUM(u.usage_quantity*lp.pricing.effective_list.default) usd {base} "
            "AND u.identity_metadata IS NOT NULL GROUP BY 1 ORDER BY usd DESC LIMIT 6", "ws top users")
        out["top_users"] = [{"user": str(r.get("usr") or ""), "spend_usd_month": round(_f(r.get("usd")))} for r in ur if _f(r.get("usd")) > 0]
    except Exception:
        pass
    try:
        # Resolve job_id → human-readable name via system.lakeflow.jobs. That table
        # is SCD (a row per job version), so dedupe to the latest name per
        # (workspace_id, job_id) before joining, or the SUM would fan out across
        # versions. Fall back to the id string when the name can't be resolved.
        job_price = ("LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
             "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
             "LEFT JOIN jn j ON CAST(u.usage_metadata.job_id AS STRING)=CAST(j.job_id AS STRING) "
             "AND CAST(u.workspace_id AS STRING)=CAST(j.workspace_id AS STRING)")
        jr = _run(warehouse_id,
            "WITH jn AS (SELECT workspace_id, job_id, name FROM system.lakeflow.jobs "
            "  QUALIFY row_number() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC)=1) "
            "SELECT CAST(u.usage_metadata.job_id AS STRING) jid, any_value(j.name) name, "
            "SUM(u.usage_quantity*lp.pricing.effective_list.default) usd "
            f"FROM system.billing.usage u {job_price} WHERE CAST(u.workspace_id AS STRING)='{sid}' "
            "AND u.usage_date>=date_trunc('MONTH',current_date()) "
            "AND u.usage_metadata.job_id IS NOT NULL GROUP BY 1 ORDER BY usd DESC LIMIT 6", "ws top jobs")
        out["top_jobs"] = [{"job": str(r.get("name") or r.get("jid") or ""),
                            "job_id": str(r.get("jid") or ""),
                            "spend_usd_month": round(_f(r.get("usd")))} for r in jr if _f(r.get("usd")) > 0]
    except Exception:
        pass
    try:
        wr = _run(warehouse_id,
            "SELECT CAST(u.usage_metadata.warehouse_id AS STRING) whid, "
            f"SUM(u.usage_quantity*lp.pricing.effective_list.default) usd {base} "
            "AND u.usage_metadata.warehouse_id IS NOT NULL GROUP BY 1 ORDER BY usd DESC LIMIT 6", "ws top warehouses")
        wh_names = _warehouse_names(warehouse_id)
        out["top_warehouses"] = [{"warehouse": wh_names.get(str(r.get("whid")), str(r.get("whid"))),
                                  "spend_usd_month": round(_f(r.get("usd")))} for r in wr if _f(r.get("usd")) > 0]
    except Exception:
        pass
    return out


def _overview_summary_extras(warehouse_id: str, total_spend: float, cost_drivers: dict[str, Any]) -> dict[str, Any]:
    """Account-level exec-summary extras for the landing view — MoM spend delta,
    MAU/queries with a prior-period delta, rolled addressable savings from the
    rec hub, and a short needs-attention list (each with a deep-link target tab).

    Pure SQL / aggregation — no AI, no Genie. Every piece is best-effort and
    fails soft to a neutral value so the summary never errors or blanks (works
    with the BU/owner table absent and with AI/Genie switched off)."""
    extras: dict[str, Any] = {}

    # --- MoM spend: this-month-to-date vs the SAME span of last month (a fair
    #     like-for-like compare; comparing MTD to a full prior month would always
    #     read as a false drop early in the month).
    try:
        mom = _run(warehouse_id,
            "SELECT "
            "SUM(CASE WHEN u.usage_date>=date_trunc('MONTH',current_date()) THEN u.usage_quantity*lp.pricing.effective_list.default ELSE 0 END) cur, "
            "SUM(CASE WHEN u.usage_date>=add_months(date_trunc('MONTH',current_date()),-1) "
            "          AND u.usage_date< add_months(date_trunc('MONTH',current_date()),-1)+ (current_date()-date_trunc('MONTH',current_date())) "
            "         THEN u.usage_quantity*lp.pricing.effective_list.default ELSE 0 END) prev "
            "FROM system.billing.usage u "
            "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
            "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
            "WHERE u.usage_date>=add_months(date_trunc('MONTH',current_date()),-1)",
            "billing (MoM spend, like-for-like MTD)")
        cur = _f(mom[0].get("cur")) if mom else 0.0
        prev = _f(mom[0].get("prev")) if mom else 0.0
        extras["spend_mtd_usd"] = round(cur)
        extras["spend_prev_mtd_usd"] = round(prev)
        extras["spend_mom_delta_usd"] = round(cur - prev)
        extras["spend_mom_pct"] = round((cur - prev) / prev, 3) if prev > 0 else 0.0
    except Exception:  # noqa: BLE001 — never block the summary
        pass

    # --- MAU + queries this 30d vs the prior 30d (change over time).
    try:
        act = _run(warehouse_id,
            "SELECT "
            "COUNT(DISTINCT CASE WHEN start_time>=dateadd(day,-30,current_date()) THEN executed_by END) mau, "
            "COUNT(CASE WHEN start_time>=dateadd(day,-30,current_date()) THEN 1 END) q, "
            "COUNT(DISTINCT CASE WHEN start_time>=dateadd(day,-60,current_date()) AND start_time<dateadd(day,-30,current_date()) THEN executed_by END) mau_prev, "
            "COUNT(CASE WHEN start_time>=dateadd(day,-60,current_date()) AND start_time<dateadd(day,-30,current_date()) THEN 1 END) q_prev "
            "FROM system.query.history WHERE start_time>=dateadd(day,-60,current_date())",
            "query.history (MAU/queries + prior)")
        if act:
            mau = int(_f(act[0].get("mau"))); mau_prev = int(_f(act[0].get("mau_prev")))
            q = int(_f(act[0].get("q"))); q_prev = int(_f(act[0].get("q_prev")))
            extras["mau"] = mau
            extras["mau_delta"] = mau - mau_prev
            extras["queries_30d"] = q
            extras["queries_30d_delta_pct"] = round((q - q_prev) / q_prev, 3) if q_prev > 0 else 0.0
            # Query-hours (compute time) this 30d — a real usage-intensity fact.
            qh = _run(warehouse_id,
                "SELECT SUM(total_duration_ms)/3600000.0 qh FROM system.query.history "
                "WHERE start_time>=dateadd(day,-30,current_date())", "query.history (query-hours)")
            if qh:
                extras["query_hours_30d"] = round(_f(qh[0].get("qh")), 0)
    except Exception:  # noqa: BLE001
        pass

    # --- Adoption breadth + Genie/AI uptake (measured, from billing). Reuse the
    #     adoption summary so the landing view reconciles with the Adoption tab.
    try:
        adopt = adoption_live(warehouse_id)
        asu = adopt.get("summary", {})
        extras["wau"] = int(asu.get("wau") or 0)
        extras["avg_breadth"] = round(_f(asu.get("avg_breadth")), 1)
        extras["num_products"] = int(asu.get("num_products") or 0)
        extras["genie_adopters"] = int(asu.get("genie_adopters") or 0)
        extras["ai_adopters"] = int(asu.get("ai_adopters") or 0)
    except Exception:  # noqa: BLE001
        pass

    # --- Governance-lite posture (measured): % of this month's spend that is
    #     UNTAGGED — a real cost-attribution gap, cheap to compute from billing.
    #     (NULL custom_tags counts as untagged; ANSI size(NULL)=NULL, so guard it.)
    try:
        tagq = _run(warehouse_id,
            "SELECT SUM(u.usage_quantity*lp.pricing.effective_list.default) usd, "
            "SUM(CASE WHEN u.custom_tags IS NULL OR size(u.custom_tags)=0 "
            "         THEN u.usage_quantity*lp.pricing.effective_list.default ELSE 0 END) untagged "
            "FROM system.billing.usage u "
            "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
            "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
            "WHERE u.usage_date>=date_trunc('MONTH',current_date())", "billing (untagged spend)")
        if tagq:
            t = _f(tagq[0].get("usd")); ut = _f(tagq[0].get("untagged"))
            extras["untagged_spend_pct"] = round(ut / t, 3) if t > 0 else 0.0
    except Exception:  # noqa: BLE001
        pass
    # Estate automation / serverless mix from the per-workspace facts (already
    # computed above in overview_live and passed via the facts list) — spend-weighted.
    try:
        facts = _workspace_facts(warehouse_id)
        tw = sum(w.get("spend_usd_month", 0) for w in facts) or 1
        extras["automated_pct"] = round(sum(w.get("automated_pct", 0) * w.get("spend_usd_month", 0) for w in facts) / tw, 3)
        extras["serverless_share"] = round(sum(w.get("serverless_share", 0) * w.get("spend_usd_month", 0) for w in facts) / tw, 3)
    except Exception:  # noqa: BLE001
        pass

    # --- Rolled addressable savings + a needs-attention list from the rec hub —
    #     the SAME cross-tab engine the Recommendations tab uses (clusters,
    #     warehouses, tables, jobs), so the landing figure reconciles with it.
    #     Each attention item carries a `tab` so the UI can deep-link.
    try:
        from data import rec_hub
        hub = rec_hub.build_hub()
        summ = hub.get("summary", {})
        extras["addressable_savings_usd_month"] = round(_f(summ.get("total_savings_usd_month") or summ.get("total_savings") or 0))
        extras["num_p1"] = int(summ.get("num_p1") or 0)
        # Top P1s (highest $), mapped to their deep-link tab via the rec category.
        _cat_tab = {"compute": "clusters", "sql": "warehouses", "storage": "tables",
                    "jobs": "jobs", "query": "queries", "tagging": "governance",
                    "genai": "ai", "access": "access", "governance": "governance"}
        recs = sorted(hub.get("recs", hub.get("recommendations", [])),
                      key=lambda r: -_f(r.get("est_savings_usd_month")))
        attention: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for r in recs:
            if r.get("priority") != "P1":
                continue
            title = r.get("title") or r.get("insight") or "Optimization opportunity"
            # De-dupe by title so the landing-page glance shows 4 DISTINCT actions,
            # not the same play repeated across several workspaces (the highest-$
            # instance of each play wins, since recs are pre-sorted by savings).
            if title in seen_titles:
                continue
            seen_titles.add(title)
            attention.append({
                "kind": "optimization",
                "title": title,
                "detail": r.get("scope_label") or r.get("category") or "",
                "est_savings_usd_month": round(_f(r.get("est_savings_usd_month"))),
                "tab": _cat_tab.get(str(r.get("category")), "recommendations"),
                # entity_key + source let the app layer drop acknowledged items
                # from this list (writeback), keyed the same as the rec hub.
                "source": "rec", "entity_key": r.get("id", ""),
            })
            if len(attention) >= 4:
                break
        extras["needs_attention"] = attention
    except Exception:  # noqa: BLE001
        extras["needs_attention"] = []

    # --- Biggest MoM cost-driver spike (fastest-growing spend) — reuse the
    #     already-computed cost_drivers MoM list; deep-link to Overview's own
    #     driver chart (stays here). Only add if a real positive spike exists.
    try:
        # cost_drivers_live emits each MoM entry with mom_pct (a fraction, or None
        # for a NEW driver = infinite growth) + is_new + delta_usd. Rank by $ delta
        # so a brand-new material driver surfaces as the top spike (sorting by
        # mom_pct alone buried new drivers, whose pct is None/0).
        mom_drivers = sorted((cost_drivers or {}).get("mom", []),
                             key=lambda d: -_f(d.get("delta_usd")))
        if mom_drivers:
            top = mom_drivers[0]
            pct = top.get("mom_pct")   # None for a new driver
            is_new = bool(top.get("is_new"))
            delta = _f(top.get("delta_usd"))
            # Flag a new driver with material spend, OR an existing one up >=15%.
            material_new = is_new and delta >= 500.0
            material_rise = pct is not None and pct >= 0.15
            if material_new or material_rise:
                label = top.get("label") or top.get("driver")
                title = (f"{label} is a new cost driver this month"
                         if is_new else
                         f"{label} spend up {round(_f(pct)*100)}% MoM")
                extras.setdefault("needs_attention", [])
                extras["needs_attention"].insert(0, {
                    "kind": "spend-spike",
                    "title": title,
                    "detail": "Fastest-growing cost driver this month",
                    "est_savings_usd_month": 0,
                    "tab": "overview",
                })
                extras["needs_attention"] = extras["needs_attention"][:4]
    except Exception:  # noqa: BLE001
        pass

    return extras


def overview_live(warehouse_id: str) -> dict[str, Any]:
    """Estate rollup from the per-workspace facts + weekly spend trend + the
    live cost-driver breakdown. Savings is the summed labelled estimate."""
    facts = _workspace_facts(warehouse_id)
    # Resolve workspace ids → friendly names so the Top-opportunities list shows
    # names, not raw ids (matches every other tab).
    _wsn = _workspace_names(warehouse_id)
    for _w in facts:
        _w["workspace"] = _wsn.get(_w["workspace_id"], _w["workspace_id"])
    total_spend = sum(w["spend_usd_month"] for w in facts)
    total_savings = sum(w["savings_usd_month"] for w in facts)
    total_dbus = sum(w["dbus_month"] for w in facts)
    save_pct = (total_savings / total_spend) if total_spend else 0.0

    weekly = _run(warehouse_id,
        "SELECT date_format(date_trunc('WEEK',u.usage_date),'yyyy-MM-dd') wk, "
        "SUM(u.usage_quantity*lp.pricing.effective_list.default) usd FROM system.billing.usage u "
        "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
        "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
        "WHERE u.usage_date>=dateadd(WEEK,-11,current_date()) "
        # Exclude the current, INCOMPLETE week — its partial data plots as a
        # near-zero drop at the end of the trend (and a future-looking Monday
        # label). Only show weeks that have fully elapsed.
        "AND u.usage_date < date_trunc('WEEK',current_date()) GROUP BY 1 ORDER BY 1",
        "system.billing.usage (weekly trend)")
    trend = [{"week": str(r.get("wk")), "spend_usd": round(_f(r.get("usd"))),
              "savings_usd": round(_f(r.get("usd")) * save_pct)} for r in weekly]

    top_opportunities = [
        {"type": "workspace", "insight": f"~{round(w['savings_pct']*100)}% headroom",
         "target": w["workspace"],
         "detail": f"{w['health']} health · {round(w['automated_pct']*100)}% automated · {round(w['serverless_share']*100)}% serverless",
         "est_savings_usd_month": w["savings_usd_month"]}
        for w in sorted(facts, key=lambda x: -x["savings_usd_month"])[:8]
    ]

    cost_drivers = cost_drivers_live(warehouse_id, None)
    payload = {
        "total_spend_usd_month": round(total_spend),
        "total_savings_usd_month": round(total_savings),
        "savings_pct": round(save_pct, 3),
        "savings_estimated": True,
        "total_dbus_month": round(total_dbus),
        "num_workspaces": len(facts),
        "num_critical": sum(1 for w in facts if w["health"] == "Critical"),
        "trend": trend,
        "top_opportunities": top_opportunities,
        "cost_drivers": cost_drivers,
    }
    # Exec-summary extras (MoM delta, MAU/queries deltas, rolled savings,
    # needs-attention). Merged in best-effort so a failure never blocks the base
    # overview — the landing view degrades to the fields it does have.
    try:
        payload.update(_overview_summary_extras(warehouse_id, total_spend, cost_drivers))
    except Exception:  # noqa: BLE001
        pass
    return payload


# ---------------------------------------------------------------------------
# Data Quality Monitoring — inventory from system.data_quality_monitoring +
# the DATA_QUALITY_MONITORING billing cost tie-in. Fields the system table
# doesn't carry (drift PSI/KS, null %, per-table cost) are left blank.
# ---------------------------------------------------------------------------
def _dq_quality(status: str) -> str:
    s = (status or "").upper()
    if "UNHEALTHY" in s or "ERROR" in s or "FAIL" in s:
        return "Critical"
    if "HEALTHY" in s or "OK" in s:
        return "Good"
    return "Warning"


def _dq_freshness(status: str) -> str:
    s = (status or "").upper()
    if "STALE" in s or "UNHEALTHY" in s:
        return "Stale"
    if "HEALTHY" in s or "FRESH" in s or "OK" in s:
        return "Fresh"
    return "OK"


def dqm_live(warehouse_id: str) -> dict[str, Any]:
    """Live Data Quality Monitoring: real monitored-table inventory from
    system.data_quality_monitoring.table_results + real DATA_QUALITY_MONITORING
    cost by workspace from billing."""
    # freshness.status + downstream_impact.* are nested structs that aren't in
    # every DQM schema version → a missing one is a hard error. Fall back to a
    # struct-less SELECT so the DQM tab renders inventory instead of 503-ing.
    try:
        inv = _run(warehouse_id,
            "SELECT catalog_name, schema_name, table_name, status, freshness.status AS fstatus, "
            "timestampdiff(HOUR, event_time, current_timestamp()) AS hours_ago, "
            "downstream_impact.num_downstream_tables AS downstream "
            "FROM system.data_quality_monitoring.table_results "
            "QUALIFY row_number() OVER (PARTITION BY catalog_name, schema_name, table_name ORDER BY event_time DESC) = 1",
            "system.data_quality_monitoring")
    except LiveError:
        inv = _run(warehouse_id,
            "SELECT catalog_name, schema_name, table_name, status, CAST(NULL AS STRING) AS fstatus, "
            "timestampdiff(HOUR, event_time, current_timestamp()) AS hours_ago, 0 AS downstream "
            "FROM system.data_quality_monitoring.table_results "
            "QUALIFY row_number() OVER (PARTITION BY catalog_name, schema_name, table_name ORDER BY event_time DESC) = 1",
            "system.data_quality_monitoring (no structs)")
    monitors: list[dict[str, Any]] = []
    for r in inv:
        cat, sch, tbl = str(r.get("catalog_name")), str(r.get("schema_name")), str(r.get("table_name"))
        quality = _dq_quality(str(r.get("status")))
        fresh = _dq_freshness(str(r.get("fstatus")))
        hours = int(_f(r.get("hours_ago")))
        monitors.append({
            "fqn": f"{cat}.{sch}.{tbl}", "catalog": cat, "schema": sch, "table": tbl,
            "monitor_type": "—", "workspace": "—",
            "profile_metrics_table": "", "drift_metrics_table": "", "dashboard_url": "",
            "schedule": "", "schedule_label": "—",
            "last_refresh_hours": hours, "freshness": fresh,
            "num_metrics": 0, "null_pct": 0.0, "drift_psi": 0.0, "drift_ks": 0.0,
            "anomalies": 0, "alerts": 0, "alert_status": "—", "quality_status": quality,
            "dbus_month": 0, "cost_usd_month": 0.0,
            "value": "high" if int(_f(r.get("downstream"))) > 0 else "low",
            "prune_candidate": fresh == "Stale",
        })
    monitors.sort(key=lambda m: ({"Critical": 0, "Warning": 1, "Good": 2}.get(m["quality_status"], 3), m["fqn"]))

    cost = _run(warehouse_id,
        "SELECT CAST(u.workspace_id AS STRING) ws, SUM(u.usage_quantity) dbus, "
        "SUM(u.usage_quantity*lp.pricing.effective_list.default) usd FROM system.billing.usage u "
        "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
        "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
        "WHERE u.billing_origin_product='DATA_QUALITY_MONITORING' AND u.usage_date>=date_trunc('MONTH',current_date()) "
        "GROUP BY 1 ORDER BY usd DESC", "system.billing.usage (DQM cost)")
    ws_names = _workspace_names(warehouse_id)
    by_workspace = [{"workspace": ws_names.get(str(r.get("ws")), str(r.get("ws"))),
                     "workspace_id": str(r.get("ws")), "num_monitors": 0,
                     "cost_usd_month": round(_f(r.get("usd")), 2), "dbus_month": round(_f(r.get("dbus")), 1)} for r in cost]
    total_cost = round(sum(w["cost_usd_month"] for w in by_workspace), 2)
    total_dbus = round(sum(w["dbus_month"] for w in by_workspace), 1)

    prune = [{"fqn": m["fqn"], "workspace": m["workspace"], "cost_usd_month": 0.0,
              "freshness": m["freshness"], "value": m["value"], "anomalies": m["anomalies"]}
             for m in monitors if m["prune_candidate"]]

    # Summary reflects ALL monitors; the returned list is capped (a big estate
    # can have tens of thousands) — critical/warning already sorted first.
    total_monitors = len(monitors)
    capped = monitors[:300]
    return {
        "monitors": capped,
        "summary": {
            "num_monitors": total_monitors, "num_visible": len(capped),
            "num_critical": sum(1 for m in monitors if m["quality_status"] == "Critical"),
            "num_warning": sum(1 for m in monitors if m["quality_status"] == "Warning"),
            "num_stale": sum(1 for m in monitors if m["freshness"] == "Stale"),
            "num_alerts_triggered": 0,
            "dqm_cost_usd_month": total_cost, "dqm_dbus_month": total_dbus,
            "num_prune_candidates": len(prune), "prune_savings_usd_month": 0.0,
        },
        "by_workspace": by_workspace,
        "prune_candidates": prune,
        "billing_line": "DATA_QUALITY_MONITORING",
        "dbu_list_price_usd": 0.0,
        "caveat": "Live: inventory + quality/freshness from system.data_quality_monitoring; cost from billing. Drift, null %, alerts and per-table cost aren't in the system table, so they read blank.",
    }


# ---------------------------------------------------------------------------
# Governed tags + Data Classification — from the tag system tables.
# Auto-classification writes semantic tags to columns (tag_name LIKE 'class.%',
# e.g. class.email_address, class.name), and governed tags land in table_tags
# (system.certification_status, domain, team…). The dedicated
# system.data_classification.results table needs its own grant; the readable
# information_schema tag tables give the same signal.
# ---------------------------------------------------------------------------
_GOV_TAG_KEYS = ["system.certification_status", "domain", "data_classification", "team", "layer", "product"]


_AI_PRODUCTS_SET = "('MODEL_SERVING','AI_GATEWAY','VECTOR_SEARCH','AGENT_BRICKS','AI_FUNCTIONS','FOUNDATION_MODEL_TRAINING')"


def adoption_live(warehouse_id: str) -> dict[str, Any]:
    """Adoption & Value from real system tables: active users + queries from
    system.query.history, feature breadth + Genie/AI uptake from billing, and
    the Value Map from system.access.table_lineage (per-table reads + users)
    joined to information_schema.tables for recency."""
    mau = _run(warehouse_id,
        "SELECT COUNT(DISTINCT executed_by) mau, COUNT(*) q, "
        "SUM(total_duration_ms)/3600000.0 qhours FROM system.query.history "
        "WHERE start_time>=dateadd(day,-30,current_date())", "system.query.history (MAU)")
    wau = _run(warehouse_id,
        "SELECT COUNT(DISTINCT executed_by) wau FROM system.query.history "
        "WHERE start_time>=dateadd(day,-7,current_date())", "system.query.history (WAU)")
    mau_n = int(_f(mau[0].get("mau"))) if mau else 0
    total_q = int(_f(mau[0].get("q"))) if mau else 0
    query_hours = round(_f(mau[0].get("qhours")), 1) if mau else 0.0
    wau_n = int(_f(wau[0].get("wau"))) if wau else 0

    # Feature breadth + Genie/AI adoption per workspace (billing).
    feat = _run(warehouse_id,
        "SELECT CAST(workspace_id AS STRING) ws, COUNT(DISTINCT billing_origin_product) breadth, "
        "MAX(CASE WHEN billing_origin_product='GENIE' THEN 1 ELSE 0 END) genie, "
        f"MAX(CASE WHEN billing_origin_product IN {_AI_PRODUCTS_SET} THEN 1 ELSE 0 END) ai "
        "FROM system.billing.usage WHERE usage_date>=dateadd(day,-30,current_date()) GROUP BY 1 ORDER BY breadth DESC",
        "system.billing.usage (feature breadth)")
    ws_names = _workspace_names(warehouse_id)
    features = [{"workspace": ws_names.get(str(r.get("ws")), str(r.get("ws"))),
                 "workspace_id": str(r.get("ws")), "products": [], "breadth": int(_f(r.get("breadth"))),
                 "genie": bool(_f(r.get("genie"))), "ai": bool(_f(r.get("ai")))} for r in feat]
    # Distinct products ACROSS the estate — the denominator the breadth bars are
    # drawn against ("breadth / num_products"). Was max single-workspace breadth,
    # which made the top workspace always read 100%; now matches the demo's
    # "product universe" semantics.
    prod = _run(warehouse_id,
        "SELECT COUNT(DISTINCT billing_origin_product) n FROM system.billing.usage "
        "WHERE usage_date>=dateadd(day,-30,current_date())", "billing (distinct products)")
    num_products = int(_f(prod[0].get("n"))) if prod else 0

    # Top users by query volume.
    tu = _run(warehouse_id,
        "SELECT executed_by u, CAST(workspace_id AS STRING) ws, COUNT(*) q, "
        "SUM(total_duration_ms)/3600000.0 qhours, "
        "datediff(HOUR, MAX(start_time), current_timestamp())/24 dlast FROM system.query.history "
        "WHERE start_time>=dateadd(day,-30,current_date()) AND executed_by IS NOT NULL "
        "GROUP BY 1,2 ORDER BY q DESC LIMIT 50", "system.query.history (top users)")
    # Optional user->team mapping (fail-soft to {}; UI gated on config.team_enabled).
    from data.adoption import team_rollup as _team_rollup
    team_by_user = _user_team(warehouse_id)
    users = [{"user": str(r.get("u")), "workspace": ws_names.get(str(r.get("ws")), str(r.get("ws"))),
              "workspace_id": str(r.get("ws")), "queries": int(_f(r.get("q"))),
              "query_hours": round(_f(r.get("qhours")), 1),
              "last_active_days": int(_f(r.get("dlast"))),
              "team": team_by_user.get(str(r.get("u")), "")} for r in tu]
    team_rollup = _team_rollup(users)

    # Value map: per-table usage (lineage reads) + users + recency.
    # Driven off the tables universe (information_schema.tables) LEFT JOIN reads,
    # NOT off lineage — so zero-read tables (the archive candidates) actually
    # appear. A lineage-first source only ever yields reads>=1, which is why
    # archive_candidates always came back 0. The UNION guarantees both the
    # high-read "gold" tail and the zero-read+stale "archive" tail survive the cap.
    assets_raw = _run(warehouse_id,
        "WITH r AS (SELECT target_table_full_name fqn, COUNT(*) reads, "
        "COUNT(DISTINCT created_by) users FROM system.access.table_lineage "
        "WHERE event_time>=dateadd(day,-30,current_date()) "
        "AND target_table_full_name IS NOT NULL GROUP BY 1), "
        "t AS (SELECT concat_ws('.', table_catalog, table_schema, table_name) fqn, "
        "datediff(current_date(), CAST(last_altered AS DATE)) days "
        "FROM system.information_schema.tables "
        "WHERE table_catalog NOT IN ('system','samples')), "
        "j AS (SELECT t.fqn, COALESCE(r.reads,0) reads, COALESCE(r.users,0) users, t.days "
        "FROM t LEFT JOIN r ON r.fqn = t.fqn) "
        "(SELECT * FROM j WHERE reads>0 ORDER BY reads DESC LIMIT 150) "
        "UNION ALL "
        "(SELECT * FROM j WHERE reads=0 AND days>30 ORDER BY days DESC LIMIT 150)",
        "system.access.table_lineage (value map)")
    # Median over tables that were actually read, matching the demo's intent
    # (zeros would otherwise drag the gold threshold to 0 and label everything gold).
    nonzero_reads = sorted(int(_f(r.get("reads"))) for r in assets_raw if int(_f(r.get("reads"))) > 0)
    med = nonzero_reads[len(nonzero_reads) // 2] if nonzero_reads else 0
    assets = []
    for r in assets_raw:
        reads = int(_f(r.get("reads"))); u = int(_f(r.get("users"))); days = int(_f(r.get("days")))
        if reads == 0:
            quad = "Archive candidate"
        elif reads > med and u > 1:
            quad = "Gold asset"
        else:
            quad = "Standard"
        assets.append({"table": str(r.get("fqn")), "workspace": "—", "total_queries": reads,
                       "unique_users": u, "days_since_update": days, "quadrant": quad})

    genie_adopters = sum(1 for f in features if f["genie"])
    ai_adopters = sum(1 for f in features if f["ai"])
    return {
        "month": "",
        "assets": assets,
        "users": users,
        "team_rollup": team_rollup,
        "features": features,
        "mau_trend": [],
        "summary": {
            "mau": mau_n, "wau": wau_n, "total_queries_month": total_q, "query_hours_month": query_hours,
            "avg_breadth": round(sum(f["breadth"] for f in features) / len(features), 1) if features else 0.0,
            "num_products": num_products,
            "genie_adopters": genie_adopters, "ai_adopters": ai_adopters,
            "num_workspaces": len(features),
            "gold_assets": sum(1 for a in assets if a["quadrant"] == "Gold asset"),
            "archive_candidates": sum(1 for a in assets if a["quadrant"] == "Archive candidate"),
        },
    }


def _live_table_defaults() -> dict[str, Any]:
    """All the file-hygiene fields the Tables UI reads, defaulted for live.
    Sizes are filled from the nightly gold census (finops_gold_table_sizes) on
    load; the deeper file-hygiene stats come from DESCRIBE DETAIL on drill-down.
    These zero-defaults apply only until the gold census has run / for tables it
    hasn't sized yet."""
    return {
        "size_bytes": 0, "size_gb": 0.0, "num_files": 0, "avg_file_mb": 0.0,
        "small_file_ratio": 0.0, "last_optimize": "—", "last_vacuum": "—",
        "partitioned": False, "over_partitioned": False, "clustered": False,
        "po_enabled": False, "lc_enabled": False, "stale_ratio": 0.0,
        "reclaimable_gb": 0.0, "reclaimable_usd_month": 0.0, "scan_cost_usd_month": 0.0,
        "optimize_saving_usd_month": 0.0, "vacuum_saving_usd_month": 0.0,
        "convert_saving_usd_month": 0.0,
        "po_lc_saving_usd_month": 0.0, "est_savings_usd_month": 0.0,
        "needs_optimize": False, "needs_vacuum": False, "needs_clustering": False, "needs_action": False,
        "reads_30d": 0, "days_since_read": 0, "cold": False, "size_snapshot": "",
        # recommendation is an InsightType enum on the UI — use "monitor" (no
        # bulk-derivable action) rather than a prose string, which rendered blank.
        "recommendation": "monitor",
        # conversion_path MUST default to None (null): [] is truthy in JS and the
        # EXTERNAL drill-down then calls .steps.join() on it → crash.
        "rationale": "", "next_steps": [], "benefits": [], "conversion_path": None,
        "caveat": "Live: table_type + freshness from information_schema, usage recency from access.table_lineage, and sizes from the nightly gold census (finops_gold_table_sizes). File-hygiene stats (small-file ratio, OPTIMIZE/VACUUM specifics) are computed via DESCRIBE DETAIL on drill-down.",
    }


def table_detail_live(warehouse_id: str, fqn: str) -> dict[str, Any] | None:
    """Live drill-down for one table. Runs DESCRIBE DETAIL for physical stats
    (size, numFiles, clustering/partition cols) — deferred to drill-down so the
    Tables page never scans every table on load (per storage-metrics research:
    there is no size system table; DESCRIBE DETAIL is the metadata-only source).
    Fixes the '0 bytes for everyone' report. Fail-soft: real workspace/table
    always renders, missing grant just leaves defaults."""
    parts = fqn.split(".")
    if len(parts) != 3:
        return None
    # fqn is request-derived (URL path) — validate against the UC-identifier
    # allowlist before interpolating into DESCRIBE DETAIL, matching every other
    # request→SQL path (a backtick in a part would otherwise break the quoting).
    if not _UC_TABLE_RE.match(fqn):
        return None
    cat, sch, tbl = parts
    base = {**_live_table_defaults(), "fqn": fqn, "catalog": cat, "schema": sch, "table": tbl}
    try:
        det = _run(warehouse_id, f"DESCRIBE DETAIL `{cat}`.`{sch}`.`{tbl}`", "table detail (DESCRIBE DETAIL)")
    except Exception:
        return base
    if det:
        d = det[0]
        size = _f(d.get("sizeInBytes")); nf = int(_f(d.get("numFiles")))
        # DESCRIBE DETAIL returns these as ARRAY cells, which the Statement
        # Execution API serialises as a JSON STRING ("[]" for none). bool("[]")
        # is True, so bool(d.get(...) or []) would report EVERY table as
        # partitioned+clustered. Parse via _arr first (same trap as collect_set).
        clustering = _arr(d.get("clusteringColumns"))
        partitions = _arr(d.get("partitionColumns"))
        avg_mb = (size / nf / 1e6) if nf else 0.0
        base.update({
            "size_bytes": round(size), "size_gb": round(size / 1e9, 3),
            "num_files": nf, "avg_file_mb": round(avg_mb, 1),
            "small_file_ratio": round(max(0.0, min(1.0, 1 - (avg_mb / 128.0))), 3) if avg_mb else 0.0,
            "partitioned": bool(partitions), "clustered": bool(clustering),
            "caveat": "Live: physical stats from DESCRIBE DETAIL on this table (computed on drill-down).",
        })
    return base


def _gold_table_sizes(warehouse_id: str) -> dict[str, dict[str, Any]]:
    """fqn -> {size_bytes, num_files, snapshot_date} from the latest snapshot in
    <gold_schema>.finops_gold_table_sizes (the nightly census). {} when no gold
    schema is configured or the table doesn't exist yet — the Tables list then
    just shows 0 sizes until the first gold run, unchanged from before.

    Uses the SAME warehouse the caller is already reading with — NOT a re-read of
    config.warehouse_id, which can be blank/different and would silently blank all
    sizes (the '0 bytes for everyone' symptom, hidden behind a fail-soft)."""
    from data import config

    gold = str(config.get_settings().get("gold_schema") or "").strip()
    # gold_schema is a catalog.schema identifier; validate before interpolating.
    if not _SCHEMA_RE.match(gold):
        return {}
    try:
        rows = _run(warehouse_id,
            f"SELECT concat_ws('.', catalog, schema, table) fqn, size_bytes, num_files, "
            f"partitioned, clustered, po_enabled, "
            f"CAST(snapshot_date AS STRING) snapshot_date FROM {gold}.finops_gold_table_sizes "
            f"WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM {gold}.finops_gold_table_sizes)",
            "gold table sizes")
    except Exception:  # noqa: BLE001 — no gold census yet → sizes stay 0
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        fqn = str(r.get("fqn") or "")
        if fqn:
            # API returns booleans as the strings "true"/"false".
            _b = lambda v: str(v).strip().lower() == "true"
            out[fqn] = {"size_bytes": round(_f(r.get("size_bytes"))), "num_files": int(_f(r.get("num_files"))),
                        "partitioned": _b(r.get("partitioned")), "clustered": _b(r.get("clustered")),
                        "po_enabled": _b(r.get("po_enabled")),
                        "snapshot_date": str(r.get("snapshot_date") or "")}
    return out


def tables_live(warehouse_id: str) -> list[dict[str, Any]]:
    """Live Tables list + health WITHOUT scanning every table. Ranks tables by
    real usage (access.table_lineage) and joins freshness (information_schema) —
    the bulk health signals (DQM has its own tab, deliberately not folded in) —
    and defers file-hygiene (DESCRIBE DETAIL) to drill-down."""
    # Drive the list FROM information_schema.tables — the AUTHORITATIVE source of
    # table_type (MANAGED vs EXTERNAL) — and LEFT JOIN lineage for usage/recency.
    # (Previously this ran the other way, FROM lineage LEFT JOIN tables, so any
    # join miss left table_type NULL → defaulted to MANAGED, and external/rarely-
    # read tables never appeared at all. a customer saw "everything managed". #3.)
    rows = _run(warehouse_id,
        "WITH u AS (SELECT target_table_full_name fqn, COUNT(*) reads, "
        "  datediff(DAY, MAX(event_time), current_timestamp()) days_since_read "
        "  FROM system.access.table_lineage WHERE event_time>=dateadd(day,-30,current_date()) "
        "  AND target_table_full_name IS NOT NULL GROUP BY 1), "
        "tbl AS (SELECT concat_ws('.',table_catalog,table_schema,table_name) fqn, table_catalog c, "
        "  table_schema s, table_name t, table_type tt, datediff(DAY, CAST(last_altered AS DATE), current_date()) age "
        "  FROM system.information_schema.tables "
        # Exclude system AND samples: samples is read-only public demo data (nyctaxi/
        # tpch/…), not the customer's estate, and it has no row in the size census
        # (gold excludes it too) — so including it polluted the Tables tab with
        # heavily-read, zero-size rows that crowded out real tables. Match the gold
        # census + adoption filter exactly so FQNs join and the list is the estate.
        "  WHERE table_schema<>'information_schema' AND table_catalog NOT IN ('system','samples')) "
        "SELECT tbl.fqn, u.reads, u.days_since_read, tbl.c, tbl.s, tbl.t, tbl.tt, tbl.age "
        "FROM tbl LEFT JOIN u ON tbl.fqn=u.fqn "
        "ORDER BY u.reads DESC NULLS LAST, tbl.age ASC LIMIT 200",
        "system.information_schema.tables + lineage")

    # DQM is intentionally NOT folded into Tables health — Data Quality has its
    # own dedicated tab. Tables health here = freshness + usage recency + file
    # hygiene only, so the two tabs don't overlap/confuse.

    # Bulk sizes from the nightly gold census (finops_gold_table_sizes) — the only
    # scalable size source (R3: no size system table). {} when no gold schema /
    # not yet built → sizes stay 0 on the list and the drill-down DESCRIBE DETAIL
    # fills them on demand, exactly as before.
    size_map = _gold_table_sizes(warehouse_id)

    out: list[dict[str, Any]] = []
    for r in rows:
        r = dict(r)
        fqn = str(r.get("fqn") or "")
        parts = fqn.split(".")
        cat = str(r.get("c") or (parts[0] if len(parts) > 2 else ""))
        sch = str(r.get("s") or (parts[1] if len(parts) > 2 else ""))
        tbl = str(r.get("t") or (parts[-1] if parts else fqn))
        # table_type comes straight from information_schema (authoritative). We
        # NO LONGER default a missing type to MANAGED — VIEW/other types pass
        # through, and EXTERNAL is honoured, so the managed/external split is real.
        tt = str(r.get("tt") or "").upper()
        ttype = "EXTERNAL" if "EXTERNAL" in tt else ("MANAGED" if "MANAGED" in tt else (tt.title() or "Unknown"))
        age = int(_f(r.get("age")))              # days since last_altered (freshness)
        never_read = r.get("days_since_read") is None
        days_read = int(_f(r.get("days_since_read")))
        read_txt = "never queried (30d)" if never_read else f"last queried {days_read}d ago"
        # Bulk health: freshness + usage recency only (DQM lives on its own tab).
        # Never-read tables are treated as stale (a cost/hygiene signal, not "fresh").
        if age > 180 or never_read or days_read > 30:
            health, reason = "Critical", f"stale — updated {age}d ago, {read_txt}"
        elif age > 60 or days_read > 14:
            health, reason = "Warning", f"aging — updated {age}d ago, {read_txt}"
        else:
            health, reason = "Good", f"fresh ({age}d) and actively queried ({days_read}d ago)"
        row = _live_table_defaults()
        row.update({
            "fqn": fqn, "catalog": cat, "schema": sch, "table": tbl, "table_type": ttype,
            "health": health, "health_reason": reason,
            "days_since_read": days_read, "reads_30d": int(_f(r.get("reads"))),
        })
        # Enrich with the precomputed size + derive the file-hygiene basics.
        sz = size_map.get(fqn)
        if sz:
            size_bytes = sz["size_bytes"]; nf = sz["num_files"]
            avg_mb = (size_bytes / nf / 1e6) if nf else 0.0
            # Cold = large + rarely read: never queried in 30d (or >30d ago) and
            # material size. A measured fact (size from gold, recency from lineage)
            # → a VACUUM/retention/archive candidate (#15).
            cold = (days_read > 30 or int(_f(r.get("reads"))) == 0) and size_bytes >= 1e9
            small_ratio = round(max(0.0, min(1.0, 1 - (avg_mb / 128.0))), 3) if avg_mb else 0.0
            part = bool(sz.get("partitioned")); clus = bool(sz.get("clustered")); po = bool(sz.get("po_enabled"))
            row.update({
                "size_bytes": size_bytes, "size_gb": round(size_bytes / 1e9, 3),
                "num_files": nf, "avg_file_mb": round(avg_mb, 1),
                "small_file_ratio": small_ratio,
                # Real DESCRIBE DETAIL facts, now stored in the gold census.
                "partitioned": part, "clustered": clus, "lc_enabled": clus, "po_enabled": po,
                # Honest non-$ hygiene flags derived from real file stats.
                "needs_optimize": (avg_mb > 0 and avg_mb < 128 and small_ratio >= 0.4),
                "needs_clustering": (not part and not clus and size_bytes >= 50e9),
                "size_snapshot": sz.get("snapshot_date", ""),
                "cold": bool(cold),
            })
            row["needs_action"] = bool(row["needs_optimize"] or row["needs_clustering"] or cold)
        # Derive the recommendation from the row's REAL hygiene flags (was a
        # constant "monitor" for every row, which contradicted the needs-OPTIMIZE
        # KPI). Priority: convert external → optimize small files → cluster large
        # unclustered → vacuum cold → monitor. All InsightType enum values.
        if ttype == "EXTERNAL":
            row["recommendation"] = "convert-to-managed"
            row["rationale"] = "External table — can't use Predictive Optimization or UC-managed file maintenance until converted."
        elif row.get("needs_optimize"):
            row["recommendation"] = "optimize"
            row["rationale"] = f"Small-file ratio {row.get('small_file_ratio', 0):.0%} (avg file {row.get('avg_file_mb', 0):.0f} MB) — OPTIMIZE would compact them."
        elif row.get("needs_clustering"):
            row["recommendation"] = "enable-LC"
            row["rationale"] = f"Large ({row.get('size_gb', 0):.0f} GB) and neither partitioned nor clustered — Liquid Clustering cuts scan/pruning cost."
        elif row.get("cold"):
            row["recommendation"] = "vacuum"
            # Set the needs_vacuum flag the /api/tables?needs_action=vacuum filter
            # keys on — cold tables are the vacuum candidates. Without it that chip
            # matched zero rows in live even when vacuum recs existed.
            row["needs_vacuum"] = True
            row["rationale"] = f"Large + rarely read ({row.get('days_since_read', 0)}d since last query) — VACUUM/retention reclaims storage."
        else:
            row["recommendation"] = "monitor"
            row["rationale"] = "No file-hygiene or layout issue detected from the current metadata — keep monitoring."
        # Populate the drill-down "Next steps" for the derived recommendation
        # (live tables previously emitted next_steps=[] → an empty section).
        row["next_steps"] = list(recommender._NEXT_STEPS.get(row["recommendation"], []))
        out.append(row)
    return out


def _principal_type(name: str) -> str:
    # LOWERCASE to match the demo generator + the frontend, which filters on
    # p.type === "user"/"group"/"service_principal" and styles pills on those keys.
    # Returning uppercase made all three Access principal-type KPI cards read 0 and
    # the pills render unstyled in live mode.
    n = (name or "").lower()
    if "@" in n:
        return "user"
    # SP client-ids are UUIDs; common svc naming otherwise
    if n.count("-") >= 4 or n.startswith("svc") or "service" in n:
        return "service_principal"
    return "group"


def access_grants_live(warehouse_id: str, limit: int = 20000) -> dict[str, Any]:
    """Grant graph from system.information_schema.table_privileges, shaped into
    the Access page's principals / grants / risks payloads. Heavy on a big
    estate — intended to be run by the gold roll-up (cached), not per request.
    Fields not in privileges (entitlements, membership, last-active, direct-vs-
    group) are left blank/0.

    ORDER BY is deterministic and the cap is high (with _run now paging chunks);
    if we still hit the cap we set ``truncated`` so the UI can warn instead of
    silently under-reporting grants (which would hide real over-privilege on a
    security panel)."""
    # Grant graph spans ALL securable levels — catalog / schema / table / routine —
    # not just tables. A catalog- or schema-level ALL PRIVILEGES grant is the most
    # serious over-privilege there is, and a table-only read made it invisible in
    # live (demo shows it). Each view is queried + wrapped independently so a single
    # missing/ungranted privilege view degrades that level only, never blanks the
    # whole graph. `inherited_from` (when non-NULL) means the grant is inherited
    # from a parent securable, not granted directly here.
    _per_view = max(1, limit // 4)
    _sources = [
        ("table", "system.information_schema.table_privileges",
         "SELECT grantee, privilege_type, table_catalog cat, table_schema sch, table_name tbl, inherited_from inh"),
        ("schema", "system.information_schema.schema_privileges",
         "SELECT grantee, privilege_type, catalog_name cat, schema_name sch, CAST(NULL AS STRING) tbl, inherited_from inh"),
        ("catalog", "system.information_schema.catalog_privileges",
         "SELECT grantee, privilege_type, catalog_name cat, CAST(NULL AS STRING) sch, CAST(NULL AS STRING) tbl, inherited_from inh"),
        ("routine", "system.information_schema.routine_privileges",
         "SELECT grantee, privilege_type, specific_catalog cat, specific_schema sch, specific_name tbl, inherited_from inh"),
    ]
    rows: list[dict[str, Any]] = []
    truncated = False
    for sec_type, tbl_fqn, proj in _sources:
        try:
            part = _run(warehouse_id,
                f"{proj} FROM {tbl_fqn} WHERE grantee IS NOT NULL "
                f"ORDER BY grantee, cat, sch, tbl LIMIT {_per_view}", tbl_fqn)
        except LiveError:
            continue  # this securable level unreadable → degrade it, keep the rest
        truncated = truncated or len(part) >= _per_view
        for r in part:
            r["_sec_type"] = sec_type
            rows.append(r)
    grants: list[dict[str, Any]] = []
    pgrants: dict[str, int] = {}
    for i, r in enumerate(rows):
        grantee = str(r.get("grantee")); cat = str(r.get("cat") or "")
        sch = str(r.get("sch") or ""); tbl = str(r.get("tbl") or "")
        priv = str(r.get("privilege_type") or "")
        sec_type = r.get("_sec_type", "table")
        # Securable string + parent are level-aware (catalog / catalog.schema / full fqn).
        securable = ".".join(p for p in (cat, sch, tbl) if p)
        parent = ".".join(p for p in (cat, sch) if p) if tbl else (cat if sch else "")
        # information_schema.*_privileges.inherited_from is the STRING 'NONE' for a
        # direct grant (NOT NULL), else the ancestor securable's name. Treat NULL/
        # ''/'NONE' (any case) as direct — otherwise every grant reads INHERITED.
        inh = r.get("inh")
        inh_s = str(inh).strip() if inh is not None else ""
        is_inherited = bool(inh_s) and inh_s.upper() != "NONE"
        grants.append({
            "id": i, "principal": grantee, "principal_type": _principal_type(grantee),
            "securable_type": sec_type, "securable": securable,
            "catalog": cat, "parent": parent, "privilege": priv,
            "granted_via": "INHERITED" if is_inherited else "DIRECT",
            "inherited_from": inh_s if is_inherited else None,
        })
        pgrants[grantee] = pgrants.get(grantee, 0) + 1
    principals = sorted(
        # member_count / num_direct_members / num_entitlements / last_active are
        # NOT derivable from the grants query (they come from group membership +
        # audit, via admin-sync). Emit None, not 0/"", so nothing renders a
        # fabricated "0 members / active never" for a principal whose membership
        # simply wasn't measured here. (The group-tree view has real counts.)
        [{"principal": p, "type": _principal_type(p), "source": "UC", "status": "Active",
          "last_active": None, "num_entitlements": None, "num_grants": n, "member_count": None,
          "num_direct_members": None} for p, n in pgrants.items()],
        key=lambda x: -x["num_grants"])
    # Over-privilege risk is driven by privilege SEVERITY, not a name substring —
    # a broad ALL PRIVILEGES / MODIFY grant is worth surfacing on ANY securable,
    # not only ones literally named prod/gold (that gate hid risk on catalogs like
    # 'main'/'edw'). Name match just bumps severity.
    # Risk shape MUST match the UI/demo contract: severity in {critical,warning}
    # and risk = a kind SLUG the Flag column + filter use (over-privileged,
    # broad-grant, direct-to-user, …) — not "High/Medium" + a prose label, which
    # broke the severity pill/sort and made the Flag filter match nothing.
    _PUBLIC = {"account users", "users", "public"}
    risks: list[dict[str, Any]] = []
    for g in grants:
        principal = g["principal"]
        priv = g["privilege"]
        sec = g["securable"]
        is_public = str(principal).strip().lower() in _PUBLIC
        prod_named = ("prod" in sec.lower() or "gold" in sec.lower())
        if priv in ("ALL PRIVILEGES", "MODIFY"):
            if is_public:
                # A broad write/all grant to everyone is the most serious.
                risks.append({"id": len(risks), "principal": principal, "risk": "broad-grant",
                              "severity": "critical",
                              "detail": f"{priv} granted to all users on {sec}",
                              "recommended_action": "Remove the account-wide grant; scope to a specific group"})
            else:
                sev = "critical" if (priv == "ALL PRIVILEGES" or prod_named) else "warning"
                risks.append({"id": len(risks), "principal": principal, "risk": "over-privileged",
                              "severity": sev,
                              "detail": f"{priv} on {sec}",
                              "recommended_action": "Replace with least-privilege SELECT / scoped grant"})
        elif is_public and priv == "SELECT" and prod_named:
            risks.append({"id": len(risks), "principal": principal, "risk": "broad-grant",
                          "severity": "warning",
                          "detail": f"SELECT granted to all users on {sec}",
                          "recommended_action": "Scope read access to the teams that need it"})
        # Direct-to-user write grant (not via a group) — a governance smell. Only
        # flag genuinely DIRECT grants; an INHERITED write isn't a direct-to-user grant.
        if (priv in ("ALL PRIVILEGES", "MODIFY") and str(g.get("principal_type", "")).lower() == "user"
                and g.get("granted_via") != "INHERITED"):
            risks.append({"id": len(risks), "principal": principal, "risk": "direct-to-user",
                          "severity": "warning",
                          "detail": f"{priv} granted directly to a user on {sec}",
                          "recommended_action": "Grant via a group, not directly to individual users"})
    return {"grants": grants, "principals": principals, "risks": risks, "truncated": truncated,
            "summary": {"num_grants": len(grants), "num_principals": len(principals),
                        "num_risks": len(risks), "truncated": truncated}}


def access_groups_live(warehouse_id: str) -> list[dict[str, Any]]:
    """Account/workspace groups via the SDK (SCIM) — not available as SQL."""
    try:
        w = _client()
        groups = list(w.groups.list())
    except Exception as e:
        raise LiveError("groups (SCIM API)", str(e)[:150])
    return [{"group": g.display_name or "", "id": g.id or "",
             "member_count": len(g.members or []),
             "members": [m.display for m in (g.members or [])[:50]]} for g in groups]


def queries_live(warehouse_id: str, days: int = 7) -> list[dict[str, Any]]:
    """Query Advisor from real system.query.history: aggregate recurring
    statements into raw metrics (spill, pruning, queued, duration, cost proxy),
    then run them through the SAME recommender as demo (enrich_query) so the
    insights/savings are computed identically. Cost is a duration-based estimate
    (per-statement $ isn't in query.history). ``days`` is the trailing run window
    the UI's time-range chip selects (24h/7d/30d) — it scopes the query.history
    predicate so the chip actually changes the result in live (was hardcoded 7d)."""
    days = max(1, int(days))
    rows = _run(warehouse_id,
        "SELECT compute.warehouse_id wh, CAST(workspace_id AS STRING) ws, "
        "any_value(executed_by) usr, any_value(statement_id) sid, "
        # Full statement for the drill-down. 16000 chars covers ~all real
        # statements while bounding the payload (~60 rows). statement_text has no
        # published hard limit; the box scrolls. GROUP BY dedup key stays 120.
        "substring(any_value(statement_text),1,16000) txt, "
        # Query source (was hardcoded 'SQL'): job/dashboard/alert/notebook else the
        # statement kind, so the Src column reflects where the query came from.
        "any_value(CASE WHEN query_source.job_info.job_id IS NOT NULL THEN 'Job' "
        "  WHEN query_source.legacy_dashboard_id IS NOT NULL OR query_source.dashboard_id IS NOT NULL THEN 'Dashboard' "
        "  WHEN query_source.alert_id IS NOT NULL THEN 'Alert' "
        "  WHEN query_source.notebook_id IS NOT NULL THEN 'Notebook' "
        "  WHEN query_source.sql_query_id IS NOT NULL THEN 'SQL editor' "
        "  ELSE COALESCE(statement_type,'SQL') END) src, "
        # Specific origin id (job/dashboard/notebook) for the drill-down.
        "any_value(COALESCE(CAST(query_source.job_info.job_id AS STRING), "
        "  query_source.dashboard_id, query_source.legacy_dashboard_id, "
        "  query_source.notebook_id, query_source.alert_id)) src_id, "
        "COUNT(*) runs, "
        "approx_percentile(execution_duration_ms,0.5)/1000 p50, "
        "approx_percentile(execution_duration_ms,0.95)/1000 p95, "
        "AVG(read_bytes) bytes_read, "
        "SUM(pruned_files)/NULLIF(SUM(pruned_files+read_files),0) pruning, "
        "SUM(spilled_local_bytes)/1e9 spill_gb, "
        "SUM(waiting_at_capacity_duration_ms)/NULLIF(SUM(total_duration_ms),0) queued, "
        # Duration-based cost proxy — use the single canonical DBU_HOUR_PROXY_USD
        # (was a hardcoded 0.55 that would silently diverge from the warehouse/
        # cluster tabs + every est_savings derived from this cost if retuned).
        f"SUM(total_duration_ms)/3600000.0 * {DBU_HOUR_PROXY_USD} cost_usd "
        "FROM system.query.history "
        f"WHERE start_time>=dateadd(day,-{days},current_date()) AND statement_text IS NOT NULL "
        "AND compute.warehouse_id IS NOT NULL "
        "GROUP BY 1,2, substring(statement_text,1,120) "
        "HAVING COUNT(*)>=2 ORDER BY cost_usd DESC LIMIT 60",
        "system.query.history (advisor)")
    # Resolve friendly names (fail-soft to id) so the UI shows name + id, not a
    # bare id (a customer review #13). Both maps degrade to {} on ungranted tables.
    wh_names = _warehouse_names(warehouse_id)
    ws_names = _workspace_names(warehouse_id)
    try:
        bu_by_ws = _workspace_bu(warehouse_id)   # workspace -> {"bu","owner"}; {} if unmapped
    except Exception:  # noqa: BLE001
        bu_by_ws = {}
    out: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        wh_id = str(r.get("wh") or "")
        ws_id = str(r.get("ws") or "")
        _txt = str(r.get("txt") or "")
        q = {
            "id": i, "statement_id": str(r.get("sid") or ""),
            "query_text": _txt,
            # Recover the target table from the SQL so the drill-down + apply_sql
            # work (was blank live). Best-effort; None-safe.
            "target_table": recommender._table_from_sql(_txt) or "",
            "src": str(r.get("src") or "SQL"),
            "src_id": str(r.get("src_id") or ""),
            "warehouse": wh_names.get(wh_id, wh_id), "warehouse_id": wh_id,
            "workspace": ws_names.get(ws_id, ws_id), "workspace_id": ws_id,
            "bu": bu_by_ws.get(ws_id, {}).get("bu") or "",
            "user": str(r.get("usr") or ""), "runs": int(_f(r.get("runs"))),
            "p50_s": round(_f(r.get("p50")), 1), "p95_s": round(_f(r.get("p95")), 1),
            "bytes_read": int(_f(r.get("bytes_read"))),
            "pruning_efficiency": round(_f(r.get("pruning")), 3),
            "spill_gb": round(_f(r.get("spill_gb")), 1),
            "small_file_ratio": 0.0,
            "queued_ratio": round(_f(r.get("queued")), 3), "stale_ratio": 0.0,
            "cost_usd": round(_f(r.get("cost_usd")), 2),
            "applied": False,
        }
        out.append(recommender.enrich_query(q))
    return out


def classification_live(warehouse_id: str) -> dict[str, Any]:
    # Detected classification categories (auto-applied class.* column tags).
    cats = _run(warehouse_id,
        "SELECT tag_name, COUNT(DISTINCT concat_ws('.',catalog_name,schema_name,table_name,column_name)) c "
        "FROM system.information_schema.column_tags WHERE tag_name LIKE 'class.%' GROUP BY 1 ORDER BY c DESC",
        "system.information_schema.column_tags")
    categories = [{"type": str(r.get("tag_name")).replace("class.", "").replace("_", " ").strip().capitalize(),
                   "count": int(_f(r.get("c")))} for r in cats]
    # DISTINCT classified columns — NOT sum(category counts), which double-counts
    # a column carrying more than one class.* tag (e.g. both name + email).
    pii_row = _run(warehouse_id,
        "SELECT COUNT(DISTINCT concat_ws('.',catalog_name,schema_name,table_name,column_name)) c "
        "FROM system.information_schema.column_tags WHERE tag_name LIKE 'class.%'",
        "column_tags (distinct pii)")
    pii_columns = int(_f(pii_row[0].get("c"))) if pii_row else 0

    # Tables that carry any classification tag + a total-tables denominator.
    scanned = _run(warehouse_id,
        "SELECT COUNT(DISTINCT concat_ws('.',catalog_name,schema_name,table_name)) t "
        "FROM system.information_schema.column_tags WHERE tag_name LIKE 'class.%'", "column_tags (scanned)")
    tables_scanned = int(_f(scanned[0].get("t"))) if scanned else 0
    tot = _run(warehouse_id,
        "SELECT COUNT(*) t FROM system.information_schema.tables "
        "WHERE table_schema <> 'information_schema' AND table_catalog NOT IN ('system','samples')",
        "information_schema.tables")
    tables_total = int(_f(tot[0].get("t"))) if tot else 0

    # Most sensitive tables (most classified columns) + the real category set per
    # table and the real sensitivity_level tag (never a hardcoded "Confidential").
    top = _run(warehouse_id,
        "SELECT c.catalog_name, c.schema_name, c.table_name, "
        "COUNT(DISTINCT c.column_name) pii, "
        "collect_set(replace(c.tag_name,'class.','')) cats, s.sev "
        "FROM system.information_schema.column_tags c "
        "LEFT JOIN (SELECT catalog_name, schema_name, table_name, max(tag_value) sev "
        "  FROM system.information_schema.column_tags WHERE tag_name='sensitivity_level' "
        "  GROUP BY 1,2,3) s "
        "  ON c.catalog_name=s.catalog_name AND c.schema_name=s.schema_name AND c.table_name=s.table_name "
        "WHERE c.tag_name LIKE 'class.%' "
        "GROUP BY 1,2,3,s.sev ORDER BY pii DESC LIMIT 8", "column_tags (top sensitive)")
    top_sensitive = [{"fqn": f"{r.get('catalog_name')}.{r.get('schema_name')}.{r.get('table_name')}",
                      "pii_columns": int(_f(r.get("pii"))),
                      "categories": sorted(str(x).replace("_", " ") for x in _arr(r.get("cats"))),
                      "sensitivity": str(r.get("sev") or "").capitalize()} for r in top]

    # Distinct catalogs carrying any classification tag (was a hardcoded 0).
    cat_en = _run(warehouse_id,
        "SELECT COUNT(DISTINCT catalog_name) c FROM system.information_schema.column_tags "
        "WHERE tag_name LIKE 'class.%'", "column_tags (catalogs)")
    catalogs_enabled = int(_f(cat_en[0].get("c"))) if cat_en else 0

    # Columns carrying a real sensitivity_level tag (was a fake duplicate of pii).
    sens = _run(warehouse_id,
        "SELECT COUNT(DISTINCT concat_ws('.',catalog_name,schema_name,table_name,column_name)) c "
        "FROM system.information_schema.column_tags WHERE tag_name='sensitivity_level'",
        "column_tags (sensitive)")
    sensitive_columns = int(_f(sens[0].get("c"))) if sens else 0

    # Governed tag coverage from table_tags.
    gov = _run(warehouse_id,
        "SELECT tag_name, COUNT(DISTINCT concat_ws('.',catalog_name,schema_name,table_name)) c "
        f"FROM system.information_schema.table_tags WHERE tag_name IN ({','.join(repr(k) for k in _GOV_TAG_KEYS)}) GROUP BY 1",
        "system.information_schema.table_tags")
    gov_counts = {str(r.get("tag_name")): int(_f(r.get("c"))) for r in gov}
    cert = _run(warehouse_id,
        "SELECT tag_value, COUNT(*) c FROM system.information_schema.table_tags "
        "WHERE tag_name='system.certification_status' GROUP BY 1", "table_tags (certification)")
    cert_map = {str(r.get("tag_value")): int(_f(r.get("c"))) for r in cert}
    denom = max(tables_total, 1)
    governed_tags = []
    for k in _GOV_TAG_KEYS:
        if k not in gov_counts:
            continue
        entry = {"tag_key": k, "allowed_values": [], "coverage_pct": round(gov_counts[k] / denom, 3),
                 "note": "Live from system.information_schema.table_tags"}
        if k == "system.certification_status":
            entry["certified"] = cert_map.get("Certified", 0)
            entry["deprecated"] = cert_map.get("Deprecated", 0)
            entry["allowed_values"] = list(cert_map.keys())
        governed_tags.append(entry)

    scan_cost = _run(warehouse_id,
        "SELECT SUM(u.usage_quantity*lp.pricing.effective_list.default) usd FROM system.billing.usage u "
        "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
        "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
        "WHERE u.billing_origin_product='DATA_CLASSIFICATION' AND u.usage_date>=date_trunc('MONTH',current_date())",
        "billing (DATA_CLASSIFICATION)")
    scan_usd = round(_f(scan_cost[0].get("usd")) if scan_cost else 0.0, 2)

    coverage = round(tables_scanned / denom, 3) if denom else 0.0
    return {
        "governed_tags": governed_tags,
        "classification": {
            "enabled": True, "catalogs_enabled": catalogs_enabled,
            "tables_scanned": tables_scanned, "tables_total": tables_total,
            "pii_columns": pii_columns, "sensitive_columns": sensitive_columns,
            "coverage_pct": coverage, "scan_cost_usd_month": scan_usd,
            "categories": categories, "top_sensitive_tables": top_sensitive, "unclassified_high_risk": [],
        },
        "summary": {
            "num_governed_tags": len(governed_tags),
            "avg_tag_coverage": round(sum(g["coverage_pct"] for g in governed_tags) / len(governed_tags), 3) if governed_tags else 0.0,
            "pii_columns": pii_columns, "classification_coverage_pct": coverage, "scan_cost_usd_month": scan_usd,
        },
    }


# ---------------------------------------------------------------------------
# Governance report — system-table tiles computed live + the admin-API tiles
# from the finops_admin ingest table. No tile is faked: an admin tile appears
# only when its dataset has been ingested; otherwise it is listed in `pending`
# so the UI can prompt to run finops_admin_sync. Storage PO/LC-adoption tiles
# (per-table properties, not in system tables) are intentionally omitted.
# ---------------------------------------------------------------------------
def _tier(v: float, good: float, warn: float, invert: bool = False) -> str:
    if invert:
        return "Good" if v <= good else ("Warning" if v <= warn else "Critical")
    return "Good" if v >= good else ("Warning" if v >= warn else "Critical")


def governance_live(warehouse_id: str, ingest_schema: str) -> dict[str, Any]:
    # --- system-table tiles --------------------------------------------------
    # Each optional sub-query is wrapped: a single missing/ungranted system table
    # (lineage, column_tags, DQM…) must NOT blank the whole governance report.
    # A failed tile degrades to a neutral default rather than 503-ing everything.
    # Track which system-table reads FAILED so their tiles can render as
    # "Unknown" (degraded) instead of a scored green/red. A failed grant must
    # never silently show a security all-clear or a fake "100% untagged".
    degraded: dict[str, str] = {}   # read-name -> addressable detail

    def _try(fn, default, name: str | None = None):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if name:
                detail = getattr(e, "detail", None) or str(e)
                degraded[name] = detail[:160]
            return default

    def _cov():
        # NULL custom_tags must count as UNTAGGED (size(NULL)=NULL under ANSI, so
        # `size(...)=0` alone silently treats NULL maps as tagged → overstates
        # coverage). Guard the NULL explicitly.
        cov = _run(warehouse_id,
            "SELECT SUM(u.usage_quantity*lp.pricing.effective_list.default) usd, "
            "SUM(CASE WHEN u.custom_tags IS NULL OR size(u.custom_tags)=0 "
            "         THEN u.usage_quantity*lp.pricing.effective_list.default ELSE 0 END) untagged "
            "FROM system.billing.usage u "
            "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
            "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
            "WHERE u.usage_date>=date_trunc('MONTH',current_date())", "billing (tag coverage)")
        t = _f(cov[0].get("usd")) if cov else 0.0
        ut = _f(cov[0].get("untagged")) if cov else 0.0
        return t, ut
    total, untagged = _try(_cov, (0.0, 0.0), "tagging")
    tagging = 1 - (untagged / total) if total else 0.0

    ext = _try(lambda: _run(warehouse_id,
        "SELECT SUM(CASE WHEN table_type='EXTERNAL' THEN 1 ELSE 0 END) ext, COUNT(*) tot "
        "FROM system.information_schema.tables "
        # Exclude samples too (read-only public demo catalog) so the governance
        # external-share % reflects the customer's own estate, matching Tables/gold.
        "WHERE table_schema<>'information_schema' AND table_catalog NOT IN ('system','samples')",
        "information_schema.tables (external share)"), [], "external")
    n_tot = _f(ext[0].get("tot")) if ext else 0.0
    pct_ext = (_f(ext[0].get("ext")) / n_tot) if n_tot else 0.0

    # lineage coverage: tables with any captured lineage / total tables.
    # The DENOMINATOR (n_tot) comes from the `external` read above — so if THAT
    # read failed (n_tot=0) we cannot compute coverage, and must mark the lineage
    # tile degraded rather than show a misleading "0% Critical" (the tile is only
    # auto-degraded when the lineage read itself fails). Flag it under a distinct
    # key so the tile → Unknown, not a fake score.
    lin = _try(lambda: _run(warehouse_id,
        "SELECT COUNT(DISTINCT target_table_full_name) c FROM system.access.table_lineage "
        "WHERE target_table_full_name IS NOT NULL AND event_time>=dateadd(day,-90,current_date())",
        "table_lineage (coverage)"), [], "lineage")
    if not n_tot and "lineage" not in degraded:
        # denominator unavailable (external/information_schema read failed)
        degraded["lineage"] = degraded.get("external", "table count unavailable (information_schema.tables read failed)")
    lineage_pct = (_f(lin[0].get("c")) / n_tot) if (lin and n_tot) else 0.0
    lineage_pct = min(lineage_pct, 1.0)

    cls = _try(lambda: classification_live(warehouse_id),
               {"governed_tags": [], "classification": {"coverage_pct": 0.0}}, "classification")
    cert = next((g for g in cls["governed_tags"] if g["tag_key"] == "system.certification_status"), None)
    cert_cov = cert["coverage_pct"] if cert else 0.0
    class_cov = cls["classification"]["coverage_pct"]

    risks = _try(lambda: access_grants_live(warehouse_id)["risks"], None, "over_priv")
    # Distinct principals with an OVER-PRIVILEGED risk specifically — matches the
    # tile label ("hold MODIFY / ALL PRIVILEGES") and the demo tile's measure. Was
    # distinct principals across ALL risk kinds (broad-grant + direct-to-user too),
    # which over-counted vs the label and diverged from demo.
    over_priv = (len({r["principal"] for r in risks if r.get("risk") == "over-privileged"})
                 if risks is not None else 0)

    facts = _try(lambda: _workspace_facts(warehouse_id), [])
    n_ws = len(facts) or 1

    tiles: list[dict[str, Any]] = [
        {"metric": "Tagging coverage", "category": "Cost attribution", "weight": 3,
         "value_pct": round(tagging, 3), "status": _tier(tagging, 0.8, 0.5),
         "gap": f"{round((1 - tagging) * 100)}% of spend untagged across {n_ws} workspaces",
         "action": "Enforce tag policies at account + serverless budget policies", "ties_to": "workspaces"},
        {"metric": "Untagged spend", "category": "Cost attribution", "weight": 2,
         "value_usd": round(untagged), "status": _tier(tagging, 0.8, 0.5),
         "gap": f"${round(untagged):,}/mo cannot be attributed to a BU / cost centre",
         "action": "Apply mandatory cost-centre + environment tags; block untagged compute", "ties_to": "workspaces"},
        # value_pct = EXTERNAL share (lower is better), matching the demo tile, the
        # gap text and the frontend InfoTip ("share of tables that are external…
        # lower is better"). Was 1-pct_ext (managed share), which inverted the
        # meaning vs demo + contradicted this tile's own gap sentence.
        {"metric": "External / unmanaged data", "category": "Storage optimisation", "weight": 2,
         "value_pct": round(pct_ext, 3), "status": _tier(pct_ext, 0.15, 0.35, invert=True),
         "gap": f"{round(pct_ext * 100)}% of tables are EXTERNAL — miss PO, LC & catalog-managed commits",
         "action": "Convert external tables to UC managed (DEEP CLONE / CTAS)", "ties_to": "tables"},
        {"metric": "Classification coverage", "category": "Data governance", "weight": 2,
         "value_pct": round(class_cov, 3), "status": _tier(class_cov, 0.6, 0.3),
         "gap": f"{round(class_cov * 100)}% of tables carry auto-classification tags",
         "action": "Enable Data Classification on remaining catalogs/schemas", "ties_to": "tables"},
        {"metric": "Certification coverage", "category": "Data governance", "weight": 1,
         "value_pct": round(cert_cov, 3), "status": _tier(cert_cov, 0.5, 0.2),
         "gap": f"{round(cert_cov * 100)}% of tables carry a certification-status tag",
         "action": "Apply system.certification_status governed tag via a rule engine", "ties_to": "tables"},
        {"metric": "Lineage coverage", "category": "Data governance", "weight": 1,
         "value_pct": round(lineage_pct, 3), "status": _tier(lineage_pct, 0.85, 0.6),
         "gap": "External + notebook-driven writes leave lineage gaps",
         "action": "Adopt Bring-Your-Own-Lineage for hybrid/external jobs", "ties_to": "tables"},
        {"metric": "Over-privileged principals", "category": "Access & identity", "weight": 3,
         "value_count": over_priv, "status": _tier(over_priv, 0, 3, invert=True),
         "gap": f"{over_priv} principals hold MODIFY / ALL PRIVILEGES grants (broad write access)",
         "action": "Downgrade to SELECT; scope writes via dedicated groups", "ties_to": "access"},
    ]

    # Mark any tile whose underlying read FAILED as Unknown/degraded — so a
    # missing grant surfaces as "couldn't read" rather than a fake green
    # all-clear (esp. the over-privileged-principals security tile) or a fake
    # "100% untagged". The gap names the exact grant to fix.
    _tile_read = {
        "Tagging coverage": "tagging", "Untagged spend": "tagging",
        "External / unmanaged data": "external", "Lineage coverage": "lineage",
        "Classification coverage": "classification", "Certification coverage": "classification",
        "Over-privileged principals": "over_priv",
    }
    for t in tiles:
        rd = _tile_read.get(t["metric"])
        if rd and rd in degraded:
            t["status"] = "Unknown"
            t["degraded"] = True
            t["gap"] = f"Could not read this metric — {degraded[rd]}"

    # --- admin-API tiles from the finops_admin ingest (only if ingested) -----
    pending: list[str] = []
    refreshed: list[str] = []

    def _admin(key: str) -> dict[str, Any] | None:
        got = read_admin(warehouse_id, ingest_schema, key)
        if got is None:
            pending.append(key)
            return None
        if got.get("ingested_at"):
            refreshed.append(got["ingested_at"])
        return got["payload"]

    bp = _admin("budget_policies")
    if bp is not None:
        cov_pct = round(bp.get("count", 0) / n_ws, 3) if n_ws else 0.0
        tiles.append({"metric": "Budget-policy coverage", "category": "Budgets & alerts", "weight": 2,
                      "value_pct": min(cov_pct, 1.0), "status": _tier(min(cov_pct, 1.0), 0.75, 0.4),
                      "gap": f"{bp.get('count', 0)} serverless budget policies across {n_ws} workspaces",
                      "action": "Roll out serverless budget policies"})
    bg = _admin("budgets")
    if bg is not None:
        tiles.append({"metric": "Account-level budgets", "category": "Budgets & alerts", "weight": 2,
                      "value_count": bg.get("count", 0),
                      "status": "Good" if bg.get("count", 0) >= 3 else "Warning" if bg.get("count", 0) >= 1 else "Critical",
                      "gap": f"{bg.get('count', 0)} account budgets configured ({bg.get('with_alerts', 0)} with alerts)",
                      "action": "Configure Account Console budgets + email alert thresholds"})
    al = _admin("alerts")
    if al is not None:
        tiles.append({"metric": "Alert / anomaly setup", "category": "Budgets & alerts", "weight": 2,
                      "value_count": al.get("count", 0),
                      "status": "Good" if al.get("count", 0) >= 3 else "Warning" if al.get("count", 0) >= 1 else "Critical",
                      "gap": f"{al.get('count', 0)} SQL alerts wired for spend/anomaly monitoring",
                      "action": "Wire SQL alerts on billing.usage + subscribe FinOps + BU owners", "ties_to": "overview"})
    cp = _admin("cluster_policy_coverage")
    if cp is not None:
        cpc = cp.get("coverage_pct", 0.0)
        tiles.append({"metric": "Cluster-policy coverage", "category": "Compute controls", "weight": 2,
                      "value_pct": round(cpc, 3), "status": _tier(cpc, 0.8, 0.5),
                      "gap": f"{cp.get('bound_clusters', 0)} of {cp.get('total_clusters', 0)} clusters bound to a cost-control policy",
                      "action": "Attach mandatory cluster policies (max DBU, auto-terminate, node caps)", "ties_to": "workspaces"})
    tk = _admin("tokens")
    if tk is not None:
        stale = tk.get("stale", 0)
        tiles.append({"metric": "PAT / token risk", "category": "Access & identity", "weight": 2,
                      "value_count": stale, "status": _tier(stale, 0, 2, invert=True),
                      "gap": f"{stale} personal access tokens older than 90 days",
                      "action": "Rotate / revoke; adopt scoped short-lived tokens (OAuth)", "ties_to": "access"})

    # Per-workspace tag coverage — the same billing-usage tag-coverage measure as
    # the "Tagging coverage" tile, but grouped by workspace_id (a first-class
    # dimension on system.billing.usage). Degrades to [] if the billing read is
    # ungranted, matching the tile's fail-soft behaviour.
    def _tag_by_ws():
        rows = _run(warehouse_id,
            "SELECT CAST(u.workspace_id AS STRING) ws, "
            "SUM(u.usage_quantity*lp.pricing.effective_list.default) usd, "
            "SUM(CASE WHEN u.custom_tags IS NULL OR size(u.custom_tags)=0 "
            "         THEN u.usage_quantity*lp.pricing.effective_list.default ELSE 0 END) untagged "
            "FROM system.billing.usage u "
            "LEFT JOIN system.billing.list_prices lp ON u.cloud=lp.cloud AND u.sku_name=lp.sku_name "
            "AND u.usage_start_time>=lp.price_start_time AND (lp.price_end_time IS NULL OR u.usage_start_time<lp.price_end_time) "
            "WHERE u.usage_date>=date_trunc('MONTH',current_date()) AND u.workspace_id IS NOT NULL "
            "GROUP BY 1 HAVING SUM(u.usage_quantity*lp.pricing.effective_list.default)>0 "
            "ORDER BY untagged DESC LIMIT 200", "billing (tag coverage by workspace)")
        ws_names = _workspace_names(warehouse_id)
        bu_by_ws = _workspace_bu(warehouse_id)
        out = []
        for r in rows:
            ws = str(r.get("ws") or "")
            usd = _f(r.get("usd")); ut = _f(r.get("untagged"))
            pct = 1 - (ut / usd) if usd else 0.0
            out.append({
                "workspace": ws_names.get(ws, ws),
                "bu": bu_by_ws.get(ws, {}).get("bu", ""),
                "spend_usd_month": round(usd),
                "tagging_pct": round(pct, 3),
                "untagged_usd_month": round(ut),
                "status": _tier(pct, 0.8, 0.5),
            })
        return out
    tagging_by_ws = _try(_tag_by_ws, [], "tagging_by_ws")

    _STATUS_SCORE = {"Good": 1.0, "Warning": 0.5, "Critical": 0.0}
    # "Unknown" (degraded) tiles are EXCLUDED from the score — scoring them 0
    # would fake a bad grade off a failed read; leaving them out keeps the grade
    # honest to what was actually measurable. .get() also avoids a KeyError.
    for tile in tiles:
        tile["score_points"] = _STATUS_SCORE.get(tile["status"], 0.0)
    scored = [t for t in tiles if t["status"] in _STATUS_SCORE]
    total_w = sum(t["weight"] for t in scored) or 1
    score = round((sum(t["weight"] * t["score_points"] for t in scored) / total_w) * 100)
    grade = "A" if score >= 80 else "B" if score >= 70 else "C" if score >= 55 else "D" if score >= 40 else "E"
    return {
        "score": score, "grade": grade,
        "counts": {s: sum(1 for t in tiles if t["status"] == s) for s in ("Good", "Warning", "Critical", "Unknown")},
        "num_tiles": len(tiles), "tiles": tiles, "tagging_by_workspace": tagging_by_ws,
        "num_degraded": len(degraded),
        "pending_admin": sorted(set(pending)),
        "last_refreshed": max(refreshed) if refreshed else "",
    }


# ---------------------------------------------------------------------------
# Access — group tree + PAT inventory from the finops_admin ingest. Fails
# closed (AdminNotIngested → 503 with 'run finops_admin_sync') when absent.
# ---------------------------------------------------------------------------
def groups_ingested(warehouse_id: str, ingest_schema: str) -> dict[str, Any]:
    got = _require_admin(warehouse_id, ingest_schema, "groups")
    payload = got["payload"]

    def _member_type(m: dict[str, Any]) -> str:
        # SCIM $ref tells us the member kind: Groups/… , Users/… , ServicePrincipals/…
        ref = str(m.get("ref") or "").lower()
        if "group" in ref:
            return "group"
        if "serviceprincipal" in ref:
            return "service-principal"
        return "user"

    nodes = [{
        "group": g["group"], "id": g.get("id", ""), "source": "SCIM",
        "member_count": g.get("member_count", 0),
        # Frontend GroupTree expects direct_members = [{principal, type}] (it
        # filters on m.type === 'group'). Emitting a flat string list crashed
        # GroupTree.filter — build the typed objects the UI contract requires.
        "direct_members": [{"principal": m.get("display", "") or m.get("value", ""),
                            "type": _member_type(m)} for m in (g.get("members") or [])],
        "entitlements": g.get("entitlements", []),
        "workspaces": [],
    } for g in payload.get("groups", [])]
    return {"groups": nodes, "ingested_at": got["ingested_at"]}


def pats_ingested(warehouse_id: str, ingest_schema: str) -> dict[str, Any]:
    got = _require_admin(warehouse_id, ingest_schema, "tokens")
    payload = got["payload"]
    # last_used_days is None (not 0): the token-management ingest doesn't capture
    # last-use, so emitting 0 ("used today") fabricates a freshness signal on a
    # security panel. None → the UI hides the "last used" line. Staleness is
    # age-based (age_days > 90), computed in admin_sync, and is unaffected.
    pats = [{"id": t.get("token_id", ""), "comment": t.get("comment", ""),
             "principal": t.get("created_by", ""), "age_days": t.get("age_days", 0),
             "last_used_days": None, "stale": t.get("stale", False)} for t in payload.get("tokens", [])]
    return {"pats": pats, "ingested_at": got["ingested_at"]}


# ---------------------------------------------------------------------------
# Access drill-downs — tree + principal-centric + securable-centric views built
# from the live grant graph (information_schema.table_privileges) and, where
# available, the ingested SCIM groups / workspace assignments / tokens. Object
# grants are always real; membership / entitlements / PATs fill in only when
# finops_admin_sync has run (best-effort, not faked).
# ---------------------------------------------------------------------------
def access_tree_live(warehouse_id: str) -> dict[str, Any]:
    """Catalog → schema → table hierarchy (for the data-centric filter) from
    information_schema, plus the distinct workspace ids from billing."""
    rows = _run(warehouse_id,
        "SELECT table_catalog c, table_schema s, table_name t "
        "FROM system.information_schema.tables WHERE table_schema<>'information_schema' "
        "AND table_catalog NOT IN ('system','samples') "
        "ORDER BY 1,2,3 LIMIT 5000", "information_schema.tables (tree)")
    catalogs: dict[str, dict[str, list[str]]] = {}
    for r in rows:
        c = str(r.get("c") or ""); s = str(r.get("s") or ""); t = str(r.get("t") or "")
        if not c or not s:
            continue
        catalogs.setdefault(c, {}).setdefault(s, []).append(f"{c}.{s}.{t}")
    ws = _run(warehouse_id,
        "SELECT DISTINCT CAST(workspace_id AS STRING) w FROM system.billing.usage "
        "WHERE usage_date>=date_trunc('MONTH',current_date())", "billing (workspaces for tree)")
    return {"workspaces": sorted(str(r.get("w")) for r in ws if r.get("w")), "catalogs": catalogs}


def principal_access_live(warehouse_id: str, principal: str, ingest_schema: str) -> dict[str, Any] | None:
    """Everything a principal can access: real object grants from the grant
    graph, enriched with ingested group membership / workspace assignments /
    PATs when available."""
    graph = access_grants_live(warehouse_id)
    pgrants = [g for g in graph["grants"] if g["principal"] == principal]
    known = {g["principal"] for g in graph["grants"]}
    if principal not in known:
        return None
    grants = [{
        "securable": g["securable"], "securable_type": str(g["securable_type"]).lower(),
        "catalog": g["catalog"], "privilege": g["privilege"],
        "granted_via": str(g["granted_via"]).lower(), "source_group": None,
        "inherited": False,
    } for g in pgrants]

    member_of: list[str] = []
    entitlements: list[dict[str, Any]] = []
    workspaces: list[dict[str, Any]] = []
    pats: list[dict[str, Any]] = []
    # best-effort enrichment from the admin ingest (empty if not yet ingested)
    grp = read_admin(warehouse_id, ingest_schema, "groups")
    if grp:
        for g in grp["payload"].get("groups", []):
            names = {m.get("display", "") for m in (g.get("members") or [])}
            if principal in names:
                member_of.append(g.get("group", ""))
                for e in g.get("entitlements", []) or []:
                    entitlements.append({"entitlement": e, "via": "group", "source_group": g.get("group", "")})
    wsa = read_admin(warehouse_id, ingest_schema, "workspace_assignments")
    if wsa:
        for a in wsa["payload"].get("assignments", []):
            if a.get("principal") == principal:
                perms = a.get("permissions", []) or []
                workspaces.append({"workspace": a.get("workspace_id", ""),
                                   "permission": (perms[0] if perms else "USER"), "source_group": None})
    tks = read_admin(warehouse_id, ingest_schema, "tokens")
    if tks:
        for t in tks["payload"].get("tokens", []):
            if t.get("created_by") == principal:
                pats.append({"id": t.get("token_id", ""), "comment": t.get("comment", ""),
                             "age_days": t.get("age_days", 0), "last_used_days": None, "stale": t.get("stale", False)})

    return {
        "principal": principal, "type": _principal_type(principal), "source": "UC", "status": "active",
        "member_of": member_of, "entitlements": entitlements, "workspaces": workspaces,
        "grants": grants, "num_grants": len(grants), "pats": pats,
    }


def securable_access_live(warehouse_id: str, fqn: str) -> dict[str, Any]:
    """Every grant on a securable, from the live grant graph. Policies (masks /
    row filters) aren't exposed in system tables, so that list is left empty."""
    graph = access_grants_live(warehouse_id)
    sg = [g for g in graph["grants"] if g["securable"] == fqn]
    parts = fqn.split(".")
    grants = [{
        "id": i, "principal": g["principal"], "principal_type": str(g["principal_type"]).lower(),
        "privilege": g["privilege"], "granted_via": str(g["granted_via"]).lower(),
        "inherited": False, "inherited_from": None,
    } for i, g in enumerate(sg)]
    return {"securable": fqn, "securable_type": "table" if len(parts) >= 3 else "catalog",
            "catalog": parts[0] if parts else "", "grants": grants, "policies": [], "num_grants": len(grants)}


# ---------------------------------------------------------------------------
# EXPLAIN-validated rewrites (Increment 3)
#
# Live plumbing for data/rewrite.py: (a) fetch the FULL statement text (the
# advisor query truncates to 120 chars for display); (b) EXPLAIN a candidate
# against the real warehouse to prove it binds to the schema; (c) call the
# in-region model endpoint to generate a candidate. All three are best-effort
# and fail-soft — the rewrite feature degrades to "unvalidated"/"unavailable"
# rather than 503-ing the whole Query Advisor tab.
# ---------------------------------------------------------------------------

def _run_scalar_text(warehouse_id: str, sql: str, source: str) -> str:
    """Run a statement and concatenate the first column of every row into one
    string — used for EXPLAIN, whose plan comes back as text rows. Raises
    LiveError on failure (the caller catches it)."""
    rows = _run(warehouse_id, sql, source)
    if not rows:
        return ""
    parts: list[str] = []
    for r in rows:
        first = next(iter(r.values()), "")
        parts.append("" if first is None else str(first))
    return "\n".join(parts)


def full_statement_text(warehouse_id: str, statement_id: str) -> str | None:
    """Return the full statement_text for a statement_id from query.history,
    or None if unavailable. The advisor list truncates text to 120 chars; the
    rewrite path needs the whole statement to EXPLAIN it.

    ``statement_id`` is request-controlled (POST body), so we VALIDATE it against
    the known statement-id shape before interpolating — quote-doubling alone is
    not injection-safe on Spark (backslash is an escape char by default, so a
    crafted id could break out of the literal). A statement_id is a UUID-like
    token; anything else is rejected outright rather than escaped."""
    if not statement_id or not _STATEMENT_ID_RE.match(statement_id):
        return None
    sid = statement_id  # already validated to a safe [0-9a-fA-F-] shape
    try:
        rows = _run(
            warehouse_id,
            "SELECT any_value(statement_text) txt FROM system.query.history "
            f"WHERE statement_id = '{sid}' GROUP BY statement_id LIMIT 1",
            "system.query.history (full text)",
        )
    except LiveError:
        return None
    if not rows:
        return None
    txt = rows[0].get("txt")
    return str(txt) if txt else None


def explain_sql(warehouse_id: str, candidate_sql: str) -> tuple[bool, str, str | None]:
    """EXPLAIN a candidate SELECT against the real warehouse.

    Returns ``(ok, detail, plan_text)``. ``ok`` is True when EXPLAIN succeeds and
    the plan does NOT contain an analysis/parse error line — Spark's EXPLAIN
    returns unresolved-column errors as plan TEXT (not an exception), so we
    inspect the plan body too. The candidate is trusted to be read-only (the
    caller guards with rewrite.is_read_only_select); we still only ever prefix
    EXPLAIN, never execute it.
    """
    from data import rewrite  # local import avoids a cycle at module load

    if not rewrite.is_read_only_select(candidate_sql):
        return (False, "candidate is not a single read-only SELECT", None)
    stmt = "EXPLAIN " + candidate_sql.strip().rstrip(";")
    try:
        plan = _run_scalar_text(warehouse_id, stmt, "EXPLAIN (rewrite validation)")
    except LiveError as e:
        # A thrown error (syntax, permission) is a clean rejection signal.
        return (False, e.detail, None)
    # Fail CLOSED on an empty plan: EXPLAIN always returns a plan body on success,
    # so no rows means we can't confirm binding — never report that as validated.
    if not plan or not plan.strip():
        return (False, "EXPLAIN returned no plan — could not confirm the rewrite binds.", None)
    # Spark surfaces binding failures inside the plan text rather than raising.
    # Match both the human phrasings and the bracketed error-class codes
    # (e.g. [TABLE_OR_VIEW_NOT_FOUND], [UNRESOLVED_COLUMN]).
    low = plan.lower()
    # Markers are error-class codes / phrases that only appear in a FAILED plan.
    # NB: use the SPECIFIC bracketed error classes, not a bare "unresolved" or
    # "[unresolved_" — a valid plan renders projected columns as `Project [col]`,
    # so `[unresolved_alerts]` (a legit column) would false-match a loose prefix.
    for marker in ("analysisexception", "parseexception", "cannot be resolved",
                   "cannot resolve", "table or view not found",
                   "[table_or_view_not_found]", "[unresolved_column]",
                   "[unresolved_column.", "[unresolved_routine",
                   "[schema_not_found]", "[datatype_mismatch"):
        if marker in low:
            first = next((ln for ln in plan.splitlines() if marker in ln.lower()), plan[:200])
            return (False, first.strip(), plan)
    return (True, "EXPLAIN succeeded: the rewrite parses and binds to your schema.", plan)


def _rewrite_generator(warehouse_id: str):
    """Return a ``prompt -> text`` callable backed by the in-region model
    endpoint, or None when AI is off / no endpoint. Reuses the SAME endpoint
    config as the recommender narration (FINOPS_LLM_ENDPOINT), so it stays in the
    customer's region.

    Gated by the documented, UI-exposed ``ai_narration`` feature flag (env
    FINOPS_FEATURE_AI_NARRATION) — the switch customers actually set — OR the
    legacy FINOPS_LLM_NARRATION opt-in. Either enables the AI rewrite; with both
    off the rewrite panel shows the deterministic guidance (status=unavailable).
    """
    from data import config

    ai_on = bool(config.get_features().get("ai_narration")) or recommender.narration_enabled()
    if not ai_on:
        return None
    endpoint = recommender.get_llm_endpoint()
    if not endpoint:
        return None

    def _gen(prompt: str) -> str:
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        w = _client()
        # Size the output budget to the INPUT: a rewrite is roughly as long as the
        # original query, so a fixed 700-token cap truncated any non-trivial query
        # mid-statement — the chopped SQL then failed EXPLAIN with a bogus
        # PARSE_SYNTAX_ERROR ("at end of input") and every rewrite showed
        # "Rejected by EXPLAIN". Budget ~1 token per 3 prompt chars, clamped to a
        # sane range (queries_live already caps statement_text at 16000 chars).
        max_out = max(1200, min(8000, len(prompt) // 3 + 600))
        # The SDK calls .as_dict() on each message, so pass ChatMessage objects
        # — a plain {"role","content"} dict raises "'dict' object has no
        # attribute 'as_dict'".
        resp = w.serving_endpoints.query(
            name=endpoint,
            messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)],
            max_tokens=max_out,
        )
        choice = resp.choices[0]
        # If the model still hit the token ceiling, the SQL is truncated and
        # invalid — return "" so propose_rewrite reports "unavailable" rather than
        # feeding chopped SQL to EXPLAIN (which would show a misleading rejection).
        if str(getattr(choice, "finish_reason", "") or "").lower() == "length":
            return ""
        return (choice.message.content or "").strip()

    return _gen


def rewrite_live(warehouse_id: str, q: dict[str, Any]) -> dict[str, Any]:
    """Full live rewrite pipeline for one query row: recover the full statement
    text, ask the in-region model for a rewrite, and validate it with EXPLAIN.
    Fail-soft — any failure downgrades the status rather than raising."""
    from data import rewrite

    row = dict(q)
    full = full_statement_text(warehouse_id, str(q.get("statement_id") or ""))
    if full:
        row["query_text"] = full
    generate = _rewrite_generator(warehouse_id)
    explain = (lambda sql: explain_sql(warehouse_id, sql)) if warehouse_id else None
    return rewrite.propose_rewrite(row, generate=generate, explain=explain)


def exec_summary_live(warehouse_id: str, overview: dict[str, Any]) -> dict[str, Any]:
    """On-demand 'what changed this month & why' narrative for the landing view.

    OPTIONAL AI enhancement — gated on ai_narration. Feeds the ALREADY-COMPUTED
    overview figures (spend MoM, top driver, savings, needs-attention) to the
    in-region model and returns a short plain-English narrative. It interprets
    the numbers the summary already shows; it never invents or fetches data. On
    any failure / AI-off it returns status='unavailable' with an empty narrative
    so the caller degrades gracefully (the pure-SQL summary stands alone)."""
    from data import exec_summary
    generate = _rewrite_generator(warehouse_id)  # None when ai_narration off / no endpoint
    return exec_summary.generate_narrative(overview, generate=generate)


