"""V19.3 multi-route operator judgment context.

The operating graph no longer chooses one route before the Agent. The system
recalls multiple candidate routes, platform style and vertical category context;
the Agent chooses one route in the backend trace. The frontend only reads the
operatorJudgmentView: selected direction, reason, test focus and recap basis.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List

V19_ROUTER_VERSION = "19.3"
OPERATOR_GROWTH = "operator_growth"
SUPPLY_CHAIN = "supply_chain_inventory"
MANAGER_COORDINATION = "manager_coordination"
SYSTEM_WATCH = "system_watch"
ALLOWED_RESPONSIBILITIES = {OPERATOR_GROWTH, SYSTEM_WATCH}

PLATFORM_STYLE_PROFILES: Dict[str, Dict[str, Any]] = {
    "天猫": {"styleName": "质感品牌货架", "titleStyle": "品牌感、质感、场景生活方式", "imageStyle": "精致场景、质感商品图、可信背书", "decisionBias": ["质感表达", "品牌信任", "生活方式场景"]},
    "淘宝": {"styleName": "搜索场景货架", "titleStyle": "场景词、人群词、卖点词组合", "imageStyle": "场景图+卖点说明+价格利益", "decisionBias": ["搜索词承接", "主图点击", "价格/场景匹配"]},
    "京东": {"styleName": "参数可信货架", "titleStyle": "功能明确、参数可信、家用场景", "imageStyle": "白底质感、参数可信、家用空间、售后信任", "decisionBias": ["参数可信", "家用场景", "售后/品质信任"]},
    "拼多多": {"styleName": "低价痛点货架", "titleStyle": "便宜、直给、痛点大字、立即购买理由", "imageStyle": "价格利益、痛点大字、粗暴卖点、前后对比", "decisionBias": ["价格利益", "痛点直给", "低价转化"]},
    "抖音": {"styleName": "内容冲突转化", "titleStyle": "同款、场景冲突、强情绪、短视频表达", "imageStyle": "前后对比、真实场景、强冲突、达人同款", "decisionBias": ["同款感", "场景冲突", "短视频转化"]},
    "小红书": {"styleName": "种草生活方式", "titleStyle": "真实体验、颜值、生活方式、细节感", "imageStyle": "生活方式、真实细节、颜值质感、体验感", "decisionBias": ["种草感", "颜值细节", "真实体验"]},
}

CATEGORY_PROFILES: Dict[str, Dict[str, Any]] = {
    "家用除湿机": {"categoryName": "家用除湿机", "trafficWords": ["梅雨季", "回南天", "卧室", "衣柜", "防潮", "防霉", "静音", "小型", "宿舍"], "painPoints": ["衣服发霉", "墙角潮湿", "被子潮", "房间异味", "小空间湿气"], "buyingTriggers": ["梅雨季", "卧室防潮", "衣柜防霉", "静音家用"], "imagePatterns": ["痛点前后对比", "适用空间图", "参数可信图", "静音家用图"], "titlePatterns": ["空间场景 + 功能痛点 + 参数/体积 + 季节词"], "seasonalWindows": ["梅雨季", "回南天"]},
    "夏季防晒服 / 户外通勤防晒": {"categoryName": "夏季防晒服", "trafficWords": ["UPF", "防紫外线", "轻薄", "透气", "不闷", "通勤", "骑行", "户外", "遮阳"], "painPoints": ["晒黑", "闷热", "不透气", "通勤暴晒"], "buyingTriggers": ["夏季防晒", "户外通勤", "骑行遮阳"], "imagePatterns": ["户外通勤场景", "轻薄透气卖点", "防晒参数背书", "穿搭人群图"], "titlePatterns": ["季节词 + 功能词 + 场景词 + 人群词"], "seasonalWindows": ["夏季", "高温防晒季"]},
    "运动速干T恤": {"categoryName": "运动速干T恤", "trafficWords": ["速干", "透气", "跑步", "健身", "运动", "夏季", "不粘身", "基础款"], "painPoints": ["出汗粘身", "闷热", "运动不透气"], "buyingTriggers": ["跑步健身", "夏季运动", "基础速干"], "imagePatterns": ["运动场景", "面料透气", "出汗前后对比", "基础款价格利益"], "titlePatterns": ["运动场景 + 速干透气 + 人群/季节词"], "seasonalWindows": ["夏季", "运动季"]},
    "户外露营椅": {"categoryName": "户外露营椅", "trafficWords": ["露营", "折叠", "便携", "承重", "钓鱼", "户外", "收纳", "稳定"], "painPoints": ["不好收纳", "不稳", "承重弱", "携带麻烦"], "buyingTriggers": ["露营季", "钓鱼户外", "便携收纳"], "imagePatterns": ["户外场景", "折叠收纳对比", "承重展示", "便携体积图"], "titlePatterns": ["户外场景 + 结构卖点 + 承重/便携参数"], "seasonalWindows": ["春夏露营季", "假期出游"]},
}

CATEGORY_HINTS = [("除湿", "家用除湿机"), ("防晒", "夏季防晒服 / 户外通勤防晒"), ("速干", "运动速干T恤"), ("运动T", "运动速干T恤"), ("露营椅", "户外露营椅"), ("折叠椅", "户外露营椅")]


def _text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values)


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return [item for item in value if item is not None and item != ""]
    if value is not None and value != "":
        return [value]
    return []


def _product_identity(package: Dict[str, Any], forecast: Dict[str, Any]) -> Dict[str, Any]:
    return forecast.get("productIdentity") if isinstance(forecast.get("productIdentity"), dict) else package.get("productIdentity") or {}


def _product_title(package: Dict[str, Any], forecast: Dict[str, Any], fallback: str = "商品") -> str:
    product = _product_identity(package, forecast)
    return str(product.get("shortTitle") or product.get("productTitle") or package.get("productTitle") or package.get("productId") or fallback)


def _event_id(data_version: str | None, package: Dict[str, Any]) -> str:
    raw = "|".join(str(x or "") for x in [data_version, package.get("storeId"), package.get("productId"), package.get("packageId")])
    return "BEV-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()


def platform_style_profile(package: Dict[str, Any], forecast: Dict[str, Any]) -> Dict[str, Any]:
    product = _product_identity(package, forecast)
    platform = str(product.get("platform") or package.get("platform") or forecast.get("platform") or "通用平台")
    for key, profile in PLATFORM_STYLE_PROFILES.items():
        if key in platform:
            return {"platform": key, **profile}
    return {"platform": platform, "styleName": "通用货架表达", "titleStyle": "场景词、卖点词、功能词组合", "imageStyle": "清晰商品图、核心卖点、使用场景", "decisionBias": ["搜索承接", "主图点击", "转化可信"]}


def infer_vertical_category(package: Dict[str, Any], forecast: Dict[str, Any]) -> Dict[str, Any]:
    product = _product_identity(package, forecast)
    current = str(product.get("verticalCategory") or package.get("verticalCategory") or "").strip()
    title = _text(product.get("productTitle"), product.get("shortTitle"), package.get("productTitle"), package.get("title"))
    if current and current not in {"未归类", "未分类", "unknown", "None"}:
        return {"verticalCategory": current, "inferredVerticalCategory": current, "categoryConfidence": 0.95, "categoryReason": "报表或商品档案已有垂直类目。"}
    for keyword, category in CATEGORY_HINTS:
        if keyword in title:
            return {"verticalCategory": category, "inferredVerticalCategory": category, "categoryConfidence": 0.76, "categoryReason": f"根据商品标题中的“{keyword}”临时推断，用于平台/类目路线判断和标题主图生成。"}
    return {"verticalCategory": "待Agent按商品标题和平台风格临时归类", "inferredVerticalCategory": "待Agent推断", "categoryConfidence": 0.35, "categoryReason": "商品档案缺少垂直类目，V19.3要求Agent先推断类目画像再选择经营路线。"}


def vertical_category_profile(package: Dict[str, Any], forecast: Dict[str, Any]) -> Dict[str, Any]:
    category = infer_vertical_category(package, forecast)
    name = category.get("inferredVerticalCategory") or category.get("verticalCategory") or "通用商品"
    profile = CATEGORY_PROFILES.get(str(name)) or CATEGORY_PROFILES.get(str(category.get("verticalCategory")))
    if profile:
        return {**category, **profile}
    return {**category, "categoryName": name, "trafficWords": [], "painPoints": [], "buyingTriggers": [], "imagePatterns": ["商品清晰图", "场景图", "卖点图"], "titlePatterns": ["平台场景词 + 核心卖点 + 商品词"], "seasonalWindows": []}


def _metric_changes(package: Dict[str, Any], route: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = package.get("correlatedMetricChanges") or package.get("allMetricChanges") or route.get("correlatedMetricChanges") or route.get("allMetricChanges") or []
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _metric_text(package: Dict[str, Any], forecast: Dict[str, Any], route: Dict[str, Any]) -> str:
    changes = _metric_changes(package, route)
    return _text(package.get("triggerMetric"), package.get("primaryRisk"), package.get("candidateReason"), forecast.get("forecastSummary"), route.get("routeName"), route.get("routeFamily"), *[ _text(x.get("metricCode"), x.get("metricName"), x.get("summary"), x.get("changeRatio")) for x in changes[:8] ])


def build_metric_route_candidates(package: Dict[str, Any], forecast: Dict[str, Any], route: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = _metric_text(package, forecast, route)
    candidates: List[Dict[str, Any]] = []
    def add(route_id: str, name: str, evidence: List[str], risk: str) -> None:
        if route_id not in {x.get("routeId") for x in candidates}:
            candidates.append({"routeId": route_id, "routeName": name, "evidence": evidence, "risk": risk})
    if any(w in raw for w in ["GMV", "支付", "sales", "增长", "+"]):
        add("growth_window", "增长窗口但需验证承接", ["销售或支付指标出现上升"], "可能把广告硬拉误判为自然爆发")
    if any(w in raw for w in ["点击", "CTR", "click"]):
        add("click_efficiency_route", "点击效率变化路线", ["点击率或点击相关指标波动"], "可能由标题、主图、平台流量或人群泛化共同造成")
    if any(w in raw for w in ["广告", "消耗", "ROAS", "ROI", "ad"]):
        add("ad_spend_dilution", "广告放量/缩量影响路线", ["广告消耗或投产指标波动"], "需要判断增长是否被投放硬拉或缩量拖累")
    if any(w in raw for w in ["库存", "可售", "断货", "stock", "availableDays"]):
        add("inventory_capacity_hint", "库存承接压力路线", ["库存或可售天数波动"], "库存是公司承接问题，不应直接生成运营补货任务")
    add("title_keyword_gap", "标题场景词承接不足", ["主指标波动需要验证搜索词/场景词承接"], "可能只修标题而忽略主图和转化")
    add("main_image_expression_gap", "主图表达/点击缺口", ["点击效率或新增流量承接需要主图验证"], "可能把标题流量缺口误判为主图问题")
    add("category_platform_window", "平台/类目窗口路线", ["需要结合平台偏好和垂直类目窗口判断"], "可能把类目窗口误判成单品爆款")
    return candidates[:7]


def build_creative_context_pack(package: Dict[str, Any], forecast: Dict[str, Any], route: Dict[str, Any], data_version: str | None = None) -> Dict[str, Any]:
    platform = platform_style_profile(package, forecast)
    category = vertical_category_profile(package, forecast)
    return {"version": V19_ROUTER_VERSION, "dataVersion": data_version or package.get("dataVersion"), "platformStyleProfile": platform, "verticalCategoryProfile": category, "metricRouteCandidates": build_metric_route_candidates(package, forecast, route), "productIdentity": _product_identity(package, forecast), "storeContext": forecast.get("storeContext") or package.get("storeContext") or {}, "metricChanges": _metric_changes(package, route), "ragStrategyBoundary": package.get("ragPermissionContext") or {}, "rule": "V19.3: system recalls candidate routes and context; Agent selects route and creates operator view."}


def build_business_event(package: Dict[str, Any], forecast: Dict[str, Any], route: Dict[str, Any], data_version: str | None = None) -> Dict[str, Any]:
    product_title = _product_title(package, forecast)
    context_pack = build_creative_context_pack(package, forecast, route, data_version)
    raw = _metric_text(package, forecast, route)
    inventory_signal = any(word in raw for word in ["inventory", "stock", "availableDays", "库存", "补货", "可售", "断货", "缺货"])
    future_hooks = [SUPPLY_CHAIN, MANAGER_COORDINATION] if inventory_signal else []
    return {"version": V19_ROUTER_VERSION, "businessEventId": _event_id(data_version, package), "businessEventType": "operator_multi_route_judgment_event", "businessEventName": f"{product_title}多路线经营判断", "eventJudgment": "系统只召回多条经营路线，不替Agent定路线；Agent必须结合平台风格、垂直类目和指标联动选择最终方向。", "roleTaskBlueprint": [OPERATOR_GROWTH], "futureDepartmentHooks": future_hooks, "creativeContextPack": context_pack, "categoryContextForCreative": context_pack.get("verticalCategoryProfile"), "platformStyleProfile": context_pack.get("platformStyleProfile"), "metricRouteCandidates": context_pack.get("metricRouteCandidates") or [], "companyCapacityReminder": "运营负责发现和验证流量缺口，公司负责组织承接；承接失败不归因给运营。" if inventory_signal else "", "rule": "V19.3 backend keeps multi-route judgment trace; frontend shows only operatorJudgmentView."}


def responsibility_from_agent(item: Dict[str, Any], package: Dict[str, Any], forecast: Dict[str, Any], route: Dict[str, Any]) -> str:
    if str(item.get("decision") or "") == "system_watch":
        return SYSTEM_WATCH
    return OPERATOR_GROWTH


def _ensure_list_field(plan: Dict[str, Any], key: str, item: Dict[str, Any]) -> None:
    if not isinstance(plan.get(key), list) or not plan.get(key):
        value = item.get(key)
        if isinstance(value, list):
            plan[key] = value


def _default_operator_view(plan: Dict[str, Any], event: Dict[str, Any]) -> Dict[str, Any]:
    trace = plan.get("agentJudgmentTrace") if isinstance(plan.get("agentJudgmentTrace"), dict) else {}
    selected = trace.get("selectedRoute") if isinstance(trace.get("selectedRoute"), dict) else {}
    context = event.get("creativeContextPack") or {}
    platform = (context.get("platformStyleProfile") or {}).get("platform", "平台")
    category = (context.get("verticalCategoryProfile") or {}).get("categoryName", plan.get("verticalCategory") or "类目")
    direction = plan.get("selectedDirection") or selected.get("routeName") or f"{platform}{category}流量缺口测试"
    return {"selectedDirection": direction, "displayReason": plan.get("businessHypothesis") or trace.get("businessHypothesis") or "系统已完成多路线判断，当前方向最适合进入运营测试。", "testFocus": plan.get("testGoal") or plan.get("creativeStrategy") or "标题场景词 + 主图结构 + 转化承接", "recapBasis": "24小时看点击率、转化率、ROAS；3天后系统自动复盘路线是否成立。"}


def enrich_mapping_task(task_plan: Dict[str, Any], item: Dict[str, Any], package: Dict[str, Any], forecast: Dict[str, Any], route: Dict[str, Any], data_version: str | None = None) -> Dict[str, Any]:
    plan = dict(task_plan or {})
    event = build_business_event(package, forecast, route, data_version)
    context_pack = event.get("creativeContextPack") or {}
    category = context_pack.get("verticalCategoryProfile") or {}

    plan["subtitle"] = plan.get("subtitle") or "V19.3多路线经营判断SOP"
    plan["actionType"] = plan.get("actionType") or "multi_route_creative_growth_test"
    plan["approvalRequired"] = False if plan.get("approvalRequired") is None else bool(plan.get("approvalRequired"))
    plan["businessEvent"] = event
    plan["businessEventId"] = event["businessEventId"]
    plan["parentEventId"] = event["businessEventId"]
    plan["taskResponsibility"] = OPERATOR_GROWTH
    plan["departmentTaskType"] = OPERATOR_GROWTH
    plan["responsibilityOwner"] = "运营：按最终选定路线执行标题/主图/流量缺口测试"
    plan["creativeContextPack"] = context_pack
    plan["platformStyleProfile"] = context_pack.get("platformStyleProfile")
    plan["verticalCategoryProfile"] = category
    plan["verticalCategory"] = category.get("categoryName") or category.get("verticalCategory")
    plan["metricRouteCandidates"] = context_pack.get("metricRouteCandidates") or []
    plan["futureDepartmentHooks"] = event.get("futureDepartmentHooks") or []
    plan["crossDepartmentDependency"] = []
    plan["companyCapacityReminder"] = event.get("companyCapacityReminder")
    plan["sopCreativeMode"] = "agent_multi_route_dynamic_creative_output_required"

    for key in ["titleVariants", "mainImageStructures", "testVariables", "successCriteria", "failureCriteria", "submissionConclusionOptions", "executionTimeline"]:
        _ensure_list_field(plan, key, item)
    for key in ["businessHypothesis", "operatingScenario", "creativeStrategy", "testGoal", "trafficGapType"]:
        if not plan.get(key) and item.get(key):
            plan[key] = item.get(key)

    trace = item.get("agentJudgmentTrace") if isinstance(item.get("agentJudgmentTrace"), dict) else plan.get("agentJudgmentTrace") if isinstance(plan.get("agentJudgmentTrace"), dict) else {}
    if trace:
        plan["agentJudgmentTrace"] = trace
    view = item.get("operatorJudgmentView") if isinstance(item.get("operatorJudgmentView"), dict) else plan.get("operatorJudgmentView") if isinstance(plan.get("operatorJudgmentView"), dict) else {}
    plan["operatorJudgmentView"] = view or _default_operator_view(plan, event)
    plan["v19ResponsibilityRouter"] = {"version": V19_ROUTER_VERSION, "rule": "System recalls routes; Agent selects the final route; frontend renders only operatorJudgmentView.", "businessEventId": event["businessEventId"], "taskResponsibility": OPERATOR_GROWTH}
    plan["forbiddenActions"] = list(dict.fromkeys(_as_list(plan.get("forbiddenActions")) + ["不得把补货作为运营完成项", "不得向前端展示候选路线和排除路线明细", "不得只复述运营思维图谱路线", "不得用固定模板覆盖Agent创作输出"] ))
    return plan


def describe_business_event_for_package(package: Dict[str, Any], forecast: Dict[str, Any], route: Dict[str, Any], data_version: str | None = None) -> Dict[str, Any]:
    return build_business_event(package, forecast, route, data_version)
