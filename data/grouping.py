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
    "Test:tst|test|testing|qa|uat|sit|fat|sat;"
    "Pre-prod:ppd|preprod|stg|stage|staging|perf|prf;"
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
_DEFAULT_NOISE = "adb,dbw,databricks,aue,ase,aueast,ausoutheast,ause"


@lru_cache(maxsize=1)
def _noise_words() -> frozenset[str]:
    """Filler words dropped from an automatic platform name (technology / region
    suffixes that every workspace shares). FINOPS_PLATFORM_STRIP_WORDS overrides
    (comma-separated; set it to "-" to strip nothing)."""
    raw = os.environ.get("FINOPS_PLATFORM_STRIP_WORDS") or _DEFAULT_NOISE
    return frozenset(w.strip().lower() for w in raw.split(",") if w.strip() and w.strip() != "-")
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
            out.extend(_split_glued(sm.group(0), base + sm.start()))
    return out


# Three-letter environment abbreviations that are also recognised when glued to
# a longer single-case word ("dataedpprd", "mrktderrtst", "devforecasting"),
# as long as at least four letters remain. Only the short codes — the full
# words ("test", "prod") glued to others ("latest", "products") are far more
# likely to be ordinary English.
_GLUE_ABBR = ("dev", "tst", "prd", "ppd", "stg", "uat", "sit", "sbx", "npd", "poc")
_GLUE_MIN_REST = 4


def _split_glued(tok: str, start: int) -> list[tuple[str, int, int]]:
    low = tok.lower()
    if (len(tok) >= 3 + _GLUE_MIN_REST and tok.isalpha() and (tok.islower() or tok.isupper())
            and low not in _all_env_tokens()):
        for ab in _GLUE_ABBR:
            if ab not in _all_env_tokens():
                continue
            if low.endswith(ab):
                k = len(tok) - 3
                return [(tok[:k], start, start + k), (tok[k:], start + k, start + len(tok))]
            if low.startswith(ab):
                return [(tok[:3], start, start + 3), (tok[3:], start + 3, start + len(tok))]
    return [(tok, start, start + len(tok))]


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


@lru_cache(maxsize=1)
def _tok2tier() -> tuple[dict[str, str], dict[str, int], dict[str, str]]:
    """token → tier (first rule wins), tier → rule order, and the two-letter
    stem of each three-letter token for numbered variants (DE2, TS2, UA2, PO2 =
    a second Dev / Test / UAT / POC environment) when that stem is unambiguous."""
    tok: dict[str, str] = {}
    order: dict[str, int] = {}
    stems: dict[str, set[str]] = {}
    for i, (tier, toks) in enumerate(_tier_rules()):
        order.setdefault(tier, i)
        for t in toks:
            tok.setdefault(t, tier)
            if len(t) == 3 and t.isalpha():
                stems.setdefault(t[:2], set()).add(tok[t])
    stem = {k: next(iter(v)) for k, v in stems.items() if len(v) == 1}
    return tok, order, stem


