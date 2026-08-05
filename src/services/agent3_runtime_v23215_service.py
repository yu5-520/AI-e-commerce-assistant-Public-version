"""V23.2.15 Agent3 runtime with one isolated auxiliary-condition repair attempt."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from src.services import agent3_sop_core_v225_service as core
from src.services import agent_token_runtime_v2259_service as hash_runtime
from src.services.agent_input_contract_v225_service import (
    AGENT3_SOP_INPUT_SCHEMA,
    assert_agent_input_envelope,
)
from src.services.llm_gateway_v196_service import call_json

AGENT3_RUNTIME_VERSION = "23.2.15"
AGENT3_STAGE = "task_mapping_agent"
AGENT3_OUTPUT_TYPE = "agent3_model_output.v23215"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _usage_summary(usage: Dict[str, Any], *, call_type: str) -> Dict[str, Any]:
    return {
        "callType": call_type,
        "provider": usage.get("provider"),
        "model": usage.get("model"),
        "providerRequestId": usage.get("providerRequestId"),
        "providerCallExecuted": bool(usage.get("providerCallExecuted")),
        "actualCalls": 1 if usage.get("providerCallExecuted") else 0,
        "inputTokens": int(usage.get("input") or 0),
        "outputTokens": int(usage.get("output") or 0),
        "reasoningTokens": int(usage.get("reasoningTokens") or 0),
        "inputFingerprint": usage.get("inputFingerprint"),
        "projectionVersion": usage.get("projectionVersion"),
        "gatewayVersion": usage.get("gatewayVersion"),
    }


def _execution_proof(
    usage: Dict[str, Any],
    *,
    package_id: str,
    result_matched: bool,
    call_type: str,
) -> Dict[str, Any]:
    provider_call = bool(usage.get("providerCallExecuted"))
    provider_request_id = _text(usage.get("providerRequestId"), 300)
    proof = {
        "version": AGENT3_RUNTIME_VERSION,
        "stage": "agent3_sop_agent" if call_type == "initial" else "agent3_auxiliary_condition_repair",
        "callType": call_type,
        "packageId": package_id,
        "semanticCallId": core.semantic_call_id(
            input_fingerprint=_text(usage.get("inputFingerprint"), 500),
            provider_request_id=provider_request_id,
            package_id=f"{package_id}:{call_type}",
        ),
        "provider": usage.get("provider"),
        "model": usage.get("model"),
        "providerRequestId": provider_request_id if provider_call else None,
        "providerCallExecuted": provider_call,
        "exactReplayValidated": False,
        "itemCorrelationId": package_id,
        "resultMatched": bool(result_matched),
        "resultOrigin": "provider_call",
        "inputFingerprint": usage.get("inputFingerprint"),
        "promptVersion": (
            core.AGENT3_SOP_CORE_VERSION
            if call_type == "initial"
            else f"{core.AGENT3_SOP_CORE_VERSION}.auxiliary-repair"
        ),
        "projectionVersion": usage.get("projectionVersion"),
        "gatewayVersion": usage.get("gatewayVersion"),
        "fallbackUsed": False,
    }
    proof["passed"] = bool(
        proof["resultMatched"]
        and proof["providerCallExecuted"]
        and proof["providerRequestId"]
        and proof["semanticCallId"]
        and not proof["fallbackUsed"]
    )
    return proof


def _raw_sop(payload: Dict[str, Any], package_id: str) -> Dict[str, Any] | None:
    values = payload.get("sops") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise ValueError("agent3_json_missing_sops_array")
    return next(
        (
            item
            for item in values
            if isinstance(item, dict) and _text(item.get("packageId"), 220) == package_id
        ),
        None,
    )


def run_agent3_sop_provider_isolated(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 1,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Call the Provider without pipeline mutation or exact-output Artifact storage."""
    del max_items_per_call
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT3_SOP_INPUT_SCHEMA)
        valid.append(envelope)
    if not valid:
        return {}, {
            "version": AGENT3_RUNTIME_VERSION,
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "itemProvenance": {},
            "runtimeSource": "agent3SopInputRef",
            "auxiliaryRepairMaxAttempts": 1,
            "fallbackAllowed": False,
        }

    outputs: Dict[str, Dict[str, Any]] = {}
    proofs: Dict[str, Dict[str, Any]] = {}
    provider_calls: List[Dict[str, Any]] = []
    errors: List[str] = []
    repair_attempts = 0
    repair_applied = 0

    for envelope in valid:
        package = _dict(envelope.get("payload"))
        package_id = _text(package.get("packageId") or package.get("itemId"), 220)
        try:
            messages, cache_payload = core._build_messages(data_version, [package])
            payload, usage = call_json(
                stage=AGENT3_STAGE,
                prompt_version=core.AGENT3_SOP_CORE_VERSION,
                messages=messages,
                temperature=0.2,
                timeout_seconds=int(os.getenv("AGENT3_SOP_TIMEOUT", "300")),
                cache_payload=cache_payload,
                cache_enabled=False,
            )
            usage = _dict(usage)
            provider_calls.append(_usage_summary(usage, call_type="initial"))
            raw = _raw_sop(_dict(payload), package_id)
            initial_proof = _execution_proof(
                usage,
                package_id=package_id,
                result_matched=isinstance(raw, dict),
                call_type="initial",
            )
            proofs[package_id] = initial_proof
            if not isinstance(raw, dict):
                errors.append(f"{package_id}:agent3_response_package_unmatched")
                continue
            if not initial_proof.get("passed"):
                errors.append(f"{package_id}:agent3_provider_proof_invalid")
                continue

            normalized = core._normalize_sop(raw, package, initial_proof)
            validation = _dict(normalized.get("contractValidation"))
            repairable = core.repairable_agent3_auxiliary_missing(validation.get("missing"))
            if validation.get("repairableAuxiliaryOnly") is True and repairable:
                repair_attempts += 1
                repair_messages, repair_cache_payload = core._build_auxiliary_repair_messages(
                    data_version,
                    package,
                    raw,
                    normalized,
                )
                repair_payload, repair_usage = call_json(
                    stage=AGENT3_STAGE,
                    prompt_version=f"{core.AGENT3_SOP_CORE_VERSION}.auxiliary-repair",
                    messages=repair_messages,
                    temperature=0.05,
                    timeout_seconds=int(os.getenv("AGENT3_AUXILIARY_REPAIR_TIMEOUT", "180")),
                    cache_payload=repair_cache_payload,
                    cache_enabled=False,
                )
                repair_usage = _dict(repair_usage)
                provider_calls.append(_usage_summary(repair_usage, call_type="auxiliary_repair"))
                repair_proof = _execution_proof(
                    repair_usage,
                    package_id=package_id,
                    result_matched=isinstance(_dict(repair_payload).get("repair"), dict),
                    call_type="auxiliary_repair",
                )
                if repair_proof.get("passed"):
                    patched_raw = core.apply_agent3_auxiliary_repair(
                        raw,
                        _dict(repair_payload),
                        package_id=package_id,
                    )
                    repaired = core._normalize_sop(patched_raw, package, initial_proof)
                    repaired["agent3AuxiliaryRepairProof"] = repair_proof
                    repaired["agent3AuxiliaryRepair"] = {
                        "version": AGENT3_RUNTIME_VERSION,
                        "attempted": True,
                        "applied": True,
                        "maxAttempts": 1,
                        "repairedFields": ["stopConditions", "rollbackConditions"],
                        "executionStepsImmutable": True,
                        "originalMissing": repairable,
                        "finalMissing": _arr(_dict(repaired.get("contractValidation")).get("missing")),
                    }
                    normalized = repaired
                    repair_applied += 1
                else:
                    normalized["agent3AuxiliaryRepair"] = {
                        "version": AGENT3_RUNTIME_VERSION,
                        "attempted": True,
                        "applied": False,
                        "maxAttempts": 1,
                        "executionStepsImmutable": True,
                        "originalMissing": repairable,
                        "failureCode": "agent3_auxiliary_repair_provider_proof_invalid",
                    }
            else:
                normalized["agent3AuxiliaryRepair"] = {
                    "version": AGENT3_RUNTIME_VERSION,
                    "attempted": False,
                    "applied": False,
                    "maxAttempts": 1,
                    "executionStepsImmutable": True,
                    "reason": "not_auxiliary_only_or_no_repair_needed",
                }
            outputs[package_id] = normalized
        except Exception as exc:
            errors.append(f"{package_id}:{str(exc)[:450]}")

    return outputs, {
        "version": AGENT3_RUNTIME_VERSION,
        "stage": "agent3_sop_agent",
        "providerStatus": (
            "ok"
            if len(outputs) == len(valid) and not errors
            else "partial"
            if outputs
            else "failed"
        ),
        "actualCalls": sum(int(item.get("actualCalls") or 0) for item in provider_calls),
        "inputTokens": sum(int(item.get("inputTokens") or 0) for item in provider_calls),
        "outputTokens": sum(int(item.get("outputTokens") or 0) for item in provider_calls),
        "reasoningTokens": sum(int(item.get("reasoningTokens") or 0) for item in provider_calls),
        "itemProvenance": proofs,
        "providerCalls": provider_calls,
        "errors": errors,
        "sopCount": len(outputs),
        "auxiliaryRepairAttempts": repair_attempts,
        "auxiliaryRepairApplied": repair_applied,
        "auxiliaryRepairMaxAttemptsPerItem": 1,
        "runtimeSource": "agent3SopInputRef",
        "requestCacheEnabled": False,
        "hardInputContract": True,
        "fallbackAllowed": False,
    }


