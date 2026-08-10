"""V22.5.20 exact Agent2 output runtime with V23.1.7 familyPayload cache.

ExecutionHash remains the execution/audit authority. Agent2 may additionally reuse a
previously accepted *model-owned* ``familyPayload`` when the compact business input and
current generation contract have the same SemanticHash. The cached payload is always
re-normalized by the current system compiler, written into a new immutable current
output Artifact and accepted under the current ExecutionHash.

Business ``packageId`` is never used as the acceptance authority. Request-level cache
remains disabled and non-ready channels are never semantically cached.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from copy import deepcopy
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, loads
from src.services import agent_token_runtime_v230_service as runtime_helpers
from src.services.agent2_action_draft_core_v225_service import (
    AGENT2_ACTION_DRAFT_CORE_VERSION,
    AGENT2_FAMILY_PAYLOAD_SCHEMA,
    AGENT2_GENERATION_COMPILER_VERSION,
    DRAFT_READY,
    _build_messages,
    _compact_package,
    _normalize_draft,
    missing_agent2_draft_contract,
    selected_family,
)
from src.services.agent2_hash_proof_bridge_v22515_service import (
    ensure_agent2_runtime_identity_tables,
)
from src.services.agent_input_contract_v225_service import (
    AGENT2_DRAFT_INPUT_SCHEMA,
    assert_agent_input_envelope,
    split_envelopes_by_budget,
)
from src.services.agent_token_runtime_hash_exact_v2259_service import (
    run_agent1_projected_inputs,
)
from src.services.agent_token_runtime_v2259_service import (
    run_agent3_sop_projected_inputs,
)
from src.services.artifact_transport_service import (
    resolve_artifact,
    store_artifact,
    validate_artifact,
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
from src.services.llm_gateway_hash_directed_v2259_service import (
    call_json_exact_artifact,
)
from src.services.llm_gateway_v196_service import provider_runtime_config

THREE_AGENT_PIPELINE_VERSION = "22.5.20"
AGENT_TOKEN_RUNTIME_VERSION = "22.5.20"
AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION = "22.5.20"
AGENT2_FAMILY_PAYLOAD_CACHE_VERSION = "23.1.7"
AGENT2_SEMANTIC_IDENTITY_SCHEMA = "agent2.family_payload_semantic_identity.v1"
AGENT2_EXACT_OUTPUT_STAGE = "action_plan_judgment_agent"
AGENT2_EXACT_OUTPUT_TYPE = "agent2_model_output.v2259"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _package_id(envelope: Dict[str, Any]) -> str:
    payload = _dict(envelope.get("payload"))
    return _text(payload.get("packageId") or payload.get("itemId"), 220)


def _semantic_cache_eligible(envelope: Dict[str, Any], package: Dict[str, Any]) -> bool:
    extensions = _dict(package.get("diagnosticExtensions"))
    if _dict(extensions.get("agent2ContractRepair")):
        return False
    runtime_execution = _dict(_dict(envelope.get("projectionAudit")).get("runtimeExecution"))
    if _text(runtime_execution.get("executionMode"), 120) in {
        "provider_regeneration_after_invalid_replay",
        "agent2_contract_repair",
    }:
        return False
    return True


def _semantic_compact_package(package: Dict[str, Any]) -> Dict[str, Any]:
    compact = deepcopy(_compact_package(package))
    # packageId is a runtime package identity. productId/storeId remain inside compact
    # and therefore semantic reuse can never cross business objects.
    compact.pop("packageId", None)
    return compact


def build_agent2_semantic_identity(
    envelope: Dict[str, Any],
    descriptor: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    semantic_input = {
        "actionFamily": selected_family(package),
        "compactPackage": _semantic_compact_package(package),
    }
    semantic_input_hash = hash_value(semantic_input)
    semantic_contract = {
        "semanticCacheVersion": AGENT2_FAMILY_PAYLOAD_CACHE_VERSION,
        "stage": descriptor.get("stage") or AGENT2_EXACT_OUTPUT_STAGE,
        "inputSchema": descriptor.get("inputSchema") or AGENT2_DRAFT_INPUT_SCHEMA,
        "projectionVersion": descriptor.get("projectionVersion"),
        "promptVersion": descriptor.get("promptVersion"),
        "actionDraftCoreVersion": AGENT2_ACTION_DRAFT_CORE_VERSION,
        "generationCompilerVersion": AGENT2_GENERATION_COMPILER_VERSION,
        "familyPayloadSchema": AGENT2_FAMILY_PAYLOAD_SCHEMA,
        "policyHash": descriptor.get("policyHash"),
        "provider": descriptor.get("provider"),
        "model": descriptor.get("model"),
        "generationParametersHash": descriptor.get("generationParametersHash"),
    }
    semantic_contract_hash = hash_value(semantic_contract)
    semantic_hash = hash_value(
        {
            "schema": AGENT2_SEMANTIC_IDENTITY_SCHEMA,
            "semanticInputHash": semantic_input_hash,
            "semanticContractHash": semantic_contract_hash,
        }
    )
    return {
        "schema": AGENT2_SEMANTIC_IDENTITY_SCHEMA,
        "version": AGENT2_FAMILY_PAYLOAD_CACHE_VERSION,
        "semanticHash": semantic_hash,
        "semanticInputHash": semantic_input_hash,
        "semanticContractHash": semantic_contract_hash,
        "cacheEligible": _semantic_cache_eligible(envelope, package),
        "cachedChannel": "familyPayload",
        "crossProductReuseAllowed": False,
        "packageIdExcluded": True,
    }


def _entry(
    envelope: Dict[str, Any],
    *,
    provider: Dict[str, Any],
) -> Dict[str, Any]:
    binding = resolve_input_binding(
        envelope,
        expected_type=AGENT2_DRAFT_INPUT_SCHEMA,
    )
    package = dict(_dict(envelope.get("payload")))
    contract = _dict(package.get("inputContract"))
    policy_hash = _text(contract.get("policyContextHash"), 160) or hash_value(
        {
            "executionLock": package.get("executionLock"),
            "actionParameterPack": package.get("actionParameterPack"),
            "verticalActionRag": package.get("verticalActionRag"),
        }
    )
    descriptor = build_execution_descriptor(
        stage=AGENT2_EXACT_OUTPUT_STAGE,
        binding=binding,
        input_schema=str(envelope.get("schema") or AGENT2_DRAFT_INPUT_SCHEMA),
        projection_version=str(
            envelope.get("projectionVersion")
            or envelope.get("schemaVersion")
            or contract.get("projectionVersion")
            or "22.5.14"
        ),
        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
        policy_hash=policy_hash,
        provider=str(provider.get("provider") or ""),
        model=str(provider.get("model") or ""),
        generation_parameters={
            "temperature": 0.16,
            "thinkingEnabled": bool(provider.get("thinkingEnabled")),
            "thinkingBudget": provider.get("thinkingBudget"),
            "responseFormat": "json_object",
            "identityContract": "itemExecutionId+inputContentHash",
        },
    )
    descriptor.update(
        packageId=_package_id(envelope),
        storeId=package.get("storeId") or descriptor.get("storeId"),
        productId=package.get("productId") or descriptor.get("productId"),
        dataVersion=package.get("dataVersion") or descriptor.get("dataVersion"),
        actionFamily=selected_family(package),
    )
    semantic = build_agent2_semantic_identity(envelope, descriptor, package)
    descriptor.update(
        semanticHash=semantic.get("semanticHash"),
        semanticInputHash=semantic.get("semanticInputHash"),
        semanticContractHash=semantic.get("semanticContractHash"),
        semanticIdentitySchema=semantic.get("schema"),
        semanticCacheContractVersion=AGENT2_FAMILY_PAYLOAD_CACHE_VERSION,
        semanticCacheEligible=semantic.get("cacheEligible") is True,
        semanticCachedChannel="familyPayload",
    )
    return {
        "envelope": envelope,
        "package": package,
        "descriptor": descriptor,
        "claim": {},
    }


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
    output_ref: str,
    output_hash: str,
    raw_ref: str | None,
    replay: bool,
    semantic_hit: bool = False,
    semantic_source_execution_hash: str | None = None,
    semantic_source_output_ref: str | None = None,
) -> Dict[str, Any]:
    result = dict(output)
    result.update(
        itemExecutionId=descriptor.get("itemExecutionId"),
        executionHash=descriptor.get("executionHash"),
        inputArtifactRef=descriptor.get("inputArtifactRef"),
        inputContentHash=descriptor.get("inputContentHash"),
        outputArtifactRef=output_ref,
        outputContentHash=output_hash,
        rawBatchOutputRef=raw_ref,
        exactExecutionReplay=bool(replay),
        semanticResultCacheHit=bool(semantic_hit),
        cachedOutputRebound=bool(semantic_hit),
        semanticHash=descriptor.get("semanticHash"),
        semanticCacheContractVersion=AGENT2_FAMILY_PAYLOAD_CACHE_VERSION,
        semanticCacheSourceExecutionHash=(
            semantic_source_execution_hash if semantic_hit else None
        ),
        semanticCacheSourceOutputRef=(semantic_source_output_ref if semantic_hit else None),
        agent2ApiCallCount=0 if semantic_hit else result.get("agent2ApiCallCount"),
        hashIdentityMatched=True,
        fallbackIdentityMatchingUsed=False,
        hashDirectedRuntimeVersion=AGENT_TOKEN_RUNTIME_VERSION,
    )
    refs = dict(_dict(result.get("artifactRefs")))
    refs["agentExecutionInputRef"] = descriptor.get("inputArtifactRef")
    refs["agentExecutionOutputRef"] = output_ref
    if raw_ref:
        refs["agentRawBatchOutputRef"] = raw_ref
    if semantic_hit and str(semantic_source_output_ref or "").startswith("ART-"):
        refs["agent2SemanticFamilyPayloadSourceRef"] = semantic_source_output_ref
    result["artifactRefs"] = refs
    return {
        key: value
        for key, value in result.items()
        if value not in (None, "")
    }


def _accepted_semantic_family_payload(
    descriptor: Dict[str, Any],
) -> Dict[str, Any] | None:
    if descriptor.get("semanticCacheEligible") is not True:
        return None
    semantic_hash = _text(descriptor.get("semanticHash"), 160)
    if not semantic_hash:
        return None
    ensure_agent2_runtime_identity_tables()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM artifact_execution_index_v2259
            WHERE stage=?
              AND status='accepted'
              AND accepted_output_ref IS NOT NULL
              AND COALESCE(reusable,1)=1
              AND accepted_contract_version=?
              AND metadata_json LIKE ?
            ORDER BY updated_at DESC
            LIMIT 64
            """,
            (
                AGENT2_EXACT_OUTPUT_STAGE,
                AGENT2_GENERATION_COMPILER_VERSION,
                f"%{semantic_hash}%",
            ),
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
        if metadata.get("semanticCacheContractVersion") != AGENT2_FAMILY_PAYLOAD_CACHE_VERSION:
            continue
        if metadata.get("semanticCacheEligible") is not True:
            continue
        if _text(metadata.get("productId"), 160) != _text(descriptor.get("productId"), 160):
            continue
        if _text(metadata.get("storeId"), 160) != _text(descriptor.get("storeId"), 160):
            continue
        if _text(metadata.get("actionFamily"), 120) != _text(descriptor.get("actionFamily"), 120):
            continue
        output_ref = _text(record.get("accepted_output_ref"), 220)
        if not output_ref.startswith("ART-"):
            continue
        validation = validate_artifact(output_ref, expected_type=AGENT2_EXACT_OUTPUT_TYPE)
        if validation.get("ok") is not True:
            continue
        artifact_value = _dict(resolve_artifact(output_ref))
        source_draft = _dict(artifact_value.get("output"))
        if not source_draft:
            continue
        if source_draft.get("draftStatus") != DRAFT_READY:
            continue
        family_payload = _dict(source_draft.get("familyPayload"))
        if not family_payload:
            continue
        if missing_agent2_draft_contract(source_draft):
            continue
        return {
            "execution": record,
            "outputArtifactRef": output_ref,
            "outputContentHash": record.get("accepted_output_hash"),
            "familyPayload": deepcopy(family_payload),
        }
    return None


