"""V21.4.1 item-level Agent2 provider and exact-replay provenance.

The LLM gateway supports exact per-item semantic replay. This module observes
the same cache identity before the call and binds a proof to every package.
Downstream gates never accept batch-level counters as item execution proof.
"""

from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Tuple

from src.services.llm_gateway_v196_service import (
    _item_cache_key,
    _provider_model,
    _provider_name,
    _read_item_cache,
    call_json,
)
from src.services.llm_input_projection_v211_service import (
    output_identity,
    parse_projected_dynamic_payload,
    prepare_llm_request,
    stage_collection,
)

AGENT2_PROVENANCE_VERSION = "21.4.1"
AGENT2_STAGE = "action_plan_judgment_agent"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _identity_key(stage: str, item: Dict[str, Any]) -> str:
    identity = output_identity(stage, item)
    return str(
        identity.get("packageId")
        or identity.get("correlationId")
        or identity.get("productId")
        or ""
    )


def _output_keys(stage: str, payload: Dict[str, Any]) -> set[str]:
    _, output_key, _ = stage_collection(stage, {})
    outputs = payload.get(str(output_key)) if output_key else None
    if not isinstance(outputs, list):
        outputs = (
            payload.get("plans")
            if stage == AGENT2_STAGE
            else payload.get("judgments")
            if stage == "product_judgment_agent"
            else []
        )
    result: set[str] = set()
    for output in _arr(outputs):
        if not isinstance(output, dict):
            continue
        key = str(
            output.get("packageId")
            or output.get("correlationId")
            or output.get("productId")
            or ""
        )
        if key:
            result.add(key)
    return result


def _semantic_call_id(
    *,
    stage: str,
    prompt_version: str,
    input_fingerprint: str,
    provider_request_id: str,
) -> str:
    raw = "|".join(
        [
            stage,
            prompt_version,
            input_fingerprint,
            provider_request_id or "exact_replay",
        ]
    )
    return "A2CALL-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20].upper()


