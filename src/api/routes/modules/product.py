"""Product module routes backed by V14.8.2 product read bridge.

The product page merges the cached frontend read model with runtime product
projection and displays business data dates instead of engineering cache labels.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, Iterable, List

from fastapi import APIRouter, HTTPException, Query, Request

from src.services.account_service import user_id_from_headers
from src.services.frontend_read_model_service import FRONTEND_READ_MODEL_VERSION, read_product_detail, read_product_views
from src.services.module_projection_service import projected_products
from src.services.task_pool_station_service import enter_task_pool_from_snapshot
from src.services.task_snapshot_station_service import create_task_snapshot

router = APIRouter()
PRODUCT_ARCHIVE_VERSION = "14.8.2"
BLANK_VALUES = {None, "", "—", "未识别"}

METRIC_GROUPS = [
    ("成交与投产", [("paymentAmount", "支付金额"), ("avgOrderValue", "客单价"), ("conversionRate", "支付转化率"), ("roi", "ROI")]),
    ("成本与利润", [("costAmount", "商品成本金额"), ("grossProfitAmount", "毛利金额"), ("grossMargin", "毛利率")]),
    ("流量与广告", [("clickRate", "点击率"), ("adSpend", "广告消耗"), ("organicVisitors", "自然流量访客数"), ("paidVisitors", "付费流量访客数")]),
    ("库存与售后", [("inventory", "库存数量"), ("refundRate", "退款率")]),
]
CORE_FIELDS = ["paymentAmount", "roi", "adSpend", "refundRate", "inventory"]
DATE_FIELDS = ["reportDate", "dataDate", "statDate", "bizDate", "businessDate", "date", "日期", "统计日期", "数据日期", "报表日期"]
UPLOAD_DATE_FIELDS = ["uploadedAt", "createdAt", "importedAt", "uploadTime", "created_at", "updatedAt"]
TEXT_DATE_FIELDS = ["dataVersion", "sourceDataVersions", "sourceFile", "fileName", "filename", "originalFilename", "importBatchId"]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _known(value: Any) -> bool:
    return value not in BLANK_VALUES


def _archive_key(item: Dict[str, Any]) -> str:
    store_id = _text(item.get("storeId") or "GLOBAL") or "GLOBAL"
    product_id = _text(item.get("productId") or item.get("id") or item.get("bundleId") or item.get("objectId")) or "PRODUCT"
    return f"{store_id}::{product_id}"


def _iter_values(value: Any) -> Iterable[str]:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
    elif value not in BLANK_VALUES:
        yield str(value)


def _fmt_date(year: str, month: str, day: str) -> str:
    return f"{int(year)}.{int(month)}.{int(day)}"


def _extract_date(value: Any) -> str | None:
    for text in _iter_values(value):
        raw = text.strip()
        patterns = [
            r"(20\d{2})[-_./年](\d{1,2})[-_./月](\d{1,2})",
            r"(20\d{2})(\d{2})(\d{2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, raw)
            if match:
                return _fmt_date(match.group(1), match.group(2), match.group(3))
    return None


def _resolve_report_date(item: Dict[str, Any]) -> str:
    for field in DATE_FIELDS:
        parsed = _extract_date(item.get(field))
        if parsed:
            return parsed
    for field in TEXT_DATE_FIELDS:
        parsed = _extract_date(item.get(field))
        if parsed:
            return parsed
    for field in UPLOAD_DATE_FIELDS:
        parsed = _extract_date(item.get(field))
        if parsed:
            return parsed
    return datetime.now().strftime("%Y.%-m.%-d") if hasattr(datetime.now(), "strftime") else datetime.now().strftime("%Y.%m.%d")


def _metric_value(item: Dict[str, Any], metrics: Dict[str, Any], field: str) -> Any:
    aliases = {
        "avgOrderValue": ["avgOrderValue", "price"],
        "costAmount": ["costAmount", "cost"],
        "grossProfitAmount": ["grossProfitAmount", "grossProfit"],
        "roi": ["roi", "roas"],
    }
    for key in [field, *(aliases.get(field) or [])]:
        value = item.get(key)
        if _known(value):
            return value
        value = metrics.get(key)
        if _known(value):
            return value
    return "—"


def _metric_sections(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    report_date = item.get("resolvedReportDate") or _resolve_report_date(item)
    sections: List[Dict[str, Any]] = []
    for title, fields in METRIC_GROUPS:
        rows = []
        for field, label in fields:
            value = _metric_value(item, metrics, field)
            if _known(value):
                rows.append({"metricCode": field, "metricName": label, "displayValue": value, "sourceSheet": "数据时间", "statDate": report_date, "dataDate": report_date})
        sections.append({"title": title, "items": rows})
    return sections


def _traffic_facts(item: Dict[str, Any]) -> List[Dict[str, Any]]:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    facts = []
    organic = _metric_value(item, metrics, "organicVisitors")
    paid = _metric_value(item, metrics, "paidVisitors")
    if _known(organic):
        facts.append({"trafficSource": "自然流量", "visitorCount": organic, "clickRate": item.get("clickRate") or "—", "conversionRate": item.get("conversionRate") or "—", "roi": item.get("roi") or "—"})
    if _known(paid):
        facts.append({"trafficSource": "付费流量", "visitorCount": paid, "clickRate": item.get("clickRate") or "—", "conversionRate": item.get("conversionRate") or "—", "roi": item.get("roi") or "—"})
    return facts


def _normalize_existing_sections(item: Dict[str, Any], report_date: str) -> List[Dict[str, Any]]:
    existing_sections = item.get("metricSections") if isinstance(item.get("metricSections"), list) else []
    if not any(section.get("items") for section in existing_sections if isinstance(section, dict)):
        return _metric_sections({**item, "resolvedReportDate": report_date})
    normalized = []
    for section in existing_sections:
        if not isinstance(section, dict):
            continue
        rows = []
        for row in section.get("items") or []:
            if isinstance(row, dict):
                rows.append({**row, "sourceSheet": "数据时间", "statDate": row.get("statDate") if _extract_date(row.get("statDate")) else report_date, "dataDate": row.get("dataDate") or report_date})
        normalized.append({**section, "items": rows})
    return normalized


def _normalize_archive_item(item: Dict[str, Any]) -> Dict[str, Any]:
    metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
    product_id = _text(item.get("productId") or item.get("id") or item.get("bundleId")) or "PRODUCT"
    store_id = _text(item.get("storeId") or "GLOBAL") or "GLOBAL"
    archive_id = f"{store_id}::{product_id}"
    report_date = _resolve_report_date(item)
    normalized = {
        **item,
        "id": archive_id,
        "objectId": item.get("objectId") or archive_id,
        "archiveId": archive_id,
        "productId": product_id,
        "rawProductId": product_id,
        "skuId": item.get("skuId"),
        "spuId": item.get("spuId"),
        "erpProductCode": item.get("erpProductCode"),
        "storeId": store_id,
        "storeName": item.get("storeName") or item.get("store") or store_id,
        "store": item.get("storeName") or item.get("store") or store_id,
        "title": item.get("title") or item.get("shortName") or product_id,
        "shortName": item.get("shortName") or item.get("title") or product_id,
        "platform": item.get("platform") or "导入数据",
        "link": item.get("link") or item.get("productLink") or item.get("productUrl"),
        "productLink": item.get("productLink") or item.get("link") or item.get("productUrl"),
        "inventory": _metric_value(item, metrics, "inventory"),
        "paymentAmount": _metric_value(item, metrics, "paymentAmount"),
        "avgOrderValue": _metric_value(item, metrics, "avgOrderValue"),
        "roas": _metric_value(item, metrics, "roas"),
        "roi": _metric_value(item, metrics, "roi"),
        "adSpend": _metric_value(item, metrics, "adSpend"),
        "clickRate": _metric_value(item, metrics, "clickRate"),
        "conversionRate": _metric_value(item, metrics, "conversionRate"),
        "refundRate": _metric_value(item, metrics, "refundRate"),
        "costAmount": _metric_value(item, metrics, "costAmount"),
        "cost": _metric_value(item, metrics, "costAmount"),
        "grossProfitAmount": _metric_value(item, metrics, "grossProfitAmount"),
        "grossMargin": _metric_value(item, metrics, "grossMargin"),
        "organicVisitors": _metric_value(item, metrics, "organicVisitors"),
        "paidVisitors": _metric_value(item, metrics, "paidVisitors"),
        "resolvedReportDate": report_date,
        "productArchiveVersion": PRODUCT_ARCHIVE_VERSION,
        "readModelVersion": FRONTEND_READ_MODEL_VERSION,
        "sourceRoute": "business-products",
        "readMode": "frontend_product_view_plus_projection_bridge",
    }
    position = item.get("productPosition") if isinstance(item.get("productPosition"), dict) else {}
    normalized["productPosition"] = {
        "systemStoreCode": item.get("systemStoreCode") or position.get("systemStoreCode"),
        "systemSpuCode": item.get("systemSpuCode") or position.get("systemSpuCode"),
        "systemLinkCode": item.get("systemLinkCode") or position.get("systemLinkCode"),
        "systemSkuCode": item.get("systemSkuCode") or position.get("systemSkuCode"),
        "platform": normalized.get("platform"),
        "storeName": normalized.get("storeName"),
        "productId": product_id,
        "skuId": normalized.get("skuId"),
        "erpProductCode": normalized.get("erpProductCode"),
        "productLink": normalized.get("productLink"),
    }
    normalized["metricSections"] = _normalize_existing_sections({**item, **normalized}, report_date)
    normalized["trafficSourceFacts"] = item.get("trafficSourceFacts") if isinstance(item.get("trafficSourceFacts"), list) and item.get("trafficSourceFacts") else _traffic_facts(normalized)
    fact_count = sum(len(section.get("items") or []) for section in normalized["metricSections"] if isinstance(section, dict))
    missing = [field for field in CORE_FIELDS if not _known(normalized.get(field))]
    normalized["metricFactSummary"] = {**(item.get("metricFactSummary") if isinstance(item.get("metricFactSummary"), dict) else {}), "factCount": fact_count, "missingFields": missing, "hasDataGap": bool(missing), "dataDate": report_date}
    normalized["sourceDataVersions"] = item.get("sourceDataVersions") or ([item.get("dataVersion")] if item.get("dataVersion") else [])
    normalized["sourceDatasets"] = item.get("sourceDatasets") or []
    normalized["taskHistorySummary"] = item.get("taskHistorySummary") or {"taskCount": 0, "summary": "商品页只显示任务摘要，完整SOP在任务详情页查看。"}
    return normalized


def _merge_product_items(user_id: str | None, store_id: str | None = None) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    read_result = read_product_views(store_id=store_id, limit=500)
    for item in read_result.get("items") or []:
        normalized = _normalize_archive_item(item)
        merged[_archive_key(normalized)] = normalized
    for item in projected_products(user_id):
        if store_id and _text(item.get("storeId")) != _text(store_id):
            continue
        normalized = _normalize_archive_item(item)
        key = _archive_key(normalized)
        merged[key] = {**merged.get(key, {}), **normalized, "readMode": "projection_overrides_compact_read_model"}
    return sorted(merged.values(), key=lambda row: (row.get("storeName") or "", row.get("productId") or ""))


def product_items(user_id: str | None, store_id: str | None = None, store_name: str | None = None) -> List[Dict[str, Any]]:
    items = _merge_product_items(user_id, store_id=store_id)
    if store_name:
        wanted = _text(store_name)
        items = [item for item in items if wanted in {_text(item.get("storeName")), _text(item.get("store"))}]
    return items


def product_task_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    missing = (item.get("metricFactSummary") or {}).get("missingFields") or []
    priority = "中" if missing else ("高" if item.get("signalStrength") == "high" else "中" if item.get("signalStrength") == "medium" else "低")
    task_type = "商品数据核验" if missing else "商品经营复核"
    task_text = f"补齐商品核心指标：{', '.join(missing)}。提交原始报表、字段映射截图和数据口径。" if missing else "基于商品全量包读模型复核商品状态，提交截图、数据口径和处理结论。"
    return {"entityType": "商品", "entityId": item["id"], "riskDomain": "指标事实补齐" if missing else (item.get("metricCode") or "商品"), "actionType": "data_gap_verification" if missing else "manual_product_review", "sourceModule": "商品模块", "source": "前端读模型人工请求", "sourceRoute": "business-products", "productId": item.get("productId") or item["id"], "objectId": item["id"], "storeIds": [item.get("storeId")] if item.get("storeId") else [], "visibleStoreIds": [item.get("storeId")] if item.get("storeId") else [], "imageLabel": item.get("imageLabel") or "品", "productShort": item.get("shortName") or item.get("title") or item.get("productId") or item["id"], "productTitle": item.get("title") or item.get("productId") or item["id"], "title": f"{task_type}｜{item.get('productId') or item['id']}", "platform": item.get("platform") or "导入数据", "store": item.get("storeName") or item.get("store") or "未绑定店铺", "priority": priority, "priorityLevel": "danger" if priority == "高" else "warning" if priority == "中" else "good", "deadline": "24小时内" if missing else ("今天内" if priority == "高" else "明天前"), "taskType": task_type, "taskSignal": "data_gap_required" if missing else (item.get("primarySignalType") or "前端人工请求"), "task": task_text, "reason": "V14.8.2：缺字段不阻断任务，商品页可直接生成数据核验任务。" if missing else "商品来自 frontend_product_view + projection bridge；页面请求不触发商品投影或Agent重算。", "judgmentTags": [item.get("verticalCategory", "未归类"), f"支付 {item.get('paymentAmount', '—')}", f"退款率 {item.get('refundRate', '—')}", f"库存 {item.get('inventory', '—')}"] + ([f"缺失 {len(missing)} 字段"] if missing else []), "sourceDataVersions": item.get("sourceDataVersions") or [], "sourceDatasets": item.get("sourceDatasets") or ["frontend_product_view", "module_projection"]}


def _enter_snapshot_pool(payload: Dict[str, Any], user_id: str | None = None) -> Dict[str, Any]:
    entity_id = payload.get("entityId") or payload.get("productId")
    source_version = (payload.get("sourceDataVersions") or [None])[0]
    priority = payload.get("priority") or "中"
    decision = "manager_review_required" if priority == "高" else "create_task_snapshot"
    task_plan = {"title": payload.get("title") or entity_id, "subtitle": "manual_product_read_model_request_v14_8_2", "entityType": payload.get("entityType") or "商品", "entityId": entity_id, "productId": payload.get("productId"), "storeId": (payload.get("storeIds") or [None])[0], "taskType": payload.get("taskType") or "商品复核", "actionType": payload.get("actionType") or "manual_request", "priority": priority, "deadline": payload.get("deadline") or "24小时内", "riskDomain": payload.get("riskDomain") or "商品", "operationBudget": {"riskLevel": "medium" if priority == "中" else "low", "operatorBudgetApplies": False, "requiresApproval": decision == "manager_review_required"}, "sopSteps": [payload.get("task") or "复核该经营对象。", "提交处理截图、数据口径和影响范围。", "后续由系统复盘相关指标。"], "evidenceRequirements": ["页面来源", "处理截图", "数据口径"], "reviewMetrics": ["支付金额", "ROAS/ROI", "点击率", "转化率", "退款率", "库存"], "needManagerReview": decision == "manager_review_required", "reason": payload.get("reason") or payload.get("taskSignal")}
    snapshot = create_task_snapshot({"dataVersion": source_version, "decision": decision, "confidence": 0.7, "entityType": payload.get("entityType") or "商品", "entityId": entity_id, "productId": payload.get("productId"), "storeId": (payload.get("storeIds") or [None])[0], "signalRef": f"manual:product-read-model:{entity_id}:{source_version or 'latest'}", "ragContext": {"source": "product_read_model_manual_request", "version": PRODUCT_ARCHIVE_VERSION}, "agentJudgment": {"decision": decision, "confidence": 0.7, "reason": task_plan["reason"], "status": "manual_read_model_snapshot_bridge"}, "taskPlan": task_plan, "operationBudget": task_plan["operationBudget"], "evidenceRequirements": task_plan["evidenceRequirements"], "systemFacts": {"modulePayload": payload}, "source": "product_module_read_model_manual_request"}, created_by=user_id)
    pool = enter_task_pool_from_snapshot(snapshot.get("taskSnapshotId"), created_by=user_id)
    return {"version": PRODUCT_ARCHIVE_VERSION, "mode": "manual_request_via_task_snapshot", "snapshot": snapshot, "pool": pool, "task": pool.get("task") if isinstance(pool, dict) else None}


@router.get("/product")
def product(request: Request, store_id: str | None = Query(None, alias="storeId"), store_name: str | None = Query(None, alias="storeName")) -> list[Dict[str, Any]]:
    user_id = user_id_from_headers(request.headers)
    return product_items(user_id, store_id=store_id, store_name=store_name)


@router.get("/product/{product_id}")
def product_detail(request: Request, product_id: str) -> Dict[str, Any]:
    user_id = user_id_from_headers(request.headers)
    cached = read_product_detail(product_id)
    if cached.get("item"):
        return _normalize_archive_item(cached["item"])
    for item in product_items(user_id):
        if product_id in {item.get("id"), item.get("productId"), item.get("archiveId"), item.get("objectId"), item.get("skuId")}:
            return item
    raise HTTPException(status_code=404, detail="product not found in frontend read model or product projection")


@router.post("/product/{product_id}/tasks")
def product_task(request: Request, product_id: str) -> Dict[str, Any]:
    user_id = user_id_from_headers(request.headers)
    item = product_detail(request, product_id)
    return _enter_snapshot_pool(product_task_payload(item), user_id=user_id)
