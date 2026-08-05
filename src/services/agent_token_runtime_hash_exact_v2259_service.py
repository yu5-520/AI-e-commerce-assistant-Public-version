"""V22.5.9 strict Agent1 hash runtime over the existing downstream runtime.

Agent1 keeps the current up-to-eight-item microbatch. Every item is matched only by
``itemExecutionId + inputContentHash``. A singleton retry is allowed only when the
provider raw response contains no item for that execution id. Contract-invalid,
duplicate, extra or hash-mismatched outputs are never treated as missing products.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Tuple

from src.services import agent_token_runtime_v2259_service as downstream
from src.services import agent_token_runtime_v230_service as helpers
from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_SCHEMA,
    assert_agent_input_envelope,
    split_envelopes_by_budget,
)
from src.services.hash_directed_artifact_runtime_v2259_service import (
    accepted_execution,
    build_execution_descriptor,
    claim_execution,
    complete_execution,
    create_batch_manifest,
    fail_execution,
    finalize_batch,
    hash_value,
    resolve_input_binding,
    store_item_output,
    store_raw_batch_output,
)
from src.services.llm_gateway_hash_directed_v2259_service import call_json_exact_artifact
from src.services.llm_gateway_v196_service import provider_runtime_config
from src.services.artifact_transport_service import store_artifact
from src.services.pipeline_artifact_contract_service import attach_pipeline_artifact_ref

THREE_AGENT_PIPELINE_VERSION = downstream.THREE_AGENT_PIPELINE_VERSION
AGENT_TOKEN_RUNTIME_VERSION = "22.5.9"
AGENT1_SAFE_RETRY_VERSION = "23.1.3"
AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION = (
    downstream.AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION
)
_AGENT1_STAGE = "product_judgment_agent"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]



def _returned_identity(raw: Any) -> Dict[str, Any]:
    value = _dict(raw)
    return {
        key: value.get(key)
        for key in (
            "itemExecutionId",
            "inputContentHash",
            "correlationId",
            "productId",
            "storeId",
            "signalId",
            "decisionType",
            "selectedOperatingRoute",
            "selectedActionFamilyHint",
        )
        if value.get(key) not in (None, "", [], {})
    }


def _rejection_reasons(
    item_execution_id: str,
    diagnostics: Dict[str, Any],
    fallback_reason: str,
) -> List[str]:
    reasons: List[str] = []
    if item_execution_id in set(diagnostics.get("duplicateItemExecutionIds") or []):
        reasons.append("duplicate_item_execution_id")
    for mismatch in diagnostics.get("inputContentHashMismatches") or []:
        if _text(_dict(mismatch).get("itemExecutionId"), 120) == item_execution_id:
            reasons.append("input_content_hash_mismatch")
    raw_returned = set(diagnostics.get("rawReturnedItemExecutionIds") or [])
    exact_returned = set(diagnostics.get("exactReturnedItemExecutionIds") or [])
    if item_execution_id in raw_returned and item_execution_id not in exact_returned:
        reasons.append("exact_hash_output_contract_invalid")
    if item_execution_id in exact_returned:
        reasons.append("normalization_contract_invalid")
    if fallback_reason:
        reasons.append(fallback_reason)
    return list(dict.fromkeys(reasons))


def _store_rejection_artifact(
    *,
    descriptor: Dict[str, Any],
    product: Dict[str, Any],
    raw_batch_output_ref: str,
    returned_identity: Any,
    reasons: List[str],
) -> str:
    item_execution_id = _text(descriptor.get("itemExecutionId"), 120)
    pipeline_item_id = _text(
        descriptor.get("pipelineItemId") or product.get("correlationId"),
        180,
    )
    value = {
        "schema": "agent1.normalization_rejection.v1",
        "version": AGENT1_SAFE_RETRY_VERSION,
        "itemExecutionId": item_execution_id or None,
        "pipelineItemId": pipeline_item_id or None,
        "inputArtifactRef": descriptor.get("inputArtifactRef"),
        "inputContentHash": descriptor.get("inputContentHash"),
        "rawBatchOutputRef": raw_batch_output_ref or None,
        "expectedIdentity": {
            "correlationId": product.get("correlationId"),
            "productId": product.get("productId") or descriptor.get("productId"),
            "storeId": product.get("storeId") or descriptor.get("storeId"),
            "signalId": product.get("signalId"),
            "itemExecutionId": item_execution_id or None,
            "inputContentHash": descriptor.get("inputContentHash"),
        },
        "returnedIdentity": returned_identity,
        "rejectionReasons": list(dict.fromkeys(reason for reason in reasons if reason)),
        "retryAllowed": False,
        "fallbackAllowed": False,
    }
    parents = [
        ref
        for ref in (
            descriptor.get("inputArtifactRef"),
            raw_batch_output_ref,
        )
        if str(ref or "").startswith("ART-")
    ]
    artifact = store_artifact(
        artifact_type="agent1.normalization_rejection.v1",
        value=value,
        schema_version=AGENT1_SAFE_RETRY_VERSION,
        tenant_id=descriptor.get("tenantId"),
        store_id=product.get("storeId") or descriptor.get("storeId"),
        product_id=product.get("productId") or descriptor.get("productId"),
        data_version=product.get("dataVersion") or descriptor.get("dataVersion"),
        created_by="agent_token_runtime_hash_exact_v2259",
        parent_refs=parents,
        metadata={
            "pipelineItemId": pipeline_item_id or None,
            "itemExecutionId": item_execution_id or None,
            "retryAllowed": False,
        },
    )
    artifact_id = str(artifact.get("artifactId") or "")
    if pipeline_item_id and artifact_id.startswith("ART-"):
        try:
            attach_pipeline_artifact_ref(
                pipeline_item_id,
                "agent1RejectionRef",
                artifact_id,
                make_current=False,
            )
        except Exception:
            pass
    return artifact_id


def _artifact_business_output(replay: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not replay:
        return None
    artifact_value = _dict(replay.get("output"))
    output = artifact_value.get("output")
    return dict(output) if isinstance(output, dict) else None


def _wait_for_accepted(
    execution_hash: str,
    *,
    timeout_seconds: float = 12.0,
) -> Dict[str, Any] | None:
    deadline = time.time() + max(0.5, float(timeout_seconds))
    while time.time() < deadline:
        replay = accepted_execution(execution_hash)
        if replay:
            return replay
        time.sleep(0.25)
    return None


def _decorate(
    output: Dict[str, Any],
    *,
    descriptor: Dict[str, Any],
    output_ref: str,
    output_hash: str,
    raw_batch_ref: str | None,
    origin: str,
) -> Dict[str, Any]:
    result = dict(output)
    result.update(
        itemExecutionId=descriptor.get("itemExecutionId"),
        executionHash=descriptor.get("executionHash"),
        inputArtifactRef=descriptor.get("inputArtifactRef"),
        inputContentHash=descriptor.get("inputContentHash"),
        outputArtifactRef=output_ref,
        outputContentHash=output_hash,
        rawBatchOutputRef=raw_batch_ref,
        resultOrigin=origin,
        hashIdentityMatched=True,
        fallbackIdentityMatchingUsed=False,
        cachedOutputRebound=False,
        legacyItemCacheUsed=False,
        secondProjectionApplied=False,
        hashDirectedRuntimeVersion=AGENT_TOKEN_RUNTIME_VERSION,
    )
    refs = dict(_dict(result.get("artifactRefs")))
    refs["agentExecutionInputRef"] = descriptor.get("inputArtifactRef")
    refs["agentExecutionOutputRef"] = output_ref
    if raw_batch_ref:
        refs["agentRawBatchOutputRef"] = raw_batch_ref
    result["artifactRefs"] = refs
    return result


def _entry(
    envelope: Dict[str, Any],
    *,
    provider: Dict[str, Any],
) -> Dict[str, Any]:
    binding = resolve_input_binding(envelope, expected_type=AGENT1_INPUT_SCHEMA)
    product = dict(_dict(envelope.get("payload")))
    contract = _dict(product.get("inputContract"))
    policy_hash = _text(contract.get("policyContextHash"), 160) or hash_value(
        product.get("diagnosticRag") or {}
    )
    descriptor = build_execution_descriptor(
        stage=_AGENT1_STAGE,
        binding=binding,
        input_schema=str(envelope.get("schema") or AGENT1_INPUT_SCHEMA),
        projection_version=str(
            envelope.get("projectionVersion")
            or contract.get("projectionVersion")
            or "22.5.8"
        ),
        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
        policy_hash=policy_hash,
        provider=str(provider.get("provider") or ""),
        model=str(provider.get("model") or ""),
        generation_parameters={
            "temperature": 0.08,
            "thinkingEnabled": bool(provider.get("thinkingEnabled")),
            "thinkingBudget": provider.get("thinkingBudget"),
            "responseFormat": "json_object",
        },
    )
    descriptor.update(
        correlationId=product.get("correlationId"),
        signalId=product.get("signalId"),
        productId=product.get("productId") or descriptor.get("productId"),
        storeId=product.get("storeId") or descriptor.get("storeId"),
        dataVersion=product.get("dataVersion") or descriptor.get("dataVersion"),
        pipelineItemId=_dict(binding.get("metadata")).get("pipelineItemId")
        or product.get("correlationId"),
    )
    product["_hashExecution"] = descriptor
    return {
        "envelope": envelope,
        "product": product,
        "descriptor": descriptor,
        "claim": {},
    }


def _policy(core: Any, product: Dict[str, Any]) -> Dict[str, Any]:
    base = core.build_agent1_rag_context()
    projected = _dict(product.get("diagnosticRag"))
    merged = {**base, **projected}
    merged["version"] = AGENT_TOKEN_RUNTIME_VERSION
    merged["mode"] = "hash_directed_exact_input_once_decision"
    merged["principles"] = list(
        dict.fromkeys([*(base.get("principles") or []), *(projected.get("principles") or [])])
    )
    merged["guardrails"] = {
        **_dict(base.get("guardrails")),
        **_dict(projected.get("guardrails")),
        "exactArtifactInputOnly": True,
        "secondProjectionAllowed": False,
        "legacyBusinessResultReplayAllowed": False,
        "itemExecutionIdRequired": True,
        "inputContentHashRequired": True,
        "fallbackIdentityMatchingAllowed": False,
    }
    return merged


def _provider_batch(
    entries: List[Dict[str, Any]],
    *,
    data_version: str | None,
    provider: Dict[str, Any],
    retry_attempt: int | None = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any]]:
    from src.services import real_product_judgment_agent_v2259_service as core

    descriptors = [entry["descriptor"] for entry in entries]
    batch = create_batch_manifest(
        stage=_AGENT1_STAGE,
        descriptors=descriptors,
        data_version=data_version,
        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
        provider=str(provider.get("provider") or ""),
        model=str(provider.get("model") or ""),
    )
    products = [entry["product"] for entry in entries]
    messages, _ = core._build_messages(
        data_version,
        products,
        _policy(core, products[0]) if products else core.build_agent1_rag_context(),
    )
    payload, usage = call_json_exact_artifact(
        stage=_AGENT1_STAGE,
        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
        messages=messages,
        execution_hashes=[str(item.get("executionHash") or "") for item in descriptors],
        temperature=0.08,
        timeout_seconds=int(os.getenv("PRODUCT_JUDGMENT_AGENT_TIMEOUT", "180")),
    )
    raw_artifact = store_raw_batch_output(
        batch=batch,
        provider_payload=payload,
        provider_usage=usage,
        data_version=data_version,
    )
    raw_ref = str(raw_artifact["artifactId"])
    normalized, diagnostics = core._normalize_judgments(payload, products, data_version)
    raw_identity_map: Dict[str, List[Dict[str, Any]]] = {}
    for raw in payload.get("judgments") if isinstance(payload.get("judgments"), list) else []:
        if not isinstance(raw, dict):
            continue
        raw_id = _text(raw.get("itemExecutionId"), 120) or "missing_itemExecutionId"
        raw_identity_map.setdefault(raw_id, []).append(_returned_identity(raw))
    diagnostics["rawReturnedIdentityByItemExecutionId"] = raw_identity_map
    extra_rejection_refs: List[str] = []
    for extra_id in diagnostics.get("extraItemExecutionIds") or []:
        ref = _store_rejection_artifact(
            descriptor={"itemExecutionId": extra_id},
            product={},
            raw_batch_output_ref=raw_ref,
            returned_identity=raw_identity_map.get(str(extra_id)) or [],
            reasons=["extra_item_execution_id"],
        )
        if ref:
            extra_rejection_refs.append(ref)
    diagnostics["extraRejectionArtifactRefs"] = extra_rejection_refs
    entry_by_id = {
        str(entry["descriptor"].get("itemExecutionId") or ""): entry
        for entry in entries
    }
    accepted: List[Dict[str, Any]] = []
    accepted_ids: List[str] = []
    for judgment in normalized:
        if not isinstance(judgment, dict):
            continue
        item_execution_id = _text(judgment.get("itemExecutionId"), 120)
        entry = entry_by_id.get(item_execution_id)
        if not entry:
            continue
        descriptor = entry["descriptor"]
        if _text(judgment.get("inputContentHash"), 160) != _text(
            descriptor.get("inputContentHash"), 160
        ):
            continue
        claim_id = _text(_dict(entry.get("claim")).get("claimId"), 160)
        if not claim_id:
            continue
        artifact = store_item_output(
            descriptor=descriptor,
            output=judgment,
            raw_batch_output_ref=raw_ref,
            artifact_type="agent1_model_output.v2259",
        )
        completion = complete_execution(
            descriptor,
            claim_id=claim_id,
            output_artifact_ref=str(artifact["artifactId"]),
            output_content_hash=str(artifact["contentHash"]),
            raw_batch_output_ref=raw_ref,
        )
        accepted.append(
            _decorate(
                judgment,
                descriptor=descriptor,
                output_ref=str(completion.get("outputArtifactRef") or artifact["artifactId"]),
                output_hash=str(completion.get("outputContentHash") or artifact["contentHash"]),
                raw_batch_ref=raw_ref,
                origin="exact_execution_artifact",
            )
        )
        accepted_ids.append(item_execution_id)

    batch_result = finalize_batch(
        batch=batch,
        returned_item_execution_ids=diagnostics.get("rawReturnedItemExecutionIds") or [],
        accepted_item_execution_ids=accepted_ids,
        raw_batch_output_ref=raw_ref,
    )
    diagnostics.update(
        batchResult=batch_result,
        batchManifestRef=batch.get("batchManifestRef"),
        batchManifestHash=batch.get("batchManifestHash"),
        rawBatchOutputRef=raw_ref,
        retryAttempt=retry_attempt,
        retryMode=("singleton_true_missing_hash" if retry_attempt else "microbatch_exact_hash"),
        requestCacheEnabled=False,
        itemResultCacheEnabled=False,
        secondProjectionApplied=False,
        cachedOutputRebindingAllowed=False,
    )
    return accepted, diagnostics, usage


def run_agent1_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 8,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT1_INPUT_SCHEMA)
        valid.append(envelope)
    if not valid:
        return [], {
            "version": AGENT_TOKEN_RUNTIME_VERSION,
            "providerStatus": "no_provider_call",
            "normalizationStatus": "not_started",
            "completenessStatus": "no_projected_inputs",
            "actualCalls": 0,
            "runtimeSource": "exact_agent1InputRef_content_hash",
            "fallbackAllowed": False,
        }

    provider = provider_runtime_config(_AGENT1_STAGE)
    judgments: List[Dict[str, Any]] = []
    claimed_entries: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    usages: List[Dict[str, Any]] = []
    errors: List[str] = []
    replay_count = 0
    waited_count = 0
    busy_count = 0
    claimed_count = 0
    true_missing_count = 0
    contract_invalid_count = 0
    retry_count = 0
    rejection_refs: List[str] = []

    for envelope in valid:
        try:
            entry = _entry(envelope, provider=provider)
            descriptor = entry["descriptor"]
            replay = accepted_execution(str(descriptor.get("executionHash") or ""))
            cached = _artifact_business_output(replay)
            if cached:
                judgments = helpers._merge_agent1_judgments(
                    judgments,
                    [
                        _decorate(
                            cached,
                            descriptor=descriptor,
                            output_ref=str(replay.get("outputArtifactRef") or ""),
                            output_hash=str(replay.get("outputContentHash") or ""),
                            raw_batch_ref=_dict(replay.get("execution")).get(
                                "raw_batch_output_ref"
                            ),
                            origin="accepted_execution_artifact",
                        )
                    ],
                )
                replay_count += 1
                continue
            claim = claim_execution(descriptor)
            status = str(claim.get("status") or "")
            if status == "accepted_replay":
                cached = _artifact_business_output(claim)
                if cached:
                    judgments = helpers._merge_agent1_judgments(
                        judgments,
                        [
                            _decorate(
                                cached,
                                descriptor=descriptor,
                                output_ref=str(claim.get("outputArtifactRef") or ""),
                                output_hash=str(claim.get("outputContentHash") or ""),
                                raw_batch_ref=_dict(claim.get("execution")).get(
                                    "raw_batch_output_ref"
                                ),
                                origin="accepted_execution_artifact",
                            )
                        ],
                    )
                    replay_count += 1
                continue
            if status == "already_running":
                waited = _wait_for_accepted(str(descriptor.get("executionHash") or ""))
                cached = _artifact_business_output(waited)
                if cached:
                    judgments = helpers._merge_agent1_judgments(
                        judgments,
                        [
                            _decorate(
                                cached,
                                descriptor=descriptor,
                                output_ref=str(waited.get("outputArtifactRef") or ""),
                                output_hash=str(waited.get("outputContentHash") or ""),
                                raw_batch_ref=_dict(waited.get("execution")).get(
                                    "raw_batch_output_ref"
                                ),
                                origin="waited_execution_artifact",
                            )
                        ],
                    )
                    waited_count += 1
                else:
                    busy_count += 1
                    errors.append(
                        f"execution_still_running:{descriptor.get('itemExecutionId')}"
                    )
                continue
            if status != "claimed":
                errors.append(
                    f"execution_claim_invalid:{descriptor.get('itemExecutionId')}:{status}"
                )
                continue
            entry["claim"] = claim
            claimed_entries.append(entry)
            claimed_count += 1
        except Exception as exc:
            errors.append(f"prepare:{str(exc)[:500]}")

    by_envelope = {id(entry["envelope"]): entry for entry in claimed_entries}
    batches: List[List[Dict[str, Any]]] = []
    if claimed_entries:
        for batch_envelopes in split_envelopes_by_budget(
            [entry["envelope"] for entry in claimed_entries],
            expected_schema=AGENT1_INPUT_SCHEMA,
            max_items=max_items_per_call,
        ):
            batches.append([by_envelope[id(envelope)] for envelope in batch_envelopes])

    retry_entries: List[Dict[str, Any]] = []
    for batch_index, entries in enumerate(batches):
        try:
            accepted, batch_diag, usage = _provider_batch(
                entries,
                data_version=data_version,
                provider=provider,
            )
            diagnostics.append({**batch_diag, "batchIndex": batch_index})
            rejection_refs.extend(batch_diag.get("extraRejectionArtifactRefs") or [])
            usages.append(helpers._usage_record(_dict(usage)))
            judgments = helpers._merge_agent1_judgments(judgments, accepted)

            raw_returned = set(batch_diag.get("rawReturnedItemExecutionIds") or [])
            exact_returned = set(batch_diag.get("exactReturnedItemExecutionIds") or [])
            accepted_ids = {
                str(item.get("itemExecutionId") or "")
                for item in accepted
                if isinstance(item, dict)
            }
            for entry in entries:
                item_execution_id = str(
                    entry["descriptor"].get("itemExecutionId") or ""
                )
                if item_execution_id in accepted_ids:
                    continue
                if item_execution_id in raw_returned:
                    # Raw output exists, so this is an output contract failure—not a
                    # missing product. Never call the model again for this execution.
                    contract_invalid_count += 1
                    claim_id = _text(
                        _dict(entry.get("claim")).get("claimId"), 160
                    )
                    reason_code = (
                        "agent1_exact_hash_output_contract_invalid"
                        if item_execution_id not in exact_returned
                        else "agent1_normalization_contract_invalid"
                    )
                    raw_map = _dict(batch_diag.get("rawReturnedIdentityByItemExecutionId"))
                    rejection_ref = _store_rejection_artifact(
                        descriptor=entry["descriptor"],
                        product=entry["product"],
                        raw_batch_output_ref=str(batch_diag.get("rawBatchOutputRef") or ""),
                        returned_identity=raw_map.get(item_execution_id) or [],
                        reasons=_rejection_reasons(item_execution_id, batch_diag, reason_code),
                    )
                    if rejection_ref:
                        rejection_refs.append(rejection_ref)
                    if claim_id:
                        fail_execution(
                            entry["descriptor"],
                            claim_id=claim_id,
                            error=reason_code + (f":{rejection_ref}" if rejection_ref else ""),
                        )
                    continue
                true_missing_count += 1
                retry_entries.append(entry)
        except Exception as exc:
            errors.append(f"batch_{batch_index}:provider:{str(exc)[:500]}")
            # A provider-call failure is retryable for each exact claimed execution.
            retry_entries.extend(entries)

    retry_limit = helpers._env_int(
        "AGENT1_MISSING_ITEM_RETRY_ATTEMPTS", 2, 0, 4
    )
    pending = retry_entries
    for attempt in range(1, retry_limit + 1):
        if not pending:
            break
        next_pending: List[Dict[str, Any]] = []
        for entry in pending:
            item_execution_id = str(
                entry["descriptor"].get("itemExecutionId") or ""
            )
            try:
                retry_count += 1
                accepted, retry_diag, usage = _provider_batch(
                    [entry],
                    data_version=data_version,
                    provider=provider,
                    retry_attempt=attempt,
                )
                diagnostics.append(retry_diag)
                usages.append(helpers._usage_record(_dict(usage), retry=True))
                if accepted:
                    judgments = helpers._merge_agent1_judgments(judgments, accepted)
                    continue
                if item_execution_id in set(
                    retry_diag.get("rawReturnedItemExecutionIds") or []
                ):
                    contract_invalid_count += 1
                    claim_id = _text(
                        _dict(entry.get("claim")).get("claimId"), 160
                    )
                    reason_code = "agent1_singleton_output_contract_invalid"
                    raw_map = _dict(retry_diag.get("rawReturnedIdentityByItemExecutionId"))
                    rejection_ref = _store_rejection_artifact(
                        descriptor=entry["descriptor"],
                        product=entry["product"],
                        raw_batch_output_ref=str(retry_diag.get("rawBatchOutputRef") or ""),
                        returned_identity=raw_map.get(item_execution_id) or [],
                        reasons=_rejection_reasons(item_execution_id, retry_diag, reason_code),
                    )
                    if rejection_ref:
                        rejection_refs.append(rejection_ref)
                    if claim_id:
                        fail_execution(
                            entry["descriptor"],
                            claim_id=claim_id,
                            error=reason_code + (f":{rejection_ref}" if rejection_ref else ""),
                        )
                    continue
                next_pending.append(entry)
            except Exception as exc:
                errors.append(
                    f"singleton_retry_{attempt}:{item_execution_id}:{str(exc)[:420]}"
                )
                next_pending.append(entry)
        pending = next_pending

    for entry in pending:
        descriptor = entry["descriptor"]
        claim_id = _text(_dict(entry.get("claim")).get("claimId"), 160)
        if claim_id and not accepted_execution(str(descriptor.get("executionHash") or "")):
            fail_execution(
                descriptor,
                claim_id=claim_id,
                error="agent1_true_missing_after_singleton_retries",
            )

    summary = helpers._usage_summary(usages, stage=_AGENT1_STAGE)
    missing_count = max(0, len(valid) - len(judgments))
    summary.update(
        version=AGENT_TOKEN_RUNTIME_VERSION,
        providerStatus=(
            "provider_succeeded"
            if missing_count == 0 and not errors
            else "provider_partial"
            if judgments
            else "provider_failed"
        ),
        normalizationStatus=(
            "exact_hash_matched"
            if missing_count == 0
            else "partial"
            if judgments
            else "failed"
        ),
        completenessStatus=(
            "complete"
            if missing_count == 0
            else "partial"
            if judgments
            else "incomplete"
        ),
        inputProductCount=len(valid),
        normalizedJudgmentCount=len(judgments),
        missingProductJudgmentCount=missing_count,
        acceptedExecutionReplayCount=replay_count,
        waitedExecutionReplayCount=waited_count,
        alreadyRunningCount=busy_count,
        claimedExecutionCount=claimed_count,
        providerBatchCount=len(batches),
        trueMissingItemCount=true_missing_count,
        singletonRetryCount=retry_count,
        outputContractInvalidCount=contract_invalid_count,
        normalizationRejectionArtifactRefs=sorted(set(rejection_refs)),
        batchDiagnostics=diagnostics,
        errors=errors,
        runtimeSource="exact_agent1InputRef_content_hash",
        matchingContract="itemExecutionId+inputContentHash",
        batchBoundary="up_to_8_independent_input_hashes",
        requestCacheEnabled=False,
        itemResultCacheEnabled=False,
        secondProjectionApplied=False,
        cachedOutputRebindingAllowed=False,
        fallbackIdentityMatchingAllowed=False,
        fallbackAllowed=False,
    )
    return judgments, summary


run_agent2_draft_projected_inputs = downstream.run_agent2_draft_projected_inputs
run_agent2_projected_inputs = downstream.run_agent2_projected_inputs
run_agent3_sop_projected_inputs = downstream.run_agent3_sop_projected_inputs

__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_TOKEN_RUNTIME_VERSION",
    "AGENT1_SAFE_RETRY_VERSION",
    "AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION",
    "run_agent1_projected_inputs",
    "run_agent2_draft_projected_inputs",
    "run_agent2_projected_inputs",
    "run_agent3_sop_projected_inputs",
]
