"""V22.5.9 Agent1 prompt and exact batch-item output contract."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

from src.services import real_product_judgment_agent_v2258_service as legacy

REAL_PRODUCT_AGENT_V2259_VERSION = "22.5.9"
PRODUCT_AGENT_MODE = "hash_directed_exact_input_once_decision"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 300) -> str:
    return " ".join(str(value or "").split())[:limit]


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
            "matchingKey": "itemExecutionId+inputContentHash",
            "expectedItemCount": len(products),
            "fallbackIdentityMatchingAllowed": False,
            "oneInputOneJudgment": True,
        },
        "products": [_fact_card(product) for product in products],
        "diagnosticRag": rag_context or build_agent1_rag_context(),
    }
    prompt = (
        "你是V22.5.9经营诊断Agent1。每个products元素都是传输系统已验Hash的专属输入文件。"
        "你只能依据该元素自身事实判断，不得读取、猜测或复用其他商品、其他批次或旧缓存判断。"
        "每个输入必须返回且只返回一项judgment。必须逐字原样返回itemExecutionId、"
        "inputContentHash、correlationId、productId、storeId、signalId。"
        "itemExecutionId与inputContentHash是唯一匹配合同；禁止只依赖数组顺序或商品ID。"
        "decisionType必须且只能是observe或act。observe为合法终态；act必须满足唯一主问题、"
        "唯一主动作、唯一责任人和唯一执行对象。只返回JSON对象，顶层judgments数组。"
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
        # Duplicate outputs make the item ambiguous. Reject every copy and do not let
        # the runtime accept the first one merely because it arrived first.
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
        identity = _business_identity(product)
        canonical = dict(raw)
        canonical.update(identity)
        canonical.update(
            itemExecutionId=item_execution_id,
            executionHash=execution.get("executionHash"),
            inputArtifactRef=execution.get("inputArtifactRef"),
            inputContentHash=expected_hash,
        )
        accepted_raw.append(canonical)
        exact_returned.append(item_execution_id)

    normalized, diagnostics = legacy._normalize_judgments(
        {**provider_payload, "judgments": accepted_raw},
        legacy._source_maps(products),
        data_version,
    )

    by_execution = {
        _text(item.get("itemExecutionId"), 120): item
        for item in normalized
        if isinstance(item, dict) and item.get("itemExecutionId")
    }
    for item_execution_id, product in expected.items():
        item = by_execution.get(item_execution_id)
        if not item:
            identity = _business_identity(product)
            for candidate in normalized:
                if not isinstance(candidate, dict):
                    continue
                if (
                    _text(candidate.get("correlationId"), 240) == identity["correlationId"]
                    and identity["correlationId"]
                ):
                    item = candidate
                    break
        if not item:
            continue
        execution = _execution(product)
        item.update(
            itemExecutionId=item_execution_id,
            executionHash=execution.get("executionHash"),
            inputArtifactRef=execution.get("inputArtifactRef"),
            inputContentHash=execution.get("inputContentHash"),
            hashIdentityMatched=True,
            fallbackIdentityMatchingUsed=False,
        )

    missing = sorted(set(expected) - set(exact_returned))
    diagnostics.update(
        version=REAL_PRODUCT_AGENT_V2259_VERSION,
        expectedItemExecutionIds=sorted(expected),
        rawReturnedItemExecutionIds=raw_returned,
        exactReturnedItemExecutionIds=exact_returned,
        missingItemExecutionIds=missing,
        extraItemExecutionIds=sorted(set(extra)),
        duplicateItemExecutionIds=sorted(duplicate_ids),
        inputContentHashMismatches=hash_mismatches,
        exactHashMatchedCount=len(exact_returned),
        fallbackIdentityMatchingAllowed=False,
        secondProjectionApplied=False,
    )
    return normalized, diagnostics


_source_maps = legacy._source_maps

__all__ = [
    "REAL_PRODUCT_AGENT_V2259_VERSION",
    "PRODUCT_AGENT_MODE",
    "build_agent1_rag_context",
    "_fact_card",
    "_build_messages",
    "_normalize_judgments",
    "_source_maps",
]
