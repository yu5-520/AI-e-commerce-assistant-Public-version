from __future__ import annotations

from pathlib import Path

import pytest

from src.services import real_product_judgment_agent_v2259_service as agent1_core
from src.services.frontend_view_artifact_v2259_service import stable_view_payload
from src.services.hash_directed_artifact_runtime_v2259_service import (
    build_execution_descriptor,
)
from src.services.llm_input_projection_v2259_service import (
    prepare_llm_request,
    rebind_cached_output,
    stage_collection,
)

ROOT = Path(__file__).resolve().parents[1]


def _product(item_id: str = "EXE-001", input_hash: str = "sha256:input-1") -> dict:
    return {
        "productId": "P10001",
        "storeId": "S001",
        "signalId": "SIG-001",
        "correlationId": "S001:P10001:SIG-001",
        "_hashExecution": {
            "itemExecutionId": item_id,
            "executionHash": "execution-1",
            "inputArtifactRef": "ART-INPUT-001",
            "inputContentHash": input_hash,
            "inputSchema": "agent_input.agent1.v3",
            "projectionVersion": "22.5.8",
        },
    }


def _patch_business_normalizer(monkeypatch: pytest.MonkeyPatch) -> None:
    def normalize(payload: dict, _source_maps: dict, _data_version: str | None):
        return [dict(item) for item in payload.get("judgments") or []], {
            "unmatchedProviderJudgmentCount": 0,
        }

    monkeypatch.setattr(agent1_core.legacy, "_normalize_judgments", normalize)
    monkeypatch.setattr(agent1_core.legacy, "_source_maps", lambda products: {"count": len(products)})


def test_agent1_requires_exact_execution_id_and_nonempty_matching_input_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_business_normalizer(monkeypatch)
    product = _product()

    accepted, diagnostics = agent1_core._normalize_judgments(
        {
            "judgments": [
                {
                    "itemExecutionId": "EXE-001",
                    "inputContentHash": "sha256:input-1",
                    "decisionType": "observe",
                }
            ]
        },
        [product],
        "DV-001",
    )
    assert len(accepted) == 1
    assert accepted[0]["itemExecutionId"] == "EXE-001"
    assert accepted[0]["inputContentHash"] == "sha256:input-1"
    assert accepted[0]["hashIdentityMatched"] is True
    assert accepted[0]["fallbackIdentityMatchingUsed"] is False
    assert diagnostics["missingItemExecutionIds"] == []
    assert diagnostics["exactHashMatchedCount"] == 1

    missing_hash, diagnostics = agent1_core._normalize_judgments(
        {
            "judgments": [
                {
                    "itemExecutionId": "EXE-001",
                    "decisionType": "observe",
                }
            ]
        },
        [product],
        "DV-001",
    )
    assert missing_hash == []
    assert diagnostics["missingItemExecutionIds"] == ["EXE-001"]
    assert diagnostics["inputContentHashMismatches"][0]["returnedInputContentHash"] is None

    wrong_hash, diagnostics = agent1_core._normalize_judgments(
        {
            "judgments": [
                {
                    "itemExecutionId": "EXE-001",
                    "inputContentHash": "sha256:old-input",
                    "decisionType": "observe",
                }
            ]
        },
        [product],
        "DV-001",
    )
    assert wrong_hash == []
    assert diagnostics["missingItemExecutionIds"] == ["EXE-001"]
    assert diagnostics["inputContentHashMismatches"][0]["expectedInputContentHash"] == "sha256:input-1"


def test_agent1_duplicate_or_extra_execution_identity_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_business_normalizer(monkeypatch)
    product = _product()
    accepted, diagnostics = agent1_core._normalize_judgments(
        {
            "judgments": [
                {
                    "itemExecutionId": "EXE-001",
                    "inputContentHash": "sha256:input-1",
                    "decisionType": "observe",
                },
                {
                    "itemExecutionId": "EXE-001",
                    "inputContentHash": "sha256:input-1",
                    "decisionType": "observe",
                },
                {
                    "itemExecutionId": "EXE-UNKNOWN",
                    "inputContentHash": "sha256:unknown",
                    "decisionType": "observe",
                },
            ]
        },
        [product],
        "DV-001",
    )
    assert accepted == []
    assert diagnostics["missingItemExecutionIds"] == ["EXE-001"]
    assert diagnostics["duplicateItemExecutionIds"] == ["EXE-001"]
    assert diagnostics["extraItemExecutionIds"] == ["EXE-UNKNOWN"]


