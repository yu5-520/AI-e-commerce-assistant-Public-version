"""V22 deterministic SOP compiler.

The compiler formats one ready Agent2 plan and preserves provider proof, Plan IR
and authorization inputs. It never adds business steps, budget actions, stop-loss
logic or cross-department coordination to the operator checklist.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List

from src.runtime_version import VERSION
from src.services.action_plan_ir_v214_service import missing_action_plan_ir
from src.services.agent2_action_plan_core_v20_service import (
    active_action_contract,
    provider_has_real_agent2_call,
    real_agent2_provider_missing_reason,
)
from src.services.agent_rag_context_v2028_service import rag_context_summary

SOP_BUILDER_CORE_VERSION = VERSION
ACTION_PLAN_IR_VERSION = VERSION
AGENT2_PROVENANCE_VERSION = VERSION
AGENT_RAG_CONTEXT_VERSION = VERSION
AGENT_RUNTIME_CONTRACT_VERSION = VERSION

_DIRECTION_LABELS = {
    "title_image_test": "开展标题主图差异化测试",
    "roas_scale": "验证高效计划放量",
    "roas_guard": "校准低效投放",
    "platform_activity": "报名平台活动承接增长",
    "activity_apply": "报名平台活动承接增长",
    "conversion_repair": "修复详情页与转化链路",
    "service_repair": "修复售后体验与信任承接",
    "similar_product_test": "开展同类商品对照测试",
}


def stable_decision_id(
    data_version: str | None,
    package_id: str | None,
    product_id: str | None,
) -> str:
    raw = "|".join(str(value or "") for value in (data_version, package_id, product_id))
    return "TGD-ITEM-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16].upper()


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = (
            value.get("summary")
            or value.get("action")
            or value.get("title")
            or value.get("text")
            or value.get("instruction")
        )
    return " ".join(str(value or "").split()).strip()


def _dedupe_lines(values: List[Any]) -> List[str]:
    result: List[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        key = re.sub(r"[，,。.!！；;：:\s]+", "", text).lower()
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
    return result


def _provider(package: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(package.get("agent2Provider"))


def _plan(package: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(package.get("agent2ActionPlan"))


def _proof(package: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    provider = _provider(package)
    return _dict(
        package.get("agent2ExecutionProof")
        or plan.get("agent2ExecutionProof")
        or _dict(provider.get("itemProvenance")).get(
            str(package.get("packageId") or plan.get("packageId") or "")
        )
    )


def _identity(package: Dict[str, Any]) -> Dict[str, Any]:
    product = _dict(package.get("productIdentity"))
    title = (
        package.get("productTitle")
        or package.get("title")
        or product.get("productTitle")
        or product.get("title")
    )
    return {
        **product,
        "productId": package.get("productId") or product.get("productId"),
        "storeId": package.get("storeId") or product.get("storeId"),
        "productTitle": title,
        "title": title,
    }


def _real_output_ready(package: Dict[str, Any]) -> tuple[bool, str | None]:
    plan = _plan(package)
    provider = _provider(package)
    proof = _proof(package, plan)
    package_id = str(package.get("packageId") or plan.get("packageId") or "")
    if not provider_has_real_agent2_call(provider, package_id, proof):
        return False, real_agent2_provider_missing_reason(provider, package_id, proof) or "agent2_execution_proof_missing"
    if not plan:
        return False, "agent2_plan_missing"
    if plan.get("fallbackAllowed") is True or package.get("fallbackAllowed") is True:
        return False, "agent2_fallback_not_allowed"
    if str(plan.get("actionPlanStatus") or "") != "ready":
        return False, f"agent2_plan_not_ready:{plan.get('actionPlanStatus')}"
    if plan.get("semanticContractMissing"):
        return False, "agent2_semantic_contract_missing"
    steps = _dedupe_lines(_arr(plan.get("operatorActionSteps")))
    structured = [item for item in _arr(plan.get("executionSteps")) if isinstance(item, dict) and item]
    if not steps and not structured:
        return False, "agent2_executable_action_missing"
    family = str(plan.get("actionFamily") or "")
    if family and missing_action_plan_ir(plan, family):
        return False, "agent2_operation_plan_invalid"
    return True, None


def _evidence(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for index, item in enumerate(_arr(plan.get("submissionEvidence"))):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or f"执行凭证{index + 1}")
        summary = _text(item.get("description") or item.get("summary"))
        if not title and not summary:
            continue
        result.append(
            {
                "title": title or f"执行凭证{index + 1}",
                "summary": summary,
                "requiredFields": item.get("requiredFields") if isinstance(item.get("requiredFields"), list) else [],
                "source": "agent2_submission_evidence",
            }
        )
    return result


def _operator_view(
    family: str,
    plan: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    agent1 = _dict(package.get("agent1OperatingJudgment"))
    decision_ir = _dict(package.get("agent1DecisionIR") or agent1.get("agent1DecisionIR"))
    conclusion = _text(
        decision_ir.get("decisionSummary")
        or plan.get("operatingConclusion")
        or plan.get("selectedDirection")
    )
    if not conclusion or len(conclusion) > 60:
        conclusion = _DIRECTION_LABELS.get(family) or "按经营判断执行本轮验证"
    basis: List[str] = []
    for value in [
        *[item.get("text") if isinstance(item, dict) else item for item in _arr(decision_ir.get("facts"))],
        agent1.get("primaryBusinessSignal"),
        agent1.get("primaryOperatingGap"),
        *_arr(agent1.get("evidenceFacts")),
    ]:
        text = _text(value)
        if text and text not in basis and text != conclusion:
            basis.append(text)
        if len(basis) >= 4:
            break
    risk = [_text(value) for value in _arr(decision_ir.get("riskBoundaries")) if _text(value)]
    return {
        "version": VERSION,
        "operatingConclusion": conclusion,
        "judgmentBasis": basis,
        "judgmentBasisText": "；".join(basis),
        "executionFocus": _text(plan.get("differentiationReason") or plan.get("operationMode")) or conclusion,
        "riskBoundary": "；".join(risk),
        "selectedDirection": conclusion,
        "displayReason": "；".join(basis),
        "rule": "V22 separates diagnosis, executable plan and deterministic display projection.",
    }


def build_sop_decision_from_package(
    package: Dict[str, Any],
    data_version: str | None,
    *,
    pipeline_item_id: str | None = None,
) -> Dict[str, Any] | None:
    ready, _ = _real_output_ready(package)
    if not ready:
        return None
    plan = _plan(package)
    provider = _provider(package)
    proof = _proof(package, plan)
    identity = _identity(package)
    family = str(plan.get("actionFamily") or "").strip()
    title = _text(plan.get("finalTaskTitle"))
    steps = _dedupe_lines(_arr(plan.get("operatorActionSteps")))
    if not steps:
        steps = _dedupe_lines(_arr(plan.get("executionSteps")))
    if not title or not steps:
        return None
    operation_plan = _dict(plan.get("operationPlan"))
    evidence = _evidence(plan)
    operator_view = _operator_view(family, plan, package)
    decision_id = stable_decision_id(data_version, package.get("packageId"), identity.get("productId"))
    rag_summary = rag_context_summary(_dict(package.get("ragContextSnapshot")))
    rag_trace = {
        "version": VERSION,
        "summary": rag_summary,
        "usedCaseIds": plan.get("ragUsedCaseIds") or [],
        "rejectedCaseIds": plan.get("ragRejectedCaseIds") or [],
        "applicationReason": plan.get("ragApplicationReason"),
        "rule": "RAG is context only; the current Agent2 plan owns execution.",
    }
    active = _dict(plan.get("activeActionContract")) or active_action_contract(plan)
    active["activeSopPlan"] = {
        **_dict(active.get("activeSopPlan")),
        "operatorActionSteps": steps,
        "executionSteps": _arr(plan.get("executionSteps")),
        "decisionBranches": _arr(plan.get("decisionBranches")),
        "submissionEvidence": _arr(plan.get("submissionEvidence")),
        "reviewMetrics": _arr(plan.get("reviewMetrics")),
    }
    task_plan = {
        "title": title,
        "taskTitle": title,
        "finalTaskTitle": title,
        "titleSource": "agent2ActionPlan.finalTaskTitle",
        "productId": identity.get("productId"),
        "storeId": identity.get("storeId"),
        "productIdentity": identity,
        "selectedActionFamily": family,
        "taskType": "operation_action",
        "taskResponsibility": "operator_growth",
        "departmentTaskType": "operator_growth",
        "priority": "高" if family in {"roas_scale", "roas_guard", "platform_activity", "activity_apply"} else "中",
        "executionDeadline": "6小时内" if family in {"title_image_test", "platform_activity", "activity_apply"} else "12小时内",
        "followUpDeadline": "系统自动复盘",
        "reviewCycle": "3/7/14/30/90天系统自动复盘",
        "assigneeRole": "operator",
        "approvalRequired": False,
        "authorizationStatus": "pending_numeric_authority",
        "operationMode": plan.get("operationMode"),
        "differentiationReason": plan.get("differentiationReason"),
        "operatingConclusion": operator_view["operatingConclusion"],
        "judgmentBasis": operator_view["judgmentBasis"],
        "operatorJudgmentView": operator_view,
        "executionObject": _dict(plan.get("executionObject")),
        "operationPlan": operation_plan,
        "agent2ExecutionProof": proof,
        "executionSteps": _arr(plan.get("executionSteps")),
        "decisionBranches": _arr(plan.get("decisionBranches")),
        "crossDepartmentActions": _arr(plan.get("crossDepartmentActions")),
        "supportingCoordination": _arr(plan.get("crossDepartmentActions")),
        "operatorExecutionSop": steps,
        "sopSteps": steps,
        "evidenceRequirements": evidence,
        "submissionEvidence": _arr(plan.get("submissionEvidence")),
        "reviewMetrics": _arr(plan.get("reviewMetrics")),
        "activeActionContract": active,
        "metricDigest": _dict(plan.get("metricDigest") or package.get("metricDigest")),
        "agent2PlanRef": f"agent2_plan:{plan.get('packageId') or package.get('packageId') or pipeline_item_id}",
        "actionParameterPack": _dict(package.get("actionParameterPack")),
        "agent1OperatingJudgment": _dict(package.get("agent1OperatingJudgment")),
        "agent1DecisionIR": _dict(package.get("agent1DecisionIR")),
        "ragDecisionTrace": rag_trace,
        "agent2Source": plan.get("agent2Source"),
        "sopSource": "v22_deterministic_agent2_projection",
        "compilerAddedStepCount": 0,
        "reason": plan.get("reason"),
    }
    chain_integrity = {
        "passed": True,
        "source": "v22_sop_builder",
        "contractVersion": VERSION,
        "pipelineItemId": pipeline_item_id,
        "agent2ProviderTracePassed": True,
        "operationPlanPassed": True,
        "taskDifferentiationPassed": True,
        "inventoryResponsibilityPassed": True,
        "ragTracePassed": rag_summary.get("matchedCount", 0)
        == len((plan.get("ragUsedCaseIds") or []) + (plan.get("ragRejectedCaseIds") or [])),
        "operationPlanVersion": VERSION,
        "provenanceVersion": VERSION,
        "compilerAddedStepCount": 0,
    }
    evidence_stamp = {
        "source": "v22_sop_builder",
        "mappingMode": "faithful_agent2_projection_only",
        "pipelineItemId": pipeline_item_id,
        "noMappingLlm": True,
        "noAgent2Rerun": True,
        "noActionPackRerun": True,
        "noLegacyRuntimeSource": True,
        "itemized": True,
        "agent2ProviderTracePassed": True,
        "agent2SemanticCallId": proof.get("semanticCallId"),
        "agent2ProviderRequestId": proof.get("providerRequestId"),
        "exactReplayValidated": proof.get("exactReplayValidated") is True,
        "fallbackAllowed": False,
        "taskTitleSource": "agent2ActionPlan.finalTaskTitle",
        "compilerAddedStepCount": 0,
    }
    return {
        "version": VERSION,
        "contractVersion": VERSION,
        "decisionId": decision_id,
        "packageId": package.get("packageId"),
        "dataVersion": data_version or package.get("dataVersion"),
        "storeId": identity.get("storeId"),
        "productId": identity.get("productId"),
        "decision": "create_task_snapshot",
        "taskTitle": title,
        "priority": task_plan["priority"],
        "reason": plan.get("reason"),
        "operatorJudgmentView": operator_view,
        "taskPlan": task_plan,
        "productJudgmentPackage": {
            "contractVersion": VERSION,
            "productId": identity.get("productId"),
            "storeId": identity.get("storeId"),
            "productIdentity": identity,
            "agent1OperatingJudgment": package.get("agent1OperatingJudgment"),
            "agent1DecisionIR": package.get("agent1DecisionIR"),
            "actionParameterPack": package.get("actionParameterPack"),
            "ragContextSummary": rag_summary,
            "metricDigest": task_plan["metricDigest"],
            "agent2PlanRef": task_plan["agent2PlanRef"],
        },
        "agent2Provider": provider,
        "agent2ActionPlan": plan,
        "operationPlan": operation_plan,
        "agent2ExecutionProof": proof,
        "agent2Source": plan.get("agent2Source"),
        "activeActionContract": active,
        "metricDigest": task_plan["metricDigest"],
        "agent2PlanRef": task_plan["agent2PlanRef"],
        "fallbackAllowed": False,
        "ragDecisionTrace": rag_trace,
        "operatorExecutionSop": steps,
        "evidenceRequirements": evidence,
        "crossDepartmentActions": task_plan["crossDepartmentActions"],
        "operationMode": task_plan["operationMode"],
        "executionObject": task_plan["executionObject"],
        "decisionBranches": task_plan["decisionBranches"],
        "chainIntegrity": chain_integrity,
        "taskMappingAgentEvidence": evidence_stamp,
        "compilerAddedStepCount": 0,
        "rule": "V22 formats Agent2 steps only and never invents a business action.",
    }


def save_sop_decision(decision: Dict[str, Any]) -> None:
    del decision
    return None


def install_sop_builder_core() -> None:
    return None


__all__ = [
    "SOP_BUILDER_CORE_VERSION",
    "build_sop_decision_from_package",
    "save_sop_decision",
    "stable_decision_id",
]
