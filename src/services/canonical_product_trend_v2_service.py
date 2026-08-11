"""Canonical product-history bridge for the V22.5.6 product detail page.

V21.7 trend math remains the single trend algorithm owner. This bridge changes only
snapshot authority and read shape: canonical snapshot sets are scanned one row at a
time and reduced immediately to the requested product. A detail read must never keep
whole multi-product snapshot payloads in memory or load the same snapshot twice.

V22.5.6.1 adds one hard evidence boundary: only snapshots inside the active competition
history epoch may participate in the current trend. Canonical archive rows from earlier
evaluator/demo runs remain preserved but are never counted as current-run evidence.
The epoch authority stays inside this already-registered canonical-history bridge so no
second runtime/history service or alternate source of truth is introduced.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.canonical_product_snapshot_service import ensure_snapshot_tables
from src.services.product_trend_read_model_v217_service import (
    MAX_SNAPSHOT_SCAN,
    build_product_trend_projection,
)

CANONICAL_PRODUCT_TREND_VERSION = "22.5.6.1-canonical-v4-epoch-slim-scan"
COMPETITION_HISTORY_EPOCH_VERSION = "1.0.1"
_CACHE: Dict[tuple[str, str, str, str, str], Dict[str, Any]] = {}

EPOCH_ID_KEY = "competition_history_epoch_id"
EPOCH_STARTED_AT_KEY = "competition_history_epoch_started_at"
EPOCH_SOURCE_RESET_TOKEN_KEY = "competition_history_epoch_source_reset_token"
EPOCH_BOOTSTRAP_MODE_KEY = "competition_history_epoch_bootstrap_mode"
EPOCH_BOOTSTRAP_SNAPSHOT_KEY = "competition_history_epoch_bootstrap_snapshot_id"
SYSTEM_RESET_SCOPE_KEY = "latest_demo_reset_scope"

_METRIC_KEYS = {
    "paymentAmount",
    "gmv",
    "roi",
    "roas",
    "adSpend",
    "grossMargin",
    "organicVisitors",
    "paidVisitors",
    "visitorCount",
    "visitors",
    "clickRate",
    "conversionRate",
    "refundRate",
    "afterSalesRate",
    "refundAmount",
    "inventory",
    "availableDays",
    "sellableDays",
    "metricDate",
    "reportDate",
    "dataDate",
    "sourceDataVersions",
    "sourceDatasets",
}

_PROFILE_KEYS = {
    "objectId",
    "productId",
    "skuId",
    "storeId",
    "storeName",
    "platform",
    "title",
    "metricDate",
    "reportDate",
    "dataDate",
}

_TOP_LEVEL_KEYS = {
    "objectId",
    "id",
    "productId",
    "skuId",
    "storeId",
    "storeName",
    "platform",
    "title",
    "metricDate",
    "reportDate",
    "dataDate",
    "sourceDataVersions",
    "sourceDatasets",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ensure_runtime_meta(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
    )


def _meta_value(conn: Any, key: str) -> str | None:
    row = conn.execute("SELECT value FROM runtime_meta WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    value = row["value"]
    return str(value) if value not in {None, ""} else None


def _set_meta(conn: Any, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO runtime_meta(key,value,updated_at) VALUES (?,?,CURRENT_TIMESTAMP)",
        (key, value),
    )


def _system_reset_state(conn: Any) -> Dict[str, str | None]:
    row = conn.execute(
        "SELECT rowid,value,updated_at FROM runtime_meta WHERE key=? LIMIT 1",
        (SYSTEM_RESET_SCOPE_KEY,),
    ).fetchone()
    if not row:
        return {"scope": None, "updatedAt": None, "token": None}
    scope = str(row["value"] or "demo")
    updated_at = str(row["updated_at"] or "") or None
    token_seed = f"{row['rowid']}|{scope}|{updated_at or ''}"
    token = "sha256:" + hashlib.sha256(token_seed.encode("utf-8")).hexdigest()
    return {"scope": scope, "updatedAt": updated_at, "token": token}


def _latest_canonical_snapshot(conn: Any) -> Dict[str, str | None] | None:
    if not _table_exists(conn, "canonical_product_snapshot_sets_v1"):
        return None
    row = conn.execute(
        """
        SELECT snapshot_id,created_at
        FROM canonical_product_snapshot_sets_v1
        ORDER BY julianday(created_at) DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return {
        "snapshotId": str(row["snapshot_id"] or "") or None,
        "createdAt": str(row["created_at"] or "") or None,
    }


