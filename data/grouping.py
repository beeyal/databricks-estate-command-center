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
# Inside each separator-delimited piece, also split on case changes and digit
# boundaries, so "ForecastingDev", "devForecasting", "DEV01" and "prod2" yield
# their environment word. Environment words are NOT matched as substrings of a
# longer word ("product" ≠ prod, "device" ≠ dev, "contest" ≠ test).
_SUBTOKEN = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")


_UPPER_PREFIX = re.compile(r"^([A-Z]{2,})([a-z].*)$")


_PIECE = re.compile(r"[A-Za-z0-9]+")


def _token_spans(name: str) -> list[tuple[str, int, int]]:
    """(token, start, end) over the original string — separators, case changes
    and digit boundaries all split; spans let the platform be rebuilt from the
    ORIGINAL text (keeping its separators and casing)."""
    out: list[tuple[str, int, int]] = []
    for pm in _PIECE.finditer(name or ""):
        piece, base = pm.group(0), pm.start()
        # "DEVforecasting": an upper-case environment word glued to a lower-case
        # word (the generic camel-case rule can't tell that from an acronym).
        um = _UPPER_PREFIX.match(piece)
        if um and um.group(1).lower() in _all_env_tokens():
            out.append((um.group(1), base, base + len(um.group(1))))
            base += len(um.group(1))
            piece = um.group(2)
        subs = list(_SUBTOKEN.finditer(piece))
        if not subs:
            out.append((piece, base, base + len(piece)))
        for sm in subs:
            out.append((sm.group(0), base + sm.start(), base + sm.end()))
    return out


def _tokens(name: str) -> list[str]:
    return [t for t, _a, _b in _token_spans(name)]


def _all_env_tokens() -> set[str]:
    rules = _tier_rules()
    return set().union(*(toks for _t, toks in rules)) if rules else set()


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
    tokens = [t.lower() for t in _tokens(name)]
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


@lru_cache(maxsize=1)
def _platform_re() -> re.Pattern[str] | None:
    pat = os.environ.get("FINOPS_PLATFORM_PATTERN") or ""
    if not pat:
        return None
    try:
        rx = re.compile(pat, re.IGNORECASE)
        return rx if "platform" in rx.groupindex else None
    except re.error:
        return None


@lru_cache(maxsize=4096)
def workspace_platform(name: str) -> str:
    """The platform / product a workspace belongs to, independent of its
    environment: the workspace name with its environment word removed, so
    ``dev-forecasting`` / ``test-forecasting`` / ``prod-forecasting`` are all
    "forecasting" and ``DATA-EDP-TST`` / ``DATA-EDP-PRD`` are both "DATA-EDP".
    Lets a team pick a platform and then see its Dev / Test / Pre-prod / Prod
    breakdown. A name with no environment word is its own platform.
    FINOPS_PLATFORM_PATTERN (a regex with a ``(?P<platform>…)`` group) overrides
    the automatic rule for conventions it can't handle."""
    raw = (name or "").strip()
    # Configured naming rules win over the automatic rule (e.g. DATA-EDP- → EDP Core).
    n = raw.lower()
    for prefix, label in _group_rules():
        if n.startswith(prefix):
            return label
    rx = _platform_re()
    if rx is not None:
        m = rx.search(raw)
        if m and m.group("platform"):
            return m.group("platform")
    spans = _token_spans(raw)
    tokens = [t for t, _a, _b in spans]
    if not tokens:
        return raw
    env_tokens = _all_env_tokens()
    keep = [True] * len(tokens)
    low = [t.lower() for t in tokens]
    for i in range(len(tokens) - 1):           # "PRE-PROD", "NON_PROD" pairs
        if low[i] + low[i + 1] in env_tokens:
            keep[i] = keep[i + 1] = False
    for i, t in enumerate(low):
        if t in env_tokens:
            keep[i] = False
    # A number right next to the environment word is an instance number
    # ("dev01", "DEV_01", "prod2"), not part of the platform name.
    for i, t in enumerate(low):
        if t.isdigit() and ((i > 0 and not keep[i - 1] and low[i - 1] in env_tokens)
                            or (i + 1 < len(low) and not keep[i + 1] and low[i + 1] in env_tokens)):
            keep[i] = False
    if all(keep) or not any(keep):
        return raw                              # nothing (or only an env word) to strip
    # Cut the dropped spans out of the ORIGINAL string, then tidy the separators
    # left behind, so "PDFReader-prod" → "PDFReader" and "net_prod_v2" → "net_v2".
    out, pos = [], 0
    for (_t, a, b), k in zip(spans, keep):
        if not k:
            out.append(raw[pos:a])
            out.append("\0")                      # marker where a span was removed
            pos = b
    out.append(raw[pos:])
    txt = "".join(out)
    txt = re.sub(r"[\s_.\-]*\0[\s_.\-]*", lambda m: _join_sep(m.group(0)), txt)
    txt = re.sub(r"\(\s*\)", "", txt)            # "Net (Prod)" → "Net"
    return txt.strip(" -_.") or raw


