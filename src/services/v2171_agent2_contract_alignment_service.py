"""V21.7.1 Agent2 prompt, target, Plan IR and failure-contract alignment.

This is the final adapter over the existing V21.6 experiment policy. It does not
replace business judgment. It only makes provider output satisfy the same
machine-addressable contract that the validator already enforces, collapses
repeated derivative errors to root causes, and replays historical alignment-only
failures once.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, dumps

AGENT2_CONTRACT_ALIGNMENT_VERSION = "21.7.1"

_DERIVATIVE_ERRORS = {
    "agent2ActionPlan.actionPlanStatus_ready",
    "agent2ActionPlan.semanticContractMissing_empty",
}
_RECOVERABLE_ROOT_ERRORS = {
    "executionObject.targetId_or_targetSelector",
    "inventory_cannot_directly_cut_operator_traffic",
    "operatorActionSteps_min_4",
    "executionSteps_min_3",
    "decisionBranches_min_2",
    "submissionEvidence_min_2",
    "creativeTestPlan.groups_min_2",
}
_REBUILD_FIELDS = {
    "agent2ActionPlan", "agent2Provider", "agent2Source",
    "agent2ExecutionProof", "actionPlanSource", "actionPlanStatus",
    "plan", "operationPlan", "sopDecision", "taskAdmission",
    "decisionId", "taskId", "reason", "blockedReason", "missing",
    "failureOwner", "frontendFailureLabel", "taskAdmissionAllowed",
    "agent2RetryPolicy",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
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


def _first_number(obj: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(obj, dict):
        for key in keys:
            value = _number(obj.get(key))
            if value is not None:
                return value
        for child in obj.values():
            value = _first_number(child, keys)
            if value is not None:
                return value
    elif isinstance(obj, list):
        for child in obj[:30]:
            value = _first_number(child, keys)
            if value is not None:
                return value
    return None


def _first_text(obj: Dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = str(obj.get(key) or "").strip()
        if value:
            return value
    return None


def _execution_selector(raw: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    execution = dict(_dict(raw.get("executionObject")))
    if execution.get("targetId") or execution.get("targetSelector"):
        return execution

    target_id = _first_text(
        execution,
        ("objectId", "planId", "linkId", "adPlanId", "skuId", "id"),
    )
    if target_id:
        execution["targetId"] = target_id
        return execution

    selector = _first_text(
        execution,
        ("selector", "targetName", "name", "criteria", "scope", "description"),
    )
    if selector:
        execution["targetSelector"] = selector
        return execution

    cross = _dict(package.get("crossValidation"))
    policy = _dict(package.get("experimentPolicy")) or _dict(cross.get("experimentPolicy"))
    target_object = str(policy.get("targetObject") or "operating_target").strip()
    product_id = str(raw.get("productId") or package.get("productId") or "").strip()
    family = str(raw.get("actionFamily") or package.get("actionFamily") or "").strip()
    parts = [value for value in (target_object, product_id, family) if value]
    if parts:
        execution["targetSelector"] = ":".join(parts)
    return execution


def _operation_kind(operation: Dict[str, Any]) -> str:
    kind = str(
        operation.get("operationType")
        or operation.get("type")
        or operation.get("action")
        or ""
    ).strip().lower()
    return {
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


def _operation_target(
    operation: Dict[str, Any],
    execution: Dict[str, Any],
) -> Dict[str, Any]:
    target = dict(_dict(operation.get("target")))
    target.setdefault(
        "id",
        operation.get("targetId")
        or target.get("targetId")
        or execution.get("targetId"),
    )
    target.setdefault(
        "selector",
        operation.get("targetSelector")
        or target.get("targetSelector")
        or execution.get("targetSelector"),
    )
    target.setdefault(
        "name",
        operation.get("targetName")
        or target.get("targetName")
        or execution.get("targetName")
        or execution.get("name"),
    )
    target.setdefault(
        "type",
        operation.get("targetType")
        or target.get("targetType")
        or execution.get("targetType")
        or "ad_plan",
    )
    return {key: value for key, value in target.items() if value not in {None, ""}}


def _canonical_operation(
    raw_operation: Dict[str, Any],
    execution: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    operation = copy.deepcopy(raw_operation)
    kind = _operation_kind(operation)
    operation["operationType"] = kind
    operation["target"] = _operation_target(operation, execution)
    current = dict(_dict(operation.get("currentValue")))
    target = dict(_dict(operation.get("targetValue")))
    action_data = _dict(package.get("actionParameterPack")) or _dict(package.get("actionDataPack"))

    if kind == "budget_update":
        current_budget = (
            _first_number(operation, ("currentBudget", "currentDailyBudget", "beforeBudget"))
            if _number(current.get("budget")) is None
            else _number(current.get("budget"))
        )
        target_budget = (
            _first_number(operation, ("targetBudget", "targetDailyBudget", "afterBudget"))
            if _number(target.get("budget")) is None
            else _number(target.get("budget"))
        )
        current_budget = current_budget if current_budget is not None else _first_number(
            action_data, ("currentBudget", "currentDailyBudget", "beforeBudget")
        )
        target_budget = target_budget if target_budget is not None else _first_number(
            action_data,
            ("targetBudget", "targetDailyBudget", "afterBudget", "recommendedBudgetUpperBound"),
        )
        if current_budget is not None:
            current["budget"] = current_budget
        if target_budget is not None:
            target["budget"] = target_budget
        if operation.get("adjustmentAmount") in {None, ""} and current_budget is not None and target_budget is not None:
            operation["adjustmentAmount"] = abs(target_budget - current_budget)

    elif kind == "bid_update":
        current_bid = _number(current.get("bid"))
        target_bid = _number(target.get("bid"))
        current_bid = current_bid if current_bid is not None else _first_number(
            operation, ("currentBid", "beforeBid")
        )
        target_bid = target_bid if target_bid is not None else _first_number(
            operation, ("targetBid", "afterBid")
        )
        current_bid = current_bid if current_bid is not None else _first_number(
            action_data, ("currentBid", "beforeBid")
        )
        target_bid = target_bid if target_bid is not None else _first_number(
            action_data, ("targetBid", "afterBid")
        )
        if current_bid is not None:
            current["bid"] = current_bid
        if target_bid is not None:
            target["bid"] = target_bid
        if operation.get("changeRate") in {None, ""}:
            operation["changeRate"] = operation.get("bidChangeRate") or operation.get("bidAdjustmentRate")

    elif kind == "target_roas_update":
        current_roas = _number(current.get("roas"))
        target_roas = _number(target.get("roas"))
        current_roas = current_roas if current_roas is not None else _first_number(
            operation,
            ("currentTargetRoas", "currentTargetROAS", "currentRoas", "currentROAS"),
        )
        target_roas = target_roas if target_roas is not None else _first_number(
            operation, ("targetRoas", "targetROAS", "targetRoi", "targetROI")
        )
        current_roas = current_roas if current_roas is not None else _first_number(
            action_data,
            ("currentTargetRoas", "currentTargetROAS", "currentRoas", "currentROAS", "currentROI"),
        )
        target_roas = target_roas if target_roas is not None else _first_number(
            action_data,
            ("targetRoas", "targetROAS", "targetRoi", "targetROI", "minimumSafeROAS", "safetyROI"),
        )
        if current_roas is not None:
            current["roas"] = current_roas
        if target_roas is not None:
            target["roas"] = target_roas

    elif kind == "stop_rule_update":
        if operation.get("threshold") in {None, ""}:
            operation["threshold"] = _first_number(
                action_data,
                ("stopLossROI", "stopLossROAS", "safetyROI", "minimumSafeROAS", "minimumSafeRoas"),
            )
        if not str(operation.get("condition") or "").strip():
            condition = operation.get("thresholdCondition") or operation.get("stopLossCondition")
            if condition:
                operation["condition"] = condition

    if current:
        operation["currentValue"] = current
    if target:
        operation["targetValue"] = target
    return operation


def canonicalize_agent2_raw(
    raw: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    aligned = copy.deepcopy(raw)
    execution = _execution_selector(aligned, package)
    aligned["executionObject"] = execution
    operation_plan = dict(_dict(aligned.get("operationPlan")))
    operations = [
        _canonical_operation(item, execution, package)
        for item in _arr(operation_plan.get("operations") or aligned.get("operations"))
        if isinstance(item, dict)
    ]
    if operations:
        operation_plan["operations"] = operations
        aligned["operationPlan"] = operation_plan
    aligned["agent2ContractAlignmentVersion"] = AGENT2_CONTRACT_ALIGNMENT_VERSION
    return aligned


def canonical_missing_fields(
    raw_missing: List[str],
    plan: Dict[str, Any],
) -> List[str]:
    semantic = [
        str(value).strip()
        for value in _arr(plan.get("semanticContractMissing"))
        if str(value).strip()
    ]
    violations = [
        str(value).strip()
        for value in _arr(plan.get("experimentPermissionViolations"))
        if str(value).strip()
    ]
    result: List[str] = []
    for value in raw_missing:
        item = str(value or "").strip()
        if not item:
            continue
        if semantic and item in _DERIVATIVE_ERRORS:
            continue
        if semantic and item.startswith("agent2ActionPlan.operationPlan."):
            if item[len("agent2ActionPlan.operationPlan."):] in semantic:
                continue
        if violations and item == "agent2ActionPlan.actionPlanStatus_ready":
            continue
        if item not in result:
            result.append(item)
    for item in semantic:
        if item not in result:
            result.append(item)
    for violation in violations:
        item = f"agent2ActionPlan.experimentPermission.{violation}"
        if item not in result:
            result.append(item)
    return result


def _failure_missing(payload: Dict[str, Any]) -> List[str]:
    plan = _dict(payload.get("agent2ActionPlan") or payload.get("plan"))
    result: List[str] = []
    for value in _arr(payload.get("missing")) + _arr(plan.get("semanticContractMissing")):
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result


def alignment_only_failure(payload: Dict[str, Any]) -> bool:
    plan = _dict(payload.get("agent2ActionPlan") or payload.get("plan"))
    if _arr(plan.get("experimentPermissionViolations")):
        return False
    if _dict(payload.get("agent2ContractRecovery")).get("version") == AGENT2_CONTRACT_ALIGNMENT_VERSION:
        return False
    missing = _failure_missing(payload)
    if not missing:
        return False
    for item in missing:
        if item in _DERIVATIVE_ERRORS or item in _RECOVERABLE_ROOT_ERRORS:
            continue
        if item.startswith("operations["):
            continue
        if item.startswith("agent2ActionPlan.operationPlan.operations["):
            continue
        return False
    return True


def clean_agent2_failure_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = {key: value for key, value in payload.items() if key not in _REBUILD_FIELDS}
    cleaned["agent2ContractRecovery"] = {
        "version": AGENT2_CONTRACT_ALIGNMENT_VERSION,
        "reason": "requeue_alignment_only_agent2_output_invalid",
        "previousMissing": _failure_missing(payload),
        "singleReplay": True,
    }
    lineage = dict(_dict(cleaned.get("lineage")))
    lineage.update(
        currentStage="action_pack_ready",
        source="pipeline_items.payload_alignment_recovery",
    )
    cleaned["lineage"] = lineage
    cleaned["fallbackAllowed"] = False
    return cleaned


def recover_alignment_only_agent2_failures(
    data_version: str | None,
    *,
    limit: int = 20,
) -> Dict[str, Any]:
    if not data_version:
        return {"version": AGENT2_CONTRACT_ALIGNMENT_VERSION, "dataVersion": None, "recoveredItemCount": 0}
    try:
        with connect() as conn:
            rows = conn.execute(
                """
                SELECT item_id,payload
                FROM pipeline_items
                WHERE data_version=?
                  AND current_stage='agent2_output_invalid'
                  AND status='failed'
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (data_version, max(1, min(100, int(limit or 20)))),
            ).fetchall()
            recovered = 0
            for row in rows:
                try:
                    outer = json.loads(row["payload"] or "{}")
                except Exception:
                    outer = {}
                payload = outer.get("payload") if isinstance(outer.get("payload"), dict) else outer
                if not isinstance(payload, dict) or not alignment_only_failure(payload):
                    continue
                cleaned = clean_agent2_failure_payload(payload)
                if isinstance(outer.get("payload"), dict):
                    outer["payload"] = cleaned
                    envelope = dict(_dict(outer.get("envelope")))
                    envelope["stage"] = "action_pack_ready"
                    outer["envelope"] = envelope
                    stored = outer
                else:
                    stored = cleaned
                conn.execute(
                    """
                    UPDATE pipeline_items
                    SET current_stage='action_pack_ready', status='retry',
                        retry_count=0, error_reason=NULL, payload=?,
                        updated_at=datetime('now')
                    WHERE item_id=?
                    """,
                    (dumps(stored), row["item_id"]),
                )
                recovered += 1
            conn.commit()
        return {
            "version": AGENT2_CONTRACT_ALIGNMENT_VERSION,
            "dataVersion": data_version,
            "recoveredItemCount": recovered,
            "singleReplay": True,
        }
    except Exception as exc:
        return {
            "version": AGENT2_CONTRACT_ALIGNMENT_VERSION,
            "dataVersion": data_version,
            "recoveredItemCount": 0,
            "error": str(exc)[:240],
        }


