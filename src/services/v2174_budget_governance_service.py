"""V21.7.4 controllable budget-variance governance.

Budget ceilings are execution boundaries, not business-judgment kill switches.
The overlay persists the Agent1 locked family into experimentPolicy, preserves
Agent2's recommendation, caps only the executable value, validates the final
operation plan, and requeues historical budget-ceiling false failures once.
"""

from __future__ import annotations

import copy
import math
from datetime import datetime
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.v2172_inventory_action_guard_service import (
    ISOLATED_MODES,
    ROAS_FAMILIES,
    _arr,
    _dict,
    _lower,
    _number,
    _package_family,
    _payload,
    _policy,
    _ratio,
    _text,
)
from src.services.v2173_agent2_policy_shape_recovery_service import (
    align_experiment_policy_to_locked_family,
    clean_stale_agent2_failure_fields,
)

BUDGET_GOVERNANCE_VERSION = "21.7.4"
_BUDGET_FAILURE_CODE = "budget_change_exceeds_ceiling"
_INSTALLED = False


def _tolerance(ceiling: float | None) -> float:
    return max(1e-9, abs(float(ceiling or 0.0)) * 1e-6)


def _budget_ceiling(policy: Dict[str, Any]) -> float | None:
    value = _ratio(policy.get("budgetChangeCeiling"))
    return None if value is None else max(0.0, min(1.0, abs(value)))


def _operation_kind(operation: Dict[str, Any]) -> str:
    kind = _lower(
        operation.get("operationType")
        or operation.get("type")
        or operation.get("action")
    )
    return {
        "budget": "budget_update",
        "budget_change": "budget_update",
        "budget_adjust": "budget_update",
    }.get(kind, kind)


def _budget_from(value: Any) -> float | None:
    obj = _dict(value)
    return _number(
        obj.get("budget")
        or obj.get("currentBudget")
        or obj.get("targetBudget")
        or obj.get("dailyBudget")
        or obj.get("value")
    )


def _current_budget(operation: Dict[str, Any]) -> float | None:
    return _budget_from(operation.get("currentValue")) or _number(
        operation.get("currentBudget")
        or operation.get("currentDailyBudget")
        or operation.get("beforeBudget")
    )


def _executed_budget(operation: Dict[str, Any]) -> float | None:
    return _budget_from(operation.get("targetValue")) or _number(
        operation.get("targetBudget")
        or operation.get("targetDailyBudget")
        or operation.get("afterBudget")
    )


def _recommended_budget(operation: Dict[str, Any]) -> float | None:
    return (
        _budget_from(operation.get("recommendedTargetValue"))
        or _number(operation.get("recommendedBudget"))
        or _executed_budget(operation)
    )


def _actual_rate(current: float | None, target: float | None) -> float | None:
    if current in {None, 0} or target is None:
        return None
    return abs(float(target) - float(current)) / abs(float(current))


def _exact_values(obj: Any, keys: set[str], parser: Any) -> List[float]:
    values: List[float] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if str(key).lower() in keys:
                parsed = parser(value)
                if parsed is not None:
                    values.append(parsed)
            values.extend(_exact_values(value, keys, parser))
    elif isinstance(obj, list):
        for value in obj[:50]:
            values.extend(_exact_values(value, keys, parser))
    return values


