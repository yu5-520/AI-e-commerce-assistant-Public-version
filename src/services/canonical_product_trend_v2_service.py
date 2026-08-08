"""Canonical product-history bridge for the V22.5.6 product detail page.

V21.7 trend math remains the single trend algorithm owner. This bridge changes only
snapshot authority: after the canonical-product migration, history must come from
``canonical_product_snapshot_sets_v1`` instead of the legacy
``system_product_snapshots_v14`` table.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.services.canonical_product_snapshot_service import (
    get_product_snapshot,
    product_snapshot_history,
)
from src.services.product_trend_read_model_v217_service import (
    MAX_SNAPSHOT_SCAN,
    build_product_trend_projection,
)

CANONICAL_PRODUCT_TREND_VERSION = "22.5.6-canonical-v2"
_CACHE: Dict[tuple[str, str, str, int, str], Dict[str, Any]] = {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_snapshot_rows(
    *,
    user_id: str | None = None,
    limit: int = MAX_SNAPSHOT_SCAN,
) -> List[Dict[str, Any]]:
    history = product_snapshot_history(limit=limit)
    snapshots: List[Dict[str, Any]] = []
    for metadata in history:
        data_version = _text(metadata.get("dataVersion"))
        if not data_version:
            continue
        snapshot = get_product_snapshot(data_version=data_version, user_id=user_id)
        if not isinstance(snapshot, dict):
            continue
        snapshots.append(
            {
                **snapshot,
                "snapshotId": snapshot.get("snapshotId") or metadata.get("snapshotId"),
                "dataVersion": snapshot.get("dataVersion") or data_version,
                "createdAt": snapshot.get("createdAt") or metadata.get("createdAt") or metadata.get("capturedAt"),
                "updatedAt": snapshot.get("updatedAt") or metadata.get("updatedAt") or metadata.get("capturedAt"),
            }
        )
    return snapshots


def read_canonical_product_trend(
    product_id: str,
    *,
    store_id: str | None = None,
    user_id: str | None = None,
) -> Dict[str, Any]:
    snapshots = _canonical_snapshot_rows(user_id=user_id)
    latest_stamp = _text(snapshots[0].get("updatedAt")) if snapshots else "none"
    cache_key = (
        _text(product_id),
        _text(store_id),
        latest_stamp,
        len(snapshots),
        _text(user_id),
    )
    cached = _CACHE.get(cache_key)
    if cached:
        return {**cached, "cacheState": "memory_hit"}

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
    }
    if len(_CACHE) >= 256:
        _CACHE.clear()
    _CACHE[cache_key] = projection
    return {**projection, "cacheState": "fresh"}


__all__ = [
    "CANONICAL_PRODUCT_TREND_VERSION",
    "read_canonical_product_trend",
]
