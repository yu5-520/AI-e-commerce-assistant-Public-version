"""V22.5.9 exact-Artifact LLM gateway.

The legacy gateway remains available for historical stages. This gateway is used by
hash-directed stages only: it sends the already-materialized Agent input without a
second semantic projection and without request/item business-result caches.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple

from src.services import llm_gateway_v196_service as legacy
from src.services.hash_directed_artifact_runtime_v2259_service import hash_value

HASH_DIRECTED_LLM_GATEWAY_VERSION = "22.5.9"


def call_json_exact_artifact(
    *,
    stage: str,
    prompt_version: str,
    messages: List[Dict[str, str]],
    execution_hashes: List[str],
    temperature: float = 0.1,
    timeout_seconds: int = 120,
    model: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Call the provider once for exact immutable inputs.

    No call to ``prepare_llm_request`` is allowed here. No legacy request cache,
    semantic item cache or identity rebinding is consulted.
    """
    provider = legacy._provider_name(stage)
    provider_model = legacy._provider_model(stage, model)
    thinking_enabled = legacy._provider_thinking_enabled(stage)
    projection = {
        "version": "22.5.9.exact_artifact_input",
        "sourceChars": sum(len(str(item.get("content") or "")) for item in messages),
        "projectedChars": sum(len(str(item.get("content") or "")) for item in messages),
        "savedChars": 0,
        "collectionSize": len(execution_hashes),
        "stableContextHash": hash_value(
            {
                "stage": stage,
                "promptVersion": prompt_version,
                "executionHashes": execution_hashes,
            }
        ),
        "secondProjectionApplied": False,
        "requestCacheEnabled": False,
        "itemResultCacheEnabled": False,
    }
    input_fingerprint = hash_value(
        {
            "gatewayVersion": HASH_DIRECTED_LLM_GATEWAY_VERSION,
            "stage": stage,
            "provider": provider,
            "model": provider_model,
            "promptVersion": prompt_version,
            "executionHashes": execution_hashes,
            "messages": messages,
        }
    )
    if not legacy._provider_enabled(stage):
        raise RuntimeError(f"{stage}_disabled")
    api_key = legacy._provider_api_key(stage)
    if not api_key:
        raise RuntimeError(f"missing_api_key_for_{stage}_{provider}")

    request_body = legacy._request_body(
        stage=stage,
        provider=provider,
        model=provider_model,
        messages=messages,
        temperature=temperature,
    )
    body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        legacy._provider_base_url(stage),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    started = time.time()
    provider_request_id = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            provider_request_id = str(
                response.headers.get("x-request-id")
                or response.headers.get("x-dashscope-request-id")
                or ""
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:500]
        usage = {
            "provider": provider,
            "model": provider_model,
            "latencyMs": int((time.time() - started) * 1000),
            "providerCallExecuted": True,
        }
        legacy._audit(
            stage=stage,
            provider=provider,
            model=provider_model,
            prompt_version=prompt_version,
            input_fingerprint=input_fingerprint,
            projection=projection,
            item_cache_hits=0,
            item_cache_misses=len(execution_hashes),
            local_replay=False,
            provider_call_executed=True,
            usage=usage,
            status="hash_directed_provider_http_error",
            error=f"HTTP {exc.code}: {detail}",
        )
        raise RuntimeError(
            f"hash_directed_provider_http_{provider}_{exc.code}:{detail}"
        ) from exc
    except urllib.error.URLError as exc:
        usage = {
            "provider": provider,
            "model": provider_model,
            "latencyMs": int((time.time() - started) * 1000),
            "providerCallExecuted": True,
        }
        legacy._audit(
            stage=stage,
            provider=provider,
            model=provider_model,
            prompt_version=prompt_version,
            input_fingerprint=input_fingerprint,
            projection=projection,
            item_cache_hits=0,
            item_cache_misses=len(execution_hashes),
            local_replay=False,
            provider_call_executed=True,
            usage=usage,
            status="hash_directed_provider_network_error",
            error=str(exc.reason)[:500],
        )
        raise RuntimeError(
            f"hash_directed_provider_network_{provider}:{exc.reason}"
        ) from exc

    data = json.loads(raw)
    provider_request_id = provider_request_id or str(data.get("id") or "")
    usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    message = (
        choices[0].get("message")
        if choices and isinstance(choices[0], dict)
        else {}
    ) or {}
    content = legacy._message_text(message if isinstance(message, dict) else {})
    if not content:
        raise ValueError("hash_directed_gateway_response_empty_content")
    payload = legacy._extract_json_object(content)
    cache_hit, cache_miss, cache_creation = legacy._provider_cache_usage(usage_raw)
    usage: Dict[str, Any] = {
        "input": int(usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens") or 0),
        "output": int(
            usage_raw.get("completion_tokens") or usage_raw.get("output_tokens") or 0
        ),
        "reasoningTokens": legacy._reasoning_tokens(usage_raw),
        "providerCacheHitInput": cache_hit,
        "providerCacheMissInput": cache_miss,
        "providerCacheCreationInput": cache_creation,
        "cacheHit": False,
        "idempotentReplay": False,
        "providerCallExecuted": True,
        "provider": provider,
        "providerRequestId": provider_request_id,
        "itemCacheHits": 0,
        "itemCacheMisses": len(execution_hashes),
        "latencyMs": int((time.time() - started) * 1000),
        "stage": stage,
        "model": provider_model,
        "thinkingEnabled": thinking_enabled,
        "thinkingBudget": legacy._provider_thinking_budget(stage),
        "promptVersion": prompt_version,
        "inputFingerprint": input_fingerprint,
        "gatewayVersion": HASH_DIRECTED_LLM_GATEWAY_VERSION,
        "projectionVersion": projection["version"],
        "projection": projection,
        "executionHashes": execution_hashes,
        "requestCacheEnabled": False,
        "itemResultCacheEnabled": False,
        "secondProjectionApplied": False,
        "actualCalls": 1,
    }
    legacy._audit(
        stage=stage,
        provider=provider,
        model=provider_model,
        prompt_version=prompt_version,
        input_fingerprint=input_fingerprint,
        projection=projection,
        item_cache_hits=0,
        item_cache_misses=len(execution_hashes),
        local_replay=False,
        provider_call_executed=True,
        usage=usage,
        status="hash_directed_provider_succeeded",
    )
    return payload, usage


__all__ = ["HASH_DIRECTED_LLM_GATEWAY_VERSION", "call_json_exact_artifact"]