def executable_permission_violations(
    raw: Dict[str, Any],
    policy: Dict[str, Any],
) -> List[str]:
    """Validate executable fields only; never scan broad budgetChange* names."""
    if not policy:
        return ["experiment_policy_missing"]

    violations: List[str] = []
    mode = _lower(raw.get("operationMode"))
    expected_mode = _lower(policy.get("experimentMode"))
    if (
        not bool(policy.get("mainlineMutationAllowed"))
        and expected_mode in ISOLATED_MODES
        and mode not in ISOLATED_MODES
    ):
        violations.append("operation_mode_must_be_isolated_or_test")

    traffic_ceiling = _ratio(policy.get("trafficShareCeiling"))
    if traffic_ceiling is not None:
        traffic_values = _exact_values(
            raw,
            {"trafficshare", "traffic_share", "flowshare", "流量占比"},
            _ratio,
        )
        if any(
            value > traffic_ceiling + _tolerance(traffic_ceiling)
            for value in traffic_values
        ):
            violations.append("traffic_share_exceeds_ceiling")

    budget_ceiling = _budget_ceiling(policy)
    if budget_ceiling is not None:
        operation_plan = _dict(raw.get("operationPlan"))
        operations = [
            item
            for item in _arr(operation_plan.get("operations") or raw.get("operations"))
            if isinstance(item, dict)
        ]
        rates: List[float] = []
        for operation in operations:
            if _operation_kind(operation) != "budget_update":
                continue
            rate = _actual_rate(
                _current_budget(operation),
                _executed_budget(operation),
            )
            if rate is None:
                rate = _ratio(
                    operation.get("executedChangeRate")
                    or operation.get("authorizedChangeRate")
                    or operation.get("changeRate")
                    or operation.get("budgetChangeRate")
                    or operation.get("budgetAdjustmentRate")
                )
            if rate is not None:
                rates.append(abs(rate))

        if not rates:
            budget_plan = _dict(raw.get("budgetPlan"))
            current = _number(
                budget_plan.get("currentBudget")
                or budget_plan.get("currentDailyBudget")
                or budget_plan.get("beforeBudget")
            )
            executed = _number(
                budget_plan.get("executedBudget")
                or budget_plan.get("authorizedBudget")
                or budget_plan.get("targetBudget")
                or budget_plan.get("targetDailyBudget")
                or budget_plan.get("afterBudget")
            )
            rate = _actual_rate(current, executed)
            if rate is not None:
                rates.append(rate)

        if any(rate > budget_ceiling + _tolerance(budget_ceiling) for rate in rates):
            violations.append(_BUDGET_FAILURE_CODE)

    duration_limit = _number(policy.get("durationHours")) or 0.0
    if duration_limit:
        durations = _exact_values(
            raw,
            {"durationhours", "duration_hours", "测试时长小时"},
            _number,
        )
        if any(value > duration_limit + 1e-9 for value in durations):
            violations.append("duration_exceeds_permission")
    return list(dict.fromkeys(violations))


def _stage_plan(current: float, recommended: float, ceiling: float) -> Dict[str, Any] | None:
    rate = _actual_rate(current, recommended)
    if rate is None or rate <= ceiling + _tolerance(ceiling):
        return None
    count = min(10, max(2, int(math.ceil(rate / max(ceiling, 1e-9)))))
    delta = recommended - current
    return {
        "status": "staged_execution",
        "stageCount": count,
        "finalRecommendedBudget": recommended,
        "stages": [
            {
                "stage": index,
                "targetBudget": round(current + delta * index / count, 6),
                "reviewAfterHours": 6 if index == 1 else 12,
                "advanceCondition": "ROI不低于安全线且转化率、消耗速度未显著恶化",
                "rollbackCondition": "ROI跌破安全线或转化率显著恶化",
            }
            for index in range(1, count + 1)
        ],
    }


