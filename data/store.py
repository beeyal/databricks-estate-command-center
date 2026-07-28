"""Writeback store — persistent user state the read-only system tables can't hold.

Backs the acknowledge/ignore capability (and, later, editable BU mapping +
budgets). This is the ``features.writeback`` tier: OFF by default, and when off
NOTHING here is touched — the app stays 100% read-only, endpoints 404, controls
hidden. Mirrors the AI/Genie optionality contract.

Backend is pluggable so the same feature works everywhere:
  * ``demo`` (USE_LIVE=false)  — an in-process dict. Resets on restart; fine for
    demos/showcases (the whole dataset is synthetic anyway).
  * ``delta`` (live, default)  — a small managed Delta table written via the
    existing SQL warehouse (the app already has one for reads). No new infra,
    no always-on Postgres cost — the writeback volume is tiny (a row per
    acknowledged alert), so Delta is a perfectly good fit here.
  * ``lakebase`` (live, opt-in) — a Lakebase (managed Postgres) instance for
    low-latency transactional writes. The RECOMMENDED production backend when a
    customer wants snappy in-app editing at scale; documented in
    docs/LAKEBASE_ASSESSMENT.md. Selected by setting ``FINOPS_LAKEBASE_*`` /
    ``writeback_backend: lakebase``. Not required — Delta is the default.

All three implement the same tiny interface (get_acks / put_ack / delete_ack),
so callers never care which backend is active.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from data import config

_log = logging.getLogger("finops.store")

# ---------------------------------------------------------------------------
# Ack record shape (shared): keyed on (source, entity_key).
#   status       : acknowledged | wont_fix | in_progress | open
#   reason       : short free text ("expected — new sources added")
#   metric_name  : which magnitude we snapshot for smart re-alert
#   metric_at_ack: value of that metric when acknowledged
#   snooze_until : ISO date or "" (no expiry)
#   updated_by / updated_at : audit
# ---------------------------------------------------------------------------

_SCHEMA_COLS = ("source", "entity_key", "status", "reason", "metric_name",
                "metric_at_ack", "snooze_until", "updated_by", "updated_at")

# In-process demo store: {(source, entity_key): record}. Module-level so it
# persists across requests within a process (resets on restart — acceptable for
# demo). Never used in live.
_DEMO: dict[tuple[str, str], dict[str, Any]] = {}

# Lakebase connection-credential cache. Opening a Lakebase connection used to do
# TWO slow round-trips EVERY time: list_database_instances() (to resolve the
# instance name from PGHOST) and generate_database_credential() (to mint the
# OAuth token). Neither changes often — the instance name never does, the token
# is valid ~1h — so cache both. This matters because the Recommendations hub
# alone calls get_acks() twice per request; without caching that's 4 API
# round-trips per hub load. _cred is (token, expires_epoch).
_lakebase_instance_name: str | None = None
# Keyed by instance name → (token, expires_epoch) so a token minted for one
# instance is never served for another (latent trap if the resolved instance
# ever changes).
_lakebase_cred: dict[str, tuple[str, float]] = {}
_CRED_SAFETY_MARGIN_S = 300  # refresh the token 5 min before it actually expires


def enabled() -> bool:
    """True only when the writeback feature is on. Every public fn no-ops (or
    returns empty) when this is false, so the tier is genuinely inert when off."""
    return bool(config.get_features().get("writeback"))


def operational() -> tuple[bool, str]:
    """Whether writeback can ACTUALLY persist in the current config — separate
    from `enabled()` (the flag). Prevents the silent-failure trap where writeback
    is on by default but, in live mode with the Delta backend, no writeback schema
    is configured (needs `gold_schema` or `FINOPS_WRITEBACK_SCHEMA`) and the app SP
    has no write grant there. Returns (ok, reason). The app surfaces this on
    /api/config so the UI can hide the ack controls with an honest note instead of
    letting acks vanish. Demo + Lakebase are always operational when enabled."""
    if not enabled():
        return (False, "writeback feature is off")
    try:
        b = _backend()
    except Exception as e:  # noqa: BLE001
        return (False, f"backend unavailable: {e}")
    if b == "demo":
        return (True, "")
    if b == "lakebase":
        # Don't just trust the flag — actually PROBE the connection + schema DDL.
        # The recreated-app trap: a new app SP inherits neither the old SP's
        # Postgres schema ownership nor grants, so put_ack() fails with
        # "permission denied for schema <x>" even though the backend is "selected".
        # Reporting operational=true here made /api/config claim writeback works
        # while every ack silently 503'd (the controls showed but never persisted).
        # A real ensure-schema probe surfaces that as writeback OFF with the reason,
        # so the UI hides the controls instead of lying. Best-effort + cheap: the
        # DDL is idempotent and short-circuited by _lakebase_ensured after the first
        # success, so this is a one-time cost per process.
        try:
            with _lakebase_conn() as c, c.cursor() as cur:
                _lakebase_ensure(cur)
                c.commit()
            return (True, "")
        except Exception as e:  # noqa: BLE001
            _reset_lakebase_ensured()
            return (False, f"lakebase writeback unavailable: {str(e)[:180]}")
    # delta backend: needs a valid writeback schema to create the ack table in.
    from data import live
    schema = os.environ.get("FINOPS_WRITEBACK_SCHEMA") or config.get_settings().get("gold_schema") or ""
    if schema and live._SCHEMA_RE.match(schema):
        return (True, "")
    return (False, "no writeback schema configured — set gold_schema or "
                   "FINOPS_WRITEBACK_SCHEMA and grant the app SP CREATE+MODIFY on it")


def _backend() -> str:
    """Which backend to use. demo ⇒ in-process; live ⇒ delta unless a Lakebase
    is available. Lakebase is auto-selected — no customer config — when the app
    has a bound database resource (the bundle injects PGINSTANCE + the PG* vars);
    it's also selected by the manual escape hatches (FINOPS_LAKEBASE_INSTANCE /
    _HOST / _DSN, or writeback_backend:lakebase) for non-bundle deploys. Anything
    else ⇒ delta — so a deployment WITHOUT Lakebase degrades cleanly."""
    from data.config import get_settings
    s = get_settings()
    if not s.get("use_live"):
        return "demo"
    if (os.environ.get("PGHOST")                # bundle-native binding (preferred)
            or os.environ.get("FINOPS_LAKEBASE_INSTANCE")
            or os.environ.get("FINOPS_LAKEBASE_HOST")
            or os.environ.get("FINOPS_LAKEBASE_DSN")
            or s.get("writeback_backend") == "lakebase"):
        return "lakebase"
    return "delta"


# ---------------------------------------------------------------------------
# Public interface — the only thing callers use.
# ---------------------------------------------------------------------------

def get_acks(warehouse_id: str = "", source: str | None = None) -> dict[str, dict[str, Any]]:
    """Return the ack records. For a SPECIFIC source, keyed by entity_key (the
    hot path — _apply_acks looks up by the row's bare entity_key). For ALL
    sources (source=None), keyed by the composite "source::entity_key" so records
    are NOT collapsed when an entity_key coincides across two sources. Empty when
    the feature is off or the store is unreachable — callers then just show
    everything as 'open' (graceful degradation, never an error)."""
    if not enabled():
        return {}
    try:
        b = _backend()
        rows = _DEMO_rows(source) if b == "demo" else (
            _delta_rows(warehouse_id, source) if b == "delta" else _lakebase_rows(source))
        if source is None:
            return {f"{r.get('source','')}::{r['entity_key']}": r for r in rows}
        return {r["entity_key"]: r for r in rows}
    except Exception as e:  # noqa: BLE001 — store issues never break the read path
        # Log the real reason (a swallowed permission/schema error here is why an
        # ack silently won't persist) but still degrade gracefully to "no acks".
        _reset_lakebase_ensured()  # a bad connection may mean the DDL must re-run
        _log.warning("writeback get_acks failed (%s backend): %s", _backend_safe(), e)
        return {}


def put_ack(warehouse_id: str, rec: dict[str, Any]) -> bool:
    """Upsert one ack record. Returns True on success, False if disabled/failed
    (caller surfaces a soft 'couldn't save' rather than erroring the page)."""
    if not enabled():
        return False
    rec = _normalise(rec)
    try:
        b = _backend()
        if b == "demo":
            _DEMO[(rec["source"], rec["entity_key"])] = rec
        elif b == "delta":
            _delta_put(warehouse_id, rec)
        else:
            _lakebase_put(rec)
        return True
    except Exception as e:  # noqa: BLE001
        _reset_lakebase_ensured()
        _log.warning("writeback put_ack failed (%s backend): %s", _backend_safe(), e)
        return False


def delete_ack(warehouse_id: str, source: str, entity_key: str) -> bool:
    """Reopen (remove) one ack. Returns True on success."""
    if not enabled():
        return False
    try:
        b = _backend()
        if b == "demo":
            _DEMO.pop((source, entity_key), None)
        elif b == "delta":
            _delta_delete(warehouse_id, source, entity_key)
        else:
            _lakebase_delete(source, entity_key)
        return True
    except Exception as e:  # noqa: BLE001
        _reset_lakebase_ensured()
        _log.warning("writeback delete_ack failed (%s backend): %s", _backend_safe(), e)
        return False


def _backend_safe() -> str:
    """_backend() but never raises — for use inside exception logging."""
    try:
        return _backend()
    except Exception:  # noqa: BLE001
        return "unknown"


def _normalise(rec: dict[str, Any]) -> dict[str, Any]:
    from datetime import datetime, timezone
    return {
        "source": str(rec.get("source") or ""),
        "entity_key": str(rec.get("entity_key") or ""),
        "status": str(rec.get("status") or "acknowledged"),
        "reason": str(rec.get("reason") or ""),
        "metric_name": str(rec.get("metric_name") or ""),
        "metric_at_ack": float(rec.get("metric_at_ack") or 0.0),
        "snooze_until": str(rec.get("snooze_until") or ""),
        "updated_by": str(rec.get("updated_by") or "app"),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# demo backend
# ---------------------------------------------------------------------------

def _DEMO_rows(source: str | None) -> list[dict[str, Any]]:
    return [dict(r) for (s, _k), r in _DEMO.items() if source is None or s == source]


# ---------------------------------------------------------------------------
# delta backend — a small managed Delta table via the read warehouse.
# ---------------------------------------------------------------------------

def _writeback_schema() -> str:
    """The catalog.schema for the ack table: FINOPS_WRITEBACK_SCHEMA, else the
    gold schema. Validated as a 2-part identifier before any interpolation."""
    from data import live
    schema = os.environ.get("FINOPS_WRITEBACK_SCHEMA") or config.get_settings().get("gold_schema") or ""
    if not schema or not live._SCHEMA_RE.match(schema):
        raise RuntimeError("writeback: no valid schema (set FINOPS_WRITEBACK_SCHEMA or gold_schema)")
    return schema


def _ack_table() -> str:
    """FQN of the ack table, in the writeback schema."""
    return f"{_writeback_schema()}.finops_ack_status"


def _delta_ensure(warehouse_id: str) -> None:
    """Create the writeback schema (best-effort) + the ack table, and enable
    Predictive Optimization + auto Liquid Clustering on it — so the customer
    doesn't have to pre-create/grant a schema, and the app's own table follows
    the very hygiene it recommends (dog-fooding PO/LC). Best-effort throughout:
    if the app SP can't CREATE the schema (locked-down catalog), we fall through
    and the existing/ungranted-schema path surfaces via the normal fail-soft."""
    from data import live
    from data import gold

    schema = _writeback_schema()  # validated catalog.schema
    # Auto-create the schema so no manual "CREATE SCHEMA + grant" step is needed
    # (mirrors the Lakebase backend, which self-creates its `finops` schema).
    try:
        live._run(warehouse_id, f"CREATE SCHEMA IF NOT EXISTS {schema}",
                  "writeback: ensure schema")
    except Exception:  # noqa: BLE001 — schema may pre-exist / be admin-managed
        pass
    # Reuse gold's managed-Delta helper: creates the table, then best-effort
    # enables PO + CLUSTER BY AUTO (guarded against the metastore hard-fail).
    gold._ensure_managed_delta(
        warehouse_id, _ack_table(),
        "source STRING, entity_key STRING, status STRING, reason STRING, "
        "metric_name STRING, metric_at_ack DOUBLE, snooze_until STRING, "
        "updated_by STRING, updated_at STRING")


def _sql_str(s: str) -> str:
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _delta_rows(warehouse_id: str, source: str | None) -> list[dict[str, Any]]:
    from data import live
    _delta_ensure(warehouse_id)
    where = f" WHERE source={_sql_str(source)}" if source else ""
    rows = live._run(warehouse_id, f"SELECT * FROM {_ack_table()}{where}", "writeback: read acks")
    out = []
    for r in rows:
        out.append({
            "source": str(r.get("source") or ""), "entity_key": str(r.get("entity_key") or ""),
            "status": str(r.get("status") or ""), "reason": str(r.get("reason") or ""),
            "metric_name": str(r.get("metric_name") or ""), "metric_at_ack": live._f(r.get("metric_at_ack")),
            "snooze_until": str(r.get("snooze_until") or ""), "updated_by": str(r.get("updated_by") or ""),
            "updated_at": str(r.get("updated_at") or ""),
        })
    return out


def _delta_put(warehouse_id: str, rec: dict[str, Any]) -> None:
    from data import live
    _delta_ensure(warehouse_id)
    t = _ack_table()
    # MERGE upsert on (source, entity_key).
    live._run(warehouse_id,
        f"MERGE INTO {t} tgt USING (SELECT "
        f"{_sql_str(rec['source'])} source, {_sql_str(rec['entity_key'])} entity_key, "
        f"{_sql_str(rec['status'])} status, {_sql_str(rec['reason'])} reason, "
        f"{_sql_str(rec['metric_name'])} metric_name, {rec['metric_at_ack']} metric_at_ack, "
        f"{_sql_str(rec['snooze_until'])} snooze_until, {_sql_str(rec['updated_by'])} updated_by, "
        f"{_sql_str(rec['updated_at'])} updated_at) src "
        "ON tgt.source=src.source AND tgt.entity_key=src.entity_key "
        "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *",
        "writeback: upsert ack")


def _delta_delete(warehouse_id: str, source: str, entity_key: str) -> None:
    from data import live
    _delta_ensure(warehouse_id)
    live._run(warehouse_id,
        f"DELETE FROM {_ack_table()} WHERE source={_sql_str(source)} AND entity_key={_sql_str(entity_key)}",
        "writeback: delete ack")


# ---------------------------------------------------------------------------
# lakebase backend — production Postgres (opt-in). Thin psycopg wrapper; imported
# lazily so the dependency is only needed when this backend is actually selected.
# ---------------------------------------------------------------------------

def _instance_name_for_host(w, pg_host: str) -> str:
    """Resolve a Lakebase instance NAME from its injected PGHOST (read-write DNS).
    The Apps binding injects PGHOST but not the instance name, and
    generate_database_credential needs the name — so match on DNS. An explicit
    FINOPS_LAKEBASE_INSTANCE overrides (skips the lookup). CACHED for the process
    lifetime — the instance name never changes, so we do the list_database_
    instances() API call at most once."""
    global _lakebase_instance_name
    if _lakebase_instance_name:
        return _lakebase_instance_name
    override = os.environ.get("FINOPS_LAKEBASE_INSTANCE")
    if override:
        _lakebase_instance_name = override
        return override
    for inst in w.database.list_database_instances():
        if pg_host in (getattr(inst, "read_write_dns", None), getattr(inst, "read_only_dns", None)):
            _lakebase_instance_name = inst.name
            return inst.name
    raise RuntimeError(f"writeback: no Lakebase instance matches PGHOST={pg_host}")


def _lakebase_token(w, instance: str) -> str:
    """A Lakebase OAuth token for `instance`, cached PER INSTANCE until shortly
    before it expires. Minting a token is an API round-trip (~1s); the token is
    valid ~1h, so reuse it across requests instead of minting one per connection."""
    import time as _t
    now = _t.time()
    hit = _lakebase_cred.get(instance)
    if hit and hit[1] - _CRED_SAFETY_MARGIN_S > now:
        return hit[0]
    cred = w.database.generate_database_credential(instance_names=[instance])
    # cred.expiration_time is an ISO datetime; fall back to a conservative 45-min
    # TTL if we can't parse it, so we still refresh well within the ~1h validity.
    exp = now + 2700.0
    try:
        from datetime import datetime, timezone
        et = getattr(cred, "expiration_time", None)
        if et:
            dt = datetime.fromisoformat(str(et).replace("Z", "+00:00"))
            # A naive datetime (no tz) would be read as LOCAL by .timestamp() and
            # skew expiry on a non-UTC container — treat it as UTC.
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            exp = dt.timestamp()
    except Exception:  # noqa: BLE001
        pass
    _lakebase_cred[instance] = (cred.token, exp)
    return cred.token


def _lakebase_conn():
    """Open a psycopg connection to the Lakebase instance.

    Three auth paths, in priority order:
      1. BUNDLE-NATIVE binding (the default — ZERO customer config): when the
         bundle binds a `database` resource to the app, the Apps runtime injects
         PGHOST/PGPORT/PGDATABASE/PGUSER/PGSSLMODE (verified on the app runtime —
         note it injects NEITHER a PGINSTANCE nor a PGPASSWORD). The app connects
         as PGUSER (the app service principal, whose Postgres role the binding
         auto-creates) and mints its own short-lived OAuth token as the password
         via w.database.generate_database_credential(). That call needs the
         instance NAME, which isn't injected — we recover it by matching PGHOST
         (the instance's read-write DNS) against the instance list. Nothing for
         the customer to set: no CLI, no env var, no manual role.
      2. MANUAL instance (FINOPS_LAKEBASE_INSTANCE): same SDK-credential flow for
         a non-bundle / UI-created app that isn't bound. Host resolved from the
         instance (or FINOPS_LAKEBASE_HOST); connects as the caller identity.
      3. Static DSN (FINOPS_LAKEBASE_DSN): a full libpq connection string, for
         environments that provision their own Postgres role/password.
    """
    import psycopg  # type: ignore  # only needed when lakebase backend is on

    dsn = os.environ.get("FINOPS_LAKEBASE_DSN")
    if dsn:
        return psycopg.connect(dsn)

    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()

    # Path 1: bundle-injected binding vars (PGHOST present ⇒ bound resource).
    pg_host = os.environ.get("PGHOST")
    if pg_host:
        instance = _instance_name_for_host(w, pg_host)  # cached after first call
        token = _lakebase_token(w, instance)            # cached until ~expiry
        return psycopg.connect(
            host=pg_host,
            port=int(os.environ.get("PGPORT", "5432")),
            dbname=os.environ.get("PGDATABASE", "databricks_postgres"),
            user=os.environ["PGUSER"],          # SP client id == auto-created role
            password=token,                     # short-lived OAuth token, not env
            sslmode=os.environ.get("PGSSLMODE", "require"))

    # Path 2: manual instance name (non-bundle deploy).
    instance = os.environ.get("FINOPS_LAKEBASE_INSTANCE")
    if not instance:
        raise RuntimeError("writeback: lakebase backend selected but none of "
                           "PGINSTANCE (bundle binding), FINOPS_LAKEBASE_INSTANCE "
                           "or FINOPS_LAKEBASE_DSN is set")
    host = os.environ.get("FINOPS_LAKEBASE_HOST")
    if not host:
        host = w.database.get_database_instance(name=instance).read_write_dns
    cred = w.database.generate_database_credential(instance_names=[instance])
    user = os.environ.get("FINOPS_LAKEBASE_USER") or w.current_user.me().user_name
    dbname = os.environ.get("FINOPS_LAKEBASE_DB") or "databricks_postgres"
    return psycopg.connect(host=host, port=5432, dbname=dbname,
                           user=user, password=cred.token, sslmode="require")


# The app writes into its OWN Postgres schema, not `public`. Under the bundle
# binding the app's SP role can CONNECT + has CREATE on the database, but NOT
# CREATE on `public` (that's owned by databricks_superuser). Creating a dedicated
# schema — which the SP role then owns outright — is what keeps writeback working
# with ZERO manual grants. Overridable via FINOPS_LAKEBASE_SCHEMA.
# The name is f-string-interpolated into DDL, so validate it as a plain Postgres
# identifier (letters/digits/underscore, not starting with a digit) and fall back
# to the safe default if it isn't — no untrusted value ever reaches the SQL, and
# this mirrors the Delta path's _SCHEMA_RE validation.
_PG_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PG_SCHEMA_RAW = os.environ.get("FINOPS_LAKEBASE_SCHEMA") or "finops"
_PG_SCHEMA = _PG_SCHEMA_RAW if _PG_IDENT_RE.match(_PG_SCHEMA_RAW) else "finops"
_PG_TABLE = f"{_PG_SCHEMA}.finops_ack_status"


# The schema + ack table are created once per process, then this flag short-
# circuits the idempotent DDL so it doesn't run 2 statements on EVERY get/put/
# delete (that DDL was a measured ~per-request tax on every ack read). Any
# connection error resets it via _lakebase_ensured=False so a reconnect re-ensures.
_lakebase_ensured = False


def _reset_lakebase_ensured() -> None:
    """Clear the cached connection STATE so the next Lakebase call fully re-resolves
    the instance, re-mints a token, and re-creates the schema/tables. Called after
    any swallowed connection error, in case the failure means the instance was
    rotated or a still-within-TTL token was revoked server-side. Must also clear the
    credential + instance-name caches — otherwise the next call re-uses the SAME
    stale token and fails again until the ~45min TTL elapses, leaving writeback dead."""
    global _lakebase_ensured, _lakebase_cache_ensured, _lakebase_instance_name
    _lakebase_ensured = False
    _lakebase_cache_ensured = False
    _lakebase_instance_name = None
    _lakebase_cred.clear()


def _lakebase_ensure(cur) -> None:
    global _lakebase_ensured
    if _lakebase_ensured:
        return
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_PG_SCHEMA}")
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_PG_TABLE} ("
        "source text, entity_key text, status text, reason text, metric_name text, "
        "metric_at_ack double precision, snooze_until text, updated_by text, "
        "updated_at timestamptz, PRIMARY KEY (source, entity_key))")
    _lakebase_ensured = True


def _lakebase_rows(source: str | None) -> list[dict[str, Any]]:
    with _lakebase_conn() as c, c.cursor() as cur:
        _lakebase_ensure(cur)
        if source:
            cur.execute("SELECT " + ",".join(_SCHEMA_COLS) + f" FROM {_PG_TABLE} WHERE source=%s", (source,))
        else:
            cur.execute("SELECT " + ",".join(_SCHEMA_COLS) + f" FROM {_PG_TABLE}")
        return [dict(zip(_SCHEMA_COLS, row)) for row in cur.fetchall()]


def _lakebase_put(rec: dict[str, Any]) -> None:
    with _lakebase_conn() as c, c.cursor() as cur:
        _lakebase_ensure(cur)
        cur.execute(
            f"INSERT INTO {_PG_TABLE} (" + ",".join(_SCHEMA_COLS) + ") "
            "VALUES (%(source)s,%(entity_key)s,%(status)s,%(reason)s,%(metric_name)s,"
            "%(metric_at_ack)s,%(snooze_until)s,%(updated_by)s,%(updated_at)s) "
            "ON CONFLICT (source, entity_key) DO UPDATE SET "
            "status=EXCLUDED.status, reason=EXCLUDED.reason, metric_name=EXCLUDED.metric_name, "
            "metric_at_ack=EXCLUDED.metric_at_ack, snooze_until=EXCLUDED.snooze_until, "
            "updated_by=EXCLUDED.updated_by, updated_at=EXCLUDED.updated_at", rec)
        c.commit()


def _lakebase_delete(source: str, entity_key: str) -> None:
    with _lakebase_conn() as c, c.cursor() as cur:
        _lakebase_ensure(cur)
        cur.execute(f"DELETE FROM {_PG_TABLE} WHERE source=%s AND entity_key=%s", (source, entity_key))
        c.commit()


# ---------------------------------------------------------------------------
# Persistent payload cache (Lakebase). Separate from the ack store: a small
# key→JSON table that lets the heavy, non-gold live endpoints (Warehouse
# Monitor, Cluster Efficiency, Governance, rec-hub, …) survive an app RESTART.
# The in-process TTL cache in app.py dies on every restart/redeploy, so without
# this each restart re-pays the 60-180s cold build. Backed by the SAME Lakebase
# instance already used for writeback (no new infra), and entirely OPTIONAL —
# every call fails soft to None so a Delta/demo/no-Lakebase deploy just keeps
# using the in-memory cache. Values are JSON text; freshness is judged by the
# caller against the returned age.
# ---------------------------------------------------------------------------

_PG_CACHE_TABLE = f"{_PG_SCHEMA}.finops_payload_cache"


def cache_available() -> bool:
    """True only when the Lakebase backend is actually usable as a cache tier.
    Gated on writeback being enabled AND the lakebase backend being selected —
    otherwise the payload cache is a no-op (the in-memory TTL cache stands in)."""
    try:
        return enabled() and _backend() == "lakebase"
    except Exception:  # noqa: BLE001
        return False


_lakebase_cache_ensured = False


def _lakebase_cache_ensure(cur) -> None:
    global _lakebase_cache_ensured
    if _lakebase_cache_ensured:
        return
    cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_PG_SCHEMA}")
    cur.execute(
        f"CREATE TABLE IF NOT EXISTS {_PG_CACHE_TABLE} ("
        "cache_key text PRIMARY KEY, payload jsonb, updated_at timestamptz)")
    _lakebase_cache_ensured = True


