"""Canonical current-epoch history adapter for frozen task evidence.

Task evidence must freeze the same canonical product facts used by product detail,
but additionally respect the task's dataVersion/creation boundary.  This adapter
reuses the registered canonical-history bridge metadata and single-product slim
snapshot reader; it never reads the retired ``system_product_snapshots_v14`` table
and never retains complete multi-product history payloads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from src.services.canonical_product_trend_v2_service import (
    _history_fingerprint,
    _history_metadata,
    _slim_snapshot_for_product,
    current_competition_history_epoch,
)
from src.services.product_trend_read_model_v217_service import MAX_SNAPSHOT_SCAN

TASK_EVIDENCE_CANONICAL_HISTORY_VERSION = "1.0.0"
SNAPSHOT_AUTHORITY = "canonical_product_snapshot_sets_v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: Any) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _candidate_product_ids(identity: Dict[str, Any]) -> List[str]:
    ordered = [
        identity.get("productObjectId"),
        identity.get("objectId"),
        identity.get("productId"),
        identity.get("platformItemId"),
        identity.get("skuId"),
        identity.get("systemProductCode"),
    ]
    return list(dict.fromkeys(_text(value) for value in ordered if _text(value)))


def _bounded_metadata(
    metadata: List[Dict[str, Any]],
    *,
    source_data_version: str | None,
    frozen_at: str | None,
) -> tuple[List[Dict[str, Any]], str | None]:
    rows = [dict(item) for item in metadata if isinstance(item, dict)]
    data_version = _text(source_data_version)
    if data_version:
        indexes = [
            index
            for index, item in enumerate(rows)
            if _text(item.get("data_version")) == data_version
        ]
        if not indexes:
            return [], "source_data_version_not_in_current_history_epoch"
        # Metadata is newest -> oldest.  Keep the task version and all older rows;
        # later reports must never rewrite a frozen task evidence window.
        rows = rows[min(indexes):]

    cutoff = _parse_time(frozen_at)
    if cutoff:
        bounded: List[Dict[str, Any]] = []
        for item in rows:
            stamp = _parse_time(item.get("created_at") or item.get("updated_at"))
            if stamp is None or stamp <= cutoff:
                bounded.append(item)
        rows = bounded
    return rows, None


def task_bounded_canonical_product_snapshots(
    identity: Dict[str, Any],
    *,
    source_data_version: str | None = None,
    frozen_at: str | None = None,
    limit: int = MAX_SNAPSHOT_SCAN,
) -> Dict[str, Any]:
    """Return product-only canonical snapshots visible at the task boundary.

    The current competition history epoch remains the hard archive boundary.  An
    explicit task dataVersion that is not present in the epoch fails closed instead
    of falling forward to the latest product state.
    """
    epoch = current_competition_history_epoch()
    epoch_id = _text(epoch.get("epochId"))
    epoch_started_at = _text(epoch.get("startedAt"))
    metadata = _history_metadata(epoch_started_at, max(1, int(limit or MAX_SNAPSHOT_SCAN)))
    history_hash = _history_fingerprint(metadata, epoch_id)
    bounded, reason = _bounded_metadata(
        metadata,
        source_data_version=source_data_version,
        frozen_at=frozen_at,
    )

    store_id = _text(identity.get("storeId")) or None
    best_product_id: str | None = None
    best_snapshots: List[Dict[str, Any]] = []
    for product_id in _candidate_product_ids(identity):
        snapshots: List[Dict[str, Any]] = []
        for row in bounded:
            snapshot = _slim_snapshot_for_product(row, product_id, store_id)
            if snapshot is not None:
                snapshots.append(snapshot)
        if len(snapshots) > len(best_snapshots):
            best_product_id = product_id
            best_snapshots = snapshots

    if not best_snapshots and reason is None:
        reason = "canonical_product_identity_not_observed_at_task_boundary"

    return {
        "version": TASK_EVIDENCE_CANONICAL_HISTORY_VERSION,
        "snapshotAuthority": SNAPSHOT_AUTHORITY,
        "legacySnapshotFallbackUsed": False,
        "wholeSnapshotRetention": False,
        "historyScanMode": "current_epoch_metadata_then_single_row_single_product_task_boundary",
        "historyEpochId": epoch_id or None,
        "historyEpochStartedAt": epoch_started_at or None,
        "historyIdentityHash": history_hash,
        "sourceDataVersion": _text(source_data_version) or None,
        "frozenAt": _text(frozen_at) or None,
        "matchedProductId": best_product_id,
        "candidateSnapshotCount": len(bounded),
        "matchedSnapshotCount": len(best_snapshots),
        "reason": reason,
        "snapshots": best_snapshots,
    }


__all__ = [
    "SNAPSHOT_AUTHORITY",
    "TASK_EVIDENCE_CANONICAL_HISTORY_VERSION",
    "task_bounded_canonical_product_snapshots",
]
