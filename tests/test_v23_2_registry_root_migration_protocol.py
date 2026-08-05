from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.registry_compiler.approval_runner import validate_approval_descriptor
from tools.registry_compiler.change_program import (
    approve_transaction,
    compile_change_program,
    verify_changed_path_scope,
)
from tools.registry_compiler.compile_registry import sha256_value
from tools.registry_compiler.module_contracts import build_module_contracts
from tools.registry_compiler.post_codegen_gate import build_registry_module_receipt
from tools.registry_compiler.registry_migration import (
    build_registry_migration_plan,
    calculate_migration_plan_hash,
    validate_registry_migration_plan,
)
from tools.registry_compiler.requirement_resolver import resolve_requirement


ROOT = Path(__file__).resolve().parents[1]
BASE_ROOT = "sha256:" + "a" * 64
HEAD_ROOT = "sha256:" + "b" * 64
PLANNED_MODULES = {
    "operating_plan_compiler",
    "action_node_transport",
    "execution_resource_orchestrator",
    "node_authorization",
    "task_blueprint_compiler",
    "stage_lifecycle",
    "stage_frontend_projection",
}


def _governance_requirement() -> dict:
    return {
        "schema": "self_update.requirement_ir.v1",
        "version": "23.1.0",
        "requirementId": "REQ-V23-2-MIGRATION-TEST",
        "objective": "Add a fail-closed Registry Root migration protocol.",
        "currentProblem": "Registry changes cannot pass current Base/Head verification.",
        "constraints": ["Normal approvals remain stale when Registry Root changes."],
        "acceptanceCriteria": ["Only approved Registry files may change."],
        "semanticAnchors": ["registry root migration protocol"],
        "productCapabilityHints": [],
        "registryIdentityHints": {
            "fields": [],
            "schemas": [],
            "modules": ["registry_compiler", "release_governance"],
            "interfaces": [],
            "stations": [],
        },
        "prohibitedChanges": ["business runtime"],
        "riskLevel": "HIGH",
        "clarifications": [],
        "state": "TRANSLATED",
    }


def _plan(base_root: str) -> dict:
    return build_registry_migration_plan(
        base_registry_root_hash=base_root,
        allowed_registry_paths=[
            "contracts/registry/modules.json",
            "contracts/registry/registry-manifest.json",
        ],
        target_modules=["registry_compiler"],
        migration_reason="Align one registered owner with repository truth.",
        prohibited_paths=["contracts/registry/fields.json"],
    )


def _introduction_plan(base_root: str) -> dict:
    return build_registry_migration_plan(
        base_registry_root_hash=base_root,
        allowed_registry_paths=[
            "contracts/registry/modules.json",
            "contracts/registry/registry-manifest.json",
        ],
        target_modules=sorted(PLANNED_MODULES),
        migration_reason="Register planned modules without activating runtime bindings.",
    )


def test_v23_2_migration_plan_hash_is_deterministic_and_exact() -> None:
    plan = _plan(BASE_ROOT)
    assert plan["migrationPlanHash"] == calculate_migration_plan_hash(plan)
    validation = validate_registry_migration_plan(plan)
    assert validation["valid"] is True
    assert validation["errors"] == []

    stale = copy.deepcopy(plan)
    stale["targetModules"] = ["release_governance"]
    invalid = validate_registry_migration_plan(stale)
    assert invalid["valid"] is False
    assert "registry_migration_plan_hash_mismatch" in invalid["errors"]


@pytest.mark.parametrize(
    "path",
    [
        "contracts/registry/*.json",
        "contracts/registry/../secrets.json",
        "frontend/registry.json",
    ],
)
def test_v23_2_migration_plan_rejects_fuzzy_or_outside_paths(path: str) -> None:
    plan = build_registry_migration_plan(
        base_registry_root_hash=BASE_ROOT,
        allowed_registry_paths=[path],
        target_modules=["registry_compiler"],
        migration_reason="test",
    )
    validation = validate_registry_migration_plan(plan)
    assert validation["valid"] is False
    assert any(error.startswith("registry_migration_path_invalid:") for error in validation["errors"])


