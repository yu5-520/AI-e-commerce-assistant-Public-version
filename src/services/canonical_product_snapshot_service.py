"""Canonical product fact snapshot root.

One immutable product fact snapshot is the sole parent of Agent and product-detail
projections. Legacy system-product-snapshot contracts remain available as aliases
on the same canonical object so downstream stations can migrate without rebuilding
facts or creating a second source of truth.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.module_projection_service import projected_products
from src.services.permission_stamp_service import row_permission_stamp

CANONICAL_PRODUCT_SNAPSHOT_VERSION = "1.1"
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
METRIC_FIELDS = [
    "roas",
    "roi",
    "adSpend",
    "paymentAmount",
    "grossMargin",
    "clickRate",
    "conversionRate",
    "refundRate",
    "inventory",
    "sellableDays",
    "organicVisitors",
    "paidVisitors",
    "inventoryStatus",
    "afterSales",
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
    "isHeroProduct",
    "isNewProduct",
    "isCampaignProduct",
    "metricDate",
    "reportDate",
    "dataDate",
]
PERMISSION_REF_FIELDS = ["permissionStampId", "permissionGateStatus", "permissionScopeRef"]
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


def _unique(values: Iterable[Any]) -> List[str]:
    return sorted(dict.fromkeys(str(value) for value in values if value not in {None, ""}))


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


def _permission_ref(item: Dict[str, Any]) -> Dict[str, Any]:
    stamp = row_permission_stamp(item)
    stamp_id = stamp.get("permissionStampId") or item.get("permissionStampId")
    status = item.get("permissionGateStatus") or ("passed" if stamp_id else "quarantine")
    scope_ref = item.get("permissionScopeRef") or (f"permission_stamp:{stamp_id}" if stamp_id else "permission_stamp:missing")
    return {
        "permissionStampId": stamp_id,
        "permissionGateStatus": status,
        "permissionScopeRef": scope_ref,
    }


def _profile(item: Dict[str, Any]) -> Dict[str, Any]:
    permission_ref = _permission_ref(item)
    profile = {field: item.get(field) for field in PROFILE_FIELDS}
    profile.update(
        {
            "objectId": _product_key(item),
            "productId": item.get("productId") or item.get("id"),
            "skuId": _first(item, ["skuId", "sku", "sku_id"]),
            "spuId": _first(item, ["spuId", "spu", "spu_id"]),
            "erpProductCode": _first(item, ["erpProductCode", "erpCode", "erp_product_code", "商家编码"]),
            "storeName": item.get("storeName") or item.get("store"),
            "platform": _first(item, ["platform", "平台"], "unknown"),
            "title": item.get("title"),
            "shortName": item.get("shortName"),
            "productUrl": _first(item, ["productUrl", "productLink", "link", "url", "商品链接"]),
            "categoryLevel1": _first(item, ["categoryLevel1", "一级类目"]),
            "categoryLevel2": _first(item, ["categoryLevel2", "二级类目"]),
            "categoryLevel3": _first(item, ["categoryLevel3", "三级类目"]),
            "verticalCategory": _first(
                item,
                ["verticalCategory", "vertical_category", "category", "categoryName", "categoryLevel3", "categoryLevel2", "categoryLevel1", "二级类目", "一级类目"],
                "未归类",
            ),
            "priceBand": _first(item, ["priceBand", "price_band", "价格带"], "unknown"),
            "productRole": _first(item, ["productRole", "role", "商品角色"], "regular"),
            "lifecycleStage": _first(item, ["lifecycleStage", "lifecycle", "生命周期"], "unknown"),
            "isHeroProduct": bool(item.get("isHeroProduct") or item.get("hero") or item.get("主推品")),
            "isNewProduct": bool(item.get("isNewProduct") or item.get("new") or item.get("新品")),
            "isCampaignProduct": bool(item.get("isCampaignProduct") or item.get("campaign") or item.get("活动品")),
            "updatedAtFromReport": item.get("updatedAtFromReport"),
            **permission_ref,
        }
    )
    return profile


def _metric(item: Dict[str, Any]) -> Dict[str, Any]:
    permission_ref = _permission_ref(item)
    metric = {
        "objectId": _product_key(item),
        "productId": item.get("productId") or item.get("id"),
        "storeId": item.get("storeId"),
    }
    for name in METRIC_FIELDS:
        metric[name] = item.get(name)
    if metric.get("roas") in {None, "", "—", "未识别"}:
        metric["roas"] = item.get("roi")
    metric.update(
        {
            "metricDate": item.get("metricDate"),
            "reportDate": item.get("reportDate"),
            "dataDate": item.get("dataDate"),
            "updatedAtFromReport": item.get("updatedAtFromReport"),
            "sourceDataVersions": list(item.get("sourceDataVersions") or []),
            "sourceDatasets": list(item.get("sourceDatasets") or []),
            "metricFacts": list(item.get("productMetricFacts") or item.get("metricFacts") or []),
            "productMetricFacts": list(item.get("productMetricFacts") or []),
            "trafficSourceFacts": list(item.get("trafficSourceFacts") or []),
            "metricFactSummary": dict(item.get("metricFactSummary") or {}),
            **permission_ref,
        }
    )
    metric["factNamespace"] = {
        "productMetricFacts": len(metric["productMetricFacts"]),
        "trafficSourceFacts": len(metric["trafficSourceFacts"]),
        "rule": "product metrics and traffic source metrics are separate namespaces.",
    }
    return metric


def _all_facts(metric: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        fact
        for fact in [*(metric.get("productMetricFacts") or []), *(metric.get("trafficSourceFacts") or [])]
        if isinstance(fact, dict)
    ]


def _fact_hash(fact: Dict[str, Any]) -> str:
    for key in ("sourceHash", "source_hash", "factHash", "fact_hash", "hash"):
        if fact.get(key):
            return str(fact[key])
    stable = {key: value for key, value in fact.items() if key not in VOLATILE_FACT_FIELDS}
    return stable_hash(stable)


def _fact_hash_refs(metric: Dict[str, Any]) -> List[str]:
    return _unique(_fact_hash(fact) for fact in _all_facts(metric))


def _fact_id_refs(metric: Dict[str, Any]) -> List[str]:
    return _unique(
        _first(fact, ["factId", "fact_id", "metricFactId", "metric_fact_id", "id"])
        for fact in _all_facts(metric)
    )


def _source_report_refs(item: Dict[str, Any], metric: Dict[str, Any]) -> List[str]:
    direct = item.get("sourceReportRefs") or item.get("source_report_refs") or []
    if not isinstance(direct, list):
        direct = [direct]
    candidates: List[Any] = list(direct)
    candidates.extend([item.get("sourceReportRef"), item.get("source_report_ref")])
    for fact in _all_facts(metric):
        candidates.extend(
            [
                fact.get("sourceReportRef"),
                fact.get("source_report_ref"),
                fact.get("sourceArtifactRef"),
                fact.get("source_artifact_ref"),
            ]
        )
    return _unique(candidates)


def _source_content_hashes(item: Dict[str, Any], metric: Dict[str, Any]) -> List[str]:
    direct = item.get("sourceContentHashes") or item.get("source_content_hashes") or []
    if not isinstance(direct, list):
        direct = [direct]
    candidates: List[Any] = list(direct)
    candidates.extend([item.get("sourceContentHash"), item.get("source_content_hash")])
    for fact in _all_facts(metric):
        candidates.extend([fact.get("sourceHash"), fact.get("source_hash")])
    return _unique(candidates)


def _source_refs(metric: Dict[str, Any], report_refs: List[str]) -> List[str]:
    refs = [f"dataVersion:{value}" for value in metric.get("sourceDataVersions") or [] if value]
    refs.extend(f"dataset:{value}" for value in metric.get("sourceDatasets") or [] if value)
    refs.extend(f"report:{value}" for value in report_refs)
    return _unique(refs)


def _metric_lineage(metric: Dict[str, Any]) -> List[Dict[str, Any]]:
    lineage: List[Dict[str, Any]] = []
    for fact in _all_facts(metric):
        lineage.append(
            {
                "factId": _first(fact, ["factId", "fact_id", "metricFactId", "metric_fact_id", "id"]),
                "factHash": _fact_hash(fact),
                "metricName": _first(fact, ["metricName", "metric_name", "name"]),
                "level": _first(fact, ["level", "scope", "factLevel", "fact_level"]),
                "sourceRowId": _first(fact, ["sourceRowId", "source_row_id", "rowId", "row_id"]),
                "sourceReportRef": _first(fact, ["sourceReportRef", "source_report_ref", "sourceArtifactRef", "source_artifact_ref"]),
            }
        )
    return lineage


def _completeness(metric: Dict[str, Any]) -> Dict[str, Any]:
    present = [name for name in CORE_METRICS if metric.get(name) not in {None, "", "—", "未识别"}]
    missing = [name for name in CORE_METRICS if name not in present]
    return {
        "requiredMetricCount": len(CORE_METRICS),
        "presentMetricCount": len(present),
        "missingMetrics": missing,
        "complete": not missing,
    }


def _product_fact_contract(item: Dict[str, Any], metric: Dict[str, Any]) -> Dict[str, Any]:
    fact_ids = _fact_id_refs(metric)
    source_versions = _unique(metric.get("sourceDataVersions") or [])
    source_datasets = _unique(metric.get("sourceDatasets") or [])
    source_reports = _source_report_refs(item, metric)
    levels = _unique(_first(fact, ["level", "scope", "factLevel", "fact_level"]) for fact in _all_facts(metric))
    return {
        "contract": "productSnapshot.factContract.v1",
        "scope": "product",
        "allowedLevels": ["product", "traffic_source"],
        "matchedLevels": levels,
        "factRefs": fact_ids,
        "metricCount": len(metric.get("productMetricFacts") or []),
        "groupedMetricCount": len(metric.get("metricFactSummary") or {}),
        "trafficSourceFactCount": len(metric.get("trafficSourceFacts") or []),
        "usesMetricFactIds": True,
        "sourceDataVersion": source_versions[-1] if source_versions else None,
        "sourceDataVersions": source_versions,
        "sourceDataset": source_datasets[-1] if source_datasets else None,
        "sourceDatasets": source_datasets,
        "sourceReportRef": source_reports[-1] if source_reports else None,
        "sourceReportRefs": source_reports,
        "dataProvenance": item.get("dataProvenance") or "materialized_metric_facts",
        "dataSourceMode": item.get("dataSourceMode") or "fact_store",
        "metricLineage": _metric_lineage(metric),
    }


def _canonical_product(item: Dict[str, Any], data_version: str | None) -> Dict[str, Any]:
    permission_ref = _permission_ref(item)
    profile = _profile(item)
    metric = _metric(item)
    fact_hash_refs = _fact_hash_refs(metric)
    fact_id_refs = _fact_id_refs(metric)
    report_refs = _source_report_refs(item, metric)
    content_hashes = _source_content_hashes(item, metric)
    source_versions = _unique(metric.get("sourceDataVersions") or ([data_version] if data_version else []))
    source_datasets = _unique(metric.get("sourceDatasets") or [])
    source_refs = _source_refs(metric, report_refs)
    fact_contract = _product_fact_contract(item, metric)
    base = {
        "schemaVersion": SCHEMA_VERSION,
        "dataVersion": data_version,
        "objectId": profile.get("objectId"),
        "productId": profile.get("productId"),
        "storeId": profile.get("storeId"),
        "platform": profile.get("platform"),
        "productRole": profile.get("productRole"),
        "category": profile.get("verticalCategory"),
        "priceBand": profile.get("priceBand"),
        "lifecycleStage": profile.get("lifecycleStage"),
        "profileSnapshot": profile,
        "metricSnapshot": metric,
        "trafficSourceFacts": metric.get("trafficSourceFacts") or [],
        "productMetricFacts": metric.get("productMetricFacts") or [],
        "metricFactSummary": metric.get("metricFactSummary") or {},
        "factRefs": fact_id_refs,
        "factHashRefs": fact_hash_refs,
        "sourceDataVersions": source_versions,
        "sourceDataVersion": source_versions[-1] if source_versions else data_version,
        "sourceDatasets": source_datasets,
        "sourceDataset": source_datasets[-1] if source_datasets else None,
        "sourceReportRefs": report_refs,
        "sourceReportRef": report_refs[-1] if report_refs else None,
        "sourceContentHashes": content_hashes,
        "sourceContentHash": content_hashes[-1] if content_hashes else None,
        "sourceArtifactRefs": source_refs,
        "freshnessStatus": item.get("freshnessStatus"),
        "freshnessAgeDays": item.get("freshnessAgeDays"),
        "freshnessPolicyDays": item.get("freshnessPolicyDays"),
        "metricDate": metric.get("metricDate"),
        "reportDate": metric.get("reportDate"),
        "dataDate": metric.get("dataDate"),
        "dataProvenance": fact_contract.get("dataProvenance"),
        "dataSourceMode": fact_contract.get("dataSourceMode"),
        "factContract": fact_contract,
        **permission_ref,
        "permissionRef": permission_ref,
        "permissionRequired": True,
        "completeness": _completeness(metric),
    }
    product_hash = stable_hash(base)
    return {
        **base,
        "productSnapshotHash": product_hash,
        "snapshotHash": product_hash,
        "parentSnapshotHash": product_hash,
    }


def build_canonical_product_snapshot_item(item: Dict[str, Any], data_version: str | None = None) -> Dict[str, Any]:
    """Pure builder used by contracts/tests and any non-persistent consumer."""
    return _canonical_product(dict(item or {}), data_version)


def agent_projection(product: Dict[str, Any]) -> Dict[str, Any]:
    parent = product.get("productSnapshotHash") or product.get("snapshotHash")
    profile = dict(product.get("profileSnapshot") or {})
    metric = dict(product.get("metricSnapshot") or {})
    permission_ref = dict(product.get("permissionRef") or {})
    context_seed = {
        "platform": profile.get("platform"),
        "storeName": profile.get("storeName"),
        "verticalCategory": profile.get("verticalCategory"),
        "productRole": profile.get("productRole"),
        "lifecycleStage": profile.get("lifecycleStage"),
        "metricDate": metric.get("metricDate"),
        "roi": metric.get("roi"),
        "roas": metric.get("roas"),
        "adSpend": metric.get("adSpend"),
        "refundRate": metric.get("refundRate"),
        "inventory": metric.get("inventory"),
        "trafficSourceCount": len(metric.get("trafficSourceFacts") or []),
    }
    body = {
        "contract": "canonicalProductSnapshot.agentProjection.v1",
        "objectId": product.get("objectId"),
        "parentSnapshotHash": parent,
        "productSnapshotHash": parent,
        "snapshotHash": parent,
        "dataVersion": product.get("dataVersion"),
        "productId": product.get("productId"),
        "storeId": product.get("storeId"),
        "permissionStampId": product.get("permissionStampId"),
        "permissionGateStatus": product.get("permissionGateStatus"),
        "permissionRef": permission_ref,
        "profileSnapshot": profile,
        "metricSnapshot": metric,
        "profileLayer": profile,
        "metricLayer": metric,
        "factRefs": list(product.get("factRefs") or []),
        "factHashRefs": list(product.get("factHashRefs") or []),
        "sourceArtifactRefs": list(product.get("sourceArtifactRefs") or []),
        "factContract": dict(product.get("factContract") or {}),
        "agentContextSeed": context_seed,
    }
    return {**body, "projectionHash": stable_hash(body)}


def detail_projection(product: Dict[str, Any]) -> Dict[str, Any]:
    parent = product.get("productSnapshotHash") or product.get("snapshotHash")
    profile = dict(product.get("profileSnapshot") or {})
    metric = dict(product.get("metricSnapshot") or {})
    body = {
        **profile,
        **metric,
        "dataVersion": product.get("dataVersion"),
        "productSnapshotHash": parent,
        "snapshotHash": parent,
        "parentSnapshotHash": parent,
        "factRefs": list(product.get("factRefs") or []),
        "factHashRefs": list(product.get("factHashRefs") or []),
        "sourceDataVersions": list(product.get("sourceDataVersions") or []),
        "sourceDatasets": list(product.get("sourceDatasets") or []),
        "sourceReportRefs": list(product.get("sourceReportRefs") or []),
        "sourceArtifactRefs": list(product.get("sourceArtifactRefs") or []),
        "factContract": dict(product.get("factContract") or {}),
        "permissionRef": dict(product.get("permissionRef") or {}),
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


def _set_fact_contract(products: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "version": CANONICAL_PRODUCT_SNAPSHOT_VERSION,
        "contract": "canonicalProductSnapshot.setFactContract.v1",
        "productCount": len(products),
        "withMetricDate": sum(1 for item in products if item.get("metricDate") or (item.get("metricSnapshot") or {}).get("metricDate")),
        "productMetricFactCount": sum(len(item.get("productMetricFacts") or []) for item in products),
        "trafficSourceFactCount": sum(len(item.get("trafficSourceFacts") or []) for item in products),
        "roiSource": "product_metric_facts_only",
        "rule": "建档商品数来自商品经营明细；流量来源仅作为子事实挂载；Agent 与详情页共享同一 productSnapshotHash。",
    }


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

    products = [build_canonical_product_snapshot_item(item, data_version) for item in projected_products(user_id)]
    if data_version:
        filtered: List[Dict[str, Any]] = []
        for item in products:
            versions = (item.get("metricSnapshot") or {}).get("sourceDataVersions") or item.get("sourceDataVersions") or []
            if not versions or data_version in versions:
                filtered.append(item)
        if filtered:
            products = filtered
    products.sort(
        key=lambda item: (
            str(item.get("storeId") or ""),
            str(item.get("productId") or ""),
            str(item.get("objectId") or ""),
        )
    )

    set_seed = {
        "schemaVersion": SCHEMA_VERSION,
        "dataVersion": data_version,
        "productSnapshotHashes": [item["productSnapshotHash"] for item in products],
    }
    set_hash = stable_hash(set_seed)
    profile_snapshots = [item.get("profileSnapshot") for item in products]
    metric_snapshots = [item.get("metricSnapshot") for item in products]
    permission_refs = [item.get("permissionRef") for item in products]
    agent_packages = [agent_projection(item) for item in products]
    detail_projections = [detail_projection(item) for item in products]
    fact_contract = _set_fact_contract(products)
    source_versions = _unique(value for item in products for value in item.get("sourceDataVersions") or [])
    source_datasets = _unique(value for item in products for value in item.get("sourceDatasets") or [])
    source_reports = _unique(value for item in products for value in item.get("sourceReportRefs") or [])
    source_hashes = _unique(value for item in products for value in item.get("sourceContentHashes") or [])
    payload = {
        "version": CANONICAL_PRODUCT_SNAPSHOT_VERSION,
        "schemaVersion": SCHEMA_VERSION,
        "snapshotId": snapshot_id,
        "dataVersion": data_version,
        "stationId": "canonical_product_snapshot_station",
        "legacyStationAlias": "system_product_snapshot_station",
        "setSnapshotHash": set_hash,
        "snapshotHash": set_hash,
        "productCount": len(products),
        "products": products,
        "profileSnapshots": profile_snapshots,
        "metricSnapshots": metric_snapshots,
        "permissionRefs": permission_refs,
        "agentProductSnapshotPackages": agent_packages,
        "detailProjections": detail_projections,
        "factContract": fact_contract,
        "profileFieldSet": PROFILE_FIELDS,
        "metricFieldSet": METRIC_FIELDS,
        "permissionFieldSet": PERMISSION_REF_FIELDS,
        "sourceDataVersions": source_versions,
        "sourceDataVersion": source_versions[-1] if source_versions else data_version,
        "sourceDatasets": source_datasets,
        "sourceDataset": source_datasets[-1] if source_datasets else None,
        "sourceReportRefs": source_reports,
        "sourceReportRef": source_reports[-1] if source_reports else None,
        "sourceContentHashes": source_hashes,
        "sourceContentHash": source_hashes[-1] if source_hashes else None,
        "source": "module_projection_service.projected_products_to_canonical_snapshot",
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
        "permissionRefCount": len(permission_refs),
        "factContract": fact_contract,
    }


def materialize_system_product_snapshot(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    force: bool = True,
) -> Dict[str, Any]:
    """Compatibility name; canonical snapshot is the only persistent source."""
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
    "build_canonical_product_snapshot_item",
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
