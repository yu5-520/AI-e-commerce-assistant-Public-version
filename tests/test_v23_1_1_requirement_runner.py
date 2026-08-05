from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.registry_compiler.approval_runner import (
    ApprovalRunnerError,
    compile_approved_requirement,
    resolve_approval_identity,
    validate_approval_descriptor,
)
from tools.registry_compiler.change_manifest import load_change_manifest
from tools.registry_compiler.completeness_report import build_completeness_report
from tools.registry_compiler.requirement_resolver import resolve_requirement


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT_PATH = ROOT / "contracts" / "requirements" / "REQ-V23-1-SELF-UPDATE-001.json"
CHANGE_PATH = ROOT / "contracts" / "changes" / "CHG-V23-1-002.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "v23.1-requirement-self-update.yml"


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _fresh_approval() -> dict:
    transaction = resolve_requirement(_read(REQUIREMENT_PATH), ROOT)
    assert transaction["state"] == "WAITING_FOR_USER_APPROVAL"
    return {
        "schema": "self_update.approval.v1",
        "version": "23.1.0",
        "requirementPath": "contracts/requirements/REQ-V23-1-SELF-UPDATE-001.json",
        "approvedBy": "yu5-520",
        "approvedAt": "2026-07-28T00:00:00Z",
        "approvedRequirementIrHash": transaction["requirementIrHash"],
        "approvedImpactHash": transaction["impactHash"],
        "approvedRegistryRootHash": transaction["registryRootHash"],
    }


def test_v23_1_1_change_manifest_completeness_passes() -> None:
    report = build_completeness_report(load_change_manifest(CHANGE_PATH), ROOT)
    assert report["softGateStatus"] == "PASS"
    assert report["softGatePassed"] is True
    assert report["missingRequiredChanges"] == []
    assert report["unexpectedChangedModules"] == []
    assert report["unverifiedAffectedModules"] == []
    assert set(report["pathMapping"]["actualChangedModules"]) == {
        "registry_compiler",
        "release_governance",
    }


def test_v23_1_1_fresh_approval_compiles_scoped_requests() -> None:
    approval = _fresh_approval()
    validation = validate_approval_descriptor(approval)
    assert validation["valid"] is True
    assert validation["errors"] == []

    bundle = compile_approved_requirement(approval, ROOT)
    transaction = bundle["transaction"]
    program = bundle["program"]
    assert transaction["state"] == "APPROVED"
    assert transaction["approval"]["status"] == "APPROVED"
    assert program["state"] == "COMPILED"
    assert program["programHash"].startswith("sha256:")
    assert set(program["directModules"]) == {"registry_compiler", "release_governance"}
    assert {item["moduleId"] for item in program["codegenRequests"]} == {
        "registry_compiler",
        "release_governance",
    }
    assert program["businessRuntimeMutated"] is False
    assert program["databaseMutated"] is False
    assert program["providerCallsExecuted"] == 0


def test_v23_1_1_approval_identity_plan_emits_refresh_hashes_without_compiling() -> None:
    approval = _fresh_approval()
    plan = resolve_approval_identity(approval, ROOT)

    assert plan["readyForApprovalRefresh"] is True
    assert plan["migrationMode"] is False
    assert plan["matches"]["approvedRequirementIrHash"] is True
    assert plan["matches"]["approvedImpactHash"] is True
    assert plan["matches"]["approvedRegistryRootHash"] is True
    assert plan["matches"]["approvedImpactBundleHash"] is False
    assert plan["expected"]["approvedImpactBundleHash"].startswith("sha256:")
    assert plan["businessRuntimeMutated"] is False
    assert plan["databaseMutated"] is False
    assert plan["providerCallsExecuted"] == 0


def test_v23_1_1_stale_approval_is_blocked_before_compilation() -> None:
    approval = _fresh_approval()
    approval["approvedImpactHash"] = "sha256:" + "0" * 64
    with pytest.raises(ApprovalRunnerError, match="STALE_APPROVAL:approvedImpactHash"):
        compile_approved_requirement(approval, ROOT)


def test_v23_1_1_workflow_publishes_review_impact_bundle_and_compiles_approval() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "contracts/requirements/REQ-*.json" in workflow
    assert "tools.self_update.cli plan" in workflow
    assert "plan-approval" in workflow
    assert "compile-approved" in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "impact-review.md" in workflow
    assert "Enforce requirement, Impact Bundle and approval result" in workflow