def _rebind_semantic_family_payload(
    source: Dict[str, Any],
    *,
    entry: Dict[str, Any],
) -> Dict[str, Any] | None:
    family_payload = _dict(source.get("familyPayload"))
    if not family_payload:
        return None
    draft = _normalize_draft(
        {"familyPayload": deepcopy(family_payload)},
        entry["package"],
        proof={},
    )
    if draft.get("draftStatus") != DRAFT_READY:
        return None
    if missing_agent2_draft_contract(draft):
        return None
    source_execution = _dict(source.get("execution"))
    draft.update(
        semanticResultCacheHit=True,
        cachedOutputRebound=True,
        semanticHash=entry["descriptor"].get("semanticHash"),
        semanticCacheContractVersion=AGENT2_FAMILY_PAYLOAD_CACHE_VERSION,
        semanticCacheSourceExecutionHash=source_execution.get("execution_hash"),
        semanticCacheSourceOutputRef=source.get("outputArtifactRef"),
        semanticCachedChannel="familyPayload",
        agent2ApiCallCount=0,
        fallbackAllowed=False,
    )
    return draft


def _store_semantic_rebound_output(
    *,
    entry: Dict[str, Any],
    draft: Dict[str, Any],
    source: Dict[str, Any],
) -> Dict[str, Any]:
    descriptor = entry["descriptor"]
    source_ref = _text(source.get("outputArtifactRef"), 220)
    value = {
        "schema": AGENT2_EXACT_OUTPUT_TYPE,
        "version": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "itemExecutionId": descriptor.get("itemExecutionId"),
        "executionHash": descriptor.get("executionHash"),
        "inputArtifactRef": descriptor.get("inputArtifactRef"),
        "inputContentHash": descriptor.get("inputContentHash"),
        "rawBatchOutputRef": None,
        "stage": descriptor.get("stage"),
        "dataVersion": descriptor.get("dataVersion"),
        "semanticHash": descriptor.get("semanticHash"),
        "semanticCacheSourceOutputRef": source_ref or None,
        "output": draft,
    }
    parents = [
        ref
        for ref in (descriptor.get("inputArtifactRef"), source_ref)
        if str(ref or "").startswith("ART-")
    ]
    return store_artifact(
        artifact_type=AGENT2_EXACT_OUTPUT_TYPE,
        value=value,
        schema_version=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        tenant_id=descriptor.get("tenantId"),
        store_id=descriptor.get("storeId"),
        product_id=descriptor.get("productId"),
        data_version=descriptor.get("dataVersion"),
        created_by="agent_token_runtime_v22520_semantic_family_payload_rebind",
        parent_refs=parents,
        metadata={
            "stage": descriptor.get("stage"),
            "itemExecutionId": descriptor.get("itemExecutionId"),
            "executionHash": descriptor.get("executionHash"),
            "inputContentHash": descriptor.get("inputContentHash"),
            "semanticHash": descriptor.get("semanticHash"),
            "semanticCacheContractVersion": AGENT2_FAMILY_PAYLOAD_CACHE_VERSION,
            "semanticCacheSourceOutputRef": source_ref or None,
            "semanticCachedChannel": "familyPayload",
            "cachedOutputRebound": True,
        },
    )


