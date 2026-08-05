"""V18.8 context-driven operating forecast.

Data deltas are facts. This deterministic layer is not allowed to write the final
operator SOP. It only prepares:
- product identity / store context / category context;
- rounded operating targets and action boundaries;
- fallback template hints for logs only.

The task-mapping Agent must combine these inputs with product, store and category
context to write the final flexible SOP.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List

OPERATING_TREND_FORECAST_VERSION = "18.8"
SAFETY_BUFFER = 1.15
DEFAULT_REPLENISH_PACK_SIZE = 10

CATEGORY_RULES: Dict[str, Dict[str, Any]] = {
    "服装": {"name": "服装类目", "inventoryUnit": "颜色/尺码", "sopFocus": ["优先补热销颜色/尺码", "主力链接不大改首图和核心标题", "用副图/副链接/低权重店铺测试新标题主图"]},
    "美妆": {"name": "美妆类目", "inventoryUnit": "规格/批次", "sopFocus": ["补货同时检查功效词、成分词和资质图", "关注过敏/刺激/假滑等评价关键词", "放量前确认合规承诺"]},
    "食品": {"name": "食品类目", "inventoryUnit": "批次/保质期", "sopFocus": ["补货按保质期和动销周期倒推", "关注破损率、仓储和物流时效", "活动放量前确认临期库存风险"]},
    "日用品": {"name": "日用品类目", "inventoryUnit": "规格/组合装", "sopFocus": ["库存确认后可快速铺货复制", "适合多个标题/主图结构测试", "允许10%-20%小幅预算扩量"]},
    "default": {"name": "通用类目", "inventoryUnit": "SKU", "sopFocus": ["按主SKU和动销结构补货", "主链接先保护权重", "用3天测试验证增长是否可持续"]},
}

ENGINEERING_KEYS = ["relationConfidence", "candidateSignal", "routeSignalStrength", "metricSignalConfidence", "taskActionLevel"]


def _num(value: Any) -> float | None:
    if value in {None, "", "—", "未识别"}:
        return None
    try:
        return float(str(value).replace("¥", "").replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def _ratio(value: Any) -> float | None:
    raw = _num(value)
    if raw is None:
        return None
    return raw / 100 if abs(raw) > 1.5 else raw


def _fmt(value: Any, digits: int = 1) -> str:
    n = _num(value)
    if n is None:
        return "待确认"
    if abs(n - round(n)) < 0.0001:
        return str(int(round(n)))
    return f"{n:.{digits}f}".rstrip("0").rstrip(".")


def _ceil_int(value: Any) -> int | None:
    n = _num(value)
    if n is None:
        return None
    return int(math.ceil(max(0, n)))


def _ceil_to_pack(value: Any, pack_size: int = DEFAULT_REPLENISH_PACK_SIZE) -> int | None:
    n = _ceil_int(value)
    if n is None:
        return None
    pack = max(1, int(pack_size or 1))
    return int(math.ceil(n / pack) * pack)


def _pct(value: Any) -> str:
    n = _ratio(value)
    if n is None:
        return "待确认"
    return f"{n * 100:+.1f}%"


def _as_list(value: Any) -> List[Dict[str, Any]]:
    return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []


def _full_evidence(package: Dict[str, Any]) -> Dict[str, Any]:
    return package.get("fullProductBundleEvidence") if isinstance(package.get("fullProductBundleEvidence"), dict) else {}


def _metric_evidence(package: Dict[str, Any]) -> Dict[str, Any]:
    evidence = _full_evidence(package)
    return evidence.get("metricEvidence") if isinstance(evidence.get("metricEvidence"), dict) else {}


def _route(package: Dict[str, Any]) -> Dict[str, Any]:
    evidence = _full_evidence(package)
    if isinstance(package.get("operatingGraphRoute"), dict) and package.get("operatingGraphRoute"):
        return package.get("operatingGraphRoute") or {}
    if isinstance(evidence.get("operatingGraphRoute"), dict):
        return evidence.get("operatingGraphRoute") or {}
    return {}


def _changes(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    evidence = _full_evidence(package)
    route = _route(package)
    for key in ["dynamicMetricChanges", "correlatedMetricChanges", "allMetricChanges"]:
        for source in [package, evidence, route]:
            values = source.get(key) if isinstance(source, dict) else None
            if isinstance(values, list) and values:
                return _as_list(values)
    return []


def _metric_from_changes(changes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in changes:
        code = str(item.get("metricCode") or "").strip()
        if code:
            out[code] = item
    return out


def _metric(package: Dict[str, Any], code: str) -> Dict[str, Any]:
    changes = _metric_from_changes(_changes(package))
    if code in changes:
        item = changes[code]
        return {"current": item.get("currentValue"), "previous": item.get("previousValue"), "changeRatio": _ratio(item.get("changeRatio") if item.get("changeRatio") is not None else item.get("changeRate")), "summary": item.get("summary")}
    raw = _metric_evidence(package).get(code)
    if isinstance(raw, dict):
        return {"current": raw.get("current") if raw.get("current") is not None else raw.get("currentValue") if raw.get("currentValue") is not None else raw.get("latest"), "previous": raw.get("previous") if raw.get("previous") is not None else raw.get("previousValue"), "changeRatio": _ratio(raw.get("changeRatio") if raw.get("changeRatio") is not None else raw.get("changeRate") if raw.get("changeRate") is not None else raw.get("changeVsPrevious")), "summary": None}
    return {"current": None, "previous": None, "changeRatio": None, "summary": None}


def _direction(package: Dict[str, Any], code: str) -> str:
    ratio = _metric(package, code).get("changeRatio")
    if ratio is None or abs(float(ratio)) < 1e-9:
        return "flat"
    return "up" if float(ratio) > 0 else "down"


def _has_up(package: Dict[str, Any], codes: List[str]) -> bool:
    return any(_direction(package, code) == "up" for code in codes)


def _has_down(package: Dict[str, Any], codes: List[str]) -> bool:
    return any(_direction(package, code) == "down" for code in codes)


def _contains(text: str, tokens: List[str]) -> bool:
    return any(token in text for token in tokens)


def build_product_identity(package: Dict[str, Any]) -> Dict[str, Any]:
    evidence = _full_evidence(package)
    title = evidence.get("title") or package.get("productTitle") or package.get("title") or package.get("productName")
    product_id = str(package.get("productId") or evidence.get("productId") or "").strip()
    store_id = str(package.get("storeId") or evidence.get("storeId") or "GLOBAL").strip()
    return {
        "productId": product_id,
        "systemProductCode": package.get("systemProductCode") or package.get("productCode") or product_id,
        "productTitle": title or product_id or "未命名商品",
        "shortTitle": evidence.get("shortTitle") or title or product_id or "未命名商品",
        "storeId": store_id,
        "storeName": evidence.get("storeName") or package.get("storeName") or "经营单元",
        "platform": evidence.get("platform") or package.get("platform") or "经营平台",
        "skuId": package.get("skuId") or evidence.get("skuId") or "",
        "platformItemId": package.get("platformItemId") or evidence.get("platformItemId") or product_id,
        "productUrl": package.get("productUrl") or evidence.get("productUrl") or "",
        "mainImageUrl": package.get("mainImageUrl") or evidence.get("mainImageUrl") or "",
        "verticalCategory": evidence.get("verticalCategory") or package.get("verticalCategory") or "未归类",
    }


def build_store_context(package: Dict[str, Any], product_identity: Dict[str, Any]) -> Dict[str, Any]:
    route = _route(package)
    return {
        "storeId": product_identity.get("storeId"),
        "storeName": product_identity.get("storeName"),
        "platform": product_identity.get("platform"),
        "storeRole": package.get("storeRole") or "未标注",
        "storeWeight": package.get("storeWeight") or ("高" if route.get("routeSignalStrength") == "strong" else "中"),
        "trafficStructure": "自然/成交共振" if _has_up(package, ["organicVisitors", "gmv", "paymentAmount"]) else "待确认",
        "actionStrengthHint": "主力店铺优先保护链接权重；测试店铺可更激进测试。",
    }


def build_category_context(package: Dict[str, Any], product_identity: Dict[str, Any]) -> Dict[str, Any]:
    raw = str(product_identity.get("verticalCategory") or "")
    key = "default"
    if _contains(raw, ["服装", "衣", "鞋", "帽", "裤", "裙"]):
        key = "服装"
    elif _contains(raw, ["美妆", "护肤", "彩妆", "香水"]):
        key = "美妆"
    elif _contains(raw, ["食品", "零食", "饮料", "生鲜"]):
        key = "食品"
    elif _contains(raw, ["日用", "家居", "清洁", "纸巾"]):
        key = "日用品"
    rule = CATEGORY_RULES.get(key, CATEGORY_RULES["default"])
    return {"verticalCategory": raw or "未归类", "categoryFamily": key, **rule}


def _inventory_forecast(package: Dict[str, Any], category: Dict[str, Any]) -> Dict[str, Any]:
    inventory = _metric(package, "inventory")
    available = _metric(package, "availableDays")
    current_inventory = _num(inventory.get("current"))
    current_days = _num(available.get("current"))
    daily_units = current_inventory / current_days if current_inventory is not None and current_days and current_days > 0 else None
    three_day = daily_units * 3 if daily_units is not None else None
    seven_day = daily_units * 7 if daily_units is not None else None
    raw_target = seven_day * SAFETY_BUFFER if seven_day is not None else None
    raw_gap = max(0.0, raw_target - current_inventory) if raw_target is not None and current_inventory is not None else None
    target_units = _ceil_to_pack(raw_target)
    gap_units = _ceil_to_pack(raw_gap)
    gmv_up = _has_up(package, ["gmv", "paymentAmount"])
    traffic_up = _has_up(package, ["organicVisitors", "paidVisitors", "visitorCount", "clickRate"])
    refund_up = _has_up(package, ["refundRate", "afterSalesRate", "refundOrderCount", "refundAmount"])
    if gmv_up or traffic_up:
        scenario, name = "growth_capacity_gap", "增长承接不足"
        summary = f"库存只能支撑约{_fmt(current_days)}天，但GMV/支付或流量正在增强，未来风险是断货打断增长窗口和自然流量权重。"
        risk, opportunity = ("高" if current_days is not None and current_days <= 2 else "中"), "高"
    elif refund_up:
        scenario, name = "inventory_quality_after_sales_risk", "动销伴随售后风险"
        summary = "库存下降同时售后风险抬头，需要先判断放量是否暴露质量、尺码、承诺或客服问题。"
        risk, opportunity = "高", "中"
    else:
        scenario, name = "inventory_supply_risk", "供给承接风险"
        summary = "库存与可售天数下降，但缺少成交/流量共振，先按供给承接风险处理。"
        risk, opportunity = "中", "中"
    return {
        "scenario": scenario,
        "scenarioName": name,
        "opportunityLevel": opportunity,
        "riskLevel": risk,
        "forecastSummary": summary,
        "calculatedTargets": {
            "currentInventory": _ceil_int(current_inventory),
            "currentAvailableDays": current_days,
            "estimatedDailySalesUnits": daily_units,
            "threeDayInventoryNeed": _ceil_int(three_day),
            "sevenDayInventoryNeed": _ceil_int(seven_day),
            "rawTargetInventoryWith15PctBuffer": raw_target,
            "targetInventoryUnits": target_units,
            "rawRecommendedReplenishmentUnits": raw_gap,
            "recommendedReplenishmentUnits": gap_units,
            "roundingRule": f"库存/补货向上取整；未知箱规按{DEFAULT_REPLENISH_PACK_SIZE}件档位展示，后续可接ERP箱规/起订量。",
            "stockoutRiskDays": current_days,
            "inventoryUnitLogic": category.get("inventoryUnit"),
        },
        "actionBoundaries": ["补货确认前不盲目增投；补货确认后才进入增长测试。", "主力店铺优先保护自然流量权重；测试动作尽量放到副图、副链接或测试店铺。"],
        "agentGuidanceBullets": ["不要写小数件补货。", "结合类目说明补货颗粒度。", "根据店铺角色决定是保护权重还是激进测试。"],
        "fallbackSopTemplate": ["将库存目标补至整数安全线", "确认48小时内到货/调拨/预售方案", "补货前稳定投放不盲目增投", "补货后再做3天标题/主图/铺货测试"],
        "evidenceRequirements": ["供应商/仓库到货时间截图", "当前库存与在途库存截图", "广告计划稳定投放或调整记录截图"],
        "reviewMetrics": ["库存", "可售天数", "GMV", "支付金额", "自然访客", "ROAS", "退款率"],
    }


def _refund_forecast(package: Dict[str, Any]) -> Dict[str, Any]:
    refund_rate = _metric(package, "refundRate")
    refund_amount = _metric(package, "refundAmount")
    payment = _metric(package, "paymentAmount")
    current_rate = _num(refund_rate.get("current"))
    previous_rate = _num(refund_rate.get("previous"))
    current_refund_amount = _num(refund_amount.get("current"))
    payment_current = _num(payment.get("current"))
    est_loss = current_refund_amount if current_refund_amount is not None else payment_current * (current_rate / 100 if current_rate and current_rate > 1 else current_rate or 0) if payment_current is not None and current_rate is not None else None
    return {
        "scenario": "scaling_after_sales_exposure" if _has_up(package, ["gmv", "paymentAmount", "adSpend", "visitorCount"]) else "after_sales_trend_risk",
        "scenarioName": "售后趋势风险",
        "opportunityLevel": "中",
        "riskLevel": "高" if (refund_rate.get("changeRatio") or 0) > 0.3 else "中",
        "forecastSummary": "退款/售后变化需要预估未来退款金额、毛利侵蚀和转化反噬；不能只写收集原因。",
        "calculatedTargets": {"currentRefundRate": current_rate, "previousRefundRate": previous_rate, "refundRateChange": refund_rate.get("changeRatio"), "estimatedRefundLossAmount": est_loss, "refundRateRecoveryLine": previous_rate},
        "actionBoundaries": ["原因未分类前不继续扩大低效付费流量。", "若质量/承诺类退款继续上升，暂停该SKU放量。"],
        "agentGuidanceBullets": ["必须按退款原因Top5拆动作。", "区分放量正常售后与质量/承诺风险。"],
        "fallbackSopTemplate": ["导出退款订单原因Top5", "对照详情页承诺/客服话术/评价关键词", "估算退款损失并保护毛利", "3天后复盘退款率与转化率"],
        "evidenceRequirements": ["退款订单明细及原因截图", "客服聊天/差评关键词截图", "详情页承诺和主图标题截图"],
        "reviewMetrics": ["退款率", "售后率", "退款金额", "转化率", "GMV", "ROAS"],
    }


def _conversion_forecast(package: Dict[str, Any]) -> Dict[str, Any]:
    conv = _metric(package, "conversionRate")
    current = _num(conv.get("current"))
    previous = _num(conv.get("previous"))
    gap = previous - current if previous is not None and current is not None else None
    return {
        "scenario": "traffic_conversion_mismatch" if _has_up(package, ["visitorCount", "organicVisitors", "paidVisitors"]) and _direction(package, "conversionRate") == "down" else "conversion_capacity_change",
        "scenarioName": "转化承接趋势",
        "opportunityLevel": "中",
        "riskLevel": "高" if gap is not None and gap > 0 else "中",
        "forecastSummary": "未来关键是恢复转化率基线并拆分自然/付费入口质量，而不是继续堆流量。",
        "calculatedTargets": {"currentConversionRate": current, "previousConversionRate": previous, "conversionRecoveryGap": gap, "targetConversionRate": previous, "testCycleDays": 3},
        "actionBoundaries": ["先恢复转化承接，不盲目扩大流量。", "若付费访客上升但转化下降，优先收紧人群/关键词。"],
        "agentGuidanceBullets": ["根据自然/付费流量差异写动作。", "写清主图/标题/详情页测试周期。"],
        "fallbackSopTemplate": ["设定上一期转化率为恢复线", "拆分自然/付费访客转化", "做2-3套主图标题测试", "3天后复盘转化是否回线"],
        "evidenceRequirements": ["详情页首屏截图", "付费/自然访客与转化拆分截图", "主图标题测试方案截图"],
        "reviewMetrics": ["转化率", "点击率", "自然访客", "付费访客", "GMV", "支付金额"],
    }


def _ad_forecast(package: Dict[str, Any]) -> Dict[str, Any]:
    roas = _metric(package, "roas")
    roi = _metric(package, "roi")
    current = _num(roas.get("current") if roas.get("current") is not None else roi.get("current"))
    previous = _num(roas.get("previous") if roas.get("previous") is not None else roi.get("previous"))
    adjustment = max(-0.4, min(0.25, (current - previous) / previous)) if current is not None and previous and previous > 0 else None
    return {
        "scenario": "ad_efficiency_forecast",
        "scenarioName": "投放效率趋势",
        "opportunityLevel": "中" if adjustment is None or adjustment >= 0 else "低",
        "riskLevel": "高" if adjustment is not None and adjustment < -0.2 else "中",
        "forecastSummary": "投放效率要预估未来广告费是否继续侵蚀成交和毛利，不能一刀切停投。",
        "calculatedTargets": {"currentRoasOrRoi": current, "previousRoasOrRoi": previous, "suggestedBudgetAdjustmentRatio": adjustment, "budgetAdjustmentText": _pct(adjustment) if adjustment is not None else "待确认"},
        "actionBoundaries": ["不暂停全部投放；只处理低效计划。", "保留高ROAS/高转化计划。"],
        "agentGuidanceBullets": ["写清预算调整比例和保留/暂停边界。"],
        "fallbackSopTemplate": ["以上一期ROAS/ROI为安全线", "下调低效预算并保留高效计划", "24小时后复盘广告消耗和成交", "回线后再恢复10%-20%测试预算"],
        "evidenceRequirements": ["广告计划ROAS/消耗截图", "低效关键词或人群包截图", "预算调整记录截图"],
        "reviewMetrics": ["ROAS", "ROI", "广告消耗", "GMV", "转化率", "点击率"],
    }


def _growth_forecast(package: Dict[str, Any]) -> Dict[str, Any]:
    gmv = _metric(package, "gmv")
    payment = _metric(package, "paymentAmount")
    growth = gmv.get("changeRatio") if gmv.get("changeRatio") is not None else payment.get("changeRatio")
    inventory_down = _has_down(package, ["inventory", "availableDays"])
    refund_up = _has_up(package, ["refundRate", "afterSalesRate"])
    return {
        "scenario": "growth_window_forecast",
        "scenarioName": "增长机会窗口",
        "opportunityLevel": "高",
        "riskLevel": "高" if inventory_down or refund_up else "中",
        "forecastSummary": "GMV/支付增长要判断库存、售后、毛利是否能承接，避免把增长窗口误做成普通观察。",
        "calculatedTargets": {"gmvGrowthRatio": growth, "testBudgetIncreaseRange": "10%-20%", "testCycleDays": 3},
        "actionBoundaries": ["库存和售后未确认前不大幅放量。", "增长测试以3天为周期。"],
        "agentGuidanceBullets": ["写清增长复制条件。", "结合店铺权重决定主链接保护或测试店铺铺货。"],
        "fallbackSopTemplate": ["加入3天增长测试池", "只允许10%-20%小幅扩量", "测试标题/主图/相似商品铺货", "复盘GMV、自然访客、转化和退款"],
        "evidenceRequirements": ["当前标题/主图/价格截图", "库存与退款率截图", "3天测试计划截图"],
        "reviewMetrics": ["GMV", "支付金额", "自然访客", "点击率", "转化率", "库存", "退款率"],
    }


def build_operating_trend_forecast(package: Dict[str, Any]) -> Dict[str, Any]:
    product_identity = build_product_identity(package)
    store_context = build_store_context(package, product_identity)
    category_context = build_category_context(package, product_identity)
    route = _route(package)
    family = str(route.get("routeFamily") or package.get("sceneRoute") or "")
    trigger = str(package.get("triggerMetric") or package.get("primaryRisk") or route.get("triggerMetric") or "")
    if family == "inventory_capacity" or trigger in {"inventory", "stock", "availableDays"}:
        body = _inventory_forecast(package, category_context)
    elif family == "after_sales_risk" or trigger in {"refundRate", "afterSalesRate", "refundAmount", "refundOrderCount"}:
        body = _refund_forecast(package)
    elif family in {"conversion_capacity", "traffic_capacity"} or trigger in {"conversionRate", "clickRate", "visitorCount", "organicVisitors", "paidVisitors"}:
        body = _conversion_forecast(package)
    elif family == "roi_efficiency" or trigger in {"roi", "roas", "adSpend"}:
        body = _ad_forecast(package)
    else:
        body = _growth_forecast(package)
    dynamic = _changes(package)
    return {"version": OPERATING_TREND_FORECAST_VERSION, "forecastRole": "context_driven_future_forecast_input", "triggerMetric": trigger, "routeFamily": family, "routeName": route.get("routeName"), "routeCoverageRate": route.get("routeCoverageRate"), "routeSignalStrength": route.get("routeSignalStrength"), "dynamicMetricChangeCount": len(dynamic), "forecastHorizons": ["3天", "7天", "14天"], "productIdentity": product_identity, "storeContext": store_context, "categoryContext": category_context, "productLifecycle": package.get("productLifecycle") or {"stage": "待判断", "currentRole": "待判断"}, **body, "rule": "V18.8: deterministic forecast prepares identity/context/rounded targets only; final flexible SOP must be generated by mapping Agent."}


def attach_forecast_to_package(package: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(package)
    forecast = build_operating_trend_forecast(item)
    item["operatingTrendForecast"] = forecast
    item["productIdentity"] = forecast.get("productIdentity") or {}
    item["storeContext"] = forecast.get("storeContext") or {}
    item["categoryContext"] = forecast.get("categoryContext") or {}
    item["forecastSummary"] = forecast.get("forecastSummary")
    item["calculatedTargets"] = forecast.get("calculatedTargets") or {}
    item["actionBoundaries"] = forecast.get("actionBoundaries") or []
    item["agentGuidanceBullets"] = forecast.get("agentGuidanceBullets") or []
    item["fallbackSopTemplate"] = forecast.get("fallbackSopTemplate") or []
    item["forecastReviewMetrics"] = forecast.get("reviewMetrics") or []
    item["forecastEvidenceRequirements"] = forecast.get("evidenceRequirements") or []
    item["trendForecastScenario"] = forecast.get("scenario")
    return item
