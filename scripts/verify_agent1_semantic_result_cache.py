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
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    findings: list[str] = []
    spec = _json("governance/agent1_semantic_result_cache_v1.json")
    registry = _json("config/v23_registry_runtime.json")
    token_source = _read("src/services/agent_token_runtime_hash_exact_v2259_service.py")
    execution_source = _read("src/services/hash_directed_artifact_runtime_v2259_service.py")
    downstream_source = _read("src/services/agent_token_runtime_v2259_service.py")
    worker_source = _read("src/services/station_agent_worker_v2259_service.py")

    agent1 = ((registry.get("modules") or {}).get("agent1_runtime") or {})
    binding = (agent1.get("activeBindingProbe") or {}).get("expectedOwners") or {}
    expected_runner = (
        "src.services.agent_token_runtime_hash_exact_v2259_service:"
        "run_agent1_projected_inputs"
    )
    if agent1.get("runner") != expected_runner:
        findings.append("agent1_registry_runner_changed")
    if binding.get("tokenRuntimeOwner") != "src.services.agent_token_runtime_hash_exact_v2259_service":
        findings.append("agent1_token_runtime_owner_changed")
    if binding.get("agent1StageOwner") != "src.services.agent_runtime_hard_interface_v2257_service":
        findings.append("agent1_stage_owner_changed")

    required_token_literals = [
        'AGENT1_SEMANTIC_RESULT_CACHE_VERSION = "23.1.6"',
        'AGENT1_SEMANTIC_IDENTITY_SCHEMA = "agent1.semantic_identity.v1"',
        "def build_agent1_semantic_identity(",
        "def _accepted_semantic_execution(",
        "artifact_execution_index_v2259",
        'mode="semanticHash_then_currentExecutionHash"',
        'origin="semantic_result_cache_rebound"',
        "agent1ApiCallCount=0",
        "semanticCacheCreatesNewOutputArtifact=True",
        "semanticCacheCrossProductReuseAllowed=False",
        "requestCacheEnabled=False",
        "itemResultCacheEnabled=True",
        "cachedOutputRebindingAllowed=True",
        'cachedOutputRebindingScope="semantic_business_body_to_new_exact_output_artifact_only"',
        'providerOutputMatchingContract="itemExecutionId+inputContentHash"',
        "claim_execution(descriptor)",
        "complete_execution(",
        "validate_artifact(artifact_id, expected_type=\"agent1_model_output.v2259\")",
        'created_by="agent_token_runtime_hash_exact_v2259_semantic_rebind"',
    ]
    for literal in required_token_literals:
        if literal not in token_source:
            findings.append(f"semantic_cache_contract_missing:{literal}")

    forbidden_token_literals = [
        "CREATE TABLE IF NOT EXISTS agent1_semantic_cache",
        "CREATE TABLE IF NOT EXISTS artifact_semantic_cache",
        "ThreadPoolExecutor",
        "asyncio.gather",
        "threading.Thread(",
        "cache_enabled=True",
        "fallbackIdentityMatchingAllowed=True",
        "semanticCacheCrossProductReuseAllowed=True",
    ]
    for literal in forbidden_token_literals:
        if literal in token_source:
            findings.append(f"semantic_cache_forbidden_behavior:{literal}")

    # The secondary identity must strip execution transport identity but retain product
    # and store semantics so a cache hit cannot cross business objects.
    required_semantic_projection_literals = [
        'for key in ("correlationId", "signalId", "dataVersion")',
        'for key in ("sourceArtifactRefs", "sourceContentHash", "dataVersions")',
        'for key in ("sourceRef", "sourceContentHash", "sourceLineageHash")',
        '"semanticInputHash": semantic_input_hash',
        '"semanticContractHash": semantic_contract_hash',
        '"crossProductReuseAllowed": False',
    ]
    for literal in required_semantic_projection_literals:
        if literal not in token_source:
            findings.append(f"semantic_projection_contract_missing:{literal}")

    # Exact execution identity remains untouched in the existing hash-directed runtime.
    exact_execution_literals = [
        '"inputArtifactRef": binding.get("inputArtifactRef")',
        '"inputContentHash": binding.get("inputContentHash")',
        '"projectionVersion": projection_version',
        '"promptVersion": prompt_version',
        '"policyHash": policy_hash',
        '"provider": provider',
        '"model": model',
        '"generationParametersHash": generation_hash',
    ]
    for literal in exact_execution_literals:
        if literal not in execution_source:
            findings.append(f"exact_execution_identity_contract_missing:{literal}")

    # Existing exact execution ledger stays the only persistent execution/cache ledger.
    if "CREATE TABLE IF NOT EXISTS artifact_execution_index_v2259" not in execution_source:
        findings.append("exact_execution_ledger_missing")
    if "metadata_json TEXT" not in execution_source:
        findings.append("exact_execution_metadata_index_missing")

    # Agent2 remains cache-off in this patch; only Agent1 gets semantic result reuse.
    for literal in (
        '"stage": "agent2_action_draft"',
        '"requestCacheEnabled": False',
        '"itemResultCacheEnabled": False',
        '"cachedOutputRebindingAllowed": False',
    ):
        if literal not in downstream_source:
            findings.append(f"agent2_cache_boundary_changed:{literal}")

    if "secondWorkerAllowed=False" not in worker_source:
        findings.append("single_worker_contract_missing")

    invariants = spec.get("invariants") or {}
    false_invariants = (
        "registryRootRotated",
        "runtimeMembershipChanged",
        "registryRunnerChanged",
        "agent1StageOwnerChanged",
        "tokenRuntimeOwnerChanged",
        "signalAdmissionChanged",
        "providerChanged",
        "modelConfiguredByThisPatch",
        "promptChanged",
        "temperatureChanged",
        "thinkingChanged",
        "tokenBudgetChanged",
        "requestCacheEnabled",
        "secondWorkerAllowed",
        "exactExecutionHashDefinitionChanged",
        "providerOutputMatchingChanged",
        "agent2Changed",
        "agent3Changed",
        "taskMappingChanged",
        "taskPoolChanged",
    )
    for key in false_invariants:
        if invariants.get(key) is not False:
            findings.append(f"governance_invariant_not_false:{key}")

    target = spec.get("targetRuntime") or {}
    if target.get("newSemanticCacheTableAllowed") is not False:
        findings.append("new_semantic_cache_table_not_fail_closed")
    if target.get("requestCacheEnabled") is not False:
        findings.append("request_cache_must_remain_disabled")
    if target.get("itemResultCacheEnabled") is not True:
        findings.append("agent1_item_result_cache_not_enabled")
    if target.get("semanticHitMustCreateNewOutputArtifact") is not True:
        findings.append("semantic_hit_new_output_artifact_not_required")
    if target.get("cachedSourceOutputMayBecomeCurrentOutputRefDirectly") is not False:
        findings.append("old_output_direct_reuse_not_forbidden")
    if target.get("crossProductReuseAllowed") is not False:
        findings.append("cross_product_semantic_reuse_not_forbidden")

    registry_policy = spec.get("registryPolicy") or {}
    if registry_policy.get("runtimeProjectionUpdateRequired") is not False:
        findings.append("runtime_projection_update_unexpected")
    if registry_policy.get("activeBindingOwnerUpdateRequired") is not False:
        findings.append("active_binding_owner_update_unexpected")

    material = {
        "schema": "competition.agent1_semantic_result_cache.report.v1",
        "version": spec.get("version"),
        "agent1Runner": agent1.get("runner"),
        "agent1StageOwner": binding.get("agent1StageOwner"),
        "tokenRuntimeOwner": binding.get("tokenRuntimeOwner"),
        "semanticIdentitySchema": target.get("semanticIdentitySchema"),
        "semanticCacheContractVersion": target.get("semanticCacheContractVersion"),
        "executionLedger": (spec.get("baseRuntime") or {}).get("executionLedger"),
        "requestCacheEnabled": target.get("requestCacheEnabled"),
        "itemResultCacheEnabled": target.get("itemResultCacheEnabled"),
        "findings": findings,
    }
    report = {
        **material,
        "verified": not findings,
        "verificationHash": _hash(material),
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
