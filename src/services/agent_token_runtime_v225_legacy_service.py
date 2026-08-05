"""V22.5 three-Agent token runtime.

Agent1 keeps the item-complete V22.4.1 runtime. Agent2 generates structured drafts.
Agent3 runs only for validated drafts and generates company-aware SOPs.

V22.5.3 disables Agent2 request-level business-result replay. Exact per-item semantic
cache remains enabled and always rebinds the current package/product/store identity.
"""
from __future__ import annotations

import hashlib
import os
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from src.services.agent_input_contract_v225_service import (
    AGENT_INPUT_CONTRACT_VERSION,
    AGENT2_DRAFT_INPUT_SCHEMA,
    AGENT3_SOP_INPUT_SCHEMA,
    assert_agent_input_envelope,
    split_envelopes_by_budget,
)

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
AGENT_TOKEN_RUNTIME_VERSION = THREE_AGENT_PIPELINE_VERSION
AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION = "22.5.3"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _agent2_cache_policy() -> Dict[str, Any]:
    return {
        "requestCacheEnabled": False,
        "itemResultCacheEnabled": True,
        "requestCacheIdentityHotfixVersion": (
            AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION
        ),
        "cacheRule": (
            "Agent2 request-level business payload replay is disabled; only exact "
            "per-item semantic replay may be reused and every reused output is rebound "
            "to the current packageId/productId/storeId/actionFamily."
        ),
    }


def run_agent1_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 8,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from src.services.agent_token_runtime_v230_service import (
        run_agent1_projected_inputs as legacy,
    )

    return legacy(
        envelopes,
        data_version=data_version,
        max_items_per_call=max_items_per_call,
    )


def run_agent2_draft_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 5,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    from src.services import agent2_action_draft_core_v225_service as core
    from src.services.agent2_provenance_v2141_service import (
        call_json_with_item_provenance,
        proof_for_package,
        provider_summary,
    )

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
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "itemProvenance": {},
            "runtimeSource": "agent2DraftInputRef",
            **_agent2_cache_policy(),
            "fallbackAllowed": False,
        }

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for envelope in valid:
        package = _dict(envelope.get("payload"))
        family = core.selected_family(package)
        if not family:
            raise ValueError("agent2_draft_projected_input_family_missing")
        grouped[family].append(envelope)

    drafts: Dict[str, Dict[str, Any]] = {}
    provider_summaries: List[Dict[str, Any]] = []
    errors: List[str] = []
    input_tokens = output_tokens = reasoning_tokens = actual_calls = cache_hits = 0

    for family in sorted(grouped):
        batches = split_envelopes_by_budget(
            grouped[family],
            expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
            max_items=max_items_per_call,
        )
        for batch in batches:
            packages = [_dict(item.get("payload")) for item in batch]
            by_id = {
                str(item.get("packageId") or item.get("itemId")): item
                for item in packages
            }
            try:
                messages, cache_payload = core._build_messages(data_version, packages)
                payload, usage = call_json_with_item_provenance(
                    stage="action_plan_judgment_agent",
                    prompt_version=AGENT_INPUT_CONTRACT_VERSION,
                    messages=messages,
                    temperature=0.16,
                    timeout_seconds=int(
                        os.getenv("ACTION_DRAFT_AGENT_TIMEOUT", "240")
                    ),
                    cache_payload=cache_payload,
                    # Request-level replay can contain an old packageId because the
                    # semantic request key deliberately strips runtime identities.
                    # Keep item cache enabled inside the gateway, but never short-circuit
                    # Agent2 with a stale request payload.
                    cache_enabled=False,
                )
                summary = provider_summary(usage)
                summary.update(
                    actionFamily=family,
                    semanticRole="agent2_action_draft",
                    **_agent2_cache_policy(),
                )
                provider_summaries.append(summary)
                actual_calls += int(summary.get("actualCalls") or 0)
                cache_hits += int(summary.get("cacheHits") or 0)
                input_tokens += int(summary.get("inputTokens") or 0)
                output_tokens += int(summary.get("outputTokens") or 0)
                reasoning_tokens += int(summary.get("reasoningTokens") or 0)
                raw_plans = payload.get("plans") if isinstance(payload, dict) else None
                if not isinstance(raw_plans, list):
                    raise ValueError("agent2_draft_json_missing_plans_array")
                for raw in raw_plans:
                    if not isinstance(raw, dict):
                        continue
                    package_id = _text(raw.get("packageId"))
                    package = by_id.get(package_id)
                    proof = proof_for_package(summary, package_id)
                    if package and proof:
                        drafts[package_id] = core._normalize_draft(
                            raw,
                            package,
                            proof,
                        )
            except Exception as exc:
                errors.append(f"{family}:{str(exc)[:450]}")

    all_proofs: Dict[str, Dict[str, Any]] = {}
    for summary in provider_summaries:
        all_proofs.update(_dict(summary.get("itemProvenance")))
    return drafts, {
        "version": AGENT_TOKEN_RUNTIME_VERSION,
        "stage": "agent2_action_draft",
        "providerStatus": (
            "ok"
            if drafts and not errors
            else "partial"
            if drafts
            else "failed"
        ),
        "actualCalls": actual_calls,
        "cacheHits": cache_hits,
        "inputTokens": input_tokens,
        "outputTokens": output_tokens,
        "reasoningTokens": reasoning_tokens,
        "itemProvenance": all_proofs,
        "providerCalls": provider_summaries,
        "errors": errors,
        "groupedActionFamilies": sorted(grouped),
        "draftCount": len(drafts),
        "runtimeSource": "agent2DraftInputRef",
        **_agent2_cache_policy(),
        "hardInputContract": True,
        "fallbackAllowed": False,
    }


