"""V22.5.8 Agent1 token runtime with separated provider and normalization status."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from src.services import agent_token_runtime_v230_service as legacy
from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_SCHEMA,
    assert_agent_input_envelope,
    split_envelopes_by_budget,
)

AGENT_TOKEN_RUNTIME_VERSION = "22.5.8"


def _merge_policy(core: Any, product: Dict[str, Any]) -> Dict[str, Any]:
    base = core.build_agent1_rag_context()
    projected = legacy._dict(product.get("diagnosticRag"))
    merged = {**base, **projected}
    merged["version"] = AGENT_TOKEN_RUNTIME_VERSION
    merged["mode"] = "v22_5_8_lineage_trend_then_execution_lock"
    merged["principles"] = list(
        dict.fromkeys([*(base.get("principles") or []), *(projected.get("principles") or [])])
    )
    merged["guardrails"] = {
        **legacy._dict(base.get("guardrails")),
        **legacy._dict(projected.get("guardrails")),
        "sourceLineageSingleOwner": True,
        "crossValidationOwnsLineage": False,
        "decisionTypeExactEnum": ["observe", "act"],
        "knownDecisionAliasesNormalizedByRuntime": True,
        "unknownDecisionFailClosedToObserve": True,
        "actRequiresEvidenceSufficient": True,
        "onePrimaryProblemNode": True,
        "onePrimaryAction": True,
        "onePrimaryExecutionTarget": True,
        "onePrimaryOwner": True,
        "unresolvedDiagnosisBecomesObservation": True,
        "nativeObservationIsLegalTerminal": True,
        "diagnosisAuditOnlyDownstream": True,
        "semanticTrendContextRequired": True,
        "duplicateSignalEvidenceForbidden": True,
    }
    if base.get("experienceCards") and not merged.get("experienceCards"):
        merged["experienceCards"] = base.get("experienceCards")
    return merged


def _identity_values(value: Dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(value.get("correlationId") or "").strip(),
        str(value.get("storeId") or "").strip(),
        str(value.get("productId") or "").strip(),
        str(value.get("signalId") or "").strip(),
    )


def _raw_payload_has_product(payload: Dict[str, Any], product: Dict[str, Any]) -> bool:
    target = _identity_values(product)
    raw_items = payload.get("judgments") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return False
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        current = _identity_values(raw)
        if target[0] and current[0] == target[0]:
            return True
        if target[1] and target[2] and current[1:3] == target[1:3]:
            return True
        if target[3] and current[3] == target[3]:
            return True
    return False


def run_agent1_projected_inputs(
    envelopes: List[Dict[str, Any]],
    *,
    data_version: str | None,
    max_items_per_call: int = 8,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    from src.services.llm_gateway_v196_service import call_json
    from src.services import real_product_judgment_agent_v2258_service as core

    valid: List[Dict[str, Any]] = []
    for envelope in envelopes:
        assert_agent_input_envelope(envelope, expected_schema=AGENT1_INPUT_SCHEMA)
        valid.append(envelope)
    if not valid:
        return [], {
            "providerStatus": "no_provider_call",
            "normalizationStatus": "not_started",
            "completenessStatus": "no_projected_inputs",
            "actualCalls": 0,
            "hardInputContract": True,
            "version": AGENT_TOKEN_RUNTIME_VERSION,
        }

    judgments: List[Dict[str, Any]] = []
    diagnostics: List[Dict[str, Any]] = []
    provider_errors: List[str] = []
    normalization_errors: List[str] = []
    usages: List[Dict[str, Any]] = []
    incomplete_batches = 0
    retry_attempted_items = 0
    retry_skipped_contract_items = 0
    recovered_items = 0
    provider_success_batches = 0
    retry_limit = legacy._env_int("AGENT1_MISSING_ITEM_RETRY_ATTEMPTS", 2, 0, 4)

    batches = split_envelopes_by_budget(
        valid,
        expected_schema=AGENT1_INPUT_SCHEMA,
        max_items=max_items_per_call,
    )
    for batch_index, batch in enumerate(batches):
        products = [legacy._dict(item.get("payload")) for item in batch]
        policy = _merge_policy(core, products[0]) if products else core.build_agent1_rag_context()
        batch_judgments: List[Dict[str, Any]] = []
        raw_payload: Dict[str, Any] = {}
        try:
            messages, cache_payload = core._build_messages(data_version, products, policy)
            raw_payload, usage = call_json(
                stage="product_judgment_agent",
                prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
                messages=messages,
                temperature=0.08,
                timeout_seconds=int(os.getenv("PRODUCT_JUDGMENT_AGENT_TIMEOUT", "180")),
                cache_payload=cache_payload,
                cache_enabled=False,
            )
            provider_success_batches += 1
            usages.append(legacy._usage_record(legacy._dict(usage)))
            try:
                batch_items, batch_diag = core._normalize_judgments(
                    raw_payload,
                    core._source_maps(products),
                    data_version,
                )
                batch_judgments = legacy._merge_agent1_judgments(batch_judgments, batch_items)
                diagnostics.append(
                    {
                        **legacy._dict(batch_diag),
                        "batchIndex": batch_index,
                        "expectedProductCount": len(products),
                        "normalizedJudgmentCount": len(batch_judgments),
                        "completenessStatus": (
                            "complete"
                            if len(legacy._missing_agent1_products(products, batch_judgments)) == 0
                            else "partial"
                        ),
                        "providerCallStatus": "provider_succeeded",
                        "inputProjectionVersion": AGENT_TOKEN_RUNTIME_VERSION,
                    }
                )
            except Exception as exc:
                normalization_errors.append(f"batch_{batch_index}:{str(exc)[:500]}")
        except Exception as exc:
            provider_errors.append(f"batch_{batch_index}:{str(exc)[:500]}")

        missing = legacy._missing_agent1_products(products, batch_judgments)
        if missing:
            incomplete_batches += 1

        for product in missing:
            if _raw_payload_has_product(raw_payload, product):
                retry_skipped_contract_items += 1
                diagnostics.append(
                    {
                        "batchIndex": batch_index,
                        "retryMode": "skipped_raw_identity_present",
                        "productAliases": sorted(legacy._agent1_identity_aliases(product)),
                        "rawIdentityPresent": True,
                        "recovered": False,
                        "attempted": 0,
                        "normalizationStatus": "output_contract_invalid",
                        "inputProjectionVersion": AGENT_TOKEN_RUNTIME_VERSION,
                    }
                )
                continue

            product_recovered = False
            singleton_policy = _merge_policy(core, product)
            for attempt in range(1, retry_limit + 1):
                retry_attempted_items += 1
                try:
                    retry_messages, retry_cache_payload = core._build_messages(
                        data_version,
                        [product],
                        singleton_policy,
                    )
                    retry_payload, retry_usage = call_json(
                        stage="product_judgment_agent",
                        prompt_version=AGENT_TOKEN_RUNTIME_VERSION,
                        messages=retry_messages,
                        temperature=0.08,
                        timeout_seconds=int(os.getenv("PRODUCT_JUDGMENT_AGENT_TIMEOUT", "180")),
                        cache_payload=retry_cache_payload,
                        cache_enabled=False,
                    )
                    provider_success_batches += 1
                    usages.append(legacy._usage_record(legacy._dict(retry_usage), retry=True))
                    retry_items, retry_diag = core._normalize_judgments(
                        retry_payload,
                        core._source_maps([product]),
                        data_version,
                    )
                    matched_retry_items = [
                        item
                        for item in retry_items
                        if isinstance(item, dict)
                        and legacy._judgment_matches_product(item, product)
                    ]
                    batch_judgments = legacy._merge_agent1_judgments(
                        batch_judgments,
                        matched_retry_items,
                    )
                    diagnostics.append(
                        {
                            **legacy._dict(retry_diag),
                            "batchIndex": batch_index,
                            "retryMode": "singleton_true_missing_product",
                            "retryAttempt": attempt,
                            "productAliases": sorted(legacy._agent1_identity_aliases(product)),
                            "normalizedJudgmentCount": len(matched_retry_items),
                            "recovered": bool(matched_retry_items),
                            "providerCallStatus": "provider_succeeded",
                            "inputProjectionVersion": AGENT_TOKEN_RUNTIME_VERSION,
                        }
                    )
                    if matched_retry_items:
                        recovered_items += 1
                        product_recovered = True
                        break
                except Exception as exc:
                    provider_errors.append(
                        f"batch_{batch_index}_singleton_retry_{attempt}:{str(exc)[:420]}"
                    )
            if not product_recovered:
                diagnostics.append(
                    {
                        "batchIndex": batch_index,
                        "retryMode": "singleton_true_missing_product",
                        "productAliases": sorted(legacy._agent1_identity_aliases(product)),
                        "rawIdentityPresent": False,
                        "recovered": False,
                        "attempted": retry_limit,
                        "inputProjectionVersion": AGENT_TOKEN_RUNTIME_VERSION,
                    }
                )
        judgments = legacy._merge_agent1_judgments(judgments, batch_judgments)

    all_products = [legacy._dict(envelope.get("payload")) for envelope in valid]
    missing_count = len(legacy._missing_agent1_products(all_products, judgments))
    provider_status = (
        "provider_succeeded"
        if provider_success_batches and not provider_errors
        else "provider_partial"
        if provider_success_batches
        else "provider_failed"
    )
    normalization_status = (
        "normalized"
        if missing_count == 0 and not normalization_errors
        else "partial"
        if judgments
        else "failed"
    )
    completeness_status = (
        "complete"
        if missing_count == 0
        else "partial"
        if judgments
        else "incomplete"
    )

    summary = legacy._usage_summary(usages, stage="product_judgment_agent")
    summary.update(
        version=AGENT_TOKEN_RUNTIME_VERSION,
        providerStatus=provider_status,
        providerCallStatus=provider_status,
        normalizationStatus=normalization_status,
        completenessStatus=completeness_status,
        decisionContractStatus=(
            "valid" if missing_count == 0 else "partial_or_invalid"
        ),
        attemptedBatches=len(batches),
        providerSucceededBatchCount=provider_success_batches,
        incompleteBatchCount=incomplete_batches,
        retryAttemptedItemCount=retry_attempted_items,
        retrySkippedRawIdentityPresentCount=retry_skipped_contract_items,
        recoveredMissingProductCount=recovered_items,
        providerErrors=provider_errors,
        normalizationErrors=normalization_errors,
        errors=[*provider_errors, *normalization_errors],
        inputProductCount=len(valid),
        normalizedJudgmentCount=len(judgments),
        missingProductJudgmentCount=missing_count,
        batchDiagnostics=diagnostics,
        requestCacheEnabled=False,
        itemCacheEnabled=True,
        completenessContract="one_input_product_one_normalized_judgment",
        executionLockContract="one_problem_one_action_one_owner_one_target",
        semanticContinuityContract="source_lineage_plus_complete_signals_plus_key_trend_semantics",
        model=os.getenv("PRODUCT_JUDGMENT_AGENT_MODEL")
        or os.getenv("QWEN_MODEL")
        or "qwen3.7-plus",
        runtimeSource="agent1InputRef.v3",
    )
    return judgments, summary


__all__ = ["AGENT_TOKEN_RUNTIME_VERSION", "run_agent1_projected_inputs"]
