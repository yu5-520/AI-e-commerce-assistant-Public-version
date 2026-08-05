"""V16.4 system product snapshot service.

Product snapshots carry product profile, product-scope metrics, child traffic
facts and report business dates. Traffic-source ROI never overwrites product ROI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.module_projection_service import projected_products
from src.services.permission_stamp_service import row_permission_stamp

SYSTEM_PRODUCT_SNAPSHOT_VERSION = "16.4"
PROFILE_FIELDS = ["objectId", "productId", "skuId", "spuId", "erpProductCode", "storeId", "storeName", "platform", "title", "shortName", "productUrl", "categoryLevel1", "categoryLevel2", "categoryLevel3", "verticalCategory", "priceBand", "productRole", "lifecycleStage", "isHeroProduct", "isNewProduct", "isCampaignProduct", "metricDate", "reportDate", "dataDate"]
METRIC_FIELDS = ["roas", "roi", "adSpend", "paymentAmount", "grossMargin", "clickRate", "conversionRate", "refundRate", "inventory", "sellableDays", "organicVisitors", "paidVisitors", "inventoryStatus", "afterSales"]
PERMISSION_REF_FIELDS = ["permissionStampId", "permissionGateStatus", "permissionScopeRef"]


def now_iso() -> str:
    return datetime.now().isoformat()


def ensure_product_snapshot_tables() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_product_snapshots_v14 (
                snapshot_id TEXT PRIMARY KEY,
                data_version TEXT,
                product_count INTEGER DEFAULT 0,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        ensure_columns(conn, "system_product_snapshots_v14", {"data_version": "TEXT", "product_count": "INTEGER DEFAULT 0", "updated_at": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_system_product_snapshot_v14_version ON system_product_snapshots_v14(data_version, created_at)")
        conn.commit()


def snapshot_id_for(data_version: str | None) -> str:
    return f"PRODUCT-SNAPSHOT-{data_version or 'latest'}"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first(item: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in {None, "", "—", "未识别"}:
            return value
    return default


def _product_key(item: Dict[str, Any]) -> str:
    return _text(item.get("objectId")) or f"{_text(item.get('platform')) or 'unknown'}::{_text(item.get('storeId')) or 'GLOBAL'}::{_text(item.get('productId') or item.get('id'))}::{_text(item.get('skuId')) or 'NO-SKU'}"


def _infer_vertical_category(item: Dict[str, Any]) -> str:
    return _text(_first(item, ["verticalCategory", "vertical_category", "category", "categoryName", "categoryLevel3", "categoryLevel2", "categoryLevel1", "category_name", "二级类目", "一级类目"], "未归类")) or "未归类"


def _permission_ref(item: Dict[str, Any]) -> Dict[str, Any]:
    stamp = row_permission_stamp(item)
    stamp_id = stamp.get("permissionStampId") or item.get("permissionStampId")
    return {"permissionStampId": stamp_id, "permissionGateStatus": "passed" if stamp_id else "quarantine", "permissionScopeRef": f"permission_stamp:{stamp_id}" if stamp_id else "permission_stamp:missing"}


def _profile_snapshot(item: Dict[str, Any]) -> Dict[str, Any]:
    profile = {"objectId": _product_key(item), "productId": item.get("productId") or item.get("id"), "skuId": _first(item, ["skuId", "sku", "sku_id"]), "spuId": _first(item, ["spuId", "spu", "spu_id"]), "erpProductCode": _first(item, ["erpProductCode", "erpCode", "erp_product_code", "商家编码"]), "storeId": item.get("storeId"), "storeName": item.get("storeName") or item.get("store"), "platform": _first(item, ["platform", "平台"], "unknown"), "title": item.get("title"), "shortName": item.get("shortName"), "productUrl": _first(item, ["productUrl", "productLink", "link", "url", "商品链接"]), "categoryLevel1": _first(item, ["categoryLevel1", "一级类目"]), "categoryLevel2": _first(item, ["categoryLevel2", "二级类目"]), "categoryLevel3": _first(item, ["categoryLevel3", "三级类目"]), "verticalCategory": _infer_vertical_category(item), "priceBand": _first(item, ["priceBand", "price_band", "价格带"], "unknown"), "productRole": _first(item, ["productRole", "role", "商品角色"], "regular"), "lifecycleStage": _first(item, ["lifecycleStage", "lifecycle", "生命周期"], "unknown"), "isHeroProduct": bool(item.get("isHeroProduct") or item.get("hero") or item.get("主推品")), "isNewProduct": bool(item.get("isNewProduct") or item.get("new") or item.get("新品")), "isCampaignProduct": bool(item.get("isCampaignProduct") or item.get("campaign") or item.get("活动品")), "metricDate": item.get("metricDate"), "reportDate": item.get("reportDate"), "dataDate": item.get("dataDate"), "updatedAtFromReport": item.get("updatedAtFromReport")}
    profile.update(_permission_ref(item))
    return profile


def _metric_snapshot(item: Dict[str, Any]) -> Dict[str, Any]:
    metric = {"objectId": _product_key(item), "productId": item.get("productId") or item.get("id"), "storeId": item.get("storeId")}
    for field in METRIC_FIELDS:
        metric[field] = item.get(field)
    if metric.get("roas") in {None, "", "—", "未识别"}:
        metric["roas"] = item.get("roi")
    metric["metricDate"] = item.get("metricDate")
    metric["reportDate"] = item.get("reportDate")
    metric["dataDate"] = item.get("dataDate")
    metric["updatedAtFromReport"] = item.get("updatedAtFromReport")
    metric["sourceDataVersions"] = item.get("sourceDataVersions") or []
    metric["sourceDatasets"] = item.get("sourceDatasets") or []
    metric["metricFacts"] = item.get("productMetricFacts") or item.get("metricFacts") or []
    metric["productMetricFacts"] = item.get("productMetricFacts") or []
    metric["trafficSourceFacts"] = item.get("trafficSourceFacts") or []
    metric["metricFactSummary"] = item.get("metricFactSummary") or {}
    metric["factNamespace"] = {"productMetricFacts": len(metric["productMetricFacts"]), "trafficSourceFacts": len(metric["trafficSourceFacts"]), "rule": "product metrics and traffic source metrics are separate namespaces."}
    metric.update(_permission_ref(item))
    return metric


def _agent_snapshot_package(profile: Dict[str, Any], metric: Dict[str, Any], permission_ref: Dict[str, Any]) -> Dict[str, Any]:
    return {"objectId": profile.get("objectId"), "productId": profile.get("productId"), "storeId": profile.get("storeId"), "permissionStampId": permission_ref.get("permissionStampId"), "permissionGateStatus": permission_ref.get("permissionGateStatus"), "profileSnapshot": profile, "metricSnapshot": metric, "agentContextSeed": {"platform": profile.get("platform"), "storeName": profile.get("storeName"), "verticalCategory": profile.get("verticalCategory"), "productRole": profile.get("productRole"), "lifecycleStage": profile.get("lifecycleStage"), "metricDate": metric.get("metricDate"), "roi": metric.get("roi"), "roas": metric.get("roas"), "adSpend": metric.get("adSpend"), "refundRate": metric.get("refundRate"), "inventory": metric.get("inventory"), "trafficSourceCount": len(metric.get("trafficSourceFacts") or [])}}


def _snapshot_item(item: Dict[str, Any]) -> Dict[str, Any]:
    permission_ref = _permission_ref(item)
    profile = _profile_snapshot(item)
    metric = _metric_snapshot(item)
    return {**profile, **metric, **permission_ref, "profileSnapshot": profile, "metricSnapshot": metric, "permissionRef": permission_ref, "trafficSourceFacts": metric.get("trafficSourceFacts") or [], "productMetricFacts": metric.get("productMetricFacts") or [], "metricFactSummary": metric.get("metricFactSummary") or {}, "agentProductSnapshotPackage": _agent_snapshot_package(profile, metric, permission_ref)}


def row_to_snapshot(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return {**payload, "snapshotId": row["snapshot_id"], "dataVersion": row["data_version"], "productCount": int(row["product_count"] or 0), "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def get_product_snapshot(data_version: str | None = None) -> Dict[str, Any] | None:
    ensure_product_snapshot_tables()
    with connect() as conn:
        if data_version:
            row = conn.execute("SELECT * FROM system_product_snapshots_v14 WHERE data_version = ? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM system_product_snapshots_v14 ORDER BY created_at DESC LIMIT 1").fetchone()
    return row_to_snapshot(row) if row else None


def product_snapshot_history(data_version: str | None = None, *, limit: int = 90) -> List[Dict[str, Any]]:
    ensure_product_snapshot_tables()
    with connect() as conn:
        if data_version:
            rows = conn.execute("SELECT * FROM system_product_snapshots_v14 WHERE data_version != ? ORDER BY created_at DESC LIMIT ?", (data_version, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM system_product_snapshots_v14 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [row_to_snapshot(row) for row in rows]


def previous_product_snapshot(data_version: str | None = None) -> Dict[str, Any] | None:
    history = product_snapshot_history(data_version, limit=1)
    return history[0] if history else None


def _fact_contract(products: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"version": SYSTEM_PRODUCT_SNAPSHOT_VERSION, "productCount": len(products), "withMetricDate": sum(1 for item in products if item.get("metricDate") or (item.get("metricSnapshot") or {}).get("metricDate")), "productMetricFactCount": sum(len(item.get("productMetricFacts") or []) for item in products), "trafficSourceFactCount": sum(len(item.get("trafficSourceFacts") or []) for item in products), "roiSource": "product_metric_facts_only", "rule": "建档商品数来自商品经营明细；流量来源仅作为子事实挂载。"}


def materialize_system_product_snapshot(data_version: str | None = None, *, user_id: str | None = None, force: bool = True) -> Dict[str, Any]:
    ensure_product_snapshot_tables()
    snapshot_id = snapshot_id_for(data_version)
    if not force:
        existing = get_product_snapshot(data_version)
        if existing:
            return {**existing, "idempotentHit": True}
    products = [_snapshot_item(item) for item in projected_products(user_id)]
    if data_version:
        filtered = []
        for item in products:
            versions = (item.get("metricSnapshot") or {}).get("sourceDataVersions") or item.get("sourceDataVersions") or []
            if not versions or data_version in versions:
                filtered.append(item)
        if filtered:
            products = filtered
    profile_snapshots = [item.get("profileSnapshot") for item in products]
    metric_snapshots = [item.get("metricSnapshot") for item in products]
    permission_refs = [item.get("permissionRef") for item in products]
    agent_packages = [item.get("agentProductSnapshotPackage") for item in products]
    fact_contract = _fact_contract(products)
    payload = {"version": SYSTEM_PRODUCT_SNAPSHOT_VERSION, "snapshotId": snapshot_id, "dataVersion": data_version, "stationId": "system_product_snapshot_station", "products": products, "profileSnapshots": profile_snapshots, "metricSnapshots": metric_snapshots, "permissionRefs": permission_refs, "agentProductSnapshotPackages": agent_packages, "productCount": len(products), "factContract": fact_contract, "profileFieldSet": PROFILE_FIELDS, "metricFieldSet": METRIC_FIELDS, "permissionFieldSet": PERMISSION_REF_FIELDS, "source": "module_projection_service.projected_products_v16_4", "rule": "V16.4 snapshot carries product-scope metrics, report business dates and child traffic source facts without ROI overwrite."}
    now = now_iso()
    with connect() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO system_product_snapshots_v14 (snapshot_id, data_version, product_count, payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM system_product_snapshots_v14 WHERE snapshot_id = ?), ?), ?)
        """, (snapshot_id, data_version, len(products), dumps(payload), snapshot_id, now, now))
        conn.commit()
        row = conn.execute("SELECT * FROM system_product_snapshots_v14 WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
    return {"version": SYSTEM_PRODUCT_SNAPSHOT_VERSION, "snapshotId": snapshot_id, "dataVersion": data_version, "productCount": len(products), "productSnapshotRef": f"system_product_snapshot:{snapshot_id}", "outputRef": f"system_product_snapshot:{snapshot_id}", "permissionRefCount": len(permission_refs), "factContract": fact_contract}


def product_snapshot_summary(limit: int = 20) -> Dict[str, Any]:
    ensure_product_snapshot_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM system_product_snapshots_v14 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = [{"snapshotId": row["snapshot_id"], "dataVersion": row["data_version"], "productCount": int(row["product_count"] or 0), "createdAt": row["created_at"], "updatedAt": row["updated_at"]} for row in rows]
    return {"version": SYSTEM_PRODUCT_SNAPSHOT_VERSION, "snapshotCount": len(items), "latest": items[0] if items else None, "items": items}
