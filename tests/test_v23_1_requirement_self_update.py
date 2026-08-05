from __future__ import annotations

import copy
from pathlib import Path

import pytest

from tools.registry_compiler.change_manifest import load_change_manifest
from tools.registry_compiler.change_program import (
    ChangeProgramError,
    approve_transaction,
    compile_change_program,
    verify_changed_path_scope,
)
from tools.registry_compiler.completeness_report import build_completeness_report
from tools.registry_compiler.requirement_resolver import (
    render_impact_review,
    resolve_requirement,
    validate_requirement_ir,
)


ROOT = Path(__file__).resolve().parents[1]
CHANGE_PATH = ROOT / "contracts" / "changes" / "CHG-V23-1-001.json"


def _requirement() -> dict:
    return {
        "schema": "self_update.requirement_ir.v1",
        "version": "23.1.0",
        "requirementId": "REQ-V23-1-TEST-001",
        "objective": "Agent2失败重跑时只重新执行失败项，成功项不得重复调用模型。",
        "currentProblem": "失败恢复可能重新执行整个批次。",
        "constraints": [
            "不得重跑Agent1",
            "不得改变成功Artifact",
            "不得重新读取完整报表",
        ],
        "acceptanceCriteria": [
            "失败项拥有独立重跑结果",
            "成功项输出Hash保持不变",
            "任务不得重复生成",
        ],
        "semanticAnchors": ["Agent2", "失败重跑", "agent2_runtime", "microbatch"],
        "productCapabilityHints": ["operating_action_generation"],
        "registryIdentityHints": {
            "fields": [],
            "schemas": [],
            "modules": ["agent2_runtime"],
            "interfaces": [],
            "stations": [],
        },
        "prohibitedChanges": ["Agent1经营判断", "Provider配置", "Prompt", "历史业务数据"],
        "riskLevel": "HIGH",
        "clarifications": [],
        "state": "TRANSLATED",
    }


def test_v23_1_change_manifest_completeness_passes() -> None:
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


def test_v23_1_requirement_ir_is_deterministic() -> None:
    first = validate_requirement_ir(_requirement())
    second = validate_requirement_ir(_requirement())
    assert first == second
    assert first["valid"] is True
    assert first["errors"] == []
    assert first["requirementIrHash"].startswith("sha256:")


def test_v23_1_resolves_product_and_registry_impact_without_runtime_mutation() -> None:
    transaction = resolve_requirement(_requirement(), ROOT)
    assert transaction["state"] == "WAITING_FOR_USER_APPROVAL"
    assert "agent2_runtime" in transaction["directModules"]
    assert "agent2_input_projection" in transaction["directModules"]
    assert "agent3_input_projection" in transaction["transitiveModules"]
    assert "agent3_runtime" in transaction["transitiveModules"]
    assert "task_mapping" in transaction["transitiveModules"]
    assert transaction["businessRuntimeMutated"] is False
    assert transaction["databaseMutated"] is False
    assert transaction["providerCallsExecuted"] == 0
    operating = next(
        item
        for item in transaction["productImpact"]
        if item["capabilityId"] == "operating_action_generation"
    )
    assert operating["displayName"] == "运营动作方案生成"
    assert operating["impactType"] == "DIRECT"
    assert any("station_module_chain_drift" in item for item in transaction["findings"])


def test_v23_1_review_is_human_readable_and_hash_bound() -> None:
    transaction = resolve_requirement(_requirement(), ROOT)
    review = render_impact_review(transaction)
    assert "运营动作方案生成" in review
    assert "最终执行方案生成" in review
    assert "requirementIrHash" in review
    assert "impactHash" in review
    assert "registryRootHash" in review
    assert "WAITING_FOR_USER_APPROVAL" in review


def test_v23_1_compiler_requires_explicit_fresh_approval() -> None:
    transaction = resolve_requirement(_requirement(), ROOT)
    with pytest.raises(ChangeProgramError, match="not_approved"):
        compile_change_program(transaction, ROOT)

    approved = approve_transaction(
        transaction,
        approved_by="yu5-520",
        approved_at="2026-07-28T00:00:00Z",
    )
    program = compile_change_program(approved, ROOT)
    assert program["state"] == "COMPILED"
    assert program["programHash"].startswith("sha256:")
    assert {item["moduleId"] for item in program["codegenRequests"]} == {
        "agent2_input_projection",
        "agent2_runtime",
    }
    assert all(item["allowedPaths"] for item in program["codegenRequests"])
    assert all(item["editAllowed"] is False for item in program["verificationRequests"])
    assert program["businessRuntimeMutated"] is False
    assert program["providerCallsExecuted"] == 0

    stale = copy.deepcopy(approved)
    stale["registryRootHash"] = "sha256:" + "0" * 64
    stale["approval"]["approvedRegistryRootHash"] = stale["registryRootHash"]
    with pytest.raises(ChangeProgramError, match="STALE_APPROVAL"):
        compile_change_program(stale, ROOT)


def test_v23_1_patch_scope_blocks_unapproved_paths() -> None:
    approved = approve_transaction(
        resolve_requirement(_requirement(), ROOT),
        approved_by="yu5-520",
        approved_at="2026-07-28T00:00:00Z",
    )
    program = compile_change_program(approved, ROOT)
    agent2_request = next(
        item for item in program["codegenRequests"] if item["moduleId"] == "agent2_runtime"
    )
    allowed_path = agent2_request["allowedPaths"][0]
    passed = verify_changed_path_scope(program, [allowed_path], ROOT)
    assert passed["status"] == "PASS"
    assert passed["passed"] is True

    blocked_path = "src/services/agent_token_runtime_hash_exact_v2259_service.py"
    blocked = verify_changed_path_scope(
        program,
        [allowed_path, blocked_path],
        ROOT,
    )
    assert blocked["status"] == "SCOPE_EXPANSION_REQUIRED"
    assert blocked["passed"] is False
    assert blocked_path in blocked["outsideApprovedPaths"]
    assert "agent1_runtime" in blocked["outsideApprovedModules"]


def test_v23_1_unresolved_requirement_stops_before_approval() -> None:
    requirement = _requirement()
    requirement["requirementId"] = "REQ-V23-1-UNKNOWN"
    requirement["objective"] = "处理一个尚未注册的全新概念。"
    requirement["semanticAnchors"] = ["unregistered-concept-xyz"]
    requirement["productCapabilityHints"] = []
    requirement["registryIdentityHints"] = {
        "fields": [],
        "schemas": [],
        "modules": [],
        "interfaces": [],
        "stations": [],
    }
    transaction = resolve_requirement(requirement, ROOT)
    assert transaction["state"] == "SEMANTIC_CLARIFICATION_REQUIRED"
    assert "no_registered_seed_resolved" in transaction["clarificationReasons"]
    assert transaction["directModules"] == []
