from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

V19_PRODUCT_LOGIC_CONTRACT_VERSION = "20.14"
DEFAULT_REVIEW_METRICS = ["点击率", "点击量", "转化率", "支付金额", "GMV", "广告消耗", "ROI/ROAS"]
BLANK_TEXT = {"", "—", "未识别", "UNKNOWN", "null", "None"}
METRIC_LABELS = {
    "paymentAmount": "支付金额", "payment_amount": "支付金额", "payAmount": "支付金额",
    "gmv": "GMV", "GMV": "GMV",
    "clickRate": "点击率", "click_rate": "点击率", "ctr": "点击率",
    "conversionRate": "转化率", "conversion_rate": "转化率", "cvr": "转化率",
    "adSpend": "广告消耗", "ad_spend": "广告消耗",
    "roi": "ROI/ROAS", "roas": "ROI/ROAS",
    "inventory": "库存", "stock": "库存",
    "availableDays": "可售天数", "available_days": "可售天数",
    "refundRate": "退款率", "afterSalesRate": "售后率",
    "organicVisitors": "自然访客", "paidVisitors": "付费访客", "visitorCount": "访客数",
}
LEGACY_FALLBACK_MARKERS = [
    "补齐后重新运行", "缺失数据或动作方案", "动作族数据补包站", "Agent2动作方案站",
    "任务映射站", "补齐【", "重新运行动作族", "系统生成异常",
    "action_plan_missing_data", "data_evidence_task",
]
OVERRIDE_KEYS = {
    "productIdentity", "systemChangePack", "dynamicMetricChanges", "agentOperatingJudgment",
    "operatorJudgmentView", "taskPlan", "operatorExecutionSop", "operatorActionSteps",
    "sopSteps", "reviewMetrics", "productActionCards", "v19ProductLogicContract",
}


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in BLANK_TEXT
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return [item for item in value if not _blank(item)] if isinstance(value, list) else []


def _first(*values: Any) -> Any:
    for value in values:
        if not _blank(value):
            return value
    return None


def _deep_find(obj: Any, keys: Iterable[str]) -> Any:
    key_list = list(keys)
    if isinstance(obj, dict):
        for key in key_list:
            if not _blank(obj.get(key)):
                return obj.get(key)
        for value in obj.values():
            found = _deep_find(value, key_list)
            if not _blank(found):
                return found
    elif isinstance(obj, (list, tuple)):
        for value in obj[:30]:
            found = _deep_find(value, key_list)
            if not _blank(found):
                return found
    return None


def _merge(base: Dict[str, Any] | None, *extras: Dict[str, Any] | None) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base or {})
    for data in extras:
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = _merge(out[key], value)
            elif key not in out or _blank(out.get(key)) or key in OVERRIDE_KEYS:
                if not _blank(value):
                    out[key] = value
    return out


def _num(value: Any) -> float | None:
    if _blank(value) or isinstance(value, (dict, list, tuple, set)):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").replace("￥", "").replace("¥", "").strip())
    except Exception:
        return None


def _ratio(item: Dict[str, Any]) -> float | None:
    for key in ["changeRatio", "changeRate", "deltaRatio", "deltaRate", "changeVsPrevious", "ratio"]:
        raw = _num(item.get(key))
        if raw is not None:
            return raw / 100 if abs(raw) > 2 else raw
    prev = _num(_first(item.get("previousValue"), item.get("previous"), item.get("before"), item.get("oldValue")))
    cur = _num(_first(item.get("currentValue"), item.get("current"), item.get("newValue"), item.get("latest")))
    if prev not in {None, 0} and cur is not None:
        return (cur - prev) / prev
    return None


def _fmt_num(value: Any) -> str:
    n = _num(value)
    if n is None:
        return "待确认" if value is None else str(value)
    return (f"{n:.2f}" if abs(n) >= 100 else f"{n:.2f}").rstrip("0").rstrip(".")


def _fmt_ratio(value: float | None) -> str:
    return "" if value is None else f"{value * 100:+.1f}%"


