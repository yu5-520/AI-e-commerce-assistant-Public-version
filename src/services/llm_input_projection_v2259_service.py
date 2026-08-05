"""V22.5.9 hash-directed LLM projection.

A model-facing input that was already materialized as an immutable Agent input
Artifact is transported without a second business projection. Legacy callers keep a
lossless stage projection, but cache fingerprints retain dataVersion, packageId,
signalId, Artifact references and content hashes. Cached outputs are never rebound
to another business identity.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Iterable, List, Tuple

LLM_INPUT_PROJECTION_VERSION = "22.5.9"

# Only transport-time values are volatile. Business identity and Artifact lineage are
# deliberately retained in exact execution fingerprints.
_VOLATILE_KEYS = {
    "generatedAt",
    "updatedAt",
    "createdAt",
    "timestamp",
    "startedAt",
    "finishedAt",
    "providerRequestId",
    "latencyMs",
    "workerId",
    "leaseExpiresAt",
    "cacheAgeMs",
    "refreshing",
}
_TRACE_KEYS = {
    "raw",
    "events",
    "history",
    "diagnostics",
    "batchDiagnostics",
    "providerTrace",
}
_IDENTITY_KEYS = {
    "productId",
    "storeId",
    "productTitle",
    "title",
    "shortTitle",
    "platform",
    "verticalCategory",
    "categoryId",
    "productRole",
    "lifecycleStage",
    "skuId",
    "spuId",
    "erpProductCode",
    "storeName",
    "dataVersion",
    "signalId",
    "correlationId",
    "packageId",
    "itemExecutionId",
    "inputArtifactRef",
    "inputContentHash",
    "executionHash",
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _arr(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean_scalar(value: Any) -> Any:
    return " ".join(value.split()) if isinstance(value, str) else value


def _compact_value(
    value: Any,
    *,
    depth: int = 0,
    max_depth: int = 10,
    max_list: int = 64,
    drop_keys: Iterable[str] = (),
) -> Any:
    blocked = set(drop_keys) | _TRACE_KEYS
    if value is None or isinstance(value, (bool, int, float, str)):
        return _clean_scalar(value)
    if depth >= max_depth:
        if isinstance(value, dict):
            return {
                str(key): _clean_scalar(item)
                for key, item in value.items()
                if key not in blocked and item not in (None, "") and isinstance(item, (bool, int, float, str))
            }
        if isinstance(value, list):
            return [_clean_scalar(item) for item in value[:max_list] if isinstance(item, (bool, int, float, str))]
        return None
    if isinstance(value, list):
        result: List[Any] = []
        for item in value[:max_list]:
            compact = _compact_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_list=max_list,
                drop_keys=blocked,
            )
            if compact not in (None, {}, []):
                result.append(compact)
        return result
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if key in blocked:
                continue
            compact = _compact_value(
                item,
                depth=depth + 1,
                max_depth=max_depth,
                max_list=max_list,
                drop_keys=blocked,
            )
            if compact not in (None, {}, []):
                result[str(key)] = compact
        return result
    return _clean_scalar(str(value))


def _identity(value: Dict[str, Any]) -> Dict[str, Any]:
    merged = {
        **_dict(value.get("profileLayer")),
        **_dict(value.get("productIdentity")),
        **_dict(value.get("identity")),
        **value,
    }
    result = {
        key: _clean_scalar(merged.get(key))
        for key in _IDENTITY_KEYS
        if merged.get(key) not in (None, "", [], {})
    }
    if not result.get("productTitle"):
        result["productTitle"] = result.get("title") or result.get("shortTitle")
    return {key: item for key, item in result.items() if item not in (None, "")}


def _parse_user_payload(messages: List[Dict[str, str]]) -> Tuple[int | None, Dict[str, Any]]:
    for index in range(len(messages) - 1, -1, -1):
        if str(messages[index].get("role") or "") != "user":
            continue
        try:
            value = json.loads(str(messages[index].get("content") or ""))
        except Exception:
            return None, {}
        return index, value if isinstance(value, dict) else {}
    return None, {}


def _append_stable_context(messages: List[Dict[str, str]], stable_context: Dict[str, Any]) -> List[Dict[str, str]]:
    result = [dict(item) for item in messages]
    marker = "\n\n[V22.5.9_HASH_DIRECTED_CONTEXT]\n" + _stable_json(stable_context)
    system_index = next((index for index, item in enumerate(result) if item.get("role") == "system"), None)
    if system_index is None:
        result.insert(0, {"role": "system", "content": marker.strip()})
    else:
        result[system_index]["content"] = str(result[system_index].get("content") or "") + marker
    return result


def _project_agent1_product(product: Dict[str, Any]) -> Dict[str, Any]:
    # The Agent1 input Artifact already owns field selection. Keep its complete
    # projected business semantics and immutable execution identity.
    return _compact_value(product, max_depth=12, max_list=96)


def _project_agent2_package(package: Dict[str, Any]) -> Dict[str, Any]:
    # Agent2 input Artifacts are already execution-lock projections. Preserve exact
    # package identity and Artifact lineage; remove audit-only raw traces only.
    return _compact_value(package, max_depth=12, max_list=96)


def prepare_llm_request(
    stage: str,
    messages: List[Dict[str, str]],
    cache_payload: Any | None,
) -> Tuple[List[Dict[str, str]], Any, Dict[str, Any]]:
    source_messages = [dict(item) for item in messages]
    source_chars = len(_stable_json(source_messages))
    user_index, parsed = _parse_user_payload(source_messages)
    if user_index is None or not parsed:
        semantic = cache_payload if cache_payload is not None else source_messages
        return source_messages, semantic, {
            "version": LLM_INPUT_PROJECTION_VERSION,
            "stage": stage,
            "applied": False,
            "sourceChars": source_chars,
            "projectedChars": source_chars,
            "semanticPayloadHash": _hash(semantic),
            "semanticContinuity": "passed",
        }

    # Hash-directed callers submit an already materialized batch manifest and exact
    # Agent input payload. No second projection or item-cache collection is allowed.
    if parsed.get("_hashDirectedExecution") is True or isinstance(parsed.get("artifactBatchManifest"), dict):
        semantic = {
            "projectionVersion": LLM_INPUT_PROJECTION_VERSION,
            "stage": stage,
            "hashDirectedExecution": True,
            "payload": _strip_volatile(copy.deepcopy(parsed)),
        }
        return source_messages, semantic, {
            "version": LLM_INPUT_PROJECTION_VERSION,
            "stage": stage,
            "applied": False,
            "projectionMode": "pre_materialized_artifact_passthrough",
            "sourceChars": source_chars,
            "projectedChars": source_chars,
            "savedChars": 0,
            "collectionSize": len(parsed.get("products") or parsed.get("packages") or parsed.get("sops") or []),
            "stableContextHash": _hash({"stage": stage, "promptVersion": parsed.get("version"), "hashDirectedExecution": True}),
            "semanticPayloadHash": _hash(semantic),
            "semanticContinuity": "passed",
            "itemCacheDisabledByArtifactContract": True,
        }

    stable_context: Dict[str, Any] = {
        "projectionVersion": LLM_INPUT_PROJECTION_VERSION,
        "promptContractVersion": parsed.get("version"),
        "identityContract": "business_identity_and_artifact_hashes_are_not_volatile",
        "cachedOutputRebindingAllowed": False,
    }
    if stage == "product_judgment_agent":
        products = [
            _project_agent1_product(item)
            for item in _arr(parsed.get("products"))
            if isinstance(item, dict)
        ]
        stable_context.update(
            contextType="agent1_materialized_input_artifact",
            diagnosticRag=_compact_value(parsed.get("diagnosticRag"), max_depth=10, max_list=64),
            requiredDynamicFields=[
                "itemExecutionId",
                "inputArtifactRef",
                "inputContentHash",
                "productId",
                "storeId",
                "trendContext",
                "sourceLineageValidation",
            ],
        )
        dynamic_payload = {"products": products}
    elif stage == "action_plan_judgment_agent":
        packages = [
            _project_agent2_package(item)
            for item in _arr(parsed.get("packages"))
            if isinstance(item, dict)
        ]
        stable_context.update(
            contextType="agent2_materialized_input_artifact",
            requiredDynamicFields=[
                "itemExecutionId",
                "inputArtifactRef",
                "inputContentHash",
                "packageId",
                "executionLock",
                "capabilityPack",
                "permissionBoundary",
                "parameterBoundary",
            ],
        )
        dynamic_payload = {"packages": packages}
    else:
        semantic = cache_payload if cache_payload is not None else parsed
        return source_messages, semantic, {
            "version": LLM_INPUT_PROJECTION_VERSION,
            "stage": stage,
            "applied": False,
            "sourceChars": source_chars,
            "projectedChars": source_chars,
            "semanticPayloadHash": _hash(semantic),
            "semanticContinuity": "passed",
        }

    projected_messages = _append_stable_context(source_messages, stable_context)
    projected_messages[user_index]["content"] = _stable_json(dynamic_payload)
    semantic_payload = {
        "stableContext": stable_context,
        "dynamicPayload": semantic_cache_payload(stage, dynamic_payload),
    }
    projected_chars = len(_stable_json(projected_messages))
    return projected_messages, semantic_payload, {
        "version": LLM_INPUT_PROJECTION_VERSION,
        "stage": stage,
        "applied": True,
        "projectionMode": "lossless_exact_identity_projection",
        "sourceChars": source_chars,
        "projectedChars": projected_chars,
        "savedChars": max(0, source_chars - projected_chars),
        "collectionSize": len(dynamic_payload.get("products") or dynamic_payload.get("packages") or []),
        "stableContextHash": _hash(stable_context),
        "semanticPayloadHash": _hash(semantic_payload),
        "semanticContinuity": "passed",
        "cachedOutputRebindingAllowed": False,
    }


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_volatile(item)
            for key, item in sorted(value.items())
            if key not in _VOLATILE_KEYS
        }
    return value


def semantic_cache_payload(stage: str, value: Any) -> Any:
    clean = _strip_volatile(copy.deepcopy(value))
    if isinstance(clean, dict):
        key = "products" if stage == "product_judgment_agent" else "packages" if stage == "action_plan_judgment_agent" else None
        if key and isinstance(clean.get(key), list):
            clean[key] = sorted(
                clean[key],
                key=lambda item: _stable_json(
                    {
                        "itemExecutionId": item.get("itemExecutionId") if isinstance(item, dict) else None,
                        "inputContentHash": item.get("inputContentHash") if isinstance(item, dict) else None,
                        "productId": _dict(item.get("identity")).get("productId") if isinstance(item, dict) else None,
                        "storeId": _dict(item.get("identity")).get("storeId") if isinstance(item, dict) else None,
                        "packageId": item.get("packageId") if isinstance(item, dict) else None,
                    }
                ),
            )
    return clean


def semantic_item_fingerprint(stage: str, item: Dict[str, Any]) -> str:
    exact = _strip_volatile(copy.deepcopy(item))
    return _hash(
        {
            "projectionVersion": LLM_INPUT_PROJECTION_VERSION,
            "stage": stage,
            "item": exact,
        }
    )


def stage_collection(stage: str, dynamic_payload: Dict[str, Any]) -> Tuple[str | None, str | None, List[Dict[str, Any]]]:
    if dynamic_payload.get("_hashDirectedExecution") is True or isinstance(dynamic_payload.get("artifactBatchManifest"), dict):
        return None, None, []
    if stage == "product_judgment_agent":
        return "products", "judgments", [item for item in _arr(dynamic_payload.get("products")) if isinstance(item, dict)]
    if stage == "action_plan_judgment_agent":
        return "packages", "plans", [item for item in _arr(dynamic_payload.get("packages")) if isinstance(item, dict)]
    return None, None, []


def parse_projected_dynamic_payload(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    _, payload = _parse_user_payload(messages)
    return payload


def replace_projected_collection(messages: List[Dict[str, str]], collection_key: str, items: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    result = [dict(item) for item in messages]
    index, payload = _parse_user_payload(result)
    if index is None:
        return result
    payload[collection_key] = items
    result[index]["content"] = _stable_json(payload)
    return result


def output_identity(stage: str, input_item: Dict[str, Any]) -> Dict[str, Any]:
    identity = _identity(input_item)
    if stage == "product_judgment_agent":
        return {
            "itemExecutionId": input_item.get("itemExecutionId") or identity.get("itemExecutionId"),
            "inputContentHash": input_item.get("inputContentHash") or identity.get("inputContentHash"),
            "correlationId": input_item.get("correlationId") or identity.get("correlationId"),
            "signalId": input_item.get("signalId") or identity.get("signalId"),
            "productId": input_item.get("productId") or identity.get("productId"),
            "storeId": input_item.get("storeId") or identity.get("storeId"),
        }
    return {
        "itemExecutionId": input_item.get("itemExecutionId") or identity.get("itemExecutionId"),
        "inputContentHash": input_item.get("inputContentHash") or identity.get("inputContentHash"),
        "packageId": input_item.get("packageId") or identity.get("packageId"),
        "productId": input_item.get("productId") or identity.get("productId"),
        "storeId": input_item.get("storeId") or identity.get("storeId"),
        "actionFamily": input_item.get("lockedActionFamily"),
    }


def rebind_cached_output(stage: str, output_item: Dict[str, Any], input_item: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(output_item)
    expected = output_identity(stage, input_item)
    for key, value in expected.items():
        if value in (None, ""):
            continue
        actual = result.get(key)
        if actual in (None, "") or str(actual) != str(value):
            raise ValueError(
                f"cached_output_exact_identity_mismatch:{stage}:{key}:{actual}:{value}"
            )
    result["cacheIdentityVerified"] = True
    result["cachedOutputRebound"] = False
    return result


__all__ = [
    "LLM_INPUT_PROJECTION_VERSION",
    "prepare_llm_request",
    "semantic_cache_payload",
    "semantic_item_fingerprint",
    "stage_collection",
    "parse_projected_dynamic_payload",
    "replace_projected_collection",
    "output_identity",
    "rebind_cached_output",
]