def _agent3_proof(
    *,
    usage: Dict[str, Any],
    package_id: str,
    result_matched: bool,
) -> Dict[str, Any]:
    provider_request_id = _text(usage.get("providerRequestId"))
    input_fingerprint = _text(usage.get("inputFingerprint"))
    raw = "|".join(
        [
            "agent3_sop_agent",
            AGENT_INPUT_CONTRACT_VERSION,
            input_fingerprint,
            provider_request_id or "provider_call",
            package_id,
        ]
    )
    semantic_call_id = (
        "A3CALL-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20].upper()
    )
    provider_call = bool(usage.get("providerCallExecuted"))
    proof = {
        "version": THREE_AGENT_PIPELINE_VERSION,
        "stage": "agent3_sop_agent",
        "packageId": package_id,
        "semanticCallId": semantic_call_id,
        "provider": usage.get("provider"),
        "model": usage.get("model"),
        "providerRequestId": provider_request_id if provider_call else None,
        "providerCallExecuted": provider_call,
        "exactReplayValidated": False,
        "itemCorrelationId": package_id,
        "resultMatched": result_matched,
        "resultOrigin": "provider_call",
        "inputFingerprint": input_fingerprint,
        "promptVersion": AGENT_INPUT_CONTRACT_VERSION,
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


def run_agent3_sop_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 1,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    from src.services import agent3_sop_core_v225_service as core
    from src.services.llm_gateway_v196_service import call_json

    del max_items_per_call
    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(
            envelope,
            expected_schema=AGENT3_SOP_INPUT_SCHEMA,
        )
        valid.append(envelope)
    if not valid:
        return {}, {
            "version": AGENT_TOKEN_RUNTIME_VERSION,
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "itemProvenance": {},
            "runtimeSource": "agent3SopInputRef",
            "fallbackAllowed": False,
        }

    sops: Dict[str, Dict[str, Any]] = {}
    proofs: Dict[str, Dict[str, Any]] = {}
    calls: List[Dict[str, Any]] = []
    errors: List[str] = []

    for envelope in valid:
        package = _dict(envelope.get("payload"))
        package_id = _text(package.get("packageId") or package.get("itemId"))
        try:
            messages, cache_payload = core._build_messages(data_version, [package])
            payload, usage = call_json(
                stage="task_mapping_agent",
                prompt_version=AGENT_INPUT_CONTRACT_VERSION,
                messages=messages,
                temperature=0.2,
                timeout_seconds=int(os.getenv("AGENT3_SOP_TIMEOUT", "300")),
                cache_payload=cache_payload,
                cache_enabled=False,
            )
            raw_sops = payload.get("sops") if isinstance(payload, dict) else None
            if not isinstance(raw_sops, list):
                raise ValueError("agent3_json_missing_sops_array")
            raw = next(
                (
                    item
                    for item in raw_sops
                    if isinstance(item, dict)
                    and _text(item.get("packageId")) == package_id
                ),
                None,
            )
            proof = _agent3_proof(
                usage=_dict(usage),
                package_id=package_id,
                result_matched=isinstance(raw, dict),
            )
            proofs[package_id] = proof
            calls.append(
                {
                    "packageId": package_id,
                    "provider": usage.get("provider"),
                    "model": usage.get("model"),
                    "providerRequestId": usage.get("providerRequestId"),
                    "actualCalls": 1 if usage.get("providerCallExecuted") else 0,
                    "inputTokens": int(usage.get("input") or 0),
                    "outputTokens": int(usage.get("output") or 0),
                    "reasoningTokens": int(usage.get("reasoningTokens") or 0),
                    "proofPassed": proof.get("passed"),
                }
            )
            if isinstance(raw, dict) and proof.get("passed"):
                sops[package_id] = core._normalize_sop(raw, package, proof)
            elif not isinstance(raw, dict):
                errors.append(f"{package_id}:agent3_response_package_unmatched")
            else:
                errors.append(f"{package_id}:agent3_provider_proof_invalid")
        except Exception as exc:
            errors.append(f"{package_id}:{str(exc)[:450]}")

    return sops, {
        "version": AGENT_TOKEN_RUNTIME_VERSION,
        "stage": "agent3_sop_agent",
        "providerStatus": (
            "ok"
            if len(sops) == len(valid) and not errors
            else "partial"
            if sops
            else "failed"
        ),
        "actualCalls": sum(int(item.get("actualCalls") or 0) for item in calls),
        "inputTokens": sum(int(item.get("inputTokens") or 0) for item in calls),
        "outputTokens": sum(int(item.get("outputTokens") or 0) for item in calls),
        "reasoningTokens": sum(
            int(item.get("reasoningTokens") or 0) for item in calls
        ),
        "itemProvenance": proofs,
        "providerCalls": calls,
        "errors": errors,
        "sopCount": len(sops),
        "runtimeSource": "agent3SopInputRef",
        "requestCacheEnabled": False,
        "hardInputContract": True,
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
