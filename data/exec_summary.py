"""Optional AI narrative for the account-level executive summary (landing view).

This is a PURE ENHANCEMENT layer on top of the pure-SQL overview payload. The
landing view is fully functional without it — this only turns the numbers the
summary already computed (spend MoM, top cost driver, rolled savings, the
needs-attention list) into a short plain-English "what changed this month & why"
paragraph.

Contract (mirrors data/rewrite.py):
  * ``generate`` is a ``prompt -> text`` callable backed by the in-region model,
    or None when AI is off / no endpoint. When None we return status
    ``unavailable`` with an empty narrative — NEVER a hard error, never a blank
    panel. The caller (and UI) treat AI as optional and hide the narrative.
  * We send only the aggregated $ figures + labels the summary already shows —
    no row-level data, no PII — to keep it honest and in-region.
  * Deterministic prompt; the model only writes prose, never numbers we didn't
    give it. The UI still shows the real figures alongside.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

STATUS_OK = "ok"
STATUS_UNAVAILABLE = "unavailable"   # AI off / no endpoint — hide the narrative
STATUS_ERROR = "error"               # model/network failure — honest message


def _fmt_usd(n: float) -> str:
    n = float(n or 0)
    if abs(n) >= 1e6:
        return f"${n/1e6:.1f}M"
    if abs(n) >= 1e3:
        return f"${n/1e3:.0f}K"
    return f"${n:.0f}"


def build_prompt(ov: dict[str, Any]) -> str:
    """Assemble the instruction from the already-computed overview figures.
    Only aggregated numbers/labels the summary already shows are included."""
    spend = ov.get("total_spend_usd_month", 0)
    mom_pct = ov.get("spend_mom_pct", 0.0)
    mom_delta = ov.get("spend_mom_delta_usd", 0)
    savings = ov.get("addressable_savings_usd_month", ov.get("total_savings_usd_month", 0))
    save_pct = ov.get("savings_pct", 0.0)
    mau = ov.get("mau", 0)
    mau_delta = ov.get("mau_delta", 0)
    q_delta = ov.get("queries_30d_delta_pct", 0.0)
    drivers = (ov.get("cost_drivers") or {}).get("drivers", [])[:3]
    top_drivers = ", ".join(f"{d.get('label') or d.get('driver')} ({round(d.get('pct_of_total',0)*100)}%)" for d in drivers)
    attention = ov.get("needs_attention", [])[:4]
    att_lines = "\n".join(
        f"- {a.get('title','')}" + (f" (~{_fmt_usd(a.get('est_savings_usd_month',0))}/mo)" if a.get('est_savings_usd_month') else "")
        for a in attention
    ) or "- (none flagged)"
    direction = "up" if mom_delta > 0 else "down" if mom_delta < 0 else "flat"

    # Extra measured estate figures for a fuller briefing.
    wau = ov.get("wau")
    qhours = ov.get("query_hours_30d")
    breadth = ov.get("num_products")
    genie_ad = ov.get("genie_adopters")
    ai_ad = ov.get("ai_adopters")
    automated = ov.get("automated_pct")
    serverless = ov.get("serverless_share")
    untagged = ov.get("untagged_spend_pct")
    crit = ov.get("num_critical", 0)

    def _pct(x: Any) -> str:
        return "n/a" if x is None else f"{round(float(x)*100)}%"

    return (
        "You are a Databricks FinOps analyst writing a thorough executive briefing for a "
        "platform-team leader — the kind they'd forward to their VP. Write FOUR paragraphs, "
        "each 3-4 sentences (aim for ~14-18 sentences total — this should be "
        "noticeably richer than a one-line summary). Use ONLY the figures below — do not invent "
        "numbers — but go beyond restating them: INTERPRET what the numbers mean, connect them "
        "(e.g. is spend growth matched by usage growth?), call out what CHANGED and the likely "
        "why, note implications, and end with a clear recommended focus. Plain-English, "
        "business-focused, no SQL/jargon.\n"
        "FORMATTING RULES (strict): Separate the four paragraphs with ONE blank line. Write "
        "flowing prose ONLY — do NOT number the paragraphs, do NOT prefix them with labels like "
        "'Paragraph 1', '1)', '1.', 'First,', or a bold/underlined title, and do NOT use markdown "
        "headers, bold, or bullets. Just four plain paragraphs of prose. Cover, in this order:\n"
        "- Cost & what's moving — the spend picture, MoM change and the driver behind it, and "
        "whether the compute mix (serverless/automation) helps or hurts.\n"
        "- Adoption & value — who's using the platform and how intensively; is usage growing "
        "in line with spend (efficient) or is spend outpacing usage (worth investigating)?\n"
        "- Health & governance — operational health and the untagged-spend attribution gap, "
        "and what that means for accountability.\n"
        "- Where to focus — the single biggest opportunity and the top 1-2 items needing "
        "attention, framed as a recommended next step.\n"
        "IMPORTANT: the 'addressable savings' figure is a MODELED ESTIMATE (a headroom signal "
        "from automation/serverless gaps), NOT a measured or committed number — describe it as "
        "an opportunity/estimate to validate on the Recommendations tab, never as guaranteed or "
        "banked savings. Every other figure is measured from system tables.\n\n"
        f"COST — total spend this month {_fmt_usd(spend)} ({direction} {_fmt_usd(abs(mom_delta))}, "
        f"{abs(mom_pct)*100:.0f}% MoM). Top drivers: {top_drivers or 'n/a'}. "
        f"Compute mix: {_pct(serverless)} serverless, {_pct(automated)} automated.\n"
        f"ADOPTION — {mau} active users in 30d" + (f" ({wau} in 7d)" if wau is not None else "") +
        f", {ov.get('queries_30d',0):,} queries" + (f" over {qhours:,.0f} compute-hours" if qhours else "") +
        f", query volume {q_delta*100:+.0f}% vs prior 30d" +
        (f"; {breadth} products used on average" if breadth else "") +
        (f"; {genie_ad} workspaces on Genie, {ai_ad} on other AI." if (genie_ad is not None) else ".") + "\n"
        f"HEALTH/GOVERNANCE — {crit} workspaces in critical health; {_pct(untagged)} of spend untagged. "
        f"Estimated optimisation opportunity {_fmt_usd(savings)}/mo (~{round(save_pct*100)}% of spend, MODELED).\n"
        f"Flagged for attention:\n{att_lines}\n"
    )


# Leading enumerators / labels a model sometimes prefixes onto each paragraph
# despite the prompt (e.g. "Paragraph 1:", "1)", "1.", "1 -", "**Cost**:",
# "First,"). We strip them so the narrative renders as clean prose. Markdown
# emphasis markers and stray bullet glyphs are stripped too.
_LEAD_LABEL_RE = re.compile(
    r"""^\s*
        (?:[*_#>\-•–—\s]*)          # stray bullet / markdown / dashes
        (?:
            (?:paragraph\s*)?\d+\s*[\.\)\:\-–—]\s+   # "1) " "1. " "Paragraph 1: "
          | (?:first|second|third|fourth|fifth)\s*[,\:\-]\s+   # "First, "
        )?
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _clean_narrative(text: str) -> str:
    """Strip leading paragraph numbers/labels and markdown emphasis so the model
    output renders as plain prose paragraphs (the UI splits on blank lines). Pure
    + defensive: whatever labelling the model adds, the reader sees clean prose."""
    # Normalise Windows newlines; collapse 3+ blank lines to the paragraph break.
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    cleaned = []
    for p in paras:
        # Convert a leading "**Title**" / "__Title__" emphasis run to "Title: "
        # FIRST (before the label stripper eats the leading * as a stray glyph).
        p = re.sub(r"^\s*(\*{1,2}|_{1,2})([^*_]+?)\1[\s:—–-]*", r"\2: ", p)
        # Then strip any leading enumerator / label ("1) ", "Paragraph 2: ", …).
        p = _LEAD_LABEL_RE.sub("", p, count=1)
        cleaned.append(p.strip())
    return "\n\n".join(cleaned)


def generate_narrative(
    ov: dict[str, Any],
    *,
    generate: Optional[Callable[[str], str]] = None,
) -> dict[str, Any]:
    """Produce the optional narrative. ``generate`` None → status unavailable
    (AI off) so the UI hides it. Never raises."""
    result: dict[str, Any] = {"status": STATUS_UNAVAILABLE, "narrative": "", "prompt_used": False}
    if generate is None:
        return result
    try:
        prompt = build_prompt(ov)
        text = (generate(prompt) or "").strip()
    except Exception as e:  # noqa: BLE001 — model/network failure → honest, non-fatal
        result["status"] = STATUS_ERROR
        result["narrative"] = ""
        result["error"] = str(e)[:200]
        return result
    if not text:
        # Model returned nothing (or hit its token ceiling) — treat as unavailable
        # rather than show an empty bubble.
        return result
    result["status"] = STATUS_OK
    result["narrative"] = _clean_narrative(text)
    result["prompt_used"] = True
    return result


if __name__ == "__main__":
    ov = {
        "total_spend_usd_month": 642500, "spend_mom_pct": 0.08, "spend_mom_delta_usd": 47000,
        "addressable_savings_usd_month": 241550, "savings_pct": 0.376, "mau": 46, "mau_delta": 4,
        "queries_30d_delta_pct": 0.12,
        "cost_drivers": {"drivers": [{"label": "SQL", "pct_of_total": 0.34}, {"label": "Jobs", "pct_of_total": 0.22}]},
        "needs_attention": [
            {"title": "Oversized clusters in prod", "est_savings_usd_month": 3200},
            {"title": "Model Serving spend up 40% MoM", "est_savings_usd_month": 0},
        ],
    }
    print("=== exec_summary self-check ===")
    # AI off → unavailable, empty, non-fatal
    r = generate_narrative(ov, generate=None)
    assert r["status"] == STATUS_UNAVAILABLE and r["narrative"] == "", r
    # AI on (fake generator)
    r = generate_narrative(ov, generate=lambda p: "Spend rose 8% to $642K this month, driven mainly by SQL. There's $241K of addressable savings, chiefly oversized clusters.")
    assert r["status"] == STATUS_OK and r["narrative"], r
    # model returns empty → unavailable, not a blank bubble
    r = generate_narrative(ov, generate=lambda p: "")
    assert r["status"] == STATUS_UNAVAILABLE, r
    # model throws → error, non-fatal
    def _boom(p): raise RuntimeError("endpoint down")
    r = generate_narrative(ov, generate=_boom)
    assert r["status"] == STATUS_ERROR and r["narrative"] == "", r
    # sanitizer strips numbered/labelled paragraph prefixes → clean prose
    labelled = "1) Cost is up.\n\nParagraph 2: Adoption is healthy.\n\n**Health** — untagged spend is high.\n\n4. Focus on tagging."
    r = generate_narrative(ov, generate=lambda p: labelled)
    n = r["narrative"]
    assert r["status"] == STATUS_OK, r
    assert "1)" not in n and "Paragraph 2" not in n and "4." not in n, f"labels leaked: {n!r}"
    assert "**" not in n, f"markdown leaked: {n!r}"
    assert n.startswith("Cost is up."), f"unexpected: {n!r}"
    assert n.split("\n\n")[1] == "Adoption is healthy.", f"unexpected: {n!r}"
    assert len(n.split("\n\n")) == 4, f"paragraph count: {n!r}"
    print("sanitizer OK:\n" + n)
    print("prompt preview:\n", build_prompt(ov)[:300])
    print("\nself-check OK")
