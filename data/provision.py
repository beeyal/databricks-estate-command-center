"""Deploy-time provisioning for the Estate Command Center.

Most customers won't already have a Genie Space or a wired LLM endpoint, so the
deployment provisions them so the AI features work out of the box:

  • ensure_genie_space — creates a purpose-built "Estate Command Center" Genie
    Space over the billing/finops tables if one doesn't exist, and returns its id.
    The serialized_space schema was validated empirically against the Genie
    create API: {"version": 2, "data_sources": {"tables": [{"identifier": ...}]}}.
  • ensure_llm_endpoint — verifies the configured Claude endpoint (a
    pay-per-token Foundation Model API endpoint, e.g. databricks-claude-sonnet-4-6)
    is present + ready so recommendation narration can call it.

Both are idempotent and non-fatal: they return a status dict and never raise, so
a provisioning gap degrades the AI feature gracefully rather than breaking deploy.
Run from the finops_admin_sync job (which already holds admin creds); the app
reads the resulting genie_space_id back from <schema>.finops_admin.
"""
from __future__ import annotations

import json
from typing import Any

GENIE_TITLE = "Estate Command Center"
GENIE_DESCRIPTION = (
    "Ask cost, usage & operations questions over your Databricks estate — spend by "
    "workspace / SKU / product / user / day, SQL query volume & latency, and job runs "
    "& outcomes. Answers come from GOVERNED metric views (never raw system tables), so "
    "every number is consistent + auditable. Auto-provisioned by the Command Center app."
)

# ---------------------------------------------------------------------------------------
# GOVERNED METRIC VIEWS — Genie grounds ONLY on these, never on raw system.* tables.
# Raw system tables leak two whole classes of error into NL Q&A: (1) the usage→list_prices
# dollar join over a price-validity window — Genie reconstructs it imperfectly → plausible
# but WRONG $ that never errors; (2) the struct fields (compute.warehouse_id,
# identity_metadata.run_as, usage_metadata.*) it guesses wrong. A metric view encapsulates
# BOTH — the join and the struct-flattening — behind MEASURE()/dimensions, so every answer
# is deterministic + auditable. We build one per domain the raw tables used to serve:
#   • mv_estate_cost  — spend/DBUs (billing.usage × list_prices, + workspace_name, serverless)
#   • mv_estate_query — SQL volume/latency/IO (query.history)
#   • mv_estate_job   — job runs & outcomes (lakeflow.job_run_timeline)
# Each metric view sits on a plain base VIEW that does the joins/struct-flattening; both are
# created in the app's gold schema. workspace_name is folded in (LEFT JOIN workspaces_latest)
# so Genie answers with NAMES, not ids — no second table needed.
# ---------------------------------------------------------------------------------------
COST_MV_NAME = "mv_estate_cost"
QUERY_MV_NAME = "mv_estate_query"
JOB_MV_NAME = "mv_estate_job"