def _metric_label(code: Any, fallback: Any = None) -> str:
    raw = str(_first(fallback, code, "指标变化"))
    return METRIC_LABELS.get(str(code), METRIC_LABELS.get(raw, raw))


def _metric_item(item: Dict[str, Any]) -> Dict[str, Any] | None:
    code = _first(item.get("metricCode"), item.get("code"), item.get("metric"), item.get("field"))
    label = _metric_label(code, _first(item.get("metricName"), item.get("label"), item.get("title"), code))
    previous = _first(item.get("previousValue"), item.get("previous"), item.get("before"), item.get("oldValue"))
    current = _first(item.get("currentValue"), item.get("current"), item.get("newValue"), item.get("latest"))
    ratio = _ratio(item)
    changed = bool(item.get("meaningfulChange") or item.get("changed") or item.get("signalStrength") in {"high", "medium"})
    changed = changed or (ratio is not None and abs(ratio) > 0.000001) or (previous is not None and current is not None and str(previous) != str(current))
    if not changed:
        return None
    summary = item.get("summary") or f"{label}：{_fmt_num(previous)} → {_fmt_num(current)}"
    if ratio is not None:
        summary = f"{summary}，变化 {_fmt_ratio(ratio)}"
    return {
        "type": "dynamic_metric_change", "metricCode": code, "metricName": label,
        "previousValue": previous, "currentValue": current, "changeRatio": ratio,
        "changeRate": _fmt_ratio(ratio), "summary": summary, "meaningfulChange": True,
    }


def _metric_candidates(*objs: Any) -> List[Any]:
    keys = ["dynamicMetricChanges", "metricChanges", "fieldSignals", "changeSignals", "changedMetrics", "metricSignals", "lines"]
    out: List[Any] = []

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(value, dict):
            for key in keys:
                if isinstance(value.get(key), list):
                    out.extend(value.get(key) or [])
            for key in ["metricEvidence", "metricLayer", "snapshotLayer", "dynamicMetrics", "systemFacts", "signal", "signalEvidence", "systemChangePack", "changePack"]:
                nested = value.get(key)
                if isinstance(nested, (dict, list)):
                    walk(nested, depth + 1)
        elif isinstance(value, list):
            for item in value[:120]:
                if isinstance(item, (dict, str)):
                    out.append(item)
                if isinstance(item, dict):
                    walk(item, depth + 1)

    for obj in objs:
        walk(obj)
    return out


def build_dynamic_metric_changes(*objs: Any) -> List[Dict[str, Any]]:
    changes: List[Dict[str, Any]] = []
    seen = set()
    for item in _metric_candidates(*objs):
        if isinstance(item, str):
            text = item.strip()
            if text and "+0.0%" not in text and "变化 0" not in text and text not in seen:
                seen.add(text)
                changes.append({"type": "dynamic_metric_change", "metricName": "指标变化", "summary": text, "meaningfulChange": True})
            continue
        if not isinstance(item, dict):
            continue
        change = _metric_item(item)
        if not change:
            continue
        key = (change.get("metricCode"), change.get("metricName"), change.get("previousValue"), change.get("currentValue"), change.get("changeRate"))
        if key in seen:
            continue
        seen.add(key)
        changes.append(change)
        if len(changes) >= 12:
            break
    return changes


