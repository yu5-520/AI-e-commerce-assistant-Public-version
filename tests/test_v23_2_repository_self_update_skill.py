from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.registry_compiler.approval_runner import (
    ApprovalRunnerError,
    compile_approved_requirement,
    validate_approval_descriptor,
)
from tools.registry_compiler.change_program import verify_access_scope
from tools.registry_compiler.requirement_resolver import resolve_requirement
from tools.self_update.active_module_resolver import resolve_active_modules
from tools.self_update.impact_bundle import build_impact_bundle
from tools.self_update.scope_policy import create_diagnostic_transaction

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT_PATH = (
    ROOT
    / "contracts"
    / "requirements"
    / "REQ-V23-2-REPOSITORY-SELF-UPDATE-SKILL-001.json"
)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fresh_legacy_approval() -> dict:
    transaction = resolve_requirement(_read(REQUIREMENT_PATH), ROOT)
    assert transaction["state"] == "WAITING_FOR_USER_APPROVAL"
    return {
        "schema": "self_update.approval.v1",
        "version": "23.1.0",
        "requirementPath": (
            "contracts/requirements/REQ-V23-2-REPOSITORY-SELF-UPDATE-SKILL-001.json"
        ),
        "approvedBy": "yu5-520",
        "approvedAt": "2026-07-28T00:00:00Z",
        "approvedRequirementIrHash": transaction["requirementIrHash"],
        "approvedImpactHash": transaction["impactHash"],
        "approvedRegistryRootHash": transaction["registryRootHash"],
    }


def test_v23_2_active_module_resolver_uses_registered_paths_without_search() -> None:
    result = resolve_active_modules(["registry_compiler", "release_governance"], ROOT)
    assert result["resolved"] is True, result["findings"]
    assert result["repositoryWideSearchExecuted"] is False
    assert result["fuzzyFilenameSearchExecuted"] is False
    assert {item["moduleId"] for item in result["modules"]} == {
        "registry_compiler",
        "release_governance",
    }
    assert all(item["classification"] == "ACTIVE" for item in result["modules"])
    assert all(item["callChain"] for item in result["modules"])
    assert all(item["activeCallableOwner"] for item in result["modules"])


def test_v23_2_plan_emits_resolved_impact_bundle_and_scopes() -> None:
    bundle = build_impact_bundle(_read(REQUIREMENT_PATH), ROOT)
    assert bundle["schema"] == "self_update.impact_bundle.v1"
    assert bundle["state"] == "RESOLVED", bundle["riskFindings"]
    assert bundle["nextAction"] == "REVIEW_IMPACT"
    assert bundle["directModules"] == ["registry_compiler", "release_governance"]
    assert set(bundle["allowedWritePaths"]).issubset(bundle["allowedReadPaths"])
    assert bundle["writeScopeIsReadSubset"] is True
    assert bundle["repositoryWideSearchAllowed"] is False
    assert bundle["fuzzyFilenameSearchAllowed"] is False
    assert "tools/self_update/cli.py" in bundle["allowedReadPaths"]
    assert ".ai/skills/repository-self-update/SKILL.md" in bundle["allowedWritePaths"]
    assert "tests/test_v23_2_*.py" in bundle["requiredTests"]
    assert str(bundle["impactBundleHash"]).startswith("sha256:")


def test_v23_2_codegen_requests_compile_read_write_and_search_policy() -> None:
    compilation = compile_approved_requirement(_fresh_legacy_approval(), ROOT)
    program = compilation["program"]
    assert program["scopeVersion"] == "23.2.3"
    assert set(program["allowedWritePaths"]).issubset(program["allowedReadPaths"])
    assert program["repositoryWideSearchAllowed"] is False
    assert program["fuzzyFilenameSearchAllowed"] is False
    assert "READ-SCOPE-GATE" in program["executionOrder"]
    assert "WRITE-SCOPE-GATE" in program["executionOrder"]
    assert program["codegenRequests"]
    for request in program["codegenRequests"]:
        assert set(request["allowedWritePaths"]).issubset(request["allowedReadPaths"])
        assert request["prohibitedPaths"]
        assert request["requiredTests"]
        assert request["repositoryWideSearchAllowed"] is False
        assert request["fuzzyFilenameSearchAllowed"] is False


