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
    spec = _json("governance/agent1_ready_first_runtime_v1.json")
    registry = _json("config/v23_registry_runtime.json")
    facade_source = _read("src/services/agent_runtime_hard_interface_v22515_service.py")
    exact_source = _read("src/services/agent_token_runtime_hash_exact_v2259_service.py")
    worker_source = _read("src/services/station_agent_worker_v2259_service.py")

    agent1 = ((registry.get("modules") or {}).get("agent1_runtime") or {})
    binding = (agent1.get("activeBindingProbe") or {}).get("expectedOwners") or {}
    expected_stage_owner = "src.services.agent_runtime_hard_interface_v2257_service"
    if binding.get("agent1StageOwner") != expected_stage_owner:
        findings.append("registered_agent1_stage_owner_changed")
    if "src/services/agent1_ready_first_runtime_v1_service.py" in (agent1.get("implementationPaths") or []):
        findings.append("unexpected_new_agent1_runtime_member")
    if agent1.get("runner") != "src.services.agent_token_runtime_hash_exact_v2259_service:run_agent1_projected_inputs":
        findings.append("exact_hash_token_runner_changed")

    required_facade_literals = [
        'AGENT1_READY_FIRST_RUNTIME_VERSION = "23.1.5"',
        'AGENT1_READY_FIRST_POLICY = "ready_first_dynamic_char_budget"',
        "AGENT1_MAX_BATCH_CHARS",
        "def plan_agent1_ready_first_batch(",
        "def run_agent1_ready_first_microbatch_hard(",
        'claimScope="current_provider_subbatch_only"',
        "waitForFullCapacity=False",
        "legacy.run_agent1_microbatch_hard",
        '"agent1StageOwner": "src.services.agent_runtime_hard_interface_v2257_service"',
        '"agent1ClaimScope": "current_provider_subbatch_only"',
        '"selectedStage": "agent1_ready_first_to_exact_hash_judgment"',
    ]
    for literal in required_facade_literals:
        if literal not in facade_source:
            findings.append(f"facade_ready_first_literal_missing:{literal}")

    forbidden_facade_literals = [
        "ThreadPoolExecutor",
        "asyncio.gather",
        "threading.Thread(",
        "call_json(",
        "call_json_exact_artifact(",
        "provider_runtime_config(",
    ]
    for literal in forbidden_facade_literals:
        if literal in facade_source:
            findings.append(f"ready_first_forbidden_runtime_behavior:{literal}")

    # This patch fixes claim granularity first. Semantic replay remains disabled until
    # the separate dual-hash cache update is introduced and verified.
    for literal in ("requestCacheEnabled=False", "itemResultCacheEnabled=False"):
        if literal not in exact_source:
            findings.append(f"exact_runtime_cache_policy_changed:{literal}")

    if "secondWorkerAllowed=False" not in worker_source:
        findings.append("single_worker_contract_missing")

    invariants = spec.get("invariants") or {}
    for key in (
        "registryRootRotated",
        "runtimeMembershipChanged",
        "agent1StageOwnerChanged",
        "signalAdmissionChanged",
        "providerChanged",
        "modelChanged",
        "promptChanged",
        "temperatureChanged",
        "thinkingChanged",
        "tokenBudgetChanged",
        "secondWorkerAllowed",
        "exactExecutionHashChanged",
        "cachedOutputRebindingEnabled",
        "agent2Changed",
        "agent3Changed",
        "taskMappingChanged",
        "taskPoolChanged",
    ):
        if invariants.get(key) is not False:
            findings.append(f"governance_invariant_not_false:{key}")

    registry_policy = spec.get("registryPolicy") or {}
    if registry_policy.get("runtimeProjectionUpdateRequired") is not False:
        findings.append("runtime_projection_update_unexpected")
    if registry_policy.get("activeBindingOwnerUpdateRequired") is not False:
        findings.append("active_binding_owner_update_unexpected")

    material = {
        "schema": "competition.agent1_ready_first_runtime.report.v1",
        "version": spec.get("version"),
        "activeFacade": (spec.get("baseRuntime") or {}).get("activeFacade"),
        "agent1StageOwner": binding.get("agent1StageOwner"),
        "agent1Runner": agent1.get("runner"),
        "claimScope": (spec.get("targetRuntime") or {}).get("claimScope"),
        "singleWorkerRequired": (spec.get("baseRuntime") or {}).get("singleWorkerRequired"),
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
