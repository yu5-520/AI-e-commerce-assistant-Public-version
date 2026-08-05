from __future__ import annotations

from pathlib import Path

from src.services.agent2_action_plan_core_v20_service import (
    metric_digest_for_family,
)
from src.services.agent_input_contract_v225_service import (
    AGENT2_MAX_ITEM_CHARS,
    stable_json,
    validate_agent_input_envelope,
)
from src.services.agent_input_transport_v22514_service import (
    AGENT2_EVIDENCE_SLICE_VERSION,
    AGENT_INPUT_TRANSPORT_VERSION,
    compile_agent2_draft_envelope,
)
from src.services.station_agent_worker_v2259_service import (
    STATION_AGENT_WORKER_VERSION,
    worker_config,
)

ROOT = Path(__file__).resolve().parents[1]


def _source(family: str = "title_image_test") -> dict:
    return {
        "packageId": "PKG-001",
        "itemId": "PI-001",
        "dataVersion": "DV-TEST",
        "productId": "P10009",
        "storeId": "JD-SH-002",
        "productTitle": "测试商品",
        "decisionType": "act",
        "selectedOperatingRoute": family,
        "selectedActionFamilyHint": family,
        "primaryProblemNode": "曝光稳定但点击率连续下降",
        "primaryAction": "执行标题主图低风险测试",
        "primaryOwner": "运营",
        "primaryExecutionTarget": {
            "targetType": "product_creative_asset",
            "targetId": "P10009",
        },
        "decisiveFacts": [
            "最近三期曝光稳定增长",
            "最近三期点击率连续下降",
            "点击后转化率保持稳定",
        ],
        "evidenceStatus": "sufficient",
        "lockedActionFamily": family,
        "actionFamily": family,
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "decisionSummary": "点击前素材层需要测试",
            "selectedOperatingRoute": family,
            "selectedActionFamilyHint": family,
            "primaryProblemNode": "曝光稳定但点击率连续下降",
            "primaryAction": "执行标题主图低风险测试",
            "primaryOwner": "运营",
            "primaryExecutionTarget": {
                "targetType": "product_creative_asset",
                "targetId": "P10009",
            },
            "decisiveFacts": [
                "最近三期曝光稳定增长",
                "最近三期点击率连续下降",
            ],
        },
        "agent1DecisionIR": {
            "decisionType": "act",
            "decisionSummary": "点击前素材层需要测试",
        },
        "matrixDispatch": {
            "selectedOperatingRoute": family,
            "selectedActionFamily": family,
            "selectedOwner": "运营",
        },
        "actionParameterPack": {
            "permissionBounds": {"operatorAllowed": True},
            "parameterBounds": {"groupCount": {"min": 2, "max": 5}},
            "ctrCurrent": 0.021,
            "ctrBaseline": 0.031,
            "impressionsCurrent": 87000,
            "gmvCurrent": 100000,
            "unrelatedFullMetricEvidence": "x" * 9000,
            "ragContextSummary": {"large": "y" * 9000},
        },
        "recentFiveOrLatestFacts": [
            {"metric": "ctr", "values": [0.031, 0.027, 0.021], "direction": "down"},
            {"metric": "impressions", "values": [82000, 85000, 87000], "direction": "up"},
            {"metric": "conversion_rate", "values": [0.041, 0.040, 0.040], "direction": "stable"},
            {"metric": "refund_rate", "values": [0.01, 0.01, 0.01], "direction": "stable"},
        ],
        "sourceLineageValidation": {
            "sourceIdentityComplete": True,
            "status": "complete",
            "sourceVersionCount": 3,
            "sourceDatasetCount": 3,
            "businessDateCount": 3,
            "businessDates": ["2026-07-20", "2026-07-23", "2026-07-26"],
            "contentHashVerified": True,
            "rawRows": ["z" * 4000] * 6,
        },
        "rawAgent1Judgment": {"full": "r" * 12000},
        "recoveredAgent1Judgment": {"full": "q" * 12000},
        "systemFacts": {"fullReport": "s" * 12000},
        "signalEvidence": {"fullReport": "e" * 12000},
    }


def test_agent2_projection_is_an_action_evidence_slice() -> None:
    envelope = compile_agent2_draft_envelope(
        _source(),
        source_ref="ART-SOURCE-001",
        source_content_hash="sha256:test",
    )
    validation = validate_agent_input_envelope(envelope)
    assert validation["ok"] is True
    payload = envelope["payload"]
    serialized = stable_json(payload)
    assert len(serialized) <= AGENT2_MAX_ITEM_CHARS
    assert payload["inputContract"]["transportVersion"] == "22.5.14"
    assert payload["inputContract"]["evidenceSliceVersion"] == "22.5.14"
    assert payload["inputContract"]["fullReportReadAllowed"] is False
    assert payload["inputContract"]["rawAgent1OutputReadAllowed"] is False
    assert payload["agent1DecisionIR"]["evidenceSlice"]["fullReportExcluded"] is True
    assert "rawAgent1Judgment" not in serialized
    assert "recoveredAgent1Judgment" not in serialized
    assert "unrelatedFullMetricEvidence" not in serialized
    assert "fullReport" not in serialized
    assert envelope["projectionAudit"]["lineageReferenceOnly"] is True


def test_existing_agent2_core_consumes_the_compact_metrics() -> None:
    envelope = compile_agent2_draft_envelope(
        _source(),
        source_ref="ART-SOURCE-002",
        source_content_hash="sha256:test2",
    )
    digest = metric_digest_for_family(
        envelope["payload"],
        "title_image_test",
    )
    assert digest["actionFamily"] == "title_image_test"
    assert digest["recentFacts"]
    assert digest["permissionBounds"] == {"operatorAllowed": True}
    assert digest["fullMetricEvidenceExcluded"] is True


def test_active_worker_routes_to_v22514_runtime() -> None:
    assert STATION_AGENT_WORKER_VERSION == "22.5.14"
    config = worker_config()
    assert config["hardAgentRuntimeVersion"] == "22.5.14"
    assert config["agent2EvidenceSliceVersion"] == "22.5.14"
    assert config["agent2ReceivesActionEvidenceSliceOnly"] is True
    assert config["fullReportReadByAgent2Allowed"] is False


def test_runtime_wires_agent2_stale_recovery_before_selection() -> None:
    source = (
        ROOT
        / "src/services/agent_runtime_hard_interface_v22514_service.py"
    ).read_text(encoding="utf-8")
    assert "recover_stale_agent2_claims" in source
    assert "_recover_agent2(None)" in source
    assert "before_selection_and_startup" in source
    assert "pending_agent2_item_count" in source
    assert "run_agent2_draft_microbatch_hard" in source


def test_projection_recovery_includes_the_real_failure_stage() -> None:
    source = (
        ROOT
        / "src/services/agent2_runtime_v22514_service.py"
    ).read_text(encoding="utf-8")
    assert '"agent2_draft_input_invalid"' in source
    assert "projection_item_budget_exceeded" in source
    assert "current_stage='action_pack_ready',status='retry'" in source
    assert '"providerCallsExecuted": 0' in source
    assert AGENT_INPUT_TRANSPORT_VERSION == "22.5.14"
    assert AGENT2_EVIDENCE_SLICE_VERSION == "22.5.14"
