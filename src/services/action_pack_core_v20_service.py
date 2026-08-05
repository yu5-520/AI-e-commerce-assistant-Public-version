"""V22 action capability compiler.

This module converts the Agent1-locked action family and current product facts
into executable objects, metric values, permission bounds and coordination data.
It never chooses a strategy, action family, target audience, activity type or SOP.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.runtime_version import VERSION
from src.services.agent_rag_context_v2028_service import (
    build_agent_rag_context_snapshot,
    rag_context_summary,
)
from src.services.route_action_department_matrix_v1915_service import (
    MATRIX_DISPATCH_VERSION,
    attach_matrix_dispatch,
    selected_family,
)

ACTION_PACK_CORE_VERSION = VERSION
ACTION_PARAMETER_ENRICHMENT_VERSION = VERSION
AGENT_RAG_CONTEXT_VERSION = VERSION
HIGH_RISK_ACTIONS = {"roas_scale", "roas_guard", "platform_activity", "activity_apply"}

ALIASES = {
    "adSpend": ["adSpend", "ad_spend", "广告消耗", "广告花费", "投放消耗"],
    "paymentAmount": ["paymentAmount", "payment_amount", "成交金额", "支付金额", "销售额", "gmv", "GMV"],
    "roi": ["roi", "ROI", "roas", "ROAS", "投产", "投产比"],
    "grossMarginRate": ["grossMarginRate", "gross_margin_rate", "grossMargin", "毛利率"],
    "grossProfitAmount": ["grossProfitAmount", "gross_profit", "grossProfit", "毛利金额", "利润", "单件毛利"],
    "price": ["price", "salePrice", "sellingPrice", "unitPrice", "售价", "商品售价", "客单价"],
    "cost": ["cost", "productCost", "商品成本", "成本", "成本价"],
    "inventory": ["inventory", "stock", "库存", "库存数量"],
    "availableDays": ["availableDays", "available_days", "sellableDays", "可售天数"],
    "paidVisitors": ["paidVisitors", "paid_visitors", "付费访客"],
    "organicVisitors": ["organicVisitors", "organic_visitors", "自然访客"],
    "conversionRate": ["conversionRate", "conversion_rate", "支付转化率", "转化率"],
    "clickRate": ["clickRate", "click_rate", "点击率", "CTR"],
    "refundRate": ["refundRate", "refund_rate", "退款率", "售后率"],
    "rating": ["rating", "score", "评分", "好评率"],
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in {"", "—", "未识别", "UNKNOWN", "null", "None"})


def _num(value: Any) -> float | None:
    if _blank(value) or isinstance(value, (dict, list, tuple, set)):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").replace("￥", "").replace("¥", "").strip())
    except Exception:
        return None


def _walk(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value[:80]:
            yield from _walk(child)


def _value(package: Dict[str, Any], name: str) -> Any:
    aliases = set(ALIASES.get(name, [name]))
    for row in _walk(package):
        for key, value in row.items():
            if str(key) in aliases and not _blank(value) and not isinstance(value, (dict, list)):
                return value
        code = str(row.get("metricCode") or row.get("code") or row.get("metricName") or row.get("label") or row.get("name") or "")
        if code in aliases:
            for key in ("currentValue", "current", "latest", "value", "amount"):
                if key in row and not _blank(row.get(key)):
                    return row.get(key)
    return None


def _metric(package: Dict[str, Any], name: str) -> float | None:
    value = _num(_value(package, name))
    if value is not None and name in {"grossMarginRate", "conversionRate", "clickRate", "refundRate", "rating"} and abs(value) > 2:
        return value / 100
    return value


def _delta(package: Dict[str, Any], name: str) -> float | None:
    aliases = set(ALIASES.get(name, [name]))
    for row in _walk(package):
        code = str(row.get("metricCode") or row.get("code") or row.get("metricName") or row.get("label") or row.get("name") or "")
        if code not in aliases and not any(str(key) in aliases for key in row):
            continue
        for key in ("changeRatio", "changeRate", "deltaRate", "changeVsPrevious", "changeVsAvg"):
            if key in row:
                value = _num(row.get(key))
                return value / 100 if value is not None and abs(value) > 2 else value
        current = next((_num(row.get(key)) for key in ("currentValue", "current", "latest", "value") if _num(row.get(key)) is not None), None)
        previous = next((_num(row.get(key)) for key in ("previousValue", "previous", "before", "oldValue") if _num(row.get(key)) is not None), None)
        if current is not None and previous not in {None, 0}:
            return (current - previous) / previous
    return None


def _identity(package: Dict[str, Any]) -> Dict[str, Any]:
    identity = _dict(package.get("productIdentity"))
    title = package.get("productTitle") or package.get("title") or identity.get("productTitle") or identity.get("title")
    return {
        **identity,
        "productId": package.get("productId") or identity.get("productId"),
        "storeId": package.get("storeId") or identity.get("storeId"),
        "productTitle": title,
        "title": title,
    }


def _margin(package: Dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    price = _metric(package, "price")
    cost = _metric(package, "cost")
    rate = _metric(package, "grossMarginRate")
    profit = _metric(package, "grossProfitAmount")
    if profit is None and price is not None and cost is not None:
        profit = price - cost
    if rate is None and price not in {None, 0} and profit is not None:
        rate = profit / price
    return price, cost, rate, profit


def _traffic(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in _walk(package):
        name = row.get("sourceName") or row.get("trafficSource") or row.get("渠道") or row.get("渠道名称")
        if _blank(name) or str(name) in seen:
            continue
        seen.add(str(name))
        item = {
            "sourceName": name,
            "visitors": row.get("visitors") or row.get("visitorCount") or row.get("访客"),
            "clickRate": row.get("clickRate") or row.get("点击率"),
            "conversionRate": row.get("conversionRate") or row.get("转化率"),
            "roi": row.get("roi") or row.get("ROI"),
        }
        result.append({key: value for key, value in item.items() if not _blank(value)})
        if len(result) >= 10:
            break
    return result


def _ad_plans(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in _walk(package):
        plan_id = row.get("adPlanId") or row.get("planId") or row.get("campaignId") or row.get("广告计划ID")
        plan_name = row.get("adPlanName") or row.get("planName") or row.get("campaignName") or row.get("计划名称")
        key = str(plan_id or plan_name or "")
        if not key or key in seen:
            continue
        seen.add(key)
        item = {
            "planId": plan_id,
            "planName": plan_name,
            "dailyBudget": row.get("dailyBudget") or row.get("budget") or row.get("预算"),
            "targetROI": row.get("targetROI") or row.get("目标ROI"),
            "currentROI": row.get("roi") or row.get("ROI"),
            "adSpend": row.get("adSpend") or row.get("广告消耗"),
            "paymentAmount": row.get("paymentAmount") or row.get("支付金额"),
        }
        result.append({key: value for key, value in item.items() if not _blank(value)})
        if len(result) >= 8:
            break
    return result


def _permission_bounds(package: Dict[str, Any]) -> Dict[str, Any]:
    cross = _dict(package.get("crossValidation"))
    policy = _dict(package.get("experimentPolicy") or cross.get("experimentPolicy"))
    return {
        key: policy.get(key)
        for key in (
            "experimentMode",
            "actionIntensity",
            "targetObject",
            "trafficShareCeiling",
            "budgetChangeCeiling",
            "durationHours",
            "mainlineMutationAllowed",
            "rollbackRequired",
            "allowed",
        )
        if policy.get(key) is not None
    }


def _inventory_coordination(package: Dict[str, Any]) -> Dict[str, Any]:
    days = _metric(package, "availableDays")
    stock = _metric(package, "inventory")
    level = "unknown" if days is None else "critical" if days < 3 else "urgent" if days < 7 else "watch" if days < 14 else "normal"
    required = level in {"critical", "urgent", "watch"}
    return {
        "ownerDepartment": "warehouse",
        "required": required,
        "urgency": level,
        "inventory": stock,
        "availableDays": days,
        "operatorResponsibility": "发起仓储协同并等待真实库存反馈，运营不承担库存决策。",
        "warehouseRequiredResponse": ["实际库存", "在途库存", "补货数量", "预计到仓时间"],
        "trafficControlRule": "库存不能单独触发暂停广告、停止放量或断流。",
    }


def _base_pack(package: Dict[str, Any], family: str) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "actionFamily": family,
        "compilerRole": "facts_permissions_and_numeric_limits_only",
        "strategyDecisionOwnedBy": "Agent2",
        "permissionBounds": _permission_bounds(package),
        "inventoryCoordination": _inventory_coordination(package),
        "trafficSourceSummary": _traffic(package),
        "fallbackAllowed": False,
    }


def build_title_image_parameter_pack(package: Dict[str, Any]) -> Dict[str, Any]:
    identity = _identity(package)
    missing = [key for key, value in {"productTitle": identity.get("productTitle"), "platform": identity.get("platform"), "verticalCategory": identity.get("verticalCategory")}.items() if _blank(value)]
    return {
        **_base_pack(package, "title_image_test"),
        "status": "creative_context_ready" if not missing else "insufficient",
        "missingMetrics": missing,
        "currentClickRate": _metric(package, "clickRate"),
        "clickRateDelta": _delta(package, "clickRate"),
        "conversionRateDelta": _delta(package, "conversionRate"),
        "creativeContext": {
            "productTitle": identity.get("productTitle"),
            "platform": identity.get("platform"),
            "verticalCategory": identity.get("verticalCategory"),
            "priceBand": identity.get("priceBand"),
            "productRole": identity.get("productRole"),
        },
        "outputBounds": {"creativeGroupMin": 2, "creativeGroupMax": 5},
        "reviewMetrics": ["点击率", "点击量", "转化率", "支付金额"],
    }


def build_roas_parameter_pack(package: Dict[str, Any], family: str = "roas_scale") -> Dict[str, Any]:
    ad_spend = _metric(package, "adSpend")
    payment = _metric(package, "paymentAmount")
    roi = _metric(package, "roi")
    price, cost, margin, profit = _margin(package)
    break_even = 1 / margin if margin and margin > 0 else None
    safety = break_even * 1.15 if break_even else None
    missing = [key for key, value in {"adSpend": ad_spend, "roi": roi, "grossMarginRate": margin}.items() if value is None]
    return {
        **_base_pack(package, family),
        "status": "valid" if not missing else "insufficient",
        "missingMetrics": missing,
        "currentAdSpend": ad_spend,
        "currentPaymentAmount": payment,
        "currentROI": roi,
        "grossMarginRate": margin,
        "grossProfitAmount": profit,
        "currentPrice": price,
        "productCost": cost,
        "breakEvenROI": break_even,
        "safetyROI": safety,
        "roiHeadroom": roi - safety if roi is not None and safety is not None else None,
        "metricChanges": {
            "adSpend": _delta(package, "adSpend"),
            "paymentAmount": _delta(package, "paymentAmount"),
            "paidVisitors": _delta(package, "paidVisitors"),
            "organicVisitors": _delta(package, "organicVisitors"),
            "conversionRate": _delta(package, "conversionRate"),
        },
        "adPlanFacts": _ad_plans(package),
        "executionObjectContract": {
            "mode": "explicit_plan" if _ad_plans(package) else "selector_rule_required",
            "rule": "有真实计划时指定计划；否则必须给出可复核的筛选规则。",
        },
        "reviewMetrics": ["广告消耗", "ROI/ROAS", "支付金额", "付费访客", "自然访客"],
    }


def build_activity_parameter_pack(package: Dict[str, Any], family: str = "platform_activity") -> Dict[str, Any]:
    price, cost, margin, profit = _margin(package)
    max_discount = max(0, profit * 0.25) if profit is not None else None
    missing = [key for key, value in {"grossProfitAmount": profit, "price": price}.items() if value is None]
    return {
        **_base_pack(package, family),
        "status": "valid" if not missing else "insufficient",
        "missingMetrics": missing,
        "currentPrice": price,
        "productCost": cost,
        "grossProfitAmount": profit,
        "grossMarginRate": margin,
        "maxDiscountAmount": max_discount,
        "metricChanges": {
            "paymentAmount": _delta(package, "paymentAmount"),
            "organicVisitors": _delta(package, "organicVisitors"),
            "conversionRate": _delta(package, "conversionRate"),
        },
        "refundRate": _metric(package, "refundRate"),
        "reviewMetrics": ["自然访客", "点击率", "转化率", "支付金额", "券后毛利", "退款率"],
    }


def build_conversion_parameter_pack(package: Dict[str, Any], family: str = "conversion_repair") -> Dict[str, Any]:
    return {
        **_base_pack(package, family),
        "status": "valid",
        "missingMetrics": [],
        "conversionRate": _metric(package, "conversionRate"),
        "conversionRateDelta": _delta(package, "conversionRate"),
        "clickRate": _metric(package, "clickRate"),
        "paymentAmountDelta": _delta(package, "paymentAmount"),
        "grossMarginRate": _margin(package)[2],
        "grossProfitAmount": _margin(package)[3],
        "refundRate": _metric(package, "refundRate"),
        "rating": _metric(package, "rating"),
        "reviewMetrics": ["转化率", "支付金额", "点击率", "退款率", "评分"],
    }


def build_similar_product_parameter_pack(package: Dict[str, Any]) -> Dict[str, Any]:
    identity = _identity(package)
    return {
        **_base_pack(package, "similar_product_test"),
        "status": "valid" if identity.get("verticalCategory") else "insufficient",
        "missingMetrics": [] if identity.get("verticalCategory") else ["verticalCategory"],
        "productIdentity": identity,
        "reviewMetrics": ["点击率", "转化率", "支付金额", "ROI/ROAS"],
    }


def build_parameter_packs(package: Dict[str, Any]) -> Dict[str, Any]:
    item = attach_matrix_dispatch(package)
    family = selected_family(item)
    if family == "title_image_test":
        pack = build_title_image_parameter_pack(item)
    elif family in {"roas_scale", "roas_guard"}:
        pack = build_roas_parameter_pack(item, family)
    elif family in {"platform_activity", "activity_apply"}:
        pack = build_activity_parameter_pack(item, family)
    elif family in {"conversion_repair", "service_repair"}:
        pack = build_conversion_parameter_pack(item, family)
    elif family == "similar_product_test":
        pack = build_similar_product_parameter_pack(item)
    else:
        raise ValueError("unsupported_locked_action_family")
    return {family: pack}


def select_action_parameter_pack(package: Dict[str, Any], family: str | None = None) -> Dict[str, Any]:
    selected = family or selected_family(package)
    packs = _dict(package.get("actionParameterPacks"))
    existing = packs.get(selected)
    single = package.get("actionParameterPack")
    if isinstance(existing, dict) and existing:
        return existing
    if isinstance(single, dict) and single.get("actionFamily") == selected:
        return single
    return build_parameter_packs(package).get(selected) or {}


def enrich_package_with_action_parameters(package: Dict[str, Any]) -> Dict[str, Any]:
    item = attach_matrix_dispatch(package)
    family = selected_family(item)
    packs = build_parameter_packs(item)
    pack = dict(packs[family])
    rag_snapshot = build_agent_rag_context_snapshot(item, pack)
    pack["ragContextSummary"] = rag_context_summary(rag_snapshot)
    pack["ragContextVersion"] = VERSION
    pack["dynamicRagIsTaskGate"] = False
    packs[family] = pack
    item.update(
        actionFamily=family,
        selectedActionFamily=family,
        actionParameterPacks=packs,
        actionParameterPack=pack,
        actionPackStatus=pack.get("status"),
        actionPackCoreVersion=VERSION,
        responsibilityContractVersion=VERSION,
        ragContextSnapshot=rag_snapshot,
        ragContextSummary=rag_context_summary(rag_snapshot),
        ragRetrievalCount=int(rag_snapshot.get("retrievalCount") or 0),
        dynamicRagStatus=rag_snapshot.get("status"),
        capabilityCompilerVersion=VERSION,
    )
    item.pop("selectedActionFamilyHint", None)
    return item


def compose_parameterized_sop(
    family: str,
    pack: Dict[str, Any],
    plan: Dict[str, Any] | None = None,
    package: Dict[str, Any] | None = None,
) -> List[str]:
    del family, pack, package
    return [str(item).strip() for item in _arr(_dict(plan).get("operatorActionSteps")) if str(item).strip()]


def install_action_pack_core() -> None:
    return None


def action_parameter_enrichment_station_core(data_version: str | None, **_: Any) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "stationId": "action_parameter_enrichment_station",
        "dataVersion": data_version,
        "matrixDispatchVersion": VERSION,
        "actionPackCoreVersion": VERSION,
        "ragContextVersion": VERSION,
        "runtimeSource": "pipeline_items.agent1_completed",
        "fallbackAllowed": False,
        "rule": "V22 compiles facts, objects, permissions and numeric limits only.",
    }


__all__ = [
    "ACTION_PACK_CORE_VERSION",
    "ACTION_PARAMETER_ENRICHMENT_VERSION",
    "build_parameter_packs",
    "select_action_parameter_pack",
    "enrich_package_with_action_parameters",
    "compose_parameterized_sop",
]
