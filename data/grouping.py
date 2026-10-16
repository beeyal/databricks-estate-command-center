"""Workspace grouping + environment classification (Chris feedback #2a / #2b).

AEMO groups and filters workspaces by their naming convention rather than looking
at every workspace individually — e.g. ``DATA-EDP-*`` (EDP core), ``DATA-ANA-*``
(EDP edge), ``FORE-*`` (forecasting) — and wants a Production vs Non-production
lens off the same names. This module turns a workspace name into a **group** and
an **environment**, driven entirely by config so any customer can point it at
their own conventions without a code change.

Config (env vars, all optional — sensible AEMO-shaped defaults):

  FINOPS_WORKSPACE_GROUPS
      Ordered ``prefix:Label`` rules, ``;``-separated. First matching prefix
      (case-insensitive) wins. Anything unmatched falls to "Other".
      e.g. "DATA-EDP-:EDP Core;DATA-ANA-:EDP Edge;FORE-:Forecasting"

  FINOPS_ENV_NONPROD_PATTERN
      Regex (case-insensitive) marking a workspace as Non-production. A name that
      matches is "Non-production", everything else "Production".
      Default catches the usual tokens: NONPROD / NON-PROD / DEV / TEST / SANDBOX
      / SBX / UAT / STG / STAGING / QA.

Pure string classification — no system-table read, no egress. Used to annotate
workspace rows (see ``app._annotate_ws_grouping``) so every page can group,
filter and roll up cost by group and by environment.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

# AEMO-shaped defaults (Chris named these conventions in his feedback).
_DEFAULT_GROUPS = "DATA-EDP-:EDP Core;DATA-ANA-:EDP Edge;FORE-:Forecasting"
_DEFAULT_NONPROD = r"(NON[-_]?PROD|\bDEV\b|DEVELOP|\bTEST\b|SANDBOX|\bSBX\b|\bUAT\b|\bSTG\b|STAGING|\bQA\b)"

_OTHER_GROUP = "Other"
_PROD = "Production"
_NONPROD = "Non-production"


@lru_cache(maxsize=1)
def _group_rules() -> list[tuple[str, str]]:
    """Parsed ``[(prefix_lower, label), …]`` in declared order (first match wins)."""
    raw = os.environ.get("FINOPS_WORKSPACE_GROUPS") or _DEFAULT_GROUPS
    rules: list[tuple[str, str]] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        prefix, label = part.split(":", 1)
        prefix, label = prefix.strip(), label.strip()
        if prefix and label:
            rules.append((prefix.lower(), label))
    return rules


@lru_cache(maxsize=1)
def _nonprod_re() -> re.Pattern[str]:
    pat = os.environ.get("FINOPS_ENV_NONPROD_PATTERN") or _DEFAULT_NONPROD
    try:
        return re.compile(pat, re.IGNORECASE)
    except re.error:
        return re.compile(_DEFAULT_NONPROD, re.IGNORECASE)


def workspace_group(name: str) -> str:
    """Map a workspace name to its group label via the first matching prefix rule."""
    n = (name or "").strip().lower()
    for prefix, label in _group_rules():
        if n.startswith(prefix):
            return label
    return _OTHER_GROUP


def workspace_environment(name: str) -> str:
    """Classify a workspace name as Production or Non-production."""
    return _NONPROD if _nonprod_re().search(name or "") else _PROD


def reset_cache() -> None:
    """Clear memoised rule parsing (tests that mutate the environment)."""
    _group_rules.cache_clear()
    _nonprod_re.cache_clear()