def test_v23_2_access_gate_blocks_out_of_scope_and_search() -> None:
    compilation = compile_approved_requirement(_fresh_legacy_approval(), ROOT)
    program = compilation["program"]
    allowed = verify_access_scope(
        program,
        read_paths=["tools/self_update/cli.py"],
        write_paths=["tools/self_update/cli.py"],
    )
    assert allowed["passed"] is True

    blocked = verify_access_scope(
        program,
        read_paths=["src/services/real_product_judgment_agent_v2259_service.py"],
        write_paths=["src/api/main.py"],
        repository_wide_search=True,
        fuzzy_filename_search=True,
    )
    assert blocked["passed"] is False
    assert set(blocked["errorCodes"]) == {
        "READ_SCOPE_VIOLATION",
        "WRITE_SCOPE_VIOLATION",
        "REPOSITORY_SEARCH_FORBIDDEN",
        "FUZZY_FILENAME_SEARCH_FORBIDDEN",
    }


def test_v23_2_unresolved_work_moves_to_separate_diagnostic_transaction() -> None:
    source = {
        "requirementId": "REQ-DEMO",
        "impactBundleHash": "sha256:" + "1" * 64,
        "transaction": {"transactionId": "CTX-REQ-DEMO"},
    }
    diagnostic = create_diagnostic_transaction(
        source,
        findings=["AMBIGUOUS_MODULE_OWNER:demo"],
        requested_read_paths=["tools/registry_compiler/module_contracts.py"],
    )
    assert diagnostic["schema"] == "self_update.diagnostic_transaction.v1"
    assert diagnostic["state"] == "DIAG_RECEIVED"
    assert diagnostic["originalTransactionState"] == "BLOCKED_BY_PLATFORM_DIAGNOSTIC"
    assert diagnostic["diagnosticId"].startswith("DIAG-REQ-DEMO-")
    assert diagnostic["allowedWritePaths"] == []
    assert diagnostic["repositoryWideSearchAllowed"] is False
    assert diagnostic["fuzzyFilenameSearchAllowed"] is False


def test_v23_2_repository_skill_state_machine_is_fail_closed() -> None:
    state_machine = _read(
        ROOT / ".ai" / "skills" / "repository-self-update" / "state-machine.json"
    )
    assert state_machine["blockedState"] == "BLOCKED_BY_PLATFORM_DIAGNOSTIC"
    assert "VERIFIED" in state_machine["normalStates"]
    assert "DIAG_VERIFIED" in state_machine["diagnosticStates"]
    assert state_machine["transitions"]["TRANSLATED"] == [
        "RESOLVED",
        "BLOCKED_BY_PLATFORM_DIAGNOSTIC",
    ]
    assert "ONLY_POST_CODEGEN_GATE_MAY_SET_VERIFIED" in state_machine["invariants"]


def test_v23_2_legacy_approval_remains_compatible_and_optional_bundle_hash_is_guarded() -> None:
    approval = _fresh_legacy_approval()
    assert "approvedImpactBundleHash" not in approval
    validation = validate_approval_descriptor(approval)
    assert validation["valid"] is True
    compilation = compile_approved_requirement(approval, ROOT)
    assert compilation["impactBundle"] is None

    stale = dict(approval)
    stale["approvedImpactBundleHash"] = "sha256:" + "0" * 64
    with pytest.raises(
        ApprovalRunnerError,
        match="STALE_APPROVAL:approvedImpactBundleHash",
    ):
        compile_approved_requirement(stale, ROOT)