def test_execution_hash_changes_with_exact_input_artifact_content_hash() -> None:
    common = {
        "stage": "product_judgment_agent",
        "input_schema": "agent_input.agent1.v3",
        "projection_version": "22.5.8",
        "prompt_version": "22.5.9",
        "policy_hash": "policy-hash",
        "provider": "aliyun_bailian",
        "model": "qwen3.7-plus",
        "generation_parameters": {"temperature": 0.08},
    }
    first = build_execution_descriptor(
        binding={
            "inputArtifactRef": "ART-A",
            "inputContentHash": "sha256:A",
            "productId": "P1",
            "storeId": "S1",
        },
        **common,
    )
    same = build_execution_descriptor(
        binding={
            "inputArtifactRef": "ART-A",
            "inputContentHash": "sha256:A",
            "productId": "P1",
            "storeId": "S1",
        },
        **common,
    )
    changed = build_execution_descriptor(
        binding={
            "inputArtifactRef": "ART-B",
            "inputContentHash": "sha256:B",
            "productId": "P1",
            "storeId": "S1",
        },
        **common,
    )
    assert first["executionHash"] == same["executionHash"]
    assert first["itemExecutionId"] == same["itemExecutionId"]
    assert first["executionHash"] != changed["executionHash"]


def test_pre_materialized_hash_input_disables_second_projection_and_item_cache() -> None:
    messages = [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": (
                '{"_hashDirectedExecution":true,'
                '"artifactBatchManifest":{"batchExecutionId":"B1"},'
                '"products":[{"itemExecutionId":"E1",'
                '"inputContentHash":"H1","dataVersion":"DV1"}]}'
            ),
        },
    ]
    projected, semantic, audit = prepare_llm_request(
        "product_judgment_agent",
        messages,
        cache_payload=None,
    )
    assert projected == messages
    assert semantic["hashDirectedExecution"] is True
    assert audit["applied"] is False
    assert audit["projectionMode"] == "pre_materialized_artifact_passthrough"
    assert audit["itemCacheDisabledByArtifactContract"] is True
    assert stage_collection(
        "product_judgment_agent",
        {
            "_hashDirectedExecution": True,
            "products": [{"itemExecutionId": "E1"}],
        },
    ) == (None, None, [])


def test_cached_output_cannot_be_rebound_to_new_hash_identity() -> None:
    exact = {
        "itemExecutionId": "E1",
        "inputContentHash": "H1",
        "productId": "P1",
        "storeId": "S1",
    }
    result = rebind_cached_output("product_judgment_agent", exact, exact)
    assert result["cachedOutputRebound"] is False
    assert result["cacheIdentityVerified"] is True

    with pytest.raises(ValueError, match="cached_output_exact_identity_mismatch"):
        rebind_cached_output(
            "product_judgment_agent",
            exact,
            {**exact, "inputContentHash": "H2"},
        )


def test_frontend_view_hash_ignores_transport_time_but_keeps_data_version() -> None:
    first = stable_view_payload(
        {
            "dataVersion": "DV-NEW",
            "items": [{"productId": "P1", "value": 10, "updatedAt": "T1"}],
            "cachedAt": "T1",
        }
    )
    second = stable_view_payload(
        {
            "dataVersion": "DV-NEW",
            "items": [{"productId": "P1", "value": 10, "updatedAt": "T2"}],
            "cachedAt": "T2",
        }
    )
    old_version = stable_view_payload(
        {
            "dataVersion": "DV-OLD",
            "items": [{"productId": "P1", "value": 10, "updatedAt": "T2"}],
        }
    )
    assert first == second
    assert first != old_version


def test_runtime_source_seals_true_missing_retry_and_incremental_frontend() -> None:
    runtime = (
        ROOT / "src/services/agent_token_runtime_hash_exact_v2259_service.py"
    ).read_text(encoding="utf-8")
    gateway = (
        ROOT / "src/services/llm_gateway_hash_directed_v2259_service.py"
    ).read_text(encoding="utf-8")
    route = (ROOT / "src/api/routes/frontend_views.py").read_text(encoding="utf-8")
    client = (ROOT / "web_demo/core/hash-view-client-v2259.js").read_text(
        encoding="utf-8"
    )
    assert "if item_execution_id in raw_returned" in runtime
    assert "Raw output exists" in runtime
    assert "singleton_true_missing_hash" in runtime
    assert "call_json_exact_artifact" in runtime
    assert "prepare_llm_request" not in gateway
    assert '"itemResultCacheEnabled": False' in gateway
    assert '@router.get("/head/{view_key}")' in route
    assert '@router.get("/artifacts/{artifact_ref}")' in route
    assert "if (cached !== null) return cached" in client
    assert "manifest?.modules?.[moduleKey]" in client
    assert "localStorage.setItem(storageKey(hash)" in client
