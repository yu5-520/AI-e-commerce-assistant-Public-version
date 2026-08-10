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
    spec = _json("governance/agent2_family_payload_semantic_cache_v1.json")
    registry = _json("config/v23_registry_runtime.json")
    runtime_source = _read("src/services/agent_token_runtime_v22520_service.py")
    facade_source = _read("src/services/agent_token_runtime_v225_service.py")
    core_source = _read("src/services/agent2_action_draft_core_v225_service.py")
    proof_source = _read("src/services/agent2_hash_proof_bridge_v22515_service.py")
    exact_source = _read("src/services/hash_directed_artifact_runtime_v2259_service.py")
    worker_source = _read("src/services/station_agent_worker_v2259_service.py")

    agent2 = ((registry.get("modules") or {}).get("agent2_runtime") or {})
    if agent2.get("runner") != "src.services.agent_runtime_hard_interface_v22515_service:run_agent2_microbatch_hard":
        findings.append("agent2_registry_runner_changed")

    required_runtime_literals = [
        'AGENT2_FAMILY_PAYLOAD_CACHE_VERSION = "23.1.7"',
        'AGENT2_SEMANTIC_IDENTITY_SCHEMA = "agent2.family_payload_semantic_identity.v1"',
        "def build_agent2_semantic_identity(",
        "def _accepted_semantic_family_payload(",
        "def _rebind_semantic_family_payload(",
        "def _store_semantic_rebound_output(",
        'compact.pop("packageId", None)',
        '"cachedChannel": "familyPayload"',
        'source_draft.get("draftStatus") != DRAFT_READY',
        "missing_agent2_draft_contract(source_draft)",
        "accepted_contract_version=?",
        "COALESCE(reusable,1)=1",
        'metadata.get("semanticCacheContractVersion") != AGENT2_FAMILY_PAYLOAD_CACHE_VERSION',
        "semanticCacheCreatesNewOutputArtifact=True",
        "semanticCacheRecompilesSystemOwnedFields=True",
        "semanticNonReadyChannelsCached=False",
        "semanticCacheCrossProductReuseAllowed=False",
        "requestCacheEnabled=False",
        "itemResultCacheEnabled=True",
        'cachedOutputRebindingScope="familyPayload_only_then_system_recompile"',
        'created_by="agent_token_runtime_v22520_semantic_family_payload_rebind"',
    ]
    for literal in required_runtime_literals:
        if literal not in runtime_source:
            findings.append(f"runtime_contract_missing:{literal}")

    forbidden_runtime_literals = [
        "CREATE TABLE IF NOT EXISTS agent2_semantic_cache",
        "CREATE TABLE IF NOT EXISTS family_payload_cache",
        "ThreadPoolExecutor",
        "asyncio.gather",
        "threading.Thread(",
        "requestCacheEnabled=True",
        "semanticNonReadyChannelsCached=True",
        "semanticCacheCrossProductReuseAllowed=True",
    ]
    for literal in forbidden_runtime_literals:
        if literal in runtime_source:
            findings.append(f"runtime_forbidden_behavior:{literal}")

    required_facade_literals = [
        "def run_agent2_draft_projected_inputs(*args, **kwargs):",
        'draft.get("semanticResultCacheHit") is not True',
        'draft["exactExecutionReplay"] = True',
        'draft["providerCallExecutedForCurrentResult"] = False',
        '"no_provider_replay_through_existing_exactReplayValidated_slot"',
    ]
    for literal in required_facade_literals:
        if literal not in facade_source:
            findings.append(f"proof_compatibility_missing:{literal}")

    # System compiler must remain the owner of status, locks and final draft assembly.
    for literal in (
        "AGENT2_GENERATION_COMPILER_VERSION = \"23.2.8\"",
        '"systemComputedDraftStatus": True',
        '"familyPayload": family_draft',
        '"systemOwnedFields": [',
        "def _normalize_draft(",
    ):
        if literal not in core_source:
            findings.append(f"system_compiler_contract_missing:{literal}")

    # Semantic sources are eligible only after the existing proof bridge marks them
    # reusable under the current compiler contract.
    for literal in (
        '"reusable": "INTEGER NOT NULL DEFAULT 1"',
        '"accepted_contract_version": "TEXT"',
        "SET reusable=1,replay_rejection_reason=NULL,accepted_content_hash=?,",
        "SET status='failed',reusable=0,replay_rejection_reason=?",
    ):
        if literal not in proof_source:
            findings.append(f"proof_reuse_contract_missing:{literal}")

    # Exact ExecutionHash definition and single Worker stay untouched.
    for literal in (
        '"inputArtifactRef": binding.get("inputArtifactRef")',
        '"inputContentHash": binding.get("inputContentHash")',
        '"provider": provider',
        '"model": model',
        '"generationParametersHash": generation_hash',
    ):
        if literal not in exact_source:
            findings.append(f"exact_execution_identity_missing:{literal}")
    if "secondWorkerAllowed=False" not in worker_source:
        findings.append("single_worker_contract_missing")

    target = spec.get("targetRuntime") or {}
    if target.get("newCacheTableAllowed") is not False:
        findings.append("new_cache_table_not_fail_closed")
    if target.get("requestCacheEnabled") is not False:
        findings.append("request_cache_must_remain_disabled")
    if target.get("itemResultCacheEnabled") is not True:
        findings.append("item_result_cache_not_enabled")
    if target.get("nonReadyChannelsCached") is not False:
        findings.append("nonready_channels_must_not_be_cached")
    if target.get("repairCacheAllowed") is not False:
        findings.append("repair_cache_must_be_disabled")
    if target.get("regenerationCacheAllowed") is not False:
        findings.append("regeneration_cache_must_be_disabled")

    for key, value in (spec.get("invariants") or {}).items():
        if value is not False:
            findings.append(f"governance_invariant_not_false:{key}")

    registry_policy = spec.get("registryPolicy") or {}
    if registry_policy.get("runtimeProjectionUpdateRequired") is not False:
        findings.append("runtime_projection_update_unexpected")
    if registry_policy.get("activeBindingOwnerUpdateRequired") is not False:
        findings.append("active_binding_owner_update_unexpected")

    material = {
        "schema": "competition.agent2_family_payload_semantic_cache.report.v1",
        "version": spec.get("version"),
        "agent2Runner": agent2.get("runner"),
        "semanticIdentitySchema": target.get("semanticIdentitySchema"),
        "semanticCacheContractVersion": target.get("semanticCacheContractVersion"),
        "cachedChannel": target.get("cachedChannel"),
        "requestCacheEnabled": target.get("requestCacheEnabled"),
        "itemResultCacheEnabled": target.get("itemResultCacheEnabled"),
        "findings": findings,
    }
    report = {**material, "verified": not findings, "verificationHash": _hash(material)}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
