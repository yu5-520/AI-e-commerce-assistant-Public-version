from __future__ import annotations

import json
import os

from core import sha256_json
from manifest_contract import freeze_manifest
from provider_adapter_http import OpenAICompatibleChatAdapter
from provider_binding import validate_provider_binding


ENDPOINT = "https://example.invalid/v1/chat/completions"
SECRET_ENV = "RB_SYNTHETIC_PROVIDER_BINDING_SECRET"
REQUEST_OPTIONS = {"thinking": {"type": "disabled"}}


def run() -> dict:
    adapter = OpenAICompatibleChatAdapter(
        endpoint=ENDPOINT,
        api_key_env=SECRET_ENV,
        model_id="synthetic-model-for-binding-smoke",
        model_version="binding-smoke-v1",
        decoding={"temperature": 0.0, "top_p": 1.0, "max_output_tokens": 256},
        provider="synthetic-provider-for-binding-smoke",
        execute_enabled=False,
        request_options=json.loads(json.dumps(REQUEST_OPTIONS)),
    )
    fragment = adapter.manifest_fragment()
    manifest = freeze_manifest(
        {
            "experiment_id": "provider-binding-smoke",
            "research_commit": "a" * 40,
            "sut_commit": "b" * 40,
            "provider": adapter.provider,
            "model_id": adapter.model_id,
            "model_version": adapter.model_version,
            "adapter_version": adapter.adapter_version,
            "proposal_protocol_version": fragment["proposal_protocol_version"],
            "endpoint_hash": sha256_json({"endpoint": ENDPOINT}),
            "decoding": dict(adapter.decoding),
            "request_options": json.loads(json.dumps(REQUEST_OPTIONS)),
            "budget": {"max_cost": 0.0, "max_tokens": 1},
            "conditions": ["baseline_runtime"],
        },
        require_concrete_provider=True,
    )
    prior = os.environ.get(SECRET_ENV)
    os.environ[SECRET_ENV] = "synthetic-secret-not-used-for-network"
    try:
        receipt = validate_provider_binding(
            frozen_manifest=manifest,
            adapter=adapter,
            require_secret=True,
        )
    finally:
        if prior is None:
            os.environ.pop(SECRET_ENV, None)
        else:
            os.environ[SECRET_ENV] = prior
    assert receipt["network_request_made"] is False
    assert receipt["paid_model_calls"] == 0
    assert receipt["request_options"] == REQUEST_OPTIONS
    return {
        "provider_binding_contract": "PASS",
        "manifest_hash": receipt["manifest_hash"],
        "endpoint_hash": receipt["endpoint_hash"],
        "proposal_protocol_version": receipt["proposal_protocol_version"],
        "request_options": receipt["request_options"],
        "secret_presence_check": "PASS",
        "network_request_made": False,
        "paid_model_calls": 0,
        "note": "Synthetic contract smoke only; does not satisfy the real provider_adapter_bound Stage A prerequisite.",
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2, sort_keys=True))
