"""V23.2.15 Agent3 runtime with V23.2.17 semantic SOP reuse and microbatching.

ExecutionHash remains the exact execution/audit authority. A second SemanticHash may
reuse only a previously accepted, current-contract-valid Agent3 SOP business body.
The old result is never rebound as the current Artifact: current package/system fields
are re-normalized, the current system constraint is revalidated, and a new immutable
output Artifact is created under the current ExecutionHash.

Initial Provider calls are packed by compatible action-family/company-policy context
and the existing Agent3 batch character budget. Auxiliary repair remains singleton,
at most once per item. No second Worker or async fan-out is introduced.
"""
from __future__ import annotations

import os
from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, loads
from src.services import agent3_sop_core_v225_service as core
from src.services import agent_token_runtime_v2259_service as hash_runtime
from src.services.agent_input_contract_v225_service import (
    AGENT3_SOP_INPUT_SCHEMA,
    assert_agent_input_envelope,
    split_envelopes_by_budget,
)
from src.services.artifact_transport_service import (
    resolve_artifact,
    store_artifact,
    validate_artifact,
)
from src.services.llm_gateway_v196_service import call_json

AGENT3_RUNTIME_VERSION = "23.2.15"
AGENT3_PERFORMANCE_VERSION = "23.2.17"
AGENT3_SEMANTIC_IDENTITY_SCHEMA = "agent3.semantic_sop_identity.v1"
AGENT3_STAGE = "task_mapping_agent"
AGENT3_OUTPUT_TYPE = "agent3_model_output.v23215"

_VOLATILE_SEMANTIC_KEYS = {
    "packageId",
    "itemId",
    "dataVersion",
    "correlationId",
    "signalId",
    "createdAt",
    "updatedAt",
    "startedAt",
    "finishedAt",
    "acceptedAt",
    "executionId",
    "itemExecutionId",
    "executionHash",
    "runtimeExecutionHash",
    "replayKeyHash",
    "semanticInputHash",
    "semanticHash",
    "attemptNo",
    "executionMode",
    "sourceExecutionHash",
    "inputArtifactRef",
    "inputContentHash",
    "outputArtifactRef",
    "outputContentHash",
    "rawBatchOutputRef",
    "artifactHash",
    "artifactRefs",
    "agent2DraftExecutionProof",
    "agent2DraftHashExecutionProof",
    "agent3ExecutionProof",
    "providerRequestId",
    "semanticCallId",
    "exactExecutionReplay",
    "semanticReplayValidated",
    "semanticResultCacheHit",
    "cachedOutputRebound",
    "hashDirectedRuntimeVersion",
}

_SEMANTIC_SOP_BODY_KEYS = (
    "sopStatus",
    "finalTaskTitle",
    "executionObjective",
    "executionSteps",
    "decisionBranches",
    "submissionEvidence",
    "crossDepartmentActions",
    "approvalFlow",
    "reviewMetrics",
    "verificationPeriod",
    "stopConditions",
    "rollbackConditions",
    "reviewCycle",
    "companyStyleReason",
    "ragUsedCaseIds",
    "ragRejectedCaseIds",
    "ragApplicationReason",
)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _solid_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _solid_value(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_SEMANTIC_KEYS
        }
    if isinstance(value, list):
        return [_solid_value(item) for item in value]
    return value


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
        "performanceVersion": AGENT3_PERFORMANCE_VERSION,
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
        "semanticReplayValidated": False,
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


def _semantic_execution_proof(
    descriptor: Dict[str, Any],
    *,
    package_id: str,
    source_execution_hash: str,
    source_output_ref: str,
) -> Dict[str, Any]:
    semantic_hash = _text(descriptor.get("semanticHash"), 160)
    proof = {
        "version": AGENT3_RUNTIME_VERSION,
        "performanceVersion": AGENT3_PERFORMANCE_VERSION,
        "stage": "agent3_sop_agent",
        "callType": "semantic_replay",
        "packageId": package_id,
        "semanticCallId": core.semantic_call_id(
            input_fingerprint=semantic_hash,
            provider_request_id="semantic-cache:" + source_execution_hash,
            package_id=f"{package_id}:semantic_replay",
        ),
        "provider": descriptor.get("provider"),
        "model": descriptor.get("model"),
        "providerRequestId": None,
        "providerCallExecuted": False,
        "providerCallExecutedForCurrentResult": False,
        "exactReplayValidated": False,
        "semanticReplayValidated": True,
        "itemCorrelationId": package_id,
        "resultMatched": True,
        "resultOrigin": "accepted_semantic_sop_artifact",
        "semanticHash": semantic_hash,
        "semanticCacheSourceExecutionHash": source_execution_hash,
        "semanticCacheSourceOutputRef": source_output_ref,
        "promptVersion": core.AGENT3_SOP_CORE_VERSION,
        "fallbackUsed": False,
        "passed": True,
    }
    return proof


