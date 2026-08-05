"""V22.5.9 hash-directed three-Agent token runtime.

Each projected input is resolved to its exact immutable Artifact, assigned one
execution hash, and either replays the exact accepted output Artifact or executes the
provider once. Agent1 keeps eight-item microbatches while every item remains
independently addressable, auditable and retryable.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from src.services import agent_token_runtime_v225_legacy_service as downstream_legacy
from src.services import agent_token_runtime_v230_service as runtime_helpers
from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_PROJECTION_VERSION,
    AGENT1_INPUT_SCHEMA,
    assert_agent_input_envelope as assert_agent1_envelope,
    split_envelopes_by_budget as split_agent1_envelopes,
)
from src.services.agent_input_contract_v225_service import (
    AGENT_INPUT_CONTRACT_VERSION,
    AGENT2_DRAFT_INPUT_SCHEMA,
    AGENT3_SOP_INPUT_SCHEMA,
    assert_agent_input_envelope,
    split_envelopes_by_budget,
)
from src.services.hash_directed_artifact_runtime_v2259_service import (
    HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
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
from src.services.llm_gateway_v196_service import call_json, provider_runtime_config

THREE_AGENT_PIPELINE_VERSION = "22.5.5"
AGENT_TOKEN_RUNTIME_VERSION = "22.5.9"
AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION = "22.5.9"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _identity(value: Dict[str, Any]) -> tuple[str, str, str, str]:
    payload = _dict(value.get("payload")) if isinstance(value.get("payload"), dict) else value
    profile = {**_dict(payload.get("profileLayer")), **_dict(payload.get("productIdentity")), **_dict(payload.get("identity"))}
    return (
        _text(value.get("correlationId") or payload.get("correlationId")),
        _text(value.get("storeId") or payload.get("storeId") or profile.get("storeId")),
        _text(value.get("productId") or payload.get("productId") or profile.get("productId")),
        _text(value.get("signalId") or payload.get("signalId")),
    )


def _package_id(envelope: Dict[str, Any]) -> str:
    payload = _dict(envelope.get("payload"))
    return _text(payload.get("packageId") or payload.get("itemId"))


def _binding_descriptor(
    envelope: Dict[str, Any],
    *,
    expected_schema: str,
    stage: str,
    prompt_version: str,
    temperature: float,
) -> Dict[str, Any]:
    binding = resolve_input_binding(envelope, expected_type=expected_schema)
    payload = _dict(envelope.get("payload"))
    contract = _dict(payload.get("inputContract"))
    policy_hash = _text(contract.get("policyContextHash")) or hash_value(
        payload.get("diagnosticRag") or payload.get("ragContext") or payload.get("companySopRag") or {}
    )
    provider = provider_runtime_config(stage)
    descriptor = build_execution_descriptor(
        stage=stage,
        binding=binding,
        input_schema=expected_schema,
        projection_version=_text(envelope.get("projectionVersion") or envelope.get("schemaVersion") or contract.get("projectionVersion")),
        prompt_version=prompt_version,
        policy_hash=policy_hash,
        provider=_text(provider.get("provider")),
        model=_text(provider.get("model")),
        generation_parameters={
            "temperature": temperature,
            "thinkingEnabled": bool(provider.get("thinkingEnabled")),
            "thinkingBudget": provider.get("thinkingBudget"),
        },
    )
    correlation, store_id, product_id, signal_id = _identity(payload)
    descriptor.update(
        correlationId=correlation or None,
        storeId=store_id or descriptor.get("storeId"),
        productId=product_id or descriptor.get("productId"),
        signalId=signal_id or None,
        packageId=_package_id(envelope) or None,
    )
    return descriptor


def _cached_business_output(replay: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not replay:
        return None
    artifact_value = _dict(replay.get("output"))
    output = artifact_value.get("output")
    return dict(output) if isinstance(output, dict) else None


def _decorate_output(
    output: Dict[str, Any],
    *,
    descriptor: Dict[str, Any],
    output_artifact_ref: str,
    output_content_hash: str,
    raw_batch_output_ref: str | None,
    replay: bool,
) -> Dict[str, Any]:
    result = dict(output)
    result.update(
        itemExecutionId=descriptor.get("itemExecutionId"),
        executionHash=descriptor.get("executionHash"),
        inputArtifactRef=descriptor.get("inputArtifactRef"),
        inputContentHash=descriptor.get("inputContentHash"),
        outputArtifactRef=output_artifact_ref,
        outputContentHash=output_content_hash,
        rawBatchOutputRef=raw_batch_output_ref,
        exactExecutionReplay=bool(replay),
        cachedOutputRebound=False,
        hashDirectedRuntimeVersion=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
    )
    refs = dict(_dict(result.get("artifactRefs")))
    refs["agentExecutionInputRef"] = descriptor.get("inputArtifactRef")
    refs["agentExecutionOutputRef"] = output_artifact_ref
    if raw_batch_output_ref:
        refs["agentRawBatchOutputRef"] = raw_batch_output_ref
    result["artifactRefs"] = refs
    return result


def _descriptor_for_raw(raw: Dict[str, Any], descriptors: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    item_execution_id = _text(raw.get("itemExecutionId"))
    input_hash = _text(raw.get("inputContentHash"))
    if item_execution_id:
        match = next((item for item in descriptors if item.get("itemExecutionId") == item_execution_id), None)
        if match and (not input_hash or input_hash == match.get("inputContentHash")):
            return match
    if input_hash:
        match = next((item for item in descriptors if item.get("inputContentHash") == input_hash), None)
        if match:
            return match
    raw_identity = _identity(raw)
    exact = [item for item in descriptors if (item.get("storeId"), item.get("productId")) == (raw_identity[1], raw_identity[2])]
    return exact[0] if len(exact) == 1 else None


def _inject_hash_batch_contract(
    messages: List[Dict[str, str]],
    *,
    batch: Dict[str, Any],
    descriptors: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    result = [dict(item) for item in messages]
    user_index = next((index for index in range(len(result) - 1, -1, -1) if result[index].get("role") == "user"), None)
    if user_index is None:
        raise ValueError("hash_directed_user_message_missing")
    payload = json.loads(str(result[user_index].get("content") or "{}"))
    if not isinstance(payload, dict):
        raise ValueError("hash_directed_user_payload_not_object")
    payload["_hashDirectedExecution"] = True
    payload["artifactBatchManifest"] = batch["manifest"]
    collection_key = "products" if isinstance(payload.get("products"), list) else "packages" if isinstance(payload.get("packages"), list) else "sops" if isinstance(payload.get("sops"), list) else None
    if collection_key:
        for item in payload.get(collection_key) or []:
            if not isinstance(item, dict):
                continue
            descriptor = _descriptor_for_raw(item, descriptors)
            if descriptor:
                item.update(
                    itemExecutionId=descriptor.get("itemExecutionId"),
                    executionHash=descriptor.get("executionHash"),
                    inputArtifactRef=descriptor.get("inputArtifactRef"),
                    inputContentHash=descriptor.get("inputContentHash"),
                )
    result[user_index]["content"] = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    system_index = next((index for index, item in enumerate(result) if item.get("role") == "system"), None)
    contract = (
        "\nV22.5.9硬合同：每个输出项必须原样返回itemExecutionId和inputContentHash；"
        "输出顺序可以变化，但不得省略、复制或改写这两个字段。"
    )
    if system_index is None:
        result.insert(0, {"role": "system", "content": contract.strip()})
    else:
        result[system_index]["content"] = str(result[system_index].get("content") or "") + contract
    return result, payload


def _agent1_batch_call(
    pairs: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]],
    *,
    data_version: str | None,
) -> Tuple[List[Dict[str, Any]], List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]], Dict[str, Any]]:
    from src.services import real_product_judgment_agent_v2258_service as core
    from src.services import agent_token_runtime_v2258_service as v2258

    envelopes = [pair[0] for pair in pairs]
    descriptors = [pair[1] for pair in pairs]
    products = [dict(_dict(envelope.get("payload"))) for envelope in envelopes]
    policy = v2258._merge_policy(core, products[0]) if products else core.build_agent1_rag_context()
    batch = create_batch_manifest(
        stage="product_judgment_agent",
        descriptors=descriptors,
        data_version=data_version,
        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
        provider=descriptors[0].get("provider") if descriptors else "",
        model=descriptors[0].get("model") if descriptors else "",
    )
    messages, _cache_payload = core._build_messages(data_version, products, policy)
    messages, exact_payload = _inject_hash_batch_contract(messages, batch=batch, descriptors=descriptors)
    payload, usage = call_json(
        stage="product_judgment_agent",
        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
        messages=messages,
        temperature=0.08,
        timeout_seconds=int(os.getenv("PRODUCT_JUDGMENT_AGENT_TIMEOUT", "180")),
        cache_payload=exact_payload,
        cache_enabled=False,
    )
    raw_artifact = store_raw_batch_output(
        batch=batch,
        provider_payload=payload,
        provider_usage=_dict(usage),
        data_version=data_version,
    )
    raw_ref = str(raw_artifact["artifactId"])
    raw_items = payload.get("judgments") if isinstance(payload, dict) else None
    raw_items = raw_items if isinstance(raw_items, list) else []
    enriched_raw: List[Dict[str, Any]] = []
    returned_ids: List[str] = []
    for value in raw_items:
        if not isinstance(value, dict):
            continue
        raw = dict(value)
        descriptor = _descriptor_for_raw(raw, descriptors)
        if descriptor:
            raw["itemExecutionId"] = descriptor.get("itemExecutionId")
            raw["inputContentHash"] = descriptor.get("inputContentHash")
            raw["inputArtifactRef"] = descriptor.get("inputArtifactRef")
            returned_ids.append(str(descriptor.get("itemExecutionId") or ""))
        enriched_raw.append(raw)
    normalized, normalization = core._normalize_judgments(
        {**payload, "judgments": enriched_raw},
        core._source_maps(products),
        data_version,
    )
    accepted: List[Dict[str, Any]] = []
    accepted_ids: List[str] = []
    completed_execution_hashes: set[str] = set()
    for judgment in normalized:
        if not isinstance(judgment, dict):
            continue
        descriptor = _descriptor_for_raw(judgment, descriptors)
        if not descriptor:
            continue
        pair = next(pair for pair in pairs if pair[1]["executionHash"] == descriptor["executionHash"])
        claim = pair[2]
        artifact = store_item_output(
            descriptor=descriptor,
            output=judgment,
            raw_batch_output_ref=raw_ref,
            artifact_type="agent1_model_output.v2259",
        )
        complete_execution(
            descriptor,
            claim_id=str(claim.get("claimId") or ""),
            output_artifact_ref=str(artifact["artifactId"]),
            output_content_hash=str(artifact["contentHash"]),
            raw_batch_output_ref=raw_ref,
        )
        accepted.append(
            _decorate_output(
                judgment,
                descriptor=descriptor,
                output_artifact_ref=str(artifact["artifactId"]),
                output_content_hash=str(artifact["contentHash"]),
                raw_batch_output_ref=raw_ref,
                replay=False,
            )
        )
        accepted_ids.append(str(descriptor.get("itemExecutionId") or ""))
        completed_execution_hashes.add(str(descriptor.get("executionHash") or ""))
    missing_pairs = [pair for pair in pairs if str(pair[1].get("executionHash") or "") not in completed_execution_hashes]
    batch_result = finalize_batch(
        batch=batch,
        returned_item_execution_ids=returned_ids,
        accepted_item_execution_ids=accepted_ids,
        raw_batch_output_ref=raw_ref,
    )
    return accepted, missing_pairs, {
        "usage": usage,
        "normalization": normalization,
        "batch": batch_result,
        "batchManifestRef": batch["batchManifestRef"],
        "rawBatchOutputRef": raw_ref,
    }


def run_agent1_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 8,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent1_envelope(envelope, expected_schema=AGENT1_INPUT_SCHEMA)
        valid.append(envelope)
    if not valid:
        return [], {
            "version": AGENT_TOKEN_RUNTIME_VERSION,
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "hashDirectedExecution": True,
            "fallbackAllowed": False,
        }

    judgments: List[Dict[str, Any]] = []
    claimed_pairs: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    errors: List[str] = []
    replay_count = busy_count = 0
    for envelope in valid:
        descriptor = _binding_descriptor(
            envelope,
            expected_schema=AGENT1_INPUT_SCHEMA,
            stage="product_judgment_agent",
            prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
            temperature=0.08,
        )
        replay = accepted_execution(str(descriptor["executionHash"]))
        cached = _cached_business_output(replay)
        if cached:
            replay_count += 1
            judgments.append(
                _decorate_output(
                    cached,
                    descriptor=descriptor,
                    output_artifact_ref=str(replay.get("outputArtifactRef") or ""),
                    output_content_hash=str(replay.get("outputContentHash") or ""),
                    raw_batch_output_ref=_dict(replay.get("execution")).get("raw_batch_output_ref"),
                    replay=True,
                )
            )
            continue
        claim = claim_execution(descriptor)
        if claim.get("status") == "accepted_replay":
            cached = _cached_business_output(claim)
            if cached:
                replay_count += 1
                judgments.append(
                    _decorate_output(
                        cached,
                        descriptor=descriptor,
                        output_artifact_ref=str(claim.get("outputArtifactRef") or ""),
                        output_content_hash=str(claim.get("outputContentHash") or ""),
                        raw_batch_output_ref=_dict(claim.get("execution")).get("raw_batch_output_ref"),
                        replay=True,
                    )
                )
            continue
        if claim.get("status") != "claimed":
            busy_count += 1
            continue
        claimed_pairs.append((envelope, descriptor, claim))

    batches: List[List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]]] = []
    if claimed_pairs:
        by_object = {id(pair[0]): pair for pair in claimed_pairs}
        for batch in split_agent1_envelopes(
            [pair[0] for pair in claimed_pairs],
            expected_schema=AGENT1_INPUT_SCHEMA,
            max_items=max_items_per_call,
        ):
            batches.append([by_object[id(envelope)] for envelope in batch])

    diagnostics: List[Dict[str, Any]] = []
    retry_pairs: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    usages: List[Dict[str, Any]] = []
    for batch in batches:
        try:
            accepted, missing, diagnostic = _agent1_batch_call(batch, data_version=data_version)
            judgments.extend(accepted)
            retry_pairs.extend(missing)
            diagnostics.append(diagnostic)
            usages.append(runtime_helpers._usage_record(_dict(diagnostic.get("usage"))))
        except Exception as exc:
            retry_pairs.extend(batch)
            errors.append(f"batch:{str(exc)[:500]}")

    retry_limit = runtime_helpers._env_int("AGENT1_MISSING_ITEM_RETRY_ATTEMPTS", 2, 0, 4)
    final_missing = retry_pairs
    for attempt in range(1, retry_limit + 1):
        if not final_missing:
            break
        next_missing: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
        for pair in final_missing:
            try:
                accepted, missing, diagnostic = _agent1_batch_call([pair], data_version=data_version)
                judgments.extend(accepted)
                next_missing.extend(missing)
                diagnostic["singletonRetryAttempt"] = attempt
                diagnostics.append(diagnostic)
                usages.append(runtime_helpers._usage_record(_dict(diagnostic.get("usage")), retry=True))
            except Exception as exc:
                next_missing.append(pair)
                errors.append(f"singleton_retry_{attempt}:{str(exc)[:500]}")
        final_missing = next_missing

    for _envelope, descriptor, claim in final_missing:
        fail_execution(descriptor, claim_id=str(claim.get("claimId") or ""), error="agent1_exact_output_missing_after_singleton_retries")

    summary = runtime_helpers._usage_summary(usages, stage="product_judgment_agent")
    summary.update(
        version=AGENT_TOKEN_RUNTIME_VERSION,
        providerStatus=("ok" if len(judgments) == len(valid) and not errors else "partial" if judgments else "failed"),
        normalizationStatus="exact_hash_matched" if len(judgments) == len(valid) else "partial",
        completenessStatus="complete" if len(judgments) == len(valid) else "incomplete",
        inputProductCount=len(valid),
        normalizedJudgmentCount=len(judgments),
        missingProductJudgmentCount=max(0, len(valid) - len(judgments)),
        exactExecutionReplayCount=replay_count,
        alreadyRunningCount=busy_count,
        providerBatchCount=len(batches),
        batchDiagnostics=diagnostics,
        errors=errors,
        hashDirectedExecution=True,
        itemCacheRole="executionHash_to_acceptedOutputArtifactRef_only",
        cachedOutputRebindingAllowed=False,
        runtimeSource="agent1InputArtifact.v3",
        fallbackAllowed=False,
    )
    return judgments, summary


def _wrap_downstream_output(
    *,
    envelope: Dict[str, Any],
    descriptor: Dict[str, Any],
    claim: Dict[str, Any],
    output: Dict[str, Any],
    artifact_type: str,
) -> Dict[str, Any]:
    artifact = store_item_output(
        descriptor=descriptor,
        output=output,
        raw_batch_output_ref=None,
        artifact_type=artifact_type,
    )
    complete_execution(
        descriptor,
        claim_id=str(claim.get("claimId") or ""),
        output_artifact_ref=str(artifact["artifactId"]),
        output_content_hash=str(artifact["contentHash"]),
    )
    return _decorate_output(
        output,
        descriptor=descriptor,
        output_artifact_ref=str(artifact["artifactId"]),
        output_content_hash=str(artifact["contentHash"]),
        raw_batch_output_ref=None,
        replay=False,
    )


def run_agent2_draft_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 5,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT2_DRAFT_INPUT_SCHEMA)
        valid.append(envelope)
    outputs: Dict[str, Dict[str, Any]] = {}
    claimed: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    replay_count = 0
    for envelope in valid:
        descriptor = _binding_descriptor(
            envelope,
            expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
            stage="action_plan_judgment_agent",
            prompt_version=AGENT_INPUT_CONTRACT_VERSION,
            temperature=0.16,
        )
        package_id = _package_id(envelope)
        replay = accepted_execution(str(descriptor["executionHash"]))
        cached = _cached_business_output(replay)
        if cached:
            replay_count += 1
            outputs[package_id] = _decorate_output(
                cached,
                descriptor=descriptor,
                output_artifact_ref=str(replay.get("outputArtifactRef") or ""),
                output_content_hash=str(replay.get("outputContentHash") or ""),
                raw_batch_output_ref=None,
                replay=True,
            )
            continue
        claim = claim_execution(descriptor)
        if claim.get("status") == "claimed":
            claimed.append((envelope, descriptor, claim))

    provider_summaries: List[Dict[str, Any]] = []
    errors: List[str] = []
    grouped: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]]] = defaultdict(list)
    from src.services import agent2_action_draft_core_v225_service as core
    for pair in claimed:
        grouped[core.selected_family(_dict(pair[0].get("payload")))].append(pair)

    for family, pairs in grouped.items():
        by_object = {id(pair[0]): pair for pair in pairs}
        batches = split_envelopes_by_budget(
            [pair[0] for pair in pairs],
            expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
            max_items=max_items_per_call,
        )
        for batch_envelopes in batches:
            batch_pairs = [by_object[id(envelope)] for envelope in batch_envelopes]
            batch_meta = create_batch_manifest(
                stage="action_plan_judgment_agent",
                descriptors=[pair[1] for pair in batch_pairs],
                data_version=data_version,
                prompt_version=AGENT_INPUT_CONTRACT_VERSION,
                provider=batch_pairs[0][1].get("provider") or "",
                model=batch_pairs[0][1].get("model") or "",
            )
            try:
                drafts, provider = downstream_legacy.run_agent2_draft_projected_inputs(
                    batch_envelopes,
                    data_version=data_version,
                    max_items_per_call=len(batch_envelopes),
                )
                provider_summaries.append(provider)
                accepted_ids: List[str] = []
                for envelope, descriptor, claim in batch_pairs:
                    package_id = _package_id(envelope)
                    draft = drafts.get(package_id)
                    if isinstance(draft, dict):
                        outputs[package_id] = _wrap_downstream_output(
                            envelope=envelope,
                            descriptor=descriptor,
                            claim=claim,
                            output=draft,
                            artifact_type="agent2_model_output.v2259",
                        )
                        accepted_ids.append(str(descriptor.get("itemExecutionId") or ""))
                    else:
                        fail_execution(descriptor, claim_id=str(claim.get("claimId") or ""), error="agent2_exact_output_missing")
                finalize_batch(
                    batch=batch_meta,
                    returned_item_execution_ids=accepted_ids,
                    accepted_item_execution_ids=accepted_ids,
                    raw_batch_output_ref=None,
                )
            except Exception as exc:
                errors.append(f"{family}:{str(exc)[:500]}")
                for _envelope, descriptor, claim in batch_pairs:
                    fail_execution(descriptor, claim_id=str(claim.get("claimId") or ""), error=str(exc))

    return outputs, {
        "version": AGENT_TOKEN_RUNTIME_VERSION,
        "stage": "agent2_action_draft",
        "providerStatus": "ok" if len(outputs) == len(valid) and not errors else "partial" if outputs else "failed",
        "actualCalls": sum(int(item.get("actualCalls") or 0) for item in provider_summaries),
        "cacheHits": replay_count,
        "exactExecutionReplayCount": replay_count,
        "providerCalls": provider_summaries,
        "errors": errors,
        "draftCount": len(outputs),
        "runtimeSource": "agent2DraftInputArtifact",
        "hashDirectedExecution": True,
        "requestCacheEnabled": False,
        "itemResultCacheEnabled": False,
        "cachedOutputRebindingAllowed": False,
        "fallbackAllowed": False,
    }


def run_agent3_sop_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 1,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
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
        descriptor = _binding_descriptor(
            envelope,
            expected_schema=AGENT3_SOP_INPUT_SCHEMA,
            stage="task_mapping_agent",
            prompt_version=AGENT_INPUT_CONTRACT_VERSION,
            temperature=0.2,
        )
        package_id = _package_id(envelope)
        replay = accepted_execution(str(descriptor["executionHash"]))
        cached = _cached_business_output(replay)
        if cached:
            replay_count += 1
            outputs[package_id] = _decorate_output(
                cached,
                descriptor=descriptor,
                output_artifact_ref=str(replay.get("outputArtifactRef") or ""),
                output_content_hash=str(replay.get("outputContentHash") or ""),
                raw_batch_output_ref=None,
                replay=True,
            )
            continue
        claim = claim_execution(descriptor)
        if claim.get("status") != "claimed":
            errors.append(f"{package_id}:execution_already_running")
            continue
        batch = create_batch_manifest(
            stage="task_mapping_agent",
            descriptors=[descriptor],
            data_version=data_version,
            prompt_version=AGENT_INPUT_CONTRACT_VERSION,
            provider=descriptor.get("provider") or "",
            model=descriptor.get("model") or "",
        )
        try:
            sops, provider = downstream_legacy.run_agent3_sop_projected_inputs(
                [envelope],
                data_version=data_version,
                max_items_per_call=1,
            )
            provider_calls.append(provider)
            sop = sops.get(package_id)
            accepted_ids: List[str] = []
            if isinstance(sop, dict):
                outputs[package_id] = _wrap_downstream_output(
                    envelope=envelope,
                    descriptor=descriptor,
                    claim=claim,
                    output=sop,
                    artifact_type="agent3_model_output.v2259",
                )
                accepted_ids.append(str(descriptor.get("itemExecutionId") or ""))
            else:
                fail_execution(descriptor, claim_id=str(claim.get("claimId") or ""), error="agent3_exact_output_missing")
            finalize_batch(
                batch=batch,
                returned_item_execution_ids=accepted_ids,
                accepted_item_execution_ids=accepted_ids,
                raw_batch_output_ref=None,
            )
        except Exception as exc:
            fail_execution(descriptor, claim_id=str(claim.get("claimId") or ""), error=str(exc))
            errors.append(f"{package_id}:{str(exc)[:500]}")

    return outputs, {
        "version": AGENT_TOKEN_RUNTIME_VERSION,
        "stage": "agent3_sop_agent",
        "providerStatus": "ok" if len(outputs) == len(valid) and not errors else "partial" if outputs else "failed",
        "actualCalls": sum(int(item.get("actualCalls") or 0) for item in provider_calls),
        "exactExecutionReplayCount": replay_count,
        "providerCalls": provider_calls,
        "errors": errors,
        "sopCount": len(outputs),
        "runtimeSource": "agent3SopInputArtifact",
        "hashDirectedExecution": True,
        "requestCacheEnabled": False,
        "cachedOutputRebindingAllowed": False,
        "fallbackAllowed": False,
    }


run_agent2_projected_inputs = run_agent2_draft_projected_inputs

__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_TOKEN_RUNTIME_VERSION",
    "AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION",
    "run_agent1_projected_inputs",
    "run_agent2_draft_projected_inputs",
    "run_agent2_projected_inputs",
    "run_agent3_sop_projected_inputs",
]