def test_v23_2_approval_requires_plan_base_root_to_equal_approved_root() -> None:
    plan = _plan(BASE_ROOT)
    approval = {
        "schema": "self_update.approval.v1",
        "version": "23.1.0",
        "requirementPath": "contracts/requirements/REQ-TEST.json",
        "approvedBy": "yu5-520",
        "approvedAt": "2026-07-29T00:00:00Z",
        "approvedRequirementIrHash": "sha256:" + "1" * 64,
        "approvedImpactHash": "sha256:" + "2" * 64,
        "approvedRegistryRootHash": HEAD_ROOT,
        "registryMigrationPlan": plan,
    }
    validation = validate_approval_descriptor(approval)
    assert validation["valid"] is False
    assert "registry_migration_base_root_approval_mismatch" in validation["errors"]


def test_v23_2_change_program_opens_only_exact_registry_paths() -> None:
    transaction = resolve_requirement(_governance_requirement(), ROOT)
    approved = approve_transaction(
        transaction,
        approved_by="yu5-520",
        approved_at="2026-07-29T00:00:00Z",
    )
    plan = _plan(transaction["registryRootHash"])
    approved["registryMigrationPlan"] = plan
    approved["approval"]["registryMigrationPlan"] = plan
    approved["approvalHash"] = sha256_value(approved["approval"])

    program = compile_change_program(approved, ROOT)
    assert program["registryMigrationPlan"] == plan
    assert set(plan["allowedRegistryPaths"]).issubset(set(program["allowedWritePaths"]))
    assert not any("*" in path for path in plan["allowedRegistryPaths"])
    governance_requests = {
        request["moduleId"]: request for request in program["codegenRequests"]
    }
    assert set(plan["allowedRegistryPaths"]).issubset(
        set(governance_requests["registry_compiler"]["allowedWritePaths"])
    )
    assert set(plan["allowedRegistryPaths"]).issubset(
        set(governance_requests["release_governance"]["allowedWritePaths"])
    )

    allowed = verify_changed_path_scope(
        program,
        ["contracts/registry/modules.json"],
        ROOT,
    )
    assert allowed["passed"] is True

    blocked = verify_changed_path_scope(
        program,
        ["contracts/registry/modules.json", "contracts/registry/fields.json"],
        ROOT,
    )
    assert blocked["passed"] is False
    assert "contracts/registry/fields.json" in blocked["outsideApprovedPaths"]


def test_v23_2_change_program_accepts_new_registered_only_targets(monkeypatch) -> None:
    transaction = resolve_requirement(_governance_requirement(), ROOT)
    approved = approve_transaction(
        transaction,
        approved_by="yu5-520",
        approved_at="2026-07-31T00:00:00Z",
    )
    contracts = copy.deepcopy(build_module_contracts(ROOT))
    for module_id in PLANNED_MODULES:
        contracts["moduleContracts"].pop(module_id, None)
    monkeypatch.setattr(
        "tools.registry_compiler.change_program.build_module_contracts",
        lambda repository: contracts,
    )
    plan = _introduction_plan(transaction["registryRootHash"])
    approved["registryMigrationPlan"] = plan
    approved["approval"]["registryMigrationPlan"] = plan
    approved["approvalHash"] = sha256_value(approved["approval"])

    program = compile_change_program(approved, ROOT)

    assert set(program["registryMigrationIntroducedModules"]) == PLANNED_MODULES
    assert set(program["approvedImpactModules"]) == {
        "registry_compiler",
        "release_governance",
        *PLANNED_MODULES,
    }
    assert set(program["generatedChangeManifest"]["expectedImplementationModules"]) == {
        "registry_compiler",
        "release_governance",
        *PLANNED_MODULES,
    }


