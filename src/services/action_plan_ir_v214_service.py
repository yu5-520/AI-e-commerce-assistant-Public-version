"""V21.4.0 operation-level action Plan IR.

The model decides concrete operations. This module normalizes them without
using a generic percentage as a budget percentage and without guessing
increase/decrease from an action-family name. V21.7.4 additionally preserves
budget recommendation/authorization/execution metadata through normalization.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, Iterable, List

ACTION_PLAN_IR_VERSION = "21.4.0"
OPERATION_PLAN_SCHEMA = "operation_plan_ir.v1"
ROAS_FAMILIES = {"roas_scale", "roas_guard"}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _float(value: Any) -> float | None:
    if value in {None, "", "—", "未识别", "UNKNOWN", "未提供"}:
        return None
    try:
        return float(
            str(value)
            .replace("¥", "")
            .replace("￥", "")
            .replace(",", "")
            .replace("元", "")
            .replace("%", "")
            .strip()
        )
    except (TypeError, ValueError):
        return None


def _first_number(
    sources: Iterable[Dict[str, Any]],
    keys: Iterable[str],
) -> float | None:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = _float(source.get(key))
            if value is not None:
                return value
    return None


def _rate(value: Any) -> float | None:
    value = _float(value)
    if value is None:
        return None
    return value / 100 if abs(value) > 2 else value


def _direction(
    value: Any,
    current: float | None = None,
    target: float | None = None,
) -> str | None:
    raw = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "up": "increase",
        "increase": "increase",
        "raise": "increase",
        "scale_up": "increase",
        "放量": "increase",
        "提高": "increase",
        "上调": "increase",
        "down": "decrease",
        "decrease": "decrease",
        "reduce": "decrease",
        "scale_down": "decrease",
        "收缩": "decrease",
        "降低": "decrease",
        "下调": "decrease",
        "set": "set",
        "keep": "keep",
        "pause": "pause",
        "stop": "pause",
    }
    if raw in aliases:
        return aliases[raw]
    if current is not None and target is not None:
        return (
            "increase"
            if target > current
            else "decrease"
            if target < current
            else "keep"
        )
    return None


def _target(
    plan: Dict[str, Any],
    operation: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    operation = operation or {}
    explicit = _dict(operation.get("target"))
    execution = _dict(operation.get("executionObject")) or _dict(
        plan.get("executionObject")
    )
    return {
        "type": explicit.get("type")
        or explicit.get("targetType")
        or execution.get("targetType")
        or operation.get("targetType")
        or "ad_plan",
        "id": explicit.get("id")
        or explicit.get("targetId")
        or execution.get("targetId")
        or operation.get("targetId"),
        "name": explicit.get("name")
        or explicit.get("targetName")
        or execution.get("targetName")
        or operation.get("targetName"),
        "selector": explicit.get("selector")
        or explicit.get("targetSelector")
        or execution.get("targetSelector")
        or operation.get("targetSelector"),
    }


def _op_id(kind: str, target: Dict[str, Any], index: int) -> str:
    raw = "|".join(
        str(value or "")
        for value in (
            kind,
            target.get("type"),
            target.get("id"),
            target.get("selector"),
            index,
        )
    )
    return "OP-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()


def _normalize_explicit(
    raw: Dict[str, Any],
    plan: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    kind = str(
        raw.get("operationType")
        or raw.get("type")
        or raw.get("action")
        or ""
    ).strip().lower()
    kind = {
        "budget": "budget_update",
        "budget_change": "budget_update",
        "budget_adjust": "budget_update",
        "bid": "bid_update",
        "bid_change": "bid_update",
        "roas": "target_roas_update",
        "target_roas": "target_roas_update",
        "pause": "stop_rule_update",
        "stop": "stop_rule_update",
    }.get(kind, kind)
    target = _target(plan, raw)
    current_value = _dict(raw.get("currentValue"))
    target_value = _dict(raw.get("targetValue"))
    result: Dict[str, Any] = {
        "operationId": raw.get("operationId") or _op_id(kind, target, index),
        "operationType": kind,
        "target": target,
        "direction": _direction(raw.get("direction")),
        "currentValue": current_value,
        "targetValue": target_value,
        "changeRate": _rate(raw.get("changeRate")),
        "adjustmentAmount": _float(raw.get("adjustmentAmount")),
        "metric": raw.get("metric"),
        "operator": raw.get("operator"),
        "threshold": _float(raw.get("threshold")),
        "condition": raw.get("condition"),
        "action": raw.get("action"),
        "controlVariables": [
            str(value).strip()
            for value in _arr(raw.get("controlVariables"))
            if str(value).strip()
        ],
        "rollback": _dict(raw.get("rollback")),
        "recommendedTargetValue": _dict(raw.get("recommendedTargetValue")),
        "authorizedTargetValue": _dict(raw.get("authorizedTargetValue")),
        "executedTargetValue": _dict(raw.get("executedTargetValue")),
        "recommendedChangeRate": _rate(raw.get("recommendedChangeRate")),
        "authorizedChangeRate": _rate(raw.get("authorizedChangeRate")),
        "executedChangeRate": _rate(raw.get("executedChangeRate")),
        "normalizationStatus": raw.get("normalizationStatus"),
        "stagedExecution": _dict(raw.get("stagedExecution")),
        "source": "agent2_explicit_operation",
    }

    if kind == "budget_update":
        current = _first_number(
            [raw, current_value],
            [
                "currentBudget",
                "currentDailyBudget",
                "beforeBudget",
                "budget",
                "amount",
                "value",
            ],
        )
        target_budget = _first_number(
            [raw, target_value],
            [
                "targetBudget",
                "targetDailyBudget",
                "afterBudget",
                "budget",
                "amount",
                "value",
            ],
        )
        budget_rate = _rate(
            raw.get("budgetChangeRate")
            or raw.get("budgetAdjustmentRate")
            or raw.get("executedChangeRate")
            or raw.get("authorizedChangeRate")
            or raw.get("changeRate")
        )
        direction = _direction(raw.get("direction"), current, target_budget)
        if (
            target_budget is None
            and current is not None
            and budget_rate is not None
            and direction in {"increase", "decrease"}
        ):
            target_budget = max(
                0.0,
                current
                * (
                    1
                    + (
                        1
                        if direction == "increase"
                        else -1
                    )
                    * abs(budget_rate)
                ),
            )
        amount = _float(raw.get("adjustmentAmount"))
        if amount is None and current is not None and target_budget is not None:
            amount = abs(target_budget - current)
        result.update(
            direction=direction,
            currentValue={"budget": current}
            if current is not None
            else current_value,
            targetValue={"budget": target_budget}
            if target_budget is not None
            else target_value,
            changeRate=abs(budget_rate) if budget_rate is not None else None,
            adjustmentAmount=abs(amount) if amount is not None else None,
        )

    elif kind == "bid_update":
        current = _first_number(
            [raw, current_value],
            ["currentBid", "beforeBid", "bid", "value"],
        )
        target_bid = _first_number(
            [raw, target_value],
            ["targetBid", "afterBid", "bid", "value"],
        )
        bid_rate = _rate(
            raw.get("bidChangeRate")
            or raw.get("bidAdjustmentRate")
            or raw.get("changeRate")
        )
        direction = _direction(raw.get("direction"), current, target_bid)
        if (
            target_bid is None
            and current is not None
            and bid_rate is not None
            and direction in {"increase", "decrease"}
        ):
            target_bid = max(
                0.0,
                current
                * (
                    1
                    + (
                        1
                        if direction == "increase"
                        else -1
                    )
                    * abs(bid_rate)
                ),
            )
        result.update(
            direction=direction,
            currentValue={"bid": current}
            if current is not None
            else current_value,
            targetValue={"bid": target_bid}
            if target_bid is not None
            else target_value,
            changeRate=abs(bid_rate) if bid_rate is not None else None,
        )

    elif kind == "target_roas_update":
        current = _first_number(
            [raw, current_value],
            [
                "currentTargetRoas",
                "currentTargetROAS",
                "currentRoas",
                "currentROAS",
                "value",
            ],
        )
        target_roas = _first_number(
            [raw, target_value],
            ["targetRoas", "targetROAS", "targetRoi", "targetROI", "value"],
        )
        result.update(
            direction=_direction(raw.get("direction"), current, target_roas),
            currentValue={"roas": current}
            if current is not None
            else current_value,
            targetValue={"roas": target_roas}
            if target_roas is not None
            else target_value,
        )

    return {
        key: value
        for key, value in result.items()
        if value not in [None, "", [], {}]
    }


def _derive_operations(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    budget = _dict(plan.get("budgetPlan"))
    execution = _dict(plan.get("executionParameters"))
    obj = _dict(plan.get("executionObject"))
    current_obj = _dict(obj.get("currentValue"))
    target_obj = _dict(obj.get("targetValue"))
    sources = [budget, execution, current_obj, target_obj]
    target = _target(plan)
    operations: List[Dict[str, Any]] = []

    current_budget = _first_number(
        sources,
        [
            "currentDailyBudget",
            "currentBudget",
            "beforeBudget",
            "dailyBudget",
            "currentAdSpend",
        ],
    )
    target_budget = _first_number(
        sources,
        [
            "targetDailyBudget",
            "targetBudget",
            "afterBudget",
            "authorizedBudget",
            "executedBudget",
            "recommendedBudgetUpperBound",
        ],
    )
    direction = _direction(
        budget.get("budgetDirection") or budget.get("direction"),
        current_budget,
        target_budget,
    )
    budget_rate = _rate(
        budget.get("executedRate")
        or budget.get("authorizedRate")
        or budget.get("budgetAdjustmentRate")
        or budget.get("recommendedBudgetIncreaseRate")
        or budget.get("recommendedBudgetDecreaseRate")
    )
    if (
        target_budget is None
        and current_budget is not None
        and budget_rate is not None
        and direction in {"increase", "decrease"}
    ):
        target_budget = max(
            0.0,
            current_budget
            * (
                1
                + (
                    1
                    if direction == "increase"
                    else -1
                )
                * abs(budget_rate)
            ),
        )
    if current_budget is not None or target_budget is not None:
        amount = (
            abs(target_budget - current_budget)
            if current_budget is not None and target_budget is not None
            else None
        )
        operations.append(
            {
                "operationId": _op_id("budget_update", target, len(operations)),
                "operationType": "budget_update",
                "target": target,
                "direction": direction,
                "currentValue": {"budget": current_budget}
                if current_budget is not None
                else {},
                "targetValue": {"budget": target_budget}
                if target_budget is not None
                else {},
                "recommendedTargetValue": {"budget": _float(budget.get("recommendedBudget"))}
                if _float(budget.get("recommendedBudget")) is not None
                else {},
                "authorizedTargetValue": {"budget": _float(budget.get("authorizedBudget"))}
                if _float(budget.get("authorizedBudget")) is not None
                else {},
                "executedTargetValue": {"budget": _float(budget.get("executedBudget"))}
                if _float(budget.get("executedBudget")) is not None
                else {},
                "recommendedChangeRate": _rate(budget.get("recommendedRate")),
                "authorizedChangeRate": _rate(budget.get("authorizedRate")),
                "executedChangeRate": _rate(budget.get("executedRate")),
                "normalizationStatus": budget.get("normalizationStatus"),
                "changeRate": abs(budget_rate)
                if budget_rate is not None
                else None,
                "adjustmentAmount": amount,
                "source": "agent2_scoped_budget_plan",
            }
        )

    bid_rate = _rate(
        execution.get("bidChangeRate")
        or execution.get("bidAdjustmentRate")
    )
    if bid_rate is not None:
        operations.append(
            {
                "operationId": _op_id("bid_update", target, len(operations)),
                "operationType": "bid_update",
                "target": target,
                "direction": _direction(
                    execution.get("bidDirection")
                    or execution.get("direction")
                ),
                "changeRate": abs(bid_rate),
                "source": "agent2_scoped_bid_parameter",
            }
        )

    current_roas = _first_number(
        sources,
        [
            "currentTargetRoas",
            "currentTargetROAS",
            "currentROI",
            "currentRoas",
            "currentROAS",
        ],
    )
    target_roas = _first_number(
        sources,
        ["targetRoas", "targetROAS", "targetRoi", "targetROI"],
    )
    if target_roas is not None:
        operations.append(
            {
                "operationId": _op_id("target_roas_update", target, len(operations)),
                "operationType": "target_roas_update",
                "target": target,
                "direction": _direction(None, current_roas, target_roas),
                "currentValue": {"roas": current_roas}
                if current_roas is not None
                else {},
                "targetValue": {"roas": target_roas},
                "source": "agent2_roas_plan",
            }
        )

    stop = _first_number(
        sources,
        [
            "stopLossROI",
            "stopLossROAS",
            "safetyROI",
            "minimumSafeROAS",
            "minimumSafeRoas",
        ],
    )
    condition = str(
        budget.get("stopLossCondition")
        or execution.get("rollbackCondition")
        or ""
    ).strip()
    if stop is not None or condition:
        operations.append(
            {
                "operationId": _op_id("stop_rule_update", target, len(operations)),
                "operationType": "stop_rule_update",
                "target": target,
                "direction": "set",
                "metric": "roi",
                "operator": "<",
                "threshold": stop,
                "condition": condition,
                "action": "pause_or_rollback",
                "source": "agent2_stop_loss_plan",
            }
        )

    return [
        {
            key: value
            for key, value in operation.items()
            if value not in [None, "", [], {}]
        }
        for operation in operations
    ]


def _validate(operation: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    kind = str(operation.get("operationType") or "")
    target = _dict(operation.get("target"))
    if not kind:
        missing.append("operationType")
    if not (target.get("id") or target.get("selector")):
        missing.append("target.id_or_selector")
    if kind in {"budget_update", "bid_update", "target_roas_update"} and not operation.get("direction"):
        missing.append("direction")
    if kind == "budget_update":
        if _float(_dict(operation.get("currentValue")).get("budget")) is None:
            missing.append("currentValue.budget")
        if _float(_dict(operation.get("targetValue")).get("budget")) is None:
            missing.append("targetValue.budget")
        if _float(operation.get("adjustmentAmount")) is None:
            missing.append("adjustmentAmount")
    if (
        kind == "bid_update"
        and _float(_dict(operation.get("currentValue")).get("bid")) is None
        and _float(_dict(operation.get("targetValue")).get("bid")) is None
        and _float(operation.get("changeRate")) is None
    ):
        missing.append("bid_value_or_changeRate")
    if (
        kind == "target_roas_update"
        and _float(_dict(operation.get("targetValue")).get("roas")) is None
    ):
        missing.append("targetValue.roas")
    if (
        kind == "stop_rule_update"
        and _float(operation.get("threshold")) is None
        and not str(operation.get("condition") or "").strip()
    ):
        missing.append("threshold_or_condition")
    return missing


def normalize_action_plan_ir(
    plan: Dict[str, Any],
    action_family: str | None = None,
) -> Dict[str, Any]:
    plan = dict(_dict(plan))
    family = str(action_family or plan.get("actionFamily") or "").strip()
    raw_plan = _dict(plan.get("operationPlan"))
    raw_ops = _arr(raw_plan.get("operations")) or _arr(plan.get("operations"))
    operations = [
        _normalize_explicit(raw, plan, index)
        for index, raw in enumerate(raw_ops)
        if isinstance(raw, dict)
    ] or (_derive_operations(plan) if family in ROAS_FAMILIES else [])
    missing: List[str] = []
    for index, operation in enumerate(operations):
        missing.extend(
            f"operations[{index}].{field}"
            for field in _validate(operation)
        )
    if family in ROAS_FAMILIES and not operations:
        missing.append("operations_min_1")
    return {
        "version": ACTION_PLAN_IR_VERSION,
        "schema": OPERATION_PLAN_SCHEMA,
        "actionFamily": family,
        "objective": raw_plan.get("objective")
        or plan.get("objective")
        or plan.get("operatingConclusion")
        or plan.get("selectedDirection"),
        "operations": operations,
        "source": "agent2_explicit_operation_plan"
        if raw_ops
        else "v21_4_compatibility_projection_from_scoped_agent2_fields",
        "validation": {
            "passed": not missing,
            "missing": list(dict.fromkeys(missing)),
            "genericAdjustmentRateUsedAsBudget": False,
            "familyNameUsedAsDirection": False,
        },
    }


def missing_action_plan_ir(
    plan: Dict[str, Any],
    action_family: str | None = None,
) -> List[str]:
    operation_plan = _dict(plan.get("operationPlan")) or normalize_action_plan_ir(
        plan,
        action_family,
    )
    missing = [
        str(value)
        for value in _arr(_dict(operation_plan.get("validation")).get("missing"))
        if str(value).strip()
    ]
    family = str(
        action_family
        or plan.get("actionFamily")
        or operation_plan.get("actionFamily")
        or ""
    )
    if family in ROAS_FAMILIES and not _arr(operation_plan.get("operations")):
        missing.append("operationPlan.operations_min_1")
    return list(dict.fromkeys(missing))


def authority_parameters_from_plan_ir(
    plan: Dict[str, Any],
    action_family: str | None = None,
) -> Dict[str, Any]:
    operation_plan = _dict(plan.get("operationPlan")) or normalize_action_plan_ir(
        plan,
        action_family,
    )
    operations = [
        value
        for value in _arr(operation_plan.get("operations"))
        if isinstance(value, dict)
    ]
    budgets = [
        value
        for value in operations
        if value.get("operationType") == "budget_update"
    ]
    bids = [
        value
        for value in operations
        if value.get("operationType") == "bid_update"
    ]
    roas = [
        value
        for value in operations
        if value.get("operationType") == "target_roas_update"
    ]
    stops = [
        value
        for value in operations
        if value.get("operationType") == "stop_rule_update"
    ]
    amount = sum(
        abs(_float(value.get("adjustmentAmount")) or 0.0)
        for value in budgets
    )
    current_budget = (
        sum(
            _float(_dict(value.get("currentValue")).get("budget")) or 0.0
            for value in budgets
        )
        if budgets
        else None
    )
    target_budget = (
        sum(
            _float(_dict(value.get("targetValue")).get("budget")) or 0.0
            for value in budgets
        )
        if budgets
        else None
    )
    current_roas = (
        _float(_dict(roas[0].get("currentValue")).get("roas"))
        if roas
        else None
    )
    target_roas = (
        _float(_dict(roas[0].get("targetValue")).get("roas"))
        if roas
        else None
    )
    roas_rate = (
        abs(target_roas - current_roas) / abs(current_roas)
        if current_roas not in {None, 0} and target_roas is not None
        else None
    )
    safety_values = [_float(value.get("threshold")) for value in stops]
    safety_values = [value for value in safety_values if value is not None]
    return {
        "operationPlanVersion": operation_plan.get("version")
        or ACTION_PLAN_IR_VERSION,
        "operationPlanSchema": operation_plan.get("schema")
        or OPERATION_PLAN_SCHEMA,
        "operationCount": len(operations),
        "operationTypes": [
            str(value.get("operationType"))
            for value in operations
            if value.get("operationType")
        ],
        "directions": sorted(
            {
                str(value.get("direction"))
                for value in operations
                if value.get("direction")
            }
        ),
        "currentBudget": current_budget,
        "targetBudget": target_budget,
        "adjustmentAmount": amount,
        "budgetOperationCount": len(budgets),
        "bidChangeRate": (
            max(
                [
                    abs(_float(value.get("changeRate")) or 0.0)
                    for value in bids
                ]
                or [0.0]
            )
            if bids
            else None
        ),
        "currentTargetRoas": current_roas,
        "targetRoas": target_roas,
        "safetyRoas": max(safety_values) if safety_values else None,
        "roasChangeRate": roas_rate,
        "validationPassed": _dict(operation_plan.get("validation")).get("passed")
        is True,
        "missing": _arr(_dict(operation_plan.get("validation")).get("missing")),
        "genericAdjustmentRateUsedAsBudget": False,
        "familyNameUsedAsDirection": False,
    }
