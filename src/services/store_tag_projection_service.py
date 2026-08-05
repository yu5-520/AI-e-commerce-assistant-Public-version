"""V12.8.2 store tag projection gateway.

Store labels shown to operators must come from the same backend governance and
business facts used by task authorization. Report performance and imported object
counts are not governance weight.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.operating_weight_policy_service import infer_operating_weight, is_governance_high_weight

STORE_TAG_PROJECTION_VERSION = "12.8.2"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


def _governance_payload(store_row: Dict[str, Any] | None = None) -> Dict[str, Any]:
    store_row = store_row or {}
    return {
        "storeId": store_row.get("storeId") or store_row.get("id"),
        "storeName": store_row.get("storeName") or store_row.get("displayName") or store_row.get("name"),
        "weightSource": store_row.get("weightSource") or store_row.get("governanceWeightSource"),
        "governanceWeightTag": store_row.get("governanceWeightTag"),
        "governanceWeightLevel": store_row.get("governanceWeightLevel"),
        "explicitWeightTags": store_row.get("explicitWeightTags") or store_row.get("governanceTags") or [],
        "ownership": store_row.get("ownership") or {},
        "ragBusinessMemory": store_row.get("ragBusinessMemory") or {},
    }


def governance_tag_for_store(store_row: Dict[str, Any] | None = None) -> Dict[str, Any]:
    weight = infer_operating_weight(_governance_payload(store_row))
    if is_governance_high_weight(weight):
        label = "战略店铺" if weight.get("combinedWeight") == "strategic" else "高权重店铺"
        level = "good"
    elif weight.get("weightSource") in {"first_report_baseline", "untrusted_imported_label"}:
        label = "权重未确认"
        level = "watch"
    else:
        label = "普通店铺"
        level = "watch"
    return {"version": STORE_TAG_PROJECTION_VERSION, "label": label, "level": level, "weight": weight, "rule": "治理权重只来自RAG/主管/老板/多期历史贡献，不来自商品数量、ROI、GMV或已入库状态。"}


def data_tags_for_store(*, product_count: int = 0, traffic_count: int = 0, comparison_ready: bool = False, trend_ready: bool = False, has_gap: bool = False) -> List[str]:
    tags: List[str] = []
    if product_count or traffic_count:
        tags.append("基线期" if not comparison_ready else "可环比")
    if trend_ready:
        tags.append("可趋势判断")
    if has_gap:
        tags.append("数据缺口")
    return tags or ["等待数据"]


def business_tags_for_store(existing_tags: List[str] | None = None, *, active_task_count: int = 0, products: List[Dict[str, Any]] | None = None) -> List[str]:
    tags: List[str] = []
    products = products or []
    existing = [str(item) for item in (existing_tags or []) if item]
    if any("库存" in item for item in existing) or any(item.get("inventoryLevel") in {"danger", "warning"} for item in products):
        tags.append("库存警告")
    if any("售后" in item for item in existing) or any(item.get("afterSalesLevel") in {"danger", "warning"} for item in products):
        tags.append("售后观察")
    if any("毛利" in item for item in existing):
        tags.append("毛利观察")
    if active_task_count:
        tags.append("执行任务")
    if not tags and products:
        tags.append("经营观察")
    return tags or ["常规观察"]


def project_store_tags(store_row: Dict[str, Any] | None = None, *, products: List[Dict[str, Any]] | None = None, traffic_count: int = 0, active_task_count: int = 0, existing_business_tags: List[str] | None = None, comparison_ready: bool = False, trend_ready: bool = False, has_gap: bool = False) -> Dict[str, Any]:
    store_row = store_row or {}
    products = products or []
    product_count = _as_int(store_row.get("productCount"), len(products)) or len(products)
    governance = governance_tag_for_store(store_row)
    data_tags = data_tags_for_store(product_count=product_count, traffic_count=traffic_count, comparison_ready=comparison_ready, trend_ready=trend_ready, has_gap=has_gap)
    business_tags = business_tags_for_store(existing_business_tags, active_task_count=active_task_count, products=products)
    return {
        "version": STORE_TAG_PROJECTION_VERSION,
        "governanceTag": governance,
        "dataTags": data_tags,
        "businessTags": business_tags,
        "riskTags": business_tags,
        "displayTags": [governance["label"], *data_tags[:2], *business_tags[:2]],
        "level": "warning" if any(tag in {"库存警告", "售后观察", "执行任务", "数据缺口"} for tag in business_tags + data_tags) else governance.get("level", "watch"),
        "rule": "店铺标签由治理标签、数据标签、经营标签三层组成；没有治理来源时不能显示高权重店铺。",
    }
