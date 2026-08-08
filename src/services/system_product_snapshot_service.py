"""Compatibility facade for the canonical product snapshot root.

The historical ``system_product_snapshot_service`` name remains import-compatible,
but it no longer owns a second product snapshot implementation. All reads and
materialization are delegated to ``canonical_product_snapshot_service`` so the
Agent path and product-detail path cannot diverge at the fact layer.
"""
from src.services.canonical_product_snapshot_service import (
    CANONICAL_PRODUCT_SNAPSHOT_VERSION,
    agent_projection,
    detail_projection,
    find_product_detail,
    get_product_snapshot,
    list_product_details,
    materialize_canonical_product_snapshot,
    materialize_system_product_snapshot,
    previous_product_snapshot,
    product_snapshot_history,
    stable_hash,
)

SYSTEM_PRODUCT_SNAPSHOT_VERSION = CANONICAL_PRODUCT_SNAPSHOT_VERSION


def product_snapshot_summary(limit: int = 20):
    items = product_snapshot_history(None, limit=limit)
    return {
        "version": SYSTEM_PRODUCT_SNAPSHOT_VERSION,
        "snapshotCount": len(items),
        "latest": items[0] if items else None,
        "items": items,
        "source": "canonical_product_snapshot_service",
    }


__all__ = [
    "SYSTEM_PRODUCT_SNAPSHOT_VERSION",
    "stable_hash",
    "agent_projection",
    "detail_projection",
    "find_product_detail",
    "list_product_details",
    "get_product_snapshot",
    "product_snapshot_history",
    "previous_product_snapshot",
    "materialize_canonical_product_snapshot",
    "materialize_system_product_snapshot",
    "product_snapshot_summary",
]
