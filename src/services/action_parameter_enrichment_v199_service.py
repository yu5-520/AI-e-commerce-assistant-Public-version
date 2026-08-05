"""V19.14 action family parameter enrichment station.

The action-family station is append-only, but it now has read authority over the
system product fact layer. It may pull product detail facts by dataVersion +
productId + storeId, compute action-family parameter packs, and pass those packs
to Agent2. It must not overwrite Agent1 text, Agent1 route, or Agent2 creative
text.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List

import src.services.dual_agent_product_task_service as base
from src.repositories.sqlite_repository import connect, loads
from src.services.operating_judgment_brief_v195_service import apply_operating_judgment_brief
from src.services.system_product_snapshot_service import get_product_snapshot

ACTION_PARAMETER_ENRICHMENT_VERSION = "19.14"
HIGH_RISK_ACTIONS = {"roas_scale", "roas_guard", "platform_activity"}
TEMPLATE_MARKERS = [
    "核心场景词",
    "核心卖点",
    "使用场景等占位词",
    "设计2-3组新标题和主图变体",
    "在广告平台创建A/B测试",
    "监控测试数据",
    "评估测试结果并应用最优素材",
    "商品主体+核心场景+关键卖点",
    "围绕核心场景词重写标题",
    "突出主卖点与场景",
]

CODE_ALIASES = {
    "adSpend": {"adSpend", "ad_spend", "广告消耗", "广告花费", "投放消耗"},
    "paymentAmount": {"paymentAmount", "payment_amount", "支付金额", "成交金额", "销售额"},
    "gmv": {"gmv", "GMV"},
    "roi": {"roi", "ROI", "roas", "ROAS", "投产", "投产比"},
    "grossMarginRate": {"grossMarginRate", "gross_margin_rate", "grossMargin", "毛利率"},
    "grossProfitAmount": {"grossProfitAmount", "gross_profit", "grossProfit", "毛利金额", "单件毛利", "利润"},
    "price": {"price", "salePrice", "sellingPrice", "unitPrice", "客单价", "售价", "商品售价"},
    "cost": {"cost", "productCost", "商品成本", "成本", "成本价", "商品成本金额"},
    "inventory": {"inventory", "stock", "库存", "库存数量"},
    "availableDays": {"availableDays", "available_days", "sellableDays", "可售天数"},
    "paidVisitors": {"paidVisitors", "paid_visitors", "付费访客", "付费流量访客数"},
    "organicVisitors": {"organicVisitors", "organic_visitors", "自然访客", "自然流量访客数"},
    "conversionRate": {"conversionRate", "conversion_rate", "支付转化率", "转化率"},
    "clickRate": {"clickRate", "click_rate", "点击率", "CTR"},
    "refundRate": {"refundRate", "refund_rate", "退款率"},
}


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _table(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _num(value: Any) -> float | None:
    if value in {None, "", "—", "未识别", "UNKNOWN"}:
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").replace("￥", "").replace("¥", "").strip())
    except Exception:
        return None


def _ratio(value: Any) -> float | None:
    number = _num(value)
    if number is None:
        return None
    return number / 100 if abs(number) > 2 else number


def _walk(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _metric_key(item: Dict[str, Any]) -> str:
    return str(item.get("metricCode") or item.get("code") or item.get("metricName") or item.get("label") or item.get("name") or "").strip()


def _match(item: Dict[str, Any], canonical: str) -> bool:
    aliases = CODE_ALIASES.get(canonical, {canonical})
    key = _metric_key(item)
    if key in aliases:
        return True
    text = " ".join(str(item.get(k) or "") for k in ["metricName", "label", "name", "title", "key"])
    return any(alias and alias in text for alias in aliases)


def _current(item: Dict[str, Any]) -> Any:
    for key in ["currentValue", "current", "latest", "value", "amount"]:
        if key in item:
            return item.get(key)
    return None


def _previous(item: Dict[str, Any]) -> Any:
    for key in ["previousValue", "previous", "before", "oldValue"]:
        if key in item:
            return item.get(key)
    return None


def _change_ratio(item: Dict[str, Any]) -> float | None:
    for key in ["changeRatio", "changeRate", "deltaRate", "changeVsPrevious", "changeVsAvg"]:
        if key in item:
            value = _ratio(item.get(key))
            if value is not None:
                return value
    prev = _num(_previous(item))
    cur = _num(_current(item))
    if prev not in {None, 0} and cur is not None:
        return (cur - prev) / prev
    return None


def _metric_item(package: Dict[str, Any], canonical: str) -> Dict[str, Any] | None:
    for item in _walk(package):
        if _match(item, canonical):
            return item
    return None


def _direct_number(package: Dict[str, Any], canonical: str) -> float | None:
    aliases = CODE_ALIASES.get(canonical, {canonical})
    for item in _walk(package):
        for key, value in item.items():
            if str(key) in aliases:
                number = _num(value)
                if number is not None:
                    return number
    return None


def _metric(package: Dict[str, Any], canonical: str) -> Dict[str, Any]:
    item = _metric_item(package, canonical) or {}
    current = _num(_current(item))
    previous = _num(_previous(item))
    # V19.14: product detail snapshots store metrics as direct keys, for example
    # metricSnapshot.sellableDays or metricSnapshot.grossMargin. Those must count
    # as current facts even if they are not metricCode rows.
    if current is None:
        current = _direct_number(package, canonical)
    return {"current": current, "previous": previous, "deltaRate": _change_ratio(item), "raw": item}


def _value(package: Dict[str, Any], canonical: str) -> float | None:
    metric = _metric(package, canonical)
    return metric.get("current") if metric.get("current") is not None else _direct_number(package, canonical)


def _pct(value: float | None) -> str:
    return "待补齐" if value is None else f"{value * 100:.1f}%"


def _money(value: float | None) -> str:
    return "待补齐" if value is None else f"{value:.2f}"


def _product_name(package: Dict[str, Any]) -> str:
    product = package.get("productIdentity") if isinstance(package.get("productIdentity"), dict) else {}
    evidence = package.get("fullProductBundleEvidence") if isinstance(package.get("fullProductBundleEvidence"), dict) else {}
    detail = package.get("systemProductFactPack") if isinstance(package.get("systemProductFactPack"), dict) else {}
    return str(product.get("shortTitle") or product.get("productTitle") or product.get("title") or evidence.get("title") or detail.get("title") or package.get("productId") or "该商品")


def _agent1_locked_family(package: Dict[str, Any]) -> str | None:
    agent1 = package.get("agent1OperatingJudgment") if isinstance(package.get("agent1OperatingJudgment"), dict) else {}
    lock = agent1.get("actionFamilyLock") if isinstance(agent1.get("actionFamilyLock"), dict) else {}
    family = lock.get("selectedActionFamily") or agent1.get("selectedActionFamily") or package.get("selectedActionFamilyHint")
    return str(family).strip() if family else None


def _available_families(package: Dict[str, Any]) -> List[str]:
    locked = _agent1_locked_family(package)
    if locked:
        return [locked]
    brief = package.get("operatingJudgmentBrief") if isinstance(package.get("operatingJudgmentBrief"), dict) else {}
    values = package.get("allowedActionFamilies") or brief.get("allowedActionFamilies") or []
    return [str(x) for x in values if str(x).strip()] if isinstance(values, list) else []


def _product_id(package: Dict[str, Any]) -> str | None:
    product = package.get("productIdentity") if isinstance(package.get("productIdentity"), dict) else {}
    evidence = package.get("fullProductBundleEvidence") if isinstance(package.get("fullProductBundleEvidence"), dict) else {}
    return str(package.get("productId") or product.get("productId") or evidence.get("productId") or "").strip() or None


def _store_id(package: Dict[str, Any]) -> str | None:
    product = package.get("productIdentity") if isinstance(package.get("productIdentity"), dict) else {}
    evidence = package.get("fullProductBundleEvidence") if isinstance(package.get("fullProductBundleEvidence"), dict) else {}
    return str(package.get("storeId") or product.get("storeId") or evidence.get("storeId") or "").strip() or None


def _find_system_product_fact(data_version: str | None, product_id: str | None, store_id: str | None) -> Dict[str, Any]:
    if not product_id:
        return {}
    snapshot = get_product_snapshot(data_version)
    products = snapshot.get("products") if isinstance(snapshot, dict) and isinstance(snapshot.get("products"), list) else []
    best: Dict[str, Any] = {}
    for item in products:
        if not isinstance(item, dict):
            continue
        if str(item.get("productId") or "") != str(product_id):
            continue
        if store_id and str(item.get("storeId") or "") != str(store_id):
            best = best or item
            continue
        return item
    return best


def _merge_missing(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(target or {})
    for key, value in (source or {}).items():
        if value in {None, "", "—", "未识别"}:
            continue
        if key not in out or out.get(key) in {None, "", "—", "未识别"}:
            out[key] = value
    return out


def _hydrate_with_system_product_facts(package: Dict[str, Any]) -> Dict[str, Any]:
    item = dict(package or {})
    data_version = item.get("dataVersion")
    fact = _find_system_product_fact(data_version, _product_id(item), _store_id(item))
    if not fact:
        item["actionParameterFactHydration"] = {"status": "not_found", "productId": _product_id(item), "storeId": _store_id(item), "rule": "V19.14 attempted to hydrate action pack from system product facts."}
        return item

    profile = fact.get("profileSnapshot") if isinstance(fact.get("profileSnapshot"), dict) else {}
    metric = fact.get("metricSnapshot") if isinstance(fact.get("metricSnapshot"), dict) else {}
    product_identity = item.get("productIdentity") if isinstance(item.get("productIdentity"), dict) else {}
    product_identity = _merge_missing(product_identity, {
        "productId": fact.get("productId"),
        "storeId": fact.get("storeId"),
        "storeName": fact.get("storeName") or profile.get("storeName"),
        "platform": fact.get("platform") or profile.get("platform"),
        "title": fact.get("title") or profile.get("title"),
        "productTitle": fact.get("title") or profile.get("title"),
        "shortTitle": fact.get("shortName") or fact.get("title") or profile.get("title"),
        "verticalCategory": fact.get("verticalCategory") or profile.get("verticalCategory"),
        "priceBand": fact.get("priceBand") or profile.get("priceBand"),
        "productRole": fact.get("productRole") or profile.get("productRole"),
        "lifecycleStage": fact.get("lifecycleStage") or profile.get("lifecycleStage"),
    })
    metric_layer = item.get("metricLayer") if isinstance(item.get("metricLayer"), dict) else {}
    metric_layer = _merge_missing(metric_layer, metric)
    # Normalize aliases into the current package so later recursive readers can find them.
    if metric_layer.get("availableDays") in {None, "", "—", "未识别"} and metric_layer.get("sellableDays") not in {None, "", "—", "未识别"}:
        metric_layer["availableDays"] = metric_layer.get("sellableDays")
    if metric_layer.get("grossMarginRate") in {None, "", "—", "未识别"} and metric_layer.get("grossMargin") not in {None, "", "—", "未识别"}:
        metric_layer["grossMarginRate"] = metric_layer.get("grossMargin")
    if metric_layer.get("price") in {None, "", "—", "未识别"} and metric_layer.get("unitPrice") not in {None, "", "—", "未识别"}:
        metric_layer["price"] = metric_layer.get("unitPrice")

    evidence = item.get("fullProductBundleEvidence") if isinstance(item.get("fullProductBundleEvidence"), dict) else {}
    evidence = _merge_missing(evidence, {
        "title": product_identity.get("title") or product_identity.get("productTitle"),
        "platform": product_identity.get("platform"),
        "verticalCategory": product_identity.get("verticalCategory"),
        "metricDate": metric_layer.get("metricDate") or fact.get("metricDate"),
        "factHydrationSource": "system_product_snapshots_v14",
    })

    item["productIdentity"] = product_identity
    item["metricLayer"] = metric_layer
    item["productMetricSnapshot"] = _merge_missing(item.get("productMetricSnapshot") if isinstance(item.get("productMetricSnapshot"), dict) else {}, metric_layer)
    item["fullProductBundleEvidence"] = evidence
    item["systemProductFactPack"] = {
        "version": ACTION_PARAMETER_ENRICHMENT_VERSION,
        "source": "system_product_snapshots_v14",
        "dataVersion": data_version,
        "productId": fact.get("productId"),
        "storeId": fact.get("storeId"),
        "title": product_identity.get("title") or product_identity.get("productTitle"),
        "profileSnapshot": profile,
        "metricSnapshot": metric_layer,
        "productMetricFacts": fact.get("productMetricFacts") or metric.get("productMetricFacts") or [],
        "trafficSourceFacts": fact.get("trafficSourceFacts") or metric.get("trafficSourceFacts") or [],
        "metricFactSummary": fact.get("metricFactSummary") or metric.get("metricFactSummary") or {},
        "rule": "V19.14 action-family station may read product detail facts, but only appends parameter packs.",
    }
    item["actionParameterFactHydration"] = {"status": "hydrated", "source": "system_product_snapshots_v14", "productId": fact.get("productId"), "storeId": fact.get("storeId"), "trafficSourceFactCount": len(item["systemProductFactPack"].get("trafficSourceFacts") or []), "productMetricFactCount": len(item["systemProductFactPack"].get("productMetricFacts") or [])}
    return item


def _gross_margin(package: Dict[str, Any]) -> tuple[float | None, float | None, float | None, float | None]:
    price = _value(package, "price")
    cost = _value(package, "cost")
    margin_rate = _value(package, "grossMarginRate")
    gross_profit = _value(package, "grossProfitAmount")
    if margin_rate is not None and margin_rate > 1:
        margin_rate = margin_rate / 100
    if gross_profit is None and price is not None and cost is not None:
        gross_profit = max(0.0, price - cost)
    if margin_rate is None and price not in {None, 0} and gross_profit is not None:
        margin_rate = gross_profit / price
    return price, cost, margin_rate, gross_profit


def _creative_plan(package: Dict[str, Any]) -> Dict[str, Any]:
    for value in [package.get("creativeTestPlan"), package.get("agentCreativePack")]:
        if isinstance(value, dict) and isinstance(value.get("groups"), list) and value.get("groups"):
            return value
    return {}


def _creative_groups(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    groups = _creative_plan(package).get("groups") if isinstance(_creative_plan(package).get("groups"), list) else []
    clean = []
    for group in groups[:5]:
        if not isinstance(group, dict):
            continue
        text = json.dumps(group, ensure_ascii=False)
        if any(marker in text for marker in TEMPLATE_MARKERS):
            continue
        if group.get("fullTitle") and isinstance(group.get("mainImageStructure"), dict):
            clean.append(group)
    return clean


def _traffic_source_summary(package: Dict[str, Any]) -> List[Dict[str, Any]]:
    fact_pack = package.get("systemProductFactPack") if isinstance(package.get("systemProductFactPack"), dict) else {}
    rows = fact_pack.get("trafficSourceFacts") if isinstance(fact_pack.get("trafficSourceFacts"), list) else []
    result = []
    for row in rows[:10]:
        if not isinstance(row, dict):
            continue
        result.append({
            "sourceName": row.get("sourceName") or row.get("trafficSource") or row.get("渠道") or row.get("name"),
            "visitors": row.get("visitors") or row.get("visitorCount") or row.get("访客"),
            "clickRate": row.get("clickRate") or row.get("点击率"),
            "conversionRate": row.get("conversionRate") or row.get("转化率"),
            "roi": row.get("roi") or row.get("ROI"),
        })
    return [item for item in result if any(value not in {None, "", "—", "未识别"} for value in item.values())]


def build_roas_parameter_pack(package: Dict[str, Any], family: str = "roas_scale") -> Dict[str, Any]:
    ad = _metric(package, "adSpend")
    pay = _metric(package, "paymentAmount")
    roi = _metric(package, "roi")
    paid = _metric(package, "paidVisitors")
    organic = _metric(package, "organicVisitors")
    conversion = _metric(package, "conversionRate")
    inventory = _metric(package, "inventory")
    days = _metric(package, "availableDays")
    price, cost, margin_rate, gross_profit = _gross_margin(package)
    current_roi = roi.get("current")
    break_even_roi = (1 / margin_rate) if margin_rate and margin_rate > 0 else None
    safety_roi = break_even_roi * 1.15 if break_even_roi else None
    roi_headroom = current_roi - safety_roi if current_roi is not None and safety_roi is not None else None
    current_ad = ad.get("current")
    available_days = days.get("current")
    base_rate = 0.06
    if roi_headroom is not None and roi_headroom > 1:
        base_rate += 0.04
    if margin_rate is not None and margin_rate >= 0.5:
        base_rate += 0.02
    if available_days is not None and available_days < 7:
        base_rate = min(base_rate, 0.03)
    elif available_days is not None and available_days < 14:
        base_rate = min(base_rate, 0.08)
    recommended = max(0.03, min(0.15, base_rate))
    upper = current_ad * (1 + recommended) if current_ad is not None else None
    missing = [name for name, value in {"adSpend": current_ad, "roi": current_roi, "grossMarginRate": margin_rate, "inventory": inventory.get("current"), "availableDays": available_days}.items() if value is None]
    core_missing = [name for name in ["adSpend", "roi", "grossMarginRate"] if name in missing]
    status = "valid" if not core_missing and len(missing) <= 1 else "insufficient"
    return {
        "version": ACTION_PARAMETER_ENRICHMENT_VERSION,
        "actionFamily": family,
        "status": status,
        "missingMetrics": missing,
        "currentAdSpend": current_ad,
        "previousAdSpend": ad.get("previous"),
        "adSpendDeltaRate": ad.get("deltaRate"),
        "currentPaymentAmount": pay.get("current"),
        "paymentDeltaRate": pay.get("deltaRate"),
        "currentROI": current_roi,
        "previousROI": roi.get("previous"),
        "grossMarginRate": margin_rate,
        "grossProfitAmount": gross_profit,
        "currentPrice": price,
        "productCost": cost,
        "breakEvenROI": break_even_roi,
        "safetyROI": safety_roi,
        "roiHeadroom": roi_headroom,
        "inventory": inventory.get("current"),
        "availableDays": available_days,
        "paidVisitorDeltaRate": paid.get("deltaRate"),
        "organicVisitorDeltaRate": organic.get("deltaRate"),
        "conversionRateDelta": conversion.get("deltaRate"),
        "recommendedBudgetIncreaseRate": recommended if current_ad is not None and status == "valid" else None,
        "recommendedBudgetUpperBound": upper if status == "valid" else None,
        "stopLossROI": safety_roi,
        "stopLossCondition": "若ROI低于安全线、支付金额未同步增长，或可售天数低于7天，停止继续放量。",
        "trafficSourceSummary": _traffic_source_summary(package),
        "reviewMetrics": ["广告消耗", "ROI/ROAS", "支付金额", "付费访客", "自然访客", "可售天数"],
        "rule": "V19.14 ROAS动作族必须从系统商品事实层读取广告消耗、ROI、毛利率、库存和可售天数后再计算放量参数。",
    }


def build_activity_parameter_pack(package: Dict[str, Any]) -> Dict[str, Any]:
    pay = _metric(package, "paymentAmount")
    organic = _metric(package, "organicVisitors")
    conversion = _metric(package, "conversionRate")
    inventory = _metric(package, "inventory")
    days = _metric(package, "availableDays")
    refund = _metric(package, "refundRate")
    price, cost, margin_rate, gross_profit = _gross_margin(package)
    max_discount = max(0.0, gross_profit * 0.25) if gross_profit is not None else None
    coupon = None
    if max_discount is not None:
        coupon = round(max(1.0, min(max_discount * 0.5, (price or max_discount) * 0.08, 5.0)))
    available_days = days.get("current")
    activity_days = 7 if available_days is None or available_days >= 7 else 3
    organic_delta = organic.get("deltaRate")
    target = "新访客、自然访客回流和加购未成交人群" if organic_delta is None or organic_delta <= 0.12 else "新访客和未成交加购人群，避免全量老客长期让利"
    missing = [name for name, value in {"price": price, "cost": cost, "grossProfitAmount": gross_profit, "inventory": inventory.get("current"), "availableDays": available_days}.items() if value is None]
    core_missing = [name for name in ["grossProfitAmount", "inventory", "availableDays"] if name in missing]
    return {
        "version": ACTION_PARAMETER_ENRICHMENT_VERSION,
        "actionFamily": "platform_activity",
        "status": "valid" if not core_missing and len(missing) <= 2 else "insufficient",
        "missingMetrics": missing,
        "currentPrice": price,
        "productCost": cost,
        "grossProfitAmount": gross_profit,
        "grossMarginRate": margin_rate,
        "maxDiscountAmount": max_discount,
        "recommendedCouponAmount": coupon,
        "recommendedActivityDays": activity_days,
        "targetAudience": target,
        "inventory": inventory.get("current"),
        "availableDays": available_days,
        "paymentDeltaRate": pay.get("deltaRate"),
        "organicVisitorDeltaRate": organic_delta,
        "conversionRateDelta": conversion.get("deltaRate"),
        "refundRate": refund.get("current"),
        "trafficSourceSummary": _traffic_source_summary(package),
        "activityGoal": "用低额权益验证自然流量或平台活动入口能否带动支付金额回升，同时保护券后毛利。",
        "activityType": "新人券/限量券/加购未成交券" if coupon is not None else "数据补全后选择活动类型",
        "stopLossCondition": "若券后毛利被明显压缩、支付金额未改善、退款率上升或库存承接不足，停止活动。",
        "reviewMetrics": ["自然访客", "点击率", "转化率", "支付金额", "券后毛利", "退款率", "可售天数"],
        "rule": "V19.14 平台活动动作族必须从系统商品事实层读取售价、成本、毛利、库存和流量变化计算优惠参数。",
    }


def _creative_context(package: Dict[str, Any]) -> Dict[str, Any]:
    product = package.get("productIdentity") if isinstance(package.get("productIdentity"), dict) else {}
    fact_pack = package.get("systemProductFactPack") if isinstance(package.get("systemProductFactPack"), dict) else {}
    metric = fact_pack.get("metricSnapshot") if isinstance(fact_pack.get("metricSnapshot"), dict) else {}
    title = product.get("productTitle") or product.get("title") or fact_pack.get("title") or _product_name(package)
    category = product.get("verticalCategory") or "未归类"
    platform = product.get("platform") or "unknown"
    title_text = str(title or "")
    scene_seeds = [category]
    if any(token in title_text for token in ["儿童", "宝宝", "童", "婴"]):
        scene_seeds.extend(["儿童", "亲子", "家庭使用"])
    if any(token in title_text for token in ["夏", "凉", "防晒", "透气"]):
        scene_seeds.extend(["夏季", "透气", "户外"])
    if any(token in title_text for token in ["收纳", "置物", "厨房", "架"]):
        scene_seeds.extend(["厨房", "空间整理", "小户型"])
    if any(token in title_text for token in ["鞋", "拖鞋", "凉鞋"]):
        scene_seeds.extend(["通勤", "居家", "防滑"])
    selling_points = []
    for token in ["防滑", "透气", "轻便", "速干", "收纳", "大容量", "折叠", "柔软", "耐磨", "除湿", "静音"]:
        if token in title_text:
            selling_points.append(token)
    if not selling_points:
        selling_points = ["核心卖点需结合商品标题与类目提炼", "主图需突出使用场景和购买理由"]
    return {
        "productTitle": title,
        "platform": platform,
        "verticalCategory": category,
        "priceBand": product.get("priceBand"),
        "productRole": product.get("productRole"),
        "lifecycleStage": product.get("lifecycleStage"),
        "currentClickRate": metric.get("clickRate"),
        "currentConversionRate": metric.get("conversionRate"),
        "currentPaymentAmount": metric.get("paymentAmount"),
        "trafficSourceSummary": _traffic_source_summary(package),
        "sceneSeedWords": list(dict.fromkeys([str(x) for x in scene_seeds if x])),
        "sellingPointSeedWords": list(dict.fromkeys([str(x) for x in selling_points if x])),
        "agent2Instruction": "Agent2必须基于这些商品事实生成2-5组完整标题和主图结构，不得输出核心场景词/核心卖点等占位模板。",
    }


def build_title_image_parameter_pack(package: Dict[str, Any]) -> Dict[str, Any]:
    click = _metric(package, "clickRate")
    conversion = _metric(package, "conversionRate")
    organic = _metric(package, "organicVisitors")
    paid = _metric(package, "paidVisitors")
    groups = _creative_groups(package)
    context = _creative_context(package)
    return {
        "version": ACTION_PARAMETER_ENRICHMENT_VERSION,
        "actionFamily": "title_image_test",
        "status": "valid" if len(groups) >= 2 else "creative_plan_required",
        "missingMetrics": [] if len(groups) >= 2 else ["agent2.creativeTestPlan.groups"],
        "currentClickRate": click.get("current"),
        "clickRateDelta": click.get("deltaRate"),
        "conversionRateDelta": conversion.get("deltaRate"),
        "trafficEntrance": "自然流量" if (organic.get("deltaRate") or 0) >= (paid.get("deltaRate") or 0) else "付费流量",
        "creativePlanGroupCount": len(groups),
        "testGroupCount": max(2, min(5, len(groups) or 3)),
        "testDurationDays": 3,
        "creativeContext": context,
        "reviewMetrics": ["点击率", "点击量", "转化率", "支付金额"],
        "rule": "V19.14 标题主图动作族读取商品标题、平台、类目、流量和点击事实；完整标题与主图结构由Agent2生成。",
    }


def build_conversion_parameter_pack(package: Dict[str, Any]) -> Dict[str, Any]:
    conversion = _metric(package, "conversionRate")
    click = _metric(package, "clickRate")
    pay = _metric(package, "paymentAmount")
    refund = _metric(package, "refundRate")
    price, cost, margin_rate, gross_profit = _gross_margin(package)
    return {
        "version": ACTION_PARAMETER_ENRICHMENT_VERSION,
        "actionFamily": "conversion_repair",
        "status": "valid",
        "conversionRate": conversion.get("current"),
        "conversionRateDelta": conversion.get("deltaRate"),
        "clickRate": click.get("current"),
        "paymentAmountDelta": pay.get("deltaRate"),
        "price": price,
        "productCost": cost,
        "grossMarginRate": margin_rate,
        "grossProfitAmount": gross_profit,
        "refundRate": refund.get("current"),
        "repairFocus": "详情页承接、价格权益、评价信任和客服承诺",
        "reviewMetrics": ["转化率", "支付金额", "点击率", "退款率"],
    }


def build_parameter_packs(package: Dict[str, Any]) -> Dict[str, Any]:
    packs: Dict[str, Any] = {}
    for family in _available_families(package):
        if family == "roas_scale":
            packs[family] = build_roas_parameter_pack(package, family)
        elif family == "roas_guard":
            packs[family] = build_roas_parameter_pack(package, family)
        elif family == "platform_activity":
            packs[family] = build_activity_parameter_pack(package)
        elif family == "title_image_test":
            packs[family] = build_title_image_parameter_pack(package)
        elif family == "conversion_repair":
            packs[family] = build_conversion_parameter_pack(package)
    return packs


def select_action_parameter_pack(package: Dict[str, Any], family: str | None = None) -> Dict[str, Any]:
    packs = package.get("actionParameterPacks") if isinstance(package.get("actionParameterPacks"), dict) else {}
    locked = _agent1_locked_family(package)
    selected = family or locked or package.get("selectedActionFamilyHint")
    if selected and isinstance(packs.get(selected), dict):
        return packs[selected]
    for key in ["title_image_test", "roas_scale", "platform_activity", "conversion_repair", "roas_guard", "similar_product_test"]:
        if isinstance(packs.get(key), dict):
            return packs[key]
    return {}


def _selected_family_hint(packs: Dict[str, Any], package: Dict[str, Any]) -> str | None:
    locked = _agent1_locked_family(package)
    if locked:
        return locked
    allowed = _available_families(package)
    return allowed[0] if allowed else next(iter(packs.keys()), None)


def enrich_package_with_action_parameters(package: Dict[str, Any]) -> Dict[str, Any]:
    item = apply_operating_judgment_brief(package)
    item = _hydrate_with_system_product_facts(item)
    packs = build_parameter_packs(item)
    item["actionParameterPacks"] = packs
    item["selectedActionFamilyHint"] = _selected_family_hint(packs, item)
    item["actionParameterPack"] = select_action_parameter_pack(item, item.get("selectedActionFamilyHint"))
    item["actionParameterEnrichmentVersion"] = ACTION_PARAMETER_ENRICHMENT_VERSION
    item["rule"] = "V19.14: action family reads system product facts and appends parameters; it cannot override Agent1 text, route or action-family lock."
    return item


def _load_packages(data_version: str | None) -> List[Dict[str, Any]]:
    if not data_version:
        return []
    base.ensure_dual_agent_tables()
    with connect() as conn:
        if not _table(conn, "product_judgment_packages_v15"):
            return []
        rows = conn.execute("SELECT payload FROM product_judgment_packages_v15 WHERE data_version = ? ORDER BY task_candidate_allowed DESC, created_at ASC", (data_version,)).fetchall()
    return [_load(row["payload"]) for row in rows]


def action_parameter_enrichment_station_v199(data_version: str | None, **_: Any) -> Dict[str, Any]:
    packages = _load_packages(data_version)
    enriched = [enrich_package_with_action_parameters(item) for item in packages]
    if enriched:
        base._save_packages(enriched)
    high_risk_missing = 0
    pack_count = 0
    hydrated_count = 0
    by_family: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for item in enriched:
        if (item.get("actionParameterFactHydration") or {}).get("status") == "hydrated":
            hydrated_count += 1
        packs = item.get("actionParameterPacks") if isinstance(item.get("actionParameterPacks"), dict) else {}
        pack_count += len(packs)
        for family, pack in packs.items():
            by_family[family] = by_family.get(family, 0) + 1
            status = str(pack.get("status") if isinstance(pack, dict) else "unknown")
            by_status[status] = by_status.get(status, 0) + 1
            if family in HIGH_RISK_ACTIONS and isinstance(pack, dict) and pack.get("status") != "valid":
                high_risk_missing += 1
    return {
        "version": ACTION_PARAMETER_ENRICHMENT_VERSION,
        "stationId": "action_parameter_enrichment_station",
        "dataVersion": data_version,
        "packageCount": len(enriched),
        "factHydratedPackageCount": hydrated_count,
        "actionParameterPackCount": pack_count,
        "highRiskInsufficientCount": high_risk_missing,
        "byActionFamily": by_family,
        "byPackStatus": by_status,
        "actionParameterRef": f"action_parameter_pack:{data_version or 'latest'}",
        "outputRef": f"action_parameter_pack:{data_version or 'latest'}",
        "rule": "V19.14: action_parameter_enrichment reads system_product_snapshots_v14 by productId/storeId/dataVersion and appends locked action-family parameter packs.",
    }


def compose_parameterized_sop(family: str, pack: Dict[str, Any], plan: Dict[str, Any] | None = None, package: Dict[str, Any] | None = None) -> List[str]:
    plan = plan or {}
    package = package or {}
    name = _product_name(package)
    if family in {"roas_scale", "roas_guard"}:
        if pack.get("status") != "valid":
            return [f"当前缺少{','.join(pack.get('missingMetrics') or [])}，不能生成投放放量/收缩任务。", "先补齐广告消耗、ROI、毛利率、库存与可售天数，再由系统重新计算投放安全线。"]
        rate = pack.get("recommendedBudgetIncreaseRate") or 0
        upper = pack.get("recommendedBudgetUpperBound")
        return [
            f"当前{name}广告消耗为{_money(pack.get('currentAdSpend'))}，ROI/ROAS为{_money(pack.get('currentROI'))}，毛利率为{_pct(pack.get('grossMarginRate'))}，先按利润安全线做小步放量。",
            f"本轮建议预算上调{rate * 100:.0f}%左右，预算上限控制在{_money(upper)}以内；不要同时改标题、主图、价格或优惠，避免变量混杂。",
            f"只选择支付金额与付费流量同步增长的计划执行，库存{_money(pack.get('inventory'))}、可售天数{_money(pack.get('availableDays'))}只作为承接约束。",
            pack.get("stopLossCondition") or "若ROI低于安全线或支付金额未同步增长，停止继续放量。",
            "系统将在执行后第1天和第3天自动比对广告消耗、ROI/ROAS、支付金额、付费访客、自然访客和可售天数。",
        ]
    if family == "platform_activity":
        if pack.get("status") != "valid":
            return [f"当前缺少{','.join(pack.get('missingMetrics') or [])}，不能生成平台活动优惠任务。", "先补齐售价、成本、毛利、库存与可售天数，再由系统计算优惠安全线。"]
        return [
            f"当前{name}单件毛利约{_money(pack.get('grossProfitAmount'))}，本次不做大额满减，优先测试低额权益承接自然流量。",
            f"建议设置{_money(pack.get('recommendedCouponAmount'))}元新人券/限量券，周期{int(pack.get('recommendedActivityDays') or 7)}天，面向{pack.get('targetAudience') or '新访客和加购未成交人群'}。",
            "活动期间不同时修改标题、主图和价格，避免判断不清是活动权益还是素材变化带来的结果。",
            f"库存{_money(pack.get('inventory'))}、可售天数{_money(pack.get('availableDays'))}只作为活动承接约束；若承接不足，改成限量领取，不做大流量活动。",
            pack.get("stopLossCondition") or "若券后毛利明显压缩或支付金额未改善，停止活动。",
            "系统将在第3天和第7天自动复盘自然访客、点击率、转化率、支付金额、券后毛利、退款率和可售天数。",
        ]
    if family == "title_image_test":
        groups = _creative_groups(package)
        if len(groups) < 2:
            return ["创意测试方案缺失：Agent2必须先根据商品事实生成creativeTestPlan，包含2-5组完整标题和主图结构。", "禁止使用标题/主图占位模板生成正式任务。"]
        lines = [f"按Agent2 creativeTestPlan 执行{len(groups)}组标题主图测试。"]
        for index, group in enumerate(groups, 1):
            structure = group.get("mainImageStructure") if isinstance(group.get("mainImageStructure"), dict) else {}
            name = group.get("groupName") or f"{chr(64 + index)}组"
            lines.append(f"{name}标题：{group.get('fullTitle')}")
            lines.append(f"{name}主图结构：{structure.get('scene') or ''}；{structure.get('foreground') or ''}；重点突出{structure.get('focus') or ''}；画面文案“{structure.get('copy') or ''}”；目标：{structure.get('visualGoal') or ''}。")
        lines.append("保持预算、入口、人群和时间窗口一致，只测试标题词与主图表达差异。")
        return lines
    return plan.get("operatorActionSteps") if isinstance(plan.get("operatorActionSteps"), list) else []
