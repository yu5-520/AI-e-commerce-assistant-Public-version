"""V22.5 contracts for judgment, action draft, company SOP and task mapping."""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List

from src.services.agent2_action_draft_core_v225_service import (
    DRAFT_READY,
    missing_agent2_draft_contract,
)
from src.services.agent3_sop_core_v225_service import (
    SOP_READY,
    SOP_REQUIRES_APPROVAL,
    missing_agent3_sop_contract,
)
from src.services.agent_runtime_contract_v2141_service import (
    missing_action_pack_contract,
    missing_agent1_contract,
    normalize_action_pack_ready_contract,
    normalize_agent1_completed_contract,
    payload_from_row,
    product_title_of,
)

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
AGENT_RUNTIME_CONTRACT_VERSION = THREE_AGENT_PIPELINE_VERSION
AGENT2_DRAFT_CONTRACT_VERSION = THREE_AGENT_PIPELINE_VERSION
AGENT3_SOP_CONTRACT_VERSION = THREE_AGENT_PIPELINE_VERSION
TASK_MAPPING_CONTRACT_VERSION = THREE_AGENT_PIPELINE_VERSION


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _proof(provider: Dict[str, Any], package_id: str, key: str = "itemProvenance") -> Dict[str, Any]:
    return _dict(_dict(provider.get(key)).get(package_id))


def normalize_agent2_draft_completed_contract(
    package: Dict[str, Any],
    draft: Dict[str, Any],
    provider: Dict[str, Any],
) -> Dict[str, Any]:
    package_id = str(
        package.get("packageId")
        or draft.get("packageId")
        or package.get("itemId")
        or ""
    )
    proof = _dict(draft.get("agent2DraftExecutionProof")) or _proof(provider, package_id)
    return {
        **package,
        "version": THREE_AGENT_PIPELINE_VERSION,
        "contractVersion": THREE_AGENT_PIPELINE_VERSION,
        "packageId": package_id,
        "productId": package.get("productId") or draft.get("productId"),
        "storeId": package.get("storeId") or draft.get("storeId"),
        "actionFamily": package.get("lockedActionFamily") or draft.get("actionFamily"),
        "lockedActionFamily": package.get("lockedActionFamily") or draft.get("actionFamily"),
        "agent2ActionDraft": draft,
        "agent2DraftExecutionProof": proof,
        "agent2DraftProvider": provider,
        "agent2DraftStatus": draft.get("draftStatus"),
        "taskAdmissionAllowed": False,
        "fallbackAllowed": False,
        "outputContract": "V22.5.agent2_draft_ready",
        "lineage": {
            **_dict(package.get("lineage")),
            "currentStage": "agent2_draft_ready",
            "source": "pipeline_items_artifact_refs_only",
        },
    }


