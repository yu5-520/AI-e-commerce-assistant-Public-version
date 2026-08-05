"""V16.4 data-driven module projection service.

V16.4 fixes the real-report fact layer:

- traffic source rows are child facts, never product master rows;
- product metrics, store metrics and traffic-source metrics use separate scopes;
- product detail ROI comes from product_metric rows only, not traffic ROI=0;
- business dates come from report rows: 统计日期 -> 更新时间 -> filename/dataVersion;
- product master key is platform + store + productId + skuId.
"""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from sqlite3 import OperationalError
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.account_service import current_user, list_stores, visible_store_ids_for_user
from src.services.backend_isolation_service import DEFAULT_ORG_ID, DEFAULT_TENANT_ID, row_scope_status, strict_data_scope_enabled
from src.services.import_row_store_service import load_import_rows
from src.services.metric_catalog_service import (
    display_short_title,
    extract_metric_facts,
    format_metric,
    metric_value,
    pick,
    product_identity,
    stable_code,
    system_codes,
)
from src.services.module_data_service import REPORT_GROUPS
from src.services.permission_stamp_service import permission_stamp_allows, row_permission_stamp

PROJECTION_VERSION = "16.4"
DATASET_LABELS = {"products": "商品报表", "inventory": "库存报表", "orders": "订单报表", "refunds": "退款报表", "customers": "客户报表"}
DATASET_SOURCE = {"products": "ERP", "inventory": "ERP", "orders": "订单报表", "refunds": "CRM", "customers": "CRM"}
BLANK_VALUES = {None, "", "—", "未识别"}
PRODUCT_METRIC_SCOPE = "product"
STORE_METRIC_SCOPE = "store"
TRAFFIC_METRIC_SCOPE = "traffic_source"
PRODUCT_SHEET_KEYWORDS = ["商品经营", "商品明细", "商品", "SKU"]
STORE_SHEET_KEYWORDS = ["店铺经营", "店铺汇总", "经营汇总", "经营单元"]
TRAFFIC_SHEET_KEYWORDS = ["流量来源", "流量", "渠道"]
PRODUCT_DISPLAY_MAPPING = {
    "inventory": "inventory_qty",
    "sellableDays": "sellable_days",
    "avgOrderValue": "avg_order_value",
    "price": "avg_order_value",
    "paymentAmount": "payment_amount",
    "cost": "product_cost_amount",
    "costAmount": "product_cost_amount",
    "grossProfitAmount": "gross_profit_amount",
    "grossMargin": "gross_margin_rate",
    "roi": "roi",
    "roas": "roi",
    "clickRate": "click_rate",
    "conversionRate": "payment_conversion_rate",
    "refundRate": "refund_rate",
    "adSpend": "ad_spend",
    "organicVisitors": "organic_visitor_count",
    "paidVisitors": "paid_visitor_count",
}


