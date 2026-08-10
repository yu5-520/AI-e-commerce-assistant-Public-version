"""V22.5.9 strict Agent1 hash runtime with V23.1.6 dual identity.

ExecutionHash remains the immutable execution/audit authority. SemanticHash is a
secondary per-item result-cache identity built only from business-semantic input plus
the current prompt/policy/provider/model/generation contract. A semantic hit never
reuses an old execution identity or old output Artifact directly: the cached business
body is rebound to the current exact input identity, written as a new immutable output
Artifact, and accepted under the current ExecutionHash.

Provider output matching remains strictly ``itemExecutionId + inputContentHash``.
Contract-invalid, duplicate, extra or hash-mismatched outputs are never treated as
missing products, and singleton retry remains limited to true missing exact identities.
"""
from __future__ import annotations

import os
import time
from copy import deepcopy
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, loads
from src.services import agent_token_runtime_v2259_service as downstream
from src.services import agent_token_runtime_v230_service as helpers
from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_SCHEMA,
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
    ensure_hash_directed_runtime_tables,
    fail_execution,
    finalize_batch,
    hash_value,
    resolve_input_binding,
    store_item_output,
    store_raw_batch_output,
)
from src.services.llm_gateway_hash_directed_v2259_service import call_json_exact_artifact
from src.services.llm_gateway_v196_service import provider_runtime_config
from src.services.artifact_transport_service import (
    resolve_artifact,
    store_artifact,
    validate_artifact,
)
from src.services.pipeline_artifact_contract_service import attach_pipeline_artifact_ref

THREE_AGENT_PIPELINE_VERSION = downstream.THREE_AGENT_PIPELINE_VERSION
AGENT_TOKEN_RUNTIME_VERSION = "22.5.9"
AGENT1_SAFE_RETRY_VERSION = "23.1.3"
AGENT1_SEMANTIC_RESULT_CACHE_VERSION = "23.1.6"
AGENT1_SEMANTIC_IDENTITY_SCHEMA = "agent1.semantic_identity.v1"
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


