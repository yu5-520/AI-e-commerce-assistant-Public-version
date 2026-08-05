"""V12.2 report layout agent service.

V12 only answered: this Sheet should go to which target table.
V12.2 answers: this Sheet contains which data blocks, and which operating scope
belongs to each block.

The service is deterministic and agent-shaped. It uses sheet names, headers,
canonical fields, row layout and row-level anchors to produce a block contract.
It does not create tasks and it does not patch missing values.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.services.metric_catalog_service import CATALOG_VERSION, canonical_field, pick, product_identity, recognized_fields

REPORT_PROFILE_VERSION = "12.2.2"

SHEET_RULES = [
    {
        "target": "product_metric_facts",
        "kind": "商品经营明细",
        "scope": "product",
        "keywords": ["商品", "经营", "明细", "SKU", "商品ID"],
        "required_any": ["product_id", "sku_id", "roi", "gross_margin_rate", "inventory_qty", "payment_amount"],
    },
    {
        "target": "store_metric_facts",
        "kind": "店铺经营汇总",
        "scope": "store",
        "keywords": ["店铺", "汇总", "经营单元"],
        "required_any": ["store_id", "store_name", "payment_amount", "roi", "refund_rate"],
    },
    {
        "target": "traffic_source_facts",
        "kind": "流量来源明细",
        "scope": "traffic_source",
        "keywords": ["流量", "来源", "渠道"],
        "required_any": ["traffic_source", "visitor_count", "click_rate", "roi", "ad_spend"],
    },
]

BLOCK_RULES = {
    "store": {"blockType": "store_summary", "targetTable": "store_metric_facts", "metricScope": "store", "label": "店铺汇总区块"},
    "product": {"blockType": "product_metric_detail", "targetTable": "product_metric_facts", "metricScope": "product", "label": "商品经营区块"},
    "traffic_source": {"blockType": "traffic_source_detail", "targetTable": "traffic_source_facts", "metricScope": "traffic_source", "label": "流量来源区块"},
    "staging": {"blockType": "staging_unknown", "targetTable": "staging_rows", "metricScope": "unknown", "label": "暂存区块"},
}


def _sheet_rows(parsed: Dict[str, Any], sheet_name: str) -> List[Dict[str, Any]]:
    sheet_rows = parsed.get("sheetRows") or {}
    if isinstance(sheet_rows, dict) and isinstance(sheet_rows.get(sheet_name), list):
        return [row for row in sheet_rows[sheet_name] if isinstance(row, dict)]
    rows = parsed.get("rows") or []
    return [row for row in rows if isinstance(row, dict) and row.get("__source_sheet") == sheet_name]


def _headers_from_sheet(sheet: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[str]:
    headers = [str(item) for item in sheet.get("headers", []) if item not in {None, ""}]
    if headers:
        return headers
    seen: List[str] = []
    for row in rows[:10]:
        for key in row.keys():
            if str(key).startswith("__"):
                continue
            if key not in seen:
                seen.append(str(key))
    return seen


def _noise_ratio(rows: List[Dict[str, Any]], headers: List[str]) -> Dict[str, Any]:
    if not rows or not headers:
        return {"emptyCellRatio": 0, "garbledCellCount": 0, "sampleSize": 0}
    total = 0
    empty = 0
    garbled = 0
    for row in rows[:50]:
        for header in headers:
            value = row.get(header)
            total += 1
            if value in {None, ""}:
                empty += 1
            if isinstance(value, str) and "�" in value:
                garbled += 1
    return {
        "emptyCellRatio": round(empty / total, 4) if total else 0,
        "garbledCellCount": garbled,
        "sampleSize": min(len(rows), 50),
    }


def _target_for_sheet(sheet_name: str, headers: List[str]) -> Dict[str, Any]:
    canonical_headers = {canonical_field(header) for header in headers if canonical_field(header)}
    name_text = sheet_name.lower()
    scored: List[Dict[str, Any]] = []
    for rule in SHEET_RULES:
        keyword_hits = sum(1 for keyword in rule["keywords"] if keyword.lower() in name_text or keyword in sheet_name)
        field_hits = sum(1 for field in rule["required_any"] if field in canonical_headers)
        score = keyword_hits * 3 + field_hits
        scored.append({"rule": rule, "score": score, "keywordHits": keyword_hits, "fieldHits": field_hits})
    best = max(scored, key=lambda item: item["score"])
    rule = best["rule"]
    confidence = min(0.98, 0.45 + best["keywordHits"] * 0.15 + best["fieldHits"] * 0.07)
    if best["score"] <= 1:
        return {"targetTable": "staging_rows", "sheetKind": "未知报表", "metricScope": "unknown", "confidence": 0.35, "routeReason": "未识别到足够字段，只进入候选暂存。"}
    return {
        "targetTable": rule["target"],
        "sheetKind": rule["kind"],
        "metricScope": rule["scope"],
        "confidence": round(confidence, 2),
        "routeReason": f"命中 {best['keywordHits']} 个名称关键词、{best['fieldHits']} 个标准字段。",
    }


def _row_scope(row: Dict[str, Any], sheet_route: Dict[str, Any]) -> str:
    ident = product_identity(row)
    has_product_anchor = bool(ident.get("productId") or ident.get("skuId") or ident.get("erpProductCode") or ident.get("productLink"))
    has_store_anchor = bool(ident.get("storeId") or ident.get("storeName"))
    if pick(row, "traffic_source"):
        return "traffic_source"
    if has_product_anchor:
        return "product"
    if has_store_anchor:
        return "store"
    fallback = str(sheet_route.get("metricScope") or "")
    return fallback if fallback in BLOCK_RULES else "staging"


def _row_index(row: Dict[str, Any], fallback: int) -> int:
    try:
        return int(row.get("__source_row_index") or fallback)
    except Exception:
        return fallback


def _block_from_rows(sheet_name: str, scope: str, rows: List[Dict[str, Any]], block_index: int, sheet_route: Dict[str, Any], headers: List[str], recognized: List[Dict[str, Any]]) -> Dict[str, Any]:
    rule = BLOCK_RULES.get(scope, BLOCK_RULES["staging"])
    source_rows = [_row_index(row, index + 2) for index, row in enumerate(rows)]
    row_start = min(source_rows) if source_rows else 0
    row_end = max(source_rows) if source_rows else 0
    header_rows = sorted({int(row.get("__source_header_row_index") or 1) for row in rows if row.get("__source_header_row_index")}) or [1]
    canonical_counts = Counter(item["canonicalField"] for item in recognized)
    identity_candidates = ["platform", "store_id", "store_name", "product_id", "sku_id", "erp_product_code", "product_link", "stat_date", "traffic_source"]
    identity_fields = [field for field in identity_candidates if canonical_counts.get(field)]
    metric_fields = [field for field in canonical_counts if field not in identity_fields and field not in {"product_name", "product_tag", "category_l1", "category_l2"}]
    issues: List[Dict[str, Any]] = []
    if rule["targetTable"] in {"product_metric_facts", "traffic_source_facts"} and not any(field in identity_fields for field in ["product_id", "sku_id", "erp_product_code", "product_link"]):
        issues.append({"severity": "blocked", "message": "区块缺少商品可定位锚点，不能进入正式商品事实。"})
    if rule["targetTable"] != "staging_rows" and not any(field in identity_fields for field in ["store_id", "store_name"]):
        issues.append({"severity": "warning", "message": "区块未识别到店铺归属字段，将降低定位置信度。"})
    confidence = sheet_route.get("confidence", 0.5)
    if scope == "traffic_source" and "traffic_source" in identity_fields:
        confidence = min(0.98, float(confidence) + 0.05)
    block_id = f"{sheet_name}::{rule['blockType']}::{block_index:03d}::{row_start}-{row_end}"
    return {
        "blockId": block_id,
        "sheetName": sheet_name,
        "blockIndex": block_index,
        "blockType": rule["blockType"],
        "blockKind": rule["label"],
        "targetTable": rule["targetTable"],
        "metricScope": rule["metricScope"],
        "range": f"R{row_start}:R{row_end}" if row_start and row_end else "unknown",
        "rowStart": row_start,
        "rowEnd": row_end,
        "rowCount": len(rows),
        "headerRows": header_rows,
        "headers": headers,
        "recognizedFields": recognized,
        "identityFields": identity_fields,
        "metricFields": metric_fields,
        "confidence": round(float(confidence), 2),
        "issues": issues,
        "sampleRows": rows[:3],
        "routeReason": f"V12.2 按行级锚点识别为 {rule['label']}，而不是整 Sheet 单一路由。",
    }


def _layout_blocks(sheet_name: str, rows: List[Dict[str, Any]], headers: List[str], recognized: List[Dict[str, Any]], sheet_route: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    blocks: List[Dict[str, Any]] = []
    current_scope: str | None = None
    current_rows: List[Dict[str, Any]] = []
    block_index = 1
    for row in rows:
        scope = _row_scope(row, sheet_route)
        if current_scope is None:
            current_scope = scope
            current_rows = [row]
            continue
        if scope == current_scope:
            current_rows.append(row)
            continue
        blocks.append(_block_from_rows(sheet_name, current_scope, current_rows, block_index, sheet_route, headers, recognized))
        block_index += 1
        current_scope = scope
        current_rows = [row]
    if current_scope is not None and current_rows:
        blocks.append(_block_from_rows(sheet_name, current_scope, current_rows, block_index, sheet_route, headers, recognized))
    return blocks


def _profile_sheet(parsed: Dict[str, Any], sheet: Dict[str, Any]) -> Dict[str, Any]:
    sheet_name = str(sheet.get("sheetName") or "Sheet")
    rows = _sheet_rows(parsed, sheet_name)
    headers = _headers_from_sheet(sheet, rows)
    recognized = recognized_fields(headers)
    canonical_counts = Counter(item["canonicalField"] for item in recognized)
    route = _target_for_sheet(sheet_name, headers)
    identity_fields = [field for field in ["platform", "store_id", "store_name", "product_id", "sku_id", "erp_product_code", "product_link", "stat_date", "traffic_source"] if canonical_counts.get(field)]
    metric_fields = [field for field in canonical_counts if field not in identity_fields and field not in {"product_name", "product_tag", "category_l1", "category_l2"}]
    display_only = [field for field in ["product_name", "product_tag", "category_l1", "category_l2"] if canonical_counts.get(field)]
    blocks = _layout_blocks(sheet_name, rows, headers, recognized, route)
    issues: List[Dict[str, Any]] = []
    if route["targetTable"] in {"product_metric_facts", "traffic_source_facts"} and not any(field in identity_fields for field in ["product_id", "sku_id", "erp_product_code", "product_link"]):
        issues.append({"severity": "blocked", "message": "缺少商品可定位锚点，不能进入正式商品事实。"})
    if not any(field in identity_fields for field in ["store_id", "store_name"]):
        issues.append({"severity": "warning", "message": "未识别到店铺归属字段，将使用上传人/历史映射兜底。"})
    noise = _noise_ratio(rows, headers)
    if noise["garbledCellCount"]:
        issues.append({"severity": "warning", "message": f"发现 {noise['garbledCellCount']} 个局部乱码单元格，相关字段会降低置信度。"})
    block_issues = [issue for block in blocks for issue in block.get("issues", [])]
    return {
        "sheetName": sheet_name,
        "sheetKind": route["sheetKind"],
        "targetTable": route["targetTable"],
        "metricScope": route.get("metricScope"),
        "rowCount": int(sheet.get("rowCount") or len(rows)),
        "rawRowCount": int(sheet.get("rawRowCount") or len(rows)),
        "headers": headers,
        "recognizedFields": recognized,
        "identityFields": identity_fields,
        "metricFields": metric_fields,
        "displayOnlyFields": display_only,
        "ignoredAsPrimaryKey": ["product_name"] if "product_name" in display_only else [],
        "confidence": route["confidence"],
        "routeReason": route["routeReason"],
        "quality": noise,
        "issues": issues + block_issues,
        "blocks": blocks,
        "blockCount": len(blocks),
        "sampleRows": rows[:3],
    }


def build_report_profile(parsed: Dict[str, Any]) -> Dict[str, Any]:
    sheets = parsed.get("sheets") or []
    sheet_profiles = [_profile_sheet(parsed, sheet) for sheet in sheets if isinstance(sheet, dict)]
    blocks = [block for sheet in sheet_profiles for block in sheet.get("blocks", [])]
    blocking = [issue for sheet in sheet_profiles for issue in sheet.get("issues", []) if issue.get("severity") == "blocked"]
    warnings = [issue for sheet in sheet_profiles for issue in sheet.get("issues", []) if issue.get("severity") == "warning"]
    targets = sorted({block.get("targetTable") for block in blocks if block.get("targetTable") and block.get("targetTable") != "staging_rows"})
    avg_conf = round(sum(float(block.get("confidence") or 0) for block in blocks) / len(blocks), 2) if blocks else 0
    return {
        "version": REPORT_PROFILE_VERSION,
        "profileMode": "sheet_to_block_profile",
        "metricCatalogVersion": CATALOG_VERSION,
        "fileName": parsed.get("fileName"),
        "format": parsed.get("format"),
        "sheetCount": parsed.get("sheetCount", len(sheet_profiles)),
        "blockCount": len(blocks),
        "totalRows": parsed.get("totalRows", 0),
        "targetFactTables": targets,
        "sheetProfiles": sheet_profiles,
        "confidence": avg_conf,
        "status": "blocked" if blocking else "needs_attention" if warnings else "ready",
        "blockingIssueCount": len(blocking),
        "warningIssueCount": len(warnings),
        "rule": "V12.2：Agent判断Sheet内数据区块和经营口径，代码按block写事实；识别失败进入暂存/缺口，不用缓存伪装成功。",
    }