@lru_cache(maxsize=4096)
def _env_scan(name: str) -> tuple[str, tuple[bool, ...]]:
    """(tier, env_mask) for a workspace name. env_mask marks the tokens that
    are the environment word (plus a number glued to it), for platform rebuild.
    Precedence: joined pairs ("PRE-PROD", "NON_PROD") → single words → numbered
    variants ("DE2"); within each, the earliest tier rule wins."""
    spans = _token_spans(name)
    low = [t.lower() for t, _a, _b in spans]
    tok, order, stem = _tok2tier()
    mask = [False] * len(low)
    found: list[tuple[int, int, str]] = []   # (stage, rule order, tier)
    for i in range(len(low) - 1):
        t = tok.get(low[i] + low[i + 1])
        if t:
            mask[i] = mask[i + 1] = True
            found.append((0, order[t], t))
    for i, w in enumerate(low):
        t = tok.get(w)
        if t and not mask[i]:
            mask[i] = True
            found.append((1, order[t], t))
    for i in range(len(low) - 1):
        a, b = spans[i], spans[i + 1]
        if (not mask[i] and low[i].isalpha() and len(low[i]) == 2 and low[i + 1].isdigit()
                and a[2] == b[1] and low[i] in stem):
            t = stem[low[i]]
            mask[i] = mask[i + 1] = True
            found.append((2, order[t], t))
    # A number GLUED to the environment word is an instance number ("dev01",
    # "DEV2", "prod2"); a number separated from it ("MDM-01-DEV") belongs to the
    # platform name and is kept.
    for i, w in enumerate(low):
        if w.isdigit() and not mask[i]:
            if (i > 0 and mask[i - 1] and spans[i - 1][2] == spans[i][1]) or \
               (i + 1 < len(low) and mask[i + 1] and spans[i][2] == spans[i + 1][1]):
                mask[i] = True
    # A number that comes AFTER the environment word and ends the name (ignoring
    # filler like ADB/AUE) is also an instance number ("forecasting_DEV_01",
    # "dbw-ap1-prd-aueast-01"). A number BEFORE it stays ("OPS-CRM-01-DEV").
    if found:
        noise = _noise_words()
        tail = [i for i, w in enumerate(low) if not mask[i] and w not in noise]
        if tail and low[tail[-1]].isdigit():
            j = tail[-1]
            prev = [i for i in range(j) if low[i] not in noise]
            if prev and mask[prev[-1]]:
                mask[j] = True
    tier = min(found)[2] if found else UNCLASSIFIED
    return tier, tuple(mask)


def workspace_tier(name: str) -> str:
    """DEV / TEST / PRE-PROD / … tier from the name's words."""
    return _env_scan(name or "")[0]


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
    if not spans:
        return raw
    _tier, mask = _env_scan(raw)
    keep = [not m for m in mask]
    if all(keep) or not any(keep):
        return raw                              # nothing (or only an env word) to strip
    # Also drop technology / region filler words (ADB, DBW, AUE…) so
    # "CORE-PAY-ENG-TST-ADB-AUE" is platform "CORE-PAY-ENG" — but only while
    # something meaningful remains.
    noise = _noise_words()
    trial = [k and spans[i][0].lower() not in noise for i, k in enumerate(keep)]
    if any(trial):
        keep = trial
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
    # Merge runs of removed spans first ("ap1-<prd>-<aueast>-01"), so the
    # separators between them collapse to one.
    while re.search(r"\0[\s_.\-]*\0", txt):
        txt = re.sub(r"\0([\s_.\-]*)\0", lambda m: "\0" if not m.group(1) else m.group(1)[0] + "\0", txt)
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
    _tok2tier.cache_clear()
    _noise_words.cache_clear()
    _env_scan.cache_clear()
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
        # domain-system-type-env-tech-region conventions, numbered environments,
        # acceptance-test codes, filler suffixes, numbers inside the platform name
        ("CORE-PAY-ENG-DE2-ADB-AUE", "Dev", "CORE-PAY-ENG"), ("CORE-PAY-ENG-TS2-ADB-AUE", "Test", "CORE-PAY-ENG"),
        ("CORE-PAY-ENG-PPD-ADB-AUE", "Pre-prod", "CORE-PAY-ENG"), ("CORE-PAY-ENG-PRD-ADB-AUE", "Prod", "CORE-PAY-ENG"),
        ("OPS-CRM-01-FAT-ADB-AUE", "Test", "OPS-CRM-01"), ("OPS-CRM-01-SAT-ADB-AUE", "Test", "OPS-CRM-01"),
        ("OPS-CRM-01-UA2-ADB-AUE", "Test", "OPS-CRM-01"), ("OPS-CRM-01-PRF-ADB-AUE", "Pre-prod", "OPS-CRM-01"),
        ("XYZ-GEN-APP-PO2-ADB-AUE", "Sandbox", "XYZ-GEN-APP"), ("dbw-ap1-prd-aueast-01", "Prod", "ap1"),
        ("coreedpprd-adb-aue", "Prod", "coreedp"), ("opscrmtst-adb-aue", "Test", "opscrm"),
        ("edp-01-prd", "Prod", "edp-01"), ("OPS-CRM-01-MIG-ADB-AUE", UNCLASSIFIED, "OPS-CRM-01-MIG-ADB-AUE"),
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