def test_v23_2_scope_accepts_migration_target_module_paths() -> None:
    plan = _introduction_plan(
        json.loads(
            (ROOT / "contracts/registry/registry-manifest.json").read_text(encoding="utf-8")
        )["registryRootHash"]
    )
    path = "tools/registry_compiler/v24_identity_catalog.py"
    program = {
        "programHash": "sha256:" + "4" * 64,
        "directModules": ["registry_compiler", "release_governance"],
        "approvedImpactModules": [
            "registry_compiler",
            "release_governance",
            *sorted(PLANNED_MODULES),
        ],
        "allowedWritePaths": [path],
        "allowedPatterns": [],
        "registryMigrationPlan": plan,
        "generatedChangeManifest": {"pathModuleHints": {}},
    }
    report = verify_changed_path_scope(program, [path], ROOT)
    assert report["passed"] is True
    assert report["outsideApprovedModules"] == []


def _reports() -> tuple[dict, dict]:
    test_report = {
        "passed": True,
        "testReportHash": "sha256:" + "5" * 64,
        "testPlan": {
            "modules": [
                {"moduleId": "registry_compiler", "matchedTests": ["tests/test_registry.py"]},
                {"moduleId": "release_governance", "matchedTests": ["tests/test_release.py"]},
            ]
        },
    }
    completeness = {
        "passed": True,
        "completenessGateHash": "sha256:" + "6" * 64,
    }
    return test_report, completeness


def _contract_set(root_hash: str, compiler_hash: str, release_hash: str, other_hash: str = "7") -> dict:
    return {
        "registryRootHash": root_hash,
        "moduleContractRootHash": "sha256:" + ("c" if root_hash == HEAD_ROOT else "d") * 64,
        "moduleContracts": {
            "registry_compiler": {"moduleContractHash": "sha256:" + compiler_hash * 64},
            "release_governance": {"moduleContractHash": "sha256:" + release_hash * 64},
            "task_pool": {"moduleContractHash": "sha256:" + other_hash * 64},
        },
    }


def _patch_migration_receipt_dependencies(monkeypatch, before: dict, after: dict) -> None:
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate._migration_snapshot_at_ref",
        lambda repository, ref, registry_paths: {
            "contracts": before,
            "manifestVerification": {"verified": True},
            "registryFileHashes": {
                "contracts/registry/modules.json": "sha256:" + "8" * 64,
                "contracts/registry/registry-manifest.json": "sha256:" + "9" * 64,
            },
        },
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.build_module_contracts",
        lambda repository: after,
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.verify_committed_manifest",
        lambda repository: {"verified": True},
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.registry_file_hashes",
        lambda repository, paths: {
            "contracts/registry/modules.json": "sha256:" + "a" * 64,
            "contracts/registry/registry-manifest.json": "sha256:" + "b" * 64,
        },
    )


def test_v23_2_registry_migration_receipt_verifies_base_head_and_target(monkeypatch) -> None:
    plan = _plan(BASE_ROOT)
    before = _contract_set(BASE_ROOT, "1", "2")
    after = _contract_set(HEAD_ROOT, "3", "4")
    _patch_migration_receipt_dependencies(monkeypatch, before, after)
    program = {
        "programHash": "sha256:" + "4" * 64,
        "registryRootHash": BASE_ROOT,
        "baseRegistryRootHash": BASE_ROOT,
        "directModules": ["registry_compiler", "release_governance"],
        "transitiveModules": [],
        "registryMigrationPlan": plan,
    }
    test_report, completeness = _reports()
    receipt = build_registry_module_receipt(
        program,
        test_report,
        completeness,
        base_ref="base-sha",
        root=ROOT,
    )
    assert receipt["passed"] is True
    assert receipt["status"] == "PASS"
    assert receipt["baseRegistryRootHash"] == BASE_ROOT
    assert receipt["headRegistryRootHash"] == HEAD_ROOT
    assert receipt["registryRootChanged"] is True
    assert receipt["manifestVerified"] is True
    assert receipt["targetModulesChanged"] == ["registry_compiler"]
    assert receipt["unexpectedChangedModules"] == []