def install_v2171_agent2_contract_alignment() -> None:
    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
    from src.services import agent_runtime_contract_v2141_service as runtime_contract
    from src.services import pipeline_action_microbatch_v205_service as action_worker

    if getattr(agent2, "_V2171_AGENT2_CONTRACT_ALIGNMENT_INSTALLED", False):
        return

    original_build_messages = agent2._build_messages
    original_normalize_plan = agent2._normalize_plan
    original_missing_contract = runtime_contract.missing_agent2_contract
    original_pipeline_tick = pipeline_worker.run_agent_pipeline_tick

    def build_messages_v2171(
        data_version: str | None,
        packages: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        messages, payload = original_build_messages(data_version, packages)
        messages[0]["content"] += (
            "V21.7.1结构硬约束：executionObject必须包含targetId或targetSelector，只有targetName无效；"
            "新建对象尚无ID时，用可执行筛选条件写targetSelector，例如productId=P1;create=new_ad_plan。"
            "operationPlan.operations每项target也必须包含id或selector，并与executionObject一致。"
            "ROAS操作分开不等于四类操作必须全部输出；只输出有事实支撑且字段完整的操作，禁止用空操作凑数。"
            "budget_update必须含currentValue.budget、targetValue.budget、adjustmentAmount；"
            "bid_update必须含currentValue.bid、targetValue.bid或changeRate；"
            "target_roas_update必须含targetValue.roas；stop_rule_update必须含threshold或condition。"
            "缺少某类操作事实时省略该操作；若所有ROAS操作均无完整事实，返回action_plan_missing_data并列出missingData。"
            "所有动作族仍必须输出至少4条operatorActionSteps、3条对象化executionSteps、2条对象化decisionBranches和2条对象化submissionEvidence。"
            "title_image_test必须输出2-5组creativeTestPlan.groups，每组必须有fullTitle、mainImageStructure和testFocusWords。"
            "库存只能生成仓储协同，不得把暂停投放、关闭计划或断流作为库存动作。"
        )
        payload["agent2ContractAlignmentVersion"] = AGENT2_CONTRACT_ALIGNMENT_VERSION
        return messages, payload

    def normalize_plan_v2171(
        raw: Dict[str, Any],
        package: Dict[str, Any],
        proof: Dict[str, Any],
    ) -> Dict[str, Any]:
        plan = original_normalize_plan(canonicalize_agent2_raw(raw, package), package, proof)
        plan["agent2ContractAlignmentVersion"] = AGENT2_CONTRACT_ALIGNMENT_VERSION
        return plan

    def missing_agent2_contract_v2171(package: Dict[str, Any]) -> List[str]:
        plan = _dict(package.get("agent2ActionPlan") or package.get("plan"))
        return canonical_missing_fields(original_missing_contract(package), plan)

    def run_agent_pipeline_tick_v2171(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        data_version = kwargs.get("data_version")
        if data_version is None and args:
            data_version = args[0]
        if not data_version:
            data_version = pipeline_worker.latest_data_version()
        recovery = recover_alignment_only_agent2_failures(data_version)
        result = original_pipeline_tick(*args, **kwargs)
        result["agent2ContractAlignmentVersion"] = AGENT2_CONTRACT_ALIGNMENT_VERSION
        result["agent2ContractAlignmentRecovery"] = recovery
        return result

    agent2._build_messages = build_messages_v2171
    agent2._normalize_plan = normalize_plan_v2171
    agent2.AGENT2_CONTRACT_ALIGNMENT_VERSION = AGENT2_CONTRACT_ALIGNMENT_VERSION
    runtime_contract.missing_agent2_contract = missing_agent2_contract_v2171
    action_worker.missing_agent2_contract = missing_agent2_contract_v2171
    pipeline_worker.run_agent_pipeline_tick = run_agent_pipeline_tick_v2171
    agent2._V2171_AGENT2_CONTRACT_ALIGNMENT_INSTALLED = True


__all__ = [
    "AGENT2_CONTRACT_ALIGNMENT_VERSION",
    "canonicalize_agent2_raw",
    "canonical_missing_fields",
    "alignment_only_failure",
    "clean_agent2_failure_payload",
    "recover_alignment_only_agent2_failures",
    "install_v2171_agent2_contract_alignment",
]