def cache_get(key: str) -> tuple[Any, float] | None:
    """Return (payload, age_seconds) for `key`, or None on miss / unavailable /
    any error. age_seconds lets the caller apply its own TTL and decide whether
    to serve-and-revalidate. Never raises — the cache is best-effort."""
    if not cache_available():
        return None
    try:
        import json
        with _lakebase_conn() as c, c.cursor() as cur:
            _lakebase_cache_ensure(cur)
            cur.execute(
                f"SELECT payload, EXTRACT(EPOCH FROM (now() - updated_at)) "
                f"FROM {_PG_CACHE_TABLE} WHERE cache_key=%s", (key,))
            row = cur.fetchone()
            if not row or row[0] is None:
                return None
            payload = row[0] if not isinstance(row[0], str) else json.loads(row[0])
            age = float(row[1]) if row[1] is not None else 0.0
            return payload, age
    except Exception as e:  # noqa: BLE001
        _reset_lakebase_ensured()
        _log.warning("payload cache_get failed (%s): %s", key, e)
        return None


def cache_put(key: str, payload: Any) -> bool:
    """Upsert `payload` under `key` with a now() timestamp. Best-effort: returns
    False (never raises) so a cache write failure can't break a live response."""
    if not cache_available():
        return False
    try:
        import json
        with _lakebase_conn() as c, c.cursor() as cur:
            _lakebase_cache_ensure(cur)
            cur.execute(
                f"INSERT INTO {_PG_CACHE_TABLE} (cache_key, payload, updated_at) "
                "VALUES (%s, %s::jsonb, now()) "
                "ON CONFLICT (cache_key) DO UPDATE SET "
                "payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at",
                (key, json.dumps(payload)))
            c.commit()
        return True
    except Exception as e:  # noqa: BLE001
        _reset_lakebase_ensured()
        _log.warning("payload cache_put failed (%s): %s", key, e)
        return False