def _preflight_item_cache(
    *,
    stage: str,
    prompt_version: str,
    messages: List[Dict[str, str]],
    cache_payload: Any,
    model: str | None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    projected_messages, _, projection = prepare_llm_request(
        stage,
        messages,
        cache_payload,
    )
    dynamic_payload = parse_projected_dynamic_payload(projected_messages)
    _, _, input_items = stage_collection(stage, dynamic_payload)
    provider = _provider_name(stage)
    provider_model = _provider_model(stage, model)
    stable_context_hash = str(projection.get("stableContextHash") or "")

    descriptors: Dict[str, Dict[str, Any]] = {}
    for position, item in enumerate(input_items):
        key = _identity_key(stage, item)
        cache_key, fingerprint = _item_cache_key(
            stage=stage,
            provider=provider,
            model=provider_model,
            prompt_version=prompt_version,
            stable_context_hash=stable_context_hash,
            item=item,
        )
        descriptors[key] = {
            "position": position,
            "cacheKey": cache_key,
            "itemFingerprint": fingerprint,
            "preCallCacheHit": _read_item_cache(cache_key) is not None,
        }
    return input_items, descriptors, projection


def call_json_with_item_provenance(
    *,
    stage: str,
    prompt_version: str,
    messages: List[Dict[str, str]],
    temperature: float = 0.1,
    timeout_seconds: int = 120,
    model: str | None = None,
    cache_enabled: bool = True,
    cache_payload: Any | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    input_items, descriptors, projection = _preflight_item_cache(
        stage=stage,
        prompt_version=prompt_version,
        messages=messages,
        cache_payload=cache_payload,
        model=model,
    )
    payload, usage = call_json(
        stage=stage,
        prompt_version=prompt_version,
        messages=messages,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        model=model,
        cache_enabled=cache_enabled,
        cache_payload=cache_payload,
    )
    usage = dict(_dict(usage))
    provider_request_id = str(usage.get("providerRequestId") or "")
    input_fingerprint = str(usage.get("inputFingerprint") or "")
    semantic_call_id = _semantic_call_id(
        stage=stage,
        prompt_version=prompt_version,
        input_fingerprint=input_fingerprint,
        provider_request_id=provider_request_id,
    )
    output_keys = _output_keys(stage, _dict(payload))
    batch_replay = bool(usage.get("idempotentReplay")) and not bool(
        usage.get("providerCallExecuted")
    )

    item_provenance: Dict[str, Dict[str, Any]] = {}
    for item in input_items:
        key = _identity_key(stage, item)
        descriptor = descriptors.get(key) or {}
        exact_replay = bool(descriptor.get("preCallCacheHit") or batch_replay)
        provider_call = bool(usage.get("providerCallExecuted")) and not exact_replay
        proof = {
            "version": AGENT2_PROVENANCE_VERSION,
            "stage": stage,
            "packageId": key if stage == AGENT2_STAGE else None,
            "semanticCallId": semantic_call_id,
            "provider": usage.get("provider"),
            "model": usage.get("model"),
            "providerRequestId": provider_request_id if provider_call else None,
            "providerCallExecuted": provider_call,
            "exactReplayValidated": exact_replay,
            "replayFingerprint": (
                descriptor.get("itemFingerprint") if exact_replay else None
            ),
            "cacheKey": descriptor.get("cacheKey") if exact_replay else None,
            "itemPosition": descriptor.get("position"),
            "itemCorrelationId": key,
            "resultMatched": key in output_keys,
            "resultOrigin": (
                "exact_semantic_replay" if exact_replay else "provider_call"
            ),
            "inputFingerprint": input_fingerprint,
            "promptVersion": prompt_version,
            "projectionVersion": usage.get("projectionVersion"),
            "gatewayVersion": usage.get("gatewayVersion"),
            "fallbackUsed": False,
        }
        proof["passed"] = valid_agent2_execution_proof(proof)
        item_provenance[key] = proof

    usage.update(
        {
            "provenanceVersion": AGENT2_PROVENANCE_VERSION,
            "semanticCallId": semantic_call_id,
            "itemProvenance": item_provenance,
            "projection": usage.get("projection") or projection,
        }
    )
    return payload, usage


def valid_agent2_execution_proof(proof: Dict[str, Any] | None) -> bool:
    proof = _dict(proof)
    if not proof or proof.get("fallbackUsed") is True:
        return False
    if proof.get("resultMatched") is not True:
        return False
    if not str(proof.get("itemCorrelationId") or "").strip():
        return False
    if not str(proof.get("semanticCallId") or "").strip():
        return False

    provider_call = proof.get("providerCallExecuted") is True
    exact_replay = proof.get("exactReplayValidated") is True
    if provider_call == exact_replay:
        return False
    if provider_call and not str(proof.get("providerRequestId") or "").strip():
        return False
    if exact_replay and not str(proof.get("replayFingerprint") or "").strip():
        return False
    return True


def proof_for_package(
    provider: Dict[str, Any] | None,
    package_id: str | None,
) -> Dict[str, Any]:
    provider = _dict(provider)
    proofs = _dict(provider.get("itemProvenance") or provider.get("itemProofs"))
    return _dict(proofs.get(str(package_id or "")))


def provider_has_valid_agent2_proof(
    provider: Dict[str, Any] | None,
    package_id: str | None = None,
    proof: Dict[str, Any] | None = None,
) -> bool:
    selected = _dict(proof) or proof_for_package(_dict(provider), package_id)
    return valid_agent2_execution_proof(selected)


def agent2_proof_missing_reason(
    provider: Dict[str, Any] | None,
    package_id: str | None = None,
    proof: Dict[str, Any] | None = None,
) -> str | None:
    provider = _dict(provider)
    selected = _dict(proof) or proof_for_package(provider, package_id)
    if selected:
        if selected.get("resultMatched") is not True:
            return "agent2_response_product_unmatched"
        if selected.get("fallbackUsed") is True:
            return "agent2_backend_fallback_not_allowed"
        if not selected.get("itemCorrelationId"):
            return "agent2_item_correlation_id_missing"
        if not selected.get("semanticCallId"):
            return "agent2_semantic_call_id_missing"
        if (
            selected.get("providerCallExecuted") is True
            and selected.get("exactReplayValidated") is True
        ):
            return "agent2_proof_origin_conflict"
        if (
            selected.get("providerCallExecuted") is not True
            and selected.get("exactReplayValidated") is not True
        ):
            return "agent2_not_dispatched_or_replay_missing"
        if (
            selected.get("providerCallExecuted") is True
            and not selected.get("providerRequestId")
        ):
            return "agent2_provider_request_id_missing"
        if (
            selected.get("exactReplayValidated") is True
            and not selected.get("replayFingerprint")
        ):
            return "agent2_exact_replay_fingerprint_invalid"
        return (
            None
            if valid_agent2_execution_proof(selected)
            else "agent2_execution_proof_invalid"
        )

    if provider.get("fallbackUsed") or provider.get("backendFallbackUsed"):
        return "agent2_backend_fallback_not_allowed"
    if (
        int(provider.get("actualCalls") or 0) > 0
        or int(provider.get("idempotentReplays") or 0) > 0
    ):
        return "agent2_item_provenance_missing"
    return "agent2_provider_call_or_exact_replay_missing"


def provider_summary(
    usage: Dict[str, Any],
    *,
    errors: List[str] | None = None,
) -> Dict[str, Any]:
    proofs = _dict(usage.get("itemProvenance"))
    passed = sum(
        1
        for proof in proofs.values()
        if valid_agent2_execution_proof(_dict(proof))
    )
    return {
        "providerStatus": (
            "ok"
            if proofs and passed == len(proofs) and not errors
            else "partial"
            if passed
            else "failed"
        ),
        "provider": usage.get("provider"),
        "model": usage.get("model"),
        "actualCalls": 1 if usage.get("providerCallExecuted") else 0,
        "idempotentReplays": (
            1
            if usage.get("idempotentReplay")
            and not usage.get("providerCallExecuted")
            else 0
        ),
        "cacheHits": int(usage.get("itemCacheHits") or 0),
        "itemCacheHits": int(usage.get("itemCacheHits") or 0),
        "itemCacheMisses": int(usage.get("itemCacheMisses") or 0),
        "inputTokens": int(usage.get("input") or 0),
        "outputTokens": int(usage.get("output") or 0),
        "reasoningTokens": int(usage.get("reasoningTokens") or 0),
        "providerCacheHitInputTokens": int(
            usage.get("providerCacheHitInput") or 0
        ),
        "providerCacheMissInputTokens": int(
            usage.get("providerCacheMissInput") or 0
        ),
        "providerRequestId": usage.get("providerRequestId"),
        "semanticCallId": usage.get("semanticCallId"),
        "itemProvenance": proofs,
        "provenanceVersion": AGENT2_PROVENANCE_VERSION,
        "passedItemCount": passed,
        "failedItemCount": max(0, len(proofs) - passed),
        "errors": errors or [],
        "fallbackUsed": False,
        "fallbackAllowed": False,
        "cacheAllowed": "validated_exact_semantic_replay_only",
        "batchCountersAcceptedAsItemProof": False,
    }