def _pick(row: Dict[str, Any], *fields: str, default: Any = None) -> Any:
    for field in fields:
        if row.get(field) not in {None, ""}:
            return row.get(field)
    return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value in {None, ""}:
        return default
    text = str(value).replace(",", "").replace("%", "").replace("¥", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _store_index() -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    for store in list_stores():
        mapping[store["id"]] = store
        mapping[store["name"]] = store
        mapping[f"{store.get('platform')} · {store.get('name')}"] = store
    return mapping


def _resolve_store_id(row: Dict[str, Any]) -> str | None:
    ident = product_identity(row)
    explicit = str(ident.get("storeId") or "").strip()
    if explicit:
        return explicit
    name = str(ident.get("storeName") or "").strip()
    mapping = _store_index()
    if name and name in mapping:
        return mapping[name]["id"]
    return None


def _store_name(store_id: str | None) -> str:
    store = _store_index().get(store_id or "")
    return store.get("name") if store else (store_id or "未绑定店铺")


def _store_platform(store_id: str | None, fallback: str = "导入数据") -> str:
    store = _store_index().get(store_id or "")
    return store.get("platform") if store else fallback


def _visible_store_ids(user_id: str | None) -> set[str]:
    return set(visible_store_ids_for_user(user_id)) if user_id else set()


def _snapshot_payloads() -> List[Dict[str, Any]]:
    try:
        with connect() as conn:
            rows = conn.execute("SELECT payload FROM data_snapshots ORDER BY created_at ASC").fetchall()
    except OperationalError:
        return []
    return [payload for row in rows if (payload := loads(row["payload"]))]


def latest_snapshots_by_dataset() -> Dict[str, Dict[str, Any]]:
    latest: Dict[str, Dict[str, Any]] = {}
    for payload in _snapshot_payloads():
        dataset = payload.get("datasetName")
        if dataset:
            latest[dataset] = payload
    return latest


def _snapshot_rows(dataset_name: str | None = None) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for dataset, payload in latest_snapshots_by_dataset().items():
        if dataset_name and dataset != dataset_name:
            continue
        source_rows = payload.get("rows") or payload.get("sampleRows") or []
        if not isinstance(source_rows, list):
            continue
        for row in source_rows:
            if not isinstance(row, dict):
                continue
            item = {str(key): value for key, value in row.items()}
            item.setdefault("dataVersion", payload.get("dataVersion"))
            item.setdefault("datasetName", payload.get("datasetName"))
            store_id = _resolve_store_id(item)
            if store_id:
                item.setdefault("storeId", store_id)
            rows.append(item)
    return rows


def _raw_dataset_rows(dataset_name: str | None = None) -> List[Dict[str, Any]]:
    full_rows = load_import_rows(dataset_name)
    return full_rows if full_rows else _snapshot_rows(dataset_name)


def _scope_decision(row: Dict[str, Any], store_id: str | None = None) -> Dict[str, Any]:
    return row_scope_status(row, tenant_id=DEFAULT_TENANT_ID, org_id=DEFAULT_ORG_ID, store_id=store_id, require_store=True)


def _row_visible(row: Dict[str, Any], user_id: str | None) -> bool:
    store_id = row.get("storeId") or row.get("store_id") or _resolve_store_id(row)
    if store_id:
        row.setdefault("storeId", store_id)
    if strict_data_scope_enabled():
        decision = _scope_decision(row, store_id)
        if decision.get("status") != "ok":
            row["scopeStatus"] = "quarantined"
            row["scopeMissing"] = decision.get("missing", [])
            row["scopeErrors"] = decision.get("errors", [])
            return False
    if not user_id:
        return True
    role = current_user(user_id).get("roleId")
    if role in {"owner", "manager", "finance"}:
        return True
    if permission_stamp_allows(row, user_id, role):
        row["permissionStampAccepted"] = True
        return True
    if not store_id:
        return True
    return store_id in _visible_store_ids(user_id)


def dataset_rows(dataset_name: str | None = None, user_id: str | None = None) -> List[Dict[str, Any]]:
    rows = _raw_dataset_rows(dataset_name)
    return [row for row in rows if _row_visible(row, user_id)]


def quarantined_dataset_rows(dataset_name: str | None = None) -> List[Dict[str, Any]]:
    rows = _raw_dataset_rows(dataset_name)
    quarantined: List[Dict[str, Any]] = []
    for row in rows:
        store_id = row.get("storeId") or row.get("store_id") or _resolve_store_id(row)
        decision = _scope_decision(row, store_id)
        if decision.get("status") != "ok":
            item = dict(row)
            item["scopeDecision"] = decision
            quarantined.append(item)
    return quarantined


def has_runtime_data(user_id: str | None = None) -> bool:
    return bool(dataset_rows(user_id=user_id))


def _source_sheet(row: Dict[str, Any]) -> str:
    return str(row.get("__source_sheet") or row.get("sourceSheet") or row.get("sheetName") or row.get("datasetName") or "").strip()


def _sheet_has(row: Dict[str, Any], keywords: List[str]) -> bool:
    sheet = _source_sheet(row)
    return any(keyword.lower() in sheet.lower() or keyword in sheet for keyword in keywords)


def _metric_scope(row: Dict[str, Any]) -> str:
    if pick(row, "traffic_source") or _sheet_has(row, TRAFFIC_SHEET_KEYWORDS):
        return TRAFFIC_METRIC_SCOPE
    ident = product_identity(row)
    has_product = bool(ident.get("productId") or ident.get("skuId") or ident.get("erpProductCode") or ident.get("productLink"))
    if _sheet_has(row, STORE_SHEET_KEYWORDS) and not has_product:
        return STORE_METRIC_SCOPE
    if has_product or _sheet_has(row, PRODUCT_SHEET_KEYWORDS):
        return PRODUCT_METRIC_SCOPE
    return STORE_METRIC_SCOPE if ident.get("storeId") or ident.get("storeName") else "unknown"


def _product_id(row: Dict[str, Any]) -> str:
    ident = product_identity(row)
    return str(ident.get("productId") or ident.get("skuId") or ident.get("erpProductCode") or ident.get("productLink") or "").strip()


def _product_key(row: Dict[str, Any], store_id: str | None) -> str:
    ident = product_identity(row)
    platform = str(ident.get("platform") or _store_platform(store_id)).strip() or "unknown"
    product_id = _product_id(row) or "PRODUCT"
    sku = str(ident.get("skuId") or "NO-SKU").strip() or "NO-SKU"
    return f"{platform}::{store_id or ident.get('storeName') or 'GLOBAL'}::{product_id}::{sku}"


def _fmt(row: Dict[str, Any], metric_code: str) -> str:
    return format_metric(metric_code, metric_value(row, metric_code))


def _iter_date_values(value: Any) -> List[str]:
    result: List[str] = []
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result.extend(_iter_date_values(item))
    elif isinstance(value, dict):
        for item in value.values():
            result.extend(_iter_date_values(item))
    elif value not in BLANK_VALUES:
        result.append(str(value))
    return result


def _fmt_date_from_text(value: Any, *, dotted: bool = False) -> str | None:
    for raw in _iter_date_values(value):
        text = raw.strip()
        for pattern in [r"(20\d{2})[-_./年](\d{1,2})[-_./月](\d{1,2})", r"(20\d{2})(\d{2})(\d{2})"]:
            match = re.search(pattern, text)
            if match:
                y, m, d = match.group(1), int(match.group(2)), int(match.group(3))
                return f"{y}.{m}.{d}" if dotted else f"{y}-{m:02d}-{d:02d}"
    return None


def _row_report_date(row: Dict[str, Any]) -> str | None:
    ident = product_identity(row)
    candidates = [ident.get("statDate"), row.get("统计日期"), row.get("stat_date"), row.get("数据日期"), row.get("报表日期"), row.get("日期"), row.get("更新时间"), row.get("updatedAt"), row.get("dataVersion"), row.get("fileName"), row.get("filename"), row.get("sourceFile")]
    for value in candidates:
        parsed = _fmt_date_from_text(value)
        if parsed:
            return parsed
    return None


def _row_report_datetime(row: Dict[str, Any]) -> str | None:
    for key in ["更新时间", "updatedAt", "updateTime", "数据更新时间"]:
        value = row.get(key)
        if value not in BLANK_VALUES:
            return str(value)
    return None


def _ensure_product(products: Dict[str, Dict[str, Any]], row: Dict[str, Any], store_id: str | None) -> Dict[str, Any]:
    product_id = _product_id(row)
    key = _product_key(row, store_id)
    ident = product_identity(row)
    codes = system_codes(row)
    stamp = row_permission_stamp(row)
    if key not in products:
        title = str(pick(row, "product_name", default=f"导入商品 {product_id}") or f"导入商品 {product_id}")
        products[key] = {
            "id": product_id,
            "objectId": key,
            "productId": product_id,
            "skuId": ident.get("skuId"),
            "erpProductCode": ident.get("erpProductCode"),
            "productLink": ident.get("productLink"),
            "systemStoreCode": codes.get("systemStoreCode"),
            "systemSpuCode": codes.get("systemSpuCode"),
            "systemLinkCode": codes.get("systemLinkCode"),
            "systemSkuCode": codes.get("systemSkuCode"),
            "storeId": store_id,
            "storeName": ident.get("storeName") or _store_name(store_id),
            "shortName": display_short_title(row, fallback=product_id),
            "title": title,
            "platform": ident.get("platform") or _store_platform(store_id),
            "store": ident.get("storeName") or _store_name(store_id),
            "imageLabel": "品",
            "link": ident.get("productLink") or "",
            "inventory": "—",
            "inventoryStatus": "待导入库存",
            "inventoryLevel": "good",
            "price": "—",
            "avgOrderValue": "—",
            "paymentAmount": "—",
            "cost": "—",
            "costAmount": "—",
            "grossProfitAmount": "—",
            "grossMargin": "—",
            "roi": "—",
            "roas": "—",
            "clickRate": "—",
            "conversionRate": "—",
            "refundRate": "—",
            "adSpend": "—",
            "organicVisitors": "—",
            "paidVisitors": "—",
            "afterSales": "标签观察",
            "afterSalesLevel": "good",
            "suggestion": "V16.4商品档案：商品经营明细建主档，流量来源只作子事实，任务由真实Agent闸门处理。",
            "sourceDataVersions": [],
            "sourceDatasets": [],
            "metricFacts": [],
            "productMetricFacts": [],
            "trafficSourceFacts": [],
            "storeMetricFacts": [],
            "metricDate": None,
            "reportDate": None,
            "dataDate": None,
            "updatedAtFromReport": None,
            "permissionStamp": stamp,
            "permissionStampId": stamp.get("permissionStampId"),
            "uploadedByUserId": stamp.get("uploadedByUserId"),
            "ownerUserId": stamp.get("ownerUserId"),
            "assignedOperatorId": stamp.get("assignedOperatorId"),
            "visibleUserIds": stamp.get("visibleUserIds") or [],
            "permissionSource": stamp.get("permissionSource"),
            "importBatchId": stamp.get("importBatchId"),
            "metricScope": PRODUCT_METRIC_SCOPE,
            "factLayerVersion": PROJECTION_VERSION,
        }
    return products[key]


def _apply_metric_display(item: Dict[str, Any], row: Dict[str, Any]) -> None:
    if _metric_scope(row) != PRODUCT_METRIC_SCOPE:
        return
    for target, metric_code in PRODUCT_DISPLAY_MAPPING.items():
        value = _fmt(row, metric_code)
        if value != "—":
            item[target] = value
    report_date = _row_report_date(row)
    if report_date:
        item["metricDate"] = report_date
        item["reportDate"] = report_date
        item["dataDate"] = report_date
    report_datetime = _row_report_datetime(row)
    if report_datetime:
        item["updatedAtFromReport"] = report_datetime
    if item.get("inventory") != "—":
        item["inventoryStatus"] = "已入库"
    if item.get("refundRate") and item["refundRate"] != "—":
        item["afterSales"] = f"退款率 {item['refundRate']}"


def _metric_facts_with_scope(row: Dict[str, Any], scope: str) -> List[Dict[str, Any]]:
    facts = []
    metric_date = _row_report_date(row)
    for fact in extract_metric_facts(row):
        facts.append({**fact, "metricScope": scope, "metricDate": metric_date, "statDate": metric_date, "dataDate": metric_date, "sourceSheet": _source_sheet(row), "sourceRowIndex": row.get("__source_row_index")})
    return facts


def _traffic_fact(row: Dict[str, Any], product: Dict[str, Any] | None = None) -> Dict[str, Any]:
    ident = product_identity(row)
    return {
        "metricScope": TRAFFIC_METRIC_SCOPE,
        "trafficSource": str(pick(row, "traffic_source", default="报表数据") or "报表数据"),
        "productId": _product_id(row),
        "skuId": ident.get("skuId"),
        "storeId": row.get("storeId") or _resolve_store_id(row),
        "storeName": ident.get("storeName") or ((product or {}).get("storeName")),
        "platform": ident.get("platform") or ((product or {}).get("platform")),
        "visitorCount": _fmt(row, "visitor_count"),
        "pageViewCount": _fmt(row, "page_view_count"),
        "clickUserCount": _fmt(row, "click_user_count"),
        "clickRate": _fmt(row, "click_rate"),
        "paymentBuyerCount": _fmt(row, "payment_buyer_count"),
        "paymentAmount": _fmt(row, "payment_amount"),
        "conversionRate": _fmt(row, "payment_conversion_rate"),
        "adSpend": _fmt(row, "ad_spend"),
        "roi": _fmt(row, "roi"),
        "metricDate": _row_report_date(row),
        "updatedAtFromReport": _row_report_datetime(row),
        "sourceSheet": _source_sheet(row),
        "sourceRowIndex": row.get("__source_row_index"),
        "rule": "traffic_source_facts are child facts; they never override product_metric_facts.roi.",
    }


def projected_products(user_id: str | None = None) -> List[Dict[str, Any]]:
    products: Dict[str, Dict[str, Any]] = {}
    traffic_rows_by_key: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in dataset_rows(user_id=user_id):
        product_id = _product_id(row)
        if not product_id:
            continue
        store_id = row.get("storeId") or _resolve_store_id(row)
        scope = _metric_scope(row)
        key = _product_key(row, store_id)
        if scope == TRAFFIC_METRIC_SCOPE:
            traffic_rows_by_key[key].append(row)
            continue
        if scope != PRODUCT_METRIC_SCOPE:
            continue
        item = _ensure_product(products, row, store_id)
        data_version = row.get("dataVersion")
        dataset = row.get("datasetName")
        if data_version and data_version not in item["sourceDataVersions"]:
            item["sourceDataVersions"].append(data_version)
        if dataset and dataset not in item["sourceDatasets"]:
            item["sourceDatasets"].append(dataset)
        _apply_metric_display(item, row)
        for fact in _metric_facts_with_scope(row, PRODUCT_METRIC_SCOPE):
            fact_key = (fact.get("metricCode"), fact.get("metricDate"), fact.get("sourceSheet"))
            seen = {(f.get("metricCode"), f.get("metricDate"), f.get("sourceSheet")) for f in item.get("productMetricFacts", [])}
            if fact_key not in seen:
                item["productMetricFacts"].append(fact)
                item["metricFacts"].append(fact)
    for key, rows in traffic_rows_by_key.items():
        product = products.get(key)
        if not product:
            continue
        seen_sources = {(fact.get("trafficSource"), fact.get("metricDate")) for fact in product.get("trafficSourceFacts", [])}
        for row in rows:
            fact = _traffic_fact(row, product)
            fkey = (fact.get("trafficSource"), fact.get("metricDate"))
            if fkey not in seen_sources:
                product["trafficSourceFacts"].append(fact)
                seen_sources.add(fkey)
    for item in products.values():
        item["metricFactSummary"] = {"factCount": len(item.get("productMetricFacts") or []), "trafficSourceFactCount": len(item.get("trafficSourceFacts") or []), "metricDate": item.get("metricDate"), "productMetricScopeOnly": True, "missingFields": [field for field in ["paymentAmount", "roi", "refundRate", "inventory"] if item.get(field) in BLANK_VALUES], "rule": "product master count comes only from product_metric_detail rows; traffic rows are attached child facts."}
    return sorted((deepcopy(item) for item in products.values()), key=lambda item: (item.get("storeId") or "", item.get("productId") or "", item.get("skuId") or ""))


def projected_traffic(user_id: str | None = None) -> List[Dict[str, Any]]:
    cards: Dict[str, Dict[str, Any]] = {}
    products = {_product_key(item, item.get("storeId")): item for item in projected_products(user_id)}
    for row in dataset_rows(user_id=user_id):
        if _metric_scope(row) != TRAFFIC_METRIC_SCOPE:
            continue
        product_id = _product_id(row)
        if not product_id:
            continue
        store_id = row.get("storeId") or _resolve_store_id(row)
        key = _product_key(row, store_id)
        product = products.get(key) or _ensure_product({}, row, store_id)
        source = str(pick(row, "traffic_source", default="报表数据") or "报表数据")
        card = cards.setdefault(key, {
            "id": f"TR-{store_id or 'GLOBAL'}-{product_id}",
            "storeId": store_id,
            "productId": product_id,
            "title": product.get("title") or f"导入商品 {product_id}",
            "platform": product.get("platform") or _store_platform(store_id),
            "store": product.get("store") or _store_name(store_id),
            "imageLabel": "流",
            "channel": source,
            "source": "报表导入",
            "exposure": _fmt(row, "visitor_count"),
            "ctr": _fmt(row, "click_rate"),
            "conversion": _fmt(row, "payment_conversion_rate"),
            "roi": _fmt(row, "roi"),
            "refundRate": _fmt(row, "refund_rate"),
            "inventory": product.get("inventory") or "—",
            "status": "观察",
            "statusLevel": "warning",
            "backflow": "流量承接复查",
            "nextStep": "流量来源明细只作为交叉验证证据，正式动作由任务闸门决定。",
            "link": product.get("link") or "",
            "trafficSources": [],
            "trafficSourceFacts": [],
            "metricDate": _row_report_date(row),
        })
        fact = _traffic_fact(row, product)
        if source not in card["trafficSources"]:
            card["trafficSources"].append(source)
        if fact not in card["trafficSourceFacts"]:
            card["trafficSourceFacts"].append(fact)
        if card["roi"] != "—":
            card["status"] = "流量结构已入库"
            card["statusLevel"] = "good"
    return list(cards.values())


def projected_report_groups(user_id: str | None = None) -> List[Dict[str, Any]]:
    latest = latest_snapshots_by_dataset()
    groups: List[Dict[str, Any]] = []
    for group in REPORT_GROUPS:
        next_group = {**group, "reports": []}
        for report in group.get("reports", []):
            dataset = report["id"]
            payload = latest.get(dataset)
            rows = dataset_rows(dataset, user_id=user_id)
            next_group["reports"].append({**report, "status": "已导入" if payload else "待导入", "count": f"{len(rows)} 条", "latestDataVersion": payload.get("dataVersion") if payload else None, "latestSnapshotAt": payload.get("createdAt") if payload else None, "source": DATASET_SOURCE.get(dataset, report.get("source", "报表"))})
        groups.append(next_group)
    return groups


def projected_report_details(user_id: str | None = None) -> Dict[str, Dict[str, Any]]:
    details: Dict[str, Dict[str, Any]] = {}
    for dataset, payload in latest_snapshots_by_dataset().items():
        rows = dataset_rows(dataset, user_id=user_id)
        headers: List[str] = []
        for row in rows[:20]:
            for key in row.keys():
                if key not in {"dataVersion", "datasetName"} and key not in headers:
                    headers.append(key)
        details[dataset] = {"title": DATASET_LABELS.get(dataset, dataset), "source": DATASET_SOURCE.get(dataset, "导入"), "summary": [["数据行", str(len(rows))], ["数据版本", payload.get("dataVersion") or "—"], ["店铺数", str(len({row.get("storeId") for row in rows if row.get("storeId")}))]], "columns": headers[:12], "rows": [[str(row.get(header, "")) for header in headers[:12]] for row in rows[:50]], "dataVersion": payload.get("dataVersion"), "createdAt": payload.get("createdAt")}
    return details


def projection_summary(user_id: str | None = None) -> Dict[str, Any]:
    latest = latest_snapshots_by_dataset()
    latest_payload = list(latest.values())[-1] if latest else None
    products = projected_products(user_id)
    traffic = projected_traffic(user_id)
    reports = projected_report_groups(user_id)
    quarantined = quarantined_dataset_rows() if strict_data_scope_enabled() else []
    metric_fact_count = sum(len(item.get("metricFacts") or []) for item in products)
    traffic_fact_count = sum(len(item.get("trafficSourceFacts") or []) for item in products)
    return {"version": PROJECTION_VERSION, "hasData": bool(latest_payload or products or traffic), "latestDataVersion": latest_payload.get("dataVersion") if latest_payload else None, "latestDatasetName": latest_payload.get("datasetName") if latest_payload else None, "latestSnapshotAt": latest_payload.get("createdAt") if latest_payload else None, "productCount": len(products), "trafficCardCount": len(traffic), "reportCount": sum(len(group.get("reports", [])) for group in reports), "dataVersionCount": len(latest), "metricFactCount": metric_fact_count, "trafficSourceFactCount": traffic_fact_count, "scopedStoreIds": sorted(_visible_store_ids(user_id)), "strictDataScope": strict_data_scope_enabled(), "quarantinedRowCount": len(quarantined), "permissionStampProjection": True, "factLayerVersion": PROJECTION_VERSION, "rule": "V16.4 product facts, store facts and traffic-source facts are isolated before fullProductBundle."}
