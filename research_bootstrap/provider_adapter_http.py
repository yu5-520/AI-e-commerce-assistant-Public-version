from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict

from core import sha256_json
from model_adapter import assert_model_view_clean


class PaidExecutionDisabled(RuntimeError):
    pass


class ProviderAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class OpenAICompatibleChatAdapter:
    """Concrete HTTP adapter for OpenAI-compatible chat-completions endpoints.

    Network execution is fail-closed by default. Merely constructing the adapter never
    performs a paid request. The caller must set execute_enabled=True explicitly and
    provide the configured API key environment variable.
    """

    endpoint: str
    api_key_env: str
    model_id: str
    model_version: str
    decoding: Dict[str, Any]
    provider: str
    execute_enabled: bool = False
    adapter_version: str = "openai-compatible-http-v1"
    timeout_seconds: int = 90
    extra_headers: Dict[str, str] = field(default_factory=dict)

    def manifest_fragment(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "adapter_version": self.adapter_version,
            "decoding": dict(self.decoding),
            "endpoint_hash": sha256_json({"endpoint": self.endpoint}),
            "paid_execution_enabled": bool(self.execute_enabled),
        }

    def generate_neutral(self, model_view: Dict[str, Any]) -> Dict[str, Any]:
        assert_model_view_clean(model_view)
        if not self.execute_enabled:
            raise PaidExecutionDisabled("paid_model_execution_disabled")
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise PaidExecutionDisabled(f"provider_secret_missing:{self.api_key_env}")
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return one JSON object only. Use key 'effects' containing a list. "
                        "Represent only actions or state changes you actually propose. "
                        "Do not infer hidden evaluation labels or experimental conditions."
                    ),
                },
                {"role": "user", "content": json.dumps(model_view, ensure_ascii=False, sort_keys=True)},
            ],
            "temperature": self.decoding["temperature"],
            "top_p": self.decoding["top_p"],
            "max_tokens": self.decoding["max_output_tokens"],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **self.extra_headers,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderAdapterError(f"provider_request_failed:{type(exc).__name__}") from exc
        try:
            body = json.loads(raw)
            content = body["choices"][0]["message"]["content"]
            structured = json.loads(content) if isinstance(content, str) else content
        except Exception as exc:
            raise ProviderAdapterError("provider_response_invalid") from exc
        if not isinstance(structured, dict) or not isinstance(structured.get("effects"), list):
            raise ProviderAdapterError("provider_structured_output_invalid")
        usage = body.get("usage") or {}
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "adapter_version": self.adapter_version,
            "input_hash": sha256_json(model_view),
            "structured_output": structured,
            "usage": {
                "input_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                "output_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
            },
            "raw_response_hash": sha256_json(body),
            "raw_response_ref": None,
        }