# ---------------------------------------------------------------------------
# Smart re-alert — shared overlay logic (pure, unit-testable).
# ---------------------------------------------------------------------------

_DEFAULT_TOLERANCE = 0.25  # reopen when the metric is >25% worse than at ack time
# For items acknowledged at a ZERO (or absent) baseline, a percentage can't be
# computed — reopen instead when the current magnitude climbs past this small
# absolute floor, so a "spend spike, $0 est. savings"-type ack still re-surfaces
# if it later becomes material rather than being suppressed forever.
_ZERO_BASE_REOPEN_FLOOR = 1000.0  # dollars (metrics here are $/mo or GB — see note)


def effective_status(ack: dict[str, Any] | None, current_metric: float,
                     tolerance: float = _DEFAULT_TOLERANCE, today_iso: str = "",
                     zero_base_floor: float = _ZERO_BASE_REOPEN_FLOOR) -> dict[str, Any]:
    """Given an ack record (or None) and the finding's CURRENT magnitude, decide
    what the UI should do. Returns {status, reopened, hidden, reason?, ...}.

    Rules:
      * no ack               → open, visible.
      * snooze expired       → reopen (open), visible.
      * metric worsened past tolerance vs snapshot → reopen with reopened=True,
        visible + badged (the safety signal).
      * otherwise (acknowledged/wont_fix within tolerance) → hidden from default.
      * in_progress          → visible but badged (kept in view, not re-triaged).
    """
    if not ack:
        return {"status": "open", "reopened": False, "hidden": False}
    status = ack.get("status", "open")
    # Snooze expiry. ISO dates compare correctly as strings; but guard against a
    # MALFORMED snooze value — a bad string would never satisfy `>=` a real date,
    # silently hiding the alert forever. A cost-alert tool must fail toward
    # SHOWING, so treat an unparseable snooze as expired (reopen), not eternal.
    snooze = ack.get("snooze_until") or ""
    if snooze:
        # Default today_iso to today's date when a caller omits it — otherwise a
        # snooze would never expire (fail toward hiding forever), the opposite of
        # the fail-toward-showing contract below.
        if not today_iso:
            from datetime import date as _date
            today_iso = _date.today().isoformat()
        import re as _re
        well_formed = bool(_re.match(r"^\d{4}-\d{2}-\d{2}", snooze))
        if not well_formed or today_iso >= snooze:
            return {"status": "open", "reopened": True, "hidden": False,
                    "reopen_reason": "snooze expired", "prev_reason": ack.get("reason", "")}
    if status == "in_progress":
        return {"status": "in_progress", "reopened": False, "hidden": False, "reason": ack.get("reason", "")}
    if status in ("acknowledged", "wont_fix"):
        base = float(ack.get("metric_at_ack") or 0.0)
        if base > 0:
            # Normal case: reopen on a proportional worsening past tolerance.
            if current_metric > base * (1.0 + tolerance):
                pct = (current_metric - base) / base
                return {"status": "open", "reopened": True, "hidden": False,
                        "reopen_reason": f"{round(pct*100)}% worse than when acknowledged",
                        "metric_at_ack": base, "prev_reason": ack.get("reason", "")}
        else:
            # Zero/absent baseline (e.g. a spend-spike item with $0 est. savings):
            # a percentage is undefined, so reopen on an ABSOLUTE threshold instead
            # rather than suppressing the alert forever no matter how large it grows.
            if current_metric > zero_base_floor:
                return {"status": "open", "reopened": True, "hidden": False,
                        "reopen_reason": "grew materially since acknowledged",
                        "metric_at_ack": base, "prev_reason": ack.get("reason", "")}
        return {"status": status, "reopened": False, "hidden": True, "reason": ack.get("reason", "")}
    return {"status": status, "reopened": False, "hidden": False}