def govern_budget_operations(
    raw: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    governed = copy.deepcopy(raw)
    package = align_experiment_policy_to_locked_family(package)
    policy = _policy(package)
    ceiling = _budget_ceiling(policy)
    family = _package_family(package)
    if family not in ROAS_FAMILIES or ceiling is None:
        governed["budgetGovernance"] = {
            "version": BUDGET_GOVERNANCE_VERSION,
            "status": "not_applicable",
            "actionFamily": family,
        }
        return governed

    operation_plan = dict(_dict(governed.get("operationPlan")))
    operations = [
        copy.deepcopy(item)
        for item in _arr(operation_plan.get("operations") or governed.get("operations"))
        if isinstance(item, dict)
    ]
    entries: List[Dict[str, Any]] = []

    for index, operation in enumerate(operations):
        if _operation_kind(operation) != "budget_update":
            continue
        current = _current_budget(operation)
        recommended = _recommended_budget(operation)
        recommended_rate = _actual_rate(current, recommended)
        if current in {None, 0} or recommended is None or recommended_rate is None:
            entries.append(
                {
                    "operationIndex": index,
                    "status": "cannot_compute",
                    "currentBudget": current,
                    "recommendedBudget": recommended,
                    "reason": "current_or_target_budget_missing",
                }
            )
            continue

        direction = "increase" if recommended > current else "decrease"
        authorized_rate = min(recommended_rate, ceiling)
        executed = round(
            current * (
                1 + authorized_rate
                if direction == "increase"
                else 1 - authorized_rate
            ),
            6,
        )
        normalized = recommended_rate > ceiling + _tolerance(ceiling)
        status = "normalized_and_passed" if normalized else "passed"
        staged = _stage_plan(current, recommended, ceiling)

        operation.update(
            direction=direction,
            currentValue={**_dict(operation.get("currentValue")), "budget": current},
            targetValue={**_dict(operation.get("targetValue")), "budget": executed},
            recommendedTargetValue={"budget": recommended},
            authorizedTargetValue={"budget": executed},
            executedTargetValue={"budget": executed},
            recommendedChangeRate=recommended_rate,
            authorizedChangeRate=authorized_rate,
            executedChangeRate=authorized_rate,
            changeRate=authorized_rate,
            adjustmentAmount=round(abs(executed - current), 6),
            normalizationStatus=status,
        )
        if staged:
            operation["stagedExecution"] = staged
        entries.append(
            {
                "operationIndex": index,
                "status": status,
                "currentBudget": current,
                "recommendedBudget": recommended,
                "authorizedBudget": executed,
                "executedBudget": executed,
                "recommendedRate": recommended_rate,
                "authorizedRate": authorized_rate,
                "executedRate": authorized_rate,
                "ceiling": ceiling,
                "normalizationReason": (
                    "超过本轮预算权限，已按权限上限执行"
                    if normalized
                    else "建议幅度在本轮权限内"
                ),
                "stagedExecution": staged,
            }
        )

    if operations:
        operation_plan["operations"] = operations
        governed["operationPlan"] = operation_plan

    budget_plan = dict(_dict(governed.get("budgetPlan")))
    if budget_plan:
        current = _number(
            budget_plan.get("currentBudget")
            or budget_plan.get("currentDailyBudget")
            or budget_plan.get("beforeBudget")
        )
        recommended = _number(
            budget_plan.get("recommendedBudget")
            or budget_plan.get("recommendedBudgetUpperBound")
            or budget_plan.get("targetBudget")
            or budget_plan.get("targetDailyBudget")
            or budget_plan.get("afterBudget")
        )
        rate = _actual_rate(current, recommended)
        if current not in {None, 0} and recommended is not None and rate is not None:
            direction = "increase" if recommended > current else "decrease"
            authorized_rate = min(rate, ceiling)
            executed = round(
                current * (
                    1 + authorized_rate
                    if direction == "increase"
                    else 1 - authorized_rate
                ),
                6,
            )
            budget_plan.update(
                recommendedBudget=recommended,
                authorizedBudget=executed,
                executedBudget=executed,
                recommendedRate=rate,
                authorizedRate=authorized_rate,
                executedRate=authorized_rate,
                normalizationStatus=(
                    "normalized_and_passed"
                    if rate > ceiling + _tolerance(ceiling)
                    else "passed"
                ),
            )
            for key in ("targetBudget", "targetDailyBudget", "afterBudget"):
                if key in budget_plan:
                    budget_plan[key] = executed
            governed["budgetPlan"] = budget_plan

    statuses = [entry.get("status") for entry in entries]
    governed["budgetGovernance"] = {
        "version": BUDGET_GOVERNANCE_VERSION,
        "actionFamily": family,
        "policyActionFamily": _lower(policy.get("actionFamily")),
        "budgetChangeCeiling": ceiling,
        "status": (
            "normalized_and_passed"
            if "normalized_and_passed" in statuses
            else "passed"
            if "passed" in statuses
            else "cannot_compute"
            if "cannot_compute" in statuses
            else "no_budget_operation"
        ),
        "entries": entries,
        "rule": "Budget ceilings normalize executable values; only non-computable or authority failures block the chain.",
    }
    return governed


def finalize_governed_plan(plan: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    package = align_experiment_policy_to_locked_family(package)
    governed = govern_budget_operations(plan, package)
    policy = _policy(package)
    violations = executable_permission_violations(governed, policy)
    semantic_missing = [
        _text(value)
        for value in _arr(governed.get("semanticContractMissing"))
        if _text(value)
    ]
    governed.update(
        experimentPolicy=policy,
        experimentPermissionViolations=violations,
        experimentPermissionStatus="passed" if not violations else "rejected",
        experimentPermissionApplied=bool(policy),
        budgetGovernanceVersion=BUDGET_GOVERNANCE_VERSION,
    )
    if not violations and not semantic_missing:
        reason = _text(governed.get("conflictReason") or governed.get("reason"))
        if governed.get("actionPlanStatus") in {
            "conflict_requires_rejudgment",
            "action_plan_missing_data",
        } and (
            not reason
            or "experiment permission" in reason.lower()
            or _BUDGET_FAILURE_CODE in reason
        ):
            governed.update(
                actionPlanStatus="ready",
                taskAdmissionAllowed=True,
                conflictReason=None,
                reason=None,
            )
    return governed


def align_action_pack_policy(
    package: Dict[str, Any],
    pack: Dict[str, Any],
    original: Any,
) -> Dict[str, Any]:
    aligned = align_experiment_policy_to_locked_family(original(package, pack))
    family = _package_family(aligned)
    policy = _policy(aligned)
    aligned["actionFamilyPolicyAlignment"] = {
        "version": BUDGET_GOVERNANCE_VERSION,
        "lockedActionFamily": family,
        "policyActionFamily": _lower(policy.get("actionFamily")),
        "targetObject": policy.get("targetObject"),
        "operationScope": policy.get("operationScope"),
        "persistedBeforeAgent2": True,
    }
    return aligned


def _failure_missing(payload: Dict[str, Any]) -> List[str]:
    plan = _dict(payload.get("agent2ActionPlan") or payload.get("plan"))
    validation = _dict(_dict(plan.get("operationPlan")).get("validation"))
    result: List[str] = []
    for source in (
        _arr(payload.get("missing")),
        _arr(plan.get("semanticContractMissing")),
        _arr(plan.get("experimentPermissionViolations")),
        _arr(validation.get("missing")),
    ):
        for value in source:
            item = _text(value)
            if item and item not in result:
                result.append(item)
    return result


def budget_false_failure(payload: Dict[str, Any]) -> bool:
    missing = _failure_missing(payload)
    allowed = {
        "agent2ActionPlan.actionPlanStatus_ready",
        "agent2ActionPlan.semanticContractMissing_empty",
    }
    return bool(missing) and all(
        _BUDGET_FAILURE_CODE in item or item in allowed
        for item in missing
    )


def recover_budget_false_failures(
    data_version: str | None,
    *,
    limit: int = 50,
) -> Dict[str, Any]:
    if not data_version:
        return {
            "version": BUDGET_GOVERNANCE_VERSION,
            "dataVersion": None,
            "requeuedCount": 0,
        }

    from src.services.agent2_runtime_resilience_v2143_service import ensure_agent2_runtime_columns
    from src.services.pipeline_item_service import build_item_envelope, record_pipeline_item_event

    ensure_agent2_runtime_columns()
    events: List[Tuple[Dict[str, Any], Dict[str, Any], str]] = []
    requeued = 0
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_items WHERE data_version=? AND current_stage='agent2_output_invalid' AND status='failed' ORDER BY updated_at ASC LIMIT ?",
            (data_version, max(1, min(200, int(limit or 50)))),
        ).fetchall()
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(pipeline_items)").fetchall()
        }
        for row in rows:
            outer = loads(row["payload"]) if row["payload"] else {}
            outer = outer if isinstance(outer, dict) else {}
            payload = _payload(outer)
            if _dict(payload.get("budgetGovernanceRecovery")).get("version") == BUDGET_GOVERNANCE_VERSION:
                continue
            if not budget_false_failure(payload):
                continue

            cleaned = align_experiment_policy_to_locked_family(
                clean_stale_agent2_failure_fields(payload)
            )
            cleaned["budgetGovernanceRecovery"] = {
                "version": BUDGET_GOVERNANCE_VERSION,
                "singleReplay": True,
                "previousStage": row["current_stage"],
                "previousMissing": _failure_missing(payload),
                "disposition": "requeue_after_budget_normalization",
            }
            cleaned["fallbackAllowed"] = False
            cleaned["lineage"] = {
                **_dict(cleaned.get("lineage")),
                "currentStage": "action_pack_ready",
                "source": "pipeline_items.payload_budget_governance_recovery",
            }
            if isinstance(outer.get("payload"), dict):
                outer["payload"] = cleaned
                outer["envelope"] = {
                    **_dict(outer.get("envelope")),
                    "stage": "action_pack_ready",
                    "actionFamily": row["action_family"],
                    "route": row["route"],
                }
                stored = outer
            else:
                stored = cleaned

            assignments = [
                "current_stage='action_pack_ready'",
                "status='retry'",
                "retry_count=0",
                "error_reason=NULL",
                "payload=?",
                "updated_at=?",
            ]
            for column in (
                "failure_code",
                "failure_class",
                "claim_id",
                "lease_expires_at",
                "retry_after",
            ):
                if column in columns:
                    assignments.append(f"{column}=NULL")
            conn.execute(
                f"UPDATE pipeline_items SET {','.join(assignments)} WHERE item_id=?",
                (dumps(stored), datetime.now().isoformat(), row["item_id"]),
            )
            output_ref = f"budget_governance_requeue:{data_version}:{row['item_id']}"
            envelope = build_item_envelope(
                data_version=row["data_version"],
                item_id=row["item_id"],
                product_id=row["product_id"],
                store_id=row["store_id"],
                signal_id=row["signal_id"],
                package_id=row["package_id"],
                decision_id=row["decision_id"],
                action_family=row["action_family"],
                route=row["route"],
                output_ref=output_ref,
                stage="action_pack_ready",
            )
            events.append((envelope, cleaned, output_ref))
            requeued += 1
        conn.commit()

    for envelope, payload, output_ref in events:
        record_pipeline_item_event(
            envelope,
            station_id="budget_governance_recovery_station",
            stage="action_pack_ready",
            status="retry",
            output_ref=output_ref,
            payload=payload,
        )
    return {
        "version": BUDGET_GOVERNANCE_VERSION,
        "dataVersion": data_version,
        "requeuedCount": requeued,
        "singleReplay": True,
        "rule": "Only budget-ceiling false failures are replayed; non-computable and authority failures remain blocked.",
    }


