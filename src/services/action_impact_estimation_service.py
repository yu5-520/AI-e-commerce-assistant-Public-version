"""System-side operating action impact estimation.

V14.4 boundary:
- This service reads the standard actionImpactInput contract first.
- Legacy evidence fields are only a safe fallback.
- Missing or mixed evidence formats must never break task creation.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.services.action_authorization_gate_service import infer_action_type

ACTION_IMPACT_ESTIMATION_VERSION = "14.4.0"


def _as_float(value: Any) -> float | None:
    if value in {None, "", "—", "未识别"}:
        return None
    try:
        return float(str(value).replace("¥", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _metric_rows(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    standard = task.get("actionImpactInput") or {}
    metrics = standard.get("metrics") if isinstance(standard, dict) else None
    if isinstance(metrics, list):
        return [item for item in metrics if isinstance(item, dict)]

    rows: List[Dict[str, Any]] = []

    def add(value: Any) -> None:
        if not value:
            return
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    rows.append(item)
                elif isinstance(item, str):
                    rows.append({"label": item, "value": None})
            return
        if isinstance(value, dict):
            required = value.get("required")
            if isinstance(required, list):
                rows.extend({"label": str(item), "value": None} for item in required)
            rows.append(value)
            return
        if isinstance(value, str):
            rows.append({"label": value, "value": None})

    add(task.get("evidencePack"))
    add(task.get("evidence"))
    detail = task.get("taskDetailReport") or {}
    if isinstance(detail, dict):
        add(detail.get("evidencePack"))
    return rows


def _metric_value(task: Dict[str, Any], names: Iterable[str]) -> float | None:
    wanted = set(names)
    for item in _metric_rows(task):
        title = str(item.get("metricCode") or item.get("title") or item.get("label") or item.get("name") or "")
        if title in wanted or any(name in title for name in wanted):
            value = item.get("metric") if item.get("metric") is not None else item.get("value")
            parsed = _as_float(value)
            if parsed is not None:
                return parsed
    return None


def _band(base: float | None, conservative_delta: float, normal_delta: float, optimistic_delta: float) -> Dict[str, Any]:
    if base is None:
        return {"basis": "pending_fact", "conservative": None, "normal": None, "optimistic": None}
    return {"basis": base, "conservative": round(base * (1 + conservative_delta), 4), "normal": round(base * (1 + normal_delta), 4), "optimistic": round(base * (1 + optimistic_delta), 4)}


def estimate_action_impact(task: Dict[str, Any], memory_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    action_type = infer_action_type(task)
    roi = _metric_value(task, ["ROI", "roi", "roas"])
    gmv = _metric_value(task, ["GMV", "支付金额", "paymentAmount"])
    click_rate = _metric_value(task, ["点击率", "clickRate"])
    conversion = _metric_value(task, ["支付转化率", "转化率", "conversionRate"])
    margin = _metric_value(task, ["毛利率", "grossMargin"])
    inventory = _metric_value(task, ["库存数量", "库存", "inventory"])

    if action_type == "activity_participation":
        effect = {"trafficMultiplier": {"conservative": 1.15, "normal": 1.45, "optimistic": 1.9}, "roi": _band(roi, -0.08, 0.02, 0.12), "gmv": _band(gmv, 0.12, 0.35, 0.7), "grossMarginRate": _band(margin, -0.08, -0.03, 0.0), "inventoryConsumption": _band(inventory, -0.3, -0.5, -0.75), "refundRisk": "activity facts are recalculated by the system"}
    elif action_type in {"title_test", "main_image_test", "creative_material_test"}:
        effect = {"clickRate": _band(click_rate, 0.03, 0.08, 0.15), "conversionRate": _band(conversion, -0.02, 0.0, 0.05), "roi": _band(roi, -0.03, 0.02, 0.08), "gmv": _band(gmv, 0.02, 0.08, 0.18), "testWindowDays": 3}
    elif action_type == "traffic_expansion":
        effect = {"roi": _band(roi, -0.1, -0.03, 0.05), "gmv": _band(gmv, 0.08, 0.22, 0.42), "clickRate": _band(click_rate, -0.02, 0.0, 0.05), "conversionRate": _band(conversion, -0.03, 0.0, 0.03)}
    else:
        effect = {"roi": _band(roi, -0.05, 0.0, 0.05), "gmv": _band(gmv, 0.0, 0.08, 0.2), "grossMarginRate": _band(margin, -0.03, 0.0, 0.02)}

    return {"version": ACTION_IMPACT_ESTIMATION_VERSION, "mode": "standard_action_impact_input_first", "actionType": action_type, "scenarioBands": effect, "inputContract": "actionImpactInput.metrics", "memoryContext": memory_context or {}}


def apply_action_impact_estimation(task: Dict[str, Any], memory_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    estimate = estimate_action_impact(task, memory_context=memory_context)
    return {**task, "actionImpactEstimate": estimate, "v126ImpactEstimate": estimate}
