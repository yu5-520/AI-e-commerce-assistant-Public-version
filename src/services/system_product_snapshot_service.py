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

Competition product-lineage rule:
- ``productRegistryKey/objectId`` identifies the product entity.
- ``productSnapshotHash`` identifies one immutable product-fact version.
- a bound hash is strict: an exact-hash miss is ``lineage_broken`` and never falls
  back to report/file/product identity.
- identity lookup exists only to migrate historical Tasks that never stored a hash.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

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
PRODUCT_SNAPSHOT_LINEAGE_VERSION = "1.0"
LEGACY_PRODUCT_SNAPSHOT_OBJECT = "system_product_snapshots_v14"
LEGACY_PRODUCT_SNAPSHOT_UPDATE_TRIGGER = "compat_system_product_snapshots_v14_update"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}, "UNKNOWN", "未识别", "—", "未提供"):
            return value
    return None


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
    """Return active business dataVersions in exact database insertion order."""
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
    """Return history for Signal/Agent only from earlier active report imports."""
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


def _target_snapshot(data_version: str | None) -> Dict[str, Any] | None:
    return get_product_snapshot(data_version) if data_version else get_product_snapshot()


def _snapshot_sets_for_exact_hash(data_version: str | None) -> List[Dict[str, Any]]:
    """Search exact immutable hash across current and canonical historical sets."""
    result: List[Dict[str, Any]] = []
    current = _target_snapshot(data_version)
    if current:
        result.append(current)
    for item in _canonical_product_snapshot_history(None, limit=120):
        if not isinstance(item, dict):
            continue
        if any(item.get("setSnapshotHash") == existing.get("setSnapshotHash") for existing in result):
            continue
        result.append(item)
    return result


