"""Workspace grouping + environment classification.

Large estates group and filter workspaces by their naming convention rather than
looking at every workspace individually (e.g. ``DATA-EDP-*`` core platform,
``DATA-ANA-*`` analytics, ``FORE-*`` forecasting), and want a Production vs
Non-production lens — and a per-environment one (DEV / TEST / PRE-PROD / PROD) —
off the same names. This module turns a workspace name into a **group**, an
**environment tier** and a Production / Non-production **environment**, driven by
config so any customer can point it at their own conventions without a code change.

Config (env vars, all optional):

  FINOPS_WORKSPACE_GROUPS
      Ordered ``prefix:Label`` rules, ``;``-separated. First matching prefix
      (case-insensitive) wins; anything unmatched is "Other". BLANK ⇒ grouping is
      OFF in live mode (every workspace is "Other" and the UI hides the group
      lens/filter). Demo mode ships sample rules matching the sample workspaces.
      e.g. "DATA-EDP-:EDP Core;DATA-ANA-:EDP Edge;FORE-:Forecasting"

  FINOPS_ENV_TIERS
      Ordered ``Tier:token|token`` rules, ``;``-separated. A workspace name is
      split into tokens on any non-alphanumeric character (``-``, ``_``, ``.``,
      space), and the first tier with a matching token wins. BLANK ⇒ the built-in
      tiers below (covers DEV / DEVELOP, TST / TEST / QA / UAT / SIT,
      PPD / PREPROD / STG / STAGING, NONPROD / NPD / NP, PRD / PROD / PRODUCTION,
      SBX / SANDBOX). Unmatched names are "Unclassified".

  FINOPS_ENV_NONPROD_PATTERN
      Optional regex (case-insensitive). When set, a name that matches is ALSO
      Non-production regardless of tier — an escape hatch for conventions the
      token rules can't express.

Pure string classification — no system-table read, no egress. Used to annotate
workspace and job rows so every page can group, filter and roll up cost by group,
tier and environment.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

# Sample rules for DEMO mode only (they match the seeded sample workspace names).
_DEMO_GROUPS = "DATA-EDP-:EDP Core;DATA-ANA-:EDP Edge;FORE-:Forecasting"
_DEFAULT_TIERS = (
    "Dev:dev|develop|development|dv;"
    "Test:tst|test|testing|qa|uat|sit;"
    "Pre-prod:ppd|preprod|stg|stage|staging|perf;"
    "Sandbox:sbx|sandbox|lab|poc|playground;"
    "Non-prod:nonprod|nonprd|npd;"
    "Prod:prd|prod|production|live"
)
NON_PROD_TIERS = {"Dev", "Test", "Pre-prod", "Sandbox", "Non-prod"}

OTHER_GROUP = "Other"
UNCLASSIFIED = "Unclassified"
PROD = "Production"
NONPROD = "Non-production"

_demo_mode = False


def set_demo_mode(enabled: bool) -> None:
    """app.py calls this at startup: demo mode gets sample group rules so the
    seeded workspaces group sensibly; live mode only groups when configured."""
    global _demo_mode
    _demo_mode = bool(enabled)
    reset_cache()


@lru_cache(maxsize=1)
def _group_rules() -> list[tuple[str, str]]:
    """Parsed ``[(prefix_lower, label), …]`` in declared order (first match wins)."""
    raw = os.environ.get("FINOPS_WORKSPACE_GROUPS") or (_DEMO_GROUPS if _demo_mode else "")
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
def _tier_rules() -> list[tuple[str, frozenset[str]]]:
    raw = os.environ.get("FINOPS_ENV_TIERS") or _DEFAULT_TIERS
    rules: list[tuple[str, frozenset[str]]] = []
    for part in raw.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        tier, toks = part.split(":", 1)
        tokens = frozenset(t.strip().lower() for t in toks.split("|") if t.strip())
        if tier.strip() and tokens:
            rules.append((tier.strip(), tokens))
    return rules


@lru_cache(maxsize=1)
def _nonprod_re() -> re.Pattern[str] | None:
    pat = os.environ.get("FINOPS_ENV_NONPROD_PATTERN") or ""
    if not pat:
        return None
    try:
        return re.compile(pat, re.IGNORECASE)
    except re.error:
        return None


_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def groups_configured() -> bool:
    """True when naming-convention group rules are in effect (config or demo)."""
    return bool(_group_rules())


def workspace_group(name: str) -> str:
    """Map a workspace name to its group label via the first matching prefix rule."""
    n = (name or "").strip().lower()
    for prefix, label in _group_rules():
        if n.startswith(prefix):
            return label
    return OTHER_GROUP


@lru_cache(maxsize=4096)
def workspace_tier(name: str) -> str:
    """DEV / TEST / PRE-PROD / … tier from the name's tokens (first rule wins)."""
    tokens = [t.lower() for t in _TOKEN_SPLIT.split(name or "") if t]
    # Also try adjacent pairs joined, so "PRE-PROD" / "NON_PROD" read as
    # "preprod" / "nonprod" — and are checked BEFORE the single tokens, so the
    # "prod" half of "PRE-PROD" can't classify it as Prod.
    pairs = [a + b for a, b in zip(tokens, tokens[1:])]
    for tier, toks in _tier_rules():
        if any(p in toks for p in pairs):
            return tier
    for tier, toks in _tier_rules():
        if any(t in toks for t in tokens):
            return tier
    return UNCLASSIFIED


def workspace_environment(name: str) -> str:
    """Production or Non-production. Non-production when the tier is a non-prod
    tier, or the optional FINOPS_ENV_NONPROD_PATTERN matches; else Production
    (an unclassified name is treated as Production — the conservative default)."""
    rx = _nonprod_re()
    if workspace_tier(name) in NON_PROD_TIERS or (rx is not None and rx.search(name or "")):
        return NONPROD
    return PROD


def reset_cache() -> None:
    """Clear memoised rule parsing (startup mode switch, tests)."""
    _group_rules.cache_clear()
    _tier_rules.cache_clear()
    _nonprod_re.cache_clear()
    workspace_tier.cache_clear()
