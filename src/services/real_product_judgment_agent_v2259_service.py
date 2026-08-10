"""V22.5.10 Agent1 prompt and exact batch-item output contract.

The hash-directed runtime owns Agent1 execution identity. Once
``itemExecutionId + inputContentHash`` matches, business normalization must preserve
that exact item and may not re-run legacy identity matching or delete the judgment
because an older hard-coded action-family whitelist does not know a newer capability.

Legacy V22.5.8 is retained only for pure decision-type canonicalization; V22.5.5 is
retained only for execution-lock shaping. Neither layer may re-own execution identity.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

from src.services import real_product_judgment_agent_v2258_service as legacy
from src.services import real_product_judgment_agent_v2255_service as execution_contract

REAL_PRODUCT_AGENT_V2259_VERSION = "22.5.10"
PRODUCT_AGENT_MODE = "hash_directed_exact_input_once_decision"
NORMALIZATION_AUTHORITY = "itemExecutionId+inputContentHash"
LEGACY_IDENTITY_REMATCH_ALLOWED = False
LEGACY_ACTION_FAMILY_WHITELIST_ALLOWED = False


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except Exception:
        number = 0.0
    return round(max(0.0, min(0.99, number)), 4)


def _execution(product: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(product.get("_hashExecution"))


def _business_identity(product: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "correlationId": _text(product.get("correlationId"), 240),
        "productId": _text(product.get("productId"), 180),
        "storeId": _text(product.get("storeId"), 180),
        "signalId": _text(product.get("signalId"), 240),
    }


def build_agent1_rag_context() -> Dict[str, Any]:
    context = legacy.build_agent1_rag_context()
    context["version"] = REAL_PRODUCT_AGENT_V2259_VERSION
    context["mode"] = PRODUCT_AGENT_MODE
    context["principles"] = list(
        dict.fromkeys(
            [
                *(context.get("principles") or []),
                "每个商品只对应一个已经验Hash的输入Artifact；不得从相似商品、旧批次或缓存结果补齐判断。",
                "必须原样返回itemExecutionId和inputContentHash；它们是批次内唯一匹配合同。",
                "商品ID只用于业务可读性，不再承担批次唯一定位职责。",
                "itemExecutionId+inputContentHash验真后禁止再次用correlationId/storeId/productId/signalId重新匹配或删除结果。",
                "Agent1输出动作族不是旧Python白名单的裁决对象；能力是否可执行由当前Action Matrix/能力边界继续裁决。",
            ]
        )
    )
    context["guardrails"] = {
        **_dict(context.get("guardrails")),
        "exactArtifactInputOnly": True,
        "secondProjectionAllowed": False,
        "legacyBusinessResultReplayAllowed": False,
        "itemExecutionIdRequired": True,
        "inputContentHashRequired": True,
        "fallbackIdentityMatchingAllowed": False,
        "legacyIdentityRematchAllowed": False,
        "legacyActionFamilyWhitelistAllowed": False,
    }
    return context


def _fact_card(product: Dict[str, Any]) -> Dict[str, Any]:
    card = legacy._fact_card(product)
    execution = _execution(product)
    card.update(
        itemExecutionId=execution.get("itemExecutionId"),
        executionHash=execution.get("executionHash"),
        inputArtifactRef=execution.get("inputArtifactRef"),
        inputContentHash=execution.get("inputContentHash"),
        inputSchema=execution.get("inputSchema"),
        projectionVersion=execution.get("projectionVersion"),
    )
    return card


def _build_messages(
    data_version: str | None,
    products: List[Dict[str, Any]],
    rag_context: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    payload = {
        "dataVersion": data_version,
        "version": REAL_PRODUCT_AGENT_V2259_VERSION,
        "batchContract": {
            "matchingKey": NORMALIZATION_AUTHORITY,
            "expectedItemCount": len(products),
            "fallbackIdentityMatchingAllowed": False,
            "oneInputOneJudgment": True,
            "legacyIdentityRematchAllowed": False,
        },
        "products": [_fact_card(product) for product in products],
        "diagnosticRag": rag_context or build_agent1_rag_context(),
    }
    prompt = (
        "你是V22.5.10经营诊断Agent1。每个products元素都是传输系统已验Hash的专属输入文件。"
        "你只能依据该元素自身事实判断，不得读取、猜测或复用其他商品、其他批次或旧缓存判断。"
        "每个输入必须返回且只返回一项judgment。必须逐字原样返回itemExecutionId、"
        "inputContentHash、correlationId、productId、storeId、signalId。"
        "itemExecutionId与inputContentHash是唯一匹配合同；禁止只依赖数组顺序或商品ID。"
        "decisionType必须且只能是observe或act。observe为合法终态；act必须满足唯一主问题、"
        "唯一主动作、唯一责任人和唯一执行对象。动作族可以表达当前业务诊断意图，后续是否具备"
        "执行能力由Action Matrix/能力边界裁决，不得为了适配旧动作族白名单改写诊断。"
        "只返回JSON对象，顶层judgments数组。"
        "字段：itemExecutionId,inputContentHash,correlationId,productId,storeId,signalId,"
        "metricCode,severity,confidence,decisionHint,decisionType,finding,coreProblem,facts,"
        "causalHypotheses,rejectedHypotheses,decisionSummary,alternatives,selectedOperatingRoute,"
        "selectedActionFamilyHint,actionIntent,preconditions,riskBoundaries,missingEvidence,"
        "evidenceStatus,primaryProblemNode,primaryAction,primaryExecutionTarget,primaryOwner,"
        "decisiveFacts,supportingCoordination,forbiddenActionDomains,excludedActions,"
        "requiredActionData,capacityConstraints,companyHooks,ragProof,routeLock,actionFamilyLock。"
    )
    return [
        {"role": "system", "content": prompt},
        {
            "role": "user",
            "content": json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ], payload


def _base_business_item(
    raw: Dict[str, Any],
    product: Dict[str, Any],
    data_version: str | None,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Normalize one already exact-hash-matched item without identity rematching."""
    canonical, warning = legacy._canonicalize_raw(raw)
    identity = _business_identity(product)
    decision_type = _text(canonical.get("decisionType"), 40).lower() or "observe"
    hint = _text(
        canonical.get("decisionHint")
        or ("observe_only" if decision_type == "observe" else "action_candidate"),
        80,
    )
    family = _text(canonical.get("selectedActionFamilyHint"), 160) or None
    route = _text(canonical.get("selectedOperatingRoute"), 160)

    if decision_type == "observe":
        hint = "observe_only"
        family = None
        route = "observe"

    severity = _text(canonical.get("severity") or "normal", 40).lower()
    if severity not in {"normal", "low", "medium", "high", "critical"}:
        severity = "normal"

    finding = _text(canonical.get("finding"), 1200)
    reason = _text(canonical.get("decisionSummary") or finding or "继续观察", 1200)
    facts = _arr(canonical.get("facts")) or [
        {
            "factRef": f"F{index + 1}",
            "role": "evidence",
            "text": _text(value, 1000),
        }
        for index, value in enumerate(_arr(canonical.get("evidence")))
        if _text(value, 1000)
    ]

    route_lock = {
        "locked": True,
        "selectedOperatingRoute": route,
        "lockReason": reason,
        "observationOnly": decision_type == "observe",
    }
    family_lock = {
        "locked": True,
        "selectedActionFamily": family,
        "lockReason": reason,
        "forbiddenOverride": True,
        "observationOnly": decision_type == "observe",
    }
    decision_ir = {
        "version": REAL_PRODUCT_AGENT_V2259_VERSION,
        "decisionType": decision_type,
        "coreProblem": _text(canonical.get("coreProblem") or finding or reason, 1200),
        "facts": facts,
        "causalHypotheses": _arr(canonical.get("causalHypotheses")),
        "rejectedHypotheses": _arr(canonical.get("rejectedHypotheses")),
        "decisionSummary": reason,
        "alternatives": _arr(canonical.get("alternatives")),
        "selectedActionFamily": family,
        "actionIntent": _text(canonical.get("actionIntent"), 1200) or None,
        "preconditions": _arr(canonical.get("preconditions")),
        "riskBoundaries": _arr(canonical.get("riskBoundaries")),
        "missingEvidence": _arr(canonical.get("missingEvidence")),
        "ragProof": _dict(canonical.get("ragProof")),
    }
    judgment = {
        "stage": "agent1_contextual_diagnosis",
        "version": REAL_PRODUCT_AGENT_V2259_VERSION,
        "displayInDetail": True,
        "decisionType": decision_type,
        "selectedOperatingRoute": route,
        "selectedActionFamily": family,
        "primaryBusinessSignal": canonical.get("primaryBusinessSignal") or finding,
        "primaryOperatingGap": canonical.get("primaryOperatingGap") or decision_ir["coreProblem"],
        "businessHypothesis": canonical.get("businessHypothesis") or decision_ir["coreProblem"],
        "evidenceFacts": _arr(canonical.get("evidence")),
        "excludedActions": _arr(canonical.get("excludedActions")),
        "requiredActionData": _arr(canonical.get("requiredActionData")),
        "capacityConstraints": _arr(canonical.get("capacityConstraints")),
        "companyHooks": _arr(canonical.get("companyHooks")),
        "routeLock": route_lock,
        "actionFamilyLock": family_lock,
        "agent1DecisionIR": decision_ir,
    }

    item = {
        "version": REAL_PRODUCT_AGENT_V2259_VERSION,
        "dataVersion": data_version or product.get("dataVersion"),
        **identity,
        "metricCode": _text(canonical.get("metricCode") or "all_metrics", 160),
        "severity": severity,
        "decisionHint": hint,
        "decisionType": decision_type,
        "confidence": _confidence(canonical.get("confidence")),
        "finding": finding or reason,
        "selectedOperatingRoute": route,
        "selectedActionFamilyHint": family,
        "businessHypothesis": judgment["businessHypothesis"],
        "agent1OperatingJudgment": judgment,
        "agent1DecisionIR": decision_ir,
        "routeLock": route_lock,
        "actionFamilyLock": family_lock,
        "requiredActionData": judgment["requiredActionData"],
        "capacityConstraints": judgment["capacityConstraints"],
        "companyHooks": judgment["companyHooks"],
        "identityResolution": {
            "mode": NORMALIZATION_AUTHORITY,
            "canonical": True,
            "legacyIdentityRematchUsed": False,
        },
        "evidence": {
            "agentEvidence": judgment["evidenceFacts"][:10],
            "source": "v22510_exact_hash_agent1",
        },
        "signal": product,
        "agent1ApiCallCount": 1,
        "ragRetrievalScope": "diagnostic_rag_before_agent1",
        "observationOnly": decision_type == "observe",
        "taskAdmissionAllowed": decision_type == "act",
        "fallbackAllowed": False,
        "legacyActionFamilyWhitelistUsed": False,
        "rule": (
            "Exact execution identity is authoritative; legacy normalizers may shape "
            "format/lock semantics but may not rematch identity or delete a judgment "
            "because an old action-family whitelist does not recognize the capability."
        ),
    }

    # V22.5.5 execution-lock shaping is retained because it does not own execution
    # identity and does not contain the V196 action-family whitelist.
    item = execution_contract._apply_execution_lock(item, canonical)

    warning_text = str(warning.get("warning") or "")
    if warning_text:
        item["rawDecisionType"] = warning.get("rawDecisionType")
        item["normalizationWarnings"] = [warning_text]
        item["normalizationStatus"] = "normalized_with_warning"
    else:
        item["normalizationWarnings"] = []
        item["normalizationStatus"] = "normalized"
    item["decisionContractStatus"] = "valid"
    item["sourceValidationStatus"] = "complete"
    item["normalizationAuthority"] = NORMALIZATION_AUTHORITY
    item["legacyIdentityRematchAllowed"] = False
    item["legacyActionFamilyWhitelistAllowed"] = False
    return item, warning


