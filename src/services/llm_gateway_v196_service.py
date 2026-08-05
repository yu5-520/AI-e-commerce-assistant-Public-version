"""V22.5.9 gateway facade: provider transport only, no business-result item cache.

Exact Agent result replay is owned by artifact_execution_index_v2259 and returns an
immutable accepted output Artifact. The legacy provider adapter, request audit and
provider prompt-cache accounting remain unchanged.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.services import llm_gateway_v196_legacy_service as legacy

LLM_GATEWAY_V196_VERSION = "22.5.9"
DEFAULT_PROVIDER = legacy.DEFAULT_PROVIDER
DEFAULT_QWEN_MODEL = legacy.DEFAULT_QWEN_MODEL
DEFAULT_QWEN_BASE_URL = legacy.DEFAULT_QWEN_BASE_URL
DEFAULT_DEEPSEEK_MODEL = legacy.DEFAULT_DEEPSEEK_MODEL
DEFAULT_DEEPSEEK_BASE_URL = legacy.DEFAULT_DEEPSEEK_BASE_URL

# Business decisions are never replayed from llm_item_result_cache_v211. Exact replay
# belongs to the immutable Artifact execution index.
legacy._AGENT_ITEM_CACHE_STAGES = set()


def ensure_llm_cache_table() -> None:
    legacy.ensure_llm_cache_table()


def provider_runtime_config(stage: str | None = None) -> Dict[str, Any]:
    result = legacy.provider_runtime_config(stage)
    result.update(
        gatewayVersion=LLM_GATEWAY_V196_VERSION,
        businessResultItemCacheEnabled=False,
        exactReplayOwner="artifact_execution_index_v2259",
        cachedOutputRebindingAllowed=False,
    )
    return result


def call_json(
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
    payload, usage = legacy.call_json(
        stage=stage,
        prompt_version=prompt_version,
        messages=messages,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
        model=model,
        cache_enabled=cache_enabled,
        cache_payload=cache_payload,
    )
    result_usage = dict(usage or {})
    result_usage.update(
        gatewayVersion=LLM_GATEWAY_V196_VERSION,
        itemCacheHits=0,
        itemCacheMisses=0,
        businessResultItemCacheEnabled=False,
        exactReplayOwner="artifact_execution_index_v2259",
        cachedOutputRebindingAllowed=False,
    )
    return payload, result_usage


def __getattr__(name: str) -> Any:
    return getattr(legacy, name)


__all__ = [
    "LLM_GATEWAY_V196_VERSION",
    "DEFAULT_PROVIDER",
    "DEFAULT_QWEN_MODEL",
    "DEFAULT_QWEN_BASE_URL",
    "DEFAULT_DEEPSEEK_MODEL",
    "DEFAULT_DEEPSEEK_BASE_URL",
    "ensure_llm_cache_table",
    "provider_runtime_config",
    "call_json",
]