def _join_sep(chunk: str) -> str:
    """Separator to leave where an environment word was removed: the first real
    separator around it (so "a-dev-b" → "a-b", "a_dev_b" → "a_b"), or nothing."""
    seps = [c for c in chunk if c in "-_. "]
    return seps[0] if seps else ""


def classify(name: str, mapping: dict[str, str] | None = None) -> dict[str, str]:
    """Platform + environment for one workspace, with WHERE each came from, by
    precedence: the customer's mapping table (``platform`` / ``environment``
    columns) → configured naming rules → the workspace name. Returns
    {platform, platform_source, env_tier, environment, env_source, group}.

    ``*_source`` is "mapping" | "rule" | "name" | "none", so the UI can be honest
    about how much of the estate is classified from naming alone."""
    m = mapping or {}
    raw = (name or "").strip()
    # Platform
    if m.get("platform"):
        platform, psrc = m["platform"], "mapping"
    else:
        n = raw.lower()
        rule = next((label for prefix, label in _group_rules() if n.startswith(prefix)), None)
        if rule:
            platform, psrc = rule, "rule"
        else:
            platform = workspace_platform(raw)
            psrc = "name" if platform != raw else "none"
    # Environment
    if m.get("environment"):
        val = m["environment"]
        tier = workspace_tier(val)
        if tier == UNCLASSIFIED:
            tier = val.strip().title()
        env = NONPROD if (tier in NON_PROD_TIERS or (tier not in ("Prod",) and tier != UNCLASSIFIED
                                                     and workspace_environment(val) == NONPROD)) else PROD
        esrc = "mapping"
    else:
        tier = workspace_tier(raw)
        env = workspace_environment(raw)
        esrc = "name" if tier != UNCLASSIFIED else "none"
    return {"platform": platform, "platform_source": psrc, "env_tier": tier, "environment": env,
            "env_source": esrc, "group": workspace_group(raw)}


def reset_cache() -> None:
    """Clear memoised rule parsing (startup mode switch, tests)."""
    _group_rules.cache_clear()
    _tier_rules.cache_clear()
    _nonprod_re.cache_clear()
    _platform_re.cache_clear()
    workspace_tier.cache_clear()
    workspace_platform.cache_clear()


if __name__ == "__main__":
    # Self-check: environment / platform from the common workspace naming styles
    # (prefix, suffix, middle, camel-case, glued, instance numbers, false friends).
    reset_cache()
    cases = [
        ("dev-forecasting", "Dev", "forecasting"), ("forecasting-dev", "Dev", "forecasting"),
        ("Forecasting Dev", "Dev", "Forecasting"), ("forecasting_DEV_01", "Dev", "forecasting"),
        ("DEVforecasting", "Dev", "forecasting"), ("ForecastingDev", "Dev", "Forecasting"),
        ("dev01-net", "Dev", "net"), ("prod2_net", "Prod", "net"), ("net_prod_v2", "Prod", "net_v2"),
        ("net-tst", "Test", "net"), ("tst_net", "Test", "net"), ("NET.PRD", "Prod", "NET"),
        ("net uat", "Test", "net"), ("QA_Billing", "Test", "Billing"), ("Net (Prod)", "Prod", "Net"),
        ("pre-prod-net", "Pre-prod", "net"), ("net-preprod", "Pre-prod", "net"),
        ("DATA-EDP-TST", "Test", "DATA-EDP"), ("DATA-EDP-PPD", "Pre-prod", "DATA-EDP"),
        ("DATA-ANA-SCADA-NONPROD", "Non-prod", "DATA-ANA-SCADA"), ("sbx-labs", "Sandbox", "labs"),
        ("product-analytics", UNCLASSIFIED, "product-analytics"),
        ("device-telemetry", UNCLASSIFIED, "device-telemetry"), ("contest-app", UNCLASSIFIED, "contest-app"),
        ("prod", "Prod", "prod"),
    ]
    bad = 0
    for name, tier, plat in cases:
        got = (workspace_tier(name), workspace_platform(name))
        if got != (tier, plat):
            bad += 1
            print(f"FAIL {name!r}: got {got}, want {(tier, plat)}")
    m = classify("anything", {"platform": "Lab", "environment": "sandbox"})
    assert (m["platform"], m["env_tier"], m["environment"], m["env_source"]) == ("Lab", "Sandbox", NONPROD, "mapping"), m
    print(f"{len(cases) - bad}/{len(cases)} naming cases OK")
    raise SystemExit(1 if bad else 0)
