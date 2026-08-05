"""V14.7 operating action authorization gate.

Authorization reads TaskIntent PermissionEnvelope first. Budget is never inferred
from free text, product codes, deadlines, titles, or IDs. V14.7 also separates
real below-company-floor evidence from missing/untrusted impact estimates so
missing ROI does not become automatic manager approval.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.services.operating_weight_policy_service import OPERATING_WEIGHT_POLICY_VERSION, infer_operating_weight, is_governance_high_weight

ACTION_AUTHORIZATION_VERSION = "14.7.0"

ACTION_LABELS = {
    "activity_participation": "活动报名 / 活动承接",
    "inventory_restock": "库存警告 / 补货承接",
    "title_test": "标题测试",
    "main_image_test": "主图测试",
    "creative_material_test": "素材测试",
    "traffic_expansion": "扩流测试",
    "price_adjustment": "价格调整",
    "ad_budget_adjustment": "投放预算调整",
    "homepage_position": "主推位调整",
    "product_demotion": "下架 / 降权处理",
    "generic_operation": "经营处理",
}

HARD_APPROVAL_ACTIONS = {"price_adjustment", "homepage_position", "product_demotion"}
BUDGET_GATED_ACTIONS = {"ad_budget_adjustment", "activity_participation", "traffic_expansion"}
APPROVAL_ACTIONS_ON_CONFIRMED_HIGH_WEIGHT = HARD_APPROVAL_ACTIONS | BUDGET_GATED_ACTIONS | {"title_test", "main_image_test"}


def _join_text(task: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key in ("title", "task", "taskType", "actionType", "riskDomain", "sourceModule", "reason", "submissionNote"):
        if task.get(key):
            parts.append(str(task.get(key)))
    parts.extend(str(item) for item in task.get("judgmentTags") or [])
    parts.extend(str(item) for item in task.get("sopSteps") or [])
    card = task.get("taskCard") or {}
    detail = task.get("taskDetailReport") or {}
    parts.extend([str(card.get("title") or ""), str(card.get("subtitle") or ""), str(detail.get("warningSummary") or "")])
    return " ".join(parts)


def _as_float(value: Any) -> float | None:
    if value in {None, "", "—", "未识别"}:
        return None
    try:
        return float(str(value).replace("¥", "").replace(",", "").replace("元", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def infer_action_type(task: Dict[str, Any]) -> str:
    intent = task.get("taskIntent") if isinstance(task.get("taskIntent"), dict) else {}
    explicit = str(task.get("actionType") or intent.get("taskType") or "")
    if explicit in ACTION_LABELS and explicit != "generic_operation":
        return explicit
    text = _join_text(task)
    if any(token in text for token in ("库存归零", "库存", "补货", "调拨", "可售天数", "断货", "缺货")):
        return "inventory_restock"
    if any(token in text for token in ("活动", "报名", "平台补贴", "商家让利", "活动价")):
        return "activity_participation"
    if any(token in text for token in ("改价", "价格", "让利")):
        return "price_adjustment"
    if any(token in text for token in ("预算", "广告消耗", "加投", "降预算", "投放")):
        return "ad_budget_adjustment"
    if any(token in text for token in ("主推位", "首页", "资源位")):
        return "homepage_position"
    if any(token in text for token in ("下架", "降权", "暂停销售")):
        return "product_demotion"
    if any(token in text for token in ("标题", "关键词", "搜索词")):
        return "title_test"
    if any(token in text for token in ("主图", "首图", "图片")):
        return "main_image_test"
    if any(token in text for token in ("素材", "创意")):
        return "creative_material_test"
    if any(token in text for token in ("扩流", "流量入口", "曝光", "渠道覆盖")):
        return "traffic_expansion"
    return "generic_operation"


def infer_object_weight(task: Dict[str, Any]) -> Dict[str, Any]:
    return infer_operating_weight(task)


def _operator_permission_level(task: Dict[str, Any]) -> str:
    ownership = task.get("ownership") or {}
    text = " ".join([str(task.get("operatorPermissionLevel") or ""), str(ownership.get("permissionLevel") or "")])
    if "高" in text or "high" in text:
        return "high"
    if "低" in text or "low" in text:
        return "low"
    return "middle"


def _company_baseline(task: Dict[str, Any]) -> Dict[str, Any]:
    rag = task.get("ragBusinessMemory") or task.get("v126RagMemory") or {}
    baseline = rag.get("companyBaseline") if isinstance(rag.get("companyBaseline"), dict) else {}
    return baseline or {"minRoi": 2.0, "minGrossMarginRate": 0.28, "operatorActivityBudgetRange": [3000, 8000]}


def _operator_budget_range(task: Dict[str, Any]) -> tuple[float, float]:
    envelope = _permission_envelope(task)
    if envelope:
        low = _as_float(envelope.get("operatorBudgetMin"))
        high = _as_float(envelope.get("operatorBudgetMax"))
        if low is not None and high is not None:
            return low, high
    baseline = _company_baseline(task)
    raw = baseline.get("operatorActivityBudgetRange") or baseline.get("operatorBudgetRange") or [3000, 8000]
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        low = _as_float(raw[0]) or 0
        high = _as_float(raw[1]) or 0
        return low, high
    return 3000.0, 8000.0


def _permission_envelope(task: Dict[str, Any]) -> Dict[str, Any]:
    envelope = task.get("permissionEnvelope") if isinstance(task.get("permissionEnvelope"), dict) else {}
    if envelope:
        return envelope
    intent = task.get("taskIntent") if isinstance(task.get("taskIntent"), dict) else {}
    envelope = intent.get("permissionEnvelope") if isinstance(intent.get("permissionEnvelope"), dict) else {}
    return envelope or {}


def _structured_budget(task: Dict[str, Any]) -> float | None:
    envelope = _permission_envelope(task)
    if envelope:
        for key in ("estimatedBudgetCost", "requestedBudget", "budgetAmount"):
            parsed = _as_float(envelope.get(key))
            if parsed is not None:
                return parsed
    intent = task.get("taskIntent") if isinstance(task.get("taskIntent"), dict) else {}
    budget_sources = [task.get("operationBudget"), intent.get("budget") if isinstance(intent, dict) else None]
    for source in budget_sources:
        if not isinstance(source, dict):
            continue
        for key in ("estimatedBudgetCost", "requestedBudget", "budgetAmount", "activityBudget", "adBudget", "plannedBudget"):
            parsed = _as_float(source.get(key))
            if parsed is not None:
                return parsed
    for key in ("requestedBudget", "budgetAmount", "activityBudget", "adBudget", "plannedBudget"):
        parsed = _as_float(task.get(key))
        if parsed is not None:
            return parsed
    return None


def _requested_budget(task: Dict[str, Any]) -> float | None:
    return _structured_budget(task)


def _conservative_floor(task: Dict[str, Any], metric: str) -> float | None:
    estimate = task.get("actionImpactEstimate") or task.get("v126ImpactEstimate") or {}
    bands = estimate.get("scenarioBands") if isinstance(estimate.get("scenarioBands"), dict) else {}
    target = bands.get(metric) if isinstance(bands.get(metric), dict) else {}
    return _as_float(target.get("conservative"))


def _metric_rows(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for source in [task.get("metricFacts"), (task.get("actionImpactInput") or {}).get("metrics") if isinstance(task.get("actionImpactInput"), dict) else None]:
        if isinstance(source, list):
            rows.extend(item for item in source if isinstance(item, dict))
    return rows


def _has_metric_evidence(task: Dict[str, Any], names: List[str]) -> bool:
    wanted = set(names)
    for item in _metric_rows(task):
        label = str(item.get("metricCode") or item.get("label") or item.get("title") or item.get("name") or "")
        if not label:
            continue
        if label in wanted or any(name in label for name in wanted):
            if _as_float(item.get("value") if item.get("value") is not None else item.get("metric")) is not None:
                return True
    return False


def _impact_floor_state(task: Dict[str, Any]) -> Dict[str, Any]:
    baseline = _company_baseline(task)
    roi_floor = _conservative_floor(task, "roi")
    margin_floor = _conservative_floor(task, "grossMarginRate")
    min_roi = _as_float(baseline.get("minRoi"))
    min_margin = _as_float(baseline.get("minGrossMarginRate"))
    has_roi_evidence = _has_metric_evidence(task, ["ROI", "roi", "ROAS", "roas"])
    has_margin_evidence = _has_metric_evidence(task, ["毛利率", "grossMargin", "grossMarginRate"])
    roi_missing_or_untrusted = roi_floor is None or (roi_floor <= 0 and not has_roi_evidence)
    margin_missing_or_untrusted = margin_floor is None or (margin_floor <= 0 and not has_margin_evidence)
    real_below_roi = bool(roi_floor is not None and min_roi is not None and roi_floor < min_roi and not roi_missing_or_untrusted)
    real_below_margin = bool(margin_floor is not None and min_margin is not None and margin_floor < min_margin and not margin_missing_or_untrusted)
    estimate_missing = bool(roi_missing_or_untrusted or margin_missing_or_untrusted)
    return {"realBelowCompanyFloor": bool(real_below_roi or real_below_margin), "estimateMissingOrUntrusted": estimate_missing, "roiConservativeFloor": roi_floor, "marginConservativeFloor": margin_floor, "hasRoiEvidence": has_roi_evidence, "hasMarginEvidence": has_margin_evidence, "companyBaseline": baseline}


def _operator_fields(action_type: str) -> List[str]:
    return {
        "activity_participation": ["活动入口", "活动价", "平台补贴", "商家让利", "报名门槛", "资源位", "竞品价格", "竞品销量截图"],
        "inventory_restock": ["当前库存截图", "可售天数", "预计到货时间", "可调拨库存", "活动或投放承接计划"],
        "ad_budget_adjustment": ["当前广告计划截图", "拟调整预算金额", "投放时段", "人群/关键词/素材范围", "调整原因"],
        "title_test": ["原标题截图", "新标题方案", "测试开始时间", "测试范围"],
        "main_image_test": ["原主图截图", "新主图方案", "测试开始时间", "测试范围"],
        "creative_material_test": ["原素材截图", "新素材方案", "投放位置", "测试开始时间"],
    }.get(action_type, ["执行截图", "处理记录", "复核说明"])


def _envelope_requires_review(task: Dict[str, Any]) -> bool | None:
    envelope = _permission_envelope(task)
    if not envelope:
        return None
    value = envelope.get("requiresManagerReview")
    if isinstance(value, bool):
        return value
    return None


def authorize_action(task: Dict[str, Any]) -> Dict[str, Any]:
    action_type = infer_action_type(task)
    object_weight = infer_object_weight(task)
    operator_level = _operator_permission_level(task)
    confirmed_high_weight = is_governance_high_weight(object_weight)
    hard_action = action_type in HARD_APPROVAL_ACTIONS
    budget_action = action_type in BUDGET_GATED_ACTIONS
    budget = _requested_budget(task)
    budget_min, budget_max = _operator_budget_range(task)
    envelope = _permission_envelope(task)
    envelope_requires_review = _envelope_requires_review(task)
    if envelope and envelope.get("budgetOverLimit") is not None:
        budget_over_limit = bool(envelope.get("budgetOverLimit"))
    else:
        budget_over_limit = bool(budget is not None and budget_max and budget > budget_max)
    impact_state = _impact_floor_state(task)
    real_below_floor = bool(impact_state.get("realBelowCompanyFloor"))
    needs_weight_approval = confirmed_high_weight and operator_level != "high" and action_type in APPROVAL_ACTIONS_ON_CONFIRMED_HIGH_WEIGHT
    if envelope_requires_review is True:
        decision = "manager_approval_required"
        layer = "manager_approval"
        reason = envelope.get("reviewReason") or "结构化权限信封要求主管确认。"
    elif envelope_requires_review is False and not hard_action and not budget_over_limit and not real_below_floor and not needs_weight_approval:
        decision = "auto_execute"
        layer = "operator_execution"
        reason = "TaskIntent权限信封确认该任务在运营可执行范围内；缺失影响估算只补证据，不自动升级审批。"
    elif hard_action and confirmed_high_weight and task.get("priority") == "高":
        decision = "owner_approval_required"
        layer = "manager_approval"
        reason = "硬风险动作作用于已确认治理高权重/战略对象，需要老板或总管确认。"
    elif hard_action or budget_over_limit or real_below_floor or needs_weight_approval:
        decision = "manager_approval_required"
        layer = "manager_approval"
        reason = "动作触发结构化审批原因，需要主管确认。"
    else:
        decision = "auto_execute"
        layer = "operator_execution"
        reason = "动作在当前账号权限和公司基线内；缺字段降低置信度但不替代Agent判断。"
    return {
        "version": ACTION_AUTHORIZATION_VERSION,
        "mode": "task_intent_permission_envelope_v14_7_soft_impact",
        "actionType": action_type,
        "actionLabel": ACTION_LABELS.get(action_type, "经营处理"),
        "operatorPermissionLevel": operator_level,
        "objectWeight": object_weight,
        "operatingWeightPolicyVersion": OPERATING_WEIGHT_POLICY_VERSION,
        "decision": decision,
        "taskLayer": layer,
        "approvalReason": reason,
        "triggeredReasons": {"hardAction": hard_action, "budgetOverLimit": budget_over_limit, "realBelowCompanyFloor": real_below_floor, "estimateMissingOrUntrusted": bool(impact_state.get("estimateMissingOrUntrusted")), "needsWeightApproval": needs_weight_approval, "envelopeRequiresReview": envelope_requires_review is True},
        "operatorFactFields": _operator_fields(action_type),
        "permissionEnvelope": envelope,
        "budgetGate": {"isBudgetAction": budget_action, "requestedBudget": budget, "operatorBudgetMin": budget_min, "operatorBudgetMax": budget_max, "budgetOverLimit": budget_over_limit, "budgetBelowSuggestedMin": bool(budget is not None and budget_min and budget < budget_min), "budgetSource": "structured_only", "freeTextBudgetParsingAllowed": False},
        "impactGate": {"usesSystemEstimate": True, "belowCompanyFloor": real_below_floor, **impact_state},
        "policy": {"operatorProvidesFactsOnly": True, "systemEstimatesImpact": True, "approvalUsesRealBelowFloorOnly": True, "estimateMissingDoesNotAutoApprove": True, "reportPerformanceIsNotGovernanceWeight": True, "inventorySignalsBeforeCreativeWords": True, "budgetActionIsNotAutomaticManagerApproval": True, "freeTextBudgetParsingAllowed": False, "rule": "V14.7：审批预算只读取结构化字段；缺失ROI/影响估算进入补证据，不自动变主管审批。"},
    }


def apply_action_authorization(task: Dict[str, Any]) -> Dict[str, Any]:
    gate = authorize_action(task)
    next_task = {**task, "actionAuthorization": gate, "v147ActionGate": gate, "v1441ActionGate": gate, "v1282ActionGate": gate, "v127ActionGate": gate, "v126ActionGate": gate}
    if gate["decision"] in {"manager_approval_required", "owner_approval_required"}:
        next_task["taskLayer"] = gate.get("taskLayer") or "manager_approval"
        next_task["assigneeId"] = None
        next_task["status"] = "待复核"
        next_task["workflowStatus"] = "待审批" if gate["decision"] == "manager_approval_required" else "待老板确认"
        next_task["displayStatus"] = next_task["workflowStatus"]
        next_task["visibleRoleIds"] = list(dict.fromkeys([*(next_task.get("visibleRoleIds") or []), "owner", "manager"]))
        card = dict(next_task.get("taskCard") or {})
        card["subtitle"] = "主管审批｜" + gate.get("actionLabel", "经营动作") if gate["decision"] == "manager_approval_required" else "老板确认｜" + gate.get("actionLabel", "经营动作")
        next_task["taskCard"] = card
        next_task["taskType"] = "主管审批任务" if gate["decision"] == "manager_approval_required" else "老板确认任务"
        next_task["priority"] = "高" if gate["decision"] == "owner_approval_required" else next_task.get("priority", "中")
    else:
        next_task["taskLayer"] = "operator_execution"
        ownership = next_task.get("ownership") or {}
        if not next_task.get("assigneeId"):
            next_task["assigneeId"] = ownership.get("assignedOperatorId")
        next_task.setdefault("status", "待接收")
        next_task.setdefault("workflowStatus", "待接收")
        next_task.setdefault("displayStatus", next_task.get("workflowStatus") or "待接收")
    return next_task
