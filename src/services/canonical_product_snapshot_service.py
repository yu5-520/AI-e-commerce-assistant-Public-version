"""Canonical product fact snapshot root.

This module is the only product-fact aggregation point between imported/materialized
facts and downstream consumers. Agent and frontend projections are descendants of
the same immutable ``productSnapshotHash``; neither branch is allowed to rebuild
product facts independently.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.module_projection_service import projected_products
from src.services.permission_stamp_service import row_permission_stamp

CANONICAL_PRODUCT_SNAPSHOT_VERSION = "1.0"
SCHEMA_VERSION = "canonicalProductSnapshot.v1"
CORE_METRICS = [
    "paymentAmount",
    "roi",
    "roas",
    "adSpend",
    "clickRate",
    "conversionRate",
    "refundRate",
    "inventory",
]
PROFILE_FIELDS = [
    "objectId",
    "productId",
    "skuId",
    "spuId",
    "erpProductCode",
    "storeId",
    "storeName",
    "platform",
    "title",
    "shortName",
    "productUrl",
    "verticalCategory",
    "categoryLevel1",
    "categoryLevel2",
    "categoryLevel3",
    "priceBand",
    "productRole",
    "lifecycleStage",
    "metricDate",
    "reportDate",
    "dataDate",
]
VOLATILE_FACT_FIELDS = {"createdAt", "updatedAt", "created_at", "updated_at"}


def now_iso() -> str:
    return datetime.now().isoformat()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(item: Dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in {None, "", "—", "未识别"}:
            return value
    return default


def _product_key(item: Dict[str, Any]) -> str:
    explicit = _text(item.get("objectId"))
    if explicit:
        return explicit
    return "::".join(
        [
            _text(item.get("platform")) or "unknown",
            _text(item.get("storeId")) or "GLOBAL",
            _text(item.get("productId") or item.get("id")) or "PRODUCT",
            _text(item.get("skuId")) or "NO-SKU",
        ]
    )


def _profile(item: Dict[str, Any]) -> Dict[str, Any]:
    product_key = _product_key(item)
    stamp = row_permission_stamp(item)
    profile = {field: item.get(field) for field in PROFILE_FIELDS}
    profile.update(
        {
            "objectId": product_key,
            "productId": item.get("productId") or item.get("id"),
            "skuId": _first(item, ["skuId", "sku", "sku_id"]),
            "spuId": _first(item, ["spuId", "spu", "spu_id"]),
            "erpProductCode": _first(item, ["erpProductCode", "erpCode", "erp_product_code", "商家编码"]),
            "storeName": item.get("storeName") or item.get("store"),
            "platform": _first(item, ["platform", "平台"], "unknown"),
            "title": item.get("title") or item.get("shortName"),
            "productUrl": _first(item, ["productUrl", "productLink", "link", "url", "商品链接"]),
            "verticalCategory": _first(item, ["verticalCategory", "categoryLevel3", "categoryLevel2", "categoryLevel1", "二级类目", "一级类目"], "未归类"),
            "permissionStampId": stamp.get("permissionStampId") or item.get("permissionStampId"),
        }
    )
    return profile


def _metric(item: Dict[str, Any]) -> Dict[str, Any]:
    metric = {name: item.get(name) for name in CORE_METRICS}
    if metric.get("roas") in {None, "", "—", "未识别"}:
        metric["roas"] = item.get("roi")
    metric.update(
        {
            "paymentAmount": item.get("paymentAmount"),
            "grossMargin": item.get("grossMargin"),
            "sellableDays": item.get("sellableDays"),
            "organicVisitors": item.get("organicVisitors"),
            "paidVisitors": item.get("paidVisitors"),
            "afterSales": item.get("afterSales"),
            "metricDate": item.get("metricDate"),
            "reportDate": item.get("reportDate"),
            "dataDate": item.get("dataDate"),
            "sourceDataVersions": list(item.get("sourceDataVersions") or []),
            "sourceDatasets": list(item.get("sourceDatasets") or []),
            "productMetricFacts": list(item.get("productMetricFacts") or item.get("metricFacts") or []),
            "trafficSourceFacts": list(item.get("trafficSourceFacts") or []),
            "metricFactSummary": dict(item.get("metricFactSummary") or {}),
        }
    )
    return metric


def _fact_hash(fact: Dict[str, Any]) -> str:
    for key in ("sourceHash", "source_hash", "factHash", "fact_hash", "hash"):
        if fact.get(key):
            return str(fact[key])
    stable = {key: value for key, value in fact.items() if key not in VOLATILE_FACT_FIELDS}
    return stable_hash(stable)


def _fact_hash_refs(metric: Dict[str, Any]) -> List[str]:
    refs: List[str] = []
    facts = [*(metric.get("productMetricFacts") or []), *(metric.get("trafficSourceFacts") or [])]
    for fact in facts:
        if isinstance(fact, dict):
            refs.append(_fact_hash(fact))
    return sorted(dict.fromkeys(refs))


def _source_refs(metric: Dict[str, Any]) -> List[str]:
    refs = [f"dataVersion:{value}" for value in metric.get("sourceDataVersions") or [] if value]
    refs.extend(f"dataset:{value}" for value in metric.get("sourceDatasets") or [] if value)
    return sorted(dict.fromkeys(refs))


def _completeness(metric: Dict[str, Any]) -> Dict[str, Any]:
    present = [name for name in CORE_METRICS if metric.get(name) not in {None, "", "—", "未识别"}]
    missing = [name for name in CORE_METRICS if name not in present]
    return {
        "requiredMetricCount": len(CORE_METRICS),
        "presentMetricCount": len(present),
        "missingMetrics": missing,
        "complete": not missing,
    }


def _canonical_product(item: Dict[str, Any], data_version: str | None) -> Dict[str, Any]:
    profile = _profile(item)
    metric = _metric(item)
    fact_refs = _fact_hash_refs(metric)
    source_refs = _source_refs(metric)
    base = {
        "schemaVersion": SCHEMA_VERSION,
        "dataVersion": data_version,
        "objectId": profile.get("objectId"),
        "productId": profile.get("productId"),
        "storeId": profile.get("storeId"),
        "profileSnapshot": profile,
        "metricSnapshot": metric,
        "factHashRefs": fact_refs,
        "sourceArtifactRefs": source_refs,
        "completeness": _completeness(metric),
    }
    product_hash = stable_hash(base)
    return {**base, "productSnapshotHash": product_hash, "parentSnapshotHash": product_hash}


def agent_projection(product: Dict[str, Any]) -> Dict[str, Any]:
    parent = product.get("productSnapshotHash")
    body = {
        "contract": "canonicalProductSnapshot.agentProjection.v1",
        "parentSnapshotHash": parent,
        "dataVersion": product.get("dataVersion"),
        "productId": product.get("productId"),
        "storeId": product.get("storeId"),
        "profileLayer": product.get("profileSnapshot") or {},
        "metricLayer": product.get("metricSnapshot") or {},
        "factHashRefs": product.get("factHashRefs") or [],
        "sourceArtifactRefs": product.get("sourceArtifactRefs") or [],
    }
    return {**body, "projectionHash": stable_hash(body)}


def detail_projection(product: Dict[str, Any]) -> Dict[str, Any]:
    parent = product.get("productSnapshotHash")
    profile = dict(product.get("profileSnapshot") or {})
    metric = dict(product.get("metricSnapshot") or {})
    body = {
        **profile,
        **metric,
        "dataVersion": product.get("dataVersion"),
        "productSnapshotHash": parent,
        "parentSnapshotHash": parent,
        "factHashRefs": list(product.get("factHashRefs") or []),
        "sourceArtifactRefs": list(product.get("sourceArtifactRefs") or []),
        "completeness": dict(product.get("completeness") or {}),
        "readMode": "canonical_product_snapshot_projection",
    }
    projection_hash = stable_hash(body)
    return {**body, "detailProjectionHash": projection_hash}


def ensure_snapshot_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS canonical_product_snapshot_sets_v1 (
                snapshot_id TEXT PRIMARY KEY,
                data_version TEXT,
                set_snapshot_hash TEXT NOT NULL,
                product_count INTEGER DEFAULT 0,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "canonical_product_snapshot_sets_v1",
            {
                "data_version": "TEXT",
                "set_snapshot_hash": "TEXT",
                "product_count": "INTEGER DEFAULT 0",
                "updated_at": "TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_canonical_product_snapshot_version ON canonical_product_snapshot_sets_v1(data_version, created_at)"
        )
        conn.commit()


def snapshot_id_for(data_version: str | None) -> str:
    return f"CANONICAL-PRODUCT-SNAPSHOT-{data_version or 'latest'}"


def _row_to_snapshot(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {
        **payload,
        "snapshotId": row["snapshot_id"],
        "dataVersion": row["data_version"],
        "setSnapshotHash": row["set_snapshot_hash"],
        "snapshotHash": row["set_snapshot_hash"],
        "productCount": int(row["product_count"] or 0),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def get_product_snapshot(data_version: str | None = None) -> Dict[str, Any] | None:
    ensure_snapshot_tables()
    with connect() as conn:
        if data_version:
            row = conn.execute(
                "SELECT * FROM canonical_product_snapshot_sets_v1 WHERE data_version=? ORDER BY created_at DESC LIMIT 1",
                (data_version,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM canonical_product_snapshot_sets_v1 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
    return _row_to_snapshot(row) if row else None


def product_snapshot_history(data_version: str | None = None, *, limit: int = 90) -> List[Dict[str, Any]]:
    ensure_snapshot_tables()
    with connect() as conn:
        if data_version:
            rows = conn.execute(
                "SELECT * FROM canonical_product_snapshot_sets_v1 WHERE data_version != ? ORDER BY created_at DESC LIMIT ?",
                (data_version, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM canonical_product_snapshot_sets_v1 ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_snapshot(row) for row in rows]


def previous_product_snapshot(data_version: str | None = None) -> Dict[str, Any] | None:
    history = product_snapshot_history(data_version, limit=1)
    return history[0] if history else None


def materialize_canonical_product_snapshot(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
) -> Dict[str, Any]:
    ensure_snapshot_tables()
    snapshot_id = snapshot_id_for(data_version)
    if not force:
        existing = get_product_snapshot(data_version)
        if existing:
            return {**existing, "idempotentHit": True}

    products = [_canonical_product(item, data_version) for item in projected_products(user_id)]
    if data_version:
        filtered: List[Dict[str, Any]] = []
        for item in products:
            versions = (item.get("metricSnapshot") or {}).get("sourceDataVersions") or []
            if not versions or data_version in versions:
                filtered.append(item)
        if filtered:
            products = filtered
    products.sort(key=lambda item: (str(item.get("storeId") or ""), str(item.get("productId") or ""), str(item.get("objectId") or "")))

    set_seed = {
        "schemaVersion": SCHEMA_VERSION,
        "dataVersion": data_version,
        "productSnapshotHashes": [item["productSnapshotHash"] for item in products],
    }
    set_hash = stable_hash(set_seed)
    payload = {
        "version": CANONICAL_PRODUCT_SNAPSHOT_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": snapshot_id,
        "dataVersion": data_version,
        "setSnapshotHash": set_hash,
        "snapshotHash": set_hash,
        "productCount": len(products),
        "products": products,
        "agentProductSnapshotPackages": [agent_projection(item) for item in products],
        "detailProjections": [detail_projection(item) for item in products],
        "source": "materialized_product_facts_to_canonical_snapshot",
        "rule": "One immutable product fact snapshot is the sole parent of Agent and product-detail projections.",
    }
    now = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO canonical_product_snapshot_sets_v1
            (snapshot_id,data_version,set_snapshot_hash,product_count,payload,created_at,updated_at)
            VALUES (?,?,?,?,?,COALESCE((SELECT created_at FROM canonical_product_snapshot_sets_v1 WHERE snapshot_id=?),?),?)
            """,
            (snapshot_id, data_version, set_hash, len(products), dumps(payload), snapshot_id, now, now),
        )
        conn.commit()
    return {
        "version": CANONICAL_PRODUCT_SNAPSHOT_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": snapshot_id,
        "dataVersion": data_version,
        "snapshotHash": set_hash,
        "setSnapshotHash": set_hash,
        "productCount": len(products),
        "productSnapshotRef": f"canonical_product_snapshot:{snapshot_id}",
        "outputRef": f"canonical_product_snapshot:{snapshot_id}",
    }


