"""Deterministic V24.1 single-stage blueprint compatibility compiler.

The module remains the dedicated REGISTERED_ONLY runner for task_blueprint_compiler.
It accepts one JSON-compatible immutable source package and projects one validated
V23 Agent3 SOP into exactly one V24 parent task, one stage and one action node.

The compiler is deliberately side-effect free: it performs no file I/O, database
writes, Provider calls, task-pool admission, lifecycle advancement or runtime wiring.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Dict


IDENTITY_VERSION = "24.0.0"
BLUEPRINT_COMPILER_VERSION = "24.1.0"
ACTIVATION_STATE = "REGISTERED_ONLY"
COMPATIBILITY_MODE = "single_stage_v23_projection"
COMPILER_RUNNER = (
    "tools.registry_compiler.v24_task_blueprint_compiler:"
    "task_blueprint_compiler_identity"
)


class BlueprintCompatibilityError(ValueError):
    """Fail-closed V24.1 compatibility compiler error."""


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BlueprintCompatibilityError(
            "V24_BLUEPRINT_SOURCE_NOT_CANONICAL_JSON"
        ) from exc


def _canonical_clone(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(
        _canonical_json(value).encode("utf-8")
    ).hexdigest()


def _stable_id(prefix: str, domain: str, source_material_hash: str) -> str:
    digest = hashlib.sha256(
        f"{BLUEPRINT_COMPILER_VERSION}:{domain}:{source_material_hash}".encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return f"{prefix}-{digest.upper()}"


def _required_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BlueprintCompatibilityError(code)
    return value.strip()


def _required_mapping(value: Any, code: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BlueprintCompatibilityError(code)
    return dict(value)


def _required_list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise BlueprintCompatibilityError(code)
    return value


def _assert_same(expected: str, actual: Any, field: str) -> None:
    if actual in (None, ""):
        return
    if not isinstance(actual, str) or actual.strip() != expected:
        raise BlueprintCompatibilityError(
            f"V24_BLUEPRINT_SOURCE_IDENTITY_MISMATCH:{field}"
        )


def _declared_action_families(value: Any) -> set[str]:
    families: set[str] = set()
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"actionFamily", "lockedActionFamily"} and isinstance(
                item, str
            ):
                normalized = item.strip()
                if normalized:
                    families.add(normalized)
            else:
                families.update(_declared_action_families(item))
    elif isinstance(value, list):
        for item in value:
            families.update(_declared_action_families(item))
    return families


def _validate_execution_steps(
    steps: Any,
    locked_family: str,
) -> list[Dict[str, Any]]:
    required_fields = (
        "stepId",
        "actionFamily",
        "actionType",
        "executionObject",
        "executorRole",
        "instruction",
        "deadline",
        "completionCriteria",
    )
    values = _required_list(steps, "V24_BLUEPRINT_EXECUTION_STEPS_REQUIRED")
    result: list[Dict[str, Any]] = []
    for index, raw in enumerate(values):
        if not isinstance(raw, Mapping):
            raise BlueprintCompatibilityError(
                f"V24_BLUEPRINT_EXECUTION_STEP_INVALID:{index}:object_required"
            )
        step = dict(raw)
        for field in required_fields:
            if step.get(field) in (None, "", [], {}):
                raise BlueprintCompatibilityError(
                    f"V24_BLUEPRINT_EXECUTION_STEP_INVALID:{index}:{field}"
                )
        if str(step.get("actionFamily")).strip() != locked_family:
            raise BlueprintCompatibilityError(
                "V24_BLUEPRINT_MULTIPLE_ACTION_FAMILIES"
            )
        result.append(step)
    return result


def _validate_conditions(
    values: Any,
    *,
    locked_family: str,
    kind: str,
) -> list[Dict[str, Any]]:
    if kind == "stop":
        code_prefix = "STOP"
        action_field = "responseAction"
    else:
        code_prefix = "ROLLBACK"
        action_field = "rollbackAction"
    required_fields = (
        "conditionId",
        "actionFamily",
        "conditionType",
        "condition",
        action_field,
        "evidenceRequired",
    )
    items = _required_list(
        values,
        f"V24_BLUEPRINT_{code_prefix}_CONDITIONS_REQUIRED",
    )
    result: list[Dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, Mapping):
            raise BlueprintCompatibilityError(
                f"V24_BLUEPRINT_{code_prefix}_CONDITION_INVALID:"
                f"{index}:object_required"
            )
        item = dict(raw)
        for field in required_fields:
            if item.get(field) in (None, "", [], {}):
                raise BlueprintCompatibilityError(
                    f"V24_BLUEPRINT_{code_prefix}_CONDITION_INVALID:"
                    f"{index}:{field}"
                )
        if str(item.get("actionFamily") or "").strip() != locked_family:
            raise BlueprintCompatibilityError(
                "V24_BLUEPRINT_MULTIPLE_ACTION_FAMILIES"
            )
        result.append(item)
    return result


def compile_single_stage_blueprint(source: Mapping[str, Any]) -> Dict[str, Any]:
    """Compile one validated V23 Agent3 SOP into one V24 single-stage blueprint.

    The input is cloned through canonical JSON before validation, so the function
    never mutates the caller's object. Identical semantic inputs produce identical
    IDs, content hashes, logical references and canonical output bytes.
    """

    if not isinstance(source, Mapping):
        raise BlueprintCompatibilityError("V24_BLUEPRINT_SOURCE_REQUIRED")
    canonical_source = _canonical_clone(dict(source))

    source_task_id = _required_text(
        canonical_source.get("sourceTaskId"),
        "V24_BLUEPRINT_SOURCE_TASK_ID_REQUIRED",
    )
    product_id = _required_text(
        canonical_source.get("productId"),
        "V24_BLUEPRINT_PRODUCT_ID_REQUIRED",
    )
    store_id = _required_text(
        canonical_source.get("storeId"),
        "V24_BLUEPRINT_STORE_ID_REQUIRED",
    )
    data_version = _required_text(
        canonical_source.get("dataVersion"),
        "V24_BLUEPRINT_DATA_VERSION_REQUIRED",
    )
    locked_family = _required_text(
        canonical_source.get("lockedActionFamily"),
        "V24_BLUEPRINT_LOCKED_ACTION_FAMILY_REQUIRED",
    )
    sop = _required_mapping(
        canonical_source.get("agent3Sop"),
        "V24_BLUEPRINT_AGENT3_SOP_REQUIRED",
    )

    if sop.get("sopStatus") != "sop_ready":
        raise BlueprintCompatibilityError("V24_BLUEPRINT_AGENT3_SOP_NOT_READY")
    contract_validation = _required_mapping(
        sop.get("contractValidation"),
        "V24_BLUEPRINT_AGENT3_CONTRACT_VALIDATION_REQUIRED",
    )
    if contract_validation.get("passed") is not True or contract_validation.get(
        "missing"
    ) not in (None, []):
        raise BlueprintCompatibilityError(
            "V24_BLUEPRINT_AGENT3_CONTRACT_NOT_VALIDATED"
        )

    _assert_same(product_id, sop.get("productId"), "productId")
    _assert_same(store_id, sop.get("storeId"), "storeId")
    _assert_same(data_version, sop.get("dataVersion"), "dataVersion")
    _assert_same(source_task_id, sop.get("sourceTaskId"), "sourceTaskId")
    _assert_same(locked_family, sop.get("actionFamily"), "actionFamily")
    _assert_same(
        locked_family,
        sop.get("lockedActionFamily"),
        "lockedActionFamily",
    )

    source_package_id = _required_text(
        canonical_source.get("sourcePackageId") or sop.get("packageId"),
        "V24_BLUEPRINT_SOURCE_PACKAGE_ID_REQUIRED",
    )
    _assert_same(source_package_id, sop.get("packageId"), "packageId")

    families = _declared_action_families(sop)
    if not families or families != {locked_family}:
        raise BlueprintCompatibilityError(
            "V24_BLUEPRINT_MULTIPLE_ACTION_FAMILIES"
        )

    execution_steps = _validate_execution_steps(
        sop.get("executionSteps"),
        locked_family,
    )
    stop_conditions = _validate_conditions(
        sop.get("stopConditions"),
        locked_family=locked_family,
        kind="stop",
    )
    rollback_conditions = _validate_conditions(
        sop.get("rollbackConditions"),
        locked_family=locked_family,
        kind="rollback",
    )

    identity_material = {
        "schema": "task.single_stage_source.v24.1",
        "sourceTaskId": source_task_id,
        "sourcePackageId": source_package_id,
        "productId": product_id,
        "storeId": store_id,
        "dataVersion": data_version,
        "lockedActionFamily": locked_family,
        "agent3Sop": sop,
        "evidenceRefs": canonical_source.get("evidenceRefs") or [],
        "agent3SopRef": canonical_source.get("agent3SopRef"),
        "agent3OutputContentHash": canonical_source.get(
            "agent3OutputContentHash"
        ),
    }
    source_material_hash = _sha256(identity_material)
    plan_id = _stable_id("PLAN", "plan", source_material_hash)
    parent_task_id = _stable_id(
        "TASK",
        "parent-task",
        source_material_hash,
    )
    stage_id = _stable_id("STAGE", "stage", source_material_hash)
    action_node_id = _stable_id(
        "NODE",
        "action-node",
        source_material_hash,
    )

    evidence_references = {
        "sourceTaskId": source_task_id,
        "sourcePackageId": source_package_id,
        "agent3SopRef": canonical_source.get("agent3SopRef"),
        "agent3OutputContentHash": canonical_source.get(
            "agent3OutputContentHash"
        ),
        "agent2DraftRef": sop.get("agent2DraftRef"),
        "agent3ExecutionProof": sop.get("agent3ExecutionProof") or {},
        "submissionEvidence": sop.get("submissionEvidence") or [],
        "sourceEvidenceRefs": canonical_source.get("evidenceRefs")
        or sop.get("evidenceRefs")
        or [],
    }

    action_node = {
        "schema": "plan.action_node_contract.v24",
        "version": BLUEPRINT_COMPILER_VERSION,
        "planId": plan_id,
        "parentTaskId": parent_task_id,
        "stageId": stage_id,
        "actionNodeId": action_node_id,
        "nodeType": "single_stage_v23_sop",
        "sequence": 1,
        "sourceTaskId": source_task_id,
        "sourcePackageId": source_package_id,
        "productId": product_id,
        "storeId": store_id,
        "dataVersion": data_version,
        "actionFamily": locked_family,
        "sopStatus": "sop_ready",
        "finalTaskTitle": sop.get("finalTaskTitle"),
        "executionObjective": sop.get("executionObjective"),
        "executionObject": sop.get("executionObject"),
        "executionSteps": execution_steps,
        "stopConditions": stop_conditions,
        "rollbackConditions": rollback_conditions,
        "decisionBranches": sop.get("decisionBranches") or [],
        "approvalFlow": sop.get("approvalFlow") or {},
        "reviewMetrics": sop.get("reviewMetrics") or [],
        "verificationPeriod": sop.get("verificationPeriod"),
        "reviewCycle": sop.get("reviewCycle") or [],
        "evidenceReferences": evidence_references,
    }

    stage = {
        "schema": "plan.stage.v24",
        "version": BLUEPRINT_COMPILER_VERSION,
        "planId": plan_id,
        "parentTaskId": parent_task_id,
        "stageId": stage_id,
        "stageType": "single_action_execution",
        "stageOrder": 1,
        "status": "ready",
        "title": sop.get("finalTaskTitle")
        or f"{locked_family} execution",
        "actionNodeIds": [action_node_id],
        "actionNodes": [action_node],
        "dependsOnStageIds": [],
        "decisionBranches": sop.get("decisionBranches") or [],
    }

    stage_graph = {
        "schema": "plan.stage_graph.v24",
        "version": BLUEPRINT_COMPILER_VERSION,
        "compatibilityMode": COMPATIBILITY_MODE,
        "planId": plan_id,
        "currentStageId": stage_id,
        "stageIds": [stage_id],
        "stages": [stage],
        "dependencyEdges": [],
        "decisionBranches": sop.get("decisionBranches") or [],
    }

    blueprint_without_ref = {
        "schema": "task.execution_blueprint.v24",
        "version": BLUEPRINT_COMPILER_VERSION,
        "compilerRunner": COMPILER_RUNNER,
        "compatibilityMode": COMPATIBILITY_MODE,
        "activationState": ACTIVATION_STATE,
        "runtimeBindingEnabled": False,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "planId": plan_id,
        "parentTaskId": parent_task_id,
        "currentStageId": stage_id,
        "productId": product_id,
        "storeId": store_id,
        "dataVersion": data_version,
        "sourceTaskId": source_task_id,
        "sourcePackageId": source_package_id,
        "lockedActionFamily": locked_family,
        "blueprintStatus": "ready",
        "stageCount": 1,
        "actionNodeCount": 1,
        "stageGraph": stage_graph,
        "sourceMaterialHash": source_material_hash,
        "sourceAgent3SopHash": _sha256(sop),
        "operatorExecutionSop": sop,
        "preservationContract": {
            "operatorExecutionSopSource": "agent3Sop",
            "executionStepsSource": "agent3Sop.executionSteps",
            "stopConditionsSource": "agent3Sop.stopConditions",
            "rollbackConditionsSource": "agent3Sop.rollbackConditions",
            "evidenceReferencesSource": "agent3Sop+source",
            "businessContentRewritten": False,
        },
    }
    blueprint_content_hash = _sha256(blueprint_without_ref)
    blueprint = {
        **blueprint_without_ref,
        "taskExecutionBlueprintRef": (
            "artifact://task-execution-blueprint/"
            + blueprint_content_hash.removeprefix("sha256:")
        ),
        "blueprintContentHash": blueprint_content_hash,
    }
    return _canonical_clone(blueprint)


def canonical_blueprint_bytes(blueprint: Mapping[str, Any]) -> bytes:
    """Return the canonical UTF-8 representation used for deterministic proof."""

    if not isinstance(blueprint, Mapping):
        raise BlueprintCompatibilityError(
            "V24_BLUEPRINT_OUTPUT_MAPPING_REQUIRED"
        )
    return _canonical_json(dict(blueprint)).encode("utf-8")


def task_blueprint_compiler_identity(
    source: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return identity metadata or compile one source package when supplied."""

    if source is not None:
        return compile_single_stage_blueprint(source)
    return {
        "schema": "registry.module_identity.v24",
        "version": IDENTITY_VERSION,
        "moduleId": "task_blueprint_compiler",
        "activationState": ACTIVATION_STATE,
        "runtimeBindingEnabled": False,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "compilerVersion": BLUEPRINT_COMPILER_VERSION,
        "compilerRunner": COMPILER_RUNNER,
        "compatibilityModes": [COMPATIBILITY_MODE],
        "sideEffectFree": True,
    }


__all__ = [
    "BlueprintCompatibilityError",
    "canonical_blueprint_bytes",
    "compile_single_stage_blueprint",
    "task_blueprint_compiler_identity",
]
