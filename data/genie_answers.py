"""Ask Genie — demo answer engine (SPEC §11).

Gated by ``features.genie``. In demo mode this returns canned answers grounded
in the seeded cost/usage data so the banner streams a coherent response without
calling a real Genie space. In live mode ``genie_client.py`` calls the Genie
Conversation API against the configured ``genie_space_id``.

Each answer carries a ``chart`` payload (bars) + follow-up chips, mirroring
GridSense's GenieMeta shape, and every answer ends with the SPEC §11 caveat:
"list-price reference, Account Console is source of truth".
"""
from __future__ import annotations

from typing import Any

from data import generate_demo as demo
from data import genie_cost

CAVEAT = "List-price reference (USD × effective_list.default); the Account Console is the source of truth for billed spend."

# The four suggested-prompt chips from SPEC §11 (kept in sync with the banner).
SUGGESTED_PROMPTS = [
    "Which team's queries cost the most last month?",
    "Untagged spend by workspace",
    "Genie Code vs Genie Space spend for a user",
    "Top external tables to convert",
]


def _top_bu_by_query_cost() -> dict[str, Any]:
    """Answer: which team/BU's queries cost the most. Aggregates the Query
    Advisor rows by workspace BU."""
    ws_bu = {w["workspace"]: w["bu"] for w in demo.get_workspaces()}
    by_bu: dict[str, float] = {}
    for q in demo.get_queries():
        bu = ws_bu.get(q["workspace"], q["workspace"])
        by_bu[bu] = by_bu.get(bu, 0.0) + q["cost_usd"]
    ranked = sorted(by_bu.items(), key=lambda kv: -kv[1])[:6]
    top, top_cost = ranked[0]
    labels = [b for b, _ in ranked]
    values = [round(c) for _, c in ranked]
    text = (
        f"Over the trailing window, **{top}** ran the most expensive queries at "
        f"about ${top_cost:,.0f} in list-price compute — the top cost driver among teams. "
        f"The next highest are {', '.join(labels[1:3])}. "
        "Most of the spend concentrates in a handful of heavy, low-pruning statements; "
        "a governed Genie Space plus a query-timeout cluster policy would curb the worst offenders. "
        + CAVEAT
    )
    return {
        "matched_question": "Which team's queries cost the most last month?",
        "chart": {"type": "bar", "labels": labels, "values": values, "unit": "USD"},
        "follow_ups": ["Untagged spend by workspace", "Top external tables to convert"],
        "text": text,
    }


def _untagged_by_workspace() -> dict[str, Any]:
    """Answer: untagged spend by workspace, from the tagging coverage data."""
    rows = demo._tagging_by_workspace()  # type: ignore[attr-defined]
    ranked = sorted(rows, key=lambda r: -r["untagged_usd_month"])[:6]
    labels = [r["workspace"] for r in ranked]
    values = [round(r["untagged_usd_month"]) for r in ranked]
    total_untagged = sum(r["untagged_usd_month"] for r in rows)
    worst = ranked[0]
    text = (
        f"Roughly ${total_untagged:,.0f}/mo of estate spend has no BU/Project/Owner tag. "
        f"The largest untagged pool is **{worst['workspace']}** at ${worst['untagged_usd_month']:,.0f}/mo "
        f"({worst['tagging_pct']*100:.0f}% tagged). Enforce mandatory tags via cluster policies for classic "
        "compute, and stand up serverless budget policies (AU PuPr) to attribute serverless notebooks/jobs. "
        + CAVEAT
    )
    return {
        "matched_question": "Untagged spend by workspace",
        "chart": {"type": "bar", "labels": labels, "values": values, "unit": "USD"},
        "follow_ups": ["Which team's queries cost the most last month?", "Genie Code vs Genie Space spend for a user"],
        "text": text,
    }


