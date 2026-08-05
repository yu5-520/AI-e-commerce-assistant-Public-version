"""V21.7.3 Agent2 policy-family, object-shape and stale-failure recovery.

Aligns a ROAS package's experiment permission to the immutable Agent1 action
family, preserves provider-authored string content as structured objects, caps
budget operations to the upstream ceiling, clears stale failure labels, and
replays only the diagnosed failures once.
"""

from __future__ import annotations

import copy
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

AGENT2_POLICY_SHAPE_RECOVERY_VERSION = "21.7.3"
STRUCTURAL_MISSING_MARKERS = {
    "executionSteps_min_3",
    "decisionBranches_min_2",
    "submissionEvidence_min_2",
}
_STALE_FAILURE_FIELDS = {
    "reason", "blockedReason", "missing", "failureOwner",
    "frontendFailureLabel", "taskAdmissionAllowed", "agent2RetryPolicy",
    "agent2Provider", "agent2Source", "agent2ExecutionProof",
    "agent2ActionPlan", "actionPlanStatus", "actionPlanSource", "plan",
    "operationPlan", "sopDecision", "taskAdmission", "decisionId", "taskId",
}
_INSTALLED = False


def _bounded_ratio(value: Any, default: float = 0.10) -> float:
    ratio = _ratio(value)
    return max(0.01, min(0.50, abs(default if ratio is None else ratio)))


def align_experiment_policy_to_locked_family(package: Dict[str, Any]) -> Dict[str, Any]:
    aligned = copy.deepcopy(package)
    family = _package_family(aligned)
    if not family:
        return aligned

    cross = dict(_dict(aligned.get("crossValidation")))
    previous = dict(_policy(aligned))
    policy = copy.deepcopy(previous)
    previous_family = _lower(policy.get("actionFamily"))
    previous_target = _lower(policy.get("targetObject"))
    policy["actionFamily"] = family

    if family in ROAS_FAMILIES:
        mode = _lower(policy.get("experimentMode"))
        policy.update(
            experimentMode=mode if mode in ISOLATED_MODES else "isolated_test",
            targetObject="new_ad_plan",
            operationScope="isolated_ad_plan_test",
            trafficShareCeiling=_bounded_ratio(policy.get("trafficShareCeiling")),
            budgetChangeCeiling=_bounded_ratio(policy.get("budgetChangeCeiling")),
            durationHours=max(6, min(720, int(_number(policy.get("durationHours")) or 72))),
            mainlineMutationAllowed=False,
            singleVariablePreferred=policy.get("singleVariablePreferred") is not False,
            rollbackRequired=policy.get("rollbackRequired") is not False,
        )
        policy.setdefault("allowed", True)

    changed = previous_family != family or (
        family in ROAS_FAMILIES
        and previous_target not in {"new_ad_plan", "ad_plan", "isolated_ad_plan"}
    )
    alignment = {
        "version": AGENT2_POLICY_SHAPE_RECOVERY_VERSION,
        "lockedActionFamily": family,
        "previousActionFamily": previous_family or None,
        "previousTargetObject": previous_target or None,
        "changed": changed,
    }
    policy["familyAlignment"] = alignment
    cross["experimentPolicy"] = policy
    aligned["crossValidation"] = cross
    aligned["experimentPolicy"] = policy
    aligned["agent2PolicyFamilyAlignment"] = alignment
    return aligned


def _target_selector(package: Dict[str, Any], policy: Dict[str, Any]) -> str:
    identity = _dict(package.get("productIdentity"))
    values = {
        "productId": package.get("productId") or identity.get("productId"),
        "skuId": package.get("skuId") or identity.get("skuId"),
        "storeId": package.get("storeId") or identity.get("storeId"),
    }
    parts = [f"{key}={_text(value)}" for key, value in values.items() if _text(value)]
    parts.extend([
        "create=new_ad_plan",
        f"scope={_text(policy.get('operationScope')) or 'isolated_ad_plan_test'}",
    ])
    return ";".join(parts)


