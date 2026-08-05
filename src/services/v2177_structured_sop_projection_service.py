"""V21.7.7 structured SOP projection hotfix.

The original SOP compiler converted Agent2 step dictionaries with ``str(dict)``.
That leaked Python payloads into the operator UI, kept pre-authority budget values in
human text, mixed cross-family semantics into ROAS tasks and appended warehouse
coordination to the main operator checklist.

This overlay keeps Agent2's machine-readable steps, rejects new cross-family ROAS
semantics, projects the authorized operation value into the displayed SOP, keeps
coordination separate and upgrades legacy stored snapshots without another Agent
call or any database deletion.
"""

from __future__ import annotations

import ast
import copy
import re
from typing import Any, Dict, Iterable, List, Tuple

STRUCTURED_SOP_PROJECTION_VERSION = "21.7.7"

_ROAS_FAMILIES = {"roas_scale", "roas_guard"}
_CROSS_FAMILY_ROAS = re.compile(
    r"(?:配置|更换|修改|制作|新增|测试|替换|调整).{0,12}"
    r"(?:标题|主图|创意|素材|定向|人群)|"
    r"(?:A\s*/?\s*B|AB)\s*测试|创意方案|标题测试|主图测试",
    re.IGNORECASE,
)
_BUDGET_WORD = re.compile(r"预算|budget", re.IGNORECASE)
_STOP_WORD = re.compile(r"止损|回滚|停止条件|恢复", re.IGNORECASE)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        for key in (
            "budget",
            "value",
            "currentBudget",
            "targetBudget",
            "dailyBudget",
        ):
            parsed = _number(value.get(key))
            if parsed is not None:
                return parsed
        return None
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").replace("¥", "").strip())
    except (TypeError, ValueError):
        return None


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _literal_step(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    text = _text(value)
    if not (text.startswith("{") and text.endswith("}")):
        return {}
    try:
        parsed = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _field(step: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = step.get(key)
        if value not in (None, "", [], {}):
            return _text(value)
    return ""


def _normalize_parameters(step: Dict[str, Any]) -> List[str]:
    result: List[str] = []
    for value in _arr(step.get("parameters")):
        text = _text(value)
        if text and text not in result:
            result.append(text)
    test_variable = _field(step, "testVariable", "variable", "changeVariable")
    locked_variable = _field(step, "lockedVariable", "controlVariable", "fixedVariable")
    if test_variable:
        result.append(f"本次变量：{test_variable}")
    if locked_variable:
        result.append(f"保持不变：{locked_variable}")
    return list(dict.fromkeys(result))


def normalize_step(value: Any, index: int = 0) -> Dict[str, Any]:
    raw = _literal_step(value)
    if not raw:
        text = _text(value)
        return {
            "step": index + 1,
            "title": text,
            "action": text,
            "parameters": [],
            "successCondition": "",
            "rollbackCondition": "",
            "escalationCondition": "",
            "source": "legacy_plain_text",
        }

    action = _field(raw, "action", "title", "summary", "text", "instruction")
    title = _field(raw, "title", "action", "summary", "text", "instruction")
    return {
        "step": int(_number(raw.get("step")) or index + 1),
        "title": title,
        "action": action or title,
        "parameters": _normalize_parameters(raw),
        "successCondition": _field(raw, "successCondition", "completionCondition", "doneCondition"),
        "rollbackCondition": _field(raw, "rollbackCondition", "stopLossCondition", "failureRollback"),
        "escalationCondition": _field(raw, "escalationCondition", "manualEscalationCondition", "handoffCondition"),
        "source": "agent2_structured_step",
    }


def _semantic_text(step: Dict[str, Any]) -> str:
    return "；".join(
        [
            _text(step.get("title")),
            _text(step.get("action")),
            *[_text(value) for value in _arr(step.get("parameters"))],
        ]
    )


def step_allowed_for_family(step: Dict[str, Any], family: str) -> bool:
    if family not in _ROAS_FAMILIES:
        return True
    text = _semantic_text(step)
    if not text:
        return False
    # Merely saying that creative or audience settings stay unchanged is legal.
    if "保持不变" in text and not re.search(r"(?:配置|更换|修改|制作|新增|测试|替换|调整)", text):
        return True
    return _CROSS_FAMILY_ROAS.search(text) is None


def _budget_from_operation(operation: Dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(operation.get(key))
        if value is not None:
            return value
    return None


def _budget_context(
    plan: Dict[str, Any],
    operation_plan: Dict[str, Any],
    authority: Dict[str, Any],
) -> Dict[str, Any]:
    budget_plan = _dict(plan.get("budgetPlan"))
    params = _dict(authority.get("parameters"))
    operation: Dict[str, Any] = {}
    for item in _arr(operation_plan.get("operations")):
        if not isinstance(item, dict):
            continue
        kind = _text(item.get("operationType") or item.get("type") or item.get("action")).lower()
        if kind in {"budget", "budget_change", "budget_adjust", "budget_update"}:
            operation = item
            break

    current = _first(
        _budget_from_operation(operation, "currentValue", "currentBudget", "beforeBudget"),
        _number(params.get("currentBudget")),
        _number(budget_plan.get("currentBudget") or budget_plan.get("beforeBudget")),
    )
    executed = _first(
        _budget_from_operation(
            operation,
            "executedTargetValue",
            "authorizedTargetValue",
            "targetValue",
            "executedBudget",
            "authorizedBudget",
            "targetBudget",
        ),
        _number(params.get("targetBudget")),
        _number(
            budget_plan.get("executedBudget")
            or budget_plan.get("authorizedBudget")
            or budget_plan.get("targetBudget")
        ),
    )
    recommended_values = [
        _budget_from_operation(operation, "recommendedTargetValue", "recommendedBudget"),
        _number(budget_plan.get("recommendedBudget")),
        _number(budget_plan.get("recommendedBudgetUpperBound")),
    ]
    recommended = next((value for value in recommended_values if value is not None), None)
    rollback = _field(operation, "rollbackCondition", "stopLossCondition")
    return {
        "current": current,
        "executed": executed,
        "recommended": recommended,
        "allRecommendations": [value for value in recommended_values if value is not None],
        "rollbackCondition": rollback,
    }


def _money(value: float | None) -> str:
    if value is None:
        return ""
    return f"¥{value:,.2f}"


def _replace_number_variants(text: str, source: float, target: float) -> str:
    replacement = f"{target:,.2f}"
    variants = {
        f"{source:.2f}",
        f"{source:,.2f}",
        str(round(source, 6)),
    }
    result = text
    for variant in sorted(variants, key=len, reverse=True):
        result = result.replace(variant, replacement)
    return result


def _reconcile_budget_step(step: Dict[str, Any], budget: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(step)
    executed = _number(budget.get("executed"))
    current = _number(budget.get("current"))
    recommendations = [
        value
        for value in _arr(budget.get("allRecommendations"))
        if _number(value) is not None
    ]
    for field in ("title", "action", "successCondition", "rollbackCondition", "escalationCondition"):
        text = _text(result.get(field))
        if executed is not None:
            for value in recommendations:
                candidate = _number(value)
                if candidate is None or abs(candidate - executed) < 0.005:
                    continue
                text = _replace_number_variants(text, candidate, executed)
        result[field] = text

    params: List[str] = []
    for value in _arr(result.get("parameters")):
        text = _text(value)
        if executed is not None:
            for candidate_value in recommendations:
                candidate = _number(candidate_value)
                if candidate is not None and abs(candidate - executed) >= 0.005:
                    text = _replace_number_variants(text, candidate, executed)
        if text and text not in params:
            params.append(text)

    budget_related = _BUDGET_WORD.search(_semantic_text(result)) is not None
    if budget_related and executed is not None:
        params = [value for value in params if not re.search(r"(?:新计划|执行|目标).{0,8}预算", value)]
        params.insert(0, f"执行预算：{_money(executed)}")
        if current is not None and abs(current - executed) >= 0.005:
            params.append(f"主计划预算保持：{_money(current)}")
    result["parameters"] = list(dict.fromkeys(params))
    return result


def _budget_step(budget: Dict[str, Any]) -> Dict[str, Any] | None:
    executed = _number(budget.get("executed"))
    current = _number(budget.get("current"))
    if executed is None:
        return None
    params = [f"执行预算：{_money(executed)}"]
    if current is not None and abs(current - executed) >= 0.005:
        params.append(f"主计划预算保持：{_money(current)}")
    return {
        "step": 0,
        "title": "设置本轮授权预算",
        "action": "按动作权限写入独立计划预算，不修改主计划预算。",
        "parameters": params,
        "successCondition": "后台计划预算与授权目标一致。",
        "rollbackCondition": _text(budget.get("rollbackCondition")) or "预算设置错误时恢复至执行前预算。",
        "escalationCondition": "平台限制无法写入预算时提交主管处理。",
        "source": "authorized_operation_projection",
    }


def _stop_step(steps: List[Dict[str, Any]], budget: Dict[str, Any]) -> Dict[str, Any]:
    rollback = _text(budget.get("rollbackCondition"))
    if not rollback:
        rollback = next(
            (_text(step.get("rollbackCondition")) for step in steps if _text(step.get("rollbackCondition"))),
            "触发任务止损条件时恢复至执行前参数。",
        )
    escalation = next(
        (_text(step.get("escalationCondition")) for step in steps if _text(step.get("escalationCondition"))),
        "无法回滚或指标异常扩大时提交主管处理。",
    )
    return {
        "step": 0,
        "title": "设置停止与回滚条件",
        "action": "在执行前确认本轮停止条件和恢复路径。",
        "parameters": [],
        "successCondition": "停止条件、回滚对象和负责人均已确认。",
        "rollbackCondition": rollback,
        "escalationCondition": escalation,
        "source": "operation_boundary_projection",
    }


def _plain_step(step: Dict[str, Any]) -> str:
    title = _text(step.get("title") or step.get("action"))
    parameters = "；".join(_text(value) for value in _arr(step.get("parameters")) if _text(value))
    return f"{title}：{parameters}" if parameters else title


def _coordination(values: Iterable[Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for value in values:
        if isinstance(value, str):
            text = _text(value)
            if text:
                result.append(
                    {
                        "department": "协同部门",
                        "deadline": "本任务时限内",
                        "action": text,
                        "requiredResponse": [],
                        "operatorFollowUp": "",
                    }
                )
            continue
        item = _dict(value)
        action = _field(item, "action", "summary", "text")
        if not action:
            continue
        result.append(
            {
                "department": _field(item, "department", "team") or "协同部门",
                "deadline": _field(item, "deadline", "timeLimit") or "本任务时限内",
                "action": action,
                "requiredResponse": [
                    _text(child)
                    for child in _arr(item.get("requiredResponse"))
                    if _text(child)
                ],
                "operatorFollowUp": _field(item, "operatorFollowUp", "followUp"),
            }
        )
    return result


def project_operator_execution(
    plan: Dict[str, Any],
    *,
    fallback_steps: List[Any] | None = None,
    active_contract: Dict[str, Any] | None = None,
    authority: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    plan = _dict(plan)
    active_contract = _dict(active_contract)
    authority = _dict(authority)
    active_sop = _dict(active_contract.get("activeSopPlan"))
    family = _text(
        plan.get("actionFamily")
        or active_contract.get("activeActionFamily")
    )
    operation_plan = _dict(
        active_contract.get("activeOperationPlan")
        or plan.get("operationPlan")
    )
    source = (
        _arr(plan.get("operatorActionSteps"))
        or _arr(active_sop.get("operatorExecutionSteps"))
        or _arr(active_sop.get("operatorActionSteps"))
        or _arr(fallback_steps)
    )
    normalized = [normalize_step(value, index) for index, value in enumerate(source)]
    valid: List[Dict[str, Any]] = []
    discarded = 0
    for step in normalized:
        if not _text(step.get("title") or step.get("action")):
            continue
        if not step_allowed_for_family(step, family):
            discarded += 1
            continue
        valid.append(step)

    budget = _budget_context(plan, operation_plan, authority)
    valid = [_reconcile_budget_step(step, budget) for step in valid]

    if family in _ROAS_FAMILIES:
        has_budget = any(_BUDGET_WORD.search(_semantic_text(step)) for step in valid)
        explicit_budget = _budget_step(budget)
        if explicit_budget and not has_budget:
            insert_at = 1 if valid else 0
            valid.insert(insert_at, explicit_budget)
        elif explicit_budget and has_budget:
            # Separate the authority value from plan-creation prose when filtering
            # a cross-family step left fewer than four executable steps.
            if len(valid) < 4 and not any(step.get("source") == "authorized_operation_projection" for step in valid):
                insert_at = 1 if valid else 0
                valid.insert(insert_at, explicit_budget)
        if not any(_STOP_WORD.search(_semantic_text(step)) for step in valid):
            valid.insert(max(len(valid) - 1, 0), _stop_step(valid, budget))

    for index, step in enumerate(valid, start=1):
        step["step"] = index

    coordination = _coordination(
        _arr(active_contract.get("supportingCoordination"))
        or _arr(plan.get("crossDepartmentActions"))
    )
    return {
        "version": STRUCTURED_SOP_PROJECTION_VERSION,
        "actionFamily": family,
        "operatorExecutionSteps": valid,
        "operatorExecutionSop": [_plain_step(step) for step in valid],
        "supportingCoordination": coordination,
        "discardedCrossFamilyStepCount": discarded,
        "budgetProjection": budget,
    }


def _update_contract(
    contract: Dict[str, Any],
    projection: Dict[str, Any],
) -> Dict[str, Any]:
    result = copy.deepcopy(_dict(contract))
    active_sop = copy.deepcopy(_dict(result.get("activeSopPlan")))
    active_sop["operatorActionSteps"] = projection["operatorExecutionSop"]
    active_sop["operatorExecutionSteps"] = projection["operatorExecutionSteps"]
    result["activeSopPlan"] = active_sop
    result["supportingCoordination"] = projection["supportingCoordination"]
    result["structuredSopProjectionVersion"] = STRUCTURED_SOP_PROJECTION_VERSION
    return result


def apply_decision_projection(decision: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(_dict(decision))
    plan = _dict(result.get("agent2ActionPlan") or _dict(result.get("taskPlan")).get("agent2ActionPlan"))
    task_plan = copy.deepcopy(_dict(result.get("taskPlan")))
    authority = _dict(result.get("authorizationDecision") or result.get("actionAuthorization") or task_plan.get("authorizationDecision"))
    contract = _dict(result.get("activeActionContract") or task_plan.get("activeActionContract"))
    projection = project_operator_execution(
        plan,
        fallback_steps=_arr(result.get("operatorExecutionSop") or task_plan.get("operatorExecutionSop")),
        active_contract=contract,
        authority=authority,
    )
    contract = _update_contract(contract, projection)

    task_plan["operatorExecutionSteps"] = projection["operatorExecutionSteps"]
    task_plan["operatorExecutionSop"] = projection["operatorExecutionSop"]
    task_plan["sopSteps"] = projection["operatorExecutionSop"]
    task_plan["supportingCoordination"] = projection["supportingCoordination"]
    task_plan["activeActionContract"] = contract
    task_plan["structuredSopProjectionVersion"] = STRUCTURED_SOP_PROJECTION_VERSION

    result["taskPlan"] = task_plan
    result["operatorExecutionSteps"] = projection["operatorExecutionSteps"]
    result["operatorExecutionSop"] = projection["operatorExecutionSop"]
    result["supportingCoordination"] = projection["supportingCoordination"]
    result["activeActionContract"] = contract
    result["discardedCrossFamilyStepCount"] = projection["discardedCrossFamilyStepCount"]
    result["structuredSopProjectionVersion"] = STRUCTURED_SOP_PROJECTION_VERSION
    return result


def _attach_projection(container: Dict[str, Any], projection: Dict[str, Any], contract: Dict[str, Any]) -> None:
    if not isinstance(container, dict):
        return
    container["operatorExecutionSteps"] = projection["operatorExecutionSteps"]
    container["operatorExecutionSop"] = projection["operatorExecutionSop"]
    container["sopSteps"] = projection["operatorExecutionSop"]
    container["supportingCoordination"] = projection["supportingCoordination"]
    container["activeActionContract"] = contract
    container["structuredSopProjectionVersion"] = STRUCTURED_SOP_PROJECTION_VERSION


def project_task_detail_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(_dict(snapshot))
    report = _dict(result.get("taskDetailReport"))
    related = _dict(result.get("relatedTask"))
    task_plan = _dict(report.get("taskPlan") or related.get("taskPlan"))
    plan = _dict(result.get("agent2ActionPlan") or report.get("agent2ActionPlan") or related.get("agent2ActionPlan"))
    contract = _dict(result.get("activeActionContract") or report.get("activeActionContract") or task_plan.get("activeActionContract"))
    authority = _dict(result.get("authorizationDecision") or result.get("actionAuthorization") or contract.get("activeAuthority"))
    projection = project_operator_execution(
        plan,
        fallback_steps=_arr(result.get("operatorExecutionSop") or report.get("operatorExecutionSop") or related.get("operatorExecutionSop")),
        active_contract=contract,
        authority=authority,
    )
    contract = _update_contract(contract, projection)

    _attach_projection(result, projection, contract)
    _attach_projection(report, projection, contract)
    _attach_projection(related, projection, contract)
    _attach_projection(task_plan, projection, contract)
    report["taskPlan"] = task_plan
    related["taskPlan"] = task_plan
    result["taskDetailReport"] = report
    result["relatedTask"] = related
    result["discardedCrossFamilyStepCount"] = projection["discardedCrossFamilyStepCount"]
    return result


def sanitize_agent2_step_semantics(plan: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(_dict(plan))
    family = _text(result.get("actionFamily"))
    raw_steps = _arr(result.get("operatorActionSteps"))
    if family not in _ROAS_FAMILIES or not raw_steps:
        return result
    valid: List[Any] = []
    discarded = 0
    for index, value in enumerate(raw_steps):
        if step_allowed_for_family(normalize_step(value, index), family):
            valid.append(value)
        else:
            discarded += 1
    result["operatorActionSteps"] = valid
    result["discardedCrossFamilyStepCount"] = discarded
    result["structuredSopProjectionVersion"] = STRUCTURED_SOP_PROJECTION_VERSION
    if discarded and len(valid) < 4:
        missing = [
            _text(value)
            for value in _arr(result.get("semanticContractMissing"))
            if _text(value)
        ]
        missing.append("operatorActionSteps.cross_family_semantics")
        result["semanticContractMissing"] = list(dict.fromkeys(missing))
        result["actionPlanStatus"] = "missing_data"
        result["reason"] = "ROAS方案混入创意、标题主图或定向变更，已阻止进入任务池。"
    return result


def install_v2177_structured_sop_projection() -> None:
    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import pipeline_sop_task_pool_v2010_service as sop_worker
    from src.services import sop_builder_core_v20_service as sop_builder
    from src.services import task_detail_snapshot_v2024_service as task_detail

    if getattr(agent2, "_V2177_STRUCTURED_SOP_PROJECTION_INSTALLED", False):
        return

    original_build_messages = agent2._build_messages
    original_normalize_plan = agent2._normalize_plan
    original_sop_builder = sop_builder.build_sop_decision_from_package
    original_build_snapshot = task_detail.build_task_detail_snapshot
    original_read_snapshot = task_detail.read_task_detail_snapshot

    def build_messages_structured(*args: Any, **kwargs: Any) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        messages, payload = original_build_messages(*args, **kwargs)
        family = _text(payload.get("lockedActionFamily"))
        if family in _ROAS_FAMILIES and messages:
            messages[0]["content"] += (
                "ROAS任务的operatorActionSteps只允许预算、出价、目标ROAS、停止和回滚动作；"
                "禁止配置或测试创意、标题、主图、素材、定向和人群。可以明确这些变量保持不变。"
            )
        return messages, payload

    def normalize_plan_structured(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return sanitize_agent2_step_semantics(original_normalize_plan(*args, **kwargs))

    def build_sop_structured(*args: Any, **kwargs: Any) -> Dict[str, Any] | None:
        decision = original_sop_builder(*args, **kwargs)
        return apply_decision_projection(decision) if decision else None

    def build_snapshot_structured(task: Dict[str, Any]) -> Dict[str, Any]:
        return project_task_detail_snapshot(original_build_snapshot(task))

    def read_snapshot_structured(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return project_task_detail_snapshot(original_read_snapshot(*args, **kwargs))

    agent2._build_messages = build_messages_structured
    agent2._normalize_plan = normalize_plan_structured
    sop_builder.build_sop_decision_from_package = build_sop_structured
    sop_worker.build_sop_decision_from_package = build_sop_structured
    task_detail.build_task_detail_snapshot = build_snapshot_structured
    task_detail.read_task_detail_snapshot = read_snapshot_structured

    for module in (agent2, sop_builder, sop_worker, task_detail):
        module.STRUCTURED_SOP_PROJECTION_VERSION = STRUCTURED_SOP_PROJECTION_VERSION
        module._V2177_STRUCTURED_SOP_PROJECTION_INSTALLED = True


__all__ = [
    "STRUCTURED_SOP_PROJECTION_VERSION",
    "apply_decision_projection",
    "install_v2177_structured_sop_projection",
    "normalize_step",
    "project_operator_execution",
    "project_task_detail_snapshot",
    "sanitize_agent2_step_semantics",
    "step_allowed_for_family",
]
