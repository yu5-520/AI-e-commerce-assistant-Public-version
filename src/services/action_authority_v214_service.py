"""V21.4.0 operation-level action authority.

The authority matrix reads explicit operations. It never multiplies a generic
percentage by budget and never infers direction from `roas_scale`/`roas_guard`.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from src.services.action_plan_ir_v214_service import (
    ACTION_PLAN_IR_VERSION,
    ROAS_FAMILIES,
    authority_parameters_from_plan_ir,
    missing_action_plan_ir,
    normalize_action_plan_ir,
)
from src.services import action_authority_v21_service as legacy

ACTION_AUTHORITY_VERSION = "21.4.0"
AUTO_EXECUTE = legacy.AUTO_EXECUTE
MANAGER_APPROVAL = legacy.MANAGER_APPROVAL
OWNER_APPROVAL = legacy.OWNER_APPROVAL
AUTHORIZATION_DATA_MISSING = legacy.AUTHORIZATION_DATA_MISSING
DEFAULT_OPERATOR_AUTHORITY = legacy.DEFAULT_OPERATOR_AUTHORITY
DEFAULT_STORE_POLICY = legacy.DEFAULT_STORE_POLICY
ensure_action_authority_tables = legacy.ensure_action_authority_tables
get_operator_authority = legacy.get_operator_authority
update_operator_authority = legacy.update_operator_authority
get_store_policy = legacy.get_store_policy
update_store_policy = legacy.update_store_policy
record_authorization_decision = legacy.record_authorization_decision
record_authorized_usage = legacy.record_authorized_usage


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _identity(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _dict(decision.get("taskPlan")); product = _dict(plan.get("productIdentity")); package = _dict(decision.get("productJudgmentPackage"))
    return {"productId": decision.get("productId") or plan.get("productId") or product.get("productId") or package.get("productId"), "storeId": decision.get("storeId") or plan.get("storeId") or product.get("storeId") or package.get("storeId")}


def _operator_for_store(store_id: str | None, plan: Dict[str, Any]) -> str | None:
    explicit = plan.get("assignedOperatorId") or plan.get("operatorId")
    if explicit: return str(explicit)
    assignment = legacy.assignment_for_store(store_id) if store_id else None
    return str((assignment or {}).get("primaryOperatorId") or "") or None


def authorize_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    plan = _dict(decision.get("taskPlan")); family = str(plan.get("selectedActionFamily") or decision.get("actionFamily") or "").strip(); identity = _identity(decision); store_id = str(identity.get("storeId") or "") or None; product_id = str(identity.get("productId") or "") or None; operator_id = _operator_for_store(store_id, plan)
    if family not in ROAS_FAMILIES:
        result = dict(legacy.authorize_decision(decision)); result.update(version=ACTION_AUTHORITY_VERSION, mode="v21_4_non_roas_existing_policy", operationPlanVersion=ACTION_PLAN_IR_VERSION); return result
    if not operator_id:
        return {"version": ACTION_AUTHORITY_VERSION, "mode": "v21_4_operation_level_roas_authority", "decision": AUTHORIZATION_DATA_MISSING, "lifecycleDecision": None, "approvalRequired": False, "requiredAuthorityLevel": "operator_assignment_required", "operatorId": None, "storeId": store_id, "productId": product_id, "actionFamily": family, "reason": "店铺没有绑定主运营，无法计算ROAS执行权限。", "missing": ["assignedOperatorId"], "highRiskValidation": True}

    agent2 = _dict(decision.get("agent2ActionPlan") or plan.get("agent2ActionPlan")); operation_plan = _dict(plan.get("operationPlan") or agent2.get("operationPlan") or decision.get("operationPlan")); source_plan = {**agent2, **plan, "operationPlan": operation_plan}
    if not operation_plan:
        operation_plan = normalize_action_plan_ir(source_plan, family); source_plan["operationPlan"] = operation_plan
    missing = missing_action_plan_ir(source_plan, family); params = authority_parameters_from_plan_ir(source_plan, family)
    if missing or params.get("validationPassed") is not True:
        return {"version": ACTION_AUTHORITY_VERSION, "mode": "v21_4_operation_level_roas_authority", "decision": AUTHORIZATION_DATA_MISSING, "lifecycleDecision": None, "approvalRequired": False, "requiredAuthorityLevel": get_operator_authority(operator_id, family).get("authorityLevel"), "operatorId": operator_id, "storeId": store_id, "productId": product_id, "actionFamily": family, "reason": "ROAS任务缺少可验证的操作级Plan IR；禁止根据动作族名称或无作用域百分比猜测预算、出价与方向。", "missing": missing or params.get("missing") or ["operationPlan"], "parameters": params, "operationPlan": operation_plan, "highRiskValidation": True}

    authority = get_operator_authority(operator_id, family); store_policy = get_store_policy(store_id or "GLOBAL"); usage = legacy._usage(operator_id, store_id, family)
    budget_multiplier = float(store_policy.get("budgetLimitMultiplier") or 1); roas_multiplier = float(store_policy.get("roasChangeMultiplier") or 1); owner_multiplier = float(store_policy.get("ownerApprovalMultiplier") or 1)
    single_limit = float(authority.get("singleAdjustmentLimit") or 0) * budget_multiplier; daily_limit = float(authority.get("dailyAdjustmentLimit") or 0) * budget_multiplier; rolling_limit = float(authority.get("rolling24hLimit") or 0) * budget_multiplier; owner_limit = float(authority.get("ownerApprovalLimit") or 0) * owner_multiplier; control_change_limit = float(authority.get("roasChangeRateLimit") or 0) * roas_multiplier
    minimum_roas = max(float(authority.get("minimumTargetRoas") or 0), float(params.get("safetyRoas") or 0)); amount = float(params.get("adjustmentAmount") or 0); max_control_change = max(float(params.get("bidChangeRate") or 0), float(params.get("roasChangeRate") or 0))
    reasons: List[str] = []; owner_reasons: List[str] = []
    if not authority.get("enabled") or not store_policy.get("enabled"): reasons.append("authority_disabled")
    if amount > 0 and amount > single_limit: reasons.append("single_adjustment_limit_exceeded")
    if amount > 0 and usage["usedToday"] + amount > daily_limit: reasons.append("daily_adjustment_limit_exceeded")
    if amount > 0 and usage["usedRolling24h"] + amount > rolling_limit: reasons.append("rolling_24h_limit_exceeded")
    if max_control_change > control_change_limit: reasons.append("bid_or_roas_change_rate_limit_exceeded")
    if params.get("targetRoas") is not None and float(params["targetRoas"]) < minimum_roas: owner_reasons.append("target_roas_below_safety_floor")
    if owner_limit and amount > owner_limit: owner_reasons.append("owner_approval_amount_exceeded")
    if owner_reasons:
        auth_decision, lifecycle_decision, reason = OWNER_APPROVAL, "manager_review_required", "ROAS操作触碰老板级金额或安全线边界，需要高级确认。"
    elif reasons:
        auth_decision, lifecycle_decision, reason = MANAGER_APPROVAL, "manager_review_required", "ROAS操作超出运营金额或参数变动额度，需要总管审批。"
    else:
        auth_decision, lifecycle_decision, reason = AUTO_EXECUTE, "create_task_snapshot", "操作级预算、出价、目标ROAS与店铺策略均在运营有效权限内。"
    return {"version": ACTION_AUTHORITY_VERSION, "mode": "v21_4_operation_level_roas_authority", "decision": auth_decision, "lifecycleDecision": lifecycle_decision, "approvalRequired": auth_decision != AUTO_EXECUTE, "requiredAuthorityLevel": authority.get("authorityLevel"), "operatorId": operator_id, "storeId": store_id, "productId": product_id, "actionFamily": family, "reason": reason, "triggeredReasons": [*owner_reasons, *reasons], "parameters": params, "operationPlan": operation_plan, "authority": authority, "storePolicy": store_policy, "usage": usage, "effectiveLimits": {"singleAdjustmentLimit": single_limit, "dailyAdjustmentLimit": daily_limit, "rolling24hLimit": rolling_limit, "ownerApprovalLimit": owner_limit, "bidOrRoasChangeRateLimit": control_change_limit, "minimumTargetRoas": minimum_roas, "remainingToday": max(0.0, daily_limit - usage["usedToday"]), "remainingRolling24h": max(0.0, rolling_limit - usage["usedRolling24h"])}, "highRiskValidation": True, "operationPlanVersion": ACTION_PLAN_IR_VERSION, "genericAdjustmentRateUsedAsBudget": False, "familyNameUsedAsDirection": False, "rule": "V21.4.0：权限只读取操作级Plan IR；预算、出价和目标ROAS分别计算，禁止跨作用域推断。"}


def apply_authorization_to_decision(decision: Dict[str, Any]) -> Dict[str, Any]:
    next_decision = deepcopy(decision); authorization = authorize_decision(next_decision); next_decision["authorizationDecision"] = authorization; next_decision["actionAuthorization"] = authorization; next_decision["authorizationVersion"] = ACTION_AUTHORITY_VERSION
    if authorization.get("decision") == AUTHORIZATION_DATA_MISSING: return next_decision
    plan = dict(_dict(next_decision.get("taskPlan"))); approval_required = bool(authorization.get("approvalRequired"))
    plan.update({"approvalRequired": approval_required, "needManagerReview": approval_required, "assignedOperatorId": authorization.get("operatorId") if not approval_required else None, "authorizationDecision": authorization, "actionAuthorization": authorization, "authorizationVersion": ACTION_AUTHORITY_VERSION, "operationPlan": authorization.get("operationPlan") or plan.get("operationPlan")})
    next_decision.update({"decision": authorization.get("lifecycleDecision"), "taskPlan": plan, "operationPlan": plan.get("operationPlan")})
    return next_decision