def _base_view_sqls(gold_schema: str) -> dict[str, str]:
    """The plain base views the metric views sit on — one per domain. Each LEFT JOINs
    workspaces_latest for the workspace NAME and flattens the structs Genie trips on."""
    g = gold_schema
    return {
        # cost: usage × effective list price (LEFT JOIN so a missing price never drops usage),
        # + workspace_name + is_serverless + flattened identity/usage-metadata structs.
        "estate_cost_base": f"""CREATE OR REPLACE VIEW {g}.estate_cost_base AS
SELECT
  u.workspace_id, ws.workspace_name,
  u.sku_name, u.billing_origin_product,
  CASE WHEN u.sku_name ILIKE '%SERVERLESS%' THEN true ELSE false END AS is_serverless,
  u.usage_unit, u.usage_date, u.usage_quantity,
  u.usage_quantity * COALESCE(lp.pricing.default, 0)  AS list_cost_usd,
  u.identity_metadata.run_as   AS run_as,
  u.usage_metadata.cluster_id  AS cluster_id,
  u.usage_metadata.warehouse_id AS warehouse_id_meta,
  u.usage_metadata.job_id      AS job_id
FROM system.billing.usage u
LEFT JOIN system.billing.list_prices lp
  ON u.sku_name = lp.sku_name
 AND u.usage_end_time >= lp.price_start_time
 AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
LEFT JOIN system.access.workspaces_latest ws ON u.workspace_id = ws.workspace_id""",
        # query: history + workspace_name; warehouse_id flattened out of the `compute` struct.
        "estate_query_base": f"""CREATE OR REPLACE VIEW {g}.estate_query_base AS
SELECT
  q.workspace_id, ws.workspace_name,
  q.statement_type, q.executed_by, q.execution_status,
  q.compute.warehouse_id AS warehouse_id,
  to_date(q.start_time)  AS query_date,
  q.total_duration_ms, q.read_rows, q.produced_rows
FROM system.query.history q
LEFT JOIN system.access.workspaces_latest ws ON q.workspace_id = ws.workspace_id""",
        # job: run timeline + workspace_name.
        "estate_job_base": f"""CREATE OR REPLACE VIEW {g}.estate_job_base AS
SELECT
  j.workspace_id, ws.workspace_name,
  j.job_id, j.result_state, j.trigger_type, j.run_type,
  to_date(j.period_start_time) AS run_date,
  j.execution_duration_seconds, j.run_duration_seconds
FROM system.lakeflow.job_run_timeline j
LEFT JOIN system.access.workspaces_latest ws ON j.workspace_id = ws.workspace_id""",
    }