def _is_at_or_after(conn: Any, left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    row = conn.execute(
        "SELECT CASE WHEN julianday(?) >= julianday(?) THEN 1 ELSE 0 END AS matched",
        (left, right),
    ).fetchone()
    return bool(row and row["matched"])


def _epoch_id(*, started_at: str, reset_token: str | None, snapshot_id: str | None) -> str:
    seed = f"{started_at}|{reset_token or 'no-reset'}|{snapshot_id or 'no-snapshot'}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return f"HIST-EPOCH-{digest}"


def _persist_epoch(
    conn: Any,
    *,
    started_at: str,
    reset_token: str | None,
    bootstrap_mode: str,
    snapshot_id: str | None,
) -> Dict[str, Any]:
    epoch_id = _epoch_id(
        started_at=started_at,
        reset_token=reset_token,
        snapshot_id=snapshot_id,
    )
    _set_meta(conn, EPOCH_ID_KEY, epoch_id)
    _set_meta(conn, EPOCH_STARTED_AT_KEY, started_at)
    _set_meta(conn, EPOCH_SOURCE_RESET_TOKEN_KEY, reset_token or "")
    _set_meta(conn, EPOCH_BOOTSTRAP_MODE_KEY, bootstrap_mode)
    _set_meta(conn, EPOCH_BOOTSTRAP_SNAPSHOT_KEY, snapshot_id or "")
    conn.commit()
    return {
        "version": COMPETITION_HISTORY_EPOCH_VERSION,
        "epochId": epoch_id,
        "startedAt": started_at,
        "bootstrapMode": bootstrap_mode,
        "bootstrapSnapshotId": snapshot_id,
        "sourceResetToken": reset_token,
        "crossEpochHistoryAllowed": False,
        "archivePreserved": True,
    }


def current_competition_history_epoch() -> Dict[str, Any]:
    """Resolve the current evaluator/demo history boundary from existing runtime state.

    Demo reset already writes ``latest_demo_reset_scope`` into ``runtime_meta``. This
    bridge observes that marker and rotates its epoch without changing or weakening the
    reset contract. Legacy databases fail closed at the newest canonical snapshot.
    """
    with connect() as conn:
        _ensure_runtime_meta(conn)
        reset = _system_reset_state(conn)
        stored_epoch = _meta_value(conn, EPOCH_ID_KEY)
        stored_started_at = _meta_value(conn, EPOCH_STARTED_AT_KEY)
        stored_reset_token = _meta_value(conn, EPOCH_SOURCE_RESET_TOKEN_KEY)
        stored_mode = _meta_value(conn, EPOCH_BOOTSTRAP_MODE_KEY)
        stored_snapshot = _meta_value(conn, EPOCH_BOOTSTRAP_SNAPSHOT_KEY)

        reset_token = reset.get("token")
        reset_at = reset.get("updatedAt")
        reset_changed = bool(stored_epoch and reset_token and reset_token != stored_reset_token)

        if stored_epoch and stored_started_at and not reset_changed:
            return {
                "version": COMPETITION_HISTORY_EPOCH_VERSION,
                "epochId": stored_epoch,
                "startedAt": stored_started_at,
                "bootstrapMode": stored_mode or "persisted",
                "bootstrapSnapshotId": stored_snapshot,
                "sourceResetToken": stored_reset_token,
                "crossEpochHistoryAllowed": False,
                "archivePreserved": True,
            }

        if reset_changed and reset_at:
            return _persist_epoch(
                conn,
                started_at=str(reset_at),
                reset_token=str(reset_token) if reset_token else None,
                bootstrap_mode="system_demo_reset_boundary",
                snapshot_id=None,
            )

        latest = _latest_canonical_snapshot(conn)
        latest_at = latest.get("createdAt") if latest else None

        # Migration safety: an old reset marker cannot prove that every historical
        # canonical row created after it belongs to one evaluator run. When this epoch
        # contract is installed onto a populated DB, admit the newest snapshot only and
        # acknowledge the current reset token. Future uploads then accumulate normally.
        if latest and latest_at and not _is_at_or_after(conn, reset_at, latest_at):
            return _persist_epoch(
                conn,
                started_at=str(latest_at),
                reset_token=str(reset_token) if reset_token else None,
                bootstrap_mode="legacy_latest_snapshot_fail_closed",
                snapshot_id=str(latest.get("snapshotId") or "") or None,
            )

        if reset_at:
            return _persist_epoch(
                conn,
                started_at=str(reset_at),
                reset_token=str(reset_token) if reset_token else None,
                bootstrap_mode="system_demo_reset_boundary",
                snapshot_id=None,
            )

        if latest and latest_at:
            return _persist_epoch(
                conn,
                started_at=str(latest_at),
                reset_token=None,
                bootstrap_mode="legacy_latest_snapshot_fail_closed",
                snapshot_id=str(latest.get("snapshotId") or "") or None,
            )

        return _persist_epoch(
            conn,
            started_at=datetime.now().isoformat(),
            reset_token=None,
            bootstrap_mode="empty_runtime_start",
            snapshot_id=None,
        )


def _identity_values(item: Dict[str, Any]) -> set[str]:
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    values = {
        item.get("objectId"),
        item.get("id"),
        item.get("productId"),
        item.get("skuId"),
        profile.get("objectId"),
        profile.get("productId"),
        profile.get("skuId"),
    }
    return {_text(value) for value in values if _text(value)}


def _store_id(item: Dict[str, Any]) -> str:
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    return _text(item.get("storeId") or profile.get("storeId"))


def _matches(item: Dict[str, Any], product_id: str, store_id: str | None) -> bool:
    if store_id and _store_id(item) != _text(store_id):
        return False
    return _text(product_id) in _identity_values(item)


def _slim_product(item: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only fields consumed by the V21.7 trend algorithm.

    Product metric facts, traffic-source facts, permission payloads and detail projections
    can be very large. They are irrelevant to trend math and must not accumulate across
    the historical scan.
    """
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    slim = {key: item.get(key) for key in _TOP_LEVEL_KEYS if key in item}
    slim["profileSnapshot"] = {key: profile.get(key) for key in _PROFILE_KEYS if key in profile}
    slim["metricSnapshot"] = {key: metric.get(key) for key in _METRIC_KEYS if key in metric}
    return slim


def _history_metadata(
    epoch_started_at: str,
    limit: int = MAX_SNAPSHOT_SCAN,
) -> List[Dict[str, Any]]:
    """Read current-epoch canonical history identity without deserializing payloads."""
    ensure_snapshot_tables()
    selected = max(1, int(limit or MAX_SNAPSHOT_SCAN))
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT snapshot_id,data_version,set_snapshot_hash,created_at,updated_at
            FROM canonical_product_snapshot_sets_v1
            WHERE julianday(created_at) >= julianday(?)
            ORDER BY julianday(created_at) DESC, rowid DESC
            LIMIT ?
            """,
            (epoch_started_at, selected),
        ).fetchall()
    return [dict(row) for row in rows]


def _history_fingerprint(rows: List[Dict[str, Any]], epoch_id: str) -> str:
    material = epoch_id + "|" + "|".join(
        f"{row.get('snapshot_id')}:{row.get('set_snapshot_hash')}:{row.get('updated_at')}"
        for row in rows
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _slim_snapshot_for_product(
    metadata: Dict[str, Any],
    product_id: str,
    store_id: str | None,
) -> Dict[str, Any] | None:
    """Deserialize exactly one set, extract one product, then drop the full payload."""
    snapshot_id = _text(metadata.get("snapshot_id"))
    if not snapshot_id:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT payload FROM canonical_product_snapshot_sets_v1 WHERE snapshot_id=? LIMIT 1",
            (snapshot_id,),
        ).fetchone()
    if not row:
        return None
    payload = loads(row["payload"])
    if not isinstance(payload, dict):
        return None
    matched = None
    for item in payload.get("products") or []:
        if isinstance(item, dict) and _matches(item, product_id, store_id):
            matched = _slim_product(item)
            break
    if matched is None:
        return None
    return {
        "snapshotId": snapshot_id,
        "dataVersion": metadata.get("data_version"),
        "setSnapshotHash": metadata.get("set_snapshot_hash"),
        "createdAt": metadata.get("created_at"),
        "updatedAt": metadata.get("updated_at"),
        "products": [matched],
    }


def read_canonical_product_trend(
    product_id: str,
    *,
    store_id: str | None = None,
    user_id: str | None = None,
) -> Dict[str, Any]:
    epoch = current_competition_history_epoch()
    epoch_id = _text(epoch.get("epochId"))
    epoch_started_at = _text(epoch.get("startedAt"))
    metadata = _history_metadata(epoch_started_at)
    history_hash = _history_fingerprint(metadata, epoch_id)
    cache_key = (
        _text(product_id),
        _text(store_id),
        epoch_id,
        history_hash,
        _text(user_id),
    )
    cached = _CACHE.get(cache_key)
    if cached:
        return {**cached, "cacheState": "memory_hit"}

    snapshots: List[Dict[str, Any]] = []
    for row in metadata:
        snapshot = _slim_snapshot_for_product(row, product_id, store_id)
        if snapshot is not None:
            snapshots.append(snapshot)

    projection = build_product_trend_projection(
        snapshots,
        product_id,
        store_id=store_id,
    )
    projection = {
        **projection,
        "canonicalBridgeVersion": CANONICAL_PRODUCT_TREND_VERSION,
        "snapshotAuthority": "canonical_product_snapshot_sets_v1",
        "legacySnapshotFallbackUsed": False,
        "historyIdentityHash": history_hash,
        "historyEpochId": epoch_id,
        "historyEpochStartedAt": epoch_started_at,
        "historyEpochBootstrapMode": epoch.get("bootstrapMode"),
        "historyScope": "current_competition_runtime_epoch",
        "crossEpochHistoryAllowed": False,
        "canonicalArchivePreserved": bool(epoch.get("archivePreserved", True)),
        "historyScanMode": "epoch_metadata_then_single_row_single_product",
        "wholeSnapshotRetention": False,
    }
    if len(_CACHE) >= 256:
        _CACHE.clear()
    _CACHE[cache_key] = projection
    return {**projection, "cacheState": "fresh"}


__all__ = [
    "CANONICAL_PRODUCT_TREND_VERSION",
    "COMPETITION_HISTORY_EPOCH_VERSION",
    "current_competition_history_epoch",
    "read_canonical_product_trend",
]