def _product_profile(product: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(product.get("profileSnapshot"))


def _product_registry_key(product: Dict[str, Any]) -> str:
    profile = _product_profile(product)
    return _text(product.get("objectId") or profile.get("objectId"))


def _product_snapshot_hash(product: Dict[str, Any]) -> str:
    return _text(product.get("productSnapshotHash") or product.get("snapshotHash"))


def _product_tokens(product: Dict[str, Any]) -> set[str]:
    profile = _product_profile(product)
    return {
        token
        for token in (
            _product_registry_key(product),
            _text(product.get("productId") or profile.get("productId")),
            _text(profile.get("skuId")),
            _text(profile.get("spuId")),
            _text(profile.get("erpProductCode")),
        )
        if token
    }


def _store_matches(product: Dict[str, Any], store_id: str | None) -> bool:
    wanted = _text(store_id)
    if not wanted:
        return True
    profile = _product_profile(product)
    return wanted in {
        _text(product.get("storeId")),
        _text(profile.get("storeId")),
        _text(profile.get("storeName")),
    }


def _find_exact_hash(
    product_snapshot_hash: str,
    data_version: str | None,
) -> Tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
    wanted = _text(product_snapshot_hash)
    if not wanted:
        return None, None
    for snapshot_set in _snapshot_sets_for_exact_hash(data_version):
        for product in _list(snapshot_set.get("products")):
            if isinstance(product, dict) and _product_snapshot_hash(product) == wanted:
                return product, snapshot_set
    return None, None


def _find_identity(
    product_id: str | None,
    store_id: str | None,
    data_version: str | None,
) -> Tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
    """Legacy migration lookup is limited to one canonical target set."""
    wanted = _text(product_id)
    snapshot_set = _target_snapshot(data_version)
    if not wanted or not snapshot_set:
        return None, snapshot_set
    for product in _list(snapshot_set.get("products")):
        if not isinstance(product, dict) or not _store_matches(product, store_id):
            continue
        if wanted in _product_tokens(product):
            return product, snapshot_set
    return None, snapshot_set


def _resolved_payload(
    product: Dict[str, Any],
    snapshot_set: Dict[str, Any] | None,
    *,
    match_mode: str,
    strict_hash: bool,
) -> Dict[str, Any]:
    detail = detail_projection(product)
    return {
        "version": PRODUCT_SNAPSHOT_LINEAGE_VERSION,
        "ready": True,
        "status": "resolved",
        "matchMode": match_mode,
        "strictHash": strict_hash,
        "legacyIdentityMigration": match_mode == "legacy_identity_migration",
        "dataVersion": product.get("dataVersion") or (snapshot_set or {}).get("dataVersion"),
        "setSnapshotHash": (snapshot_set or {}).get("setSnapshotHash"),
        "productRegistryKey": _product_registry_key(product),
        "productSnapshotHash": _product_snapshot_hash(product),
        "productSnapshot": detail,
    }


def resolve_product_snapshot(
    *,
    product_id: str | None = None,
    store_id: str | None = None,
    data_version: str | None = None,
    product_snapshot_hash: str | None = None,
    allow_legacy_identity_migration: bool = False,
) -> Dict[str, Any]:
    """Resolve one canonical product snapshot under a strict hash-first rule."""
    bound_hash = _text(product_snapshot_hash)
    if bound_hash:
        product, snapshot_set = _find_exact_hash(bound_hash, data_version)
        if not product:
            return {
                "version": PRODUCT_SNAPSHOT_LINEAGE_VERSION,
                "ready": False,
                "status": "lineage_broken",
                "reason": "bound_product_snapshot_hash_not_found",
                "productSnapshotHash": bound_hash,
                "dataVersion": data_version,
                "strictHash": True,
                "legacyIdentityMigration": False,
            }
        return _resolved_payload(product, snapshot_set, match_mode="exact_hash", strict_hash=True)

    if not allow_legacy_identity_migration:
        return {
            "version": PRODUCT_SNAPSHOT_LINEAGE_VERSION,
            "ready": False,
            "status": "unbound",
            "reason": "product_snapshot_hash_required",
            "dataVersion": data_version,
            "strictHash": False,
            "legacyIdentityMigration": False,
        }

    product, snapshot_set = _find_identity(product_id, store_id, data_version)
    if not product:
        return {
            "version": PRODUCT_SNAPSHOT_LINEAGE_VERSION,
            "ready": False,
            "status": "legacy_identity_not_resolved",
            "reason": "canonical_product_identity_not_found",
            "productId": product_id,
            "storeId": store_id,
            "dataVersion": data_version,
            "strictHash": False,
            "legacyIdentityMigration": True,
        }
    return _resolved_payload(
        product,
        snapshot_set,
        match_mode="legacy_identity_migration",
        strict_hash=False,
    )


def _public_product_view(product: Dict[str, Any]) -> Dict[str, Any]:
    detail = detail_projection(product)
    detail["viewVersion"] = PRODUCT_SNAPSHOT_LINEAGE_VERSION
    detail["productRegistryKey"] = detail.get("objectId")
    detail["productSnapshotHash"] = detail.get("productSnapshotHash") or detail.get("snapshotHash")
    detail["metrics"] = {
        key: detail.get(key)
        for key in (
            "paymentAmount",
            "roas",
            "roi",
            "adSpend",
            "clickRate",
            "conversionRate",
            "refundRate",
            "grossMargin",
            "inventory",
        )
    }
    detail["readMode"] = "canonical_product_snapshot"
    detail["lineageRule"] = "productRegistryKey identifies entity; productSnapshotHash identifies immutable fact version."
    return detail


def read_canonical_product_views(
    *,
    store_id: str | None = None,
    data_version: str | None = None,
    limit: int = 100,
) -> Dict[str, Any]:
    snapshot_set = _target_snapshot(data_version)
    if not snapshot_set:
        return {
            "version": PRODUCT_SNAPSHOT_LINEAGE_VERSION,
            "ready": False,
            "count": 0,
            "currentDataVersion": data_version,
            "items": [],
            "reason": "canonical_product_snapshot_not_materialized",
        }
    items: List[Dict[str, Any]] = []
    for product in _list(snapshot_set.get("products")):
        if not isinstance(product, dict) or not _store_matches(product, store_id):
            continue
        items.append(_public_product_view(product))
        if len(items) >= int(limit):
            break
    return {
        "version": PRODUCT_SNAPSHOT_LINEAGE_VERSION,
        "ready": bool(items),
        "count": len(items),
        "currentDataVersion": snapshot_set.get("dataVersion"),
        "setSnapshotHash": snapshot_set.get("setSnapshotHash"),
        "items": items,
        "rule": "Product pages read every registered canonical product, not only products admitted by the signal gate.",
    }


def read_canonical_product_detail(
    product_id: str,
    *,
    store_id: str | None = None,
    data_version: str | None = None,
) -> Dict[str, Any]:
    resolved = resolve_product_snapshot(
        product_id=product_id,
        store_id=store_id,
        data_version=data_version,
        allow_legacy_identity_migration=True,
    )
    if not resolved.get("ready"):
        return {
            "version": PRODUCT_SNAPSHOT_LINEAGE_VERSION,
            "ready": False,
            "currentDataVersion": data_version,
            "item": None,
            "lineage": resolved,
        }
    detail = dict(_dict(resolved.get("productSnapshot")))
    detail["productRegistryKey"] = resolved.get("productRegistryKey")
    detail["productSnapshotHash"] = resolved.get("productSnapshotHash")
    detail["metrics"] = {
        key: detail.get(key)
        for key in (
            "paymentAmount",
            "roas",
            "roi",
            "adSpend",
            "clickRate",
            "conversionRate",
            "refundRate",
            "grossMargin",
            "inventory",
        )
    }
    return {
        "version": PRODUCT_SNAPSHOT_LINEAGE_VERSION,
        "ready": True,
        "currentDataVersion": resolved.get("dataVersion"),
        "item": detail,
        "lineage": {
            key: resolved.get(key)
            for key in (
                "version",
                "status",
                "matchMode",
                "strictHash",
                "legacyIdentityMigration",
                "setSnapshotHash",
                "productRegistryKey",
                "productSnapshotHash",
            )
        },
    }


def _sop_candidates(task: Dict[str, Any]) -> Iterable[Any]:
    report = _dict(task.get("taskDetailReport"))
    related = _dict(task.get("relatedTask"))
    plan = _dict(_first(task.get("taskPlan"), report.get("taskPlan"), related.get("taskPlan"), {}))
    related_plan = _dict(related.get("taskPlan"))
    return (
        task.get("operatorExecutionSop"),
        report.get("operatorExecutionSop"),
        plan.get("operatorExecutionSop"),
        plan.get("operatorActionSteps"),
        task.get("sopSteps"),
        report.get("sopSteps"),
        related.get("operatorExecutionSop"),
        related.get("sopSteps"),
        related_plan.get("operatorExecutionSop"),
        related_plan.get("sopSteps"),
    )


def recover_frozen_sop(task: Dict[str, Any]) -> List[Any]:
    """Recover already-frozen SOP content without invoking Agent or builders."""
    for candidate in _sop_candidates(task):
        if isinstance(candidate, list) and any(item not in (None, "", {}, []) for item in candidate):
            return [item for item in candidate if item not in (None, "", {}, [])]
    return []


def bind_task_product_lineage(task: Dict[str, Any]) -> Dict[str, Any]:
    """Attach canonical product lineage to a Task without rerunning Agent/SOP."""
    result = dict(_dict(task))
    report = _dict(result.get("taskDetailReport"))
    related = _dict(result.get("relatedTask"))
    plan = _dict(_first(result.get("taskPlan"), report.get("taskPlan"), related.get("taskPlan"), {}))
    product = dict(
        _dict(
            _first(
                result.get("productIdentity"),
                report.get("productIdentity"),
                plan.get("productIdentity"),
                related.get("productIdentity"),
                {},
            )
        )
    )
    data_version = _text(
        _first(
            result.get("dataVersion"),
            report.get("dataVersion"),
            related.get("dataVersion"),
            result.get("workflowRunId"),
            result.get("workflow_run_id"),
        )
    ) or None
    bound_hash = _text(
        _first(
            result.get("productSnapshotHash"),
            report.get("productSnapshotHash"),
            plan.get("productSnapshotHash"),
            product.get("productSnapshotHash"),
            related.get("productSnapshotHash"),
        )
    ) or None
    product_id = _text(
        _first(
            product.get("productRegistryKey"),
            product.get("objectId"),
            product.get("productId"),
            product.get("skuId"),
            result.get("productId"),
            related.get("productId"),
        )
    ) or None
    store_id = _text(
        _first(
            product.get("storeId"),
            result.get("storeId"),
            related.get("storeId"),
        )
    ) or None

    lineage = resolve_product_snapshot(
        product_id=product_id,
        store_id=store_id,
        data_version=data_version,
        product_snapshot_hash=bound_hash,
        allow_legacy_identity_migration=not bool(bound_hash),
    )
    result["productSnapshotLineage"] = {
        key: lineage.get(key)
        for key in (
            "version",
            "ready",
            "status",
            "reason",
            "matchMode",
            "strictHash",
            "legacyIdentityMigration",
            "dataVersion",
            "setSnapshotHash",
            "productRegistryKey",
            "productSnapshotHash",
        )
        if lineage.get(key) is not None
    }
    if lineage.get("ready"):
        registry_key = lineage.get("productRegistryKey")
        snapshot_hash = lineage.get("productSnapshotHash")
        product.update(
            objectId=product.get("objectId") or registry_key,
            productRegistryKey=registry_key,
            productSnapshotHash=snapshot_hash,
        )
        result["productIdentity"] = product
        result["productRegistryKey"] = registry_key
        result["productSnapshotHash"] = snapshot_hash
        result["productSnapshot"] = lineage.get("productSnapshot")
        result["productSnapshotStatus"] = "resolved"
    else:
        result["productSnapshotStatus"] = lineage.get("status") or "unresolved"

    sop = recover_frozen_sop(result)
    if sop:
        result["operatorExecutionSop"] = sop
        result["sopSteps"] = sop
    return result


__all__ = [
    "SYSTEM_PRODUCT_SNAPSHOT_VERSION",
    "PRODUCT_SNAPSHOT_LINEAGE_VERSION",
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
    "resolve_product_snapshot",
    "read_canonical_product_views",
    "read_canonical_product_detail",
    "recover_frozen_sop",
    "bind_task_product_lineage",
]
