"""V22.5.20 exact Agent2 output runtime.

Agent1 remains on the strict V22.5.9 runtime.  Agent2 now owns the same immutable
execution identity: every plan must return ``itemExecutionId`` and
``inputContentHash`` and every provider batch is stored before normalization.
Business ``packageId`` is never used as the acceptance authority.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from src.services import agent_token_runtime_v230_service as runtime_helpers
from src.services.agent2_action_draft_core_v225_service import (
    _build_messages,
    _normalize_draft,
    selected_family,
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
from src.services.llm_gateway_hash_directed_v2259_service import (
    call_json_exact_artifact,
)
from src.services.llm_gateway_v196_service import provider_runtime_config

THREE_AGENT_PIPELINE_VERSION = "22.5.20"
AGENT_TOKEN_RUNTIME_VERSION = "22.5.20"
AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION = "22.5.20"
AGENT2_EXACT_OUTPUT_STAGE = "action_plan_judgment_agent"
AGENT2_EXACT_OUTPUT_TYPE = "agent2_model_output.v2259"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _package_id(envelope: Dict[str, Any]) -> str:
    payload = _dict(envelope.get("payload"))
    return _text(payload.get("packageId") or payload.get("itemId"), 220)


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
        hashIdentityMatched=True,
        fallbackIdentityMatchingUsed=False,
        cachedOutputRebound=False,
        hashDirectedRuntimeVersion=AGENT_TOKEN_RUNTIME_VERSION,
    )
    refs = dict(_dict(result.get("artifactRefs")))
    refs["agentExecutionInputRef"] = descriptor.get("inputArtifactRef")
    refs["agentExecutionOutputRef"] = output_ref
    if raw_ref:
        refs["agentRawBatchOutputRef"] = raw_ref
    result["artifactRefs"] = refs
    return result


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
    truly missing item.  It is never sufficient for acceptance.
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
            "runtimeSource": "agent2DraftInputArtifact.exactHash.v22520",
            "fallbackAllowed": False,
        }

    provider = provider_runtime_config(AGENT2_EXACT_OUTPUT_STAGE)
    outputs: Dict[str, Dict[str, Any]] = {}
    claimed: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    usages: List[Dict[str, Any]] = []
    errors: List[str] = []
    replay_count = busy_count = contract_invalid_count = true_missing_count = 0

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
            claimed.append(entry)
        except Exception as exc:
            errors.append(f"prepare:{_text(exc, 500)}")

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for entry in claimed:
        grouped[str(entry["descriptor"].get("actionFamily") or "")].append(entry)

    true_missing: List[Dict[str, Any]] = []
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
    summary.update(
        version=AGENT_TOKEN_RUNTIME_VERSION,
        providerStatus=(
            "ok"
            if len(outputs) == len(valid) and not errors
            else "partial"
            if outputs
            else "failed"
        ),
        draftCount=len(outputs),
        inputCount=len(valid),
        exactExecutionReplayCount=replay_count,
        alreadyRunningCount=busy_count,
        exactContractInvalidCount=contract_invalid_count,
        trueMissingCount=true_missing_count,
        singletonRetryCount=singleton_retry_count,
        batchDiagnostics=diagnostics,
        errors=errors,
        itemProvenance={},
        runtimeSource="agent2DraftInputArtifact.exactHash.v22520",
        hashDirectedExecution=True,
        rawBatchArtifactStored=True,
        acceptanceIdentity="itemExecutionId+inputContentHash",
        packageIdAcceptanceAllowed=False,
        requestCacheEnabled=False,
        itemResultCacheEnabled=False,
        fallbackAllowed=False,
    )
    return outputs, summary


run_agent2_projected_inputs = run_agent2_draft_projected_inputs


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_TOKEN_RUNTIME_VERSION",
    "AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION",
    "AGENT2_EXACT_OUTPUT_STAGE",
    "run_agent1_projected_inputs",
    "run_agent2_draft_projected_inputs",
    "run_agent2_projected_inputs",
    "run_agent3_sop_projected_inputs",
]
