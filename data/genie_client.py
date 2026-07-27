"""Databricks Genie Conversation API client (SPEC §11; live mode).

Sends a question to a Genie Space curated over the cost/usage/access tables and
returns the text answer. The space ID comes from config (``genie_space_id`` /
``GENIE_SPACE_ID`` env).

If the space is unavailable (no ID or no creds), the caller (app.py) falls back
to the demo answer engine.

Conversation flow:
  1. POST /api/2.0/genie/spaces/{space_id}/start-conversation
  2. Poll GET  .../conversations/{conv_id}/messages/{msg_id} until COMPLETED.
  3. Extract the text attachment from the completed message.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
from typing import Any
import time
import urllib.error
import urllib.request

from data.config import get_settings

logger = logging.getLogger(__name__)

_POLL_INTERVAL = 2.0
# Genie cost questions generate SQL + run it on the warehouse, which regularly
# takes >30s — a short timeout made real questions fall back to the "couldn't
# answer" message. Allow up to 90s (still bounded so the request can't hang).
_POLL_TIMEOUT = 90.0


def _get_credentials() -> tuple[str, str]:
    host = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
    if host and not host.startswith("http"):
        host = f"https://{host}"
    token = os.environ.get("DATABRICKS_TOKEN", "")
    if host and token:
        return host, token
    try:
        from databricks.sdk import WorkspaceClient  # type: ignore

        w = WorkspaceClient()
        # In a deployed Databricks App the runtime injects client-id/secret, not
        # DATABRICKS_HOST/TOKEN — so resolve BOTH from the SDK config (host from
        # w.config.host, a bearer from the OAuth credential chain). Without the
        # host fallback, is_available() was false in the App and Genie never ran.
        if not host:
            host = str(getattr(w.config, "host", "") or "").rstrip("/")
        bearer = w.config.authenticate().get("Authorization", "")
        sdk_token = bearer[len("Bearer "):].strip() if bearer.startswith("Bearer ") else ""
        return host, (token or sdk_token)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Databricks SDK auth unavailable: %s", exc)
    return host, token


# Memoise ONLY a successfully-resolved space id. A plain @lru_cache would cache
# an empty "" from a request that lands before the async self-provision finishes
# — wedging Ask Genie "not configured" for the whole process even after the Space
# exists. Caching only non-empty lets a later call succeed.
_SPACE_ID_CACHE: str = ""


def _provisioned_space_id() -> str:
    """The auto-created FinOps Genie Space id, so Ask Genie works out-of-the-box
    with no manual genie_space_id. Two discovery paths:
      1. the id the provision step persisted in <ingest_schema>.finops_admin
         (key 'provision'); and if that isn't found (the Delta round-trip can
         fail silently on some runtimes),
      2. the newest Space TITLED 'FinOps Command Center' via the list API —
         the same title ensure_genie_space creates + dedupes on.
    Only a non-empty result is cached (see _SPACE_ID_CACHE)."""
    global _SPACE_ID_CACHE
    if _SPACE_ID_CACHE:
        return _SPACE_ID_CACHE
    s = get_settings()
    wh = str(s.get("warehouse_id") or ""); schema = str(s.get("ingest_schema") or "")
    # (1) persisted provision record
    if wh and schema:
        try:
            from data import live

            got = live.read_admin(wh, schema, "provision")
            sid = str(((got["payload"] or {}).get("genie") or {}).get("space_id") or "") if got else ""
            if sid:
                _SPACE_ID_CACHE = sid
                return sid
        except Exception:  # noqa: BLE001
            pass
    # (2) discover by title (matches provision.GENIE_TITLE / ensure_genie_space)
    try:
        from databricks.sdk import WorkspaceClient

        w = WorkspaceClient()
        # Prefix match: the Genie create API appends a timestamp, so titles look
        # like "FinOps Command Center 2026-07-21 08:03:40".
        found = [sp for sp in _all_spaces(w)
                 if (getattr(sp, "title", "") or "").startswith("FinOps Command Center") and getattr(sp, "space_id", None)]
        if found:
            _SPACE_ID_CACHE = max(found, key=lambda sp: sp.space_id).space_id
            return _SPACE_ID_CACHE
    except Exception:  # noqa: BLE001
        pass
    return ""


def _all_spaces(w: Any) -> list:
    """List all Genie spaces across pages. w.genie.list_spaces() returns a
    GenieListSpacesResponse ({spaces, next_page_token}) — NOT an iterable — so
    read .spaces and follow next_page_token."""
    out: list = []
    token = None
    for _ in range(50):  # hard page cap
        resp = w.genie.list_spaces(page_token=token) if token else w.genie.list_spaces()
        out.extend(getattr(resp, "spaces", None) or [])
        token = getattr(resp, "next_page_token", None)
        if not token:
            break
    return out


def _space_id() -> str:
    return str(get_settings().get("genie_space_id") or "") or _provisioned_space_id()


def is_available() -> bool:
    """True only when a Genie space is configured and creds exist. Any false ⇒
    caller uses the demo answer engine."""
    host, token = _get_credentials()
    return bool(host and token and _space_id())


def _api(host: str, token: str, method: str, path: str, body: dict | None = None) -> dict:
    url = f"{host}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
        return json.loads(resp.read().decode())


def ask(question: str) -> str | None:
    """Send *question* to the Genie space; return the answer text or None."""
    if not is_available():
        return None
    host, token = _get_credentials()
    space_id = _space_id()
    try:
        started = _api(host, token, "POST",
                       f"/api/2.0/genie/spaces/{space_id}/start-conversation",
                       {"content": question})
        conv_id = started.get("conversation_id") or started.get("conversation", {}).get("conversation_id")
        msg_id = started.get("message_id") or started.get("message", {}).get("id")
        if not conv_id or not msg_id:
            logger.warning("Genie start-conversation missing IDs: %s", started)
            return None
        deadline = time.time() + _POLL_TIMEOUT
        while time.time() < deadline:
            time.sleep(_POLL_INTERVAL)
            msg = _api(host, token, "GET",
                       f"/api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages/{msg_id}")
            status = msg.get("status", "")
            if status == "COMPLETED":
                return _extract_text(msg)
            if status in ("FAILED", "CANCELLED"):
                logger.warning("Genie message status: %s", status)
                return None
        logger.warning("Genie query timed out after %.0fs", _POLL_TIMEOUT)
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Genie API error: %s", exc)
        return None


def _extract_text(message: dict) -> str | None:
    """Pull the natural-language answer out of a completed Genie message.

    A response has MULTIPLE attachments (query / suggested_questions / text /
    viz) in arbitrary order. Prefer the `text` answer across ALL of them FIRST;
    only if there's no text attachment fall back to a query block's description.
    (The old code returned on the first attachment that had text-OR-query, so a
    leading `query` attachment shadowed the real `text` answer → None/SQL.)"""
    attachments = message.get("attachments", []) or []
    # 1) any text attachment
    for a in attachments:
        tb = a.get("text") or {}
        content = tb.get("content") or tb.get("body") or tb.get("text")
        if content:
            return content
    # 2) fall back to a query block's prose description (not the raw SQL)
    for a in attachments:
        qb = a.get("query") or {}
        desc = qb.get("description")
        if desc:
            return desc
    return message.get("content") or None
