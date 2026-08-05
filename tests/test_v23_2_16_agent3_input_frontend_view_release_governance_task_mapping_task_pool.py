from __future__ import annotations

import json
from pathlib import Path

from src.services import pipeline_agent3_sop_v225_service as pipeline
from src.services.agent3_system_constraint_v23215_service import (
    AGENT2_PROOF_BRIDGE_VERSION,
    canonicalize_agent2_draft_proof,
    compile_agent3_sop_envelope_v23216,
    resolve_agent2_draft_execution_proof,
    valid_agent2_draft_execution_proof,
)
from src.services.agent_runtime_contract_v225_service import (
    missing_agent3_sop_completed_contract,
    normalize_agent3_sop_completed_contract,
)

ROOT = Path(__file__).resolve().parents[1]
BASE_REGISTRY_ROOT = "sha256:bfab9f1a6b6be32a4b51228b647a37aa1b0ccf09f964ff273df52c16fabe8839"
V24_MIGRATION_ID = "REG-MIG-V24-0-FOUNDATION-001"


def _proof(stage: str, semantic_call_id: str) -> dict:
    is_agent3 = "agent3" in stage
    return {
        "version": "23.2.16",
        "stage": stage,
        "packageId": "PKG-1",
        "semanticCallId": semantic_call_id,
        "provider": "aliyun_bailian",
        "model": "qwen3.7-plus",
        "providerRequestId": f"REQ-{semantic_call_id}" if is_agent3 else None,
        "providerCallExecuted": is_agent3,
        "exactReplayValidated": not is_agent3,
        "itemCorrelationId": "PKG-1",
        "resultMatched": True,
        "resultOrigin": "provider_call" if is_agent3 else "exact_semantic_replay",
        "inputFingerprint": f"FP-{semantic_call_id}",
        "promptVersion": "23.2.16",
        "fallbackUsed": False,
        "passed": True,
    }


def _package(agent2_proof: dict) -> dict:
    return {
        "version": "22.5.0",
        "dataVersion": "DV-1",
        "itemId": "PI-1",
        "packageId": "PKG-1",
        "productId": "P1",
        "storeId": "S1",
        "productTitle": "测试商品",
        "productIdentity": {
            "productId": "P1",
            "storeId": "S1",
            "productTitle": "测试商品",
        },
        "lockedActionFamily": "conversion_repair",
        "actionParameterPack": {"executionObjects": [{"targetId": "P1"}]},
        "recentFiveOrLatestFacts": [{"metric": "conversionRate", "current": 0.02}],
        "agent1DecisionIR": {"decisionType": "conversion_repair"},
        "agent1OperatingJudgment": {"decisionSummary": "修复详情页承接"},
        "matrixDispatch": {"selectedActionFamily": "conversion_repair"},
        "agent2ActionDraft": {
            "packageId": "PKG-1",
            "productId": "P1",
            "storeId": "S1",
            "actionFamily": "conversion_repair",
            "draftStatus": "draft_ready",
            "operationPlan": {"target": "详情页转化修复"},
            "agent2DraftExecutionProof": agent2_proof,
        },
        "agent2DraftProvider": {
            "itemProvenance": {"PKG-1": agent2_proof},
        },
    }


def _sop(agent3_proof: dict) -> dict:
    return {
        "packageId": "PKG-1",
        "productId": "P1",
        "storeId": "S1",
        "actionFamily": "conversion_repair",
        "sopStatus": "sop_ready",
        "finalTaskTitle": "修复详情页转化承接",
        "operatorActionSteps": ["检查承接断点", "修改详情页", "提交复核"],
        "executionObject": {"targetType": "product", "targetId": "P1"},
        "executionSteps": [],
        "companyStyleReason": "按当前商品证据执行",
        "agent3ExecutionProof": agent3_proof,
    }


def _registry_modules() -> dict[str, dict]:
    document = json.loads(
        (ROOT / "contracts/registry/modules.json").read_text(encoding="utf-8")
    )
    return {item["moduleId"]: item for item in document["modules"]}


def _v24_migration() -> dict:
    document = json.loads(
        (ROOT / "contracts/registry/migrations.json").read_text(encoding="utf-8")
    )
    return next(
        item for item in document["migrations"] if item["migrationId"] == V24_MIGRATION_ID
    )


def test_nested_exact_replay_proof_is_promoted_to_canonical_field() -> None:
    agent2_proof = _proof("action_plan_judgment_agent", "A2")
    package = _package(agent2_proof)
    package.pop("agent2DraftExecutionProof", None)

    normalized = canonicalize_agent2_draft_proof(package)

    assert valid_agent2_draft_execution_proof(agent2_proof) is True
    assert normalized["agent2DraftExecutionProof"] == agent2_proof
    assert normalized["agent2ActionDraft"]["agent2DraftExecutionProof"] == agent2_proof
    assert normalized["agent2ProofBridge"]["version"] == AGENT2_PROOF_BRIDGE_VERSION
    assert normalized["agent2ProofBridge"]["sourcePath"] == (
        "agent2ActionDraft.agent2DraftExecutionProof"
    )


