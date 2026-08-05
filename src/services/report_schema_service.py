"""V6 report schema preview and unified import routing service.

Upload flow:
    one file -> backend field recognition -> automatic dataset routing -> import preview
    -> confirm import -> alert/runtime records

V6.0 changes the report center from front-end report-type selection to a unified
ERP/CRM/platform data entrance. The UI only chooses the source system. The backend
classifies rows into existing internal datasets and module projections.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.services.import_row_store_service import save_import_rows
from src.services.report_alert_service import import_report_dataset

SCHEMA_VERSION = "6.0.0"
AUTO_DATASET_NAMES = {"auto", "unified", "all", "erp", "crm", "platform", "manual"}

FIELD_LABELS = {
    "product_id": "商品ID",
    "customer_id": "客户ID",
    "store_id": "店铺ID",
    "store_name": "店铺名称",
    "available_stock": "当前库存",
    "safety_stock": "安全库存",
    "refund_amount": "退款金额",
    "refund_reason": "退款原因",
    "quantity": "购买件数",
    "actual_paid": "实付金额",
    "stock": "商品库存",
    "sale_price": "售价",
    "cost_price": "成本",
    "total_orders": "订单数",
    "refund_count": "退款次数",
    "product_name": "商品名称",
    "category": "类目",
    "platform": "平台",
    "roi": "ROI",
    "traffic": "流量",
    "clicks": "点击量",
    "ctr": "点击率",
    "conversion_rate": "转化率",
    "gross_margin": "毛利率",
    "ad_spend": "投放花费",
    "sales_volume": "销量",
    "revenue": "销售额",
    "good_review_rate": "好评率",
    "bad_review_rate": "差评率",
    "refund_rate": "退款率",
}

REPORT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "inventory": {
        "label": "库存数据",
        "identity_fields": ["product_id"],
        "warning_fields": ["available_stock", "safety_stock"],
        "optional_fields": ["store_id", "store_name", "sku", "warehouse", "sales_volume"],
        "target_modules": ["商品中心", "趋势中心", "任务中心"],
        "alert_hint": "后台识别为库存数据，用于写入商品库存、趋势快照和库存类任务。",
    },
    "refunds": {
        "label": "售后退款数据",
        "identity_fields": ["product_id"],
        "warning_fields": ["refund_amount", "refund_reason"],
        "optional_fields": ["store_id", "store_name", "refund_id", "order_id", "refund_time", "refund_rate", "bad_review_rate"],
        "target_modules": ["商品中心", "趋势中心", "任务中心"],
        "alert_hint": "后台识别为售后数据，用于写入售后风险、评价变化和售后类任务。",
    },
    "orders": {
        "label": "订单销售数据",
        "identity_fields": ["product_id"],
        "warning_fields": ["quantity", "actual_paid"],
        "optional_fields": ["store_id", "store_name", "order_id", "order_time", "buyer_id", "sales_volume", "revenue"],
        "target_modules": ["商品中心", "趋势中心", "总览"],
        "alert_hint": "后台识别为订单数据，用于写入销量、销售额和销售趋势。",
    },
    "products": {
        "label": "商品经营数据",
        "identity_fields": ["product_id"],
        "warning_fields": ["stock", "sale_price", "cost_price"],
        "optional_fields": [
            "store_id", "store_name", "product_name", "category", "platform", "roi", "traffic", "clicks", "ctr",
            "conversion_rate", "gross_margin", "ad_spend", "sales_volume", "revenue", "good_review_rate", "bad_review_rate", "refund_rate",
        ],
        "target_modules": ["商品中心", "趋势中心", "任务中心"],
        "alert_hint": "后台识别为商品经营数据，用于写入商品主档、利润、流量、ROI 和趋势快照。",
    },
    "customers": {
        "label": "客户CRM数据",
        "identity_fields": ["customer_id"],
        "warning_fields": ["total_orders", "refund_count"],
        "optional_fields": ["store_id", "store_name", "customer_name", "last_order_time", "tag", "refund_rate"],
        "target_modules": ["商品中心", "趋势中心", "任务中心"],
        "alert_hint": "后台识别为 CRM 客户数据，用于写入客户售后、复购和风险线索。",
    },
}

FIELD_ALIASES: Dict[str, List[str]] = {
    "product_id": ["product_id", "商品ID", "商品id", "商品编码", "商家编码", "SKU", "sku", "sku编码", "货号", "款号", "商品编号", "宝贝ID", "宝贝id"],
    "customer_id": ["customer_id", "客户ID", "客户id", "买家ID", "买家账号", "用户ID", "会员ID"],
    "store_id": ["store_id", "storeId", "店铺ID", "店铺id", "店铺编号", "店铺编码", "店铺id编码"],
    "store_name": ["store_name", "store", "店铺", "店铺名称", "店铺名", "门店", "店名"],
    "available_stock": ["available_stock", "current_stock", "库存", "可用库存", "当前库存", "现货库存", "实际库存", "可售库存"],
    "safety_stock": ["safety_stock", "安全库存", "库存安全线", "最低库存", "预警库存", "安全线"],
    "refund_amount": ["refund_amount", "退款金额", "退款额", "售后金额", "退货金额", "金额"],
    "refund_reason": ["refund_reason", "退款原因", "售后原因", "退货原因", "原因", "问题原因"],
    "quantity": ["quantity", "数量", "购买数量", "件数", "成交件数", "商品数量", "下单件数", "销量", "销售件数"],
    "actual_paid": ["actual_paid", "实付金额", "买家实付", "订单金额", "支付金额", "成交金额", "应收金额", "付款金额", "销售额", "GMV", "gmv"],
    "stock": ["stock", "库存", "商品库存", "现货库存", "可售库存", "当前库存"],
    "sale_price": ["sale_price", "售价", "销售价", "活动价", "成交价", "标价", "商品售价"],
    "cost_price": ["cost_price", "成本", "成本价", "采购价", "供货价", "商品成本"],
    "total_orders": ["total_orders", "订单数", "累计订单", "成交订单", "购买次数", "下单次数"],
    "refund_count": ["refund_count", "退款次数", "售后次数", "退货次数", "退款笔数"],
    "product_name": ["product_name", "商品名称", "商品标题", "标题", "宝贝标题", "品名"],
    "category": ["category", "类目", "商品类目", "垂直类目", "品类", "平台类目"],
    "platform": ["platform", "平台", "渠道", "来源平台"],
    "roi": ["roi", "ROI", "投产", "投产比", "投入产出比", "广告ROI", "推广ROI"],
    "traffic": ["traffic", "流量", "访客数", "访问量", "曝光", "曝光量", "展现量", "浏览量", "PV", "uv", "UV"],
    "clicks": ["clicks", "点击", "点击量", "点击数"],
    "ctr": ["ctr", "CTR", "点击率"],
    "conversion_rate": ["conversion_rate", "转化率", "支付转化率", "成交转化率", "CVR", "cvr"],
    "gross_margin": ["gross_margin", "毛利率", "利润率", "商品毛利率"],
    "ad_spend": ["ad_spend", "投放花费", "广告花费", "推广花费", "消耗", "投放消耗"],
    "sales_volume": ["sales_volume", "销量", "销售数量", "成交件数", "销售件数"],
    "revenue": ["revenue", "销售额", "GMV", "成交金额", "支付金额"],
    "good_review_rate": ["good_review_rate", "好评率", "正向评价率"],
    "bad_review_rate": ["bad_review_rate", "差评率", "负评率", "负向评价率"],
    "refund_rate": ["refund_rate", "退款率", "退货率", "售后率"],
}


def normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[\s_\-—/\\（）()\[\]【】:*：%]+", "", text)


NORMALIZED_ALIASES = {canonical: {normalize_text(item) for item in [canonical, *aliases]} for canonical, aliases in FIELD_ALIASES.items()}


def normalize_dataset_name(dataset_name: str | None) -> str:
    value = (dataset_name or "auto").strip().lower().replace("-", "_")
    aliases = {"order": "orders", "refund": "refunds", "product": "products", "stock": "inventory", "stocks": "inventory", "customer": "customers"}
    value = aliases.get(value, value)
    if value in AUTO_DATASET_NAMES:
        return "auto"
    if value not in REPORT_TEMPLATES:
        raise ValueError(f"Unsupported dataset_name: {dataset_name}")
    return value


def get_report_templates() -> Dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "mode": "v6_unified_backend_routing",
        "ownershipRule": "V6.0 前台只选择来源系统；商品、库存、利润、ROI、流量等分类由后台字段识别和路由完成。",
        "sources": ["ERP", "CRM", "平台后台", "广告后台", "手动表格"],
        "datasets": [
            {
                "datasetName": name,
                "label": template["label"],
                "identityFields": template["identity_fields"],
                "warningFields": template["warning_fields"],
                "optionalFields": template["optional_fields"],
                "targetModules": template.get("target_modules", []),
                "alertHint": template["alert_hint"],
                "fields": [
                    {"field": field, "label": FIELD_LABELS.get(field, field), "aliases": FIELD_ALIASES.get(field, [])[:8]}
                    for field in [*template["identity_fields"], *template["warning_fields"], *template["optional_fields"]]
                ],
            }
            for name, template in REPORT_TEMPLATES.items()
        ],
    }


def _headers_from_rows(rows: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for row in rows[:20]:
        if not isinstance(row, dict):
            continue
        for key in row.keys():
            text = str(key)
            if text not in seen:
                seen.append(text)
    return seen


def _mapping_override_for_dataset(field_mapping: Dict[str, Any] | None, dataset_name: str) -> Dict[str, str]:
    if not isinstance(field_mapping, dict):
        return {}
    datasets = field_mapping.get("datasets")
    if isinstance(datasets, dict) and isinstance(datasets.get(dataset_name), dict):
        return {str(key): str(value) for key, value in datasets[dataset_name].items()}
    if field_mapping.get("datasetName") == dataset_name and isinstance(field_mapping.get("mapping"), dict):
        return {str(key): str(value) for key, value in field_mapping["mapping"].items()}
    return {str(key): str(value) for key, value in field_mapping.items() if isinstance(value, str)}


def infer_field_mapping(dataset_name: str, rows: List[Dict[str, Any]]) -> Dict[str, str]:
    dataset = normalize_dataset_name(dataset_name)
    if dataset == "auto":
        raise ValueError("auto dataset does not have one fixed schema; use preview_report_dataset('auto', rows)")
    template = REPORT_TEMPLATES[dataset]
    target_fields = [*template["identity_fields"], *template["warning_fields"], *template["optional_fields"]]
    headers = _headers_from_rows(rows)
    normalized_headers = [(header, normalize_text(header)) for header in headers]
    mapping: Dict[str, str] = {}
    for canonical in target_fields:
        aliases = NORMALIZED_ALIASES.get(canonical, {normalize_text(canonical)})
        match = next((header for header, normalized in normalized_headers if normalized in aliases), None)
        if match:
            mapping[canonical] = match
    return mapping


def normalize_rows_with_mapping(rows: List[Dict[str, Any]], field_mapping: Dict[str, str]) -> List[Dict[str, Any]]:
    normalized_rows: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        next_row = deepcopy(row)
        for canonical, source_field in field_mapping.items():
            if source_field in row and canonical not in next_row:
                next_row[canonical] = row.get(source_field)
        normalized_rows.append(next_row)
    return normalized_rows


def _preview_named_dataset(dataset_name: str, rows: List[Dict[str, Any]], field_mapping: Dict[str, Any] | None = None, *, include_rows: bool = True) -> Dict[str, Any]:
    dataset = normalize_dataset_name(dataset_name)
    if dataset == "auto":
        raise ValueError("auto routing should use preview_report_dataset")
    template = REPORT_TEMPLATES[dataset]
    inferred_mapping = infer_field_mapping(dataset, rows)
    final_mapping = {**inferred_mapping, **_mapping_override_for_dataset(field_mapping, dataset)}
    identity_missing = [field for field in template["identity_fields"] if field not in final_mapping]
    warning_missing = [field for field in template["warning_fields"] if field not in final_mapping]
    optional_missing = [field for field in template["optional_fields"] if field not in final_mapping]
    normalized_rows = normalize_rows_with_mapping(rows, final_mapping)
    ownership_ready = "store_id" in final_mapping or "store_name" in final_mapping
    issues: List[Dict[str, Any]] = []
    for field in identity_missing:
        issues.append({"field": field, "label": FIELD_LABELS.get(field, field), "severity": "blocked", "message": f"缺少{FIELD_LABELS.get(field, field)}，无法确认数据对象。"})
    for field in warning_missing:
        issues.append({"field": field, "label": FIELD_LABELS.get(field, field), "severity": "warning", "message": f"缺少{FIELD_LABELS.get(field, field)}，该类信号可能不会生成或不够准确。"})
    if not ownership_ready:
        issues.append({"field": "store_id", "label": "店铺归属", "severity": "warning", "message": "未识别到店铺字段，系统会尝试按商品所属店铺补齐。"})
    status = "blocked" if identity_missing else "needs_attention" if warning_missing or not ownership_ready else "ready"
    result = {
        "version": SCHEMA_VERSION,
        "datasetName": dataset,
        "label": template["label"],
        "rowCount": len(rows),
        "headers": _headers_from_rows(rows),
        "fieldMapping": final_mapping,
        "recognizedFields": [{"field": field, "label": FIELD_LABELS.get(field, field), "sourceField": source} for field, source in final_mapping.items()],
        "missingIdentityFields": identity_missing,
        "missingWarningFields": warning_missing,
        "missingOptionalFields": optional_missing,
        "ownershipReady": ownership_ready,
        "targetModules": template.get("target_modules", []),
        "issues": issues,
        "status": status,
        "canImport": bool(rows) and not identity_missing,
        "message": "字段和店铺归属已识别，可以导入。" if status == "ready" else "字段或店铺归属有缺失，确认影响后仍可导入。" if status == "needs_attention" else "缺少关键对象字段，暂不能导入。",
        "alertHint": template["alert_hint"],
        "previewRows": normalized_rows[:5],
    }
    if include_rows:
        result["normalizedRows"] = normalized_rows
    return result


def _route_score(preview: Dict[str, Any]) -> Dict[str, int]:
    template = REPORT_TEMPLATES[preview["datasetName"]]
    mapping = preview.get("fieldMapping") or {}
    identity_hits = len([field for field in template["identity_fields"] if field in mapping])
    warning_hits = len([field for field in template["warning_fields"] if field in mapping])
    optional_hits = len([field for field in template["optional_fields"] if field in mapping])
    return {"identityHits": identity_hits, "warningHits": warning_hits, "optionalHits": optional_hits, "totalHits": identity_hits * 3 + warning_hits * 2 + optional_hits}


def _preview_auto_dataset(rows: List[Dict[str, Any]], field_mapping: Dict[str, Any] | None = None, *, source_system: str | None = None) -> Dict[str, Any]:
    previews: List[Dict[str, Any]] = []
    for name in REPORT_TEMPLATES:
        item = _preview_named_dataset(name, rows, field_mapping, include_rows=False)
        score = _route_score(item)
        can_route = item["canImport"] and (score["warningHits"] > 0 or score["optionalHits"] >= 2)
        if can_route:
            item["routeScore"] = score
            item["routeReason"] = f"识别到 {score['warningHits']} 个核心字段、{score['optionalHits']} 个辅助字段。"
            previews.append(item)
    if not previews:
        candidates = []
        for name in REPORT_TEMPLATES:
            item = _preview_named_dataset(name, rows, field_mapping, include_rows=False)
            score = _route_score(item)
            if item["canImport"]:
                item["routeScore"] = score
                item["routeReason"] = "仅识别到对象字段，建议补充指标字段后导入。"
                candidates.append(item)
        previews = sorted(candidates, key=lambda item: item.get("routeScore", {}).get("totalHits", 0), reverse=True)[:1]
    routed_mappings = {item["datasetName"]: item.get("fieldMapping", {}) for item in previews}
    all_fields: Dict[str, Dict[str, Any]] = {}
    for item in previews:
        for field in item.get("recognizedFields", []):
            all_fields.setdefault(field["field"], field)
    issues: List[Dict[str, Any]] = []
    if not rows:
        issues.append({"field": "rows", "label": "数据行", "severity": "blocked", "message": "没有读取到有效数据行。"})
    if not previews:
        issues.append({"field": "product_id", "label": "对象识别", "severity": "blocked", "message": "未识别到商品ID或客户ID，后台无法建立分类路由。"})
    missing_store = not any("store_id" in (item.get("fieldMapping") or {}) or "store_name" in (item.get("fieldMapping") or {}) for item in previews)
    if previews and missing_store:
        issues.append({"field": "store_id", "label": "店铺归属", "severity": "warning", "message": "未识别到店铺字段，系统会尝试按商品或账号权限补齐店铺归属。"})
    status = "blocked" if not previews or not rows else "needs_attention" if issues else "ready"
    return {
        "version": SCHEMA_VERSION,
        "datasetName": "auto",
        "label": "一键导入",
        "sourceSystem": source_system or "ERP/CRM/平台数据",
        "rowCount": len(rows),
        "headers": _headers_from_rows(rows),
        "fieldMapping": {"mode": "auto", "datasets": routed_mappings},
        "recognizedFields": list(all_fields.values()),
        "detectedDatasets": previews,
        "detectedDatasetCount": len(previews),
        "targetModules": sorted({module for item in previews for module in item.get("targetModules", [])}),
        "issues": issues,
        "status": status,
        "canImport": bool(rows) and bool(previews),
        "message": f"已识别 {len(previews)} 条后台分类路由；确认后系统将分别写入对应模块。" if previews else "未识别到可导入的数据路由，请检查商品ID、客户ID或字段名称。",
        "alertHint": "V6.0 前台不再选择商品/库存/订单分类；系统会按字段自动分类到商品中心、趋势中心、任务中心和导入记录。",
        "backendRoutingRule": "一份报表可同时命中多个内部数据集；同一商品的库存、利润、ROI、流量等字段会被后台拆分为不同模块投影。",
        "previewRows": rows[:5],
    }


def preview_report_dataset(dataset_name: str, rows: List[Dict[str, Any]] | None, field_mapping: Dict[str, Any] | None = None, source_system: str | None = None) -> Dict[str, Any]:
    dataset = normalize_dataset_name(dataset_name)
    dataset_rows = rows if isinstance(rows, list) else []
    if dataset == "auto":
        return _preview_auto_dataset(dataset_rows, field_mapping, source_system=source_system)
    return _preview_named_dataset(dataset, dataset_rows, field_mapping)


def _compact_preview(preview: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in preview.items() if key != "normalizedRows"}


def confirm_report_import(
    dataset_name: str,
    rows: List[Dict[str, Any]] | None,
    field_mapping: Dict[str, Any] | None = None,
    auto_create_tasks: bool = True,
    source_system: str | None = None,
) -> Dict[str, Any]:
    dataset = normalize_dataset_name(dataset_name)
    dataset_rows = rows if isinstance(rows, list) else []
    if dataset == "auto":
        preview = preview_report_dataset("auto", dataset_rows, field_mapping, source_system=source_system)
        if not preview["canImport"]:
            raise ValueError(preview["message"])
        results: List[Dict[str, Any]] = []
        for route in preview.get("detectedDatasets", []):
            routed_dataset = route["datasetName"]
            routed_mapping = (preview.get("fieldMapping") or {}).get("datasets", {}).get(routed_dataset, route.get("fieldMapping") or {})
            routed_rows = normalize_rows_with_mapping(dataset_rows, routed_mapping)
            result = import_report_dataset(routed_dataset, rows=routed_rows, auto_create_tasks=auto_create_tasks)
            save_import_rows(result["dataVersion"], routed_dataset, routed_rows)
            result["schemaPreview"] = _compact_preview(route)
            result["fullRowsPersisted"] = True
            results.append(result)
        import_id = f"V6AUTO_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        return {
            "version": SCHEMA_VERSION,
            "mode": "v6_unified_backend_routing",
            "importId": import_id,
            "datasetName": "auto",
            "sourceSystem": source_system or preview.get("sourceSystem"),
            "dataVersion": results[0]["dataVersion"] if results else import_id,
            "rowCount": len(dataset_rows),
            "routedDatasetCount": len(results),
            "detectedDatasets": preview.get("detectedDatasets", []),
            "targetModules": preview.get("targetModules", []),
            "alertCount": sum(item.get("alertCount", 0) for item in results),
            "createdTaskCount": sum(item.get("createdTaskCount", 0) for item in results),
            "results": results,
            "schemaPreview": _compact_preview(preview),
            "fullRowsPersisted": True,
            "message": "一键导入完成：后台已完成字段识别、分类路由、模块写入和任务同步。",
        }
    preview = preview_report_dataset(dataset, dataset_rows, field_mapping, source_system=source_system)
    if not preview["canImport"]:
        raise ValueError(preview["message"])
    result = import_report_dataset(preview["datasetName"], rows=preview["normalizedRows"], auto_create_tasks=auto_create_tasks)
    save_import_rows(result["dataVersion"], preview["datasetName"], preview["normalizedRows"])
    result["schemaPreview"] = _compact_preview(preview)
    result["version"] = SCHEMA_VERSION
    result["fullRowsPersisted"] = True
    return result