def _genie_code_vs_spaces(question: str) -> dict[str, Any]:
    """Answer: Genie spend by surface (Code / Spaces / Agents), optionally for a
    named user."""
    payload = genie_cost.get_genie_cost()
    # Try to spot a user email in the question for a per-user split.
    user = None
    for r in payload["breakdown"]:
        if r["user_identity"].split("@")[0] in question.lower() or r["user_identity"] in question:
            user = r["user_identity"]
            break
    split_note = (
        "Split is by usage_metadata.genie.surface (GENIE_CODE = Code, GENIE_ONE = Genie One / Databricks One chat, "
        "GENIE_AGENTS = agent surface); the surface enum is open. Budgets can't be separated by Genie surface, only by "
        "user/workspace. Consumption pricing from 6 Jul 2026 with 150 free DBUs/user/month (shared across surfaces), so "
        "figures read light until usage flows. "
    )
    if user:
        rows = [r for r in payload["breakdown"] if r["user_identity"] == user]
        agg: dict[str, float] = {}
        for r in rows:
            agg[r["label"]] = agg.get(r["label"], 0.0) + r["total_list_cost_usd"]
        pairs = sorted(agg.items(), key=lambda kv: -kv[1])
        summary = ", ".join(f"{lbl} ≈ ${v:,.0f}" for lbl, v in pairs) or "no Genie usage"
        text = f"For **{user}** this month by surface: {summary} (list-price DBUs). " + split_note + CAVEAT
        labels = [lbl for lbl, _ in pairs]
        values = [round(v) for _, v in pairs]
    else:
        st = payload["surface_totals"]
        summary = ", ".join(f"{s['label']} ≈ ${s['list_usd']:,.0f}" for s in st) or "no Genie usage"
        text = f"Estate-wide this month by surface: {summary} (list-price DBUs). " + split_note + CAVEAT
        labels = [s["label"] for s in st]
        values = [round(s["list_usd"]) for s in st]
    return {
        "matched_question": "Genie Code vs Genie Space spend for a user",
        "chart": {"type": "bar", "labels": labels, "values": values, "unit": "USD"},
        "follow_ups": ["Untagged spend by workspace", "Which team's queries cost the most last month?"],
        "text": text,
    }


def _top_external_tables() -> dict[str, Any]:
    """Answer: top external tables to convert to managed."""
    ext = [t for t in demo.get_tables() if t["table_type"] == "EXTERNAL"]
    ext.sort(key=lambda t: -t["est_savings_usd_month"])
    top = ext[:6]
    labels = [t["table"] for t in top]
    values = [round(t["est_savings_usd_month"]) for t in top]
    total = sum(t["est_savings_usd_month"] for t in ext)
    lead = top[0]
    text = (
        f"There are {len(ext)} external tables with an estimated ${total:,.0f}/mo of optimisation opportunity. "
        f"The highest-impact conversion is **{lead['fqn']}** (~${lead['est_savings_usd_month']:,.0f}/mo). "
        "Converting external → UC managed (CTAS or DEEP CLONE) unlocks Predictive Optimization (auto OPTIMIZE/VACUUM/ANALYZE) "
        "and Liquid Clustering / CLUSTER BY AUTO. " + CAVEAT
    )
    return {
        "matched_question": "Top external tables to convert",
        "chart": {"type": "bar", "labels": labels, "values": values, "unit": "USD/mo"},
        "follow_ups": ["Which team's queries cost the most last month?", "Untagged spend by workspace"],
        "text": text,
    }


def answer(question: str) -> dict[str, Any]:
    """Route a question to the best-matching canned answer (demo mode).

    Keyword-matched against the four suggested prompts + common phrasings; falls
    back to a helpful estate-summary answer for anything unrecognised.
    """
    q = question.lower()
    if any(k in q for k in ("genie code", "genie space", "genie spaces", "code vs")):
        return _genie_code_vs_spaces(question)
    if any(k in q for k in ("untagged", "tag", "attribut")):
        return _untagged_by_workspace()
    if any(k in q for k in ("external table", "convert", "managed")):
        return _top_external_tables()
    if any(k in q for k in ("team", "queries cost", "most expensive", "expensive quer", "cost the most")):
        return _top_bu_by_query_cost()

    # Fallback: estate cost summary grounded in the overview.
    ov = demo.get_overview()
    text = (
        f"Across {ov['num_workspaces']} workspaces the estate spends about "
        f"${ov['total_spend_usd_month']:,.0f}/mo, with an estimated ${ov['total_savings_usd_month']:,.0f}/mo "
        f"({ov['savings_pct']*100:.0f}%) of addressable savings. Ask about a team's query cost, untagged spend, "
        "Genie Code vs Spaces split, or external tables to convert for a focused breakdown. " + CAVEAT
    )
    drivers = ov["cost_drivers"]["drivers"][:6]
    return {
        "matched_question": question,
        "chart": {"type": "bar", "labels": [d["label"] for d in drivers], "values": [round(d["spend_usd_month"]) for d in drivers], "unit": "USD"},
        "follow_ups": SUGGESTED_PROMPTS[:3],
        "text": text,
    }