def _semantic_business_payload(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """Return only model-relevant Agent1 business semantics.

    Execution transport identity is deliberately excluded: correlationId, signalId,
    dataVersion, source Artifact refs/hashes and source data-version ids. Business dates,
    facts, metrics, trends, product/store identity, RAG policy and lineage quality remain.
    """

    payload = deepcopy(_dict(envelope.get("payload")))
    for key in ("correlationId", "signalId", "dataVersion"):
        payload.pop(key, None)

    lineage = deepcopy(_dict(payload.get("sourceLineageValidation")))
    if lineage:
        for key in ("sourceArtifactRefs", "sourceContentHash", "dataVersions"):
            lineage.pop(key, None)
        payload["sourceLineageValidation"] = lineage

    contract = deepcopy(_dict(payload.get("inputContract")))
    if contract:
        for key in ("sourceRef", "sourceContentHash", "sourceLineageHash"):
            contract.pop(key, None)
        payload["inputContract"] = contract

    return payload


def build_agent1_semantic_identity(
    envelope: Dict[str, Any],
    descriptor: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the secondary semantic identity without weakening ExecutionHash."""

    from src.services import real_product_judgment_agent_v2259_service as core

    semantic_payload = _semantic_business_payload(envelope)
    semantic_input_hash = hash_value(semantic_payload)
    contract = {
        "semanticCacheVersion": AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
        "stage": descriptor.get("stage") or _AGENT1_STAGE,
        "inputSchema": descriptor.get("inputSchema") or AGENT1_INPUT_SCHEMA,
        "projectionVersion": descriptor.get("projectionVersion"),
        "promptVersion": descriptor.get("promptVersion"),
        "promptContractVersion": getattr(
            core,
            "REAL_PRODUCT_AGENT_V2259_VERSION",
            "unknown",
        ),
        "policyHash": descriptor.get("policyHash"),
        "provider": descriptor.get("provider"),
        "model": descriptor.get("model"),
        "generationParametersHash": descriptor.get("generationParametersHash"),
    }
    semantic_contract_hash = hash_value(contract)
    semantic_hash = hash_value(
        {
            "schema": AGENT1_SEMANTIC_IDENTITY_SCHEMA,
            "semanticInputHash": semantic_input_hash,
            "semanticContractHash": semantic_contract_hash,
        }
    )
    return {
        "schema": AGENT1_SEMANTIC_IDENTITY_SCHEMA,
        "version": AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
        "semanticHash": semantic_hash,
        "semanticInputHash": semantic_input_hash,
        "semanticContractHash": semantic_contract_hash,
        "executionIdentityExcluded": [
            "correlationId",
            "signalId",
            "dataVersion",
            "sourceArtifactRefs",
            "sourceContentHash",
            "sourceDataVersions",
        ],
        "crossProductReuseAllowed": False,
    }


def _accepted_semantic_execution(
    semantic_hash: str,
    *,
    exclude_execution_hash: str | None = None,
) -> Dict[str, Any] | None:
    """Resolve a validated prior accepted execution by SemanticHash.

    No new cache table is introduced. The existing exact execution ledger remains the
    authority; SemanticHash is stored inside its immutable descriptor metadata and used
    only as a secondary lookup key.
    """

    semantic_hash = _text(semantic_hash, 160)
    if not semantic_hash:
        return None
    ensure_hash_directed_runtime_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM artifact_execution_index_v2259
            WHERE stage=? AND status='accepted' AND metadata_json LIKE ?
            ORDER BY updated_at DESC
            LIMIT 64
            """,
            (_AGENT1_STAGE, f"%{semantic_hash}%"),
        ).fetchall()
    for row in rows:
        record = dict(row)
        execution_hash = str(record.get("execution_hash") or "")
        if exclude_execution_hash and execution_hash == exclude_execution_hash:
            continue
        metadata = loads(record.get("metadata_json")) if record.get("metadata_json") else {}
        if not isinstance(metadata, dict):
            continue
        if _text(metadata.get("semanticHash"), 160) != semantic_hash:
            continue
        if metadata.get("semanticCacheContractVersion") != AGENT1_SEMANTIC_RESULT_CACHE_VERSION:
            continue
        artifact_id = str(record.get("accepted_output_ref") or "")
        if not artifact_id.startswith("ART-"):
            continue
        validation = validate_artifact(artifact_id, expected_type="agent1_model_output.v2259")
        if validation.get("ok") is not True:
            continue
        value = resolve_artifact(artifact_id)
        artifact_value = _dict(value)
        if not isinstance(artifact_value.get("output"), dict):
            continue
        return {
            "execution": record,
            "semanticDescriptor": metadata,
            "outputArtifactRef": artifact_id,
            "outputContentHash": record.get("accepted_output_hash"),
            "output": artifact_value,
        }
    return None


_SEMANTIC_REBIND_KEYS = {
    "dataVersion",
    "correlationId",
    "productId",
    "storeId",
    "signalId",
    "itemExecutionId",
    "executionHash",
    "inputArtifactRef",
    "inputContentHash",
    "outputArtifactRef",
    "outputContentHash",
    "rawBatchOutputRef",
    "resultOrigin",
    "hashIdentityMatched",
    "fallbackIdentityMatchingUsed",
    "cachedOutputRebound",
    "legacyItemCacheUsed",
    "secondProjectionApplied",
    "hashDirectedRuntimeVersion",
    "artifactRefs",
    "signal",
    "agent1ApiCallCount",
    "semanticHash",
    "semanticResultCacheHit",
    "semanticCacheSourceExecutionHash",
    "semanticCacheSourceOutputRef",
    "semanticCacheContractVersion",
}


def _semantic_business_body(output: Dict[str, Any]) -> Dict[str, Any]:
    result = deepcopy(output)
    for key in _SEMANTIC_REBIND_KEYS:
        result.pop(key, None)
    return result


def _rebind_semantic_output(
    cached: Dict[str, Any],
    *,
    descriptor: Dict[str, Any],
    product: Dict[str, Any],
    source: Dict[str, Any],
) -> Dict[str, Any]:
    result = _semantic_business_body(cached)
    source_execution = _dict(source.get("execution"))
    result.update(
        dataVersion=product.get("dataVersion") or descriptor.get("dataVersion"),
        correlationId=product.get("correlationId"),
        productId=product.get("productId") or descriptor.get("productId"),
        storeId=product.get("storeId") or descriptor.get("storeId"),
        signalId=product.get("signalId"),
        signal=product,
        itemExecutionId=descriptor.get("itemExecutionId"),
        executionHash=descriptor.get("executionHash"),
        inputArtifactRef=descriptor.get("inputArtifactRef"),
        inputContentHash=descriptor.get("inputContentHash"),
        agent1ApiCallCount=0,
        semanticHash=descriptor.get("semanticHash"),
        semanticResultCacheHit=True,
        semanticCacheSourceExecutionHash=source_execution.get("execution_hash"),
        semanticCacheSourceOutputRef=source.get("outputArtifactRef"),
        semanticCacheContractVersion=AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
        cachedOutputRebound=True,
        fallbackIdentityMatchingUsed=False,
    )
    identity_resolution = dict(_dict(result.get("identityResolution")))
    identity_resolution.update(
        mode="semanticHash_then_currentExecutionHash",
        canonical=True,
        semanticCacheRebound=True,
        legacyIdentityRematchUsed=False,
    )
    result["identityResolution"] = identity_resolution
    return result


def _store_semantic_rebound_output(
    *,
    descriptor: Dict[str, Any],
    product: Dict[str, Any],
    output: Dict[str, Any],
    source: Dict[str, Any],
) -> Dict[str, Any]:
    source_output_ref = str(source.get("outputArtifactRef") or "")
    value = {
        "schema": "agent1_model_output.v2259",
        "version": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "itemExecutionId": descriptor.get("itemExecutionId"),
        "executionHash": descriptor.get("executionHash"),
        "inputArtifactRef": descriptor.get("inputArtifactRef"),
        "inputContentHash": descriptor.get("inputContentHash"),
        "rawBatchOutputRef": None,
        "stage": descriptor.get("stage"),
        "dataVersion": product.get("dataVersion") or descriptor.get("dataVersion"),
        "semanticHash": descriptor.get("semanticHash"),
        "semanticCacheSourceOutputRef": source_output_ref or None,
        "output": output,
    }
    parents = [
        ref
        for ref in (
            descriptor.get("inputArtifactRef"),
            source_output_ref,
        )
        if str(ref or "").startswith("ART-")
    ]
    return store_artifact(
        artifact_type="agent1_model_output.v2259",
        value=value,
        schema_version=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        tenant_id=descriptor.get("tenantId"),
        store_id=product.get("storeId") or descriptor.get("storeId"),
        product_id=product.get("productId") or descriptor.get("productId"),
        data_version=product.get("dataVersion") or descriptor.get("dataVersion"),
        created_by="agent_token_runtime_hash_exact_v2259_semantic_rebind",
        parent_refs=parents,
        metadata={
            "stage": descriptor.get("stage"),
            "itemExecutionId": descriptor.get("itemExecutionId"),
            "executionHash": descriptor.get("executionHash"),
            "inputContentHash": descriptor.get("inputContentHash"),
            "semanticHash": descriptor.get("semanticHash"),
            "semanticCacheContractVersion": AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
            "semanticCacheSourceOutputRef": source_output_ref or None,
            "cachedOutputRebound": True,
        },
    )


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
    semantic_rebound = origin == "semantic_result_cache_rebound"
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
        semanticHash=descriptor.get("semanticHash"),
        semanticCacheContractVersion=AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
        semanticResultCacheHit=semantic_rebound,
        cachedOutputRebound=semantic_rebound,
        legacyItemCacheUsed=False,
        secondProjectionApplied=False,
        hashDirectedRuntimeVersion=AGENT_TOKEN_RUNTIME_VERSION,
    )
    refs = dict(_dict(result.get("artifactRefs")))
    refs["agentExecutionInputRef"] = descriptor.get("inputArtifactRef")
    refs["agentExecutionOutputRef"] = output_ref
    if raw_batch_ref:
        refs["agentRawBatchOutputRef"] = raw_batch_ref
    semantic_source_ref = str(result.get("semanticCacheSourceOutputRef") or "")
    if semantic_source_ref.startswith("ART-"):
        refs["agentSemanticCacheSourceRef"] = semantic_source_ref
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
    semantic = build_agent1_semantic_identity(envelope, descriptor)
    descriptor.update(
        semanticHash=semantic.get("semanticHash"),
        semanticInputHash=semantic.get("semanticInputHash"),
        semanticContractHash=semantic.get("semanticContractHash"),
        semanticIdentitySchema=semantic.get("schema"),
        semanticCacheContractVersion=AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
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
        "semanticCacheMayRebindBusinessBodyOnly": True,
        "semanticCacheMustCreateNewExactOutputArtifact": True,
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
        itemResultCacheEnabled=True,
        semanticResultCacheEnabled=True,
        semanticCacheContractVersion=AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
        secondProjectionApplied=False,
        cachedOutputRebindingAllowed=True,
        cachedOutputRebindingScope="semantic_business_body_to_new_exact_output_artifact_only",
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
            "runtimeSource": "semanticHash+exact_agent1InputRef_content_hash",
            "semanticResultCacheEnabled": True,
            "fallbackAllowed": False,
        }

    provider = provider_runtime_config(_AGENT1_STAGE)
    judgments: List[Dict[str, Any]] = []
    claimed_entries: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    usages: List[Dict[str, Any]] = []
    errors: List[str] = []
    semantic_cache_errors: List[str] = []
    replay_count = 0
    waited_count = 0
    busy_count = 0
    claimed_count = 0
    semantic_hit_count = 0
    semantic_miss_count = 0
    semantic_rebound_count = 0
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
            claimed_count += 1
            semantic_source: Dict[str, Any] | None = None
            try:
                semantic_source = _accepted_semantic_execution(
                    str(descriptor.get("semanticHash") or ""),
                    exclude_execution_hash=str(descriptor.get("executionHash") or ""),
                )
            except Exception as exc:
                semantic_cache_errors.append(
                    f"lookup:{descriptor.get('itemExecutionId')}:{str(exc)[:420]}"
                )

            semantic_cached = _artifact_business_output(semantic_source)
            if semantic_cached and semantic_source:
                try:
                    rebound = _rebind_semantic_output(
                        semantic_cached,
                        descriptor=descriptor,
                        product=entry["product"],
                        source=semantic_source,
                    )
                    artifact = _store_semantic_rebound_output(
                        descriptor=descriptor,
                        product=entry["product"],
                        output=rebound,
                        source=semantic_source,
                    )
                    completion = complete_execution(
                        descriptor,
                        claim_id=str(claim.get("claimId") or ""),
                        output_artifact_ref=str(artifact["artifactId"]),
                        output_content_hash=str(artifact["contentHash"]),
                        raw_batch_output_ref=None,
                    )
                    judgments = helpers._merge_agent1_judgments(
                        judgments,
                        [
                            _decorate(
                                rebound,
                                descriptor=descriptor,
                                output_ref=str(
                                    completion.get("outputArtifactRef")
                                    or artifact["artifactId"]
                                ),
                                output_hash=str(
                                    completion.get("outputContentHash")
                                    or artifact["contentHash"]
                                ),
                                raw_batch_ref=None,
                                origin="semantic_result_cache_rebound",
                            )
                        ],
                    )
                    semantic_hit_count += 1
                    semantic_rebound_count += 1
                    continue
                except Exception as exc:
                    semantic_cache_errors.append(
                        f"rebind:{descriptor.get('itemExecutionId')}:{str(exc)[:420]}"
                    )

            semantic_miss_count += 1
            claimed_entries.append(entry)
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
    all_from_semantic_cache = bool(
        valid
        and semantic_hit_count == len(valid)
        and replay_count == 0
        and waited_count == 0
        and not batches
        and not errors
    )
    summary.update(
        version=AGENT_TOKEN_RUNTIME_VERSION,
        providerStatus=(
            "semantic_cache_replay"
            if all_from_semantic_cache
            else "provider_succeeded"
            if missing_count == 0 and not errors
            else "provider_partial"
            if judgments
            else "provider_failed"
        ),
        normalizationStatus=(
            "semantic_cache_rebound"
            if all_from_semantic_cache
            else "exact_hash_matched"
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
        semanticResultCacheHitCount=semantic_hit_count,
        semanticResultCacheMissCount=semantic_miss_count,
        semanticReboundOutputCount=semantic_rebound_count,
        semanticCacheErrors=semantic_cache_errors,
        providerBatchCount=len(batches),
        trueMissingItemCount=true_missing_count,
        singletonRetryCount=retry_count,
        outputContractInvalidCount=contract_invalid_count,
        normalizationRejectionArtifactRefs=sorted(set(rejection_refs)),
        batchDiagnostics=diagnostics,
        errors=errors,
        runtimeSource="semanticHash+exact_agent1InputRef_content_hash",
        matchingContract=(
            "exactExecutionHash_replay_or_semanticHash_then_currentExecutionHash_rebind"
        ),
        providerOutputMatchingContract="itemExecutionId+inputContentHash",
        batchBoundary="up_to_8_independent_input_hashes",
        semanticIdentitySchema=AGENT1_SEMANTIC_IDENTITY_SCHEMA,
        semanticCacheContractVersion=AGENT1_SEMANTIC_RESULT_CACHE_VERSION,
        semanticResultCacheEnabled=True,
        semanticCacheUsesExistingExecutionLedger=True,
        semanticCacheCreatesNewOutputArtifact=True,
        semanticCacheCrossProductReuseAllowed=False,
        requestCacheEnabled=False,
        itemResultCacheEnabled=True,
        secondProjectionApplied=False,
        cachedOutputRebindingAllowed=True,
        cachedOutputRebindingScope="semantic_business_body_to_new_exact_output_artifact_only",
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
    "AGENT1_SEMANTIC_RESULT_CACHE_VERSION",
    "AGENT1_SEMANTIC_IDENTITY_SCHEMA",
    "AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION",
    "build_agent1_semantic_identity",
    "run_agent1_projected_inputs",
    "run_agent2_draft_projected_inputs",
    "run_agent2_projected_inputs",
    "run_agent3_sop_projected_inputs",
]