def run_agent3_sop_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 1,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Hash-directed pipeline runtime with V23.2.15 Provider generation and repair."""
    del max_items_per_call
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT3_SOP_INPUT_SCHEMA)
        valid.append(envelope)
    outputs: Dict[str, Dict[str, Any]] = {}
    provider_calls: List[Dict[str, Any]] = []
    errors: List[str] = []
    replay_count = 0

    for envelope in valid:
        descriptor = hash_runtime._binding_descriptor(
            envelope,
            expected_schema=AGENT3_SOP_INPUT_SCHEMA,
            stage=AGENT3_STAGE,
            prompt_version=core.AGENT3_SOP_CORE_VERSION,
            temperature=0.2,
        )
        package_id = hash_runtime._package_id(envelope)
        replay = hash_runtime.accepted_execution(str(descriptor["executionHash"]))
        cached = hash_runtime._cached_business_output(replay)
        if cached:
            replay_count += 1
            outputs[package_id] = hash_runtime._decorate_output(
                cached,
                descriptor=descriptor,
                output_artifact_ref=str(replay.get("outputArtifactRef") or ""),
                output_content_hash=str(replay.get("outputContentHash") or ""),
                raw_batch_output_ref=None,
                replay=True,
            )
            continue
        claim = hash_runtime.claim_execution(descriptor)
        if claim.get("status") != "claimed":
            errors.append(f"{package_id}:execution_already_running")
            continue
        batch = hash_runtime.create_batch_manifest(
            stage=AGENT3_STAGE,
            descriptors=[descriptor],
            data_version=data_version,
            prompt_version=core.AGENT3_SOP_CORE_VERSION,
            provider=descriptor.get("provider") or "",
            model=descriptor.get("model") or "",
        )
        try:
            sops, provider = run_agent3_sop_provider_isolated(
                [envelope],
                data_version=data_version,
                max_items_per_call=1,
            )
            provider_calls.append(provider)
            sop = sops.get(package_id)
            accepted_ids: List[str] = []
            if isinstance(sop, dict):
                outputs[package_id] = hash_runtime._wrap_downstream_output(
                    envelope=envelope,
                    descriptor=descriptor,
                    claim=claim,
                    output=sop,
                    artifact_type=AGENT3_OUTPUT_TYPE,
                )
                accepted_ids.append(str(descriptor.get("itemExecutionId") or ""))
            else:
                hash_runtime.fail_execution(
                    descriptor,
                    claim_id=str(claim.get("claimId") or ""),
                    error="agent3_exact_output_missing",
                )
            hash_runtime.finalize_batch(
                batch=batch,
                returned_item_execution_ids=accepted_ids,
                accepted_item_execution_ids=accepted_ids,
                raw_batch_output_ref=None,
            )
        except Exception as exc:
            hash_runtime.fail_execution(
                descriptor,
                claim_id=str(claim.get("claimId") or ""),
                error=str(exc),
            )
            errors.append(f"{package_id}:{str(exc)[:500]}")

    return outputs, {
        "version": AGENT3_RUNTIME_VERSION,
        "stage": "agent3_sop_agent",
        "providerStatus": (
            "ok"
            if len(outputs) == len(valid) and not errors
            else "partial"
            if outputs
            else "failed"
        ),
        "actualCalls": sum(int(item.get("actualCalls") or 0) for item in provider_calls),
        "exactExecutionReplayCount": replay_count,
        "providerCalls": provider_calls,
        "errors": errors,
        "sopCount": len(outputs),
        "auxiliaryRepairAttempts": sum(int(item.get("auxiliaryRepairAttempts") or 0) for item in provider_calls),
        "auxiliaryRepairApplied": sum(int(item.get("auxiliaryRepairApplied") or 0) for item in provider_calls),
        "runtimeSource": "agent3SopInputArtifact",
        "hashDirectedExecution": True,
        "requestCacheEnabled": False,
        "cachedOutputRebindingAllowed": False,
        "fallbackAllowed": False,
    }


__all__ = [
    "AGENT3_RUNTIME_VERSION",
    "run_agent3_sop_provider_isolated",
    "run_agent3_sop_projected_inputs",
]