def install_v2174_budget_governance() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
    from src.services import agent_runtime_contract_v2010_service as runtime_contract
    from src.services import v216_agent2_experiment_policy_service as v216_policy

    if getattr(agent2, "_V2174_BUDGET_GOVERNANCE_INSTALLED", False):
        _INSTALLED = True
        return

    v216_policy._permission_violations = executable_permission_violations
    original_normalize = agent2._normalize_plan
    original_action_pack = runtime_contract.normalize_action_pack_ready_contract
    original_tick = pipeline_worker.run_agent_pipeline_tick

    def normalize_plan_v2174(
        raw: Dict[str, Any],
        package: Dict[str, Any],
        proof: Dict[str, Any],
    ) -> Dict[str, Any]:
        aligned_package = align_experiment_policy_to_locked_family(package)
        governed_raw = govern_budget_operations(raw, aligned_package)
        plan = original_normalize(governed_raw, aligned_package, proof)
        return finalize_governed_plan(plan, aligned_package)

    def normalize_action_pack_ready_v2174(
        package: Dict[str, Any],
        pack: Dict[str, Any],
    ) -> Dict[str, Any]:
        return align_action_pack_policy(package, pack, original_action_pack)

    def run_pipeline_tick_v2174(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        data_version = (
            kwargs.get("data_version")
            or (args[0] if args else None)
            or pipeline_worker.latest_data_version()
        )
        before = recover_budget_false_failures(data_version)
        result = original_tick(*args, **kwargs)
        after = recover_budget_false_failures(data_version)
        followup = None
        if int(after.get("requeuedCount") or 0) > 0:
            followup = original_tick(*args, **kwargs)
        result["budgetGovernanceVersion"] = BUDGET_GOVERNANCE_VERSION
        result["budgetGovernanceRecoveryBefore"] = before
        result["budgetGovernanceRecoveryAfter"] = after
        if followup is not None:
            result["budgetGovernanceImmediateFollowup"] = {
                key: value
                for key, value in followup.items()
                if key != "result"
            }
        return result

    agent2._normalize_plan = normalize_plan_v2174
    runtime_contract.normalize_action_pack_ready_contract = normalize_action_pack_ready_v2174
    pipeline_worker.normalize_action_pack_ready_contract = normalize_action_pack_ready_v2174
    pipeline_worker.run_agent_pipeline_tick = run_pipeline_tick_v2174

    for module in (agent2, runtime_contract, pipeline_worker, v216_policy):
        module.BUDGET_GOVERNANCE_VERSION = BUDGET_GOVERNANCE_VERSION
        module._V2174_BUDGET_GOVERNANCE_INSTALLED = True
    _INSTALLED = True


__all__ = [
    "BUDGET_GOVERNANCE_VERSION",
    "align_action_pack_policy",
    "budget_false_failure",
    "executable_permission_violations",
    "finalize_governed_plan",
    "govern_budget_operations",
    "install_v2174_budget_governance",
    "recover_budget_false_failures",
]
