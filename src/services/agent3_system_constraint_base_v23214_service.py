"""Stable V23.2.14 Agent3 execution-step constraint used by later contracts."""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

BASE_AGENT3_SYSTEM_CONSTRAINT_VERSION = "23.2.14"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 1200) -> str:
    if isinstance(value, dict):
        value = value.get("instruction") or value.get("action") or value.get("summary") or value.get("text")
    return " ".join(str(value or "").split())[:limit]


def _norm(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _compact(value: Any, *, depth: int = 0, max_depth: int = 7, max_list: int = 24) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text(value)
    if depth >= max_depth:
        return value if isinstance(value, (str, int, float, bool)) else None
    if isinstance(value, list):
        result = []
        for item in value[:max_list]:
            compacted = _compact(item, depth=depth + 1, max_depth=max_depth, max_list=max_list)
            if compacted not in (None, "", [], {}):
                result.append(compacted)
        return result
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            compacted = _compact(item, depth=depth + 1, max_depth=max_depth, max_list=max_list)
            if compacted not in (None, "", [], {}):
                result[str(key)] = compacted
        return result
    return _text(value)


_FAMILY_POLICIES: Dict[str, Dict[str, Any]] = {
    "title_image_test": {
        "allowedActionTypes": [
            "creative_brief", "title_generation", "image_generation", "creative_production",
            "creative_review", "experiment_grouping", "creative_replacement",
            "detail_consistency_check", "result_review", "rollback",
        ],
        "requiredActionTypeGroups": [
            ["creative_brief", "title_generation", "image_generation", "creative_production"],
            ["experiment_grouping", "creative_replacement"],
            ["detail_consistency_check", "result_review"],
        ],
        "forbiddenActions": [
            "inventory_coordination", "warehouse_followup", "replenishment_request",
            "roas_adjustment", "budget_adjustment", "activity_application",
            "logistics_action", "customer_service_action",
        ],
        "blockedKeyTokens": [
            "inventory", "warehouse", "replenish", "restock", "stockcoordination",
            "roas", "budget", "campaign", "activityapply", "platformactivity",
            "logistics", "customerservice",
        ],
        "blockedTextTerms": [
            "仓储", "仓库", "库存", "补货", "在途库存", "ROAS", "预算调整",
            "活动报名", "物流协同", "客服协同",
        ],
    },
    "conversion_repair": {
        "allowedActionTypes": [
            "page_audit", "problem_localization", "content_restructure", "trust_repair",
            "detail_consistency_check", "experiment_control", "result_review", "rollback",
        ],
        "requiredActionTypeGroups": [
            ["page_audit", "problem_localization"],
            ["content_restructure", "trust_repair", "detail_consistency_check"],
            ["experiment_control", "result_review"],
        ],
        "forbiddenActions": [
            "inventory_coordination", "warehouse_followup", "replenishment_request",
            "roas_adjustment", "budget_adjustment", "activity_application",
        ],
        "blockedKeyTokens": [
            "inventory", "warehouse", "replenish", "restock", "stockcoordination",
            "roas", "budget", "activityapply", "platformactivity",
        ],
        "blockedTextTerms": ["仓储", "仓库", "库存", "补货", "在途库存", "ROAS", "预算调整", "活动报名"],
    },
    "roas_guard": {
        "allowedActionTypes": [
            "plan_audit", "budget_adjustment", "bid_adjustment", "schedule_adjustment",
            "audience_adjustment", "plan_split", "result_review", "rollback",
        ],
        "requiredActionTypeGroups": [
            ["plan_audit"],
            ["budget_adjustment", "bid_adjustment", "schedule_adjustment", "audience_adjustment", "plan_split"],
            ["result_review"],
        ],
        "forbiddenActions": [
            "inventory_coordination", "warehouse_followup", "replenishment_request",
            "creative_replacement", "activity_application",
        ],
        "blockedKeyTokens": ["inventory", "warehouse", "replenish", "restock", "titleimage", "creative", "activityapply"],
        "blockedTextTerms": ["仓储", "仓库", "库存", "补货", "标题替换", "主图替换", "活动报名"],
    },
    "roas_scale": {
        "allowedActionTypes": [
            "plan_audit", "budget_adjustment", "bid_adjustment", "schedule_adjustment",
            "audience_adjustment", "plan_split", "result_review", "rollback",
        ],
        "requiredActionTypeGroups": [
            ["plan_audit"],
            ["budget_adjustment", "bid_adjustment", "schedule_adjustment", "audience_adjustment", "plan_split"],
            ["result_review"],
        ],
        "forbiddenActions": [
            "inventory_coordination", "warehouse_followup", "replenishment_request",
            "creative_replacement", "activity_application",
        ],
        "blockedKeyTokens": ["inventory", "warehouse", "replenish", "restock", "titleimage", "creative", "activityapply"],
        "blockedTextTerms": ["仓储", "仓库", "库存", "补货", "标题替换", "主图替换", "活动报名"],
    },
    "platform_activity": {
        "allowedActionTypes": [
            "eligibility_check", "application_prepare", "application_submit",
            "activity_configuration", "result_review", "rollback",
        ],
        "requiredActionTypeGroups": [
            ["eligibility_check"],
            ["application_prepare", "application_submit", "activity_configuration"],
            ["result_review"],
        ],
        "forbiddenActions": [
            "inventory_coordination", "warehouse_followup", "replenishment_request",
            "creative_replacement", "roas_adjustment",
        ],
        "blockedKeyTokens": ["inventory", "warehouse", "replenish", "restock", "titleimage", "creative", "roas", "budgetadjustment"],
        "blockedTextTerms": ["仓储", "仓库", "库存", "补货", "标题替换", "主图替换", "ROAS调整"],
    },
    "activity_apply": {
        "allowedActionTypes": [
            "eligibility_check", "application_prepare", "application_submit",
            "activity_configuration", "result_review", "rollback",
        ],
        "requiredActionTypeGroups": [
            ["eligibility_check"],
            ["application_prepare", "application_submit", "activity_configuration"],
            ["result_review"],
        ],
        "forbiddenActions": [
            "inventory_coordination", "warehouse_followup", "replenishment_request",
            "creative_replacement", "roas_adjustment",
        ],
        "blockedKeyTokens": ["inventory", "warehouse", "replenish", "restock", "titleimage", "creative", "roas", "budgetadjustment"],
        "blockedTextTerms": ["仓储", "仓库", "库存", "补货", "标题替换", "主图替换", "ROAS调整"],
    },
}

_DEFAULT_POLICY = {
    "allowedActionTypes": ["execution_prepare", "execution_action", "result_review", "rollback"],
    "requiredActionTypeGroups": [["execution_action"], ["result_review"]],
    "forbiddenActions": ["inventory_coordination", "warehouse_followup", "replenishment_request"],
    "blockedKeyTokens": ["inventory", "warehouse", "replenish", "restock", "stockcoordination"],
    "blockedTextTerms": ["仓储", "仓库", "库存", "补货", "在途库存"],
}

_GENERIC_SAFE_KEYS = {
    "permissionbounds", "permissionboundary", "riskboundaries", "validationmetrics",
    "requiredevidence", "executionobjects", "executiontargets", "parameterranges",
    "platformconstraints", "categoryconstraints", "metricdefinitions",
    "selectedactionfamily", "lockedactionfamily", "actionfamily",
}

_BASELINE_ACTION_PATTERNS = (
    re.compile(r"(?:保存|留存|记录|采集|建立).{0,10}(?:基线|快照)"),
    re.compile(r"(?:基线|快照).{0,10}(?:保存|留存|记录|采集|建立)"),
)

_ROLE_ONLY_TOKENS = {
    "designteam", "designer", "copywriter", "operation", "operator", "operationspecialist",
    "dataanalyst", "analyst", "manager", "reviewer", "creative", "creative team",
    "设计团队", "设计师", "文案", "运营", "运营专员", "数据分析师", "审核人", "主管",
}


def family_policy(family: str | None) -> Dict[str, Any]:
    value = str(family or "").strip()
    selected = _FAMILY_POLICIES.get(value, _DEFAULT_POLICY)
    return {
        "version": BASE_AGENT3_SYSTEM_CONSTRAINT_VERSION,
        "lockedActionFamily": value,
        "allowedActionTypes": list(selected["allowedActionTypes"]),
        "requiredActionTypeGroups": [list(group) for group in selected["requiredActionTypeGroups"]],
        "forbiddenActions": list(selected["forbiddenActions"]),
        "blockedKeyTokens": list(selected["blockedKeyTokens"]),
        "blockedTextTerms": list(selected["blockedTextTerms"]),
        "minExecutionSteps": 3,
        "structuredStepRequiredFields": [
            "stepId", "actionFamily", "actionType", "executionObject", "executorRole",
            "instruction", "deadline", "completionCriteria",
        ],
    }


def _contains_blocked_text(value: Any, policy: Dict[str, Any]) -> bool:
    text = _text(value, 5000).lower()
    return any(str(term).lower() in text for term in policy.get("blockedTextTerms") or [])


def _sanitize_action_source(value: Any, policy: Dict[str, Any], *, depth: int = 0) -> Any:
    if depth > 9:
        return None
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        blocked_tokens = [str(item) for item in policy.get("blockedKeyTokens") or []]
        for key, item in value.items():
            normalized = _norm(key)
            if normalized not in _GENERIC_SAFE_KEYS and any(token in normalized for token in blocked_tokens):
                continue
            cleaned = _sanitize_action_source(item, policy, depth=depth + 1)
            if cleaned not in (None, "", [], {}):
                result[str(key)] = cleaned
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            cleaned = _sanitize_action_source(item, policy, depth=depth + 1)
            if cleaned not in (None, "", [], {}):
                result.append(cleaned)
        return result
    if isinstance(value, str):
        if _contains_blocked_text(value, policy):
            return None
        return _text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _text(value)


def _safe_company_context(package: Dict[str, Any], policy: Dict[str, Any]) -> Dict[str, Any]:
    operating = _dict(package.get("companyOperatingPolicySnapshot"))
    rag = _dict(package.get("companySopRagSnapshot"))
    approval = _dict(package.get("approvalPolicySnapshot"))
    brand = _dict(package.get("brandStyleSnapshot"))
    return {
        "managementStyle": _text(operating.get("managementStyle"), 500),
        "taskTimingPolicy": _sanitize_action_source(operating.get("taskTimingPolicy"), policy),
        "companyExecutionPrinciples": _sanitize_action_source(rag.get("companyExecutionPrinciples"), policy),
        "approvedCaseIds": [str(item) for item in _arr(rag.get("approvedCaseIds"))[:12]],
        "positiveExperienceCards": _sanitize_action_source(rag.get("positiveExperienceCards"), policy),
        "negativeCases": _sanitize_action_source(rag.get("negativeCases"), policy),
        "approvalPolicy": _sanitize_action_source(approval, policy),
        "brandStyle": _sanitize_action_source(brand, policy),
    }


def compile_agent3_provider_package(package: Dict[str, Any]) -> Dict[str, Any]:
    package = _dict(package)
    draft = _dict(package.get("agent2ActionDraft"))
    family = _text(
        package.get("lockedActionFamily")
        or draft.get("actionFamily")
        or _dict(package.get("matrixDispatch")).get("selectedActionFamily"),
        100,
    )
    policy = family_policy(family)
    decision = _dict(package.get("agent1DecisionIR"))
    judgment = _dict(package.get("agent1OperatingJudgment"))
    result = {
        "packageId": package.get("packageId") or package.get("itemId"),
        "itemId": package.get("itemId"),
        "dataVersion": package.get("dataVersion"),
        "productId": package.get("productId"),
        "storeId": package.get("storeId"),
        "productTitle": package.get("productTitle") or package.get("title"),
        "productIdentity": _compact(package.get("productIdentity")),
        "lockedActionFamily": family,
        "decisionContext": _sanitize_action_source(
            {
                "decisionType": decision.get("decisionType") or judgment.get("decisionType"),
                "decisionSummary": decision.get("decisionSummary") or judgment.get("decisionSummary"),
                "primaryBusinessSignal": judgment.get("primaryBusinessSignal"),
                "selectedActionFamily": family,
            },
            policy,
        ),
        "actionSources": {
            "agent2ActionDraft": _sanitize_action_source(draft, policy),
            "actionParameterPack": _sanitize_action_source(package.get("actionParameterPack"), policy),
            "recentFiveOrLatestFacts": _sanitize_action_source(package.get("recentFiveOrLatestFacts"), policy),
        },
        "constraints": {
            "lockedActionFamily": family,
            "actionFamilyMutationAllowed": False,
            "crossFamilyActionsForbidden": True,
            "systemFactsCannotBecomeOperatorActions": True,
            "permissionBoundary": _sanitize_action_source(
                draft.get("permissionBoundary")
                or _dict(package.get("actionParameterPack")).get("permissionBounds"),
                policy,
            ),
            "riskBoundaries": _sanitize_action_source(draft.get("riskBoundaries"), policy),
        },
        "systemCompletedFacts": {
            "taskEvidenceFrozenBySystem": True,
            "baselineRetentionHandledBySystem": True,
            "operatorMustNotCreateBaselineRetentionStep": True,
            "operatorMayReadFrozenEvidenceAsReference": True,
        },
        "allowedActionTypes": policy["allowedActionTypes"],
        "forbiddenActions": policy["forbiddenActions"],
        "requiredActionTypeGroups": policy["requiredActionTypeGroups"],
        "outputStepContract": {
            "authoritativeStepCollection": "executionSteps",
            "operatorActionStepsGeneratedBySystem": True,
            "topLevelExecutionObjectGeneratedBySystem": True,
            "minExecutionSteps": policy["minExecutionSteps"],
            "structuredStepRequiredFields": policy["structuredStepRequiredFields"],
            "executionObjectMeansTargetNotResponsibleRole": True,
            "executorRoleMeansResponsibleRole": True,
            "eachStepActionFamilyMustEqualLockedFamily": True,
            "eachActionTypeMustBeAllowed": True,
            "deadlineAndCompletionCriteriaRequired": True,
        },
        "companyContext": _safe_company_context(package, policy),
        "systemConstraintContract": {
            "version": BASE_AGENT3_SYSTEM_CONSTRAINT_VERSION,
            "onlyActionSourcesMayGenerateOperatorActions": True,
            "constraintsMayNotGenerateOperatorActions": True,
            "systemCompletedFactsMayNotGenerateOperatorActions": True,
            "executionStepsAreSingleSourceOfTruth": True,
            "operatorActionStepsAreDeterministicProjection": True,
            "forbiddenActionsFailClosed": True,
            "outputValidationRequired": True,
        },
    }
    return _compact(result)


def _walk_text(value: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_text(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_text(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def _action_surface(sop: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "executionSteps": sop.get("executionSteps"),
        "operatorActionSteps": sop.get("operatorActionSteps"),
        "decisionBranches": sop.get("decisionBranches"),
        "submissionEvidence": sop.get("submissionEvidence"),
        "crossDepartmentActions": sop.get("crossDepartmentActions"),
        "stopConditions": sop.get("stopConditions"),
        "rollbackConditions": sop.get("rollbackConditions"),
    }


def _role_only_execution_object(value: Any) -> bool:
    if isinstance(value, dict):
        return False
    normalized = _norm(value)
    return bool(normalized and normalized in {_norm(item) for item in _ROLE_ONLY_TOKENS})


def validate_agent3_sop_system_contract(
    sop: Dict[str, Any],
    package: Dict[str, Any] | None = None,
) -> List[str]:
    sop = _dict(sop)
    package = _dict(package)
    draft = _dict(package.get("agent2ActionDraft"))
    family = _text(
        package.get("lockedActionFamily")
        or draft.get("actionFamily")
        or sop.get("actionFamily"),
        100,
    )
    policy = family_policy(family)
    missing: List[str] = []
    contaminated_paths: List[str] = []
    baseline_paths: List[str] = []
    blocked_terms = [str(item) for item in policy.get("blockedTextTerms") or []]
    for path, text in _walk_text(_action_surface(sop)):
        lower = text.lower()
        if any(term.lower() in lower for term in blocked_terms):
            contaminated_paths.append(path)
        if any(pattern.search(text) for pattern in _BASELINE_ACTION_PATTERNS):
            baseline_paths.append(path)
    if contaminated_paths:
        missing.append("agent3_sop_cross_family_contamination:" + ",".join(contaminated_paths[:8]))
    if baseline_paths:
        missing.append("agent3_system_fact_converted_to_action:" + ",".join(baseline_paths[:8]))

    structured = [item for item in _arr(sop.get("executionSteps")) if isinstance(item, dict) and item]
    if len(structured) < int(policy.get("minExecutionSteps") or 3):
        missing.append("agent3_sop_execution_steps_min_3")
    required_fields = [str(item) for item in policy.get("structuredStepRequiredFields") or []]
    allowed_types = set(str(item) for item in policy.get("allowedActionTypes") or [])
    seen_types: set[str] = set()
    for index, step in enumerate(structured):
        for field in required_fields:
            if step.get(field) in (None, "", [], {}):
                missing.append(f"agent3_execution_step_{index + 1}_missing:{field}")
        step_family = _text(step.get("actionFamily"), 100)
        if step_family and step_family != family:
            missing.append(f"agent3_execution_step_{index + 1}_family_mismatch")
        action_type = _text(step.get("actionType"), 120)
        if action_type:
            seen_types.add(action_type)
            if allowed_types and action_type not in allowed_types:
                missing.append(f"agent3_execution_step_{index + 1}_action_type_forbidden:{action_type}")
        if _role_only_execution_object(step.get("executionObject")):
            missing.append(f"agent3_execution_step_{index + 1}_execution_object_is_role")
    for group_index, group in enumerate(policy.get("requiredActionTypeGroups") or [], 1):
        if not seen_types.intersection(str(item) for item in group):
            missing.append(f"agent3_required_action_type_group_{group_index}_missing")
    projected = [_text(item) for item in _arr(sop.get("operatorActionSteps")) if _text(item)]
    authoritative = [_text(item.get("instruction")) for item in structured if _text(item.get("instruction"))]
    if projected and projected != authoritative:
        missing.append("agent3_operator_action_steps_projection_mismatch")
    for key in ("submissionEvidence", "rollbackConditions"):
        if sop.get(key) in (None, "", [], {}):
            missing.append(f"agent3_sop_missing:{key}")
    return list(dict.fromkeys(missing))


__all__ = [
    "BASE_AGENT3_SYSTEM_CONSTRAINT_VERSION",
    "family_policy",
    "compile_agent3_provider_package",
    "validate_agent3_sop_system_contract",
]
