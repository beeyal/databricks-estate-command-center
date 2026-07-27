"""Deploy-time provisioning for the FinOps Command Center.

Most customers won't already have a Genie Space or a wired LLM endpoint, so the
deployment provisions them so the AI features work out of the box:

  • ensure_genie_space — creates a purpose-built "FinOps Command Center" Genie
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

# Tables the FinOps Genie Space is grounded on. Billing is the core; the rest
# add query/lineage context. All are system tables every UC metastore exposes.
# The curated set of system tables Ask Genie can reason over. Kept to the tables
# the app itself reads AND that are broadly granted, so Genie can answer across
# cost, SQL, jobs, warehouses, clusters and workspace context — not just billing.
# Deliberately EXCLUDED:
#   • system.information_schema.*  — per-catalog views, large/noisy for NL Q&A;
#   • system.access.audit          — very large, rarely what a cost question needs;
#   • system.data_quality_monitoring.results / system.data_classification.results
#                                  — optional/grant-gated (adding an ungranted
#                                    table would make the whole Space error).
# workspaces_latest is included so Genie answers with workspace NAMES, not ids.
DEFAULT_GENIE_TABLES = [
    "system.billing.usage",
    "system.billing.list_prices",
    "system.query.history",
    "system.access.table_lineage",
    "system.access.workspaces_latest",
    "system.compute.warehouses",
    "system.compute.clusters",
    "system.compute.node_timeline",
    "system.lakeflow.job_run_timeline",
]

GENIE_TITLE = "FinOps Command Center"
GENIE_DESCRIPTION = (
    "Ask cost, usage & operations questions over Databricks system tables — spend "
    "by workspace / SKU / product / user / month, plus SQL query history, jobs, "
    "warehouses, clusters and lineage context. Auto-provisioned by the FinOps "
    "Command Center app."
)


def ensure_genie_space(warehouse_id: str, tables: list[str] | None = None,
                       title: str = GENIE_TITLE, existing_space_id: str = "") -> dict[str, Any]:
    """Create the FinOps Genie Space if it doesn't already exist.

    Idempotency source of truth is ``existing_space_id`` (the id this job
    persisted on a prior run, passed in by the caller): if that space still
    exists we reuse it. We do NOT rely on list_spaces for the dedupe decision —
    it is eventually consistent, so a just-created space isn't returned promptly
    and title-scans would spawn duplicates on every run. Returns
    {"ok", "created", "space_id"} (+ "error" on failure)."""
    tables = tables or DEFAULT_GENIE_TABLES
    desired = sorted(set(tables))
    serialized = json.dumps({
        "version": 2,
        "data_sources": {"tables": [{"identifier": t} for t in desired]},
    })

    def _sync_tables(sid: str) -> None:
        """Update a reused space's table set to the desired list if it drifted —
        so widening DEFAULT_GENIE_TABLES actually reaches an already-created
        space (reuse alone would keep the old, narrower set). Best-effort."""
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
            # ("FinOps Command Center 2026-07-21 08:03:40"), so an exact-equals
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
_DEFAULT_APP = "finops-command-center"


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
                  source_path: str = "", run_as_sp: str = "") -> dict[str, Any]:
    """Ensure the AI features + jobs are provisioned and persist the result into
    ``<ingest_schema>.finops_admin`` (key 'provision') so the app can read the
    auto-created genie_space_id. Idempotent — reuses the previously-persisted
    Genie Space id and skips jobs that already exist. Safe to run at deploy time,
    daily from the job, and on app startup (UI-only path)."""
    from data import live

    payload: dict[str, Any] = {}
    prior = live.read_admin(warehouse_id, ingest_schema, "provision")
    prior_id = str(((prior["payload"] if prior else {} or {}).get("genie") or {}).get("space_id") or "")

    if genie:
        payload["genie"] = ensure_genie_space(warehouse_id, existing_space_id=prior_id)
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
    print("provisioning →", schema or "(no ingest schema — not persisted)")
    for k, v in run_provision(wh, schema, genie=genie, ai_narration=ai, llm_endpoint=llm,
                              source_path=src, run_as_sp=run_as).items():
        print(f"  {k:6} {v}")
