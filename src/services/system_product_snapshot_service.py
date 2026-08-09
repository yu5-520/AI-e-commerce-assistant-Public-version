"""Compatibility facade for the canonical product snapshot root.

The historical ``system_product_snapshot_service`` name remains import-compatible,
but it no longer owns a second product snapshot implementation. All reads and
materialization are delegated to ``canonical_product_snapshot_service`` so the
Agent path and product-detail path cannot diverge at the fact layer.

V22.5 baseline recovery adds one important boundary here: Agent/Signal history is
scoped to dataVersions that still exist in the active imported-report runtime and
must be strictly earlier in import order than the current report. Canonical rows
left from an earlier demo cycle therefore cannot turn the first newly uploaded
report into a fake comparison signal, and a later upload can never be used as the
"previous" report merely because asynchronous station work completed first.

When the historical ``system_product_snapshots_v14`` object is absent, a SQLite
compatibility view is created over the canonical table. Its only supported legacy
write is UPDATE, implemented as an INSTEAD OF trigger that writes through to the
canonical table. No rows are persisted in the legacy object, so it cannot become a
second source of truth.
"""
from __future__ import annotations

from typing import Dict, List

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
    product_snapshot_history as _canonical_product_snapshot_history,
    stable_hash,
)

SYSTEM_PRODUCT_SNAPSHOT_VERSION = CANONICAL_PRODUCT_SNAPSHOT_VERSION
LEGACY_PRODUCT_SNAPSHOT_OBJECT = "system_product_snapshots_v14"
LEGACY_PRODUCT_SNAPSHOT_UPDATE_TRIGGER = "compat_system_product_snapshots_v14_update"


def _ensure_legacy_snapshot_compatibility_view() -> str:
    """Route stale SQL reads/metadata updates into canonical storage."""
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
            object_type = "view"
        else:
            object_type = str(row["type"] or "")

        if object_type == "view":
            conn.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS {LEGACY_PRODUCT_SNAPSHOT_UPDATE_TRIGGER}
                INSTEAD OF UPDATE ON {LEGACY_PRODUCT_SNAPSHOT_OBJECT}
                BEGIN
                    UPDATE canonical_product_snapshot_sets_v1
                    SET
                        data_version = NEW.data_version,
                        product_count = NEW.product_count,
                        payload = NEW.payload,
                        created_at = NEW.created_at,
                        updated_at = NEW.updated_at
                    WHERE snapshot_id = OLD.snapshot_id;
                END
                """
            )
            conn.commit()
            return "canonical_compatibility_view_ready"

        return f"existing_{object_type}"


def _active_report_import_order() -> Dict[str, int]:
    """Return active business dataVersions in exact database insertion order.

    ``reset-runtime-data`` clears ``imported_report_rows``. Canonical snapshot storage
    historically survived some reset paths, so canonical rows alone are not enough to
    decide whether a report belongs to the current comparison cycle. SQLite rowid is
    used only as an ordering witness inside the active import table; it is never a
    product identity or cross-table join key.
    """
    try:
        with connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='imported_report_rows' LIMIT 1"
            ).fetchone()
            if not exists:
                return {}
            rows = conn.execute(
                """
                SELECT data_version, MIN(rowid) AS first_rowid
                FROM imported_report_rows
                WHERE data_version IS NOT NULL AND TRIM(data_version) != ''
                GROUP BY data_version
                ORDER BY first_rowid ASC
                """
            ).fetchall()
    except Exception:
        return {}
    return {
        str(row["data_version"]): int(row["first_rowid"])
        for row in rows
        if row["data_version"] and row["first_rowid"] is not None
    }


def product_snapshot_history(
    data_version: str | None = None,
    *,
    limit: int = 90,
) -> List[dict]:
    """Return history for Signal/Agent only from earlier active report imports.

    An unscoped history read remains the canonical history API. A scoped read is the
    Signal/Agent temporal gate: if the current dataVersion is not part of the active
    import runtime there is no admissible comparison history; otherwise only active
    dataVersions inserted before it may become previous snapshots.
    """
    selected_limit = max(1, int(limit or 90))
    if not data_version:
        return _canonical_product_snapshot_history(None, limit=selected_limit)

    order = _active_report_import_order()
    current_order = order.get(str(data_version))
    if current_order is None:
        return []
    allowed_versions = {
        version
        for version, first_rowid in order.items()
        if first_rowid < current_order
    }
    if not allowed_versions:
        return []

    candidates = _canonical_product_snapshot_history(
        str(data_version),
        limit=max(selected_limit, len(order) + 8),
    )
    filtered = [
        item
        for item in candidates
        if str(item.get("dataVersion") or "") in allowed_versions
    ]
    filtered.sort(
        key=lambda item: order.get(str(item.get("dataVersion") or ""), -1),
        reverse=True,
    )
    return filtered[:selected_limit]


def previous_product_snapshot(data_version: str | None = None):
    history = product_snapshot_history(data_version, limit=1)
    return history[0] if history else None


def materialize_system_product_snapshot(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
):
    """Legacy station entrypoint routed to the canonical root plus SQL bridge."""
    _ensure_legacy_snapshot_compatibility_view()
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