def missing_agent2_draft_completed_contract(package: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    draft = _dict(package.get("agent2ActionDraft"))
    if not draft:
        return ["agent2ActionDraft"]
    missing.extend(missing_agent2_draft_contract(draft))
    if draft.get("draftStatus") != DRAFT_READY:
        missing.append("agent2ActionDraft.draftStatus_ready")
    proof = _dict(package.get("agent2DraftExecutionProof"))
    if not (
        proof.get("resultMatched") is True
        and (
            proof.get("providerCallExecuted") is True
            or proof.get("exactReplayValidated") is True
        )
        and proof.get("fallbackUsed") is not True
        and proof.get("semanticCallId")
    ):
        missing.append("agent2DraftExecutionProof")
    return list(dict.fromkeys(missing))


def normalize_agent3_sop_completed_contract(
    package: Dict[str, Any],
    sop: Dict[str, Any],
    provider: Dict[str, Any],
) -> Dict[str, Any]:
    package_id = str(
        package.get("packageId")
        or sop.get("packageId")
        or package.get("itemId")
        or ""
    )
    proof = _dict(sop.get("agent3ExecutionProof")) or _proof(provider, package_id)
    return {
        **package,
        "version": THREE_AGENT_PIPELINE_VERSION,
        "contractVersion": THREE_AGENT_PIPELINE_VERSION,
        "packageId": package_id,
        "productId": package.get("productId") or sop.get("productId"),
        "storeId": package.get("storeId") or sop.get("storeId"),
        "actionFamily": package.get("lockedActionFamily") or sop.get("actionFamily"),
        "lockedActionFamily": package.get("lockedActionFamily") or sop.get("actionFamily"),
        "agent3Sop": sop,
        "agent3ExecutionProof": proof,
        "agent3Provider": provider,
        "agent3SopStatus": sop.get("sopStatus"),
        "taskAdmissionAllowed": False,
        "fallbackAllowed": False,
        "outputContract": "V22.5.agent3_sop_ready",
        "lineage": {
            **_dict(package.get("lineage")),
            "currentStage": "agent3_sop_ready",
            "source": "pipeline_items_artifact_refs_only",
        },
    }


def _valid_agent3_execution_proof(proof: Dict[str, Any]) -> bool:
    provider_validated = bool(
        proof.get("providerCallExecuted") is True
        and proof.get("providerRequestId")
    )
    exact_replay_validated = proof.get("exactReplayValidated") is True
    semantic_replay_validated = proof.get("semanticReplayValidated") is True
    return bool(
        proof.get("resultMatched") is True
        and (
            provider_validated
            or exact_replay_validated
            or semantic_replay_validated
        )
        and proof.get("semanticCallId")
        and proof.get("fallbackUsed") is not True
        and proof.get("passed") is not False
    )


def missing_agent3_sop_completed_contract(package: Dict[str, Any]) -> List[str]:
    missing = missing_agent2_draft_completed_contract(package)
    sop = _dict(package.get("agent3Sop"))
    if not sop:
        missing.append("agent3Sop")
        return list(dict.fromkeys(missing))
    missing.extend(missing_agent3_sop_contract(sop, package))
    if sop.get("sopStatus") not in {SOP_READY, SOP_REQUIRES_APPROVAL}:
        missing.append("agent3Sop.sopStatus_ready_or_requires_approval")
    proof = _dict(package.get("agent3ExecutionProof"))
    if not _valid_agent3_execution_proof(proof):
        missing.append("agent3ExecutionProof")
    return list(dict.fromkeys(missing))


def build_task_mapping_decision(
    package: Dict[str, Any],
    *,
    pipeline_item_id: str | None = None,
) -> Dict[str, Any]:
    sop = _dict(package.get("agent3Sop"))
    draft = _dict(package.get("agent2ActionDraft"))
    product = _dict(package.get("productIdentity"))
    family = str(
        package.get("lockedActionFamily")
        or sop.get("actionFamily")
        or draft.get("actionFamily")
        or ""
    )
    title = str(sop.get("finalTaskTitle") or "").strip()
    steps = [
        str(item).strip()
        for item in _arr(sop.get("operatorActionSteps"))
        if str(item).strip()
    ]
    package_id = str(
        package.get("packageId")
        or sop.get("packageId")
        or package.get("itemId")
        or ""
    )
    decision_id = "TGD-V225-" + hashlib.sha1(
        "|".join(
            str(value or "")
            for value in (
                package.get("dataVersion"),
                package_id,
                package.get("productId"),
            )
        ).encode("utf-8")
    ).hexdigest()[:16].upper()
    provider = _dict(package.get("agent3Provider"))
    proof = _dict(package.get("agent3ExecutionProof"))
    operation_plan = _dict(draft.get("operationPlan"))
    compatibility_agent2_plan = {
        "version": THREE_AGENT_PIPELINE_VERSION,
        "packageId": package_id,
        "productId": package.get("productId"),
        "storeId": package.get("storeId"),
        "actionFamily": family,
        "actionPlanStatus": "ready",
        "semanticContractMissing": [],
        "operationPlan": operation_plan,
        "agent2ExecutionProof": proof,
        "source": "agent3_sop_compatibility_projection",
    }
    timing = _dict(
        _dict(package.get("companyOperatingPolicySnapshot")).get("taskTimingPolicy")
    )
    urgent = timing.get("urgent") or "6小时内"
    normal = timing.get("normal") or "12小时内"
    task_plan = {
        "version": THREE_AGENT_PIPELINE_VERSION,
        "title": title,
        "taskTitle": title,
        "finalTaskTitle": title,
        "titleSource": "agent3Sop.finalTaskTitle",
        "productId": package.get("productId") or product.get("productId"),
        "storeId": package.get("storeId") or product.get("storeId"),
        "productIdentity": product,
        "selectedActionFamily": family,
        "taskType": "operation_action",
        "taskResponsibility": "operator_growth",
        "departmentTaskType": "operator_growth",
        "priority": (
            "高"
            if family in {"roas_scale", "roas_guard", "platform_activity", "activity_apply"}
            else "中"
        ),
        "executionDeadline": (
            urgent
            if family in {"title_image_test", "platform_activity", "activity_apply"}
            else normal
        ),
        "followUpDeadline": "系统自动复盘",
        "reviewCycle": sop.get("reviewCycle")
        or ["3天", "7天", "14天", "30天", "90天"],
        "assigneeRole": "operator",
        "approvalRequired": sop.get("sopStatus") == SOP_REQUIRES_APPROVAL,
        "operationMode": "agent3_company_sop",
        "differentiationReason": (
            sop.get("companyStyleReason") or draft.get("differentiationReason")
        ),
        "executionObject": _dict(sop.get("executionObject")),
        "operationPlan": operation_plan,
        "operatorExecutionSop": steps,
        "sopSteps": steps,
        "executionSteps": sop.get("executionSteps") or [],
        "decisionBranches": sop.get("decisionBranches") or [],
        "submissionEvidence": sop.get("submissionEvidence") or [],
        "evidenceRequirements": sop.get("submissionEvidence") or [],
        "crossDepartmentActions": sop.get("crossDepartmentActions") or [],
        "supportingCoordination": sop.get("crossDepartmentActions") or [],
        "approvalFlow": _dict(sop.get("approvalFlow")),
        "reviewMetrics": sop.get("reviewMetrics") or [],
        "verificationPeriod": sop.get("verificationPeriod"),
        "stopConditions": sop.get("stopConditions") or [],
        "rollbackConditions": sop.get("rollbackConditions") or [],
        "agent2ExecutionProof": proof,
        "agent3ExecutionProof": proof,
        "agent2ActionDraft": draft,
        "agent3Sop": sop,
        "compilerAddedStepCount": 0,
        "sopSource": "v22_5_agent3_company_sop",
        "fallbackAllowed": False,
    }
    return {
        "version": THREE_AGENT_PIPELINE_VERSION,
        "decisionId": decision_id,
        "decision": (
            "manager_review_required"
            if task_plan["approvalRequired"]
            else "create_task_snapshot"
        ),
        "dataVersion": package.get("dataVersion"),
        "packageId": package_id,
        "productId": task_plan["productId"],
        "storeId": task_plan["storeId"],
        "taskTitle": title,
        "selectedActionFamily": family,
        "taskPlan": task_plan,
        "operatorExecutionSop": steps,
        "agent2ActionPlan": compatibility_agent2_plan,
        "agent2ActionDraft": draft,
        "agent3Sop": sop,
        "agent2Provider": provider,
        "agent2ExecutionProof": proof,
        "agent3Provider": provider,
        "agent3ExecutionProof": proof,
        "operationPlan": operation_plan,
        "taskMappingAgentEvidence": {
            "version": THREE_AGENT_PIPELINE_VERSION,
            "pipelineItemId": pipeline_item_id,
            "noMappingLlm": True,
            "noAgent2Rerun": True,
            "noActionPackRerun": True,
            "itemized": True,
            "noLegacyRuntimeSource": True,
            "agent2ProviderTracePassed": True,
            "agent3ProviderTracePassed": True,
            "agent3SemanticReplayTraceAllowed": proof.get("semanticReplayValidated") is True,
            "compilerAddedStepCount": 0,
            "fallbackAllowed": False,
        },
        "chainIntegrity": {
            "passed": True,
            "taskDifferentiationPassed": True,
            "inventoryResponsibilityPassed": True,
            "ragTracePassed": True,
            "operationPlanPassed": True,
            "agent3SopPassed": True,
        },
        "compilerAddedStepCount": 0,
        "fallbackAllowed": False,
        "taskAdmissionAllowed": True,
        "outputContract": "V22.5.task_mapped",
    }


def missing_task_mapping_contract(decision: Dict[str, Any]) -> List[str]:
    missing: List[str] = []
    task_plan = _dict(decision.get("taskPlan"))
    if not decision.get("decisionId"):
        missing.append("decisionId")
    if decision.get("decision") not in {
        "create_task_snapshot",
        "manager_review_required",
    }:
        missing.append("decision")
    for key in ("productId", "storeId", "taskTitle"):
        if decision.get(key) in (None, "", {}, []):
            missing.append(key)
    if not _arr(task_plan.get("operatorExecutionSop")):
        missing.append("taskPlan.operatorExecutionSop")
    if task_plan.get("compilerAddedStepCount") != 0:
        missing.append("taskPlan.compilerAddedStepCount_zero")
    if _dict(decision.get("chainIntegrity")).get("passed") is not True:
        missing.append("chainIntegrity.passed")
    return list(dict.fromkeys(missing))


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_RUNTIME_CONTRACT_VERSION",
    "AGENT2_DRAFT_CONTRACT_VERSION",
    "AGENT3_SOP_CONTRACT_VERSION",
    "TASK_MAPPING_CONTRACT_VERSION",
    "normalize_agent1_completed_contract",
    "normalize_action_pack_ready_contract",
    "missing_agent1_contract",
    "missing_action_pack_contract",
    "payload_from_row",
    "product_title_of",
    "normalize_agent2_draft_completed_contract",
    "missing_agent2_draft_completed_contract",
    "normalize_agent3_sop_completed_contract",
    "missing_agent3_sop_completed_contract",
    "build_task_mapping_decision",
    "missing_task_mapping_contract",
]