def test_v23_2_registry_migration_receipt_accepts_registered_only_introduction(monkeypatch) -> None:
    plan = build_registry_migration_plan(
        base_registry_root_hash=BASE_ROOT,
        allowed_registry_paths=[
            "contracts/registry/modules.json",
            "contracts/registry/registry-manifest.json",
        ],
        target_modules=["stage_lifecycle"],
        migration_reason="Register one planned module.",
    )
    before = _contract_set(BASE_ROOT, "1", "2")
    after = _contract_set(HEAD_ROOT, "3", "4")
    after["moduleContracts"]["stage_lifecycle"] = {
        "moduleContractHash": "sha256:" + "e" * 64,
        "definition": {
            "status": "REGISTERED_ONLY",
            "activationState": "REGISTERED_ONLY",
            "runtimeBindingEnabled": False,
            "upstream": [],
            "downstream": [],
        },
    }
    _patch_migration_receipt_dependencies(monkeypatch, before, after)
    program = {
        "programHash": "sha256:" + "4" * 64,
        "registryRootHash": BASE_ROOT,
        "baseRegistryRootHash": BASE_ROOT,
        "directModules": ["registry_compiler", "release_governance"],
        "transitiveModules": [],
        "registryMigrationPlan": plan,
    }
    test_report, completeness = _reports()
    receipt = build_registry_module_receipt(
        program,
        test_report,
        completeness,
        base_ref="base-sha",
        root=ROOT,
    )
    assert receipt["passed"] is True
    assert receipt["introducedTargetModules"] == ["stage_lifecycle"]
    assert receipt["introducedTargetModuleChecks"]["stage_lifecycle"][
        "registeredOnlyBoundaryPassed"
    ] is True
    assert receipt["migrationErrors"] == []


def test_v23_2_registry_migration_receipt_blocks_unchanged_root(monkeypatch) -> None:
    plan = _plan(BASE_ROOT)
    before = _contract_set(BASE_ROOT, "1", "2")
    after = _contract_set(BASE_ROOT, "3", "4")
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate._migration_snapshot_at_ref",
        lambda repository, ref, registry_paths: {
            "contracts": before,
            "manifestVerification": {"verified": True},
            "registryFileHashes": {
                path: "sha256:" + "8" * 64 for path in registry_paths
            },
        },
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.build_module_contracts",
        lambda repository: after,
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.verify_committed_manifest",
        lambda repository: {"verified": True},
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.registry_file_hashes",
        lambda repository, paths: {
            path: "sha256:" + "9" * 64 for path in paths
        },
    )
    program = {
        "programHash": "sha256:" + "4" * 64,
        "registryRootHash": BASE_ROOT,
        "baseRegistryRootHash": BASE_ROOT,
        "directModules": ["registry_compiler", "release_governance"],
        "transitiveModules": [],
        "registryMigrationPlan": plan,
    }
    test_report, completeness = _reports()
    receipt = build_registry_module_receipt(
        program,
        test_report,
        completeness,
        base_ref="base-sha",
        root=ROOT,
    )
    assert receipt["passed"] is False
    assert "REGISTRY_ROOT_UNCHANGED" in receipt["migrationErrors"]


def test_v23_2_module_contracts_keep_registry_root_as_separate_identity() -> None:
    contracts = build_module_contracts(ROOT)
    assert contracts["registryRootBindingMode"] == "separate_receipt_identity"
    assert contracts["registryRootHash"].startswith("sha256:")
    assert all(
        "registryRootHash" not in contract
        for contract in contracts["moduleContracts"].values()
    )
