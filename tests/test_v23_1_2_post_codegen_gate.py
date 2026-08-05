from __future__ import annotations

import copy
from pathlib import Path

from tools.registry_compiler.change_manifest import load_change_manifest
from tools.registry_compiler.completeness_report import build_completeness_report
from tools.registry_compiler.post_codegen_gate import (
    build_registry_module_receipt,
    build_test_plan,
    verify_post_codegen,
)

ROOT = Path(__file__).resolve().parents[1]
CHANGE_PATH = ROOT / "contracts" / "changes" / "CHG-V23-1-003.json"


def test_v23_1_2_change_manifest_completeness_passes() -> None:
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


def test_v23_1_2_builds_test_plan_for_edit_and_verify_modules(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_agent2_runtime.py").write_text("def test_ok(): assert True\n")
    (tmp_path / "tests" / "test_task_mapping.py").write_text("def test_ok(): assert True\n")
    program = {
        "programHash": "sha256:" + "1" * 64,
        "codegenRequests": [
            {
                "requestId": "EDIT-001",
                "moduleId": "agent2_runtime",
                "allowedTestPatterns": ["tests/test_*agent2*.py"],
            }
        ],
        "verificationRequests": [
            {
                "requestId": "VERIFY-001",
                "moduleId": "task_mapping",
                "editAllowed": False,
                "allowedTestPatterns": ["tests/test_*task_mapping*.py"],
            }
        ],
    }
    plan = build_test_plan(program, tmp_path)
    assert plan["ready"] is True
    assert plan["missingModuleTestCoverage"] == []
    assert set(plan["tests"]) == {
        "tests/test_agent2_runtime.py",
        "tests/test_task_mapping.py",
    }


def test_v23_1_2_registry_receipt_requires_direct_change_and_stable_verify_module(monkeypatch) -> None:
    before = {
        "registryRootHash": "sha256:" + "a" * 64,
        "moduleContractRootHash": "sha256:" + "b" * 64,
        "moduleContracts": {
            "registry_compiler": {"moduleContractHash": "sha256:" + "1" * 64},
            "release_governance": {"moduleContractHash": "sha256:" + "2" * 64},
        },
    }
    after = copy.deepcopy(before)
    after["moduleContractRootHash"] = "sha256:" + "c" * 64
    after["moduleContracts"]["registry_compiler"]["moduleContractHash"] = "sha256:" + "3" * 64

    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate._contracts_at_ref",
        lambda repository, ref: before,
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.build_module_contracts",
        lambda repository: after,
    )
    program = {
        "programHash": "sha256:" + "4" * 64,
        "registryRootHash": before["registryRootHash"],
        "directModules": ["registry_compiler"],
        "transitiveModules": ["release_governance"],
    }
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
    receipt = build_registry_module_receipt(
        program,
        test_report,
        completeness,
        base_ref="base-sha",
        root=ROOT,
    )
    assert receipt["status"] == "PASS"
    assert receipt["passed"] is True
    direct = next(item for item in receipt["moduleReceipts"] if item["moduleId"] == "registry_compiler")
    verify = next(item for item in receipt["moduleReceipts"] if item["moduleId"] == "release_governance")
    assert direct["moduleContractHashChanged"] is True
    assert verify["moduleContractHashChanged"] is False


def test_v23_1_2_verified_state_requires_every_gate(monkeypatch) -> None:
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.git_changed_paths",
        lambda repository, base_ref, head_ref: {
            "resolved": True,
            "paths": ["tools/registry_compiler/post_codegen_gate.py"],
            "error": None,
        },
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.verify_changed_path_scope",
        lambda program, paths, repository: {
            "status": "PASS",
            "passed": True,
            "scopeVerificationHash": "sha256:" + "7" * 64,
        },
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.build_test_plan",
        lambda program, repository, changed_paths=None: {
            "ready": True,
            "tests": ["tests/test_ok.py"],
            "modules": [],
        },
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.execute_test_plan",
        lambda plan, repository, timeout_seconds: {
            "status": "PASS",
            "passed": True,
            "testReportHash": "sha256:" + "8" * 64,
            "testPlan": plan,
        },
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.build_completeness_gate_report",
        lambda transaction, program, paths, repository: {
            "status": "PASS",
            "passed": True,
            "completenessGateHash": "sha256:" + "9" * 64,
        },
    )
    monkeypatch.setattr(
        "tools.registry_compiler.post_codegen_gate.build_registry_module_receipt",
        lambda program, tests, completeness, base_ref, root: {
            "status": "PASS",
            "passed": True,
            "registryReceiptHash": "sha256:" + "a" * 64,
        },
    )
    transaction = {
        "schema": "self_update.change_transaction.v1",
        "version": "23.1.0",
        "state": "APPROVED",
        "requirementId": "REQ-TEST",
    }
    program = {
        "programHash": "sha256:" + "b" * 64,
        "directModules": ["registry_compiler"],
        "allowedPatterns": [],
    }
    result = verify_post_codegen(
        transaction,
        program,
        base_ref="base-sha",
        head_ref="head-sha",
        root=ROOT,
    )
    assert result["passed"] is True
    assert result["verifiedTransaction"]["state"] == "VERIFIED"
