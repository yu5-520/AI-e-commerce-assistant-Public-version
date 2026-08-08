"""V21.5 compatibility bridge to the canonical product snapshot root.

V21.5 scopes projections to one business dataVersion. The historical runtime
implemented that scope by monkey-patching ``materialize_system_product_snapshot``
and then updating ``system_product_snapshots_v14`` directly. The canonical snapshot
migration removes that legacy table, so this bridge keeps only the dataVersion
scope behavior and routes persistence through ``canonical_product_snapshot_service``.
"""
from __future__ import annotations

from typing import Any, Dict

from src.repositories.sqlite_repository import connect, loads
from src.services import canonical_product_snapshot_service as canonical

BRIDGE_VERSION = "canonicalProductSnapshot.v21_5_bridge.v1"


def _batch_id(data_version: str | None) -> str | None:
    if not data_version:
        return None
    try:
        from src.services import v215_report_batch_evidence_service as v215

        return v215._batch_for_data_version(connect, loads, data_version)
    except Exception:
        return None


def materialize_canonical_product_snapshot_v215(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
) -> Dict[str, Any]:
    """Preserve V21.5 version scoping without touching the removed legacy table."""
    from src.services import v215_report_batch_evidence_service as v215

    token = v215._PROJECTION_VERSION_CONTEXT.set(data_version)
    try:
        result = canonical.materialize_canonical_product_snapshot(
            data_version=data_version,
            user_id=user_id,
            force=force,
        )
    finally:
        v215._PROJECTION_VERSION_CONTEXT.reset(token)

    return {
        **result,
        "compatibilityBridge": BRIDGE_VERSION,
        "version": v215.V215_VERSION,
        "dataVersion": data_version,
        "businessDataVersion": data_version,
        "reportBatchId": _batch_id(data_version),
        "canonicalProductSnapshot": True,
        "legacySnapshotTableRead": False,
        "legacySnapshotTableWrite": False,
    }


def install_canonical_product_snapshot_v215_bridge() -> Dict[str, Any]:
    """Repair V21.5 runtime aliases after its installer has applied monkey patches."""
    from src.services import product_signal_snapshot_service as signal_snapshot
    from src.services import system_product_snapshot_service as product_snapshot

    product_snapshot.materialize_system_product_snapshot = materialize_canonical_product_snapshot_v215
    signal_snapshot.materialize_system_product_snapshot = materialize_canonical_product_snapshot_v215
    return {
        "version": BRIDGE_VERSION,
        "installed": True,
        "productSnapshotOwner": "canonical_product_snapshot_service",
        "legacySnapshotTableRead": False,
        "legacySnapshotTableWrite": False,
    }


__all__ = [
    "BRIDGE_VERSION",
    "materialize_canonical_product_snapshot_v215",
    "install_canonical_product_snapshot_v215_bridge",
]