def build_product_identity(*objs: Any) -> Dict[str, Any]:
    raw: Dict[str, Any] = {}
    for obj in objs:
        if not isinstance(obj, (dict, list, tuple)):
            continue
        for key in ["productIdentity", "profileLayer", "productProfile", "identity"]:
            value = _deep_find(obj, [key])
            if isinstance(value, dict):
                raw = _merge(raw, value)
    product_id = _first(raw.get("productId"), _deep_find(objs, ["productId", "product_id", "entityId", "platformItemId", "商品ID"]))
    title = _first(raw.get("productTitle"), raw.get("title"), _deep_find(objs, ["productTitle", "title", "商品标题", "商品名称", "shortName"]))
    store_id = _first(raw.get("storeId"), _deep_find(objs, ["storeId", "store_id", "店铺ID"]))
    store_name = _first(raw.get("storeName"), raw.get("store"), _deep_find(objs, ["storeName", "store", "店铺"]))
    sku = _first(raw.get("skuId"), raw.get("sku"), raw.get("skuCode"), _deep_find(objs, ["skuId", "sku", "skuCode", "规格"]))
    vertical = _first(raw.get("verticalCategory"), raw.get("category"), _deep_find(objs, ["verticalCategory", "category", "类目"]))
    platform = _first(raw.get("platform"), _deep_find(objs, ["platform", "平台"]))
    return {
        **raw, "productId": product_id, "productTitle": title or product_id or "商品未命名",
        "title": title or product_id or "商品未命名", "systemProductCode": raw.get("systemProductCode") or product_id,
        "storeId": store_id, "storeName": store_name or store_id or "经营单元",
        "platform": platform or "经营平台", "skuId": sku or "未标注",
        "platformItemId": raw.get("platformItemId") or product_id, "verticalCategory": vertical or "未归类",
    }


def _first_dict(*objs: Any, keys: List[str]) -> Dict[str, Any]:
    for key in keys:
        value = _deep_find(objs, [key])
        if isinstance(value, dict) and value:
            return value
    return {}


def _clean_lines(value: Any) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in _arr(value):
        text = str(_first(item.get("action"), item.get("text"), item.get("summary"), item.get("title"), item.get("value"), item.get("reason"), "") if isinstance(item, dict) else item)
        text = " ".join(text.split()).strip(" ,;，；")
        if not text or any(marker in text for marker in LEGACY_FALLBACK_MARKERS):
            continue
        if text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= 14:
            break
    return result


