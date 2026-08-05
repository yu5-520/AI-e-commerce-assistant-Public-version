from __future__ import annotations

from pathlib import Path

from src.services import agent2_provenance_v2141_service as provenance
from src.services import agent_token_runtime_v225_service as runtime
from src.services.agent_input_contract_v225_service import (
    AGENT2_DRAFT_INPUT_SCHEMA,
    build_projection_envelope,
)

ROOT = Path(__file__).resolve().parents[1]
RECOVERY_SCRIPT = ROOT / "scripts" / "recover_agent2_request_cache_identity.py"


def _envelope(package_id: str = "PKG-CURRENT") -> dict:
    payload = {
        "packageId": package_id,
        "itemId": "PI-CURRENT",
        "dataVersion": "DV-CURRENT",
        "productId": "P10008",
        "storeId": "DY-SH-003",
        "productTitle": "厨房多功能收纳架",
        "productIdentity": {
            "productId": "P10008",
            "storeId": "DY-SH-003",
            "productTitle": "厨房多功能收纳架",
        },
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "actionFamilyLock": {
                "selectedActionFamily": "conversion_repair",
                "lockedByAgent1": True,
            },
        },
        "matrixDispatch": {
            "selectedActionFamily": "conversion_repair",
            "lockedByAgent1": True,
        },
        "lockedActionFamily": "conversion_repair",
        "actionParameterPack": {
            "permissionBounds": {
                "operatorCanExecute": True,
                "managerApprovalRequired": False,
            },
        },
        "inputContract": {
            "schema": AGENT2_DRAFT_INPUT_SCHEMA,
            "fallbackAllowed": False,
        },
    }
    return build_projection_envelope(
        schema=AGENT2_DRAFT_INPUT_SCHEMA,
        payload=payload,
        source_artifact_refs=["ART-TEST-CAPABILITY"],
        source_content_hash="source-hash",
    )


def _proof(package_id: str) -> dict:
    return {
        "packageId": package_id,
        "semanticCallId": "A2CALL-CURRENT",
        "provider": "aliyun_bailian",
        "model": "qwen3.7-plus",
        "providerRequestId": "REQ-CURRENT",
        "providerCallExecuted": True,
        "exactReplayValidated": False,
        "itemCorrelationId": package_id,
        "resultMatched": True,
        "resultOrigin": "provider_call",
        "inputFingerprint": "fingerprint-current",
        "fallbackUsed": False,
        "passed": True,
    }


def test_agent2_disables_request_cache_but_keeps_current_identity(monkeypatch) -> None:
    captured: dict = {}
    package_id = "PKG-CURRENT"
    proof = _proof(package_id)

    def fake_call_json_with_item_provenance(**kwargs):
        captured.update(kwargs)
        return (
            {
                "plans": [
                    {
                        "packageId": package_id,
                        "productId": "P10008",
                        "storeId": "DY-SH-003",
                        "actionFamily": "conversion_repair",
                        "draftStatus": "draft_missing_data",
                        "problemNode": "退款原因待补充",
                        "actionIntent": "先补齐退款与质检证据",
                        "missingData": ["退款原因明细"],
                    }
                ]
            },
            {
                "providerCallExecuted": True,
                "providerRequestId": "REQ-CURRENT",
                "provider": "aliyun_bailian",
                "model": "qwen3.7-plus",
                "inputFingerprint": "fingerprint-current",
            },
        )

    def fake_provider_summary(_usage):
        return {
            "providerStatus": "ok",
            "provider": "aliyun_bailian",
            "model": "qwen3.7-plus",
            "actualCalls": 1,
            "cacheHits": 0,
            "inputTokens": 10,
            "outputTokens": 10,
            "reasoningTokens": 0,
            "itemProvenance": {package_id: proof},
        }

    monkeypatch.setattr(
        provenance,
        "call_json_with_item_provenance",
        fake_call_json_with_item_provenance,
    )
    monkeypatch.setattr(provenance, "provider_summary", fake_provider_summary)
    monkeypatch.setattr(
        provenance,
        "proof_for_package",
        lambda _summary, selected: proof if selected == package_id else {},
    )

    drafts, provider = runtime.run_agent2_draft_projected_inputs(
        [_envelope(package_id)],
        data_version="DV-CURRENT",
        max_items_per_call=5,
    )

    assert captured["cache_enabled"] is False
    assert drafts[package_id]["packageId"] == package_id
    assert drafts[package_id]["productId"] == "P10008"
    assert provider["requestCacheEnabled"] is False
    assert provider["itemResultCacheEnabled"] is True
    assert provider["requestCacheIdentityHotfixVersion"] == "22.5.3"


def test_recovery_is_exact_dead_letter_only_and_apply_guarded() -> None:
    source = RECOVERY_SCRIPT.read_text(encoding="utf-8")

    assert "agent2_dead_letter" in source
    assert "agent2_draft_returned_no_plan" in source
    assert "status='failed'" in source
    assert 'parser.add_argument("--data-version", required=True)' in source
    assert 'parser.add_argument("--item-id")' in source
    assert '"--apply"' in source
    assert "agent1Rerun" in source
    assert "observedItemsTouched" in source
    assert "requestCacheDeleted" in source


def test_runtime_exports_request_cache_identity_hotfix() -> None:
    assert runtime.AGENT2_REQUEST_CACHE_IDENTITY_HOTFIX_VERSION == "22.5.3"
    policy = runtime._agent2_cache_policy()
    assert policy["requestCacheEnabled"] is False
    assert policy["itemResultCacheEnabled"] is True
