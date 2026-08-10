#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _json(path: str) -> dict[str, Any]:
    value = json.loads(_read(path))
    if not isinstance(value, dict):
        raise SystemExit(f"json_object_required:{path}")
    return value


def _hash(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    findings: list[str] = []
    spec = _json("governance/agent3_cross_department_coordination_contract_v1.json")
    registry = _json("config/v23_registry_runtime.json")
    active = _read("src/services/agent3_system_constraint_v23215_service.py")
    stable_base = _read("src/services/agent3_system_constraint_base_v23214_service.py")
    core = _read("src/services/agent3_sop_core_v225_service.py")
    mapping = _read("src/services/agent_runtime_contract_v225_service.py")
    runtime = _read("src/services/agent3_runtime_v23215_service.py")
    worker = _read("src/services/station_agent_worker_v2259_service.py")

    modules = registry.get("modules") or {}
    agent3 = modules.get("agent3_runtime") or {}
    task_mapping = modules.get("task_mapping") or {}
    expected_agent3_runner = "src.services.pipeline_agent3_sop_v225_service:run_agent3_sop_microbatch_v225"
    if agent3.get("runner") != expected_agent3_runner:
        findings.append("agent3_registry_runner_changed")
    if "src/services/agent3_system_constraint_v23215_service.py" not in (agent3.get("implementationPaths") or []):
        findings.append("active_system_constraint_not_registered")
    if task_mapping.get("runner") != "src.services.pipeline_task_mapping_v225_service:run_task_mapping_microbatch_v225":
        findings.append("task_mapping_registry_runner_changed")

    required_active = [
        'AGENT3_SYSTEM_CONSTRAINT_VERSION = "23.2.18"',
        '_CROSS_DEPARTMENT_REQUIRED_FIELDS = ["department", "action", "reason"]',
        '"crossDepartmentCoordinationContract"',
        '"downstreamField": "supportingCoordination"',
        '"otherDepartmentDomainTermsAllowed": True',
        '"mayNotMutateLockedActionFamily": True',
        '"mayNotBecomeOperatorExecutionSteps": True',
        'operator_sop["crossDepartmentActions"] = []',
        "def _validate_cross_department_coordination(",
        'f"agent3_cross_department_coordination_{index}_missing:{field}"',
        'f"agent3_cross_department_coordination_{index}_action_family_override"',
        'crossDepartmentActionsAreSupportingCoordination=True',
        'crossDepartmentDomainTermsDoNotMutateOperatorActionFamily=True',
    ]
    for literal in required_active:
        if literal not in active:
            findings.append(f"active_coordination_contract_missing:{literal}")

    # The historical base must remain the strict operator-family policy and must not be
    # silently weakened as part of this patch.
    for literal in (
        '"roas_scale": {',
        '"forbiddenActions": [',
        '"inventory_coordination", "warehouse_followup", "replenishment_request"',
        '"blockedTextTerms": ["仓储", "仓库", "库存", "补货"',
        'missing.append("agent3_sop_cross_family_contamination:"',
    ):
        if literal not in stable_base:
            findings.append(f"stable_operator_policy_missing:{literal}")

    # Normalization keeps the coordination field; downstream task mapping keeps the
    # same field and exposes the intended supportingCoordination alias.
    if '"crossDepartmentActions": [item for item in _arr(raw.get("crossDepartmentActions"))' not in core:
        findings.append("agent3_normalizer_dropped_cross_department_actions")
    for literal in (
        '"crossDepartmentActions": sop.get("crossDepartmentActions") or []',
        '"supportingCoordination": sop.get("crossDepartmentActions") or []',
    ):
        if literal not in mapping:
            findings.append(f"task_mapping_coordination_interface_missing:{literal}")

    # Current semantic identity already carries the active constraint version, which
    # makes the contract bump invalidate old semantic SOP entries without changing
    # Agent3 performance/cache ownership.
    for literal in (
        '"agent3SystemConstraintVersion": core.AGENT3_SYSTEM_CONSTRAINT_VERSION',
        'AGENT3_PERFORMANCE_VERSION = "23.2.17"',
        'requestCacheEnabled": False',
        'parallelProviderCallsAllowed": False',
    ):
        if literal not in runtime:
            findings.append(f"agent3_semantic_lineage_missing:{literal}")

    if "secondWorkerAllowed=False" not in worker:
        findings.append("single_worker_contract_missing")

    forbidden_active = [
        "ThreadPoolExecutor",
        "asyncio.gather",
        "threading.Thread(",
        "requestCacheEnabled=True",
        "maxAuxiliaryRepairAttempts=2",
    ]
    for literal in forbidden_active:
        if literal in active:
            findings.append(f"active_contract_forbidden_change:{literal}")

    for key, value in (spec.get("invariants") or {}).items():
        if value is not False:
            findings.append(f"governance_invariant_not_false:{key}")

    selected = spec.get("allowedRuntimeChanges") or []
    if selected != ["src/services/agent3_system_constraint_v23215_service.py"]:
        findings.append("runtime_selection_not_single_active_constraint_owner")

    lineage = spec.get("fieldLineage") or []
    names = {str(item.get("field")) for item in lineage if isinstance(item, dict)}
    for expected in (
        "agent3Sop.executionSteps",
        "agent3Sop.crossDepartmentActions",
        "taskPlan.supportingCoordination",
    ):
        if expected not in names:
            findings.append(f"field_lineage_missing:{expected}")

    material = {
        "schema": "competition.agent3_cross_department_coordination_contract.report.v1",
        "version": spec.get("version"),
        "agent3Runner": agent3.get("runner"),
        "taskMappingRunner": task_mapping.get("runner"),
        "activeSystemConstraintVersion": "23.2.18",
        "selectedRuntimeChanges": selected,
        "findings": findings,
    }
    report = {**material, "verified": not findings, "verificationHash": _hash(material)}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
