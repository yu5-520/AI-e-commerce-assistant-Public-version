"""V23.2.15 Agent3 SOP with structured auxiliary conditions and isolated repair."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Tuple

from src.services.agent3_system_constraint_v23215_service import (
    AGENT3_SYSTEM_CONSTRAINT_VERSION,
    compile_agent3_provider_package,
    family_policy,
    validate_agent3_sop_system_contract,
)

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
AGENT3_SOP_CORE_VERSION = "23.2.15"
AGENT3_SOP_SCHEMA = "agent3.sop.v1"

SOP_READY = "sop_ready"
SOP_MISSING_DATA = "sop_missing_data"
SOP_REQUIRES_APPROVAL = "sop_requires_approval"
SOP_CONFLICT = "sop_conflict"
SOP_STATUSES = {SOP_READY, SOP_MISSING_DATA, SOP_REQUIRES_APPROVAL, SOP_CONFLICT}

_LEGACY_DUPLICATE_STEP_ERRORS = {
    "executionObject",
    "operatorActionSteps",
    "operatorActionSteps_min_1",
    "agent3_sop_operator_steps_min_3",
    "agent3_sop_structured_steps_cover_operator_steps",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 1200) -> str:
    if isinstance(value, dict):
        value = (
            value.get("summary")
            or value.get("text")
            or value.get("action")
            or value.get("title")
            or value.get("condition")
        )
    return " ".join(str(value or "").split())[:limit]


def _dedupe_lines(values: Any) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in _arr(values):
        line = _text(value, 1200)
        marker = "".join(ch.lower() for ch in line if ch.isalnum())
        if line and marker and marker not in seen:
            seen.add(marker)
            result.append(line)
    return result


def _system_constraint_required(package: Dict[str, Any]) -> bool:
    contract = _dict(package.get("inputContract"))
    return bool(
        contract.get("schema") == "agent_input.agent3_sop.v1"
        or contract.get("agent3SystemConstraintRequired") is True
        or package.get("enforceAgent3SystemConstraint") is True
    )


def _build_messages(
    data_version: str | None,
    packages: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    constrained_packages = [compile_agent3_provider_package(package) for package in packages]
    payload = {
        "dataVersion": data_version,
        "version": AGENT3_SOP_CORE_VERSION,
        "schema": AGENT3_SOP_SCHEMA,
        "systemConstraintVersion": AGENT3_SYSTEM_CONSTRAINT_VERSION,
        "packages": constrained_packages,
    }
    prompt = (
        "你是V23.2.15 Agent3公司化SOP Agent。系统已完成动作族锁定、输入裁剪和字段分区。"
        "每个package中只有actionSources可以转化为运营动作；constraints只能限制方案，"
        "systemCompletedFacts是系统已完成事实，严禁再次写成人工步骤；forbiddenActions严禁输出。"
        "不得重新分析原始报表、重新选择动作族、扩大权限、参数或执行对象。"
        "executionSteps是唯一权威步骤集合，不要输出operatorActionSteps，系统会从executionSteps[*].instruction确定性生成展示列表。"
        "不要输出顶层executionObject，系统会从商品身份确定性生成任务主对象。"
        "每条executionSteps必须包含stepId,actionFamily,actionType,executionObject,executorRole,instruction,deadline,completionCriteria。"
        "executionObject表示被操作的商品、素材、页面、计划、实验组或结果数据；executorRole表示设计、文案、运营、审核或数据分析等负责人，严禁混用。"
        "actionFamily必须等于lockedActionFamily，actionType必须来自allowedActionTypes并覆盖requiredActionTypeGroups。"
        "stopConditions和rollbackConditions必须是结构化对象数组，不得输出自由文本。"
        "每条stopConditions必须包含conditionId,actionFamily,conditionType,condition,responseAction,evidenceRequired；"
        "conditionType只能来自allowedStopConditionTypes。"
        "每条rollbackConditions必须包含conditionId,actionFamily,conditionType,condition,rollbackAction,evidenceRequired；"
        "conditionType只能来自allowedRollbackConditionTypes。"
        "停止条件只能由当前动作族的指标保护、合规、实验完整性、素材反馈或样本充分性触发；"
        "不得用库存、仓储、补货、其他部门、ROAS或活动风险停止标题主图任务。"
        "不得把系统冻结基线、证据快照或数据留存写成人工动作；只能引用系统冻结证据作为对照。"
        "不同商品步骤必须体现Agent2草案差异，禁止固定模板填充。"
        "每个结果必须包含packageId,productId,storeId,actionFamily,sopStatus,finalTaskTitle,executionObjective,"
        "executionSteps,decisionBranches,submissionEvidence,crossDepartmentActions,approvalFlow,reviewMetrics,"
        "verificationPeriod,stopConditions,rollbackConditions,reviewCycle,companyStyleReason,ragUsedCaseIds,"
        "ragRejectedCaseIds,ragApplicationReason,semanticContractMissing。"
        "可直接执行时sopStatus=sop_ready；权限触发审核但内容完整时sop_requires_approval；"
        "事实不足时sop_missing_data；与草案、系统合同或权限冲突时sop_conflict。"
        "只返回严格JSON对象，顶层sops数组。"
    )
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        },
    ], payload


def _normalize_status(raw: Dict[str, Any]) -> str:
    value = _text(raw.get("sopStatus") or raw.get("status"), 80).lower()
    aliases = {
        "ready": SOP_READY,
        "requires_approval": SOP_REQUIRES_APPROVAL,
        "missing_data": SOP_MISSING_DATA,
        "conflict": SOP_CONFLICT,
    }
    return aliases.get(value, value if value in SOP_STATUSES else "")


def _normalize_execution_step(value: Dict[str, Any], index: int, family: str) -> Dict[str, Any]:
    step = dict(value)
    result = {
        "stepId": _text(step.get("stepId") or step.get("id") or f"STEP-{index + 1}", 120),
        "actionFamily": _text(step.get("actionFamily") or family, 100),
        "actionType": _text(step.get("actionType") or step.get("type"), 120),
        "executionObject": (
            step.get("executionObject")
            or step.get("targetObject")
            or step.get("target")
            or step.get("object")
        ),
        "executorRole": _text(
            step.get("executorRole")
            or step.get("responsibleRole")
            or step.get("ownerRole")
            or step.get("executor"),
            160,
        ),
        "instruction": _text(step.get("instruction") or step.get("action") or step.get("step"), 1600),
        "deadline": _text(step.get("deadline") or step.get("timeLimit") or step.get("due"), 300),
        "completionCriteria": _text(
            step.get("completionCriteria")
            or step.get("doneDefinition")
            or step.get("acceptanceCriteria"),
            1000,
        ),
    }
    for key, item in step.items():
        if key not in result and item not in (None, "", [], {}):
            result[key] = item
    return {key: item for key, item in result.items() if item not in (None, "", [], {})}


def _normalize_auxiliary_condition(
    value: Any,
    *,
    index: int,
    family: str,
    kind: str,
    strict: bool,
) -> Any:
    if not isinstance(value, dict):
        if not strict:
            return _text(value, 1200)
        return {
            "conditionId": f"{'STOP' if kind == 'stop' else 'ROLLBACK'}-{index + 1}",
            "actionFamily": family,
            "conditionType": "unclassified",
            "condition": _text(value, 1200),
        }
    item = dict(value)
    condition_id = _text(
        item.get("conditionId")
        or item.get("id")
        or f"{'STOP' if kind == 'stop' else 'ROLLBACK'}-{index + 1}",
        120,
    )
    result: Dict[str, Any] = {
        "conditionId": condition_id,
        "actionFamily": _text(item.get("actionFamily") or family, 100),
        "conditionType": _text(item.get("conditionType") or item.get("type"), 160),
        "condition": _text(
            item.get("condition")
            or item.get("trigger")
            or item.get("when")
            or item.get("text"),
            1400,
        ),
        "evidenceRequired": item.get("evidenceRequired") or item.get("evidence"),
    }
    if kind == "stop":
        result["responseAction"] = _text(
            item.get("responseAction") or item.get("action") or item.get("response"),
            1000,
        )
    else:
        result["rollbackAction"] = _text(
            item.get("rollbackAction") or item.get("action") or item.get("response"),
            1000,
        )
    for key, item_value in item.items():
        if key not in result and item_value not in (None, "", [], {}):
            result[key] = item_value
    return {key: item_value for key, item_value in result.items() if item_value not in (None, "", [], {})}


def _normalize_auxiliary_conditions(
    values: Any,
    *,
    family: str,
    kind: str,
    strict: bool,
) -> List[Any]:
    return [
        _normalize_auxiliary_condition(item, index=index, family=family, kind=kind, strict=strict)
        for index, item in enumerate(_arr(values))
        if item not in (None, "", [], {})
    ]


def _task_execution_object(raw: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    existing = _dict(raw.get("executionObject"))
    if existing:
        return existing
    identity = _dict(package.get("productIdentity"))
    product_id = package.get("productId") or identity.get("productId")
    store_id = package.get("storeId") or identity.get("storeId")
    title = package.get("productTitle") or identity.get("productTitle") or identity.get("title")
    return {
        key: value
        for key, value in {
            "targetType": "product",
            "targetId": product_id,
            "storeId": store_id,
            "label": title or product_id,
        }.items()
        if value not in (None, "")
    }


def _object_ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"id", "targetId", "productId", "planId", "campaignId", "activityId"} and item not in (None, ""):
                result.add(str(item))
            result.update(_object_ids(item))
    elif isinstance(value, list):
        for item in value:
            result.update(_object_ids(item))
    return result


def _boundary_missing(sop: Dict[str, Any], package: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    draft = _dict(package.get("agent2ActionDraft"))
    locked_family = _text(package.get("lockedActionFamily") or draft.get("actionFamily"), 100)
    if _text(sop.get("actionFamily"), 100) != locked_family:
        missing.append("actionFamily_matches_agent1_lock")
    allowed_ids = _object_ids(
        {
            "productIdentity": package.get("productIdentity"),
            "actionParameterPack": package.get("actionParameterPack"),
            "agent2ActionDraft": draft,
        }
    )
    used_ids = _object_ids(
        {
            "executionObject": sop.get("executionObject"),
            "executionSteps": sop.get("executionSteps"),
            "crossDepartmentActions": sop.get("crossDepartmentActions"),
        }
    )
    allowed_ids.update(
        str(value)
        for value in (package.get("productId"), package.get("storeId"), package.get("packageId"))
        if value not in (None, "")
    )
    unknown = sorted(value for value in used_ids if value not in allowed_ids)
    if unknown:
        missing.append("fabricated_execution_object:" + ",".join(unknown[:8]))
    if _system_constraint_required(package):
        missing.extend(validate_agent3_sop_system_contract(sop, package))
    return list(dict.fromkeys(missing))


def missing_agent3_sop_contract(sop: Dict[str, Any], package: Dict[str, Any] | None = None) -> List[str]:
    sop = _dict(sop)
    package = _dict(package)
    missing: List[str] = []
    validation = _dict(sop.get("contractValidation"))
    evaluated_status = str(validation.get("evaluatedStatus") or sop.get("sopStatus") or "")
    if sop.get("sopStatus") not in SOP_STATUSES:
        missing.append("sopStatus")
    for key in ("packageId", "productId", "storeId", "actionFamily"):
        if sop.get(key) in (None, "", {}, []):
            missing.append(key)
    if evaluated_status in {SOP_READY, SOP_REQUIRES_APPROVAL}:
        for key in (
            "finalTaskTitle", "executionObjective", "executionObject", "operatorActionSteps",
            "executionSteps", "submissionEvidence", "verificationPeriod", "stopConditions",
            "rollbackConditions", "reviewMetrics", "companyStyleReason",
        ):
            if sop.get(key) in (None, "", {}, []):
                missing.append(key)
        if package:
            missing.extend(_boundary_missing(sop, package))
    return list(dict.fromkeys(missing))


def repairable_agent3_auxiliary_missing(missing: Any) -> List[str]:
    values = [_text(item, 500) for item in _arr(missing) if _text(item, 500)]
    if not values:
        return []
    allowed_prefixes = (
        "agent3_stop_condition_",
        "agent3_stop_conditions_",
        "agent3_rollback_condition_",
        "agent3_rollback_conditions_",
        "agent3_sop_missing:rollbackConditions",
        "agent3_sop_missing:stopConditions",
    )
    repairable: List[str] = []
    for item in values:
        if item.startswith(allowed_prefixes):
            repairable.append(item)
            continue
        if item.startswith("agent3_sop_cross_family_contamination:") and (
            ".stopConditions" in item or ".rollbackConditions" in item
        ):
            repairable.append(item)
            continue
        if item.startswith("agent3_system_fact_converted_to_action:") and (
            ".stopConditions" in item or ".rollbackConditions" in item
        ):
            repairable.append(item)
            continue
        return []
    return repairable


def _build_auxiliary_repair_messages(
    data_version: str | None,
    package: Dict[str, Any],
    raw_sop: Dict[str, Any],
    normalized_sop: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    family = _text(
        package.get("lockedActionFamily")
        or _dict(package.get("agent2ActionDraft")).get("actionFamily")
        or raw_sop.get("actionFamily"),
        100,
    )
    policy = family_policy(family)
    validation = _dict(normalized_sop.get("contractValidation"))
    payload = {
        "dataVersion": data_version,
        "version": AGENT3_SOP_CORE_VERSION,
        "repairType": "agent3_auxiliary_condition_repair",
        "packageId": package.get("packageId") or package.get("itemId"),
        "lockedActionFamily": family,
        "allowedStopConditionTypes": policy["allowedStopConditionTypes"],
        "allowedRollbackConditionTypes": policy["allowedRollbackConditionTypes"],
        "stopConditionRequiredFields": policy["stopConditionRequiredFields"],
        "rollbackConditionRequiredFields": policy["rollbackConditionRequiredFields"],
        "invalidFields": repairable_agent3_auxiliary_missing(validation.get("missing")),
        "currentStopConditions": raw_sop.get("stopConditions"),
        "currentRollbackConditions": raw_sop.get("rollbackConditions"),
        "immutableSopDigest": {
            "finalTaskTitle": normalized_sop.get("finalTaskTitle"),
            "executionObjective": normalized_sop.get("executionObjective"),
            "executionSteps": normalized_sop.get("executionSteps"),
            "submissionEvidence": normalized_sop.get("submissionEvidence"),
            "reviewMetrics": normalized_sop.get("reviewMetrics"),
            "verificationPeriod": normalized_sop.get("verificationPeriod"),
        },
    }
    prompt = (
        "你是V23.2.15 Agent3辅助条件局部修复器。只能修复stopConditions和rollbackConditions，"
        "严禁修改、重写、概括或返回executionSteps、标题、目标、证据、指标、周期及其他字段。"
        "停止条件必须只属于lockedActionFamily，只能使用allowedStopConditionTypes；"
        "回滚条件只能使用allowedRollbackConditionTypes。"
        "不得使用库存、仓储、补货、其他部门、ROAS或活动风险停止title_image_test。"
        "每条停止条件必须返回conditionId,actionFamily,conditionType,condition,responseAction,evidenceRequired；"
        "每条回滚条件必须返回conditionId,actionFamily,conditionType,condition,rollbackAction,evidenceRequired。"
        "只返回严格JSON对象：{\"repair\":{\"packageId\":\"...\",\"stopConditions\":[...],\"rollbackConditions\":[...]}}。"
    )
    return [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))},
    ], payload


def apply_agent3_auxiliary_repair(
    raw_sop: Dict[str, Any],
    repair_payload: Dict[str, Any],
    *,
    package_id: str,
) -> Dict[str, Any]:
    repair = _dict(repair_payload.get("repair"))
    if _text(repair.get("packageId"), 220) != _text(package_id, 220):
        raise ValueError("agent3_auxiliary_repair_package_mismatch")
    stop_conditions = repair.get("stopConditions")
    rollback_conditions = repair.get("rollbackConditions")
    if not isinstance(stop_conditions, list) or not isinstance(rollback_conditions, list):
        raise ValueError("agent3_auxiliary_repair_lists_missing")
    if not all(isinstance(item, dict) for item in stop_conditions + rollback_conditions):
        raise ValueError("agent3_auxiliary_repair_item_not_structured")
    patched = dict(raw_sop)
    patched["stopConditions"] = stop_conditions
    patched["rollbackConditions"] = rollback_conditions
    patched["auxiliaryConditionRepairApplied"] = True
    return patched


def _normalize_sop(
    raw: Dict[str, Any],
    package: Dict[str, Any],
    proof: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    draft = _dict(package.get("agent2ActionDraft"))
    family = _text(
        package.get("lockedActionFamily")
        or draft.get("actionFamily")
        or raw.get("actionFamily"),
        100,
    )
    strict = _system_constraint_required(package)
    structured = [
        _normalize_execution_step(item, index, family)
        for index, item in enumerate(_arr(raw.get("executionSteps")))
        if isinstance(item, dict) and item
    ]
    legacy_steps = _dedupe_lines(raw.get("operatorActionSteps") or raw.get("sopSteps"))
    if not structured and legacy_steps and not strict:
        structured = [
            {"stepId": f"STEP-{index + 1}", "actionFamily": family, "instruction": line}
            for index, line in enumerate(legacy_steps)
        ]
    operator_steps = _dedupe_lines(
        [step.get("instruction") for step in structured if _text(step.get("instruction"))]
    )
    evaluated_status = _normalize_status(raw)
    sop: Dict[str, Any] = {
        "version": AGENT3_SOP_CORE_VERSION,
        "schema": AGENT3_SOP_SCHEMA,
        "systemConstraintVersion": AGENT3_SYSTEM_CONSTRAINT_VERSION,
        "packageId": raw.get("packageId") or package.get("packageId") or package.get("itemId"),
        "productId": raw.get("productId") or package.get("productId"),
        "storeId": raw.get("storeId") or package.get("storeId"),
        "actionFamily": family,
        "lockedActionFamily": family,
        "sopStatus": evaluated_status,
        "finalTaskTitle": _text(raw.get("finalTaskTitle") or raw.get("taskTitle"), 260),
        "executionObjective": _text(raw.get("executionObjective") or raw.get("objective"), 1000),
        "executionObject": _task_execution_object(raw, package),
        "operatorActionSteps": operator_steps,
        "operatorActionStepsSource": "executionSteps[*].instruction",
        "executionSteps": structured,
        "authoritativeStepCollection": "executionSteps",
        "decisionBranches": [item for item in _arr(raw.get("decisionBranches")) if item not in (None, "", {}, [])],
        "submissionEvidence": [item for item in _arr(raw.get("submissionEvidence")) if item not in (None, "", {}, [])],
        "crossDepartmentActions": [item for item in _arr(raw.get("crossDepartmentActions")) if item not in (None, "", {}, [])],
        "approvalFlow": _dict(raw.get("approvalFlow")),
        "reviewMetrics": [item for item in _arr(raw.get("reviewMetrics")) if item not in (None, "", {}, [])],
        "verificationPeriod": _text(raw.get("verificationPeriod"), 240),
        "stopConditions": _normalize_auxiliary_conditions(
            raw.get("stopConditions"), family=family, kind="stop", strict=strict
        ),
        "rollbackConditions": _normalize_auxiliary_conditions(
            raw.get("rollbackConditions"), family=family, kind="rollback", strict=strict
        ),
        "reviewCycle": [item for item in _arr(raw.get("reviewCycle")) if item not in (None, "", {}, [])]
        or ["3天", "7天", "14天", "30天", "90天"],
        "companyStyleReason": _text(raw.get("companyStyleReason"), 1000),
        "ragUsedCaseIds": [str(item) for item in _arr(raw.get("ragUsedCaseIds"))],
        "ragRejectedCaseIds": [str(item) for item in _arr(raw.get("ragRejectedCaseIds"))],
        "ragApplicationReason": _text(raw.get("ragApplicationReason"), 1000),
        "agent3ExecutionProof": proof or {},
        "agent2DraftRef": package.get("agent2DraftRef"),
        "auxiliaryConditionRepairApplied": bool(raw.get("auxiliaryConditionRepairApplied")),
        "fallbackAllowed": False,
    }
    declared = [
        _text(item, 300)
        for item in _arr(raw.get("semanticContractMissing"))
        if _text(item, 300) and _text(item, 300) not in _LEGACY_DUPLICATE_STEP_ERRORS
    ]
    missing = list(dict.fromkeys(declared + missing_agent3_sop_contract(sop, package)))
    repairable = repairable_agent3_auxiliary_missing(missing)
    sop["semanticContractMissing"] = missing
    sop["contractValidation"] = {
        "version": AGENT3_SYSTEM_CONSTRAINT_VERSION,
        "evaluatedStatus": evaluated_status,
        "passed": not missing,
        "missing": missing,
        "authoritativeStepCollection": "executionSteps",
        "operatorProjectionSource": "executionSteps[*].instruction",
        "repairableAuxiliaryOnly": bool(missing and repairable and len(repairable) == len(missing)),
        "repairableMissing": repairable,
        "statusDowngraded": bool(evaluated_status in {SOP_READY, SOP_REQUIRES_APPROVAL} and missing),
    }
    if evaluated_status in {SOP_READY, SOP_REQUIRES_APPROVAL} and missing:
        sop["sopStatus"] = SOP_MISSING_DATA
    return {
        key: value
        for key, value in sop.items()
        if value not in (None, "", [], {})
        or key in {"semanticContractMissing", "submissionEvidence", "crossDepartmentActions"}
    }


def semantic_call_id(
    *,
    input_fingerprint: str,
    provider_request_id: str,
    package_id: str,
) -> str:
    raw = "|".join(
        [
            "agent3_sop_agent",
            AGENT3_SOP_CORE_VERSION,
            input_fingerprint,
            provider_request_id or "provider_call",
            package_id,
        ]
    )
    return "A3CALL-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20].upper()


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT3_SOP_CORE_VERSION",
    "AGENT3_SOP_SCHEMA",
    "SOP_READY",
    "SOP_MISSING_DATA",
    "SOP_REQUIRES_APPROVAL",
    "SOP_CONFLICT",
    "_build_messages",
    "_build_auxiliary_repair_messages",
    "_normalize_sop",
    "apply_agent3_auxiliary_repair",
    "missing_agent3_sop_contract",
    "repairable_agent3_auxiliary_missing",
    "semantic_call_id",
]