def _inject_exact_contract(
    messages: List[Dict[str, str]],
    entries: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    result = [dict(item) for item in messages]
    user_index = next(
        (
            index
            for index in range(len(result) - 1, -1, -1)
            if result[index].get("role") == "user"
        ),
        None,
    )
    if user_index is None:
        raise ValueError("agent2_exact_user_message_missing")
    payload = json.loads(str(result[user_index].get("content") or "{}"))
    if not isinstance(payload, dict):
        raise ValueError("agent2_exact_user_payload_not_object")

    entries_by_package = {
        str(entry["descriptor"].get("packageId") or ""): entry
        for entry in entries
    }
    injected = 0
    for package in payload.get("packages") or []:
        if not isinstance(package, dict):
            continue
        entry = entries_by_package.get(str(package.get("packageId") or ""))
        if not entry:
            continue
        descriptor = entry["descriptor"]
        package.update(
            itemExecutionId=descriptor.get("itemExecutionId"),
            executionHash=descriptor.get("executionHash"),
            inputArtifactRef=descriptor.get("inputArtifactRef"),
            inputContentHash=descriptor.get("inputContentHash"),
        )
        injected += 1
    if injected != len(entries):
        raise ValueError(
            f"agent2_exact_input_identity_injection_incomplete:{injected}/{len(entries)}"
        )

    payload["_hashDirectedExecution"] = True
    payload["exactOutputIdentity"] = "itemExecutionId+inputContentHash"
    result[user_index]["content"] = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    contract = (
        "\nV22.5.20硬合同：plans中的每个输出项必须原样返回输入的"
        "itemExecutionId和inputContentHash；不得省略、复制、猜测或改写。"
        "packageId仅用于业务展示，不能替代执行身份。"
    )
    system_index = next(
        (index for index, item in enumerate(result) if item.get("role") == "system"),
        None,
    )
    if system_index is None:
        result.insert(0, {"role": "system", "content": contract.strip()})
    else:
        result[system_index]["content"] = (
            str(result[system_index].get("content") or "") + contract
        )
    return result, payload


def _raw_match(
    raw_plans: List[Any],
    entry: Dict[str, Any],
) -> Tuple[Dict[str, Any] | None, str]:
    """Return an exact plan and classify any non-acceptable raw response.

    Package identity is inspected only to distinguish a malformed returned item from a
    truly missing item. It is never sufficient for acceptance.
    """
    descriptor = entry["descriptor"]
    expected_id = _text(descriptor.get("itemExecutionId"), 160)
    expected_hash = _text(descriptor.get("inputContentHash"), 160)
    expected_package = _text(descriptor.get("packageId"), 220)

    exact: List[Dict[str, Any]] = []
    related = False
    for value in raw_plans:
        if not isinstance(value, dict):
            continue
        raw_id = _text(value.get("itemExecutionId"), 160)
        raw_hash = _text(value.get("inputContentHash"), 160)
        raw_package = _text(value.get("packageId"), 220)
        if raw_id == expected_id or (raw_package and raw_package == expected_package):
            related = True
        if raw_id == expected_id and raw_hash == expected_hash:
            if raw_package and raw_package != expected_package:
                return None, "contract_invalid_package_mismatch"
            exact.append(dict(value))

    if len(exact) == 1:
        return exact[0], "exact"
    if len(exact) > 1:
        return None, "contract_invalid_duplicate_exact_identity"
    if related:
        return None, "contract_invalid_identity"
    return None, "true_missing"


def _execute_batch(
    entries: List[Dict[str, Any]],
    *,
    data_version: str | None,
    provider: Dict[str, Any],
    retry_attempt: int | None = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Dict[str, Any], Dict[str, Any]]:
    descriptors = [entry["descriptor"] for entry in entries]
    batch = create_batch_manifest(
        stage=AGENT2_EXACT_OUTPUT_STAGE,
        descriptors=descriptors,
        data_version=data_version,
        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
        provider=str(provider.get("provider") or ""),
        model=str(provider.get("model") or ""),
    )
    packages = [entry["package"] for entry in entries]
    messages, _ = _build_messages(data_version, packages)
    messages, exact_payload = _inject_exact_contract(messages, entries)
    payload, usage = call_json_exact_artifact(
        stage=AGENT2_EXACT_OUTPUT_STAGE,
        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
        messages=messages,
        execution_hashes=[str(item.get("executionHash") or "") for item in descriptors],
        temperature=0.16,
        timeout_seconds=int(os.getenv("ACTION_DRAFT_AGENT_TIMEOUT", "240")),
    )
    raw_artifact = store_raw_batch_output(
        batch=batch,
        provider_payload=payload,
        provider_usage=usage,
        data_version=data_version,
    )
    raw_ref = str(raw_artifact["artifactId"])
    raw_plans = payload.get("plans") if isinstance(payload, dict) else None
    if not isinstance(raw_plans, list):
        raw_plans = []

    accepted: Dict[str, Dict[str, Any]] = {}
    outcomes: Dict[str, str] = {}
    accepted_ids: List[str] = []
    returned_ids: List[str] = []
    for value in raw_plans:
        if isinstance(value, dict) and _text(value.get("itemExecutionId"), 160):
            returned_ids.append(_text(value.get("itemExecutionId"), 160))

    for entry in entries:
        descriptor = entry["descriptor"]
        item_execution_id = str(descriptor.get("itemExecutionId") or "")
        package_id = str(descriptor.get("packageId") or "")
        raw, outcome = _raw_match(raw_plans, entry)
        outcomes[item_execution_id] = outcome
        if not raw:
            continue
        draft = _normalize_draft(raw, entry["package"], proof={})
        artifact = store_item_output(
            descriptor=descriptor,
            output=draft,
            raw_batch_output_ref=raw_ref,
            artifact_type=AGENT2_EXACT_OUTPUT_TYPE,
        )
        completion = complete_execution(
            descriptor,
            claim_id=str(_dict(entry.get("claim")).get("claimId") or ""),
            output_artifact_ref=str(artifact["artifactId"]),
            output_content_hash=str(artifact["contentHash"]),
            raw_batch_output_ref=raw_ref,
        )
        accepted[package_id] = _decorate_output(
            draft,
            descriptor=descriptor,
            output_ref=str(completion.get("outputArtifactRef") or artifact["artifactId"]),
            output_hash=str(completion.get("outputContentHash") or artifact["contentHash"]),
            raw_ref=raw_ref,
            replay=False,
        )
        accepted_ids.append(item_execution_id)

    batch_result = finalize_batch(
        batch=batch,
        returned_item_execution_ids=returned_ids,
        accepted_item_execution_ids=accepted_ids,
        raw_batch_output_ref=raw_ref,
    )
    diagnostic = {
        "actionFamily": selected_family(packages[0]) if packages else None,
        "batchManifestRef": batch.get("batchManifestRef"),
        "batchManifestHash": batch.get("batchManifestHash"),
        "rawBatchOutputRef": raw_ref,
        "rawPlanCount": len(raw_plans),
        "acceptedCount": len(accepted),
        "outcomes": outcomes,
        "retryAttempt": retry_attempt,
        "retryMode": "singleton_true_missing" if retry_attempt else "microbatch_exact_hash",
        "exactRequestPayloadHash": hash_value(exact_payload),
        "batchResult": batch_result,
    }
    return accepted, outcomes, diagnostic, _dict(usage)


def run_agent2_draft_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 5,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(
            envelope,
            expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
        )
        valid.append(envelope)
    if not valid:
        return {}, {
            "version": AGENT_TOKEN_RUNTIME_VERSION,
            "stage": "agent2_action_draft",
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "draftCount": 0,
            "runtimeSource": "agent2DraftInputArtifact.semanticFamilyPayload+exactHash.v2317",
            "semanticFamilyPayloadCacheEnabled": True,
            "fallbackAllowed": False,
        }

    provider = provider_runtime_config(AGENT2_EXACT_OUTPUT_STAGE)
    outputs: Dict[str, Dict[str, Any]] = {}
    claimed: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    usages: List[Dict[str, Any]] = []
    errors: List[str] = []
    semantic_cache_errors: List[str] = []
    replay_count = busy_count = contract_invalid_count = true_missing_count = 0
    semantic_hit_count = semantic_miss_count = semantic_rebound_count = 0

    for envelope in valid:
        try:
            entry = _entry(envelope, provider=provider)
            descriptor = entry["descriptor"]
            replay = accepted_execution(str(descriptor.get("executionHash") or ""))
            cached = _cached_business_output(replay)
            if cached:
                outputs[str(descriptor.get("packageId") or "")] = _decorate_output(
                    cached,
                    descriptor=descriptor,
                    output_ref=str(replay.get("outputArtifactRef") or ""),
                    output_hash=str(replay.get("outputContentHash") or ""),
                    raw_ref=_dict(replay.get("execution")).get("raw_batch_output_ref"),
                    replay=True,
                )
                replay_count += 1
                continue
            claim = claim_execution(descriptor)
            if claim.get("status") == "accepted_replay":
                cached = _cached_business_output(claim)
                if cached:
                    outputs[str(descriptor.get("packageId") or "")] = _decorate_output(
                        cached,
                        descriptor=descriptor,
                        output_ref=str(claim.get("outputArtifactRef") or ""),
                        output_hash=str(claim.get("outputContentHash") or ""),
                        raw_ref=_dict(claim.get("execution")).get("raw_batch_output_ref"),
                        replay=True,
                    )
                    replay_count += 1
                continue
            if claim.get("status") != "claimed":
                busy_count += 1
                errors.append(
                    f"execution_busy:{descriptor.get('itemExecutionId')}:{claim.get('status')}"
                )
                continue
            entry["claim"] = claim

            semantic_source: Dict[str, Any] | None = None
            if descriptor.get("semanticCacheEligible") is True:
                try:
                    semantic_source = _accepted_semantic_family_payload(descriptor)
                except Exception as exc:
                    semantic_cache_errors.append(
                        f"lookup:{descriptor.get('itemExecutionId')}:{_text(exc, 420)}"
                    )
            semantic_draft = None
            if semantic_source:
                try:
                    semantic_draft = _rebind_semantic_family_payload(
                        semantic_source,
                        entry=entry,
                    )
                except Exception as exc:
                    semantic_cache_errors.append(
                        f"rebind:{descriptor.get('itemExecutionId')}:{_text(exc, 420)}"
                    )
            if semantic_draft and semantic_source:
                try:
                    artifact = _store_semantic_rebound_output(
                        entry=entry,
                        draft=semantic_draft,
                        source=semantic_source,
                    )
                    completion = complete_execution(
                        descriptor,
                        claim_id=str(claim.get("claimId") or ""),
                        output_artifact_ref=str(artifact["artifactId"]),
                        output_content_hash=str(artifact["contentHash"]),
                        raw_batch_output_ref=None,
                    )
                    source_execution = _dict(semantic_source.get("execution"))
                    outputs[str(descriptor.get("packageId") or "")] = _decorate_output(
                        semantic_draft,
                        descriptor=descriptor,
                        output_ref=str(
                            completion.get("outputArtifactRef") or artifact["artifactId"]
                        ),
                        output_hash=str(
                            completion.get("outputContentHash") or artifact["contentHash"]
                        ),
                        raw_ref=None,
                        replay=False,
                        semantic_hit=True,
                        semantic_source_execution_hash=str(
                            source_execution.get("execution_hash") or ""
                        ),
                        semantic_source_output_ref=str(
                            semantic_source.get("outputArtifactRef") or ""
                        ),
                    )
                    semantic_hit_count += 1
                    semantic_rebound_count += 1
                    continue
                except Exception as exc:
                    semantic_cache_errors.append(
                        f"persist:{descriptor.get('itemExecutionId')}:{_text(exc, 420)}"
                    )

            if descriptor.get("semanticCacheEligible") is True:
                semantic_miss_count += 1
            claimed.append(entry)
        except Exception as exc:
            errors.append(f"prepare:{_text(exc, 500)}")

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in claimed:
        grouped[str(entry["descriptor"].get("actionFamily") or "")].append(entry)

    true_missing: List[Dict[str, Any]] = []
    provider_batch_count = 0
    for family in sorted(grouped):
        by_envelope = {id(entry["envelope"]): entry for entry in grouped[family]}
        batches = split_envelopes_by_budget(
            [entry["envelope"] for entry in grouped[family]],
            expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
            max_items=max(1, min(12, int(max_items_per_call or 5))),
        )
        for batch_envelopes in batches:
            entries = [by_envelope[id(envelope)] for envelope in batch_envelopes]
            try:
                accepted, outcomes, diagnostic, usage = _execute_batch(
                    entries,
                    data_version=data_version,
                    provider=provider,
                )
                provider_batch_count += 1
                outputs.update(accepted)
                diagnostics.append(diagnostic)
                usages.append(runtime_helpers._usage_record(usage))
                for entry in entries:
                    item_execution_id = str(
                        entry["descriptor"].get("itemExecutionId") or ""
                    )
                    outcome = outcomes.get(item_execution_id, "true_missing")
                    if outcome == "true_missing":
                        true_missing.append(entry)
                        true_missing_count += 1
                    elif outcome != "exact":
                        contract_invalid_count += 1
                        fail_execution(
                            entry["descriptor"],
                            claim_id=str(entry["claim"].get("claimId") or ""),
                            error="agent2_exact_hash_output_contract_invalid:" + outcome,
                        )
            except Exception as exc:
                errors.append(f"{family}:provider:{_text(exc, 500)}")
                for entry in entries:
                    fail_execution(
                        entry["descriptor"],
                        claim_id=str(entry["claim"].get("claimId") or ""),
                        error="agent2_exact_batch_failed:" + _text(exc, 500),
                    )

    singleton_retry_count = 0
    for entry in true_missing:
        try:
            accepted, outcomes, diagnostic, usage = _execute_batch(
                [entry],
                data_version=data_version,
                provider=provider,
                retry_attempt=1,
            )
            provider_batch_count += 1
            singleton_retry_count += 1
            outputs.update(accepted)
            diagnostics.append(diagnostic)
            usages.append(runtime_helpers._usage_record(usage, retry=True))
            item_execution_id = str(entry["descriptor"].get("itemExecutionId") or "")
            outcome = outcomes.get(item_execution_id, "true_missing")
            if outcome != "exact":
                if outcome != "true_missing":
                    contract_invalid_count += 1
                fail_execution(
                    entry["descriptor"],
                    claim_id=str(entry["claim"].get("claimId") or ""),
                    error=(
                        "agent2_exact_output_missing_after_singleton_retry"
                        if outcome == "true_missing"
                        else "agent2_exact_hash_output_contract_invalid:" + outcome
                    ),
                )
        except Exception as exc:
            errors.append(f"singleton:{_text(exc, 500)}")
            fail_execution(
                entry["descriptor"],
                claim_id=str(entry["claim"].get("claimId") or ""),
                error="agent2_exact_singleton_failed:" + _text(exc, 500),
            )

    summary = runtime_helpers._usage_summary(
        usages,
        stage=AGENT2_EXACT_OUTPUT_STAGE,
    )
    all_semantic_hits = bool(
        valid
        and semantic_hit_count == len(valid)
        and replay_count == 0
        and not claimed
        and not errors
    )
    summary.update(
        version=AGENT_TOKEN_RUNTIME_VERSION,
        providerStatus=(
            "semantic_cache_replay"
            if all_semantic_hits
            else "ok"
            if len(outputs) == len(valid) and not errors
            else "partial"
            if outputs
            else "failed"
        ),
        draftCount=len(outputs),
        inputCount=len(valid),
        exactExecutionReplayCount=replay_count,
        semanticFamilyPayloadCacheHitCount=semantic_hit_count,
        semanticFamilyPayloadCacheMissCount=semantic_miss_count,
        semanticFamilyPayloadReboundCount=semantic_rebound_count,
        semanticCacheErrors=semantic_cache_errors,
        alreadyRunningCount=busy_count,
        exactContractInvalidCount=contract_invalid_count,
        trueMissingCount=true_missing_count,
        singletonRetryCount=singleton_retry_count,
        providerBatchCount=provider_batch_count,
        batchDiagnostics=diagnostics,
        errors=errors,
        itemProvenance={},
        runtimeSource="agent2DraftInputArtifact.semanticFamilyPayload+exactHash.v2317",
        hashDirectedExecution=True,
        rawBatchArtifactStored=bool(provider_batch_count),
        acceptanceIdentity="itemExecutionId+inputContentHash",
        packageIdAcceptanceAllowed=False,
        semanticIdentitySchema=AGENT2_SEMANTIC_IDENTITY_SCHEMA,
        semanticCacheContractVersion=AGENT2_FAMILY_PAYLOAD_CACHE_VERSION,
        semanticFamilyPayloadCacheEnabled=True,
        semanticCachedChannel="familyPayload",
        semanticNonReadyChannelsCached=False,
        semanticCacheUsesExistingExecutionLedger=True,
        semanticCacheRequiresReusableAcceptedContract=True,
        semanticCacheCreatesNewOutputArtifact=True,
        semanticCacheRecompilesSystemOwnedFields=True,
        semanticCacheCrossProductReuseAllowed=False,
        requestCacheEnabled=False,
        itemResultCacheEnabled=True,
        cachedOutputRebindingAllowed=True,
        cachedOutputRebindingScope="familyPayload_only_then_system_recompile",
        fallbackAllowed=False,
    )
    return outputs, summary


run_agent2_projected_inputs = run_agent2_draft_projected_inputs


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_TOKEN_RUNTIME_VERSION",
    "AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION",
    "AGENT2_FAMILY_PAYLOAD_CACHE_VERSION",
    "AGENT2_SEMANTIC_IDENTITY_SCHEMA",
    "AGENT2_EXACT_OUTPUT_STAGE",
    "build_agent2_semantic_identity",
    "run_agent1_projected_inputs",
    "run_agent2_draft_projected_inputs",
    "run_agent2_projected_inputs",
    "run_agent3_sop_projected_inputs",
]
