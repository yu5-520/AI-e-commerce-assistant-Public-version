"""Canonical product-history bridge for the V22.5.6 product detail page.

V21.7 trend math remains the single trend algorithm owner. This bridge changes only
snapshot authority and read shape: canonical snapshot sets are scanned one row at a
time and reduced immediately to the requested product. A detail read must never keep
whole multi-product snapshot payloads in memory or load the same snapshot twice.

V22.5.6.1 adds one hard evidence boundary: only snapshots inside the active competition
history epoch may participate in the current trend. Canonical archive rows from earlier
evaluator/demo runs remain preserved but are never counted as current-run evidence.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.canonical_product_snapshot_service import ensure_snapshot_tables
from src.services.competition_history_epoch_service import current_competition_history_epoch
from src.services.product_trend_read_model_v217_service import (
    MAX_SNAPSHOT_SCAN,
    build_product_trend_projection,
)

CANONICAL_PRODUCT_TREND_VERSION = "22.5.6.1-canonical-v4-epoch-slim-scan"
_CACHE: Dict[tuple[str, str, str, str, str], Dict[str, Any]] = {}

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
    # Do not retain the multi-product payload beyond this function.
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
    "read_canonical_product_trend",
]
