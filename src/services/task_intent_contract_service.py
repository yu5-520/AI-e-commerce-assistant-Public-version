"""V14.4.1 task intent contract service.

TaskIntent is the stable boundary between Agent output, task snapshots, task pool,
and legacy task creation. V14.4.1 adds PermissionEnvelope so downstream approval
logic reads structured budget/permission fields instead of guessing numbers from
free text, product codes, deadlines, or titles.
"""

from __future__ import annotations

from typing import Any, Dict, List

TASK_INTENT_CONTRACT_VERSION = "14.4.1"
DEFAULT_OPERATOR_BUDGET_MIN = 3000.0
DEFAULT_OPERATOR_BUDGET_MAX = 8000.0
HARD_REVIEW_DECISIONS = {"manager_review_required", "owner_approval_required"}
HARD_REVIEW_TASK_TYPES = {"price_adjustment", "homepage_position", "product_demotion"}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: Any) -> float | None:
    if value in {None, "", "—", "未识别"}:
        return None
    try:
        return float(str(value).replace("¥", "").replace(",", "").replace("元", "").replace("%", "").strip())
    except Exception:
        return None


def _metric_rows(*values: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for value in values:
        for item in _as_list(value):
            if isinstance(item, dict):
                if item.get("metricCode") or item.get("label") or item.get("title"):
                    rows.append(item)
            elif isinstance(item, str):
                rows.append({"label": item, "value": None})
    return rows


def _evidence_requirements(*values: Any) -> List[str]:
    result: List[str] = []
    for value in values:
        if isinstance(value, dict):
            required = value.get("required")
            if isinstance(required, list):
                result.extend(str(item) for item in required if item)
            continue
        for item in _as_list(value):
            if isinstance(item, dict):
                label = item.get("title") or item.get("label") or item.get("name")
                if label:
                    result.append(str(label))
            elif item:
                result.append(str(item))
    return list(dict.fromkeys(result))


def _budget_cost(budget: Dict[str, Any]) -> float | None:
    for key in ("estimatedBudgetCost", "requestedBudget", "budgetAmount", "activityBudget", "adBudget", "plannedBudget"):
        value = _float(budget.get(key)) if isinstance(budget, dict) else None
        if value is not None:
            return value
    return None


def build_permission_envelope(intent_seed: Dict[str, Any], budget: Dict[str, Any]) -> Dict[str, Any]:
    decision = str(intent_seed.get("decision") or "create_task_snapshot")
    task_type = str(intent_seed.get("taskType") or "general_operation")
    risk_level = str(intent_seed.get("riskLevel") or budget.get("riskLevel") or "low")
    cost = _budget_cost(budget)
    budget_max = _float(budget.get("operatorBudgetMax")) if isinstance(budget, dict) else None
    budget_min = _float(budget.get("operatorBudgetMin")) if isinstance(budget, dict) else None
    budget_max = budget_max if budget_max is not None else DEFAULT_OPERATOR_BUDGET_MAX
    budget_min = budget_min if budget_min is not None else DEFAULT_OPERATOR_BUDGET_MIN
    operator_budget_applies = bool(budget.get("operatorBudgetApplies", risk_level != "high")) if isinstance(budget, dict) else risk_level != "high"
    hard_decision = decision in HARD_REVIEW_DECISIONS
    hard_task_type = task_type in HARD_REVIEW_TASK_TYPES
    high_risk = risk_level == "high"
    budget_over_limit = bool(operator_budget_applies and cost is not None and budget_max and cost > budget_max)
    requires_review = bool(hard_decision or hard_task_type or high_risk or budget_over_limit)
    if hard_decision:
        reason = "Agent decision requires manager review."
    elif hard_task_type:
        reason = "Hard operating action requires manager review."
    elif high_risk:
        reason = "High-risk task requires manager review."
    elif budget_over_limit:
        reason = "Estimated budget exceeds operator budget limit."
    else:
        reason = None
    return {
        "version": TASK_INTENT_CONTRACT_VERSION,
        "operatorBudgetApplies": operator_budget_applies,
        "estimatedBudgetCost": cost,
        "operatorBudgetMin": budget_min,
        "operatorBudgetMax": budget_max,
        "budgetOverLimit": budget_over_limit,
        "requiresManagerReview": requires_review,
        "reviewReason": reason,
        "budgetSource": "TaskIntent.budget",
        "freeTextBudgetParsingAllowed": False,
        "rule": "Permission uses structured budget and decision fields only; product codes, titles, deadlines, and free text cannot become budget.",
    }


def normalize_task_intent(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    payload = payload or {}
    task_plan = payload.get("taskPlan") or {}
    signal = payload.get("signal") or ((payload.get("systemFacts") or {}).get("signal") if isinstance(payload.get("systemFacts"), dict) else {}) or {}
    budget = payload.get("operationBudget") or task_plan.get("operationBudget") or {}
    seed = {
        "taskType": task_plan.get("taskType") or payload.get("taskType") or "general_operation",
        "decision": payload.get("decision") or task_plan.get("decision") or "create_task_snapshot",
        "riskLevel": payload.get("riskLevel") or task_plan.get("riskLevel") or budget.get("riskLevel") or "low",
    }
    entity = {
        "entityType": payload.get("entityType") or task_plan.get("entityType") or signal.get("entityType") or "product",
        "entityId": payload.get("entityId") or task_plan.get("entityId") or signal.get("entityId"),
        "productId": payload.get("productId") or task_plan.get("productId") or signal.get("productId"),
        "storeId": payload.get("storeId") or task_plan.get("storeId") or signal.get("storeId"),
        "verticalCategory": task_plan.get("verticalCategory") or signal.get("verticalCategory"),
    }
    sop = _as_list(task_plan.get("sopSteps") or payload.get("sop") or payload.get("steps"))
    evidence = _evidence_requirements(payload.get("evidenceRequirements"), task_plan.get("evidenceRequirements"), payload.get("evidencePack"))
    metrics = _metric_rows(payload.get("metricFacts"), payload.get("reviewMetrics"), (signal.get("productMetricSnapshot") or {}).get("metricFacts") if isinstance(signal.get("productMetricSnapshot"), dict) else None)
    permission = build_permission_envelope(seed, budget if isinstance(budget, dict) else {})
    intent = {
        "version": TASK_INTENT_CONTRACT_VERSION,
        "taskType": seed["taskType"],
        "decision": seed["decision"],
        "riskLevel": seed["riskLevel"],
        "priority": task_plan.get("priority") or payload.get("priority") or "中",
        "title": task_plan.get("title") or payload.get("title") or "经营任务",
        "deadline": task_plan.get("deadline") or payload.get("deadline") or "24小时内",
        "entity": entity,
        "budget": budget if isinstance(budget, dict) else {},
        "permissionEnvelope": permission,
        "sop": [str(item) for item in sop if item],
        "evidenceRequirements": evidence,
        "metricFacts": metrics,
        "sourceRefs": {
            "signalId": payload.get("signalRef") or payload.get("signalId"),
            "judgmentId": (payload.get("systemFacts") or {}).get("judgmentId") if isinstance(payload.get("systemFacts"), dict) else payload.get("judgmentId"),
            "taskSnapshotId": payload.get("taskSnapshotId"),
        },
    }
    return intent


def validate_task_intent(intent: Dict[str, Any]) -> Dict[str, Any]:
    missing = []
    if not intent.get("decision"):
        missing.append("decision")
    if not intent.get("title"):
        missing.append("title")
    if not isinstance(intent.get("entity"), dict):
        missing.append("entity")
    if not isinstance(intent.get("budget"), dict):
        missing.append("budget")
    if not isinstance(intent.get("permissionEnvelope"), dict):
        missing.append("permissionEnvelope")
    if not isinstance(intent.get("evidenceRequirements"), list):
        missing.append("evidenceRequirements")
    if not isinstance(intent.get("metricFacts"), list):
        missing.append("metricFacts")
    return {"version": TASK_INTENT_CONTRACT_VERSION, "status": "passed" if not missing else "failed", "missing": missing}


def to_legacy_task_payload(payload: Dict[str, Any] | None) -> Dict[str, Any]:
    intent = normalize_task_intent(payload)
    validation = validate_task_intent(intent)
    entity = intent.get("entity") or {}
    permission = intent.get("permissionEnvelope") or {}
    return {
        **(payload or {}),
        "taskIntent": intent,
        "taskIntentValidation": validation,
        "permissionEnvelope": permission,
        "title": intent.get("title"),
        "priority": intent.get("priority"),
        "riskLevel": intent.get("riskLevel"),
        "storeId": entity.get("storeId"),
        "productId": entity.get("productId"),
        "verticalCategory": entity.get("verticalCategory"),
        "operationBudget": intent.get("budget") or {},
        "requestedBudget": permission.get("estimatedBudgetCost"),
        "operatorBudgetApplies": permission.get("operatorBudgetApplies"),
        "evidencePack": [{"title": item, "value": None} for item in intent.get("evidenceRequirements") or []],
        "actionImpactInput": {"metrics": intent.get("metricFacts") or [], "budget": intent.get("budget") or {}, "taskType": intent.get("taskType")},
        "sopSteps": intent.get("sop") or [],
    }