def materialize_system_product_snapshot(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
) -> Dict[str, Any]:
    """Compatibility name for callers while canonical path becomes authoritative."""
    return materialize_canonical_product_snapshot(data_version=data_version, user_id=user_id, force=force)


def find_product_detail(
    product_id: str,
    *,
    data_version: str | None = None,
    user_id: str | None = None,
) -> Dict[str, Any] | None:
    snapshot = get_product_snapshot(data_version)
    if snapshot is None:
        materialize_canonical_product_snapshot(data_version=data_version, user_id=user_id, force=True)
        snapshot = get_product_snapshot(data_version)
    wanted = _text(product_id)
    for product in (snapshot or {}).get("products") or []:
        keys = {
            _text(product.get("productId")),
            _text(product.get("objectId")),
            _text((product.get("profileSnapshot") or {}).get("skuId")),
        }
        if wanted in keys:
            return detail_projection(product)
    return None


def list_product_details(
    *,
    data_version: str | None = None,
    user_id: str | None = None,
) -> List[Dict[str, Any]]:
    snapshot = get_product_snapshot(data_version)
    if snapshot is None:
        materialize_canonical_product_snapshot(data_version=data_version, user_id=user_id, force=True)
        snapshot = get_product_snapshot(data_version)
    return [detail_projection(item) for item in (snapshot or {}).get("products") or []]


__all__ = [
    "CANONICAL_PRODUCT_SNAPSHOT_VERSION",
    "SCHEMA_VERSION",
    "stable_hash",
    "agent_projection",
    "detail_projection",
    "get_product_snapshot",
    "product_snapshot_history",
    "previous_product_snapshot",
    "materialize_canonical_product_snapshot",
    "materialize_system_product_snapshot",
    "find_product_detail",
    "list_product_details",
]
