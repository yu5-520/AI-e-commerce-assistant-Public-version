"""V22.4.1 token runtime with item-complete Agent recovery.

The public functions accept only validated Agent input projection envelopes. They
perform deterministic budget splitting, cache/provider dispatch and usage audit;
they never resolve full business artifacts or decide which business fields belong
in a prompt.

V22.4.1 makes Agent1 completeness an item-level contract. A provider response may
succeed at HTTP/JSON level while omitting products from a batch. Whole-request
cache is therefore bypassed for Agent1, complete judgments are preserved, and only
missing products are retried as singleton calls before the runtime reports partial
output. This prevents one incomplete provider batch from poisoning request cache or
turning already-completed products into batch-wide failures.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Dict, List, Set, Tuple

from src.services.agent_input_contract_v230_service import (
    AGENT_INPUT_CONTRACT_VERSION,
    AGENT1_INPUT_SCHEMA,
    AGENT2_INPUT_SCHEMA,
    assert_agent_input_envelope,
    split_envelopes_by_budget,
)

AGENT_TOKEN_RUNTIME_VERSION = "22.4.1"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _usage_summary(items: List[Dict[str, Any]], *, stage: str) -> Dict[str, Any]:
    return {
        "version": AGENT_TOKEN_RUNTIME_VERSION,
        "stage": stage,
        "actualCalls": sum(int(item.get("actualCalls") or 0) for item in items),
        "idempotentReplays": sum(int(item.get("idempotentReplays") or 0) for item in items),
        "cacheHits": sum(int(item.get("cacheHits") or 0) for item in items),
        "inputTokens": sum(int(item.get("inputTokens") or item.get("input") or 0) for item in items),
        "outputTokens": sum(int(item.get("outputTokens") or item.get("output") or 0) for item in items),
        "reasoningTokens": sum(int(item.get("reasoningTokens") or 0) for item in items),
        "providerCallExecuted": any(bool(item.get("providerCallExecuted")) for item in items),
        "hardInputContract": True,
        "projectionVersion": AGENT_INPUT_CONTRACT_VERSION,
        "fallbackAllowed": False,
    }


def _agent1_identity_aliases(value: Dict[str, Any]) -> Set[str]:
    """Return stable aliases shared by projected inputs and normalized outputs."""

    identity = _dict(value.get("identity"))
    if not identity:
        identity = _dict(value.get("productIdentity"))

    correlation_id = _text(value.get("correlationId") or identity.get("correlationId"))
    signal_id = _text(value.get("signalId") or identity.get("signalId"))
    product_id = _text(
        value.get("productId")
        or value.get("erpProductCode")
        or identity.get("productId")
        or identity.get("erpProductCode")
    )
    store_id = _text(value.get("storeId") or identity.get("storeId"))

    aliases: Set[str] = set()
    if correlation_id:
        aliases.add("correlation:{}".format(correlation_id))
    if signal_id:
        aliases.add("signal:{}".format(signal_id))
    if product_id and store_id:
        aliases.add("store_product:{}:{}".format(store_id, product_id))
    elif product_id:
        aliases.add("product:{}".format(product_id))
    return aliases


def _judgment_matches_product(judgment: Dict[str, Any], product: Dict[str, Any]) -> bool:
    judgment_aliases = _agent1_identity_aliases(judgment)
    product_aliases = _agent1_identity_aliases(product)
    return bool(judgment_aliases and product_aliases and judgment_aliases.intersection(product_aliases))


def _merge_agent1_judgments(
    existing: List[Dict[str, Any]],
    incoming: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    result = list(existing)
    for candidate in incoming:
        if not isinstance(candidate, dict) or not candidate:
            continue
        aliases = _agent1_identity_aliases(candidate)
        duplicate = False
        for current in result:
            current_aliases = _agent1_identity_aliases(current)
            if aliases and current_aliases and aliases.intersection(current_aliases):
                duplicate = True
                break
        if not duplicate:
            result.append(candidate)
    return result


def _missing_agent1_products(
    products: List[Dict[str, Any]],
    judgments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    missing: List[Dict[str, Any]] = []
    for product in products:
        if not any(_judgment_matches_product(judgment, product) for judgment in judgments):
            missing.append(product)
    return missing


def _usage_record(usage: Dict[str, Any], *, retry: bool = False) -> Dict[str, Any]:
    return {
        **_dict(usage),
        "actualCalls": 0 if usage.get("cacheHit") else 1,
        "cacheHits": 1 if usage.get("cacheHit") else 0,
        "inputTokens": int(usage.get("input") or 0),
        "outputTokens": int(usage.get("output") or 0),
        "missingItemRetry": retry,
    }


def run_agent1_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 8,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from src.services.llm_gateway_v196_service import call_json
    from src.services import real_product_judgment_agent_v196_service as core

    valid = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT1_INPUT_SCHEMA)
        valid.append(envelope)
    if not valid:
        return [], {
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "hardInputContract": True,
            "version": AGENT_TOKEN_RUNTIME_VERSION,
        }

    judgments: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    errors: List[str] = []
    usages: List[Dict[str, Any]] = []
    incomplete_batches = 0
    retry_attempted_items = 0
    recovered_items = 0
    retry_limit = _env_int("AGENT1_MISSING_ITEM_RETRY_ATTEMPTS", 2, 0, 4)

    batches = split_envelopes_by_budget(
        valid,
        expected_schema=AGENT1_INPUT_SCHEMA,
        max_items=max_items_per_call,
    )
    for batch_index, batch in enumerate(batches):
        products = [_dict(item.get("payload")) for item in batch]
        policy = _dict(products[0].get("diagnosticRag")) if products else {}
        batch_judgments: List[Dict[str, Any]] = []
        try:
            messages, cache_payload = core._build_messages(data_version, products, policy)
            payload, usage = call_json(
                stage="product_judgment_agent",
                prompt_version=AGENT_INPUT_CONTRACT_VERSION,
                messages=messages,
                temperature=0.08,
                timeout_seconds=int(os.getenv("PRODUCT_JUDGMENT_AGENT_TIMEOUT", "180")),
                cache_payload=cache_payload,
                # Agent1 request-level cache must never replay a historically partial
                # batch. Item cache remains active inside the gateway and can replay
                # only outputs that were actually matched to individual products.
                cache_enabled=False,
            )
            batch_items, batch_diag = core._normalize_judgments(
                payload,
                core._source_maps(products),
                data_version,
            )
            batch_judgments = _merge_agent1_judgments(batch_judgments, batch_items)
            diagnostics.append(
                {
                    **_dict(batch_diag),
                    "batchIndex": batch_index,
                    "expectedProductCount": len(products),
                    "normalizedJudgmentCount": len(batch_judgments),
                    "completenessStatus": (
                        "complete" if len(_missing_agent1_products(products, batch_judgments)) == 0 else "partial"
                    ),
                }
            )
            usages.append(_usage_record(_dict(usage)))
        except Exception as exc:
            errors.append("batch_{}:{}".format(batch_index, str(exc)[:500]))

        missing = _missing_agent1_products(products, batch_judgments)
        if missing:
            incomplete_batches += 1

        # Retry only products omitted from the provider response. Singleton calls are
        # identity-safe and prevent already-completed products from paying again.
        for product in missing:
            product_recovered = False
            for attempt in range(1, retry_limit + 1):
                retry_attempted_items += 1
                try:
                    retry_messages, retry_cache_payload = core._build_messages(
                        data_version,
                        [product],
                        policy,
                    )
                    retry_payload, retry_usage = call_json(
                        stage="product_judgment_agent",
                        prompt_version=AGENT_INPUT_CONTRACT_VERSION,
                        messages=retry_messages,
                        temperature=0.08,
                        timeout_seconds=int(os.getenv("PRODUCT_JUDGMENT_AGENT_TIMEOUT", "180")),
                        cache_payload=retry_cache_payload,
                        cache_enabled=False,
                    )
                    retry_items, retry_diag = core._normalize_judgments(
                        retry_payload,
                        core._source_maps([product]),
                        data_version,
                    )
                    matched_retry_items = [
                        item
                        for item in retry_items
                        if isinstance(item, dict) and _judgment_matches_product(item, product)
                    ]
                    batch_judgments = _merge_agent1_judgments(batch_judgments, matched_retry_items)
                    usages.append(_usage_record(_dict(retry_usage), retry=True))
                    diagnostics.append(
                        {
                            **_dict(retry_diag),
                            "batchIndex": batch_index,
                            "retryMode": "singleton_missing_product",
                            "retryAttempt": attempt,
                            "productAliases": sorted(_agent1_identity_aliases(product)),
                            "normalizedJudgmentCount": len(matched_retry_items),
                            "recovered": bool(matched_retry_items),
                        }
                    )
                    if matched_retry_items:
                        recovered_items += 1
                        product_recovered = True
                        break
                except Exception as exc:
                    errors.append(
                        "batch_{}_singleton_retry_{}:{}".format(
                            batch_index,
                            attempt,
                            str(exc)[:420],
                        )
                    )
            if not product_recovered:
                diagnostics.append(
                    {
                        "batchIndex": batch_index,
                        "retryMode": "singleton_missing_product",
                        "productAliases": sorted(_agent1_identity_aliases(product)),
                        "recovered": False,
                        "attempted": retry_limit,
                    }
                )

        judgments = _merge_agent1_judgments(judgments, batch_judgments)

    missing_count = len(_missing_agent1_products(
        [_dict(envelope.get("payload")) for envelope in valid],
        judgments,
    ))
    status = "ok" if missing_count == 0 else "partial" if judgments else "failed"
    summary = _usage_summary(usages, stage="product_judgment_agent")
    summary.update(
        providerStatus=status,
        attemptedBatches=len(batches),
        incompleteBatchCount=incomplete_batches,
        retryAttemptedItemCount=retry_attempted_items,
        recoveredMissingProductCount=recovered_items,
        recoveredWithErrors=bool(missing_count == 0 and errors),
        errors=errors,
        inputProductCount=len(valid),
        normalizedJudgmentCount=len(judgments),
        missingProductJudgmentCount=missing_count,
        batchDiagnostics=diagnostics,
        requestCacheEnabled=False,
        itemCacheEnabled=True,
        completenessContract="one_input_product_one_normalized_judgment",
        model=os.getenv("PRODUCT_JUDGMENT_AGENT_MODEL") or os.getenv("QWEN_MODEL") or "qwen3.7-plus",
        runtimeSource="agent1InputRef",
    )
    return judgments, summary


def run_agent2_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 5,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    from src.services import agent2_action_plan_core_v20_service as core
    from src.services.agent2_provenance_v2141_service import (
        call_json_with_item_provenance,
        proof_for_package,
        provider_summary,
    )

    valid = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT2_INPUT_SCHEMA)
        valid.append(envelope)
    if not valid:
        return {}, {
            "providerStatus": "no_projected_inputs",
            "actualCalls": 0,
            "itemProvenance": {},
            "hardInputContract": True,
            "version": AGENT_TOKEN_RUNTIME_VERSION,
        }

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for envelope in valid:
        package = _dict(envelope.get("payload"))
        family = _text(package.get("lockedActionFamily") or package.get("actionFamily"))
        if not family:
            raise ValueError("agent2_projected_input_family_missing")
        grouped[family].append(envelope)

    plans: Dict[str, Dict[str, Any]] = {}
    provider_summaries: List[Dict[str, Any]] = []
    raw_usages: List[Dict[str, Any]] = []
    errors: List[str] = []
    for family in sorted(grouped):
        batches = split_envelopes_by_budget(
            grouped[family],
            expected_schema=AGENT2_INPUT_SCHEMA,
            max_items=max_items_per_call,
        )
        for batch in batches:
            packages = [_dict(item.get("payload")) for item in batch]
            by_id = {str(item.get("packageId") or item.get("itemId")): item for item in packages}
            try:
                messages, cache_payload = core._build_messages(data_version, packages)
                payload, usage = call_json_with_item_provenance(
                    stage="action_plan_judgment_agent",
                    prompt_version=AGENT_INPUT_CONTRACT_VERSION,
                    messages=messages,
                    temperature=0.12,
                    timeout_seconds=int(os.getenv("ACTION_PLAN_AGENT_TIMEOUT", "240")),
                    cache_payload=cache_payload,
                    cache_enabled=True,
                )
                summary = provider_summary(usage)
                summary["actionFamily"] = family
                provider_summaries.append(summary)
                raw_usages.append(
                    {
                        **_dict(usage),
                        "actualCalls": int(summary.get("actualCalls") or 0),
                        "idempotentReplays": int(summary.get("idempotentReplays") or 0),
                        "cacheHits": int(summary.get("cacheHits") or 0),
                        "inputTokens": int(summary.get("inputTokens") or 0),
                        "outputTokens": int(summary.get("outputTokens") or 0),
                    }
                )
                raw_plans = payload.get("plans") if isinstance(payload, dict) else None
                if not isinstance(raw_plans, list):
                    raise ValueError("agent2_json_missing_plans_array")
                for raw in raw_plans:
                    if not isinstance(raw, dict):
                        continue
                    package_id = _text(raw.get("packageId"))
                    package = by_id.get(package_id)
                    proof = proof_for_package(summary, package_id)
                    if package and proof:
                        plans[package_id] = core._normalize_plan(raw, package, proof)
            except Exception as exc:
                errors.append(f"{family}:{str(exc)[:450]}")

    all_proofs: Dict[str, Dict[str, Any]] = {}
    for summary in provider_summaries:
        all_proofs.update(_dict(summary.get("itemProvenance")))
    usage_summary = _usage_summary(raw_usages, stage="action_plan_judgment_agent")
    usage_summary.update(
        providerStatus="ok" if plans and not errors else "partial" if plans else "failed",
        itemProvenance=all_proofs,
        errors=errors,
        groupedActionFamilies=sorted(grouped),
        familyCallCount=len(provider_summaries),
        runtimeSource="agent2InputRef",
    )
    return plans, usage_summary


__all__ = [
    "AGENT_TOKEN_RUNTIME_VERSION",
    "run_agent1_projected_inputs",
    "run_agent2_projected_inputs",
]