def test_governed_legacy_alias_is_accepted_but_agent3_proof_is_rejected() -> None:
    agent2_proof = _proof("action_plan_judgment_agent", "A2")
    agent3_proof = _proof("agent3_sop_agent", "A3")

    resolved, source_path = resolve_agent2_draft_execution_proof(
        {"packageId": "PKG-1", "agent2ExecutionProof": agent2_proof}
    )
    rejected, rejected_path = resolve_agent2_draft_execution_proof(
        {"packageId": "PKG-1", "agent2ExecutionProof": agent3_proof}
    )

    assert resolved == agent2_proof
    assert source_path == "agent2ExecutionProof"
    assert rejected == {}
    assert rejected_path == ""


def test_projection_receives_canonical_proof_without_model_generation() -> None:
    agent2_proof = _proof("action_plan_judgment_agent", "A2")
    package = _package(agent2_proof)
    package.pop("agent2DraftExecutionProof", None)

    envelope = compile_agent3_sop_envelope_v23216(
        package,
        source_ref="ART-AGENT2",
        source_content_hash="sha256:agent2",
    )
    payload = envelope["payload"]

    assert payload["agent2DraftExecutionProof"]["semanticCallId"] == "A2"
    assert payload["agent2ActionDraft"]["agent2DraftExecutionProof"]["semanticCallId"] == "A2"
    assert envelope["projectionAudit"]["agent2ProofCanonical"] is True
    assert envelope["projectionAudit"]["agent2ProofBridgeVersion"] == (
        AGENT2_PROOF_BRIDGE_VERSION
    )


def test_sealed_completed_contract_passes_the_bridged_agent2_proof_forward() -> None:
    agent2_proof = _proof("action_plan_judgment_agent", "A2")
    agent3_proof = _proof("agent3_sop_agent", "A3")
    package = _package(agent2_proof)
    package.pop("agent2DraftExecutionProof", None)
    canonical = canonicalize_agent2_draft_proof(package)

    completed = normalize_agent3_sop_completed_contract(
        canonical,
        _sop(agent3_proof),
        {"itemProvenance": {"PKG-1": agent3_proof}},
    )
    missing = missing_agent3_sop_completed_contract(completed)

    assert completed["agent2DraftExecutionProof"] == agent2_proof
    assert completed["agent3ExecutionProof"] == agent3_proof
    assert "agent2DraftExecutionProof" not in missing


def test_single_public_agent3_pipeline_binds_v23216_bridge() -> None:
    assert pipeline.PIPELINE_AGENT3_SOP_VERSION == "23.2.16"
    assert pipeline.run_agent3_sop_microbatch is pipeline.run_agent3_sop_microbatch_v225
    assert pipeline.run_agent3_sop_microbatch.__module__ == (
        "src.services.pipeline_agent3_sop_v225_service"
    )
    assert pipeline.ensure_agent3_sop_input_ref_v23216.__module__ == (
        "src.services.agent3_system_constraint_v23215_service"
    )


def test_registry_migration_binds_agent3_and_preserves_downstream_contracts() -> None:
    config = json.loads(
        (ROOT / "config/v23_registry_runtime.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (ROOT / "contracts/registry/registry-manifest.json").read_text(encoding="utf-8")
    )
    migration = _v24_migration()
    modules = _registry_modules()

    assert config["registryRootHash"] == migration["baseRegistryRootHash"]
    assert manifest["registryRootHash"] != migration["baseRegistryRootHash"]
    assert manifest["registryRootHash"] != BASE_REGISTRY_ROOT
    assert migration["runtimeBehaviorChanged"] is False
    assert config["agent2ProofBridgeVersion"] == "23.2.16"
    assert config["modules"]["agent3_input_projection"]["runner"] == (
        "src.services.agent3_system_constraint_v23215_service:"
        "ensure_agent3_sop_input_ref_v23216"
    )
    assert config["modules"]["agent3_runtime"]["runner"] == (
        "src.services.pipeline_agent3_sop_v225_service:run_agent3_sop_microbatch_v225"
    )
    assert modules["agent3_input_projection"]["runtimeContractVersion"] == "23.2.16"
    assert modules["agent3_runtime"]["runtimeContractVersion"] == "23.2.16"
    assert modules["registry_compiler"]["runtimeContractVersion"] == "23.2.16"
    assert modules["registry_compiler"]["runner"] == (
        "tools.registry_compiler.compile_registry:main"
    )
    assert modules["task_mapping"]["runtimeContractVersion"] == "23.2.9"
    assert modules["task_pool"]["runtimeContractVersion"] == "23.2.9"
    assert modules["task_mapping"]["runner"] == (
        "src.services.pipeline_task_mapping_v225_service:run_task_mapping_microbatch_v225"
    )
    assert modules["task_pool"]["runner"] == (
        "src.services.pipeline_task_mapping_v225_service:"
        "run_task_pool_admission_microbatch_v225"
    )
