"""V18.6 Operating Graph Route Coverage service.

The operating graph is not a single trigger metric. It is a route of strongly
linked metrics. A strong operation can be generated only after the current report
has a comparable previous report and the route has real visible movement.

Rule:
- first report / no comparable previous report is baseline, not a task signal;
- current == previous or changeRatio == 0 is never a dynamic metric change;
- signalStrength cannot override a zero-change metric;
- if around 80% of available strong-linked route metrics show meaningful change,
  the route is strong enough for operation_action;
- weak cause evidence should change task action level, not erase the route.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

OPERATING_GRAPH_ROUTE_VERSION = "18.6"
ROUTE_STRONG_THRESHOLD = 0.80
ROUTE_MEDIUM_THRESHOLD = 0.50
MIN_ROUTE_AVAILABLE_FOR_STRONG = 3
ZERO_CHANGE_EPSILON = 1e-9

METRIC_LABELS = {
    "refundRate": "退款率",
    "afterSalesRate": "售后率",
    "refundOrderCount": "退款订单数",
    "refundAmount": "退款金额",
    "paymentAmount": "支付金额",
    "gmv": "GMV",
    "roi": "ROI",
    "roas": "ROAS",
    "adSpend": "广告消耗",
    "clickRate": "点击率",
    "conversionRate": "转化率",
    "visitorCount": "访客数",
    "paidVisitors": "付费访客",
    "organicVisitors": "自然访客",
    "inventory": "库存",
    "stock": "库存",
    "availableDays": "可售天数",
    "grossMargin": "毛利率",
}

METRIC_ALIASES = {
    "refund_rate": "refundRate",
    "refundRate": "refundRate",
    "退款率": "refundRate",
    "afterSalesRate": "afterSalesRate",
    "售后率": "afterSalesRate",
    "refundOrderCount": "refundOrderCount",
    "退款订单数": "refundOrderCount",
    "refundAmount": "refundAmount",
    "退款金额": "refundAmount",
    "paymentAmount": "paymentAmount",
    "payAmount": "paymentAmount",
    "支付金额": "paymentAmount",
    "成交金额": "paymentAmount",
    "gmv": "gmv",
    "GMV": "gmv",
    "roi": "roi",
    "ROI": "roi",
    "roas": "roas",
    "ROAS": "roas",
    "adSpend": "adSpend",
    "广告消耗": "adSpend",
    "clickRate": "clickRate",
    "点击率": "clickRate",
    "conversionRate": "conversionRate",
    "转化率": "conversionRate",
    "visitorCount": "visitorCount",
    "访客数": "visitorCount",
    "paidVisitors": "paidVisitors",
    "付费访客": "paidVisitors",
    "organicVisitors": "organicVisitors",
    "自然访客": "organicVisitors",
    "inventory": "inventory",
    "stock": "inventory",
    "库存": "inventory",
    "availableDays": "availableDays",
    "可售天数": "availableDays",
    "grossMargin": "grossMargin",
    "毛利率": "grossMargin",
}

ROUTES = {
    "refundRate": {
        "routeName": "售后风险路线",
        "routeFamily": "after_sales_risk",
        "primaryMetrics": ["refundRate"],
        "strongLinkedMetrics": ["refundOrderCount", "refundAmount", "afterSalesRate", "conversionRate", "paymentAmount", "gmv", "visitorCount", "clickRate"],
    },
    "afterSalesRate": {
        "routeName": "售后风险路线",
        "routeFamily": "after_sales_risk",
        "primaryMetrics": ["afterSalesRate"],
        "strongLinkedMetrics": ["refundRate", "refundOrderCount", "refundAmount", "conversionRate", "paymentAmount", "gmv", "visitorCount", "clickRate"],
    },
    "refundOrderCount": {
        "routeName": "售后风险路线",
        "routeFamily": "after_sales_risk",
        "primaryMetrics": ["refundOrderCount"],
        "strongLinkedMetrics": ["refundRate", "refundAmount", "afterSalesRate", "conversionRate", "paymentAmount", "gmv", "visitorCount"],
    },
    "inventory": {
        "routeName": "库存承接路线",
        "routeFamily": "inventory_capacity",
        "primaryMetrics": ["inventory"],
        "strongLinkedMetrics": ["availableDays", "paymentAmount", "gmv", "conversionRate", "visitorCount", "paidVisitors", "organicVisitors", "adSpend"],
    },
    "availableDays": {
        "routeName": "库存承接路线",
        "routeFamily": "inventory_capacity",
        "primaryMetrics": ["availableDays"],
        "strongLinkedMetrics": ["inventory", "paymentAmount", "gmv", "conversionRate", "visitorCount", "paidVisitors", "organicVisitors", "adSpend"],
    },
    "roi": {
        "routeName": "投产效率路线",
        "routeFamily": "roi_efficiency",
        "primaryMetrics": ["roi"],
        "strongLinkedMetrics": ["roas", "adSpend", "paymentAmount", "gmv", "conversionRate", "clickRate", "visitorCount"],
    },
    "roas": {
        "routeName": "投产效率路线",
        "routeFamily": "roi_efficiency",
        "primaryMetrics": ["roas"],
        "strongLinkedMetrics": ["roi", "adSpend", "paymentAmount", "gmv", "conversionRate", "clickRate", "visitorCount"],
    },
    "conversionRate": {
        "routeName": "转化承接路线",
        "routeFamily": "conversion_capacity",
        "primaryMetrics": ["conversionRate"],
        "strongLinkedMetrics": ["clickRate", "visitorCount", "paymentAmount", "gmv", "refundRate", "adSpend"],
    },
    "clickRate": {
        "routeName": "点击承接路线",
        "routeFamily": "traffic_capacity",
        "primaryMetrics": ["clickRate"],
        "strongLinkedMetrics": ["conversionRate", "visitorCount", "paymentAmount", "gmv", "adSpend"],
    },
    "paymentAmount": {
        "routeName": "成交增长路线",
        "routeFamily": "sales_growth",
        "primaryMetrics": ["paymentAmount"],
        "strongLinkedMetrics": ["gmv", "conversionRate", "visitorCount", "clickRate", "adSpend", "inventory", "availableDays", "refundRate"],
    },
    "gmv": {
        "routeName": "成交增长路线",
        "routeFamily": "sales_growth",
        "primaryMetrics": ["gmv"],
        "strongLinkedMetrics": ["paymentAmount", "conversionRate", "visitorCount", "clickRate", "adSpend", "inventory", "availableDays", "refundRate"],
    },
}

NEGATIVE_UP_METRICS = {"refundRate", "afterSalesRate", "refundAmount", "refundOrderCount", "adSpend"}
NEGATIVE_DOWN_METRICS = {"inventory", "stock", "availableDays", "roi", "roas", "conversionRate", "clickRate", "grossMargin"}


def canonical_metric(metric_code: Any) -> str:
    text = str(metric_code or "").strip()
    return METRIC_ALIASES.get(text, text)


def _to_float(value: Any) -> float | None:
    if value in {None, "", "—", "未识别"}:
        return None
    try:
        text = str(value).replace(",", "").replace("¥", "").replace("%", "").strip()
        return float(text)
    except Exception:
        return None


def _pick(payload: Dict[str, Any], keys: List[str]) -> Any:
    for key in keys:
        if key in payload and payload.get(key) not in {None, "", "—", "未识别"}:
            return payload.get(key)
    return None


def _value_parts(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return {
            "current": _pick(raw, ["current", "currentValue", "latest", "value"]),
            "previous": _pick(raw, ["previous", "previousValue", "baseline", "before"]),
            "delta": _pick(raw, ["delta", "change", "changeValue", "changeVsPrevious"]),
            "rate": _pick(raw, ["deltaRate", "changeRate", "rate", "changeRatio", "changeVsPreviousRate"]),
            "signalStrength": raw.get("signalStrength"),
            "signalType": raw.get("signalType"),
            "windows": raw.get("windows"),
        }
    return {"current": raw, "previous": None, "delta": None, "rate": None, "signalStrength": None, "signalType": None, "windows": None}


def _change_ratio(current: Any, previous: Any, explicit_rate: Any = None) -> float | None:
    if explicit_rate not in {None, "", "—", "未识别"}:
        raw = _to_float(explicit_rate)
        if raw is not None:
            return raw / 100 if abs(raw) > 1.5 else raw
    cur = _to_float(current)
    prev = _to_float(previous)
    if cur is None or prev is None:
        return None
    if prev == 0:
        if cur == 0:
            return 0.0
        return 1.0 if cur > 0 else -1.0
    return (cur - prev) / abs(prev)


def _change_text(ratio: float | None, current: Any, previous: Any) -> str | None:
    if ratio is None:
        cur = _to_float(current)
        prev = _to_float(previous)
        if cur is not None and prev is not None and prev == 0 and cur != 0:
            return "新增"
        return None
    return f"{ratio * 100:+.1f}%"


def _direction(metric_code: str, current: Any, previous: Any, ratio: float | None) -> str:
    if ratio is None or abs(ratio) < ZERO_CHANGE_EPSILON:
        return "flat"
    if metric_code in NEGATIVE_UP_METRICS:
        return "risk_up" if ratio > 0 else "risk_down"
    if metric_code in NEGATIVE_DOWN_METRICS:
        return "risk_up" if ratio < 0 else "risk_down"
    return "positive_up" if ratio > 0 else "positive_down"


def _meaningful(metric_code: str, ratio: float | None, current: Any, previous: Any, signal_strength: Any = None) -> bool:
    """Real movement is the hard prerequisite.

    signalStrength is useful only after a real current-vs-previous difference is
    proven. It cannot turn first-report values, missing previous values, or 0%
    change into a dynamic business signal.
    """
    cur = _to_float(current)
    prev = _to_float(previous)
    if cur is None or prev is None:
        return False
    if ratio is not None and abs(float(ratio)) < ZERO_CHANGE_EPSILON:
        return False
    if ratio is None:
        if cur == prev:
            return False
        return str(signal_strength or "").lower() in {"strong", "medium", "high", "明显", "强"}
    threshold = 0.05
    if metric_code in {"refundRate", "afterSalesRate", "conversionRate", "clickRate", "grossMargin", "roi", "roas"}:
        threshold = 0.03
    if metric_code in {"inventory", "availableDays"}:
        threshold = 0.10
    if abs(ratio) >= threshold:
        return True
    return str(signal_strength or "").lower() in {"strong", "medium", "high", "明显", "强"} and abs(ratio) > ZERO_CHANGE_EPSILON


def build_all_metric_changes(metric_evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(metric_evidence, dict):
        return []
    result: List[Dict[str, Any]] = []
    for raw_code, raw_value in metric_evidence.items():
        metric_code = canonical_metric(raw_code)
        parts = _value_parts(raw_value)
        current = parts.get("current")
        previous = parts.get("previous")
        ratio = _change_ratio(current, previous, parts.get("rate"))
        direction = _direction(metric_code, current, previous, ratio)
        meaningful = _meaningful(metric_code, ratio, current, previous, parts.get("signalStrength"))
        if not meaningful:
            continue
        label = METRIC_LABELS.get(metric_code, metric_code)
        change_text = _change_text(ratio, current, previous)
        summary = f"{label}：{previous} → {current}"
        if change_text:
            summary = f"{summary}，变化 {change_text}"
        result.append({
            "version": OPERATING_GRAPH_ROUTE_VERSION,
            "type": "dynamic_metric_change",
            "metricCode": metric_code,
            "metricName": label,
            "label": label,
            "previousValue": previous,
            "currentValue": current,
            "changeValue": parts.get("delta"),
            "changeRatio": ratio,
            "changeRate": change_text,
            "direction": direction,
            "meaningfulChange": True,
            "signalStrength": parts.get("signalStrength"),
            "signalType": parts.get("signalType"),
            "windows": parts.get("windows"),
            "summary": summary,
        })
    return result


def _route_for(trigger_metric: Any) -> Tuple[str, Dict[str, Any]]:
    canonical = canonical_metric(trigger_metric)
    if canonical in ROUTES:
        return canonical, ROUTES[canonical]
    return canonical, {
        "routeName": "经营变化路线",
        "routeFamily": "operating_change",
        "primaryMetrics": [canonical] if canonical else [],
        "strongLinkedMetrics": ["paymentAmount", "gmv", "conversionRate", "clickRate", "visitorCount", "adSpend", "refundRate", "inventory", "availableDays"],
    }


def build_operating_graph_route(trigger_metric: Any, metric_evidence: Dict[str, Any]) -> Dict[str, Any]:
    trigger, route = _route_for(trigger_metric)
    changes = build_all_metric_changes(metric_evidence)
    by_code = {item.get("metricCode"): item for item in changes if item.get("metricCode")}
    route_metrics = list(dict.fromkeys((route.get("primaryMetrics") or []) + (route.get("strongLinkedMetrics") or [])))
    required = [canonical_metric(item) for item in route_metrics if item]
    primary = [code for code in (route.get("primaryMetrics") or []) if canonical_metric(code) in by_code]
    strong_linked = [canonical_metric(item) for item in (route.get("strongLinkedMetrics") or [])]
    linked_available = [code for code in strong_linked if code in by_code]
    linked_changed = [code for code in linked_available if by_code.get(code, {}).get("meaningfulChange")]
    changed = [code for code in required if code in by_code and by_code.get(code, {}).get("meaningfulChange")]
    available = [code for code in required if code in by_code]
    if not primary:
        route_coverage = 0.0
        strength = "weak"
        recommended = "no_task"
    else:
        denominator = max(1, len(linked_available) or len(available))
        route_coverage = round(len(linked_changed or changed) / denominator, 4)
        if route_coverage >= ROUTE_STRONG_THRESHOLD and (len(linked_available) or len(available)) >= MIN_ROUTE_AVAILABLE_FOR_STRONG:
            strength = "strong"
            recommended = "operation_action"
        elif route_coverage >= ROUTE_MEDIUM_THRESHOLD:
            strength = "medium"
            recommended = "data_evidence_task"
        else:
            strength = "weak"
            recommended = "observation_task"
        if primary and strength == "medium" and len(linked_changed) >= max(2, int(len(linked_available) * 0.5)):
            recommended = "operation_action"
    correlated: List[Dict[str, Any]] = []
    primary_codes = [canonical_metric(x) for x in route.get("primaryMetrics") or []]
    for code in required:
        item = dict(by_code.get(code) or {})
        if not item:
            continue
        item["routeRole"] = "primary" if code in primary_codes else "strong_linked"
        item["routeName"] = route.get("routeName")
        item["routeFamily"] = route.get("routeFamily")
        correlated.append(item)
    missing = [code for code in required if code not in by_code]
    return {
        "version": OPERATING_GRAPH_ROUTE_VERSION,
        "triggerMetric": trigger,
        "routeName": route.get("routeName"),
        "routeFamily": route.get("routeFamily"),
        "primaryMetrics": primary_codes,
        "strongLinkedMetrics": strong_linked,
        "requiredMetricCount": len(required),
        "availableMetricCount": len(available),
        "changedMetricCount": len(changed),
        "primaryChangedMetricCount": len(primary),
        "linkedAvailableMetricCount": len(linked_available),
        "linkedChangedMetricCount": len(linked_changed),
        "routeCoverageRate": route_coverage,
        "routeSignalStrength": strength,
        "recommendedTaskType": recommended,
        "coverageThreshold": ROUTE_STRONG_THRESHOLD,
        "changedMetricCodes": changed,
        "linkedChangedMetricCodes": linked_changed,
        "missingMetricCodes": missing,
        "correlatedMetricChanges": correlated,
        "allMetricChanges": changes,
        "baselineSafe": True,
        "rule": "V18.6: first report and zero-change metrics cannot enter operating graph routes; strong operation requires a changed primary metric plus route co-movement.",
    }


def attach_route_to_package(package: Dict[str, Any], evidence: Dict[str, Any]) -> Dict[str, Any]:
    trigger = evidence.get("triggerMetric") or package.get("triggerMetric") or package.get("primaryRisk")
    metric_evidence = evidence.get("metricEvidence") if isinstance(evidence.get("metricEvidence"), dict) else {}
    route = build_operating_graph_route(trigger, metric_evidence)
    output = dict(evidence)
    output["operatingGraphRoute"] = route
    output["allMetricChanges"] = route.get("allMetricChanges") or []
    output["correlatedMetricChanges"] = route.get("correlatedMetricChanges") or []
    output["dynamicMetricChanges"] = route.get("correlatedMetricChanges") or route.get("allMetricChanges") or []
    output["routeCoverageRate"] = route.get("routeCoverageRate")
    output["routeSignalStrength"] = route.get("routeSignalStrength")
    output["recommendedTaskType"] = route.get("recommendedTaskType")
    return output