def _provider_compatibility_material(package: Dict[str, Any]) -> Dict[str, Any]:
    compiled = core.compile_agent3_provider_package(package)
    return {
        "lockedActionFamily": compiled.get("lockedActionFamily"),
        "companyContext": compiled.get("companyContext"),
        "allowedActionTypes": compiled.get("allowedActionTypes"),
        "requiredActionTypeGroups": compiled.get("requiredActionTypeGroups"),
        "forbiddenActions": compiled.get("forbiddenActions"),
        "outputStepContract": compiled.get("outputStepContract"),
        "allowedStopConditionTypes": compiled.get("allowedStopConditionTypes"),
        "allowedRollbackConditionTypes": compiled.get("allowedRollbackConditionTypes"),
        "auxiliaryConditionContract": compiled.get("auxiliaryConditionContract"),
        "systemConstraintContract": compiled.get("systemConstraintContract"),
    }


def build_agent3_semantic_identity(
    envelope: Dict[str, Any],
    descriptor: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    compiled = core.compile_agent3_provider_package(package)
    semantic_business = _solid_value(compiled)
    semantic_input_hash = hash_runtime.hash_value(semantic_business)
    semantic_contract = {
        "semanticCacheVersion": AGENT3_PERFORMANCE_VERSION,
        "stage": descriptor.get("stage") or AGENT3_STAGE,
        "inputSchema": descriptor.get("inputSchema") or AGENT3_SOP_INPUT_SCHEMA,
        "projectionVersion": descriptor.get("projectionVersion"),
        "promptVersion": descriptor.get("promptVersion"),
        "agent3CoreVersion": core.AGENT3_SOP_CORE_VERSION,
        "agent3SystemConstraintVersion": core.AGENT3_SYSTEM_CONSTRAINT_VERSION,
        "policyHash": descriptor.get("policyHash"),
        "provider": descriptor.get("provider"),
        "model": descriptor.get("model"),
        "generationParametersHash": descriptor.get("generationParametersHash"),
    }
    semantic_contract_hash = hash_runtime.hash_value(semantic_contract)
    semantic_hash = hash_runtime.hash_value(
        {
            "schema": AGENT3_SEMANTIC_IDENTITY_SCHEMA,
            "semanticInputHash": semantic_input_hash,
            "semanticContractHash": semantic_contract_hash,
        }
    )
    compatibility_hash = hash_runtime.hash_value(
        {
            "performanceVersion": AGENT3_PERFORMANCE_VERSION,
            "policy": _provider_compatibility_material(package),
            "promptVersion": descriptor.get("promptVersion"),
            "provider": descriptor.get("provider"),
            "model": descriptor.get("model"),
            "generationParametersHash": descriptor.get("generationParametersHash"),
        }
    )
    return {
        "schema": AGENT3_SEMANTIC_IDENTITY_SCHEMA,
        "version": AGENT3_PERFORMANCE_VERSION,
        "semanticHash": semantic_hash,
        "semanticInputHash": semantic_input_hash,
        "semanticContractHash": semantic_contract_hash,
        "batchCompatibilityHash": compatibility_hash,
        "cacheEligible": True,
        "packageAndExecutionIdentityExcluded": True,
        "crossProductReuseAllowed": False,
    }


def _entry(envelope: Dict[str, Any]) -> Dict[str, Any]:
    package = dict(_dict(envelope.get("payload")))
    descriptor = hash_runtime._binding_descriptor(
        envelope,
        expected_schema=AGENT3_SOP_INPUT_SCHEMA,
        stage=AGENT3_STAGE,
        prompt_version=core.AGENT3_SOP_CORE_VERSION,
        temperature=0.2,
    )
    semantic = build_agent3_semantic_identity(envelope, descriptor, package)
    descriptor.update(
        semanticHash=semantic.get("semanticHash"),
        semanticInputHash=semantic.get("semanticInputHash"),
        semanticContractHash=semantic.get("semanticContractHash"),
        semanticIdentitySchema=semantic.get("schema"),
        semanticCacheContractVersion=AGENT3_PERFORMANCE_VERSION,
        semanticCacheEligible=True,
        batchCompatibilityHash=semantic.get("batchCompatibilityHash"),
        actionFamily=package.get("lockedActionFamily")
        or _dict(package.get("agent2ActionDraft")).get("actionFamily"),
    )
    return {
        "envelope": envelope,
        "package": package,
        "descriptor": descriptor,
        "claim": {},
    }


def _raw_sops(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    values = payload.get("sops") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise ValueError("agent3_json_missing_sops_array")
    result: Dict[str, Dict[str, Any]] = {}
    duplicates: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        package_id = _text(value.get("packageId"), 220)
        if not package_id:
            continue
        if package_id in result:
            duplicates.add(package_id)
            continue
        result[package_id] = dict(value)
    if duplicates:
        raise ValueError(
            "agent3_duplicate_package_ids:" + ",".join(sorted(duplicates)[:8])
        )
    return result


def _semantic_sop_body(source: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: deepcopy(source.get(key))
        for key in _SEMANTIC_SOP_BODY_KEYS
        if source.get(key) not in (None, "", [], {})
    }


def _accepted_semantic_sop(descriptor: Dict[str, Any]) -> Dict[str, Any] | None:
    semantic_hash = _text(descriptor.get("semanticHash"), 160)
    if not semantic_hash or descriptor.get("semanticCacheEligible") is not True:
        return None
    hash_runtime.ensure_hash_directed_runtime_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT execution_hash,accepted_output_ref,accepted_output_hash,metadata_json,updated_at
            FROM artifact_execution_index_v2259
            WHERE stage=?
              AND status='accepted'
              AND accepted_output_ref IS NOT NULL
              AND metadata_json LIKE ?
            ORDER BY updated_at DESC
            LIMIT 64
            """,
            (AGENT3_STAGE, f"%{semantic_hash}%"),
        ).fetchall()
    for raw in rows:
        record = dict(raw)
        if _text(record.get("execution_hash"), 160) == _text(
            descriptor.get("executionHash"), 160
        ):
            continue
        metadata = loads(record.get("metadata_json")) if record.get("metadata_json") else {}
        if not isinstance(metadata, dict):
            continue
        if _text(metadata.get("semanticHash"), 160) != semantic_hash:
            continue
        if metadata.get("semanticCacheContractVersion") != AGENT3_PERFORMANCE_VERSION:
            continue
        if metadata.get("semanticCacheEligible") is not True:
            continue
        if _text(metadata.get("productId"), 160) != _text(descriptor.get("productId"), 160):
            continue
        if _text(metadata.get("storeId"), 160) != _text(descriptor.get("storeId"), 160):
            continue
        replay = hash_runtime.accepted_execution(str(record.get("execution_hash") or ""))
        source = hash_runtime._cached_business_output(replay)
        if not source:
            continue
        if source.get("agent3SemanticCacheSeedEligible") is not True:
            continue
        if source.get("semanticResultCacheHit") is True:
            continue
        if source.get("sopStatus") not in {core.SOP_READY, core.SOP_REQUIRES_APPROVAL}:
            continue
        if _arr(source.get("semanticContractMissing")):
            continue
        if _dict(source.get("contractValidation")).get("passed") is not True:
            continue
        if source.get("auxiliaryConditionRepairApplied") is True:
            continue
        if _dict(source.get("agent3AuxiliaryRepair")).get("attempted") is True:
            continue
        output_ref = _text(replay.get("outputArtifactRef"), 220)
        if not output_ref.startswith("ART-"):
            continue
        if validate_artifact(output_ref, expected_type=AGENT3_OUTPUT_TYPE).get("ok") is not True:
            continue
        return {
            "execution": record,
            "outputArtifactRef": output_ref,
            "outputContentHash": replay.get("outputContentHash"),
            "sop": source,
        }
    return None


def _rebind_semantic_sop(
    source: Dict[str, Any],
    *,
    entry: Dict[str, Any],
) -> Dict[str, Any] | None:
    source_sop = _dict(source.get("sop"))
    if not source_sop:
        return None
    source_execution = _dict(source.get("execution"))
    source_execution_hash = _text(source_execution.get("execution_hash"), 160)
    source_output_ref = _text(source.get("outputArtifactRef"), 220)
    package_id = _text(
        entry["package"].get("packageId") or entry["package"].get("itemId"), 220
    )
    proof = _semantic_execution_proof(
        entry["descriptor"],
        package_id=package_id,
        source_execution_hash=source_execution_hash,
        source_output_ref=source_output_ref,
    )
    rebound = core._normalize_sop(
        _semantic_sop_body(source_sop),
        entry["package"],
        proof,
    )
    validation = _dict(rebound.get("contractValidation"))
    if validation.get("passed") is not True:
        return None
    if rebound.get("sopStatus") not in {core.SOP_READY, core.SOP_REQUIRES_APPROVAL}:
        return None
    if core.missing_agent3_sop_contract(rebound, entry["package"]):
        return None
    rebound.update(
        semanticResultCacheHit=True,
        cachedOutputRebound=True,
        semanticReplayValidated=True,
        semanticHash=entry["descriptor"].get("semanticHash"),
        semanticCacheContractVersion=AGENT3_PERFORMANCE_VERSION,
        semanticCacheSourceExecutionHash=source_execution_hash,
        semanticCacheSourceOutputRef=source_output_ref,
        agent3ApiCallCount=0,
        agent3SemanticCacheSeedEligible=False,
        agent3AuxiliaryRepair={
            "version": AGENT3_RUNTIME_VERSION,
            "attempted": False,
            "applied": False,
            "maxAttempts": 1,
            "executionStepsImmutable": True,
            "reason": "semantic_cache_hit_current_contract_revalidated",
        },
        fallbackAllowed=False,
    )
    return rebound


def _store_semantic_rebound_output(
    *,
    entry: Dict[str, Any],
    sop: Dict[str, Any],
    source: Dict[str, Any],
) -> Dict[str, Any]:
    descriptor = entry["descriptor"]
    source_ref = _text(source.get("outputArtifactRef"), 220)
    value = {
        "schema": AGENT3_OUTPUT_TYPE,
        "version": hash_runtime.HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "itemExecutionId": descriptor.get("itemExecutionId"),
        "executionHash": descriptor.get("executionHash"),
        "inputArtifactRef": descriptor.get("inputArtifactRef"),
        "inputContentHash": descriptor.get("inputContentHash"),
        "rawBatchOutputRef": None,
        "stage": descriptor.get("stage"),
        "dataVersion": descriptor.get("dataVersion"),
        "semanticHash": descriptor.get("semanticHash"),
        "semanticCacheSourceOutputRef": source_ref or None,
        "output": sop,
    }
    parents = [
        ref
        for ref in (descriptor.get("inputArtifactRef"), source_ref)
        if str(ref or "").startswith("ART-")
    ]
    return store_artifact(
        artifact_type=AGENT3_OUTPUT_TYPE,
        value=value,
        schema_version=hash_runtime.HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        tenant_id=descriptor.get("tenantId"),
        store_id=descriptor.get("storeId"),
        product_id=descriptor.get("productId"),
        data_version=descriptor.get("dataVersion"),
        created_by="agent3_runtime_v23215_semantic_sop_rebind",
        parent_refs=parents,
        metadata={
            "stage": descriptor.get("stage"),
            "itemExecutionId": descriptor.get("itemExecutionId"),
            "executionHash": descriptor.get("executionHash"),
            "inputContentHash": descriptor.get("inputContentHash"),
            "semanticHash": descriptor.get("semanticHash"),
            "semanticCacheContractVersion": AGENT3_PERFORMANCE_VERSION,
            "semanticCacheSourceOutputRef": source_ref or None,
            "cachedOutputRebound": True,
        },
    )


def _provider_batch_once(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    packages = [dict(_dict(envelope.get("payload"))) for envelope in envelopes]
    package_ids = [
        _text(package.get("packageId") or package.get("itemId"), 220)
        for package in packages
    ]
    if not package_ids or any(not value for value in package_ids):
        raise ValueError("agent3_batch_package_id_missing")
    if len(set(package_ids)) != len(package_ids):
        raise ValueError("agent3_batch_package_id_duplicate")

    messages, cache_payload = core._build_messages(data_version, packages)
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
    raw_by_package = _raw_sops(_dict(payload))
    outputs: Dict[str, Dict[str, Any]] = {}
    proofs: Dict[str, Dict[str, Any]] = {}
    provider_calls: List[Dict[str, Any]] = [
        {
            **_usage_summary(usage, call_type="initial"),
            "batchSize": len(packages),
            "packageIds": package_ids,
        }
    ]
    errors: List[str] = []
    repair_attempts = 0
    repair_applied = 0

    for package in packages:
        package_id = _text(package.get("packageId") or package.get("itemId"), 220)
        raw = raw_by_package.get(package_id)
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
        repair_attempted = False
        if validation.get("repairableAuxiliaryOnly") is True and repairable:
            repair_attempted = True
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
            provider_calls.append(
                {
                    **_usage_summary(repair_usage, call_type="auxiliary_repair"),
                    "batchSize": 1,
                    "packageIds": [package_id],
                }
            )
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

        final_validation = _dict(normalized.get("contractValidation"))
        normalized.update(
            semanticResultCacheHit=False,
            cachedOutputRebound=False,
            semanticReplayValidated=False,
            agent3ApiCallCount=1,
            agent3SemanticCacheSeedEligible=bool(
                not repair_attempted
                and final_validation.get("passed") is True
                and normalized.get("sopStatus") in {core.SOP_READY, core.SOP_REQUIRES_APPROVAL}
                and not _arr(normalized.get("semanticContractMissing"))
            ),
            agent3ProviderInitialBatchSize=len(packages),
            fallbackAllowed=False,
        )
        outputs[package_id] = normalized

    return outputs, {
        "version": AGENT3_RUNTIME_VERSION,
        "performanceVersion": AGENT3_PERFORMANCE_VERSION,
        "stage": "agent3_sop_agent",
        "providerStatus": (
            "ok"
            if len(outputs) == len(packages) and not errors
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
        "initialProviderBatchCount": 1,
        "initialProviderBatchSize": len(packages),
        "auxiliaryRepairAttempts": repair_attempts,
        "auxiliaryRepairApplied": repair_applied,
        "auxiliaryRepairMaxAttemptsPerItem": 1,
        "runtimeSource": "agent3SopInputRef.compatibleMicrobatch.v23217",
        "requestCacheEnabled": False,
        "hardInputContract": True,
        "fallbackAllowed": False,
    }


def run_agent3_sop_provider_isolated(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 2,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Provider generation with compatible, char-budgeted initial microbatches."""
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT3_SOP_INPUT_SCHEMA)
        valid.append(envelope)
    if not valid:
        return {}, {
            "version": AGENT3_RUNTIME_VERSION,
            "performanceVersion": AGENT3_PERFORMANCE_VERSION,
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "itemProvenance": {},
            "runtimeSource": "agent3SopInputRef.compatibleMicrobatch.v23217",
            "auxiliaryRepairMaxAttempts": 1,
            "fallbackAllowed": False,
        }

    cap = max(1, min(6, int(max_items_per_call or 2)))
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for envelope in valid:
        package = _dict(envelope.get("payload"))
        key = hash_runtime.hash_value(_provider_compatibility_material(package))
        grouped[key].append(envelope)

    outputs: Dict[str, Dict[str, Any]] = {}
    calls: List[Dict[str, Any]] = []
    errors: List[str] = []
    initial_batches = 0
    repair_attempts = repair_applied = 0
    for compatibility_hash in sorted(grouped):
        batches = split_envelopes_by_budget(
            grouped[compatibility_hash],
            expected_schema=AGENT3_SOP_INPUT_SCHEMA,
            max_items=cap,
        )
        for batch in batches:
            batch_outputs, summary = _provider_batch_once(
                batch,
                data_version=data_version,
            )
            outputs.update(batch_outputs)
            calls.extend(_arr(summary.get("providerCalls")))
            errors.extend(str(value) for value in _arr(summary.get("errors")))
            initial_batches += 1
            repair_attempts += int(summary.get("auxiliaryRepairAttempts") or 0)
            repair_applied += int(summary.get("auxiliaryRepairApplied") or 0)

    return outputs, {
        "version": AGENT3_RUNTIME_VERSION,
        "performanceVersion": AGENT3_PERFORMANCE_VERSION,
        "stage": "agent3_sop_agent",
        "providerStatus": (
            "ok"
            if len(outputs) == len(valid) and not errors
            else "partial"
            if outputs
            else "failed"
        ),
        "actualCalls": sum(int(item.get("actualCalls") or 0) for item in calls),
        "inputTokens": sum(int(item.get("inputTokens") or 0) for item in calls),
        "outputTokens": sum(int(item.get("outputTokens") or 0) for item in calls),
        "reasoningTokens": sum(int(item.get("reasoningTokens") or 0) for item in calls),
        "providerCalls": calls,
        "errors": errors,
        "sopCount": len(outputs),
        "initialProviderBatchCount": initial_batches,
        "compatibleGroupCount": len(grouped),
        "maxItemsPerInitialProviderCall": cap,
        "dynamicMicrobatchEnabled": True,
        "batchBudgetContract": "AGENT3_MAX_BATCH_CHARS",
        "auxiliaryRepairAttempts": repair_attempts,
        "auxiliaryRepairApplied": repair_applied,
        "auxiliaryRepairMaxAttemptsPerItem": 1,
        "runtimeSource": "agent3SopInputRef.compatibleMicrobatch.v23217",
        "requestCacheEnabled": False,
        "hardInputContract": True,
        "fallbackAllowed": False,
    }


def run_agent3_sop_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 2,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Exact execution + SemanticHash reuse + compatible initial Provider microbatch."""
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT3_SOP_INPUT_SCHEMA)
        valid.append(envelope)
    if not valid:
        return {}, {
            "version": AGENT3_RUNTIME_VERSION,
            "performanceVersion": AGENT3_PERFORMANCE_VERSION,
            "stage": "agent3_sop_agent",
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "semanticSopCacheEnabled": True,
            "dynamicMicrobatchEnabled": True,
            "requestCacheEnabled": False,
            "fallbackAllowed": False,
        }

    cap = max(1, min(6, int(max_items_per_call or 2)))
    outputs: Dict[str, Dict[str, Any]] = {}
    provider_calls: List[Dict[str, Any]] = []
    errors: List[str] = []
    semantic_errors: List[str] = []
    claimed: List[Dict[str, Any]] = []
    replay_count = semantic_hit_count = semantic_miss_count = semantic_rebound_count = 0
    busy_count = 0

    for envelope in valid:
        try:
            entry = _entry(envelope)
            descriptor = entry["descriptor"]
            package_id = _text(entry["package"].get("packageId") or entry["package"].get("itemId"), 220)
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
            if claim.get("status") == "accepted_replay":
                cached = hash_runtime._cached_business_output(claim)
                if cached:
                    replay_count += 1
                    outputs[package_id] = hash_runtime._decorate_output(
                        cached,
                        descriptor=descriptor,
                        output_artifact_ref=str(claim.get("outputArtifactRef") or ""),
                        output_content_hash=str(claim.get("outputContentHash") or ""),
                        raw_batch_output_ref=None,
                        replay=True,
                    )
                continue
            if claim.get("status") != "claimed":
                busy_count += 1
                errors.append(f"{package_id}:execution_already_running")
                continue
            entry["claim"] = claim

            source = None
            try:
                source = _accepted_semantic_sop(descriptor)
            except Exception as exc:
                semantic_errors.append(f"{package_id}:lookup:{str(exc)[:420]}")
            rebound = None
            if source:
                try:
                    rebound = _rebind_semantic_sop(source, entry=entry)
                except Exception as exc:
                    semantic_errors.append(f"{package_id}:rebind:{str(exc)[:420]}")
            if rebound and source:
                try:
                    artifact = _store_semantic_rebound_output(
                        entry=entry,
                        sop=rebound,
                        source=source,
                    )
                    completion = hash_runtime.complete_execution(
                        descriptor,
                        claim_id=str(claim.get("claimId") or ""),
                        output_artifact_ref=str(artifact["artifactId"]),
                        output_content_hash=str(artifact["contentHash"]),
                        raw_batch_output_ref=None,
                    )
                    decorated = hash_runtime._decorate_output(
                        rebound,
                        descriptor=descriptor,
                        output_artifact_ref=str(
                            completion.get("outputArtifactRef") or artifact["artifactId"]
                        ),
                        output_content_hash=str(
                            completion.get("outputContentHash") or artifact["contentHash"]
                        ),
                        raw_batch_output_ref=None,
                        replay=False,
                    )
                    decorated.update(
                        semanticResultCacheHit=True,
                        cachedOutputRebound=True,
                        semanticReplayValidated=True,
                        semanticHash=descriptor.get("semanticHash"),
                        semanticCacheContractVersion=AGENT3_PERFORMANCE_VERSION,
                        semanticCacheSourceExecutionHash=rebound.get(
                            "semanticCacheSourceExecutionHash"
                        ),
                        semanticCacheSourceOutputRef=rebound.get(
                            "semanticCacheSourceOutputRef"
                        ),
                        agent3ApiCallCount=0,
                    )
                    outputs[package_id] = decorated
                    semantic_hit_count += 1
                    semantic_rebound_count += 1
                    continue
                except Exception as exc:
                    semantic_errors.append(f"{package_id}:persist:{str(exc)[:420]}")

            semantic_miss_count += 1
            claimed.append(entry)
        except Exception as exc:
            errors.append(f"prepare:{str(exc)[:500]}")

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in claimed:
        grouped[str(entry["descriptor"].get("batchCompatibilityHash") or "")].append(entry)

    provider_batch_count = 0
    provider_generated_item_count = 0
    for compatibility_hash in sorted(grouped):
        entries_for_group = grouped[compatibility_hash]
        by_object = {id(entry["envelope"]): entry for entry in entries_for_group}
        batches = split_envelopes_by_budget(
            [entry["envelope"] for entry in entries_for_group],
            expected_schema=AGENT3_SOP_INPUT_SCHEMA,
            max_items=cap,
        )
        for batch_envelopes in batches:
            batch_entries = [by_object[id(envelope)] for envelope in batch_envelopes]
            descriptors = [entry["descriptor"] for entry in batch_entries]
            batch_meta = hash_runtime.create_batch_manifest(
                stage=AGENT3_STAGE,
                descriptors=descriptors,
                data_version=data_version,
                prompt_version=core.AGENT3_SOP_CORE_VERSION,
                provider=str(descriptors[0].get("provider") or ""),
                model=str(descriptors[0].get("model") or ""),
            )
            accepted_ids: List[str] = []
            try:
                sops, provider = run_agent3_sop_provider_isolated(
                    batch_envelopes,
                    data_version=data_version,
                    max_items_per_call=len(batch_envelopes),
                )
                provider_batch_count += 1
                provider_calls.append(provider)
                for entry in batch_entries:
                    descriptor = entry["descriptor"]
                    package_id = _text(
                        entry["package"].get("packageId") or entry["package"].get("itemId"), 220
                    )
                    sop = sops.get(package_id)
                    if isinstance(sop, dict):
                        sop.update(
                            semanticHash=descriptor.get("semanticHash"),
                            semanticCacheContractVersion=AGENT3_PERFORMANCE_VERSION,
                            batchCompatibilityHash=descriptor.get("batchCompatibilityHash"),
                        )
                        outputs[package_id] = hash_runtime._wrap_downstream_output(
                            envelope=entry["envelope"],
                            descriptor=descriptor,
                            claim=entry["claim"],
                            output=sop,
                            artifact_type=AGENT3_OUTPUT_TYPE,
                        )
                        accepted_ids.append(str(descriptor.get("itemExecutionId") or ""))
                        provider_generated_item_count += 1
                    else:
                        hash_runtime.fail_execution(
                            descriptor,
                            claim_id=str(entry["claim"].get("claimId") or ""),
                            error="agent3_exact_output_missing",
                        )
                hash_runtime.finalize_batch(
                    batch=batch_meta,
                    returned_item_execution_ids=accepted_ids,
                    accepted_item_execution_ids=accepted_ids,
                    raw_batch_output_ref=None,
                )
            except Exception as exc:
                errors.append(f"provider_batch:{str(exc)[:500]}")
                for entry in batch_entries:
                    hash_runtime.fail_execution(
                        entry["descriptor"],
                        claim_id=str(entry["claim"].get("claimId") or ""),
                        error=str(exc),
                    )
                hash_runtime.finalize_batch(
                    batch=batch_meta,
                    returned_item_execution_ids=[],
                    accepted_item_execution_ids=[],
                    raw_batch_output_ref=None,
                )

    actual_calls = sum(int(item.get("actualCalls") or 0) for item in provider_calls)
    all_semantic_hits = bool(
        valid
        and semantic_hit_count == len(valid)
        and replay_count == 0
        and not claimed
        and not errors
    )
    return outputs, {
        "version": AGENT3_RUNTIME_VERSION,
        "performanceVersion": AGENT3_PERFORMANCE_VERSION,
        "stage": "agent3_sop_agent",
        "providerStatus": (
            "semantic_cache_replay"
            if all_semantic_hits
            else "ok"
            if len(outputs) == len(valid) and not errors
            else "partial"
            if outputs
            else "failed"
        ),
        "actualCalls": actual_calls,
        "exactExecutionReplayCount": replay_count,
        "semanticSopCacheHitCount": semantic_hit_count,
        "semanticSopCacheMissCount": semantic_miss_count,
        "semanticSopReboundCount": semantic_rebound_count,
        "semanticCacheErrors": semantic_errors,
        "alreadyRunningCount": busy_count,
        "providerBatchCount": provider_batch_count,
        "providerGeneratedItemCount": provider_generated_item_count,
        "compatibleGroupCount": len(grouped),
        "maxItemsPerInitialProviderCall": cap,
        "providerCalls": provider_calls,
        "errors": errors,
        "sopCount": len(outputs),
        "auxiliaryRepairAttempts": sum(
            int(item.get("auxiliaryRepairAttempts") or 0) for item in provider_calls
        ),
        "auxiliaryRepairApplied": sum(
            int(item.get("auxiliaryRepairApplied") or 0) for item in provider_calls
        ),
        "runtimeSource": "agent3SopInputArtifact.semanticSop+compatibleMicrobatch.v23217",
        "hashDirectedExecution": True,
        "semanticIdentitySchema": AGENT3_SEMANTIC_IDENTITY_SCHEMA,
        "semanticCacheContractVersion": AGENT3_PERFORMANCE_VERSION,
        "semanticSopCacheEnabled": True,
        "semanticCacheSeedRequiresCleanProviderOutput": True,
        "semanticCacheCreatesNewOutputArtifact": True,
        "semanticCacheRevalidatesCurrentSystemContract": True,
        "semanticCacheCrossProductReuseAllowed": False,
        "dynamicMicrobatchEnabled": True,
        "compatiblePolicyGroupingRequired": True,
        "batchBudgetContract": "AGENT3_MAX_BATCH_CHARS",
        "requestCacheEnabled": False,
        "itemResultCacheEnabled": True,
        "cachedOutputRebindingAllowed": True,
        "cachedOutputRebindingScope": "sop_business_body_then_current_system_revalidation",
        "auxiliaryRepairMaxAttemptsPerItem": 1,
        "parallelProviderCallsAllowed": False,
        "fallbackAllowed": False,
    }


__all__ = [
    "AGENT3_RUNTIME_VERSION",
    "AGENT3_PERFORMANCE_VERSION",
    "AGENT3_SEMANTIC_IDENTITY_SCHEMA",
    "build_agent3_semantic_identity",
    "run_agent3_sop_provider_isolated",
    "run_agent3_sop_projected_inputs",
]
