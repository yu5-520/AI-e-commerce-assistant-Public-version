"""V23.2.18 Agent3 system contract and Agent2 proof bridge.

Execution steps remain the authoritative operator-owned SOP body. Stop and rollback
conditions are family-typed structures. Cross-department actions are a separate
supporting-coordination surface: they may describe another department's work but may
not mutate the locked operator action family or become operator execution steps.

The system-owned bridge also restores a previously validated Agent2 proof before
Agent3 input projection; Agent3 never generates it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.services import agent3_system_constraint_base_v23214_service as base
from src.services import agent_input_transport_v225_service as transport
from src.services.agent_input_contract_v225_service import AGENT3_SOP_INPUT_SCHEMA
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)

AGENT3_SYSTEM_CONSTRAINT_VERSION = "23.2.18"
AGENT2_PROOF_BRIDGE_VERSION = "23.2.16"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 1200) -> str:
    if isinstance(value, dict):
        value = value.get("condition") or value.get("instruction") or value.get("text")
    return " ".join(str(value or "").split())[:limit]


_CONDITION_POLICIES: Dict[str, Dict[str, Any]] = {
    "title_image_test": {
        "allowedStopConditionTypes": [
            "metric_guardrail",
            "creative_compliance",
            "experiment_integrity",
            "material_negative_feedback",
            "insufficient_sample",
        ],
        "allowedRollbackConditionTypes": [
            "restore_previous_asset",
            "pause_invalid_variant",
            "experiment_reset",
        ],
    },
    "conversion_repair": {
        "allowedStopConditionTypes": [
            "metric_guardrail",
            "page_compliance",
            "experiment_integrity",
            "material_negative_feedback",
            "insufficient_sample",
        ],
        "allowedRollbackConditionTypes": [
            "restore_previous_page",
            "pause_invalid_variant",
            "experiment_reset",
        ],
    },
    "roas_guard": {
        "allowedStopConditionTypes": [
            "spend_guardrail",
            "roas_guardrail",
            "conversion_guardrail",
            "plan_integrity",
            "insufficient_sample",
        ],
        "allowedRollbackConditionTypes": [
            "restore_previous_bid",
            "restore_previous_budget",
            "pause_invalid_plan",
        ],
    },
    "roas_scale": {
        "allowedStopConditionTypes": [
            "spend_guardrail",
            "roas_guardrail",
            "conversion_guardrail",
            "plan_integrity",
            "insufficient_sample",
        ],
        "allowedRollbackConditionTypes": [
            "restore_previous_bid",
            "restore_previous_budget",
            "pause_invalid_plan",
        ],
    },
    "platform_activity": {
        "allowedStopConditionTypes": [
            "eligibility_change",
            "platform_compliance",
            "activity_configuration_conflict",
            "result_guardrail",
        ],
        "allowedRollbackConditionTypes": [
            "withdraw_application",
            "restore_previous_configuration",
            "pause_activity_execution",
        ],
    },
    "activity_apply": {
        "allowedStopConditionTypes": [
            "eligibility_change",
            "platform_compliance",
            "activity_configuration_conflict",
            "result_guardrail",
        ],
        "allowedRollbackConditionTypes": [
            "withdraw_application",
            "restore_previous_configuration",
            "pause_activity_execution",
        ],
    },
}

_DEFAULT_CONDITION_POLICY = {
    "allowedStopConditionTypes": [
        "metric_guardrail",
        "compliance_guardrail",
        "execution_integrity",
        "insufficient_sample",
    ],
    "allowedRollbackConditionTypes": [
        "restore_previous_state",
        "pause_invalid_execution",
    ],
}

_STOP_REQUIRED_FIELDS = [
    "conditionId",
    "actionFamily",
    "conditionType",
    "condition",
    "responseAction",
    "evidenceRequired",
]

_ROLLBACK_REQUIRED_FIELDS = [
    "conditionId",
    "actionFamily",
    "conditionType",
    "condition",
    "rollbackAction",
    "evidenceRequired",
]

_CROSS_DEPARTMENT_REQUIRED_FIELDS = ["department", "action", "reason"]


def family_policy(family: str | None) -> Dict[str, Any]:
    policy = dict(base.family_policy(family))
    selected = _CONDITION_POLICIES.get(str(family or "").strip(), _DEFAULT_CONDITION_POLICY)
    policy.update(
        version=AGENT3_SYSTEM_CONSTRAINT_VERSION,
        allowedStopConditionTypes=list(selected["allowedStopConditionTypes"]),
        allowedRollbackConditionTypes=list(selected["allowedRollbackConditionTypes"]),
        stopConditionRequiredFields=list(_STOP_REQUIRED_FIELDS),
        rollbackConditionRequiredFields=list(_ROLLBACK_REQUIRED_FIELDS),
        crossDepartmentCoordinationRequiredFields=list(_CROSS_DEPARTMENT_REQUIRED_FIELDS),
        maxAuxiliaryRepairAttempts=1,
    )
    return policy


def compile_agent3_provider_package(package: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base.compile_agent3_provider_package(package))
    family = str(result.get("lockedActionFamily") or "")
    policy = family_policy(family)
    result["allowedStopConditionTypes"] = policy["allowedStopConditionTypes"]
    result["allowedRollbackConditionTypes"] = policy["allowedRollbackConditionTypes"]
    result["auxiliaryConditionContract"] = {
        "stopConditionsStructured": True,
        "rollbackConditionsStructured": True,
        "stopConditionRequiredFields": policy["stopConditionRequiredFields"],
        "rollbackConditionRequiredFields": policy["rollbackConditionRequiredFields"],
        "conditionActionFamilyMustEqualLockedFamily": True,
        "conditionTypesMustBeAllowedForFamily": True,
        "unrelatedBusinessRiskCannotStopCurrentActionFamily": True,
        "maxFieldRepairAttempts": policy["maxAuxiliaryRepairAttempts"],
    }
    result["crossDepartmentCoordinationContract"] = {
        "field": "crossDepartmentActions",
        "downstreamField": "supportingCoordination",
        "supportingCoordinationOnly": True,
        "requiredFields": policy["crossDepartmentCoordinationRequiredFields"],
        "otherDepartmentDomainTermsAllowed": True,
        "mayNotMutateLockedActionFamily": True,
        "mayNotBecomeOperatorExecutionSteps": True,
        "operatorExecutionStillBoundToLockedActionFamily": True,
    }
    system_contract = dict(_dict(result.get("systemConstraintContract")))
    system_contract.update(
        version=AGENT3_SYSTEM_CONSTRAINT_VERSION,
        auxiliaryConditionsAreFamilyTyped=True,
        auxiliaryFieldRepairIsIsolated=True,
        auxiliaryFieldRepairMayNotModifyExecutionSteps=True,
        operatorActionSurfaceSeparatedFromCrossDepartmentCoordination=True,
        crossDepartmentActionsAreSupportingCoordination=True,
        crossDepartmentDomainTermsDoNotMutateOperatorActionFamily=True,
    )
    result["systemConstraintContract"] = system_contract
    output_contract = dict(_dict(result.get("outputStepContract")))
    output_contract["structuredStopConditionsRequired"] = True
    output_contract["structuredRollbackConditionsRequired"] = True
    output_contract["crossDepartmentCoordinationContract"] = (
        "department+action+reason; supporting coordination only"
    )
    result["outputStepContract"] = output_contract
    return result


def _validate_condition_list(
    values: Any,
    *,
    family: str,
    kind: str,
    allowed_types: List[str],
    required_fields: List[str],
) -> List[str]:
    errors: List[str] = []
    items = _arr(values)
    prefix = "stop_condition" if kind == "stop" else "rollback_condition"
    if not items:
        errors.append(f"agent3_{prefix}s_missing")
        return errors
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict):
            errors.append(f"agent3_{prefix}_{index}_not_structured")
            continue
        for field in required_fields:
            if item.get(field) in (None, "", [], {}):
                errors.append(f"agent3_{prefix}_{index}_missing:{field}")
        item_family = _text(item.get("actionFamily"), 100)
        if item_family and item_family != family:
            errors.append(f"agent3_{prefix}_{index}_family_mismatch")
        condition_type = _text(item.get("conditionType"), 160)
        if condition_type and condition_type not in set(allowed_types):
            errors.append(f"agent3_{prefix}_{index}_type_forbidden:{condition_type}")
    return errors


def _validate_cross_department_coordination(
    values: Any,
    *,
    family: str,
    required_fields: List[str],
) -> List[str]:
    errors: List[str] = []
    for index, item in enumerate(_arr(values), 1):
        if not isinstance(item, dict):
            errors.append(f"agent3_cross_department_coordination_{index}_not_structured")
            continue
        for field in required_fields:
            if item.get(field) in (None, "", [], {}):
                errors.append(
                    f"agent3_cross_department_coordination_{index}_missing:{field}"
                )
        declared_family = _text(item.get("actionFamily"), 100)
        if declared_family and declared_family != family:
            errors.append(
                f"agent3_cross_department_coordination_{index}_action_family_override"
            )
    return errors


def _structured_auxiliary_required(package: Dict[str, Any]) -> bool:
    contract = _dict(package.get("inputContract"))
    return bool(
        package.get("enforceStructuredAuxiliaryConditions") is True
        or contract.get("schema") == "agent_input.agent3_sop.v1"
        or contract.get("agent3SystemConstraintRequired") is True
    )


def validate_agent3_sop_system_contract(
    sop: Dict[str, Any],
    package: Dict[str, Any] | None = None,
) -> List[str]:
    sop = _dict(sop)
    package = _dict(package)
    draft = _dict(package.get("agent2ActionDraft"))
    family = _text(
        package.get("lockedActionFamily")
        or draft.get("actionFamily")
        or sop.get("actionFamily"),
        100,
    )
    policy = family_policy(family)

    # V23.2.18 contract layering: the stable base owns strict operator-action
    # contamination checks. Cross-department coordination is deliberately removed
    # from that operator surface and validated by its own structural contract below.
    operator_sop = dict(sop)
    operator_sop["crossDepartmentActions"] = []
    errors = list(base.validate_agent3_sop_system_contract(operator_sop, package))
    errors.extend(
        _validate_cross_department_coordination(
            sop.get("crossDepartmentActions"),
            family=family,
            required_fields=policy["crossDepartmentCoordinationRequiredFields"],
        )
    )

    if not _structured_auxiliary_required(package):
        return list(dict.fromkeys(errors))
    errors.extend(
        _validate_condition_list(
            sop.get("stopConditions"),
            family=family,
            kind="stop",
            allowed_types=policy["allowedStopConditionTypes"],
            required_fields=policy["stopConditionRequiredFields"],
        )
    )
    errors.extend(
        _validate_condition_list(
            sop.get("rollbackConditions"),
            family=family,
            kind="rollback",
            allowed_types=policy["allowedRollbackConditionTypes"],
            required_fields=policy["rollbackConditionRequiredFields"],
        )
    )
    return list(dict.fromkeys(errors))


def valid_agent2_draft_execution_proof(value: Any) -> bool:
    """Accept only verified Agent2 provider or exact-replay provenance."""
    proof = _dict(value)
    stage = str(proof.get("stage") or "").strip().lower()
    if not proof or "agent3" in stage:
        return False
    return bool(
        proof.get("resultMatched") is True
        and (
            proof.get("providerCallExecuted") is True
            or proof.get("exactReplayValidated") is True
        )
        and proof.get("fallbackUsed") is not True
        and proof.get("semanticCallId")
        and proof.get("passed") is not False
    )


def resolve_agent2_draft_execution_proof(
    source: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    """Return one verified Agent2 proof and its deterministic source path."""
    source = _dict(source)
    draft = _dict(source.get("agent2ActionDraft"))
    task_plan = _dict(source.get("taskPlan"))
    agent2_plan = _dict(source.get("agent2ActionPlan"))
    package_id = str(
        source.get("packageId")
        or draft.get("packageId")
        or source.get("itemId")
        or ""
    )
    agent2_draft_provider = _dict(source.get("agent2DraftProvider"))
    agent2_provider = _dict(source.get("agent2Provider"))
    candidates = [
        (source.get("agent2DraftExecutionProof"), "agent2DraftExecutionProof"),
        (
            draft.get("agent2DraftExecutionProof"),
            "agent2ActionDraft.agent2DraftExecutionProof",
        ),
        (source.get("agent2ExecutionProof"), "agent2ExecutionProof"),
        (
            task_plan.get("agent2DraftExecutionProof"),
            "taskPlan.agent2DraftExecutionProof",
        ),
        (task_plan.get("agent2ExecutionProof"), "taskPlan.agent2ExecutionProof"),
        (
            agent2_plan.get("agent2DraftExecutionProof"),
            "agent2ActionPlan.agent2DraftExecutionProof",
        ),
        (
            agent2_plan.get("agent2ExecutionProof"),
            "agent2ActionPlan.agent2ExecutionProof",
        ),
        (
            _dict(agent2_draft_provider.get("itemProvenance")).get(package_id),
            "agent2DraftProvider.itemProvenance",
        ),
        (
            _dict(agent2_provider.get("itemProvenance")).get(package_id),
            "agent2Provider.itemProvenance",
        ),
    ]
    for candidate, source_path in candidates:
        if valid_agent2_draft_execution_proof(candidate):
            return dict(_dict(candidate)), source_path
    return {}, ""


def canonicalize_agent2_draft_proof(source: Dict[str, Any]) -> Dict[str, Any]:
    """Promote a verified proof to the canonical internal Agent2 field."""
    normalized = dict(_dict(source))
    proof, source_path = resolve_agent2_draft_execution_proof(normalized)
    if not proof:
        return normalized
    normalized["agent2DraftExecutionProof"] = proof
    draft = dict(_dict(normalized.get("agent2ActionDraft")))
    if draft:
        draft["agent2DraftExecutionProof"] = proof
        normalized["agent2ActionDraft"] = draft
    normalized["agent2ProofBridge"] = {
        "version": AGENT2_PROOF_BRIDGE_VERSION,
        "canonicalField": "agent2DraftExecutionProof",
        "sourcePath": source_path,
        "legacyAliasUsed": source_path in {
            "agent2ExecutionProof",
            "taskPlan.agent2ExecutionProof",
            "agent2ActionPlan.agent2ExecutionProof",
        },
        "systemOwned": True,
        "agent3MayGenerateProof": False,
    }
    return normalized


def compile_agent3_sop_envelope_v23216(
    source: Dict[str, Any],
    *,
    source_ref: str,
    source_content_hash: str,
) -> Dict[str, Any]:
    """Canonicalize Agent2 proof before the sealed Agent3 projection runs."""
    canonical = canonicalize_agent2_draft_proof(source)
    proof = _dict(canonical.get("agent2DraftExecutionProof"))
    if not valid_agent2_draft_execution_proof(proof):
        raise ValueError("agent2_draft_execution_proof_missing_before_agent3_projection")
    envelope = transport.compile_agent3_sop_envelope(
        canonical,
        source_ref=source_ref,
        source_content_hash=source_content_hash,
    )
    audit = _dict(envelope.get("projectionAudit"))
    audit.update(
        agent2ProofBridgeVersion=AGENT2_PROOF_BRIDGE_VERSION,
        agent2ProofCanonical=True,
        agent2ProofSourcePath=_dict(canonical.get("agent2ProofBridge")).get(
            "sourcePath"
        ),
    )
    return envelope


def ensure_agent3_sop_input_ref_v23216(row: Dict[str, Any]) -> str:
    """Build Agent3 input from the internal Agent2 Artifact, never task detail."""
    refs = artifact_refs_from_row(row)
    source_ref = str(
        refs.get("agent2DraftRef")
        or refs.get("currentStageRef")
        or row.get("payload_artifact_ref")
        or ""
    )
    source_hash = transport._source_hash(source_ref)
    existing = transport._existing(
        row,
        ref_key="agent3SopInputRef",
        schema=AGENT3_SOP_INPUT_SCHEMA,
        source_ref=source_ref,
        source_hash=source_hash,
    )
    if existing:
        try:
            envelope = transport.resolve_agent_input_ref(
                existing,
                expected_schema=AGENT3_SOP_INPUT_SCHEMA,
            )
            payload = _dict(envelope.get("payload"))
            proof = _dict(payload.get("agent2DraftExecutionProof"))
            if valid_agent2_draft_execution_proof(proof):
                attach_pipeline_artifact_ref(
                    str(row.get("item_id")),
                    "agent3SopInputRef",
                    existing,
                    make_current=True,
                )
                return existing
        except Exception:
            pass
    source = transport._resolve_source(source_ref)
    envelope = compile_agent3_sop_envelope_v23216(
        source,
        source_ref=source_ref,
        source_content_hash=source_hash,
    )
    return transport._store(
        envelope,
        row=row,
        source_ref=source_ref,
        ref_key="agent3SopInputRef",
        schema=AGENT3_SOP_INPUT_SCHEMA,
    )


__all__ = [
    "AGENT3_SYSTEM_CONSTRAINT_VERSION",
    "AGENT2_PROOF_BRIDGE_VERSION",
    "family_policy",
    "compile_agent3_provider_package",
    "validate_agent3_sop_system_contract",
    "valid_agent2_draft_execution_proof",
    "resolve_agent2_draft_execution_proof",
    "canonicalize_agent2_draft_proof",
    "compile_agent3_sop_envelope_v23216",
    "ensure_agent3_sop_input_ref_v23216",
]