def _metric_view_sqls(gold_schema: str) -> dict[str, str]:
    """The three governed metric views (version 1.1 YAML, DBR 17.2+). Synonyms carry the
    business vocabulary so the prose instruction stays short. Keys are the FQ view names."""
    g = gold_schema
    cost = f"""  version: 1.1
  comment: >-
    Estate COST & usage — the governed dollar layer (billing.usage × list_prices, list cost).
    Slice spend_usd / dbus by workspace (id or name), SKU, product, day, serverless-or-not, or
    the principal that ran it. Wrap measures in MEASURE(...) and GROUP BY ALL. Synonyms: spend =
    cost = dollars = list_cost_usd; DBU = usage; product = billing_origin_product.
  source: {g}.estate_cost_base
  dimensions:
    - name: workspace_id
      expr: workspace_id
      comment: "Databricks workspace id (numeric string)."
    - name: workspace_name
      expr: workspace_name
      comment: "Human-readable workspace name — PREFER this over the id in answers. Synonyms: workspace, environment."
    - name: sku_name
      expr: sku_name
      comment: "Billing SKU, e.g. ENTERPRISE_SQL_COMPUTE. Synonyms: sku, rate, line item."
    - name: product
      expr: billing_origin_product
      comment: >-
        Databricks product the spend came from (SQL, JOBS, MODEL_SERVING, APPS, LAKEBASE,
        VECTOR_SEARCH, …). Synonyms: product, workload, service, feature area.
    - name: is_serverless
      expr: is_serverless
      comment: "True if a serverless SKU. Synonyms: serverless, classic (=false)."
    - name: usage_date
      expr: usage_date
      comment: "Calendar date of usage. Filter usage_date >= dateadd(day, -N, current_date())."
    - name: run_as
      expr: run_as
      comment: "Principal (user/SP) the usage ran as, where known. Synonyms: user, owner, who, principal."
  measures:
    - name: spend_usd
      expr: SUM(list_cost_usd)
      comment: "Total LIST cost in USD (usage_quantity × list price). Synonyms: spend, cost, dollars, $, bill."
    - name: dbus
      expr: SUM(usage_quantity)
      comment: "Total DBUs consumed. Synonyms: DBUs, usage, consumption, compute units."
"""
    query = f"""  version: 1.1
  comment: >-
    SQL QUERY activity — volume, latency and IO from query history. Slice by workspace, SQL
    statement type, warehouse, the user who ran it, execution status, or day. Wrap measures in
    MEASURE(...) and GROUP BY ALL. Synonyms: queries = statements; latency = duration.
  source: {g}.estate_query_base
  dimensions:
    - name: workspace_id
      expr: workspace_id
    - name: workspace_name
      expr: workspace_name
      comment: "Human-readable workspace name — prefer over the id. Synonyms: workspace."
    - name: statement_type
      expr: statement_type
      comment: "SQL statement kind (SELECT, INSERT, …). Synonyms: query type, statement kind."
    - name: warehouse_id
      expr: warehouse_id
      comment: "SQL warehouse the query ran on. Synonyms: warehouse, SQL endpoint."
    - name: executed_by
      expr: executed_by
      comment: "Principal that ran the query. Synonyms: user, who, run by."
    - name: execution_status
      expr: execution_status
      comment: "FINISHED / FAILED / CANCELED. Synonyms: status, outcome."
    - name: query_date
      expr: query_date
      comment: "Date the query started. Filter query_date >= dateadd(day, -N, current_date())."
  measures:
    - name: query_count
      expr: COUNT(1)
      comment: "Number of queries. Synonyms: queries, statements, how many queries, volume."
    - name: avg_duration_ms
      expr: AVG(total_duration_ms)
      comment: "Average query duration in milliseconds. Synonyms: latency, avg runtime, how slow."
    - name: total_duration_ms
      expr: SUM(total_duration_ms)
      comment: "Total query duration in milliseconds. Synonyms: total runtime, compute time."
    - name: rows_read
      expr: SUM(read_rows)
      comment: "Total rows read. Synonyms: rows scanned, read volume."
    - name: rows_produced
      expr: SUM(produced_rows)
      comment: "Total rows produced/returned. Synonyms: rows returned, output rows."
"""
    job = f"""  version: 1.1
  comment: >-
    JOB RUNS — Lakeflow/Workflows job run timeline: counts, outcomes and durations. Slice by
    workspace, job_id, result state, trigger type, run type, or day. Wrap measures in
    MEASURE(...) and GROUP BY ALL. Synonyms: runs = job runs; failures = result_state ERROR/FAILED.
  source: {g}.estate_job_base
  dimensions:
    - name: workspace_id
      expr: workspace_id
    - name: workspace_name
      expr: workspace_name
      comment: "Human-readable workspace name — prefer over the id. Synonyms: workspace."
    - name: job_id
      expr: job_id
      comment: "The job's id. Synonyms: job, workflow."
    - name: result_state
      expr: result_state
      comment: "Run outcome: SUCCEEDED / FAILED / ERROR / CANCELED / …. Synonyms: outcome, status, did it fail."
    - name: trigger_type
      expr: trigger_type
      comment: "What triggered the run (SCHEDULED, MANUAL, …). Synonyms: trigger, how started."
    - name: run_type
      expr: run_type
      comment: "JOB_RUN / SUBMIT_RUN / WORKFLOW_RUN. Synonyms: run kind."
    - name: run_date
      expr: run_date
      comment: "Date the run started. Filter run_date >= dateadd(day, -N, current_date())."
  measures:
    - name: run_count
      expr: COUNT(1)
      comment: "Number of job runs. Synonyms: runs, executions, how many runs."
    - name: total_run_seconds
      expr: SUM(run_duration_seconds)
      comment: "Total run duration in seconds. Synonyms: total runtime, compute seconds."
    - name: avg_run_seconds
      expr: AVG(run_duration_seconds)
      comment: "Average run duration in seconds. Synonyms: avg runtime, how long."
"""
    return {
        f"{g}.{COST_MV_NAME}":  f"CREATE OR REPLACE VIEW {g}.{COST_MV_NAME} WITH METRICS LANGUAGE YAML AS $$\n{cost}$$",
        f"{g}.{QUERY_MV_NAME}": f"CREATE OR REPLACE VIEW {g}.{QUERY_MV_NAME} WITH METRICS LANGUAGE YAML AS $$\n{query}$$",
        f"{g}.{JOB_MV_NAME}":   f"CREATE OR REPLACE VIEW {g}.{JOB_MV_NAME} WITH METRICS LANGUAGE YAML AS $$\n{job}$$",
    }


