"""V19.4 operator action family contracts.

V19.3 lets the Agent select the operating route. V19.4 adds the second decision:
which operating action family should be used to attack the selected route.
Not every growth task is a title/main-image test.
"""

from __future__ import annotations

from typing import Any, Dict, List

ACTION_FAMILY_VERSION = "19.4"

TITLE_IMAGE_TEST = "title_image_test"
PLATFORM_ACTIVITY = "platform_activity"
ROAS_SCALE = "roas_scale"
ROAS_GUARD = "roas_guard"
CONVERSION_REPAIR = "conversion_repair"
SIMILAR_PRODUCT_TEST = "similar_product_test"

ACTION_FAMILIES = {
    TITLE_IMAGE_TEST: {
        "label": "标题/主图测试",
        "when": "点击率下降、搜索承接不足、主图表达或场景词缺口。",
        "requiredFields": ["titleVariants", "mainImageStructures"],
    },
    PLATFORM_ACTIVITY: {
        "label": "平台活动报名",
        "when": "自然流量上涨、GMV上涨、转化稳定，适合承接平台或类目自然流量窗口。",
        "requiredFields": ["activityPlan", "activityEligibilityChecklist", "activityMaterialChecklist"],
    },
    ROAS_SCALE: {
        "label": "ROAS放量",
        "when": "广告消耗增加后GMV/支付金额同步上涨，ROAS/ROI未明显恶化。",
        "requiredFields": ["budgetAdjustmentPlan", "campaignSelectionRule", "stopLossRule"],
    },
    ROAS_GUARD: {
        "label": "ROAS收缩",
        "when": "广告消耗上涨但GMV没跟上，转化/ROI/ROAS恶化。",
        "requiredFields": ["cutBudgetPlan", "lowEfficiencyPlanList", "preserveTrafficRule"],
    },
    CONVERSION_REPAIR: {
        "label": "转化承接修复",
        "when": "点击或流量仍在，但转化率下降，需修详情页、价格、优惠、评价、客服承接。",
        "requiredFields": ["conversionBlockers", "detailPageChecklist", "priceOrCouponPlan"],
    },
    SIMILAR_PRODUCT_TEST: {
        "label": "同类商品承接测试",
        "when": "类目窗口存在但单品承接不稳，需要判断是单品问题还是类目机会。",
        "requiredFields": ["comparisonProducts", "trafficSplitPlan", "testMetric"],
    },
}


def _text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values)


def _changes(package: Dict[str, Any], route: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = package.get("correlatedMetricChanges") or package.get("allMetricChanges") or route.get("correlatedMetricChanges") or route.get("allMetricChanges") or []
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def build_action_family_candidates(package: Dict[str, Any], forecast: Dict[str, Any] | None = None, route: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    """Recall possible operating action families; Agent selects exactly one."""
    forecast = forecast or {}
    route = route or {}
    raw = _text(package.get("triggerMetric"), package.get("primaryRisk"), package.get("candidateReason"), forecast.get("forecastSummary"), route.get("routeName"), route.get("routeFamily"), *[_text(x.get("metricCode"), x.get("metricName"), x.get("summary"), x.get("changeRatio")) for x in _changes(package, route)[:8]])
    candidates: List[Dict[str, Any]] = []

    def add(action_family: str, evidence: List[str], risk: str) -> None:
        if action_family in {item.get("actionFamily") for item in candidates}:
            return
        profile = ACTION_FAMILIES[action_family]
        candidates.append({"actionFamily": action_family, "label": profile["label"], "when": profile["when"], "evidence": evidence, "risk": risk, "requiredFields": profile["requiredFields"]})

    if any(word in raw for word in ["自然", "访客", "搜索", "流量", "GMV", "支付", "增长"]):
        add(PLATFORM_ACTIVITY, ["自然流量或销售趋势可能进入平台/类目窗口"], "若转化不稳或活动门槛不满足，报名活动会浪费窗口。")
    if any(word in raw for word in ["广告", "ROAS", "ROI", "投产", "消耗"]):
        add(ROAS_SCALE, ["广告消耗与销售存在联动，需要判断是否可以放量"], "若ROAS已被稀释，盲目放量会扩大亏损。")
        add(ROAS_GUARD, ["广告消耗或ROI/ROAS波动需要保护预算"], "若误收缩，会错过增长窗口。")
    if any(word in raw for word in ["点击", "CTR", "主图", "标题", "关键词"]):
        add(TITLE_IMAGE_TEST, ["点击效率或搜索承接需要验证"], "不能把所有增长机会都误判为标题/主图问题。")
    if any(word in raw for word in ["转化", "CVR", "详情", "价格", "评价", "客服"]):
        add(CONVERSION_REPAIR, ["点击或流量后的成交承接可能受阻"], "若流量本身不足，先修转化可能看不到结果。")
    add(SIMILAR_PRODUCT_TEST, ["需要判断类目窗口是否只属于单品，还是同类商品可承接"], "若同类商品基础差，会稀释测试资源。")
    add(TITLE_IMAGE_TEST, ["默认保留标题/主图测试作为点击缺口打法"], "只有点击/搜索承接问题明显时才优先使用。")
    return candidates[:6]


def action_family_profile(action_family: str) -> Dict[str, Any]:
    return {"actionFamily": action_family, **ACTION_FAMILIES.get(action_family, ACTION_FAMILIES[TITLE_IMAGE_TEST])}


def _has_list_or_text(plan: Dict[str, Any], key: str) -> bool:
    value = plan.get(key)
    if isinstance(value, list):
        return len(value) > 0
    if isinstance(value, dict):
        return bool(value)
    return bool(str(value or "").strip())


def action_family_contract_ok(plan: Dict[str, Any]) -> bool:
    family = str(plan.get("selectedActionFamily") or "").strip()
    if family not in ACTION_FAMILIES:
        return False
    required = ACTION_FAMILIES[family]["requiredFields"]
    return all(_has_list_or_text(plan, key) for key in required)


def action_family_public_label(action_family: str) -> str:
    return ACTION_FAMILIES.get(action_family, ACTION_FAMILIES[TITLE_IMAGE_TEST])["label"]
