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
    spec = _json("governance/agent3_semantic_sop_microbatch_v1.json")
    registry = _json("config/v23_registry_runtime.json")
    runtime = _read("src/services/agent3_runtime_v23215_service.py")
    facade = _read("src/services/agent_token_runtime_v225_service.py")
    contract = _read("src/services/agent_runtime_contract_v225_service.py")
    core = _read("src/services/agent3_sop_core_v225_service.py")
    system_constraint = _read("src/services/agent3_system_constraint_v23215_service.py")
    worker = _read("src/services/station_agent_worker_v2259_service.py")
    exact_runtime = _read("src/services/hash_directed_artifact_runtime_v2259_service.py")

    modules = registry.get("modules") or {}
    agent3 = modules.get("agent3_runtime") or {}
    if agent3.get("runner") != "src.services.pipeline_agent3_sop_v225_service:run_agent3_sop_microbatch_v225":
        findings.append("agent3_registry_runner_changed")

    required_runtime = [
        'AGENT3_PERFORMANCE_VERSION = "23.2.17"',
        'AGENT3_SEMANTIC_IDENTITY_SCHEMA = "agent3.semantic_sop_identity.v1"',
        "def build_agent3_semantic_identity(",
        "def _accepted_semantic_sop(",
        "def _rebind_semantic_sop(",
        "def _store_semantic_rebound_output(",
        "def _provider_batch_once(",
        "def run_agent3_sop_provider_isolated(",
        "split_envelopes_by_budget(",
        'source.get("agent3SemanticCacheSeedEligible") is not True',
        'source.get("semanticResultCacheHit") is True',
        'source.get("auxiliaryConditionRepairApplied") is True',
        'semanticReplayValidated=True',
        'providerCallExecutedForCurrentResult": False',
        'created_by="agent3_runtime_v23215_semantic_sop_rebind"',
        'semanticCacheCreatesNewOutputArtifact": True',
        'semanticCacheRevalidatesCurrentSystemContract": True',
        'dynamicMicrobatchEnabled": True',
        'compatiblePolicyGroupingRequired": True',
        'parallelProviderCallsAllowed": False',
        'requestCacheEnabled": False',
        'itemResultCacheEnabled": True',
        'cachedOutputRebindingScope": "sop_business_body_then_current_system_revalidation"',
    ]
    for literal in required_runtime:
        if literal not in runtime:
            findings.append(f"runtime_contract_missing:{literal}")

    forbidden_runtime = [
        "ThreadPoolExecutor",
        "asyncio.gather",
        "threading.Thread(",
        "requestCacheEnabled=True",
        "parallelProviderCallsAllowed=True",
        "CREATE TABLE IF NOT EXISTS agent3_semantic",
        "CREATE TABLE IF NOT EXISTS sop_cache",
        "auxiliaryRepairMaxAttemptsPerItem\": 2",
    ]
    for literal in forbidden_runtime:
        if literal in runtime:
            findings.append(f"runtime_forbidden_behavior:{literal}")

    required_facade = [
        "def run_agent3_sop_projected_inputs(envelopes, *args, **kwargs):",
        'if requested_int <= 1:',
        'kwargs["max_items_per_call"] = 2',
        "_run_agent3_sop_projected_inputs_v23217",
    ]
    for literal in required_facade:
        if literal not in facade:
            findings.append(f"active_facade_missing:{literal}")

    required_contract = [
        "def _valid_agent3_execution_proof(",
        'semantic_replay_validated = proof.get("semanticReplayValidated") is True',
        "or semantic_replay_validated",
        '"agent3SemanticReplayTraceAllowed": proof.get("semanticReplayValidated") is True',
    ]
    for literal in required_contract:
        if literal not in contract:
            findings.append(f"agent3_execution_contract_missing:{literal}")

    for literal in (
        'AGENT3_SOP_CORE_VERSION = "23.2.15"',
        "def _normalize_sop(",
        "repairable_agent3_auxiliary_missing",
        "maxAttempts\": 1",
    ):
        if literal not in core and literal not in runtime:
            findings.append(f"agent3_core_contract_missing:{literal}")

    for literal in (
        'AGENT3_SYSTEM_CONSTRAINT_VERSION = "23.2.15"',
        "def compile_agent3_provider_package(",
        "def validate_agent3_sop_system_contract(",
        '"maxFieldRepairAttempts": policy["maxAuxiliaryRepairAttempts"]',
    ):
        if literal not in system_constraint:
            findings.append(f"agent3_system_constraint_missing:{literal}")

    if "secondWorkerAllowed=False" not in worker:
        findings.append("single_worker_contract_missing")

    for literal in (
        '"inputArtifactRef": binding.get("inputArtifactRef")',
        '"inputContentHash": binding.get("inputContentHash")',
        '"provider": provider',
        '"model": model',
        '"generationParametersHash": generation_hash',
    ):
        if literal not in exact_runtime:
            findings.append(f"exact_execution_identity_missing:{literal}")

    semantic = spec.get("semanticCache") or {}
    microbatch = spec.get("microbatch") or {}
    proof = spec.get("proof") or {}
    if semantic.get("requestCacheEnabled") is not False:
        findings.append("request_cache_must_remain_disabled")
    if semantic.get("itemResultCacheEnabled") is not True:
        findings.append("semantic_item_cache_not_enabled")
    if semantic.get("sourceAuxiliaryRepairAttempted") is not False:
        findings.append("repaired_source_cache_must_be_disabled")
    if semantic.get("crossProductReuseAllowed") is not False:
        findings.append("cross_product_reuse_must_be_disabled")
    if microbatch.get("defaultCapacity") != 2:
        findings.append("agent3_default_microbatch_capacity_not_two")
    if microbatch.get("parallelProviderCallsAllowed") is not False:
        findings.append("parallel_provider_calls_must_be_disabled")
    if microbatch.get("secondWorkerAllowed") is not False:
        findings.append("second_worker_must_be_disabled")
    if proof.get("semanticReplayProviderCallExecuted") is not False:
        findings.append("semantic_replay_must_not_fake_provider_call")

    for key, value in (spec.get("invariants") or {}).items():
        if value is not False:
            findings.append(f"governance_invariant_not_false:{key}")

    registry_policy = spec.get("registryPolicy") or {}
    if registry_policy.get("runtimeProjectionUpdateRequired") is not False:
        findings.append("runtime_projection_update_unexpected")
    if registry_policy.get("activeBindingOwnerUpdateRequired") is not False:
        findings.append("active_binding_owner_update_unexpected")

    material = {
        "schema": "competition.agent3_semantic_sop_microbatch.report.v1",
        "version": spec.get("version"),
        "agent3Runner": agent3.get("runner"),
        "semanticIdentitySchema": semantic.get("semanticIdentitySchema"),
        "cacheContractVersion": semantic.get("cacheContractVersion"),
        "defaultMicrobatchCapacity": microbatch.get("defaultCapacity"),
        "parallelProviderCallsAllowed": microbatch.get("parallelProviderCallsAllowed"),
        "findings": findings,
    }
    report = {**material, "verified": not findings, "verificationHash": _hash(material)}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