def build_v19_product_logic_contract(task: Dict[str, Any] | None = None, payload: Dict[str, Any] | None = None, report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    task = dict(task or {})
    payload = dict(payload or {})
    report = dict(report or {})
    sop = _first_dict(task, payload, report, keys=["sopDecision"])
    agent2 = _first_dict(task, payload, report, keys=["agent2ActionPlan", "plan", "actionPlan"])
    action_pack = _first_dict(task, payload, report, keys=["actionParameterPack"])
    matrix = _first_dict(task, payload, report, keys=["matrixDispatch"])
    agent1 = _first_dict(task, payload, report, keys=["agentOperatingJudgment", "agent1OperatingJudgment", "agentJudgment", "operatingJudgmentBrief"])
    identity = build_product_identity(task, payload, report, sop, agent2, action_pack)
    changes = build_dynamic_metric_changes(task, payload, report, sop, agent2, action_pack)

    plan = _merge(
        _first_dict(task, payload, report, keys=["taskPlan", "taskCard"]),
        _dict(sop.get("taskPlan")),
        _dict(agent2.get("taskPlan")),
    )
    family = _first(plan.get("selectedActionFamily"), plan.get("actionFamily"), sop.get("actionFamily"), agent2.get("actionFamily"), action_pack.get("actionFamily"), matrix.get("selectedActionFamily"), payload.get("actionFamily"), task.get("actionFamily"))
    creative = _first(plan.get("creativeTestPlan"), agent2.get("creativeTestPlan"), payload.get("creativeTestPlan"))
    if isinstance(creative, dict):
        plan["creativeTestPlan"] = creative
    for key in ["budgetPlan", "activityPlan", "conversionRepairPlan", "executionParameters"]:
        value = _first(plan.get(key), agent2.get(key), payload.get(key))
        if isinstance(value, dict):
            plan[key] = value
    operator_lines = _clean_lines(_first(plan.get("operatorExecutionSop"), plan.get("operatorActionSteps"), sop.get("operatorExecutionSop"), sop.get("sopSteps"), sop.get("operatorActionSteps"), agent2.get("operatorActionSteps"), task.get("operatorExecutionSop"), report.get("operatorExecutionSop")))
    review_metrics = _arr(_first(plan.get("reviewMetrics"), agent2.get("reviewMetrics"), sop.get("reviewMetrics"), task.get("reviewMetrics"))) or DEFAULT_REVIEW_METRICS[:4]
    operator_view = _dict(plan.get("operatorJudgmentView")) or {
        "selectedDirection": _first(plan.get("selectedDirection"), plan.get("businessHypothesis"), agent1.get("businessHypothesis"), agent1.get("primaryBusinessSignal"), agent1.get("primaryOperatingGap"), family, "当前商品经营动作"),
        "displayReason": _first(plan.get("reason"), agent1.get("reason"), agent1.get("primaryOperatingGap"), changes[0].get("summary") if changes else None, "Agent已完成经营路线判断，系统按商品动态证据生成任务。"),
        "testFocus": _first(plan.get("testFocus"), plan.get("testGoal"), agent1.get("testFocus"), "围绕本次核心变化指标执行验证。"),
        "recapBasis": "提交执行痕迹后，系统按后续报表自动复盘。",
        "source": "v19_product_logic_contract_on_v20_payload",
    }
    system_change_pack = {
        "version": V19_PRODUCT_LOGIC_CONTRACT_VERSION, "source": "pipeline_items.payload",
        "dynamicMetricChanges": changes, "lines": changes, "hasRealDynamicChange": bool(changes),
        "rule": "V20.14 restores V19 task detail metric-change contract without reading legacy runtime tables.",
    }
    plan = _merge(plan, {
        "selectedActionFamily": family, "actionFamily": family,
        "selectedOperatingRoute": _first(matrix.get("routeName"), matrix.get("routeId"), payload.get("route"), agent1.get("selectedOperatingRoute")),
        "operatorJudgmentView": operator_view, "productIdentity": identity,
        "agentOperatingJudgment": agent1, "agent2ActionPlan": agent2, "actionParameterPack": action_pack,
        "matrixDispatch": matrix, "operatorExecutionSop": operator_lines, "operatorActionSteps": operator_lines,
        "reviewMetrics": review_metrics, "dynamicMetricChanges": changes, "systemChangePack": system_change_pack,
    })
    return {
        "version": V19_PRODUCT_LOGIC_CONTRACT_VERSION, "productIdentity": identity,
        "systemChangePack": system_change_pack, "dynamicMetricChanges": changes,
        "agentOperatingJudgment": agent1, "agentJudgment": agent1, "operatorJudgmentView": operator_view,
        "taskPlan": plan, "agent2ActionPlan": agent2, "sopDecision": sop,
        "actionParameterPack": action_pack, "matrixDispatch": matrix,
        "operatorExecutionSop": operator_lines, "sopSteps": operator_lines,
        "reviewMetrics": review_metrics, "productActionCards": [identity] if identity else [],
        "v19ProductLogicContract": {
            "version": V19_PRODUCT_LOGIC_CONTRACT_VERSION, "runtimeSource": "pipeline_items.payload",
            "legacyRuntimeSourceUsed": False,
            "restoredFields": ["productIdentity", "systemChangePack", "dynamicMetricChanges", "agentOperatingJudgment", "operatorJudgmentView", "taskPlan", "operatorExecutionSop"],
        },
    }


def apply_v19_product_logic_contract(task: Dict[str, Any] | None, payload: Dict[str, Any] | None, report: Dict[str, Any] | None = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    task = dict(task or {})
    report = dict(report or {})
    contract = build_v19_product_logic_contract(task, payload, report)
    task_out = _merge(task, contract)
    report_out = _merge(report, contract)
    task_out["taskDetailReport"] = _merge(_dict(task_out.get("taskDetailReport")), report_out)
    return task_out, report_out


def stamp_v19_product_logic_contract(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = dict(payload or {})
    contract = build_v19_product_logic_contract({}, payload, {})
    return _merge(payload, contract)
