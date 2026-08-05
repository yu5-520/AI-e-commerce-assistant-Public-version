"""Legacy LLM provider wrapper.

V19.7 removes this file's direct HTTP provider route. Compatibility callers are
forwarded to llm_gateway_v196_service.call_json so the repository has one real
DeepSeek gateway.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from src.services.llm_gateway_v196_service import LLM_GATEWAY_V196_VERSION, call_json

LLM_GATEWAY_VERSION = "LEGACY_WRAPPER_TO_19.6"


def current_llm_config() -> Dict[str, Any]:
    return {"version": LLM_GATEWAY_VERSION, "enabled": True, "mockMode": False, "traceEnabled": False, "providerName": "deepseek", "providerType": "openai_compatible", "baseUrlConfigured": True, "apiKeyConfigured": bool(os.getenv("DEEPSEEK_API_KEY") or os.getenv("LLM_API_KEY")), "model": os.getenv("DEEPSEEK_MODEL") or os.getenv("LLM_MODEL") or "deepseek-v4-pro", "boundary": "V19.7 compatibility wrapper; real calls go through llm_gateway_v196_service."}


def llm_status() -> Dict[str, Any]:
    config = current_llm_config()
    return {**config, "ready": bool(config.get("apiKeyConfigured")), "realGateway": "llm_gateway_v196_service", "gatewayVersion": LLM_GATEWAY_V196_VERSION}


def generate_json(*, prompt_name: str, payload: Dict[str, Any], expected_keys: List[str] | None = None, agent_name: str = "LLM Gateway", schema_name: str = "generic_json") -> Dict[str, Any]:
    messages = [{"role": "system", "content": f"请作为{agent_name}执行{prompt_name}，只输出严格JSON。"}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))}]
    output, usage = call_json(stage=str(prompt_name or "legacy_llm_provider"), prompt_version="legacy_wrapper_v19.7", messages=messages, temperature=float(os.getenv("LLM_TEMPERATURE", "0.2") or 0.2), timeout_seconds=int(float(os.getenv("LLM_TIMEOUT", "120") or 120)), cache_payload={"promptName": prompt_name, "schemaName": schema_name, "payload": payload})
    if expected_keys:
        for key in expected_keys:
            output.setdefault(key, None)
    return {"version": LLM_GATEWAY_VERSION, "enabled": True, "provider": "deepseek", "model": usage.get("model"), "status": "success", "fallbackUsed": False, "latencyMs": usage.get("latencyMs", 0), "trace": {"gatewayVersion": usage.get("gatewayVersion"), "cacheHit": usage.get("cacheHit"), "inputFingerprint": usage.get("inputFingerprint")}, "output": output, "boundary": "V19.7 compatibility wrapper; no direct HTTP route remains here."}