def _normalize_judgments(
    provider_payload: Dict[str, Any],
    products: List[Dict[str, Any]],
    data_version: str | None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    raw_items = provider_payload.get("judgments") if isinstance(provider_payload, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("provider_json_missing_judgments_array")

    expected: Dict[str, Dict[str, Any]] = {}
    for product in products:
        execution = _execution(product)
        item_execution_id = _text(execution.get("itemExecutionId"), 120)
        if not item_execution_id:
            raise ValueError("agent1_hash_execution_identity_missing")
        expected[item_execution_id] = product

    raw_ids = [
        _text(raw.get("itemExecutionId"), 120)
        for raw in raw_items
        if isinstance(raw, dict) and _text(raw.get("itemExecutionId"), 120)
    ]
    id_counts = Counter(raw_ids)
    duplicate_ids = {value for value, count in id_counts.items() if count > 1}

    accepted_raw: List[Dict[str, Any]] = []
    raw_returned: List[str] = []
    exact_returned: List[str] = []
    extra: List[str] = []
    hash_mismatches: List[Dict[str, Any]] = []

    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_execution_id = _text(raw.get("itemExecutionId"), 120)
        if item_execution_id:
            raw_returned.append(item_execution_id)
        product = expected.get(item_execution_id)
        if not product:
            extra.append(item_execution_id or "missing_itemExecutionId")
            continue
        if item_execution_id in duplicate_ids:
            continue

        execution = _execution(product)
        expected_hash = _text(execution.get("inputContentHash"), 160)
        returned_hash = _text(raw.get("inputContentHash"), 160)
        if not returned_hash or returned_hash != expected_hash:
            hash_mismatches.append(
                {
                    "itemExecutionId": item_execution_id,
                    "expectedInputContentHash": expected_hash,
                    "returnedInputContentHash": returned_hash or None,
                }
            )
            continue

        # Exact execution identity is authoritative. Human-readable business identity
        # is projected from the input Artifact after the hash match, never used to
        # re-select a source item.
        canonical = dict(raw)
        canonical.update(_business_identity(product))
        canonical.update(
            itemExecutionId=item_execution_id,
            executionHash=execution.get("executionHash"),
            inputArtifactRef=execution.get("inputArtifactRef"),
            inputContentHash=expected_hash,
        )
        accepted_raw.append(canonical)
        exact_returned.append(item_execution_id)

    normalized: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    for raw in accepted_raw:
        item_execution_id = _text(raw.get("itemExecutionId"), 120)
        product = expected[item_execution_id]
        item, warning = _base_business_item(raw, product, data_version)
        execution = _execution(product)
        item.update(
            itemExecutionId=item_execution_id,
            executionHash=execution.get("executionHash"),
            inputArtifactRef=execution.get("inputArtifactRef"),
            inputContentHash=execution.get("inputContentHash"),
            hashIdentityMatched=True,
            fallbackIdentityMatchingUsed=False,
        )
        normalized.append(item)
        warnings.append(warning)

    missing = sorted(set(expected) - set(exact_returned))
    alias_count = sum(
        1
        for warning in warnings
        if str(warning.get("warning") or "").startswith("decision_alias_normalized")
    )
    fail_closed_count = sum(
        1
        for warning in warnings
        if str(warning.get("warning") or "").startswith(
            ("decision_type_invalid_fail_closed", "act_route_contract_incomplete")
        )
    )

    diagnostics = {
        "version": REAL_PRODUCT_AGENT_V2259_VERSION,
        "providerResponseItemCount": len(raw_items),
        "normalizedJudgmentCount": len(normalized),
        "normalizationStatus": (
            "normalized_with_warning"
            if alias_count or fail_closed_count
            else "normalized"
        ),
        "decisionAliasNormalizedCount": alias_count,
        "unknownDecisionFailClosedCount": fail_closed_count,
        "providerCallStatusOwnedByTokenRuntime": True,
        "noMatchingJudgmentMeansIdentityAbsentOnly": True,
        "normalizationAuthority": NORMALIZATION_AUTHORITY,
        "legacyIdentityRematchAllowed": False,
        "legacyActionFamilyWhitelistAllowed": False,
        "legacyNormalizerDeletionAllowed": False,
        "expectedItemExecutionIds": sorted(expected),
        "rawReturnedItemExecutionIds": raw_returned,
        "exactReturnedItemExecutionIds": exact_returned,
        "missingItemExecutionIds": missing,
        "extraItemExecutionIds": sorted(set(extra)),
        "duplicateItemExecutionIds": sorted(duplicate_ids),
        "inputContentHashMismatches": hash_mismatches,
        "exactHashMatchedCount": len(exact_returned),
        "fallbackIdentityMatchingAllowed": False,
        "secondProjectionApplied": False,
    }
    return normalized, diagnostics


_source_maps = legacy._source_maps


__all__ = [
    "REAL_PRODUCT_AGENT_V2259_VERSION",
    "PRODUCT_AGENT_MODE",
    "NORMALIZATION_AUTHORITY",
    "LEGACY_IDENTITY_REMATCH_ALLOWED",
    "LEGACY_ACTION_FAMILY_WHITELIST_ALLOWED",
    "build_agent1_rag_context",
    "_fact_card",
    "_build_messages",
    "_normalize_judgments",
    "_source_maps",
]
