"""Compatibility facade for the canonical product snapshot root.

The historical ``system_product_snapshot_service`` name remains import-compatible,
but it no longer owns a second product snapshot implementation. All reads and
materialization are delegated to ``canonical_product_snapshot_service`` so the
Agent path and product-detail path cannot diverge at the fact layer.

A read-only SQLite compatibility view is created only when the historical
``system_product_snapshots_v14`` object is absent. This keeps stale direct SQL
readers on the canonical table during the migration window without restoring the
legacy table as a second persisted source of truth.
"""
from src.repositories.sqlite_repository import connect
from src.services.canonical_product_snapshot_service import (
    CANONICAL_PRODUCT_SNAPSHOT_VERSION,
    agent_projection,
    detail_projection,
    ensure_snapshot_tables,
    find_product_detail,
    get_product_snapshot,
    list_product_details,
    materialize_canonical_product_snapshot,
    previous_product_snapshot,
    product_snapshot_history,
    stable_hash,
)

SYSTEM_PRODUCT_SNAPSHOT_VERSION = CANONICAL_PRODUCT_SNAPSHOT_VERSION
LEGACY_PRODUCT_SNAPSHOT_OBJECT = "system_product_snapshots_v14"


def _ensure_legacy_snapshot_read_view() -> str:
    """Bridge stale SQL readers to canonical storage without reviving legacy writes."""
    ensure_snapshot_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT type FROM sqlite_master WHERE name=? LIMIT 1",
            (LEGACY_PRODUCT_SNAPSHOT_OBJECT,),
        ).fetchone()
        if row is None:
            conn.execute(
                f"""
                CREATE VIEW {LEGACY_PRODUCT_SNAPSHOT_OBJECT} AS
                SELECT
                    snapshot_id,
                    data_version,
                    product_count,
                    payload,
                    created_at,
                    updated_at
                FROM canonical_product_snapshot_sets_v1
                """
            )
            conn.commit()
            return "canonical_read_view_created"
        return f"existing_{row['type']}"


def materialize_system_product_snapshot(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
):
    """Legacy station entrypoint routed to the canonical root plus read bridge."""
    _ensure_legacy_snapshot_read_view()
    return materialize_canonical_product_snapshot(
        data_version=data_version,
        user_id=user_id,
        force=force,
    )


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