def _objectize(value: Any, kind: str) -> List[Dict[str, Any]]:
    source = value.get("items") if isinstance(value, dict) else value
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(_arr(source)):
        if isinstance(item, dict) and item:
            result.append(copy.deepcopy(item))
            continue
        text = _text(item)
        if not text:
            continue
        common = {"source": "agent2_provider_string_shape_alignment"}
        if kind == "execution":
            result.append({"stepId": f"STEP-{index + 1}", "sequence": index + 1, "action": text, **common})
        elif kind == "decision":
            result.append({"branchId": f"BRANCH-{index + 1}", "statement": text, **common})
        else:
            result.append({"evidenceId": f"EVIDENCE-{index + 1}", "requirement": text, **common})
    return result


def _first_number(obj: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(obj, dict):
        for key in keys:
            value = _number(obj.get(key))
            if value is not None:
                return value
        for child in obj.values():
            found = _first_number(child, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for child in obj[:30]:
            found = _first_number(child, keys)
            if found is not None:
                return found
    return None


def _cap_rate_fields(value: Any, ceiling: float, audit: List[Dict[str, Any]], path: str = "") -> Any:
    tokens = (
        "budgetchangerate", "budget_change_rate", "budgetadjustmentrate",
        "budget_adjustment_rate", "recommendedbudgetincreaserate",
        "recommendedbudgetdecreaserate", "预算调整比例", "预算变化比例",
    )
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            rate = _ratio(child) if any(token in str(key).lower() for token in tokens) else None
            if rate is not None and abs(rate) > ceiling + 1e-9:
                result[key] = ceiling
                audit.append({"path": child_path, "original": rate, "normalized": ceiling, "reason": "locked_family_budget_ceiling"})
            else:
                result[key] = _cap_rate_fields(child, ceiling, audit, child_path)
        return result
    if isinstance(value, list):
        return [_cap_rate_fields(child, ceiling, audit, f"{path}[{index}]") for index, child in enumerate(value)]
    return value


def _cap_budget_operations(raw: Dict[str, Any], package: Dict[str, Any], ceiling: float, audit: List[Dict[str, Any]]) -> Dict[str, Any]:
    aligned = copy.deepcopy(raw)
    operation_plan = dict(_dict(aligned.get("operationPlan")))
    operations = [copy.deepcopy(item) for item in _arr(operation_plan.get("operations") or aligned.get("operations")) if isinstance(item, dict)]
    pack = _dict(package.get("actionParameterPack")) or _dict(package.get("actionDataPack"))
    pack_current = _first_number(pack, ("currentBudget", "currentDailyBudget", "beforeBudget", "currentAdSpend"))

    for index, operation in enumerate(operations):
        kind = _lower(operation.get("operationType") or operation.get("type") or operation.get("action"))
        if kind not in {"budget", "budget_change", "budget_adjust", "budget_update"}:
            continue
        current_value = dict(_dict(operation.get("currentValue")))
        target_value = dict(_dict(operation.get("targetValue")))
        current = _number(current_value.get("budget")) or _first_number(operation, ("currentBudget", "currentDailyBudget", "beforeBudget")) or pack_current
        target = _number(target_value.get("budget")) or _first_number(operation, ("targetBudget", "targetDailyBudget", "afterBudget"))
        if current in {None, 0} or target is None:
            continue
        rate = abs(target - current) / abs(current)
        if rate <= ceiling + 1e-9:
            continue
        direction = "increase" if target > current else "decrease"
        capped = max(0.0, current * (1 + ceiling if direction == "increase" else 1 - ceiling))
        operation.update(
            direction=direction,
            currentValue={**current_value, "budget": current},
            targetValue={**target_value, "budget": capped},
            changeRate=ceiling,
            adjustmentAmount=abs(capped - current),
        )
        audit.append({"path": f"operationPlan.operations[{index}].targetValue.budget", "original": target, "normalized": capped, "reason": "locked_family_budget_ceiling"})

    if operations:
        operation_plan["operations"] = operations
        aligned["operationPlan"] = operation_plan
    return aligned


def align_agent2_policy_and_shape(raw: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    package = align_experiment_policy_to_locked_family(package)
    policy = _policy(package)
    family = _package_family(package)
    aligned = copy.deepcopy(raw)
    audit: List[Dict[str, Any]] = []
    aligned["executionSteps"] = _objectize(aligned.get("executionSteps"), "execution")
    aligned["decisionBranches"] = _objectize(aligned.get("decisionBranches"), "decision")
    aligned["submissionEvidence"] = _objectize(aligned.get("submissionEvidence"), "evidence")

    if family in ROAS_FAMILIES:
        aligned["operationMode"] = _lower(policy.get("experimentMode")) or "isolated_test"
        execution = dict(_dict(aligned.get("executionObject")))
        selector = _lower(execution.get("targetSelector"))
        target_type = _lower(execution.get("targetType") or execution.get("type"))
        if not execution.get("targetId") and (
            not execution.get("targetSelector") or "secondary_link" in selector
            or "activity" in selector or "inventory_coordination_ticket" in selector
            or target_type not in {"", "ad_plan", "advertising_plan"}
        ):
            replacement = _target_selector(package, policy)
            audit.append({"path": "executionObject.targetSelector", "original": execution.get("targetSelector"), "normalized": replacement, "reason": "locked_roas_family_requires_isolated_ad_plan"})
            execution.update(targetSelector=replacement, targetType="ad_plan")
            execution.pop("targetId", None)
        aligned["executionObject"] = execution

        ceiling = _bounded_ratio(policy.get("budgetChangeCeiling"))
        aligned = _cap_rate_fields(aligned, ceiling, audit)
        aligned = _cap_budget_operations(aligned, package, ceiling, audit)
        operation_plan = dict(_dict(aligned.get("operationPlan")))
        operations = [copy.deepcopy(item) for item in _arr(operation_plan.get("operations")) if isinstance(item, dict)]
        selector = _text(_dict(aligned.get("executionObject")).get("targetSelector"))
        for operation in operations:
            target = dict(_dict(operation.get("target")))
            target_selector = _lower(target.get("selector") or target.get("targetSelector"))
            target_type = _lower(target.get("type") or target.get("targetType"))
            if not target.get("id") and (
                not target.get("selector") or "secondary_link" in target_selector
                or "activity" in target_selector
                or target_type not in {"", "ad_plan", "advertising_plan"}
            ):
                target.update(type="ad_plan", selector=selector)
                target.pop("id", None)
            operation["target"] = target
        if operations:
            operation_plan["operations"] = operations
            aligned["operationPlan"] = operation_plan

    aligned["agent2PolicyShapeAlignment"] = {
        "version": AGENT2_POLICY_SHAPE_RECOVERY_VERSION,
        "lockedActionFamily": family,
        "policyActionFamily": _lower(policy.get("actionFamily")) or None,
        "objectShapePreserved": True,
        "normalizations": audit,
    }
    return aligned


def clean_stale_agent2_failure_fields(package: Dict[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in package.items() if key not in _STALE_FAILURE_FIELDS}


def _failure_missing(payload: Dict[str, Any]) -> List[str]:
    plan = _dict(payload.get("agent2ActionPlan") or payload.get("plan"))
    validation = _dict(_dict(plan.get("operationPlan")).get("validation"))
    result: List[str] = []
    for value in (
        _arr(payload.get("missing")) + _arr(plan.get("semanticContractMissing"))
        + _arr(plan.get("experimentPermissionViolations")) + _arr(validation.get("missing"))
    ):
        item = _text(value)
        if item and item not in result:
            result.append(item)
    return result


def classify_policy_shape_failure(payload: Dict[str, Any], action_family: str | None = None) -> Dict[str, Any]:
    family = _lower(action_family or payload.get("actionFamily") or payload.get("lockedActionFamily"))
    policy = _policy(payload)
    policy_family = _lower(policy.get("actionFamily"))
    target = _lower(policy.get("targetObject"))
    missing = _failure_missing(payload)
    shape = any(any(marker in item for marker in STRUCTURAL_MISSING_MARKERS) for item in missing)
    family_mismatch = bool(policy_family and family and policy_family != family)
    target_mismatch = bool(family in ROAS_FAMILIES and target and target not in {"new_ad_plan", "ad_plan", "isolated_ad_plan"})
    matched = bool(family in ROAS_FAMILIES and (shape or family_mismatch or target_mismatch))
    return {
        "matched": matched,
        "actionFamily": family,
        "policyActionFamily": policy_family,
        "policyTargetObject": target,
        "familyMismatch": family_mismatch,
        "targetMismatch": target_mismatch,
        "shapeFailure": shape,
        "permissionFailure": any("experimentpermission" in item.lower() or "budget_change_exceeds_ceiling" in item.lower() for item in missing),
        "previousMissing": missing,
        "disposition": "requeue_agent2_after_policy_shape_alignment" if matched else "leave_untouched",
    }


def recover_policy_shape_failures(data_version: str | None, *, limit: int = 50) -> Dict[str, Any]:
    if not data_version:
        return {"version": AGENT2_POLICY_SHAPE_RECOVERY_VERSION, "dataVersion": None, "requeuedCount": 0}

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
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(pipeline_items)").fetchall()}
        for row in rows:
            outer = loads(row["payload"]) if row["payload"] else {}
            outer = outer if isinstance(outer, dict) else {}
            payload = _payload(outer)
            if _dict(payload.get("agent2PolicyShapeRecovery")).get("version") == AGENT2_POLICY_SHAPE_RECOVERY_VERSION:
                continue
            classification = classify_policy_shape_failure(payload, row["action_family"])
            if not classification["matched"]:
                continue
            cleaned = align_experiment_policy_to_locked_family(clean_stale_agent2_failure_fields(payload))
            cleaned["agent2PolicyShapeRecovery"] = {
                "version": AGENT2_POLICY_SHAPE_RECOVERY_VERSION,
                "singleReplay": True,
                "previousStage": row["current_stage"],
                "previousActionFamily": row["action_family"],
                "classification": classification,
            }
            cleaned["fallbackAllowed"] = False
            cleaned["lineage"] = {**_dict(cleaned.get("lineage")), "currentStage": "action_pack_ready", "source": "pipeline_items.payload_policy_shape_recovery"}
            if isinstance(outer.get("payload"), dict):
                outer["payload"] = cleaned
                outer["envelope"] = {**_dict(outer.get("envelope")), "stage": "action_pack_ready", "actionFamily": row["action_family"], "route": row["route"]}
                stored = outer
            else:
                stored = cleaned

            assignments = ["current_stage='action_pack_ready'", "status='retry'", "retry_count=0", "error_reason=NULL", "payload=?", "updated_at=?"]
            for column in ("failure_code", "failure_class", "claim_id", "lease_expires_at", "retry_after"):
                if column in columns:
                    assignments.append(f"{column}=NULL")
            conn.execute(
                f"UPDATE pipeline_items SET {','.join(assignments)} WHERE item_id=?",
                (dumps(stored), datetime.now().isoformat(), row["item_id"]),
            )
            output_ref = f"agent2_policy_shape_requeue:{data_version}:{row['item_id']}"
            envelope = build_item_envelope(
                data_version=row["data_version"], item_id=row["item_id"],
                product_id=row["product_id"], store_id=row["store_id"],
                signal_id=row["signal_id"], package_id=row["package_id"],
                decision_id=row["decision_id"], action_family=row["action_family"],
                route=row["route"], output_ref=output_ref, stage="action_pack_ready",
            )
            events.append((envelope, cleaned, output_ref))
            requeued += 1
        conn.commit()

    for envelope, payload, output_ref in events:
        record_pipeline_item_event(
            envelope, station_id="agent2_policy_shape_recovery_station",
            stage="action_pack_ready", status="retry", output_ref=output_ref,
            payload=payload,
        )
    return {
        "version": AGENT2_POLICY_SHAPE_RECOVERY_VERSION,
        "dataVersion": data_version,
        "requeuedCount": requeued,
        "singleReplay": True,
        "rule": "Only diagnosed ROAS policy-family or object-shape failures are replayed once.",
    }


def install_v2173_agent2_policy_shape_recovery() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
    from src.services import pipeline_action_microbatch_v205_service as action_worker

    if getattr(agent2, "_V2173_AGENT2_POLICY_SHAPE_RECOVERY_INSTALLED", False):
        _INSTALLED = True
        return
    original_compact = agent2._compact_package
    original_messages = agent2._build_messages
    original_normalize = agent2._normalize_plan
    original_mark_invalid = action_worker._mark_agent2_output_invalid
    original_pipeline_tick = pipeline_worker.run_agent_pipeline_tick

    def compact_package_v2173(package: Dict[str, Any]) -> Dict[str, Any]:
        return original_compact(align_experiment_policy_to_locked_family(package))

    def build_messages_v2173(data_version: str | None, packages: List[Dict[str, Any]]) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
        packages = [align_experiment_policy_to_locked_family(package) for package in packages]
        messages, payload = original_messages(data_version, packages)
        messages[0]["content"] += (
            "V21.7.3合同修复：experimentPolicy.actionFamily必须与Agent1锁定动作族完全一致；"
            "roas_scale和roas_guard只能使用new_ad_plan/isolated_ad_plan_test，禁止继承platform_activity的"
            "secondary_link_small_traffic_activity权限。executionSteps、decisionBranches和submissionEvidence"
            "必须输出JSON对象数组，禁止字符串数组；已有字符串内容只能结构化封装，不得丢弃。"
            "预算目标与预算变化比例都不得超过experimentPolicy.budgetChangeCeiling。"
        )
        payload["agent2PolicyShapeRecoveryVersion"] = AGENT2_POLICY_SHAPE_RECOVERY_VERSION
        return messages, payload

    def normalize_plan_v2173(raw: Dict[str, Any], package: Dict[str, Any], proof: Dict[str, Any]) -> Dict[str, Any]:
        package = align_experiment_policy_to_locked_family(package)
        plan = original_normalize(align_agent2_policy_and_shape(raw, package), package, proof)
        plan["agent2PolicyShapeRecoveryVersion"] = AGENT2_POLICY_SHAPE_RECOVERY_VERSION
        plan["agent2PolicyFamilyAlignment"] = package.get("agent2PolicyFamilyAlignment")
        return plan

    def mark_agent2_output_invalid_v2173(item: Dict[str, Any], package: Dict[str, Any], plan: Dict[str, Any], provider: Dict[str, Any], missing: List[str] | None = None) -> None:
        original_mark_invalid(item, clean_stale_agent2_failure_fields(package), plan, provider, missing)

    def run_pipeline_tick_v2173(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        data_version = kwargs.get("data_version") or (args[0] if args else None) or pipeline_worker.latest_data_version()
        recovery = recover_policy_shape_failures(data_version)
        result = original_pipeline_tick(*args, **kwargs)
        result["agent2PolicyShapeRecoveryVersion"] = AGENT2_POLICY_SHAPE_RECOVERY_VERSION
        result["agent2PolicyShapeRecovery"] = recovery
        return result

    agent2._compact_package = compact_package_v2173
    agent2._build_messages = build_messages_v2173
    agent2._normalize_plan = normalize_plan_v2173
    action_worker._mark_agent2_output_invalid = mark_agent2_output_invalid_v2173
    pipeline_worker.run_agent_pipeline_tick = run_pipeline_tick_v2173
    for module in (agent2, action_worker, pipeline_worker):
        module.AGENT2_POLICY_SHAPE_RECOVERY_VERSION = AGENT2_POLICY_SHAPE_RECOVERY_VERSION
        module._V2173_AGENT2_POLICY_SHAPE_RECOVERY_INSTALLED = True
    _INSTALLED = True


__all__ = [
    "AGENT2_POLICY_SHAPE_RECOVERY_VERSION",
    "align_experiment_policy_to_locked_family",
    "align_agent2_policy_and_shape",
    "classify_policy_shape_failure",
    "clean_stale_agent2_failure_fields",
    "install_v2173_agent2_policy_shape_recovery",
    "recover_policy_shape_failures",
]
