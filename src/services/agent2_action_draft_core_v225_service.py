"""V22.5.5 Agent2 action-draft core.

Agent2 receives one evidence-backed execution lock from Agent1/Action Matrix. It may
add vertical, platform and parameter detail, but it cannot reopen causal diagnosis,
change the primary action, add a second direct target or turn coordination into an
operator-owned action.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from src.services.action_plan_ir_v214_service import (
    ROAS_FAMILIES,
    missing_action_plan_ir,
    normalize_action_plan_ir,
)
from src.services.agent_execution_lock_v2255_service import (
    execution_handoff,
    execution_lock_from,
    missing_execution_lock,
)

THREE_AGENT_PIPELINE_VERSION = "22.5.5"
AGENT2_ACTION_DRAFT_CORE_VERSION = THREE_AGENT_PIPELINE_VERSION
AGENT2_DRAFT_SCHEMA = "agent2.action_draft.v1"

DRAFT_READY = "draft_ready"
DRAFT_MISSING_DATA = "draft_missing_data"
DRAFT_CONFLICT = "draft_conflict"
DRAFT_REJECTED = "draft_rejected"
DRAFT_STATUSES = {DRAFT_READY, DRAFT_MISSING_DATA, DRAFT_CONFLICT, DRAFT_REJECTED}

ROAS_DRAFT_KEY = "operationPlan"
FAMILY_DRAFT_KEYS = {
    "title_image_test": "creativeDraft",
    "roas_scale": ROAS_DRAFT_KEY,
    "roas_guard": ROAS_DRAFT_KEY,
    "platform_activity": "activityDraft",
    "activity_apply": "activityDraft",
    "conversion_repair": "repairDraft",
    "service_repair": "repairDraft",
    "similar_product_test": "experimentDraft",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 800) -> str:
    if isinstance(value, dict):
        value = value.get("summary") or value.get("text") or value.get("action") or value.get("id")
    return " ".join(str(value or "").split())[:limit]


def _reason_list(value: Any, *, limit: int = 800) -> List[str]:
    if isinstance(value, list):
        values = value
    elif value in (None, "", {}, []):
        values = []
    else:
        values = [value]
    return list(
        dict.fromkeys(
            text
            for text in (_text(item, limit) for item in values)
            if text
        )
    )


def selected_family(package: Dict[str, Any]) -> str:
    lock = execution_lock_from(package)
    matrix = _dict(package.get("matrixDispatch"))
    judgment = _dict(package.get("agent1OperatingJudgment"))
    family_lock = _dict(judgment.get("actionFamilyLock"))
    return _text(
        lock.get("selectedActionFamily")
        or package.get("lockedActionFamily")
        or package.get("actionFamily")
        or matrix.get("selectedActionFamily")
        or family_lock.get("selectedActionFamily"),
        100,
    )


AGENT2_GENERATION_COMPILER_VERSION = "23.2.8"
AGENT2_FAMILY_PAYLOAD_SCHEMA = "agent2.family_payload.v1"


def _family_contract(family: str) -> Dict[str, Any]:
    if family == "title_image_test":
        return {
            "contractId": "title_image_test.v1",
            "outputField": "familyPayload",
            "payloadType": "creativeDraft",
            "minDirections": 2,
            "maxDirections": 5,
            "requiredDirectionFields": [
                "fullTitle",
                "mainImageStructure",
                "testFocusWords",
                "platformFit",
                "differenceFromOthers",
            ],
        }
    if family in ROAS_FAMILIES:
        return {
            "contractId": f"{family}.v1",
            "outputField": "familyPayload",
            "payloadType": "operationPlan",
            "minimumOperations": 1,
            "requiredOperationFields": [
                "operationType",
                "target",
                "direction",
                "currentValue",
                "targetValue",
                "rollback",
            ],
        }
    if family in {"platform_activity", "activity_apply"}:
        return {
            "contractId": f"{family}.v1",
            "outputField": "familyPayload",
            "payloadType": "activityDraft",
            "requiredFields": [
                "activityType",
                "thresholdRange",
                "benefitRange",
                "marginBoundary",
                "acceptanceConditions",
                "exitConditions",
            ],
        }
    if family in {"conversion_repair", "service_repair"}:
        return {
            "contractId": f"{family}.v1",
            "outputField": "familyPayload",
            "payloadType": "repairDraft",
            "requiredFields": [
                "repairDetail",
                "parameterRanges",
                "validationMetrics",
                "riskBoundaries",
            ],
        }
    if family == "similar_product_test":
        return {
            "contractId": "similar_product_test.v1",
            "outputField": "familyPayload",
            "payloadType": "experimentDraft",
            "requiredFields": [
                "comparisonTarget",
                "singleVariable",
                "experimentDirection",
                "parameterRanges",
                "validationMetrics",
                "stopBoundary",
            ],
        }
    raise ValueError("unsupported_locked_action_family")


def _repair_context(package: Dict[str, Any]) -> Dict[str, Any]:
    extensions = _dict(package.get("diagnosticExtensions"))
    return _dict(extensions.get("agent2ContractRepair"))


def _compact_package(package: Dict[str, Any]) -> Dict[str, Any]:
    family = selected_family(package)
    handoff = execution_handoff(package)
    lock = _dict(handoff.get("executionLock"))
    missing = missing_execution_lock(lock)
    if missing:
        raise ValueError("agent2_execution_lock_invalid:" + ",".join(missing))

    action_context = {
        "productTitle": package.get("productTitle") or package.get("title"),
        "productIdentity": _dict(package.get("productIdentity")),
        "decisiveFacts": lock.get("decisiveFacts") or [],
        "recentFiveOrLatestFacts": _arr(package.get("recentFiveOrLatestFacts")),
        "actionParameterPack": _dict(package.get("actionParameterPack")),
        "verticalActionRag": _dict(package.get("verticalActionRag")),
    }
    action_context = {
        key: value
        for key, value in action_context.items()
        if value not in (None, "", [], {})
    }

    result = {
        "packageId": package.get("packageId") or package.get("itemId"),
        "productId": package.get("productId"),
        "storeId": package.get("storeId"),
        "immutableContext": {
            "primaryProblemNode": lock.get("primaryProblemNode"),
            "primaryAction": lock.get("primaryAction"),
            "primaryExecutionTarget": lock.get("primaryExecutionTarget"),
            "primaryOwner": lock.get("primaryOwner"),
            "supportingCoordination": lock.get("supportingCoordination") or [],
        },
        "actionContext": action_context,
        "familyContract": _family_contract(family),
    }
    repair = _repair_context(package)
    if repair:
        result["repairContext"] = repair
    return result


def _draft_instruction(family: str) -> str:
    if family == "title_image_test":
        return (
            "只生成familyPayload。familyPayload必须包含directions，数量2到5。"
            "每个方向必须包含fullTitle、mainImageStructure、testFocusWords、"
            "platformFit、differenceFromOthers。方向必须是同一主动作下的不同候选，"
            "不得生成多个主动作。"
        )
    if family in ROAS_FAMILIES:
        return (
            "只生成familyPayload。familyPayload是operationPlan，operations至少1项，"
            "每项围绕同一个锁定动作和对象，写operationType、target、direction、"
            "currentValue、targetValue、参数范围和rollback。"
        )
    if family in {"platform_activity", "activity_apply"}:
        return (
            "只生成familyPayload。familyPayload是activityDraft，写活动类型、门槛范围、"
            "权益范围、毛利边界、承接条件、退出条件和需要公司确认的字段。"
        )
    if family in {"conversion_repair", "service_repair"}:
        return (
            "只生成familyPayload。familyPayload是repairDraft，写repairDetail、"
            "parameterRanges、validationMetrics、riskBoundaries和supportingCoordination。"
        )
    if family == "similar_product_test":
        return (
            "只生成familyPayload。familyPayload是experimentDraft，写对照对象、唯一变量、"
            "实验方向、参数范围、验证指标和停止边界。"
        )
    raise ValueError("unsupported_locked_action_family")


def _build_messages(
    data_version: str | None,
    packages: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    families = {selected_family(item) for item in packages}
    if len(families) != 1:
        raise ValueError("v2328_one_locked_action_family_required_per_call")
    family = next(iter(families))
    payload = {
        "dataVersion": data_version,
        "version": AGENT2_GENERATION_COMPILER_VERSION,
        "schema": AGENT2_FAMILY_PAYLOAD_SCHEMA,
        "lockedActionFamily": family,
        "packages": [_compact_package(item) for item in packages],
    }
    prompt = (
        "你是Agent2动作正文生成器。系统已经完成经营判断、动作族分类、权限校验和执行锁定。"
        "immutableContext只供理解，不得复制、改写或重新判断。系统会自动注入问题、动作族、"
        "主动作、执行对象、责任人、权限、状态和执行身份。"
        "每个plan只返回packageId，以及四种结果通道中的一种："
        "familyPayload、非空missingData、非空conflictReasons、非空rejectedReason。"
        "禁止返回draftStatus；系统将依据内容自动计算状态。"
        "禁止返回primaryProblemNode、primaryAction、primaryExecutionTarget、primaryOwner、"
        "executionTargets、permissionBoundary、生命周期字段或最终SOP。"
        + _draft_instruction(family)
        + "若存在repairContext，只修复其中列出的缺失项，不得改变既有有效内容。"
        "只返回严格JSON对象，顶层plans数组。"
    )
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ], payload


def _normalize_status(raw: Dict[str, Any]) -> str:
    """Compatibility parser only; model-declared status is never authoritative."""

    value = _text(raw.get("draftStatus") or raw.get("actionPlanStatus"), 80).lower()
    aliases = {
        "ready": DRAFT_READY,
        "pending_execution": DRAFT_READY,
        "action_plan_missing_data": DRAFT_MISSING_DATA,
        "missing_data": DRAFT_MISSING_DATA,
        "conflict_requires_rejudgment": DRAFT_CONFLICT,
        "blocked": DRAFT_CONFLICT,
        "rejected": DRAFT_REJECTED,
    }
    return aliases.get(value, value if value in DRAFT_STATUSES else "")


def _normalize_creative(raw: Dict[str, Any]) -> Dict[str, Any]:
    source = _dict(raw.get("familyPayload") or raw.get("creativeDraft") or raw.get("creativeDraft"))
    groups = []
    for item in _arr(source.get("directions") or source.get("groups"))[:5]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("fullTitle"), 260)
        image = _dict(item.get("mainImageStructure"))
        focus = [
            _text(value, 100)
            for value in _arr(item.get("testFocusWords"))
            if _text(value, 100)
        ]
        platform_fit = _text(item.get("platformFit"), 800)
        difference = _text(item.get("differenceFromOthers"), 800)
        if title and image and focus and platform_fit and difference:
            groups.append(
                {
                    **item,
                    "fullTitle": title,
                    "mainImageStructure": image,
                    "testFocusWords": focus,
                    "platformFit": platform_fit,
                    "differenceFromOthers": difference,
                }
            )
    return {**source, "directions": groups, "directionCount": len(groups)} if source or groups else {}


def _normalize_repair(raw: Dict[str, Any], lock: Dict[str, Any]) -> Dict[str, Any]:
    source = _dict(raw.get("familyPayload") or raw.get("repairDraft") or raw.get("conversionRepairPlan"))
    return {
        key: value
        for key, value in {
            **source,
            "primaryProblemNode": lock.get("primaryProblemNode"),
            "primaryAction": lock.get("primaryAction"),
            "primaryExecutionTarget": lock.get("primaryExecutionTarget"),
            "repairDetail": _text(source.get("repairDetail") or raw.get("repairDetail"), 1600),
            "parameterRanges": _dict(source.get("parameterRanges")),
            "validationMetrics": _arr(source.get("validationMetrics")),
            "riskBoundaries": _arr(source.get("riskBoundaries")),
            "supportingCoordination": _arr(source.get("supportingCoordination")) or _arr(lock.get("supportingCoordination")),
            "missingData": _arr(source.get("missingData")),
        }.items()
        if value not in (None, "", [], {}) or key in {"missingData", "supportingCoordination"}
    }


def _normalize_family_draft(raw: Dict[str, Any], family: str, lock: Dict[str, Any]) -> Dict[str, Any]:
    payload = _dict(raw.get("familyPayload"))
    if family in ROAS_FAMILIES:
        return normalize_action_plan_ir(
            {"operationPlan": payload} if payload else raw,
            family,
        )
    if family == "title_image_test":
        return _normalize_creative(raw)
    if family in {"platform_activity", "activity_apply"}:
        return payload or _dict(raw.get("activityDraft") or raw.get("activityPlan"))
    if family in {"conversion_repair", "service_repair"}:
        return _normalize_repair(raw, lock)
    if family == "similar_product_test":
        return payload or _dict(raw.get("experimentDraft") or raw.get("similarProductPlan"))
    return {}


def _title_image_contract_missing(raw: Dict[str, Any], normalized: Dict[str, Any]) -> List[str]:
    source = _dict(raw.get("familyPayload") or raw.get("creativeDraft") or raw.get("creativeDraft"))
    missing: List[str] = []
    if not source:
        return ["agent2_title_image_creative_draft_missing"]
    raw_directions = [item for item in _arr(source.get("directions") or source.get("groups")) if isinstance(item, dict)]
    for item in raw_directions[:5]:
        if not _text(item.get("fullTitle"), 260):
            missing.append("agent2_title_image_full_title_missing")
        if not _dict(item.get("mainImageStructure")):
            missing.append("agent2_title_image_main_image_structure_missing")
        if not [value for value in _arr(item.get("testFocusWords")) if _text(value, 100)]:
            missing.append("agent2_title_image_test_focus_words_missing")
        if not _text(item.get("platformFit"), 800):
            missing.append("agent2_title_image_platform_fit_missing")
        if not _text(item.get("differenceFromOthers"), 800):
            missing.append("agent2_title_image_difference_missing")
    if len(_arr(normalized.get("directions"))) < 2:
        missing.append("agent2_title_image_directions_insufficient")
    return list(dict.fromkeys(missing))


def _family_contract_missing(raw: Dict[str, Any], family: str, family_draft: Dict[str, Any]) -> List[str]:
    if family == "title_image_test":
        return _title_image_contract_missing(raw, family_draft)
    if family in ROAS_FAMILIES:
        values = missing_action_plan_ir({"operationPlan": family_draft}, family)
        return [f"agent2_{family}_{value}" for value in values]
    if not family_draft:
        return [f"agent2_{family}_family_payload_missing"]
    return []


def _system_list(package: Dict[str, Any], family_draft: Dict[str, Any], *keys: str) -> List[Any]:
    pack = _dict(package.get("actionParameterPack"))
    for source in (family_draft, pack, package):
        for key in keys:
            values = _arr(_dict(source).get(key))
            if values:
                return [value for value in values if value not in (None, "", {}, [])]
    return []


def _system_dict(package: Dict[str, Any], family_draft: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    pack = _dict(package.get("actionParameterPack"))
    for source in (family_draft, pack, package):
        for key in keys:
            value = _dict(_dict(source).get(key))
            if value:
                return value
    return {}


def _differentiation_reason(family: str, family_draft: Dict[str, Any]) -> str:
    if family == "title_image_test":
        values = [
            _text(item.get("differenceFromOthers"), 400)
            for item in _arr(family_draft.get("directions"))
            if isinstance(item, dict) and _text(item.get("differenceFromOthers"), 400)
        ]
        return "；".join(values)[:1200]
    return _text(family_draft.get("differentiationReason"), 1200)


def _draft_missing(draft: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    status = draft.get("draftStatus")
    if status not in DRAFT_STATUSES:
        missing.append(
            "agent2_outcome_channel_conflict"
            if draft.get("outcomeChannelConflict") is True
            else "agent2_output_channel_missing"
        )
    for key in ("packageId", "productId", "storeId", "actionFamily"):
        if draft.get(key) in (None, "", {}, []):
            missing.append(key)
    for key in ("primaryProblemNode", "primaryAction", "primaryExecutionTarget", "primaryOwner"):
        if draft.get(key) in (None, "", {}, []):
            missing.append(f"agent2_system_lock_missing:{key}")
    targets = [item for item in _arr(draft.get("executionTargets")) if item not in (None, "", {}, [])]
    if len(targets) != 1:
        missing.append("agent2_system_execution_target_invalid")

    if status == DRAFT_MISSING_DATA and not _reason_list(draft.get("missingData")):
        missing.append("agent2_missing_data_reason_missing")
    if status == DRAFT_CONFLICT and not _reason_list(draft.get("conflictReasons")):
        missing.append("agent2_conflict_reason_missing")
    if status == DRAFT_REJECTED and not _text(draft.get("rejectedReason"), 1200):
        missing.append("agent2_rejected_reason_missing")
    if status == DRAFT_READY:
        missing.extend(str(value) for value in _arr(draft.get("familyContractMissing")) if str(value))
    elif status not in DRAFT_STATUSES:
        missing.extend(str(value) for value in _arr(draft.get("familyContractMissing")) if str(value))
    return list(dict.fromkeys(missing))


def repairable_agent2_contract_missing(missing: List[str]) -> bool:
    values = [str(value) for value in missing if str(value)]
    family_values = [
        value
        for value in values
        if value.startswith("agent2_title_image_")
        or value.startswith("agent2_roas_")
        or value.endswith("_family_payload_missing")
    ]
    non_family = [
        value
        for value in values
        if value not in family_values and value != "agent2_output_channel_missing"
    ]
    return bool(family_values) and not non_family


def _normalize_draft(
    raw: Dict[str, Any],
    package: Dict[str, Any],
    proof: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    family = selected_family(package)
    lock = execution_lock_from(package)
    lock_missing = missing_execution_lock(lock)
    if lock_missing:
        raise ValueError("agent2_execution_lock_invalid:" + ",".join(lock_missing))

    family_key = FAMILY_DRAFT_KEYS.get(family)
    family_draft = _normalize_family_draft(raw, family, lock)
    family_missing = _family_contract_missing(raw, family, family_draft)
    missing_data = _reason_list(raw.get("missingData"))
    conflict_reasons = _reason_list(
        raw.get("conflictReasons") or raw.get("conflicts") or raw.get("conflictReason")
    )
    rejected_reason = _text(
        raw.get("rejectedReason") or raw.get("rejectionReason") or raw.get("rejectReason"),
        1200,
    )
    has_family_payload = bool(family_draft) and not family_missing
    channels = [
        name
        for name, active in (
            ("familyPayload", has_family_payload),
            ("missingData", bool(missing_data)),
            ("conflictReasons", bool(conflict_reasons)),
            ("rejectedReason", bool(rejected_reason)),
        )
        if active
    ]
    channel_conflict = len(channels) > 1
    status = (
        DRAFT_READY
        if channels == ["familyPayload"]
        else DRAFT_MISSING_DATA
        if channels == ["missingData"]
        else DRAFT_CONFLICT
        if channels == ["conflictReasons"]
        else DRAFT_REJECTED
        if channels == ["rejectedReason"]
        else ""
    )

    locked_target = _dict(lock.get("primaryExecutionTarget"))
    handoff = execution_handoff(package)
    permission_boundary = (
        _dict(handoff.get("permissionBoundary"))
        or _dict(package.get("permissionBoundary"))
        or _dict(_dict(package.get("actionParameterPack")).get("permissionBounds"))
    )
    draft: Dict[str, Any] = {
        "version": AGENT2_GENERATION_COMPILER_VERSION,
        "schema": AGENT2_DRAFT_SCHEMA,
        "generationCompilerVersion": AGENT2_GENERATION_COMPILER_VERSION,
        "familyContractId": _family_contract(family).get("contractId"),
        "packageId": package.get("packageId") or package.get("itemId"),
        "productId": package.get("productId"),
        "storeId": package.get("storeId"),
        "actionFamily": family,
        "lockedActionFamily": family,
        "draftStatus": status,
        "systemComputedDraftStatus": True,
        "modelDeclaredDraftStatus": _normalize_status(raw),
        "outcomeChannel": channels[0] if len(channels) == 1 else None,
        "outcomeChannelConflict": channel_conflict,
        "primaryProblemNode": lock.get("primaryProblemNode"),
        "primaryAction": lock.get("primaryAction"),
        "primaryExecutionTarget": locked_target,
        "primaryOwner": lock.get("primaryOwner"),
        "problemNode": lock.get("primaryProblemNode"),
        "actionIntent": lock.get("primaryAction"),
        "executionTargets": [locked_target],
        "supportingCoordination": _arr(lock.get("supportingCoordination")),
        "forbiddenActionDomains": _arr(lock.get("forbiddenActionDomains")),
        "parameterRanges": _system_dict(package, family_draft, "parameterRanges", "parameterBounds"),
        "permissionBoundary": permission_boundary,
        "riskBoundaries": _system_list(package, family_draft, "riskBoundaries", "riskBoundary", "stopConditions"),
        "validationMetrics": _system_list(package, family_draft, "validationMetrics", "reviewMetrics"),
        "requiredEvidence": _system_list(package, family_draft, "requiredEvidence", "evidenceRequirements"),
        "missingData": missing_data,
        "conflictReasons": conflict_reasons,
        "rejectedReason": rejected_reason,
        "differentiationReason": _differentiation_reason(family, family_draft),
        "familyPayload": family_draft,
        "familyContractMissing": family_missing,
        "ragUsedCaseIds": [str(item) for item in _arr(_dict(package.get("verticalActionRag")).get("approvedCaseIds"))],
        "ragRejectedCaseIds": [],
        "ragApplicationReason": _text(_dict(package.get("verticalActionRag")).get("agentInstruction"), 800),
        "agent2DraftExecutionProof": proof or {},
        "diagnosticRejudgmentAllowed": False,
        "fallbackAllowed": False,
        "finalSopGenerated": False,
        "systemOwnedFields": [
            "primaryProblemNode",
            "actionFamily",
            "primaryAction",
            "primaryExecutionTarget",
            "primaryOwner",
            "executionTargets",
            "permissionBoundary",
            "draftStatus",
            "executionIdentity",
        ],
    }
    if family_key:
        draft[family_key] = family_draft
    draft["semanticContractMissing"] = _draft_missing(draft)
    return {
        key: value
        for key, value in draft.items()
        if value not in (None, "", [], {})
        or key
        in {
            "draftStatus",
            "missingData",
            "conflictReasons",
            "rejectedReason",
            "supportingCoordination",
            "familyPayload",
            "familyContractMissing",
            "semanticContractMissing",
        }
    }


def missing_agent2_draft_contract(draft: Dict[str, Any]) -> List[str]:
    return _draft_missing(_dict(draft))


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT2_ACTION_DRAFT_CORE_VERSION",
    "AGENT2_DRAFT_SCHEMA",
    "AGENT2_GENERATION_COMPILER_VERSION",
    "AGENT2_FAMILY_PAYLOAD_SCHEMA",
    "DRAFT_READY",
    "DRAFT_MISSING_DATA",
    "DRAFT_CONFLICT",
    "DRAFT_REJECTED",
    "FAMILY_DRAFT_KEYS",
    "selected_family",
    "missing_agent2_draft_contract",
    "repairable_agent2_contract_missing",
    "_build_messages",
    "_normalize_draft",
]
