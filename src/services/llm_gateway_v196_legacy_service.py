"""V21.2 multi-provider LLM gateway with quality-first cost governance.

The business chain stays model-agnostic. Alibaba Cloud Bailian/Qwen and DeepSeek
share the same semantic projection, exact per-item replay, audit and JSON contract.
Qwen3.7-Plus is the preferred competition profile, while provider selection stays
explicit and reversible through environment variables.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.llm_input_projection_v211_service import (
    LLM_INPUT_PROJECTION_VERSION,
    output_identity,
    parse_projected_dynamic_payload,
    prepare_llm_request,
    rebind_cached_output,
    replace_projected_collection,
    semantic_item_fingerprint,
    stage_collection,
)

LLM_GATEWAY_V196_VERSION = "21.2"
DEFAULT_PROVIDER = "aliyun_bailian"
DEFAULT_QWEN_MODEL = "qwen3.7-plus"
DEFAULT_QWEN_BASE_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
DEFAULT_DEEPSEEK_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/chat/completions"
_AGENT_ITEM_CACHE_STAGES = {"product_judgment_agent", "action_plan_judgment_agent"}

_PROVIDER_ALIASES = {
    "aliyun": "aliyun_bailian",
    "aliyun_bailian": "aliyun_bailian",
    "bailian": "aliyun_bailian",
    "dashscope": "aliyun_bailian",
    "qwen": "aliyun_bailian",
    "deepseek": "deepseek",
    "openai_compatible": "openai_compatible",
    "compatible": "openai_compatible",
}

_STAGE_PREFIXES = {
    "product_judgment_agent": "PRODUCT_JUDGMENT_AGENT",
    "action_plan_judgment_agent": "ACTION_PLAN_AGENT",
    "task_mapping_agent": "TASK_MAPPING_AGENT",
}


def _table(conn: Any, name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
    )


def _columns(conn: Any, table: str) -> set[str]:
    if not _table(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _ensure_column(conn: Any, table: str, column: str, definition: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str) -> int | None:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _stage_env(stage: str | None, suffix: str) -> str | None:
    prefix = _STAGE_PREFIXES.get(str(stage or ""))
    return os.getenv(f"{prefix}_{suffix}") if prefix else None


def _normalize_provider(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    return _PROVIDER_ALIASES.get(raw, raw or DEFAULT_PROVIDER)


def _provider_name(stage: str | None = None) -> str:
    explicit = _stage_env(stage, "PROVIDER") or os.getenv("LLM_PROVIDER")
    if explicit:
        return _normalize_provider(explicit)
    if os.getenv("BAILIAN_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY"):
        return "aliyun_bailian"
    if os.getenv("DEEPSEEK_API_KEY"):
        return "deepseek"
    return DEFAULT_PROVIDER


def _provider_api_key(stage: str | None = None) -> str | None:
    stage_key = _stage_env(stage, "API_KEY")
    if stage_key:
        return stage_key
    provider = _provider_name(stage)
    if provider == "aliyun_bailian":
        return (
            os.getenv("BAILIAN_API_KEY")
            or os.getenv("DASHSCOPE_API_KEY")
            or os.getenv("QWEN_API_KEY")
            or os.getenv("LLM_API_KEY")
        )
    if provider == "deepseek":
        return os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")
    return os.getenv("LLM_API_KEY")


def _provider_model(stage: str | None = None, model: str | None = None) -> str:
    if model:
        return str(model).strip()
    stage_model = _stage_env(stage, "MODEL")
    if stage_model:
        return stage_model
    provider = _provider_name(stage)
    if provider == "aliyun_bailian":
        return (
            os.getenv("BAILIAN_MODEL")
            or os.getenv("QWEN_MODEL")
            or os.getenv("LLM_MODEL")
            or DEFAULT_QWEN_MODEL
        )
    if provider == "deepseek":
        return (
            os.getenv("DEEPSEEK_MODEL")
            or os.getenv("LLM_MODEL")
            or DEFAULT_DEEPSEEK_MODEL
        )
    return os.getenv("LLM_MODEL") or DEFAULT_QWEN_MODEL


def _normalize_chat_completions_url(value: str) -> str:
    url = str(value or "").strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/chat/completions"):
        return url
    if url.endswith("/compatible-mode/v1") or url.endswith("/v1"):
        return url + "/chat/completions"
    parsed = urllib.parse.urlparse(url)
    if not parsed.path or parsed.path == "/":
        return url + "/chat/completions"
    return url


def _provider_base_url(stage: str | None = None) -> str:
    stage_url = _stage_env(stage, "BASE_URL")
    if stage_url:
        return _normalize_chat_completions_url(stage_url)
    provider = _provider_name(stage)
    if provider == "aliyun_bailian":
        value = (
            os.getenv("BAILIAN_BASE_URL")
            or os.getenv("DASHSCOPE_BASE_URL")
            or os.getenv("QWEN_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or DEFAULT_QWEN_BASE_URL
        )
        return _normalize_chat_completions_url(value)
    if provider == "deepseek":
        return _normalize_chat_completions_url(
            os.getenv("DEEPSEEK_BASE_URL")
            or os.getenv("LLM_BASE_URL")
            or DEFAULT_DEEPSEEK_BASE_URL
        )
    return _normalize_chat_completions_url(
        os.getenv("LLM_BASE_URL") or DEFAULT_QWEN_BASE_URL
    )


def _provider_enabled(stage: str | None = None) -> bool:
    stage_enabled = _stage_env(stage, "ENABLED")
    if stage_enabled is not None:
        return str(stage_enabled).strip().lower() not in {"0", "false", "no", "off"}
    return _env_bool("LLM_ENABLED", True)


def _provider_thinking_enabled(stage: str | None = None) -> bool:
    stage_value = _stage_env(stage, "ENABLE_THINKING")
    if stage_value is not None:
        return str(stage_value).strip().lower() not in {"0", "false", "no", "off"}
    provider = _provider_name(stage)
    if provider != "aliyun_bailian":
        return False
    for name in ("BAILIAN_ENABLE_THINKING", "QWEN_ENABLE_THINKING", "LLM_ENABLE_THINKING"):
        if os.getenv(name) is not None:
            return _env_bool(name, True)
    return stage in _AGENT_ITEM_CACHE_STAGES


def _provider_thinking_budget(stage: str | None = None) -> int | None:
    prefix = _STAGE_PREFIXES.get(str(stage or ""))
    names = [f"{prefix}_THINKING_BUDGET"] if prefix else []
    names += ["BAILIAN_THINKING_BUDGET", "QWEN_THINKING_BUDGET", "LLM_THINKING_BUDGET"]
    for name in names:
        value = _env_int(name)
        if value:
            return value
    return None


def provider_runtime_config(stage: str | None = None) -> Dict[str, Any]:
    """Return safe runtime metadata; never return an API key."""

    url = _provider_base_url(stage)
    parsed = urllib.parse.urlparse(url)
    return {
        "provider": _provider_name(stage),
        "model": _provider_model(stage),
        "baseUrl": url,
        "endpointHost": parsed.netloc,
        "enabled": _provider_enabled(stage),
        "apiKeyConfigured": bool(_provider_api_key(stage)),
        "thinkingEnabled": _provider_thinking_enabled(stage),
        "thinkingBudget": _provider_thinking_budget(stage),
        "gatewayVersion": LLM_GATEWAY_V196_VERSION,
    }


def ensure_llm_cache_table() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_call_cache_v196 (
                cache_key TEXT PRIMARY KEY,
                stage TEXT,
                model TEXT,
                prompt_version TEXT,
                input_fingerprint TEXT,
                payload TEXT NOT NULL,
                usage TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_item_result_cache_v211 (
                cache_key TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                stable_context_hash TEXT,
                item_fingerprint TEXT NOT NULL,
                product_id TEXT,
                store_id TEXT,
                action_family TEXT,
                payload TEXT NOT NULL,
                provider_usage TEXT,
                provider_succeeded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_inference_audit_v211 (
                call_id TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                input_fingerprint TEXT NOT NULL,
                projection_version TEXT,
                source_chars INTEGER DEFAULT 0,
                projected_chars INTEGER DEFAULT 0,
                saved_chars INTEGER DEFAULT 0,
                collection_size INTEGER DEFAULT 0,
                item_cache_hits INTEGER DEFAULT 0,
                item_cache_misses INTEGER DEFAULT 0,
                local_replay INTEGER DEFAULT 0,
                provider_call_executed INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                provider_cache_hit_tokens INTEGER DEFAULT 0,
                provider_cache_miss_tokens INTEGER DEFAULT 0,
                latency_ms INTEGER DEFAULT 0,
                status TEXT,
                error TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _ensure_column(conn, "llm_item_result_cache_v211", "provider", "TEXT")
        _ensure_column(conn, "llm_inference_audit_v211", "provider", "TEXT")
        _ensure_column(conn, "llm_inference_audit_v211", "reasoning_tokens", "INTEGER DEFAULT 0")
        _ensure_column(
            conn,
            "llm_inference_audit_v211",
            "provider_cache_creation_tokens",
            "INTEGER DEFAULT 0",
        )
        _ensure_column(conn, "llm_inference_audit_v211", "provider_request_id", "TEXT")
        _ensure_column(conn, "llm_inference_audit_v211", "thinking_enabled", "INTEGER DEFAULT 0")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_item_cache_stage_product ON llm_item_result_cache_v211(stage, product_id, store_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_llm_audit_stage_created ON llm_inference_audit_v211(stage, created_at)"
        )
        conn.commit()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _cache_key(
    stage: str,
    provider: str,
    model: str,
    prompt_version: str,
    input_fingerprint: str,
) -> str:
    return _fingerprint(
        {
            "stage": stage,
            "provider": provider,
            "model": model,
            "promptVersion": prompt_version,
            "inputFingerprint": input_fingerprint,
        }
    )


def _extract_json_object(text: str) -> Dict[str, Any]:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        import re

        clean = re.sub(r"^```(?:json)?", "", clean, flags=re.IGNORECASE).strip()
        clean = re.sub(r"```$", "", clean).strip()
    try:
        data = json.loads(clean)
        return data if isinstance(data, dict) else {"value": data}
    except Exception:
        import re

        match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
        if not match:
            raise ValueError("llm_gateway_response_has_no_json_object")
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else {"value": data}


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "".join(parts).strip()
    return str(content or "").strip()


def _read_request_cache(cache_key: str) -> Tuple[Dict[str, Any], Dict[str, Any]] | None:
    ensure_llm_cache_table()
    with connect() as conn:
        row = conn.execute(
            "SELECT payload, usage FROM llm_call_cache_v196 WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if not row:
        return None
    payload = loads(row["payload"])
    usage = loads(row["usage"]) if row["usage"] else {}
    return (
        payload if isinstance(payload, dict) else {},
        usage if isinstance(usage, dict) else {},
    )


def _write_request_cache(
    cache_key: str,
    *,
    stage: str,
    model: str,
    prompt_version: str,
    input_fingerprint: str,
    payload: Dict[str, Any],
    usage: Dict[str, Any],
) -> None:
    ensure_llm_cache_table()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO llm_call_cache_v196
            (cache_key, stage, model, prompt_version, input_fingerprint, payload, usage)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cache_key,
                stage,
                model,
                prompt_version,
                input_fingerprint,
                dumps(payload),
                dumps(usage),
            ),
        )
        conn.commit()


def _item_cache_key(
    *,
    stage: str,
    provider: str,
    model: str,
    prompt_version: str,
    stable_context_hash: str,
    item: Dict[str, Any],
) -> Tuple[str, str]:
    item_fingerprint = semantic_item_fingerprint(stage, item)
    return (
        _fingerprint(
            {
                "stage": stage,
                "provider": provider,
                "model": model,
                "promptVersion": prompt_version,
                "stableContextHash": stable_context_hash,
                "itemFingerprint": item_fingerprint,
            }
        ),
        item_fingerprint,
    )


def _read_item_cache(cache_key: str) -> Dict[str, Any] | None:
    ensure_llm_cache_table()
    with connect() as conn:
        row = conn.execute(
            "SELECT payload FROM llm_item_result_cache_v211 WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    if not row:
        return None
    payload = loads(row["payload"])
    return payload if isinstance(payload, dict) else None


def _write_item_cache(
    *,
    cache_key: str,
    stage: str,
    provider: str,
    model: str,
    prompt_version: str,
    stable_context_hash: str,
    item_fingerprint: str,
    input_item: Dict[str, Any],
    output_item: Dict[str, Any],
    provider_usage: Dict[str, Any],
) -> None:
    identity = output_identity(stage, input_item)
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO llm_item_result_cache_v211
            (cache_key, stage, provider, model, prompt_version, stable_context_hash,
             item_fingerprint, product_id, store_id, action_family, payload,
             provider_usage, provider_succeeded_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                cache_key,
                stage,
                provider,
                model,
                prompt_version,
                stable_context_hash,
                item_fingerprint,
                identity.get("productId"),
                identity.get("storeId"),
                identity.get("actionFamily"),
                dumps(output_item),
                dumps(provider_usage),
            ),
        )
        conn.commit()


def _output_matches_input(stage: str, output: Dict[str, Any], item: Dict[str, Any]) -> bool:
    identity = output_identity(stage, item)
    if stage == "product_judgment_agent":
        correlation = identity.get("correlationId")
        if correlation and str(output.get("correlationId") or "") == str(correlation):
            return True
    else:
        package_id = identity.get("packageId")
        if package_id and str(output.get("packageId") or "") == str(package_id):
            return True
    return (
        str(output.get("productId") or "") == str(identity.get("productId") or "")
        and str(output.get("storeId") or "") == str(identity.get("storeId") or "")
    )


def _match_provider_outputs(
    stage: str,
    items: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
) -> List[Dict[str, Any] | None]:
    remaining = [dict(item) for item in outputs if isinstance(item, dict)]
    result: List[Dict[str, Any] | None] = []
    for input_item in items:
        match_index = next(
            (
                index
                for index, output in enumerate(remaining)
                if _output_matches_input(stage, output, input_item)
            ),
            None,
        )
        if match_index is None:
            result.append(None)
            continue
        result.append(remaining.pop(match_index))
    if len(items) == 1 and result == [None] and len(remaining) == 1:
        return [remaining[0]]
    return result


def _provider_cache_usage(usage_raw: Dict[str, Any]) -> Tuple[int, int, int]:
    details = usage_raw.get("prompt_tokens_details") or usage_raw.get("input_tokens_details")
    details = details if isinstance(details, dict) else {}
    hit = int(
        usage_raw.get("prompt_cache_hit_tokens")
        or usage_raw.get("cache_hit_tokens")
        or usage_raw.get("cache_read_input_tokens")
        or details.get("cached_tokens")
        or details.get("cache_read_input_tokens")
        or 0
    )
    creation = int(
        usage_raw.get("prompt_cache_creation_tokens")
        or usage_raw.get("cache_creation_input_tokens")
        or details.get("cache_creation_tokens")
        or details.get("cache_creation_input_tokens")
        or 0
    )
    miss = int(
        usage_raw.get("prompt_cache_miss_tokens")
        or usage_raw.get("cache_miss_tokens")
        or 0
    )
    if miss <= 0:
        prompt = int(usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens") or 0)
        miss = max(0, prompt - hit)
    return hit, miss, creation


def _reasoning_tokens(usage_raw: Dict[str, Any]) -> int:
    details = usage_raw.get("completion_tokens_details") or usage_raw.get("output_tokens_details")
    details = details if isinstance(details, dict) else {}
    return int(
        usage_raw.get("reasoning_tokens")
        or details.get("reasoning_tokens")
        or details.get("thinking_tokens")
        or 0
    )


def _audit(
    *,
    stage: str,
    provider: str,
    model: str,
    prompt_version: str,
    input_fingerprint: str,
    projection: Dict[str, Any],
    item_cache_hits: int,
    item_cache_misses: int,
    local_replay: bool,
    provider_call_executed: bool,
    usage: Dict[str, Any],
    status: str,
    error: str | None = None,
) -> None:
    ensure_llm_cache_table()
    call_id = "LLM-" + hashlib.sha1(
        f"{time.time_ns()}|{stage}|{provider}|{input_fingerprint}".encode("utf-8")
    ).hexdigest()[:24].upper()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO llm_inference_audit_v211
            (call_id, stage, provider, model, prompt_version, input_fingerprint,
             projection_version, source_chars, projected_chars, saved_chars,
             collection_size, item_cache_hits, item_cache_misses, local_replay,
             provider_call_executed, input_tokens, output_tokens, reasoning_tokens,
             provider_cache_hit_tokens, provider_cache_miss_tokens,
             provider_cache_creation_tokens, provider_request_id, thinking_enabled,
             latency_ms, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                stage,
                provider,
                model,
                prompt_version,
                input_fingerprint,
                projection.get("version"),
                int(projection.get("sourceChars") or 0),
                int(projection.get("projectedChars") or 0),
                int(projection.get("savedChars") or 0),
                int(projection.get("collectionSize") or 0),
                int(item_cache_hits),
                int(item_cache_misses),
                1 if local_replay else 0,
                1 if provider_call_executed else 0,
                int(usage.get("input") or 0),
                int(usage.get("output") or 0),
                int(usage.get("reasoningTokens") or 0),
                int(usage.get("providerCacheHitInput") or 0),
                int(usage.get("providerCacheMissInput") or 0),
                int(usage.get("providerCacheCreationInput") or 0),
                str(usage.get("providerRequestId") or "") or None,
                1 if usage.get("thinkingEnabled") else 0,
                int(usage.get("latencyMs") or 0),
                status,
                str(error or "")[:1000] or None,
            ),
        )
        conn.commit()


def _request_body(
    *,
    stage: str,
    provider: str,
    model: str,
    messages: List[Dict[str, str]],
    temperature: float,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    if provider == "aliyun_bailian":
        body["enable_thinking"] = _provider_thinking_enabled(stage)
        thinking_budget = _provider_thinking_budget(stage)
        if thinking_budget:
            body["thinking_budget"] = thinking_budget
    return body


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
    provider = _provider_name(stage)
    provider_model = _provider_model(stage, model)
    thinking_enabled = _provider_thinking_enabled(stage)
    projected_messages, semantic_payload, projection = prepare_llm_request(
        stage,
        messages,
        cache_payload,
    )
    input_fingerprint = _fingerprint(semantic_payload)
    request_key = _cache_key(
        stage,
        provider,
        provider_model,
        prompt_version,
        input_fingerprint,
    )

    if cache_enabled:
        cached_request = _read_request_cache(request_key)
        if cached_request:
            payload, original_usage = cached_request
            usage = {
                **original_usage,
                "input": 0,
                "output": 0,
                "reasoningTokens": 0,
                "cacheHit": True,
                "idempotentReplay": True,
                "providerCallExecuted": False,
                "provider": provider,
                "stage": stage,
                "model": provider_model,
                "thinkingEnabled": thinking_enabled,
                "promptVersion": prompt_version,
                "inputFingerprint": input_fingerprint,
                "gatewayVersion": LLM_GATEWAY_V196_VERSION,
                "projectionVersion": LLM_INPUT_PROJECTION_VERSION,
                "projection": projection,
            }
            _audit(
                stage=stage,
                provider=provider,
                model=provider_model,
                prompt_version=prompt_version,
                input_fingerprint=input_fingerprint,
                projection=projection,
                item_cache_hits=0,
                item_cache_misses=0,
                local_replay=True,
                provider_call_executed=False,
                usage=usage,
                status="request_cache_replay",
            )
            return payload, usage

    dynamic_payload = parse_projected_dynamic_payload(projected_messages)
    collection_key, output_key, input_items = stage_collection(stage, dynamic_payload)
    stable_context_hash = str(projection.get("stableContextHash") or "")
    item_cache_enabled = (
        stage in _AGENT_ITEM_CACHE_STAGES
        and _env_bool("LLM_ITEM_RESULT_CACHE_ENABLED", True)
        and bool(collection_key and output_key and input_items)
    )

    cached_outputs: Dict[int, Dict[str, Any]] = {}
    missing_items: List[Dict[str, Any]] = []
    missing_positions: List[int] = []
    cache_descriptors: Dict[int, Tuple[str, str]] = {}
    if item_cache_enabled:
        for position, item in enumerate(input_items):
            item_key, item_fingerprint = _item_cache_key(
                stage=stage,
                provider=provider,
                model=provider_model,
                prompt_version=prompt_version,
                stable_context_hash=stable_context_hash,
                item=item,
            )
            cache_descriptors[position] = (item_key, item_fingerprint)
            cached_item = _read_item_cache(item_key)
            if cached_item is None:
                missing_positions.append(position)
                missing_items.append(item)
            else:
                cached_outputs[position] = rebind_cached_output(stage, cached_item, item)
    else:
        missing_positions = list(range(len(input_items)))
        missing_items = list(input_items)
    initial_item_cache_hits = len(cached_outputs)

    if item_cache_enabled and input_items and not missing_items:
        payload = {
            str(output_key): [cached_outputs[index] for index in range(len(input_items))]
        }
        usage = {
            "input": 0,
            "output": 0,
            "reasoningTokens": 0,
            "cacheHit": True,
            "idempotentReplay": True,
            "providerCallExecuted": False,
            "provider": provider,
            "itemCacheHits": len(input_items),
            "itemCacheMisses": 0,
            "latencyMs": 0,
            "stage": stage,
            "model": provider_model,
            "thinkingEnabled": thinking_enabled,
            "promptVersion": prompt_version,
            "inputFingerprint": input_fingerprint,
            "gatewayVersion": LLM_GATEWAY_V196_VERSION,
            "projectionVersion": LLM_INPUT_PROJECTION_VERSION,
            "projection": projection,
        }
        _audit(
            stage=stage,
            provider=provider,
            model=provider_model,
            prompt_version=prompt_version,
            input_fingerprint=input_fingerprint,
            projection=projection,
            item_cache_hits=len(input_items),
            item_cache_misses=0,
            local_replay=True,
            provider_call_executed=False,
            usage=usage,
            status="item_cache_replay",
        )
        return payload, usage

    provider_messages = projected_messages
    if item_cache_enabled and missing_items and len(missing_items) != len(input_items):
        provider_messages = replace_projected_collection(
            projected_messages,
            str(collection_key),
            missing_items,
        )

    api_key = _provider_api_key(stage)
    if not _provider_enabled(stage):
        raise RuntimeError(f"{stage}_disabled")
    if not api_key:
        raise RuntimeError(f"missing_api_key_for_{stage}_{provider}")

    request_body = _request_body(
        stage=stage,
        provider=provider,
        model=provider_model,
        messages=provider_messages,
        temperature=temperature,
    )
    body = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    base_url = _provider_base_url(stage)
    req = urllib.request.Request(
        base_url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    start = time.time()
    provider_request_id = ""
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            raw = resp.read().decode("utf-8")
            provider_request_id = str(
                resp.headers.get("x-request-id")
                or resp.headers.get("x-dashscope-request-id")
                or ""
            )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:500]
        usage = {
            "provider": provider,
            "model": provider_model,
            "thinkingEnabled": thinking_enabled,
            "latencyMs": int((time.time() - start) * 1000),
        }
        _audit(
            stage=stage,
            provider=provider,
            model=provider_model,
            prompt_version=prompt_version,
            input_fingerprint=input_fingerprint,
            projection=projection,
            item_cache_hits=initial_item_cache_hits,
            item_cache_misses=len(missing_items),
            local_replay=False,
            provider_call_executed=True,
            usage=usage,
            status="provider_http_error",
            error=f"HTTP {exc.code}: {detail}",
        )
        raise RuntimeError(
            f"llm_gateway_provider_http_{provider}_{exc.code}:{detail}"
        ) from exc
    except urllib.error.URLError as exc:
        usage = {
            "provider": provider,
            "model": provider_model,
            "thinkingEnabled": thinking_enabled,
            "latencyMs": int((time.time() - start) * 1000),
        }
        _audit(
            stage=stage,
            provider=provider,
            model=provider_model,
            prompt_version=prompt_version,
            input_fingerprint=input_fingerprint,
            projection=projection,
            item_cache_hits=initial_item_cache_hits,
            item_cache_misses=len(missing_items),
            local_replay=False,
            provider_call_executed=True,
            usage=usage,
            status="provider_network_error",
            error=str(exc.reason)[:500],
        )
        raise RuntimeError(f"llm_gateway_provider_network_{provider}:{exc.reason}") from exc

    data = json.loads(raw)
    provider_request_id = provider_request_id or str(data.get("id") or "")
    usage_raw = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    choices = data.get("choices") if isinstance(data.get("choices"), list) else []
    message = (choices[0].get("message") if choices and isinstance(choices[0], dict) else {}) or {}
    content = _message_text(message if isinstance(message, dict) else {})
    if not content:
        raise ValueError("llm_gateway_response_empty_content")
    provider_payload = _extract_json_object(content)
    provider_cache_hit, provider_cache_miss, provider_cache_creation = _provider_cache_usage(
        usage_raw
    )
    usage: Dict[str, Any] = {
        "input": int(usage_raw.get("prompt_tokens") or usage_raw.get("input_tokens") or 0),
        "output": int(usage_raw.get("completion_tokens") or usage_raw.get("output_tokens") or 0),
        "reasoningTokens": _reasoning_tokens(usage_raw),
        "providerCacheHitInput": provider_cache_hit,
        "providerCacheMissInput": provider_cache_miss,
        "providerCacheCreationInput": provider_cache_creation,
        "cacheHit": False,
        "idempotentReplay": False,
        "providerCallExecuted": True,
        "provider": provider,
        "providerRequestId": provider_request_id,
        "itemCacheHits": initial_item_cache_hits,
        "itemCacheMisses": len(missing_items),
        "latencyMs": int((time.time() - start) * 1000),
        "stage": stage,
        "model": provider_model,
        "thinkingEnabled": thinking_enabled,
        "thinkingBudget": _provider_thinking_budget(stage),
        "promptVersion": prompt_version,
        "inputFingerprint": input_fingerprint,
        "gatewayVersion": LLM_GATEWAY_V196_VERSION,
        "projectionVersion": LLM_INPUT_PROJECTION_VERSION,
        "projection": projection,
    }

    payload = provider_payload
    if item_cache_enabled and collection_key and output_key:
        fresh_outputs = provider_payload.get(output_key)
        fresh_outputs = fresh_outputs if isinstance(fresh_outputs, list) else []
        matched = _match_provider_outputs(stage, missing_items, fresh_outputs)
        for relative_index, output_item in enumerate(matched):
            if not isinstance(output_item, dict):
                continue
            original_position = missing_positions[relative_index]
            input_item = input_items[original_position]
            rebound = rebind_cached_output(stage, output_item, input_item)
            cached_outputs[original_position] = rebound
            item_key, item_fingerprint = cache_descriptors[original_position]
            _write_item_cache(
                cache_key=item_key,
                stage=stage,
                provider=provider,
                model=provider_model,
                prompt_version=prompt_version,
                stable_context_hash=stable_context_hash,
                item_fingerprint=item_fingerprint,
                input_item=input_item,
                output_item=output_item,
                provider_usage=usage,
            )
        payload = {
            **provider_payload,
            str(output_key): [
                cached_outputs[index]
                for index in range(len(input_items))
                if index in cached_outputs
            ],
        }

    if cache_enabled:
        _write_request_cache(
            request_key,
            stage=stage,
            model=provider_model,
            prompt_version=prompt_version,
            input_fingerprint=input_fingerprint,
            payload=payload,
            usage=usage,
        )

    _audit(
        stage=stage,
        provider=provider,
        model=provider_model,
        prompt_version=prompt_version,
        input_fingerprint=input_fingerprint,
        projection=projection,
        item_cache_hits=initial_item_cache_hits,
        item_cache_misses=len(missing_items),
        local_replay=False,
        provider_call_executed=True,
        usage=usage,
        status="provider_succeeded",
    )
    return payload, usage
