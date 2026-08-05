"""V22 final Agent runtime contract with Plan IR and item provenance."""
from __future__ import annotations

from typing import Any, Dict, List

from src.runtime_version import VERSION
from src.services.action_plan_ir_v214_service import ROAS_FAMILIES, missing_action_plan_ir
from src.services.agent2_provenance_v2141_service import (
    agent2_proof_missing_reason,
    provider_has_valid_agent2_proof,
)
from src.services import agent_runtime_contract_v2010_service as core

AGENT_RUNTIME_CONTRACT_VERSION = VERSION
AGENT1_JUDGMENT_CONTRACT_VERSION = VERSION
MATRIX_DISPATCH_CONTRACT_VERSION = VERSION
ACTION_PACK_CONTRACT_VERSION = VERSION
AGENT2_PLAN_CONTRACT_VERSION = VERSION
SOP_DECISION_CONTRACT_VERSION = VERSION
SOURCE_PIPELINE_ITEMS_ONLY = core.SOURCE_PIPELINE_ITEMS_ONLY
FORBIDDEN_RUNTIME_SOURCES = core.FORBIDDEN_RUNTIME_SOURCES

blank = core.blank
first_present = core.first_present
safe_load = core.safe_load
deep_find = core.deep_find
merge_current = core.merge_current
deep_merge_keep = core.deep_merge_keep
payload_from_row = core.payload_from_row
stable_id = core.stable_id
product_id_of = core.product_id_of
store_id_of = core.store_id_of
product_title_of = core.product_title_of
action_family_of = core.action_family_of
route_of = core.route_of
normalize_agent1_judgment = core.normalize_agent1_judgment
normalize_agent1_completed_contract = core.normalize_agent1_completed_contract
normalize_action_pack_ready_contract = core.normalize_action_pack_ready_contract
missing_agent1_contract = core.missing_agent1_contract
missing_action_pack_contract = core.missing_action_pack_contract


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _proof(package: Dict[str, Any], plan: Dict[str, Any], provider: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(
        package.get("agent2ExecutionProof")
        or plan.get("agent2ExecutionProof")
        or _dict(provider.get("itemProvenance")).get(
            str(package.get("packageId") or plan.get("packageId") or "")
        )
    )


def normalize_agent2_completed_contract(
    package: Dict[str, Any],
    plan: Dict[str, Any],
    provider: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    provider = provider or {}
    base = core.normalize_agent2_completed_contract(package, plan, provider)
    proof = _proof(package, plan, provider)
    lineage = _dict(base.get("lineage"))
    completed = list(lineage.get("completedStages") or [])
    if "agent2_completed" not in completed:
        completed.append("agent2_completed")
    return merge_current(
        base,
        {
            "version": VERSION,
            "contractVersion": VERSION,
            "agent2ActionPlan": plan,
            "operationPlan": _dict(plan.get("operationPlan")),
            "activeActionContract": _dict(plan.get("activeActionContract")),
            "metricDigest": _dict(plan.get("metricDigest")),
            "agent2ExecutionProof": proof,
            "agent2Provider": provider,
            "agent2Source": plan.get("agent2Source") or "llm_provider_call",
            "fallbackAllowed": False,
            "lineage": {
                **lineage,
                "currentStage": "agent2_completed",
                "completedStages": completed,
                "source": SOURCE_PIPELINE_ITEMS_ONLY,
                "operationPlanVersion": VERSION,
                "provenanceVersion": VERSION,
            },
            "outputContract": "V22.agent2_completed",
        },
    )


def missing_agent2_contract(package: Dict[str, Any]) -> List[str]:
    missing = core.missing_agent2_contract(package)
    plan = _dict(package.get("agent2ActionPlan"))
    family = str(action_family_of(package) or plan.get("actionFamily") or "").strip()
    if plan and family in ROAS_FAMILIES:
        missing.extend(
            f"agent2ActionPlan.operationPlan.{field}"
            for field in missing_action_plan_ir(plan, family)
        )
    provider = _dict(package.get("agent2Provider"))
    proof = _proof(package, plan, provider)
    package_id = str(package.get("packageId") or plan.get("packageId") or "")
    if not provider_has_valid_agent2_proof(provider, package_id, proof):
        missing.append(
            agent2_proof_missing_reason(provider, package_id, proof)
            or "agent2ExecutionProof"
        )
    return list(dict.fromkeys(missing))


def normalize_sop_mapped_contract(
    package: Dict[str, Any],
    decision: Dict[str, Any],
) -> Dict[str, Any]:
    base = core.normalize_sop_mapped_contract(package, decision)
    plan = _dict(package.get("agent2ActionPlan"))
    proof = _dict(
        package.get("agent2ExecutionProof")
        or plan.get("agent2ExecutionProof")
        or decision.get("agent2ExecutionProof")
    )
    operation_plan = _dict(
        decision.get("operationPlan")
        or _dict(decision.get("taskPlan")).get("operationPlan")
        or plan.get("operationPlan")
    )
    return merge_current(
        base,
        {
            "version": VERSION,
            "contractVersion": VERSION,
            "operationPlan": operation_plan,
            "agent2ExecutionProof": proof,
            "activeActionContract": decision.get("activeActionContract")
            or _dict(decision.get("taskPlan")).get("activeActionContract"),
            "outputContract": "V22.sop_mapped",
        },
    )


def missing_sop_contract(package: Dict[str, Any]) -> List[str]:
    missing = missing_agent2_contract(package)
    decision = _dict(package.get("sopDecision"))
    if not decision:
        missing.append("sopDecision")
    else:
        steps = decision.get("operatorExecutionSop") or _dict(decision.get("taskPlan")).get("operatorExecutionSop")
        if not [item for item in steps or [] if str(item).strip()]:
            missing.append("sopDecision.operatorExecutionSop")
        proof = _dict(
            decision.get("agent2ExecutionProof")
            or _dict(decision.get("taskPlan")).get("agent2ExecutionProof")
        )
        if not proof:
            missing.append("sopDecision.agent2ExecutionProof")
    return list(dict.fromkeys(missing))


def normalize_task_admitted_contract(
    package: Dict[str, Any],
    admission: Dict[str, Any],
) -> Dict[str, Any]:
    base = core.normalize_task_admitted_contract(package, admission)
    decision = _dict(package.get("sopDecision"))
    task_plan = _dict(decision.get("taskPlan"))
    proof = _dict(
        decision.get("agent2ExecutionProof")
        or task_plan.get("agent2ExecutionProof")
        or package.get("agent2ExecutionProof")
    )
    authorization = _dict(
        admission.get("authorizationDecision") or decision.get("authorizationDecision")
    )
    return merge_current(
        base,
        {
            "version": VERSION,
            "contractVersion": VERSION,
            "operationPlan": _dict(
                task_plan.get("operationPlan")
                or decision.get("operationPlan")
                or package.get("operationPlan")
            ),
            "agent2ExecutionProof": proof,
            "authorizationDecision": authorization,
            "actionAuthorization": authorization,
            "activeActionContract": task_plan.get("activeActionContract")
            or decision.get("activeActionContract"),
            "outputContract": "V22.task_admitted",
        },
    )


__all__ = [
    "AGENT_RUNTIME_CONTRACT_VERSION",
    "normalize_agent1_completed_contract",
    "normalize_action_pack_ready_contract",
    "normalize_agent2_completed_contract",
    "normalize_sop_mapped_contract",
    "normalize_task_admitted_contract",
    "missing_agent1_contract",
    "missing_action_pack_contract",
    "missing_agent2_contract",
    "missing_sop_contract",
    "payload_from_row",
    "product_title_of",
]
