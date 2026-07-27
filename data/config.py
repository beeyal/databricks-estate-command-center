"""Deploy-time configuration + feature-flag resolution (SPEC §10, §14).

Single source of truth for the flag contract consumed by ``app.py`` and every
``/api/*`` endpoint. Precedence (highest first):

  1. Environment variable  (FINOPS_FEATURE_*, USE_LIVE, GENIE_SPACE_ID, …)
  2. ``config.yaml``        (optional; sits next to app.py)
  3. Built-in default

The three feature flags gate optional surfaces (SPEC §10):

  * ``features.genie``        — the Ask Genie banner + ``/api/genie/ask`` SSE.
  * ``features.ai_narration`` — optional LLM rationale on recommendations.
  * ``features.dqm``          — the Data Quality Monitoring tab + ``/api/dqm``.

A flag being ``false`` must make the surface *inert server-side* too — the
guarded endpoints raise 404 and never issue a Genie/model call. The
deterministic recommender, cost-attribution and Genie-cost attribution are
ALWAYS on (they call no model) and are not gated by any flag.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

# Built-in defaults (lowest precedence). Genie + DQM default on; ai_narration
# off (the deterministic rule text is the safe default — no model call).
_DEFAULTS: dict[str, Any] = {
    # ai_narration defaults OFF — deterministic rule text is the safe, no-model-
    # call default (matches the docstring above + admin_sync/provision, which
    # both default FINOPS_FEATURE_AI_NARRATION to "false" when the env is unset).
    # writeback defaults ON — the acknowledge/ignore state tier. Backed by an
    # in-process store in demo and a small Delta table in live (no Postgres
    # dependency unless Lakebase is explicitly configured). Still independently
    # switchable off (FINOPS_FEATURE_WRITEBACK=false) for a strictly read-only
    # deployment — the controls hide + endpoints 404 when off.
    "features": {"genie": True, "ai_narration": False, "dqm": True, "writeback": True},
    "use_live": False,
    "genie_space_id": "",
    "warehouse_id": "",
    "fx_rate": 1.52,
    "llm_endpoint": "databricks-claude-sonnet-4-6",
    # LEGACY roll-up cache schema (e.g. "main.finops_cc"), superseded by
    # gold_schema (the serving layer). Kept for back-compat; leave blank on new
    # deployments — gold_schema is the required live-mode serving path.
    "rollup_schema": "",
    # Gold-table schema (catalog.schema, e.g. "main.finops_gold"). The nightly
    # finops_gold_sync job precomputes estate payloads + a table-size census here
    # and the app reads them instantly (the app's serving layer). REQUIRED in live
    # mode: the app's reads are over account-wide system tables (tens of millions
    # of rows), so live-per-request is not production-viable — app.py refuses to
    # boot in live mode unless this is set (escape hatch: FINOPS_ALLOW_LIVE_NO_GOLD
    # for read-only evals). Ignored in demo mode.
    "gold_schema": "",
    # UC schema the finops_admin_sync job writes admin-API data into (budgets,
    # policies, tokens, groups, …). The app reads those tables as real data.
    # Blank ⇒ admin-API-backed panels (governance budget/policy/PAT/alert tiles,
    # access groups + PATs) show a 'run finops_admin_sync' prompt.
    "ingest_schema": "",
    # Workspace path holding the code (e.g. a Git folder). When set + live, the
    # app self-provisions the two daily jobs on startup via the Jobs API — the
    # no-CLI path. Blank ⇒ jobs come from the Asset Bundle (CLI deploy) instead.
    "source_path": "",
    # Optional SP to run the auto-created jobs as (needs account+workspace admin).
    "job_run_as": "",
    # Optional customer-maintained table mapping workspace_id -> business unit
    # (+ optional owner), e.g. "main.finops_ref.workspace_bu". Expected columns:
    # workspace_id (string), bu (string), owner (string, optional). When set +
    # live, the app left-joins it to attribute workspaces/cost to a BU. Blank ⇒
    # NO business-unit dimension: the app hides all BU columns/filters/splits
    # rather than showing an empty "—" (a customer review #16). Demo always has BUs.
    "workspace_bu_table": "",
    # Optional customer-maintained table mapping a user -> team, e.g.
    # "main.finops_ref.user_team". Expected columns: user (string, email/principal
    # matching system.query.history.executed_by) and team (string). When set +
    # live, the Adoption tab shows a Team column + per-team rollups. Blank ⇒ the
    # app hides the team dimension entirely (a customer review #14). Demo always has it.
    "user_team_table": "",
}


def _as_bool(val: Any, fallback: bool) -> bool:
    if isinstance(val, bool):
        return val
    if val is None:
        return fallback
    return str(val).strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def _file_config() -> dict[str, Any]:
    """Load config.yaml if present. Never raises — a missing/broken file just
    means the env + defaults take over."""
    if not _CONFIG_PATH.exists():
        return {}
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(_CONFIG_PATH.read_text()) or {}
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _resolve_flag(name: str, key: str) -> bool:
    """Resolve one feature flag: env var > config.yaml > default."""
    default = bool(_DEFAULTS["features"][key])
    file_features = _file_config().get("features") or {}
    file_val = file_features.get(key, default)
    env_val = os.environ.get(name)
    if env_val is not None:
        return _as_bool(env_val, _as_bool(file_val, default))
    return _as_bool(file_val, default)


@lru_cache(maxsize=1)
def get_features() -> dict[str, bool]:
    """The resolved ``{genie, ai_narration, dqm}`` flag contract."""
    return {
        "genie": _resolve_flag("FINOPS_FEATURE_GENIE", "genie"),
        "ai_narration": _resolve_flag("FINOPS_FEATURE_AI_NARRATION", "ai_narration"),
        "dqm": _resolve_flag("FINOPS_FEATURE_DQM", "dqm"),
        "writeback": _resolve_flag("FINOPS_FEATURE_WRITEBACK", "writeback"),
    }


def _resolve_scalar(env_names: list[str], key: str) -> Any:
    """Resolve a scalar config value: env var > config.yaml > default."""
    for env in env_names:
        val = os.environ.get(env)
        if val not in (None, ""):
            return val
    file_val = _file_config().get(key)
    if file_val not in (None, ""):
        return file_val
    return _DEFAULTS[key]


@lru_cache(maxsize=1)
def get_settings() -> dict[str, Any]:
    """Resolved non-flag settings surfaced to the app + UI."""
    use_live_raw = _resolve_scalar(["USE_LIVE"], "use_live")
    fx_raw = _resolve_scalar(["FINOPS_FX_AUD", "FINOPS_FX_RATE"], "fx_rate")
    try:
        fx = float(fx_raw)
    except (TypeError, ValueError):
        fx = float(_DEFAULTS["fx_rate"])
    return {
        "use_live": _as_bool(use_live_raw, False),
        "genie_space_id": _resolve_scalar(["GENIE_SPACE_ID", "FINOPS_GENIE_SPACE_ID"], "genie_space_id"),
        "warehouse_id": _resolve_scalar(["DATABRICKS_WAREHOUSE_ID", "FINOPS_WAREHOUSE_ID"], "warehouse_id"),
        "fx_rate": fx,
        "llm_endpoint": _resolve_scalar(["FINOPS_LLM_ENDPOINT"], "llm_endpoint"),
        "rollup_schema": _resolve_scalar(["FINOPS_ROLLUP_SCHEMA"], "rollup_schema"),
        "gold_schema": _resolve_scalar(["FINOPS_GOLD_SCHEMA"], "gold_schema"),
        "ingest_schema": _resolve_scalar(["FINOPS_INGEST_SCHEMA"], "ingest_schema"),
        "source_path": _resolve_scalar(["FINOPS_SOURCE_PATH"], "source_path"),
        "job_run_as": _resolve_scalar(["FINOPS_JOB_RUN_AS"], "job_run_as"),
        "workspace_bu_table": _resolve_scalar(["FINOPS_WORKSPACE_BU_TABLE"], "workspace_bu_table"),
        "user_team_table": _resolve_scalar(["FINOPS_USER_TEAM_TABLE"], "user_team_table"),
    }


def bu_enabled() -> bool:
    """Whether the app should surface the business-unit dimension at all.

    Demo mode always has BUs (the seeded workspaces carry them). Live mode has a
    real BU only when the customer points ``workspace_bu_table`` at their own
    workspace->BU mapping table; otherwise there is NO system-table source for
    org structure, so the UI hides BU columns/filters/splits entirely rather than
    showing an empty placeholder (a customer review #16)."""
    s = get_settings()
    if not _as_bool(s.get("use_live"), False):
        return True
    return bool(str(s.get("workspace_bu_table") or "").strip())


def team_enabled() -> bool:
    """Whether the app should surface the user->team dimension. Demo always has
    it; live mode only when ``user_team_table`` is configured (no system-table
    source for team membership otherwise) — else the UI hides team."""
    s = get_settings()
    if not _as_bool(s.get("use_live"), False):
        return True
    return bool(str(s.get("user_team_table") or "").strip())


def reset_cache() -> None:
    """Clear memoised config — used by tests that mutate the environment."""
    _file_config.cache_clear()
    get_features.cache_clear()
    get_settings.cache_clear()
