"""V15 report schema Agent service.

The report Agent is a schema translator only. It reads filenames, sheet names,
headers and sample value types, then returns a system field mapping. Rows are
cleaned by code after the mapping is resolved.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Dict, List

from src.repositories.sqlite_repository import dumps
from src.services.agent_budget_ledger_service import get_report_schema_mapping_cache, save_report_schema_mapping_cache
from src.services.agent_llm_gateway_v15_service import request_agent_call

REPORT_SCHEMA_AGENT_VERSION = "15.0"
KNOWN_FIELD_ALIASES = {
    "商品ID": "productId", "商品编号": "productId", "商品id": "productId", "商品编码": "productId",
    "店铺": "storeName", "店铺名称": "storeName", "店铺ID": "storeId",
    "支付金额": "paymentAmount", "成交金额": "paymentAmount", "销售额": "paymentAmount", "GMV": "paymentAmount", "实付金额": "paymentAmount",
    "访客数": "visitorCount", "点击率": "clickRate", "转化率": "conversionRate",
    "退款率": "refundRate", "库存": "inventory", "毛利率": "grossMargin",
    "投产比": "roi", "ROI": "roi", "ROAS": "roas", "广告花费": "adSpend", "投放花费": "adSpend",
    "日期": "dataDate", "统计日期": "dataDate", "时间": "dataDate",
}


def _normalize_header(value: Any) -> str:
    return str(value or "").strip().replace("\n", " ").replace("\t", " ")


def report_schema_fingerprint(*, file_name: str | None, sheets: List[Dict[str, Any]]) -> str:
    structure = []
    for sheet in sheets:
        headers = [_normalize_header(item) for item in (sheet.get("headers") or [])]
        sample_types = []
        for row in (sheet.get("sampleRows") or [])[:3]:
            if isinstance(row, dict):
                values = [row.get(header) for header in headers]
            else:
                values = list(row or [])[: len(headers)]
            sample_types.append([type(value).__name__ for value in values])
        structure.append({"sheetName": sheet.get("sheetName"), "headers": headers, "sampleTypes": sample_types})
    return sha256(dumps({"fileNameHint": file_name, "sheets": structure}).encode("utf-8")).hexdigest()


def _local_field_mapping(headers: List[str]) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    for header in headers:
        normalized = _normalize_header(header)
        mapping[normalized] = KNOWN_FIELD_ALIASES.get(normalized) or KNOWN_FIELD_ALIASES.get(normalized.upper()) or "unknown"
    return mapping


def resolve_report_schema_mapping(*, file_name: str | None, sheets: List[Dict[str, Any]], data_version: str | None = None, run_id: str | None = None, platform: str | None = None) -> Dict[str, Any]:
    fingerprint = report_schema_fingerprint(file_name=file_name, sheets=sheets)
    cached = get_report_schema_mapping_cache(fingerprint)
    if cached:
        request_agent_call(stage="report_schema_agent", data_version=data_version, run_id=run_id, purpose="report_schema_cache_hit", payload={"fingerprint": fingerprint}, requested_calls=0, actual_calls=0, cache_hit=True, fallback_allowed=True)
        return {**cached, "cacheHit": True, "agentCallCount": 0}
    sheet_results: List[Dict[str, Any]] = []
    unknown_headers: List[str] = []
    for sheet in sheets:
        headers = [_normalize_header(item) for item in (sheet.get("headers") or [])]
        mapping = _local_field_mapping(headers)
        unknown_headers.extend([header for header, target in mapping.items() if target == "unknown"])
        sheet_type = "product_metrics" if any(value in mapping.values() for value in ["productId", "paymentAmount", "refundRate", "inventory"]) else "unknown"
        sheet_results.append({"sheetName": sheet.get("sheetName"), "sheetType": sheet_type, "fieldMapping": mapping, "unknownHeaders": [header for header, target in mapping.items() if target == "unknown"]})
    needs_agent = bool(unknown_headers)
    gateway = request_agent_call(stage="report_schema_agent", data_version=data_version, run_id=run_id, purpose="report_schema_mapping_unknown_headers" if needs_agent else "report_schema_local_mapping", payload={"fileName": file_name, "fingerprint": fingerprint, "unknownHeaders": unknown_headers[:50]}, requested_calls=1 if needs_agent else 0, actual_calls=0, cache_hit=False, fallback_allowed=True)
    confidence = 0.72 if needs_agent else 0.94
    record = {"version": REPORT_SCHEMA_AGENT_VERSION, "schemaFingerprint": fingerprint, "fileName": file_name, "platform": platform or "unknown", "sheets": sheet_results, "confidence": confidence, "agentStage": "report_schema_agent", "agentCallCount": gateway.get("actualCalls", 0), "agentGateway": gateway, "rule": "Report Agent maps headers/sheets only; row cleaning is code-owned."}
    save_report_schema_mapping_cache(schema_fingerprint=fingerprint, platform=platform, sheet_type="multi_sheet" if len(sheet_results) > 1 else (sheet_results[0].get("sheetType") if sheet_results else "unknown"), confidence=confidence, field_mapping={item["sheetName"] or f"sheet_{idx}": item["fieldMapping"] for idx, item in enumerate(sheet_results)}, payload=record)
    return record
