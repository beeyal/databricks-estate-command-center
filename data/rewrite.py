"""AI-proposed, EXPLAIN-validated query rewrites (Increment 3).

Extends the deterministic recommender (``data/recommender.py``): for a
rewrite-candidate query, ask the configured *in-region* Claude serving endpoint
to propose a concrete rewritten ``SELECT``, then VALIDATE it by running
``EXPLAIN`` against the customer's own SQL warehouse. Only a rewrite whose
EXPLAIN succeeds — i.e. it parses and *binds against the real schema* — is
surfaced as "validated". A hallucinated rewrite that references a column that
does not exist fails EXPLAIN and is **rejected rather than shown**. That is the
entire point of the feature: validated rewrites, not plausible-looking ones.

Honesty contract (mirrors the rest of the app):
  * EXPLAIN proves the rewrite is RUNNABLE and binds to the real schema. It does
    NOT prove semantic equivalence (identical result set) — we say so explicitly
    and tell the operator to review before running.
  * AI and the warehouse are BOTH optional. With neither we return the
    deterministic guidance only (status ``unavailable``). We never fabricate a
    "validated" rewrite in live mode.
  * READ-ONLY. The candidate must be a single ``SELECT`` / ``WITH`` statement;
    anything else is rejected *before* EXPLAIN, and EXPLAIN never executes the
    query.

The core :func:`propose_rewrite` takes the LLM ``generate`` callable and the
``explain`` callable as arguments, so it is pure, unit-testable, and reused
unchanged by demo mode (canned generate + no explain) and live mode (FMAPI +
warehouse EXPLAIN).
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from data import recommender

# Status the UI renders a badge from.
#   validated     — EXPLAIN succeeded: rewrite parses + binds to the real schema
#   unvalidated   — a candidate was produced but no warehouse was available to
#                   EXPLAIN it (e.g. AI-on demo, or warehouse unset) → show it,
#                   clearly labelled "not validated against your schema"
#   rejected      — a candidate was produced but EXPLAIN failed → do NOT present
#                   it as runnable; surface the binding error instead
#   unavailable   — AI narration/endpoint is off → deterministic guidance only
#   not_applicable— the statement is not a SELECT we can rewrite
STATUS_VALIDATED = "validated"
STATUS_UNVALIDATED = "unvalidated"
STATUS_REJECTED = "rejected"
STATUS_UNAVAILABLE = "unavailable"
STATUS_NA = "not_applicable"

# Only these lead keywords are accepted as a read-only, single-statement query
# we are willing to EXPLAIN. Everything else (INSERT/UPDATE/DELETE/MERGE/DROP/
# CREATE/ALTER/…) is refused before it ever reaches the warehouse.
_SELECT_LEAD = re.compile(r"^\s*(?:WITH|SELECT)\b", re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|"
    r"COPY|REFRESH|OPTIMIZE|VACUUM|ANALYZE|SET|USE|CALL|EXPLAIN)\b",
    re.IGNORECASE,
)


# EXPLAIN failures that are NOT schema-binding failures: the rewrite may be
# perfectly valid, but EXPLAIN can't confirm it because something the STATEMENT
# needs at runtime isn't available to a bare EXPLAIN — bind parameters
# (`:param_end_date`, `?`, IDENTIFIER()), temp views / session state, or a
# widget. Treating these as "rejected" is a false negative that hides a good
# rewrite behind a scary "Rejected by EXPLAIN". They map to UNVALIDATED instead:
# a candidate we produced but honestly could not check against the schema.
_UNVALIDATABLE_EXPLAIN = re.compile(
    r"unbound_sql_parameter|unbound_parameter|parameter_marker|"
    r"missing_parameter|unresolved_variable|cannot_resolve_temp|"
    r"temp_table_or_view|missing.*widget",
    re.IGNORECASE,
)


def _is_unvalidatable_explain_error(detail: str) -> bool:
    """True when an EXPLAIN failure means "can't validate" (bind parameters /
    session state), NOT "the rewrite doesn't bind to your schema"."""
    return bool(detail and _UNVALIDATABLE_EXPLAIN.search(detail))


# A PARSE/SYNTAX failure (not a binding failure). On a parameter-heavy query the
# model can truncate or mangle the rewrite (e.g. an unterminated string literal),
# which fails EXPLAIN with PARSE_SYNTAX_ERROR — that says the CANDIDATE didn't
# parse, NOT that the customer's schema is wrong. On such a query we downgrade to
# UNVALIDATED rather than the scary "rejected (wrong column/table)".
_PARSE_EXPLAIN_ERROR = re.compile(
    r"parse_syntax_error|parseexception|syntax error at or near|"
    r"parse_empty_statement|unclosed|unterminated",
    re.IGNORECASE,
)


def _is_parse_explain_error(detail: str) -> bool:
    """True when an EXPLAIN failure is a parse/syntax error (the candidate SQL
    itself didn't parse), as opposed to a schema-binding error."""
    return bool(detail and _PARSE_EXPLAIN_ERROR.search(detail))


def has_bind_parameters(sql: str) -> bool:
    """True if the query carries bind parameters a bare EXPLAIN can't supply.

    Databricks named markers (``:end_date``) and ``IDENTIFIER(:x)`` are the
    common case in ``system.query.history``. We deliberately do NOT flag ``?``
    (positional markers are rare here and easy to false-match on ``??`` / casts).
    ``::`` casts are excluded so ``col::date`` isn't mistaken for a marker.
    """
    if not sql:
        return False
    scrubbed = _strip_quoted(_strip_sql_comments(sql))
    if re.search(r"\bIDENTIFIER\s*\(", scrubbed, re.IGNORECASE):
        return True
    # A named marker `:word` that is not part of a `::` cast.
    return bool(re.search(r"(?<!:):[A-Za-z_]\w*", scrubbed))


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` block comments so the read-only
    guard can't be fooled by a forbidden keyword hidden behind a comment."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


# String literals / quoted identifiers, blanked before keyword + ';' scanning so
# a value like WHERE op IN ('INSERT','DELETE') doesn't trip the DDL/DML guard and
# a ';' inside a literal isn't mistaken for statement stacking. Handles Spark's
# default backslash escaping (\' inside a '...' literal).
_QUOTED = re.compile(r"'(?:\\.|[^'\\])*'" r'|"(?:\\.|[^"\\])*"' r"|`(?:``|[^`])*`", re.DOTALL)


def _strip_quoted(sql: str) -> str:
    """Replace the CONTENTS of every string literal / quoted identifier with an
    empty token, so structural scanning sees `''` not the literal's text."""
    return _QUOTED.sub("''", sql)


def is_read_only_select(sql: str) -> bool:
    """True iff ``sql`` is a single read-only ``SELECT`` / ``WITH`` statement.

    Rejects statement stacking (a second statement after ``;``) and any DDL/DML
    keyword — defence in depth on top of the fact that we only ever run it under
    ``EXPLAIN``. Comments and string/identifier literals are scrubbed first so the
    guard neither (a) misses a keyword hidden in a comment nor (b) false-rejects a
    legitimate SELECT that merely mentions a keyword inside a string literal.
    """
    if not sql or not sql.strip():
        return False
    # SELECT-lead check runs on the real (literal-preserving) body — the leading
    # token is never inside a literal.
    body = _strip_sql_comments(sql).strip().rstrip(";").strip()
    if not _SELECT_LEAD.match(body):
        return False
    # Structural checks run on the literal-scrubbed body.
    scrubbed = _strip_quoted(body)
    if ";" in scrubbed:  # statement stacking after stripping a single trailing ';'
        return False
    if _FORBIDDEN.search(scrubbed):
        return False
    return True


def extract_sql(text: str) -> Optional[str]:
    """Pull a single SQL statement out of a model response.

    Handles a ```` ```sql ```` fenced block, a bare fenced block, or raw SQL with
    surrounding prose. Returns None if nothing SELECT-shaped is found.
    """
    if not text:
        return None
    # Prefer a fenced code block.
    m = re.search(r"```(?:sql)?\s*(.+?)```", text, re.DOTALL | re.IGNORECASE)
    candidate = m.group(1) if m else text
    # Trim to the first SELECT/WITH … the model sometimes prefixes prose.
    lead = re.search(r"(?is)\b(WITH|SELECT)\b.*", candidate)
    if not lead:
        return None
    sql = lead.group(0).strip()
    # Keep only up to the first statement terminator.
    sql = sql.split(";")[0].strip()
    return sql or None


def _build_prompt(q: dict[str, Any]) -> str:
    """The instruction sent to the in-region Claude endpoint. Deterministic and
    schema-honest: we give the model the real statement + measured metrics and
    forbid it from inventing columns."""
    return (
        "You are a Databricks SQL optimisation assistant. Rewrite the query below "
        "to reduce data scanned and cost WITHOUT changing its result set. Rules: "
        "(1) return ONLY the rewritten SQL in a single ```sql code block, no prose; "
        "(2) it MUST be a single read-only SELECT/WITH statement; "
        "(3) do NOT invent column or table names — use only identifiers present in "
        "the original query; "
        "(4) prefer projecting the explicit columns actually needed instead of "
        "SELECT *, and pushing selective predicates down; "
        "(5) preserve ordering, grouping and semantics.\n\n"
        f"Measured metrics — pruning {q.get('pruning_efficiency', 0):.0%}, "
        f"bytes read {q.get('bytes_read', 0) / 1e9:.0f} GB, "
        f"spill {q.get('spill_gb', 0):.0f} GB, p95 {q.get('p95_s', 0):.0f}s, "
        f"runs {q.get('runs', 0)}.\n\n"
        f"Original query:\n```sql\n{q.get('query_text', '').strip()}\n```"
    )


def propose_rewrite(
    q: dict[str, Any],
    *,
    generate: Optional[Callable[[str], str]],
    explain: Optional[Callable[[str], tuple[bool, str, Optional[str]]]] = None,
    baseline_explain: bool = True,
) -> dict[str, Any]:
    """Produce a (possibly EXPLAIN-validated) rewrite for one query row.

    Parameters
    ----------
    q
        The query row (needs at least ``query_text``; uses metrics for the prompt).
    generate
        ``prompt -> model_text``. If None (AI disabled / no endpoint) we return
        ``status="unavailable"`` with the deterministic next-steps only — never a
        fabricated rewrite.
    explain
        ``sql -> (ok, detail, plan_text)``. Runs EXPLAIN against the real
        warehouse. If None (no warehouse) the candidate is returned ``unvalidated``.
    baseline_explain
        When True and ``explain`` is available, also EXPLAIN the ORIGINAL so the
        UI can show a before/after plan pair.
    """
    original = (q.get("query_text") or "").strip()
    result: dict[str, Any] = {
        "status": STATUS_UNAVAILABLE,
        "candidate_sql": None,
        "explanation": "",
        "validation_detail": "",
        "plan_before": None,
        "plan_after": None,
        "next_steps": list(recommender._NEXT_STEPS.get("rewrite", [])),
        "caveat": (
            "EXPLAIN validation confirms the rewrite parses and binds to your "
            "schema — it does not prove the results are identical. Review for "
            "semantic equivalence before running."
        ),
    }

    if not is_read_only_select(original):
        result["status"] = STATUS_NA
        result["explanation"] = (
            "Automated rewrite is offered only for read-only SELECT queries. This "
            "statement is not a plain SELECT, so no rewrite was generated."
        )
        return result

    if generate is None:
        result["explanation"] = (
            "AI rewrite is turned off (no in-region model endpoint configured). "
            "The deterministic next steps below still apply."
        )
        return result

    # 1) Ask the model for a candidate rewrite.
    try:
        raw = generate(_build_prompt(q))
    except Exception as e:  # noqa: BLE001 — model/network failure → honest fallback
        result["explanation"] = f"Could not reach the rewrite model: {e}"
        return result

    # An empty raw is the generator's signal that the model hit its output token
    # ceiling (the rewrite would be truncated → invalid SQL). Say so honestly
    # instead of implying the model misbehaved.
    if not (raw or "").strip():
        result["explanation"] = (
            "This query is too long to rewrite reliably within the model's output "
            "limit — no rewrite was generated. The deterministic next steps below "
            "still apply."
        )
        return result
    candidate = extract_sql(raw or "")
    if not candidate or not is_read_only_select(candidate):
        result["status"] = STATUS_REJECTED
        result["explanation"] = (
            "The model did not return a valid single read-only SELECT, so no "
            "rewrite is offered. Falling back to the deterministic guidance."
        )
        return result

    result["candidate_sql"] = candidate
    result["explanation"] = (
        "Proposed rewrite projects the needed columns and pushes predicates down "
        "to cut data scanned."
    )

    # 2) Validate against the real schema via EXPLAIN (if a warehouse is wired).
    if explain is None:
        result["status"] = STATUS_UNVALIDATED
        result["validation_detail"] = (
            "No warehouse available to validate — the rewrite has NOT been checked "
            "against your schema. Run EXPLAIN yourself before using it."
        )
        return result

    ok, detail, plan_after = explain(candidate)
    result["plan_after"] = plan_after
    if not ok:
        # Distinguish "can't validate" from "doesn't bind". A parameterised
        # query (`:end_date`, IDENTIFIER(:x)) or one that leans on session state
        # fails EXPLAIN with UNBOUND_SQL_PARAMETER / similar even when the
        # rewrite is perfectly valid — EXPLAIN simply has no value to bind. That
        # is NOT a schema-binding failure, so we surface it as UNVALIDATED (show
        # the rewrite, clearly labelled "couldn't auto-validate") rather than
        # REJECTED (hide it as broken).
        #
        # Decide from the EXPLAIN failure SIGNATURE, not the mere presence of a
        # bind parameter: a parameterised query with a genuinely wrong
        # column/table fails with UNRESOLVED_COLUMN/etc. (which _is_unvalidatable
        # does NOT match) and must correctly fall through to REJECTED. We only
        # lean on has_bind_parameters as a tiebreaker when EXPLAIN gave us no
        # usable detail to classify (empty/uninformative error string).
        # A parse/syntax error on a PARAMETER-HEAVY query almost always means the
        # model truncated/mangled its rewrite (e.g. an unterminated literal), not
        # that the schema is wrong — EXPLAIN can't cleanly validate these anyway
        # (the bind markers aren't supplied), so downgrade to UNVALIDATED instead
        # of the misleading "rejected (wrong column/table)".
        parse_on_param = _is_parse_explain_error(detail) and has_bind_parameters(candidate)
        unvalidatable = _is_unvalidatable_explain_error(detail) or parse_on_param or (
            not (detail or "").strip() and has_bind_parameters(candidate)
        )
        if unvalidatable:
            result["status"] = STATUS_UNVALIDATED
            result["validation_detail"] = (
                "This query uses bind parameters (e.g. :date) or session state, so "
                "EXPLAIN couldn't auto-validate the rewrite without values — it was "
                "NOT checked against your schema. "
                + ("(The model's rewrite also didn't parse cleanly, which is common "
                   "on long parameter-heavy queries.) " if parse_on_param else "")
                + "Review it and run EXPLAIN with your parameter values before using it."
            )
            return result
        result["status"] = STATUS_REJECTED
        # Report the ACTUAL failure kind — a parse/syntax error is the candidate
        # not parsing, which is distinct from "doesn't bind to your schema".
        if _is_parse_explain_error(detail):
            result["validation_detail"] = (
                f"EXPLAIN rejected the rewrite — it didn't parse ({detail}). The "
                "model likely produced malformed or truncated SQL, so it's not "
                "offered as runnable."
            )
        else:
            result["validation_detail"] = (
                f"EXPLAIN rejected the rewrite ({detail}). It does not bind against "
                "your schema, so it is not offered as runnable."
            )
        return result

    result["status"] = STATUS_VALIDATED
    result["validation_detail"] = detail or "EXPLAIN succeeded: the rewrite parses and binds to your schema."
    if baseline_explain:
        try:
            b_ok, _b_detail, plan_before = explain(original)
            if b_ok:
                result["plan_before"] = plan_before
        except Exception:  # noqa: BLE001 — baseline is best-effort only
            pass
    return result


# ---------------------------------------------------------------------------
# Demo mode — a canned, illustrative rewrite (no live model, no warehouse).
# ---------------------------------------------------------------------------

def demo_rewrite(q: dict[str, Any]) -> dict[str, Any]:
    """A deterministic, illustrative rewrite for demo mode.

    Turns ``SELECT *`` into an explicit projection using columns inferred from
    the statement's own predicates (never invents names), and shows a
    before/after plan sketch. Clearly labelled as illustrative — the surrounding
    payload is already marked ``demo`` so the UI shows the sample-data badge.
    """
    original = (q.get("query_text") or "").strip()
    result: dict[str, Any] = {
        "status": STATUS_NA,
        "candidate_sql": None,
        "explanation": "",
        "validation_detail": "",
        "plan_before": None,
        "plan_after": None,
        "next_steps": list(recommender._NEXT_STEPS.get("rewrite", [])),
        "caveat": (
            "Illustrative rewrite (sample data). In live mode the rewrite is "
            "generated by your in-region model and validated with EXPLAIN against "
            "your own schema; it is not proven result-identical — review first."
        ),
    }
    if not is_read_only_select(original):
        result["explanation"] = (
            "Automated rewrite is offered only for read-only SELECT queries."
        )
        return result

    cols = recommender._infer_predicate_columns(original)
    table = q.get("target_table") or recommender._table_from_sql(original) or "<table>"
    # Build an explicit projection: inferred predicate columns first, plus a
    # couple of illustrative measure columns when we have nothing better.
    proj = ", ".join(cols) if cols else "col_a, col_b, col_c"
    candidate = original
    if re.search(r"(?is)\bSELECT\s+\*", original):
        candidate = re.sub(r"(?is)\bSELECT\s+\*", f"SELECT {proj}", original, count=1)
    else:
        # Not a SELECT * — show a predicate-pushdown flavoured note instead.
        candidate = original

    result["candidate_sql"] = candidate
    # DATA HONESTY: demo has NO warehouse to EXPLAIN against, so we must NOT claim
    # "validated" (whose UI badge asserts "EXPLAIN succeeded — parses and binds to
    # your schema"). "unvalidated" is the truthful status — its note says a rewrite
    # was generated but couldn't be auto-checked, review + EXPLAIN it yourself. The
    # whole payload is also demo-flagged with an illustrative caveat.
    result["status"] = STATUS_UNVALIDATED
    result["explanation"] = (
        "Projects the columns actually referenced instead of SELECT *, so the "
        "engine reads fewer columns and prunes more files."
    )
    prune = q.get("pruning_efficiency", 0.2)
    gain = min(0.75, (1 - prune) * 0.8 + 0.15)
    result["validation_detail"] = (
        f"Illustrative EXPLAIN COST (not run — no warehouse in demo): estimated "
        f"scan ↓ ~{gain:.0%} (pruning {prune:.0%} → higher; fewer columns materialised)."
    )
    gb = q.get("bytes_read", 0) / 1e9
    result["plan_before"] = (
        f"== Physical Plan (before) ==\n"
        f"* ColumnarToRow\n"
        f"  +- FileScan parquet {table}[*]  (SELECT *: all columns)\n"
        f"     PushedFilters: []   ReadSchema: <all>   est. scan ≈ {gb:.0f} GB"
    )
    result["plan_after"] = (
        f"== Physical Plan (after) ==\n"
        f"* ColumnarToRow\n"
        f"  +- FileScan parquet {table}[{proj}]\n"
        f"     PushedFilters: [{', '.join(cols) if cols else '…'}]   "
        f"est. scan ≈ {gb * (1 - gain):.0f} GB"
    )
    return result


if __name__ == "__main__":
    import json

    print("=== rewrite self-check ===")
    # read-only guard
    assert is_read_only_select("SELECT a FROM t WHERE x = 1")
    assert is_read_only_select("WITH c AS (SELECT 1) SELECT * FROM c")
    assert not is_read_only_select("DELETE FROM t")
    assert not is_read_only_select("SELECT 1; DROP TABLE t")
    assert not is_read_only_select("SELECT 1 /* then */ ; INSERT INTO t VALUES (1)")
    assert not is_read_only_select("EXPLAIN SELECT 1")  # no nested EXPLAIN
    # keywords / ';' INSIDE string literals must NOT false-reject a real SELECT
    assert is_read_only_select("SELECT * FROM audit WHERE op IN ('INSERT','UPDATE','DELETE')")
    assert is_read_only_select("SELECT * FROM t WHERE note = 'we DROP nothing; really'")
    assert is_read_only_select("SELECT col AS `my update col` FROM t")
    # but a real trailing statement (outside a literal) is still rejected
    assert not is_read_only_select("SELECT * FROM t WHERE x='a'; DELETE FROM t")
    # Pathological: a literal containing BOTH an escaped quote AND a '--' — comment
    # stripping mangles it, so the guard FAILS SAFE (rejects) rather than risk it.
    # (The real injection vector, full_statement_text, is separately regex-guarded.)
    assert not is_read_only_select(r"SELECT * FROM t WHERE s = 'a\'; DROP TABLE t --'")
    # extract_sql
    assert extract_sql("Here you go:\n```sql\nSELECT a FROM t\n```") == "SELECT a FROM t"
    assert extract_sql("SELECT a, b FROM t WHERE x=1") == "SELECT a, b FROM t WHERE x=1"
    assert extract_sql("no sql here") is None

    sample = {
        "query_text": "SELECT * FROM acme.silver.reads WHERE read_date > '2026-06-01'",
        "target_table": "acme.silver.reads",
        "pruning_efficiency": 0.1,
        "bytes_read": 620e9,
        "runs": 2400,
    }

    # AI off
    r = propose_rewrite(sample, generate=None)
    assert r["status"] == STATUS_UNAVAILABLE, r["status"]

    # AI on, no warehouse → unvalidated
    gen = lambda p: "```sql\nSELECT read_date, nmi FROM acme.silver.reads WHERE read_date > '2026-06-01'\n```"
    r = propose_rewrite(sample, generate=gen, explain=None)
    assert r["status"] == STATUS_UNVALIDATED and r["candidate_sql"], r

    # AI on + EXPLAIN ok → validated
    exp_ok = lambda sql: (True, "EXPLAIN succeeded", "== Physical Plan ==\nFileScan …")
    r = propose_rewrite(sample, generate=gen, explain=exp_ok)
    assert r["status"] == STATUS_VALIDATED and r["plan_after"], r

    # AI on + EXPLAIN fails → rejected, candidate not presented as runnable
    exp_bad = lambda sql: (False, "AnalysisException: column 'nmi' cannot be resolved", None)
    r = propose_rewrite(sample, generate=gen, explain=exp_bad)
    assert r["status"] == STATUS_REJECTED, r

    # bind-parameter detection
    assert has_bind_parameters("SELECT * FROM t WHERE d > :end_date")
    assert has_bind_parameters("SELECT * FROM IDENTIFIER(:tbl)")
    assert not has_bind_parameters("SELECT col::date FROM t WHERE x = 1")
    assert _is_unvalidatable_explain_error(
        "[UNBOUND_SQL_PARAMETER] Found the unbound parameter: param_end_date")
    assert not _is_unvalidatable_explain_error("column 'nmi' cannot be resolved")

    # AI on, EXPLAIN fails on an UNBOUND parameter → UNVALIDATED (not rejected):
    # the rewrite is offered but labelled "couldn't auto-validate", never hidden
    # as broken. This is the parameterised-query false-rejection fix.
    param_q = {**sample, "query_text": "SELECT * FROM acme.silver.reads WHERE read_date > :end_date"}
    gen_param = lambda p: "```sql\nSELECT read_date, nmi FROM acme.silver.reads WHERE read_date > :end_date\n```"
    exp_unbound = lambda sql: (False, "[UNBOUND_SQL_PARAMETER] Found the unbound parameter: param_end_date", None)
    r = propose_rewrite(param_q, generate=gen_param, explain=exp_unbound)
    assert r["status"] == STATUS_UNVALIDATED and r["candidate_sql"], r
    assert "bind parameters" in r["validation_detail"], r

    # CRITICAL (Isaac review): a parameterised query whose rewrite has a GENUINE
    # binding error (wrong column/table) must still be REJECTED — the bind
    # parameter must NOT mask a real schema failure. We decide from the EXPLAIN
    # error SIGNATURE, not the presence of a ':param'.
    exp_realbad = lambda sql: (False, "[UNRESOLVED_COLUMN] column `nmi` cannot be resolved", None)
    r = propose_rewrite(param_q, generate=gen_param, explain=exp_realbad)
    assert r["status"] == STATUS_REJECTED, r
    # ...but an EMPTY/uninformative EXPLAIN detail on a parameterised query still
    # degrades to UNVALIDATED (best-effort tiebreaker), never a false rejection.
    exp_empty = lambda sql: (False, "", None)
    r = propose_rewrite(param_q, generate=gen_param, explain=exp_empty)
    assert r["status"] == STATUS_UNVALIDATED, r

    # PARSE error on a PARAMETER-heavy query → UNVALIDATED, not rejected: the
    # model truncated/mangled a long param-driven query (e.g. unterminated
    # literal); that's "couldn't validate", not "your schema is wrong".
    assert _is_parse_explain_error("[PARSE_SYNTAX_ERROR] Syntax error at or near ''")
    assert not _is_parse_explain_error("[UNRESOLVED_COLUMN] column `nmi` cannot be resolved")
    exp_parse = lambda sql: (False, "[PARSE_SYNTAX_ERROR] Syntax error at or near ''. SQLSTATE: 42601", None)
    r = propose_rewrite(param_q, generate=gen_param, explain=exp_parse)
    assert r["status"] == STATUS_UNVALIDATED, r
    assert "didn't parse cleanly" in r["validation_detail"], r
    # But a parse error on a NON-parameterised query is a genuine bad rewrite →
    # REJECTED, and the message names it as a parse (not a binding) failure.
    exp_parse2 = lambda sql: (False, "[PARSE_SYNTAX_ERROR] Syntax error at or near 'FRM'", None)
    r = propose_rewrite(sample, generate=gen, explain=exp_parse2)
    assert r["status"] == STATUS_REJECTED, r
    assert "didn't parse" in r["validation_detail"], r

    # model returns junk → rejected
    r = propose_rewrite(sample, generate=lambda p: "sorry I can't help", explain=exp_ok)
    assert r["status"] == STATUS_REJECTED, r

    # non-SELECT input → not applicable
    r = propose_rewrite({"query_text": "OPTIMIZE acme.silver.reads"}, generate=gen)
    assert r["status"] == STATUS_NA, r

    # demo rewrite
    d = demo_rewrite(sample)
    assert d["status"] == STATUS_VALIDATED and "read_date" in d["candidate_sql"], d
    print(json.dumps(d, indent=2))
    print("\nself-check OK")