if __name__ == "__main__":
    print("=== store.effective_status self-check ===")
    # no ack → open/visible
    assert effective_status(None, 100.0) == {"status": "open", "reopened": False, "hidden": False}
    # acknowledged, within tolerance → hidden
    ack = {"status": "acknowledged", "reason": "expected", "metric_at_ack": 100.0}
    r = effective_status(ack, 110.0)  # +10% < 25%
    assert r["hidden"] and not r["reopened"], r
    # acknowledged, worsened past tolerance → reopened, visible, badged
    r = effective_status(ack, 161.0)  # +61%
    assert r["reopened"] and not r["hidden"] and "61%" in r["reopen_reason"], r
    # snooze expired → reopened
    r = effective_status({"status": "acknowledged", "snooze_until": "2026-07-01", "metric_at_ack": 100}, 100.0, today_iso="2026-07-22")
    assert r["reopened"] and "snooze" in r["reopen_reason"], r
    # snooze NOT yet expired → still hidden
    r = effective_status({"status": "acknowledged", "snooze_until": "2026-12-01", "metric_at_ack": 100}, 100.0, today_iso="2026-07-22")
    assert r["hidden"] and not r["reopened"], r
    # MALFORMED snooze → fail toward SHOWING (reopen), never hide forever
    r = effective_status({"status": "acknowledged", "snooze_until": "not-a-date", "metric_at_ack": 100}, 100.0, today_iso="2026-07-22")
    assert r["reopened"] and not r["hidden"], r
    # in_progress → visible, not hidden
    r = effective_status({"status": "in_progress"}, 100.0)
    assert not r["hidden"] and r["status"] == "in_progress", r
    # wont_fix within tolerance → hidden
    r = effective_status({"status": "wont_fix", "metric_at_ack": 100}, 105.0)
    assert r["hidden"], r
    # zero baseline, still small → hidden (no divide-by-zero, no false reopen)
    r = effective_status({"status": "acknowledged", "metric_at_ack": 0.0}, 500.0, zero_base_floor=1000.0)
    assert r["hidden"] and not r["reopened"], r
    # zero baseline, grew past the absolute floor → reopened (the fix: was hidden forever)
    r = effective_status({"status": "acknowledged", "metric_at_ack": 0.0}, 5000.0, zero_base_floor=1000.0)
    assert r["reopened"] and not r["hidden"] and "materially" in r["reopen_reason"], r

    # operational(): demo is always operational when the flag is on; feature-off
    # is not operational. (Live+delta+no-schema is exercised via app config, not
    # here, since it depends on env — this covers the pure-logic branches.)
    import os as _os
    _os.environ["FINOPS_FEATURE_WRITEBACK"] = "true"
    config.get_features.cache_clear() if hasattr(config.get_features, "cache_clear") else None
    ok, reason = operational()
    assert ok and reason == "", (ok, reason)  # demo mode (use_live false) → ok
    print("self-check OK")