def build_metric_views(warehouse_id: str, gold_schema: str) -> dict[str, Any]:
    """Create the base views + the three governed metric views (cost / query / job) in the
    gold schema. Best-effort and non-fatal (mirrors run_gold): a metastore without DBR 17.2 /
    metric-view support means the Genie Space simply has nothing to ground on and Ask degrades
    to 'not configured' — we NEVER fall back to raw system tables (governed-only, by design).
    Returns {ok, metric_views:[...], error?}."""
    from . import live  # local import: provision is imported in contexts without a warehouse
    if not warehouse_id or not gold_schema or not live._SCHEMA_RE.match(gold_schema):
        return {"ok": False, "metric_views": [], "error": "no warehouse_id / valid gold_schema"}
    try:
        live._run(warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {gold_schema}", "genie: ensure gold schema")
        for name, sql in _base_view_sqls(gold_schema).items():
            live._run(warehouse_id, sql, f"genie: base view {name}")
        mvs = _metric_view_sqls(gold_schema)
        for fq, sql in mvs.items():
            live._run(warehouse_id, sql, f"genie: metric view {fq}")
        return {"ok": True, "metric_views": sorted(mvs.keys())}
    except Exception as e:  # noqa: BLE001 — non-fatal; Ask degrades to 'not configured'
        return {"ok": False, "metric_views": [], "error": str(e)[:200]}


# Short, high-signal instruction block (playbook: text instructions are the LAST layer —
# the metric views carry the vocabulary via synonyms, so this only reinforces the routing:
# WHICH metric view per question shape + the MEASURE()/GROUP BY ALL call pattern + time
# filtering). ONE combined block (API allows at most one). governed-ONLY: there are no raw
# tables in the space, so there is nothing to say about structs — that complexity is gone.
def _text_instructions(cost_mv: str, query_mv: str, job_mv: str) -> list[str]:
    return [
        f"Every question is answered from GOVERNED METRIC VIEWS — always SELECT <dimensions>, "
        f"MEASURE(<measure>) ... GROUP BY ALL (SELECT * is unsupported on a metric view). "
        f"Route by question shape: COST / SPEND / DBUs → {cost_mv} (MEASURE spend_usd, dbus); "
        f"SQL QUERY volume / latency / rows → {query_mv} (MEASURE query_count, avg_duration_ms, "
        f"total_duration_ms, rows_read, rows_produced); JOB runs / failures / durations → "
        f"{job_mv} (MEASURE run_count, total_run_seconds, avg_run_seconds).",
        "Filter time on each view's date dimension: usage_date ({cost}), query_date ({query}), "
        "run_date ({job}) — e.g. usage_date >= dateadd(day, -N, current_date()). Prefer the "
        "workspace_name dimension over workspace_id in answers (both exist on every view).".format(
            cost=cost_mv.split('.')[-1], query=query_mv.split('.')[-1], job=job_mv.split('.')[-1]),
        "Vocabulary: spend = cost = dollars = spend_usd (USD, LIST price); DBU = usage = dbus; "
        "product = SQL/JOBS/MODEL_SERVING/APPS/…; is_serverless splits serverless vs classic; "
        "job failures = result_state in ('FAILED','ERROR'); queries = query_count.",
    ]


def _sample_questions() -> list[str]:
    return [
        # cost
        "What did we spend in the last 30 days, by product?",
        "Which 10 workspaces had the highest spend last month?",
        "Show daily spend for the last 30 days.",
        "Who are the top 10 users by spend in the last 30 days?",
        "What's our serverless vs classic spend split?",
        # query
        "How many queries ran in the last 7 days, by statement type?",
        "Which warehouses ran the most queries last week?",
        "Which users had the slowest average query latency last week?",
        # job
        "How many job runs failed in the last 7 days, by workspace?",
        "Which workspaces had the most job runs last week?",
    ]


def _example_sqls(cost_mv: str, query_mv: str, job_mv: str) -> list[tuple[str, str]]:
    """(question, sql) — each verified against the live metric views before shipping. Teaches
    the MEASURE()/GROUP BY ALL pattern and which metric view answers which question shape.
    ALL governed metric views — no raw system tables."""
    return [
        # cost
        ("What did we spend in the last 30 days, by product?",
         f"SELECT product, MEASURE(spend_usd) AS spend_usd, MEASURE(dbus) AS dbus "
         f"FROM {cost_mv} WHERE usage_date >= dateadd(day, -30, current_date()) "
         f"GROUP BY ALL ORDER BY spend_usd DESC"),
        ("Which 10 workspaces had the highest spend last month?",
         f"SELECT workspace_name, MEASURE(spend_usd) AS spend_usd FROM {cost_mv} "
         f"WHERE usage_date >= dateadd(day, -30, current_date()) GROUP BY ALL "
         f"ORDER BY spend_usd DESC LIMIT 10"),
        ("What's our serverless vs classic spend split?",
         f"SELECT is_serverless, MEASURE(spend_usd) AS spend_usd FROM {cost_mv} "
         f"WHERE usage_date >= dateadd(day, -30, current_date()) GROUP BY ALL"),
        ("Who are the top 10 users by spend in the last 30 days?",
         f"SELECT run_as, MEASURE(spend_usd) AS spend_usd FROM {cost_mv} "
         f"WHERE usage_date >= dateadd(day, -30, current_date()) AND run_as IS NOT NULL "
         f"GROUP BY ALL ORDER BY spend_usd DESC LIMIT 10"),
        # query
        ("How many queries ran in the last 7 days, by statement type?",
         f"SELECT statement_type, MEASURE(query_count) AS query_count FROM {query_mv} "
         f"WHERE query_date >= dateadd(day, -7, current_date()) GROUP BY ALL ORDER BY query_count DESC"),
        ("Which warehouses ran the most queries last week?",
         f"SELECT warehouse_id, MEASURE(query_count) AS query_count FROM {query_mv} "
         f"WHERE query_date >= dateadd(day, -7, current_date()) AND warehouse_id IS NOT NULL "
         f"GROUP BY ALL ORDER BY query_count DESC LIMIT 10"),
        # job
        ("How many job runs failed in the last 7 days, by workspace?",
         f"SELECT workspace_name, MEASURE(run_count) AS failed_runs FROM {job_mv} "
         f"WHERE run_date >= dateadd(day, -7, current_date()) AND result_state IN ('FAILED','ERROR') "
         f"GROUP BY ALL ORDER BY failed_runs DESC LIMIT 10"),
    ]


def _stable_id(*parts: str) -> str:
    """Deterministic 32-hex id from content, so re-runs keep the SAME id for the same
    question/instruction/SQL (the API keys items by id — random ids churn the space)."""
    import hashlib
    return hashlib.sha256("||".join(parts).encode()).hexdigest()[:32]


def build_serialized_space(metric_views: list[str]) -> str:
    """Build serialized_space in the CANONICAL Genie API schema (validated against the live
    create API): data_sources.tables sorted by identifier; config.sample_questions[{id,
    question:[str]}]; instructions.text_instructions[{id, content:[str]}] (AT MOST ONE);
    instructions.example_question_sqls[{id, question:[str], sql:[str]}] sorted by id. Every
    string field is an ARRAY.

    metric_views is the GOVERNED set the space grounds on — [cost, query, job] fully-qualified.
    We ground ONLY these (no raw system tables, by design). When they're present we also add
    the routing instruction + worked example SQL; if none were built (metric-view support
    absent) we ship an empty space rather than falling back to raw tables."""
    mvs = sorted(set(m for m in metric_views if m))
    body: dict[str, Any] = {
        "version": 2,
        "data_sources": {"tables": [{"identifier": m} for m in mvs]},
    }
    if not mvs:
        return json.dumps(body)  # nothing governed to ground on — empty (never raw tables)
    # Resolve the three views by suffix so the content builders get the right FQ names even
    # if the caller passes them in a different order / a subset.
    def _pick(suffix: str) -> str:
        return next((m for m in mvs if m.endswith("." + suffix)), mvs[0])
    cost_mv, query_mv, job_mv = _pick(COST_MV_NAME), _pick(QUERY_MV_NAME), _pick(JOB_MV_NAME)
    body["config"] = {"sample_questions": sorted(
        ({"id": _stable_id("q", q), "question": [q]} for q in _sample_questions()),
        key=lambda e: e["id"])}
    joined = "\n".join(f"- {t}" for t in _text_instructions(cost_mv, query_mv, job_mv))
    body["instructions"] = {
        "text_instructions": [{"id": _stable_id("ti", joined), "content": [joined]}],
        "example_question_sqls": sorted(
            ({"id": _stable_id("sql", q, s), "question": [q], "sql": [s]}
             for q, s in _example_sqls(cost_mv, query_mv, job_mv)),
            key=lambda e: e["id"]),
    }
    return json.dumps(body)


def ensure_genie_space(warehouse_id: str, metric_views: list[str] | None = None,
                       title: str = GENIE_TITLE, existing_space_id: str = "") -> dict[str, Any]:
    """Create the FinOps Genie Space if it doesn't already exist.

    Idempotency source of truth is ``existing_space_id`` (the id this job
    persisted on a prior run, passed in by the caller): if that space still
    exists we reuse it. We do NOT rely on list_spaces for the dedupe decision —
    it is eventually consistent, so a just-created space isn't returned promptly
    and title-scans would spawn duplicates on every run. Returns
    {"ok", "created", "space_id"} (+ "error" on failure).

    metric_views is the GOVERNED set (fully-qualified) the space grounds on — the space
    NEVER grounds on raw system tables (governed-only, by design). Empty/None → an empty
    space (metric-view support unavailable) rather than a raw-table fallback."""
    metric_views = metric_views or []
    serialized = build_serialized_space(metric_views)

    def _sync_tables(sid: str) -> None:
        """Re-push the full canonical serialized space to a reused space, so a widened
        metric-view set / updated instructions actually reach an already-created space
        (reuse alone would keep the old config). Best-effort."""
        try:
            w.genie.update_space(space_id=sid, serialized_space=serialized,
                                 description=GENIE_DESCRIPTION)
        except Exception:  # noqa: BLE001 - update unsupported/failed → keep the space as-is
            pass

    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        # Reuse the previously-created space if it's still there.
        if existing_space_id:
            try:
                w.genie.get_space(space_id=existing_space_id)
                _sync_tables(existing_space_id)
                return {"ok": True, "created": False, "space_id": existing_space_id}
            except Exception:  # noqa: BLE001 - space was deleted; fall through
                pass
        # Fallback dedupe by TITLE via the list API. The persisted-id round-trip
        # (through <schema>.finops_admin) can fail silently on some runtimes, which
        # made every app restart/deploy create a NEW space (we found 20 dupes in a
        # day). list_spaces IS reliable on a later boot (only racy in the seconds
        # right after a create), so reuse the newest same-title space if one exists
        # before creating another.
        try:
            # Prefix match: the create API appends a timestamp to the title
            # ("Estate Command Center 2026-07-21 08:03:40"), so an exact-equals
            # check never matched and every run created a new space.
            # list_spaces() returns a GenieListSpacesResponse (not iterable) —
            # read .spaces + follow next_page_token.
            _spaces = []
            _tok = None
            for _ in range(50):
                _r = w.genie.list_spaces(page_token=_tok) if _tok else w.genie.list_spaces()
                _spaces.extend(getattr(_r, "spaces", None) or [])
                _tok = getattr(_r, "next_page_token", None)
                if not _tok:
                    break
            found = [s for s in _spaces
                     if (getattr(s, "title", "") or "").startswith(title) and getattr(s, "space_id", None)]
            if found:
                # newest wins (space_id is time-ordered ULID-ish); pick max.
                sid = max(found, key=lambda s: s.space_id).space_id
                _sync_tables(sid)
                return {"ok": True, "created": False, "space_id": sid, "reused_by_title": True}
        except Exception:  # noqa: BLE001 - list unavailable → fall through to create
            pass
        # the create API requires data_sources.tables sorted by identifier
        sp = w.genie.create_space(warehouse_id=warehouse_id, serialized_space=serialized,
                                  title=title, description=GENIE_DESCRIPTION)
        return {"ok": True, "created": True, "space_id": sp.space_id}
    except Exception as e:
        return {"ok": False, "created": False, "space_id": "", "error": str(e)[:250]}


def ensure_llm_endpoint(name: str) -> dict[str, Any]:
    """Verify the Claude serving endpoint exists + is ready.

    databricks-claude-* are workspace-managed pay-per-token Foundation Model API
    endpoints. We verify rather than duplicate them; if it's absent we report the
    exact fix (enable FM APIs / point llm_endpoint at an existing one) instead of
    creating a duplicate external endpoint."""
    if not name:
        return {"ok": False, "endpoint": "", "error": "no llm_endpoint configured"}
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        ep = w.serving_endpoints.get(name)
        state = ep.state.ready.value if getattr(ep, "state", None) and ep.state.ready else "UNKNOWN"
        return {"ok": True, "endpoint": name, "ready": state, "exists": True}
    except Exception as e:
        return {"ok": False, "endpoint": name, "exists": False,
                "error": (f"endpoint '{name}' not found — enable Foundation Model APIs, "
                          "or set llm_endpoint to an existing Claude endpoint. "
                          f"({str(e)[:120]})")}


# Default app name (matches databricks.yml's app_name default). Job names are
# derived from the ACTUAL app name at runtime (see _app_name) so the bundle path
# and the app-self-provision path never create duplicates — even after a rename.
_DEFAULT_APP = "estate-command-center"


def _app_name() -> str:
    """The app name the bundle used (var.app_name). We read it from the app's own
    name via env if available, else the default — so self-provisioned job names
    match the bundle's `${var.app_name}-*-sync` names and dedup correctly."""
    import os
    return (os.environ.get("FINOPS_APP_NAME") or os.environ.get("DATABRICKS_APP_NAME") or _DEFAULT_APP).strip() or _DEFAULT_APP


def ensure_jobs(source_path: str, warehouse_id: str, ingest_schema: str, *,
                run_as_sp: str = "") -> dict[str, Any]:
    """Create the two daily jobs (admin-sync + gold-sync) via the Jobs API if they
    don't already exist — the no-terminal equivalent of the Asset Bundle's job
    resources, for a UI-only "Create app" deploy.

    NOTE: on the normal BUNDLE deploy path the bundle already creates these jobs,
    so this is a no-op there (source_path stays blank). It only does work on the
    UI-only path where source_path points at the synced code. Idempotent by job
    name (names derived from the app name → dedup with the bundle's jobs). Tasks
    point at ``{source_path}/data/*.py``. Non-fatal — returns a status dict."""
    if not source_path:
        # Bundle path: the bundle owns the jobs — nothing to do (NOT an error).
        return {"ok": True, "skipped": "bundle deploy owns the jobs (source_path blank)"}
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service import compute, jobs

        app = _app_name()
        admin_job, gold_job = f"{app}-admin-sync", f"{app}-gold-sync"
        w = WorkspaceClient()
        existing = {j.settings.name for j in w.jobs.list(expand_tasks=False)}
        env = [jobs.JobEnvironment(environment_key="default",
               spec=compute.Environment(client="2", dependencies=["databricks-sdk>=0.28.0"]))]
        run_as = jobs.JobRunAs(service_principal_name=run_as_sp) if run_as_sp else None
        out: dict[str, Any] = {}

        def _mk(name: str, task_key: str, py: str, cron: str) -> None:
            if name in existing:
                out[name] = "exists"
                return
            w.jobs.create(
                name=name,
                tasks=[jobs.Task(
                    task_key=task_key,
                    spark_python_task=jobs.SparkPythonTask(
                        python_file=f"{source_path}/data/{py}",
                        parameters=[warehouse_id, ingest_schema]),
                    environment_key="default")],
                environments=env,
                schedule=jobs.CronSchedule(quartz_cron_expression=cron, timezone_id="UTC",
                                           pause_status=jobs.PauseStatus.PAUSED),
                run_as=run_as)
            out[name] = "created"

        # Schedule the nightly gold-sync (fast/cheap serving layer) + admin-sync.
        _mk(admin_job, "admin_sync", "admin_sync.py", "0 0 2 * * ?")   # 02:00 daily
        _mk(gold_job, "gold_sync", "gold.py", "0 30 1 * * ?")          # 01:30 daily
        return {"ok": True, **out}
    except Exception as e:
        return {"ok": False, "error": str(e)[:250]}


def run_provision(warehouse_id: str, ingest_schema: str, *, genie: bool = True,
                  ai_narration: bool = False,
                  llm_endpoint: str = "databricks-claude-sonnet-4-6",
                  source_path: str = "", run_as_sp: str = "",
                  gold_schema: str = "") -> dict[str, Any]:
    """Ensure the AI features + jobs are provisioned and persist the result into
    ``<ingest_schema>.finops_admin`` (key 'provision') so the app can read the
    auto-created genie_space_id. Idempotent — reuses the previously-persisted
    Genie Space id and skips jobs that already exist. Safe to run at deploy time,
    daily from the job, and on app startup (UI-only path).

    gold_schema (catalog.schema) is where the governed metric views are built; if given,
    Genie is grounded ONLY on those governed views + taught the MEASURE() routing. Falls back
    to the ingest_schema. If neither is usable / metric views can't be built, the space is
    left empty (never grounded on raw system tables — governed-only, by design)."""
    from data import live

    payload: dict[str, Any] = {}
    prior = live.read_admin(warehouse_id, ingest_schema, "provision")
    prior_id = str(((prior["payload"] if prior else {} or {}).get("genie") or {}).get("space_id") or "")

    if genie:
        # Build the governed metric views first (best-effort), then ground Genie ONLY on them.
        # Prefer the gold schema; fall back to the ingest schema (both are app-owned + writable).
        mv_schema = gold_schema or ingest_schema
        metric_views: list[str] = []
        if mv_schema:
            mv_res = build_metric_views(warehouse_id, mv_schema)
            payload["metric_views"] = mv_res
            if mv_res.get("ok"):
                metric_views = mv_res["metric_views"]
        payload["genie"] = ensure_genie_space(warehouse_id, metric_views=metric_views,
                                              existing_space_id=prior_id)
    if ai_narration:
        payload["llm"] = ensure_llm_endpoint(llm_endpoint)
    # Create the daily admin-sync job if a source path is known (UI-only path —
    # the Asset Bundle already creates it on the CLI path; names match so this is
    # a no-op there).
    if source_path:
        payload["jobs"] = ensure_jobs(source_path, warehouse_id, ingest_schema, run_as_sp=run_as_sp)

    # Persist the provision record (Delta) so the app can discover the Genie
    # Space id.
    if payload:
        if ingest_schema:
            from datetime import datetime, timezone

            from data.admin_sync import _ensure_admin_table
            _ensure_admin_table(warehouse_id, ingest_schema)
            body = "'" + json.dumps(payload, default=str).replace("\\", "\\\\").replace("'", "''") + "'"
            ts = datetime.now(timezone.utc).isoformat()
            live._run(warehouse_id, f"DELETE FROM {ingest_schema}.finops_admin WHERE key='provision'", "clear provision")
            live._run(warehouse_id,
                f"INSERT INTO {ingest_schema}.finops_admin (key, payload, ingested_at) "
                f"VALUES ('provision', {body}, TIMESTAMP'{ts}')", "write provision")
    return payload


if __name__ == "__main__":  # deploy-time / manual entry: python -m data.provision <wh> <schema>
    import os
    import sys

    wh = sys.argv[1] if len(sys.argv) > 1 else (
        os.environ.get("DATABRICKS_WAREHOUSE_ID") or os.environ["FINOPS_WAREHOUSE_ID"])
    schema = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("FINOPS_INGEST_SCHEMA", "")
    genie = str(os.environ.get("FINOPS_FEATURE_GENIE", "true")).lower() == "true"
    ai = str(os.environ.get("FINOPS_FEATURE_AI_NARRATION", "false")).lower() == "true"
    llm = os.environ.get("FINOPS_LLM_ENDPOINT", "databricks-claude-sonnet-4-6")
    src = os.environ.get("FINOPS_SOURCE_PATH", "")
    run_as = os.environ.get("FINOPS_JOB_RUN_AS", "")
    gold = os.environ.get("FINOPS_GOLD_SCHEMA", "")
    print("provisioning →", schema or "(no ingest schema — not persisted)")
    for k, v in run_provision(wh, schema, genie=genie, ai_narration=ai, llm_endpoint=llm,
                              source_path=src, run_as_sp=run_as, gold_schema=gold).items():
        print(f"  {k:6} {v}")
