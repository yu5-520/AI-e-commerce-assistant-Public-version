from __future__ import annotations

from pathlib import Path

from src.services.agent_input_contract_v2257_service import (
    AGENT1_INPUT_PROJECTION_VERSION,
    AGENT1_INPUT_SCHEMA,
    assert_agent_input_envelope,
)
from src.services.agent_input_transport_v2257_service import compile_agent1_envelope
from src.services.real_product_judgment_agent_v2257_service import _fact_card


ROOT = Path(__file__).resolve().parents[1]


def _source() -> dict:
    signals = []
    metric_codes = [
        "paymentAmount",
        "roi",
        "adSpend",
        "grossMargin",
        "organicVisitors",
        "paidVisitors",
        "clickRate",
        "conversionRate",
        "refundRate",
        "inventory",
        "availableDays",
        "orderCount",
        "avgOrderValue",
        "refundAmount",
        "visitorCount",
        "paidOrderCount",
        "gmv",
    ]
    for index, code in enumerate(metric_codes):
        signals.append(
            {
                "metricCode": code,
                "previous": 100 + index,
                "current": 90 + index,
                "latest": 90 + index,
                "changeRatio": -0.1,
                "changeRate": -0.1,
                "changeVsPrevious": -10,
                "meaningfulChange": True,
                "signalStrength": "strong",
                "signalType": "continuous_decline",
                "windows": {
                    "recent3": {"direction": "down", "sampleCount": 3},
                    "recent5": {"direction": "down", "sampleCount": 5},
                },
                "reason": f"{code}连续下降",
            }
        )
    return {
        "productId": "P10001",
        "storeId": "S001",
        "signalId": "SIG-001",
        "dataVersion": "DV-001",
        "profileLayer": {
            "productId": "P10001",
            "storeId": "S001",
            "productTitle": "测试商品",
            "platform": "京东",
        },
        "snapshotLayer": {"fieldSignals": signals},
        "crossValidation": {
            "sourceVersionCount": 3,
            "changedMetricCount": 17,
            "abnormalMetricCount": 17,
        },
        "factLayerValidation": {
            "status": "passed",
            "metricCompleteness": 1.0,
        },
        "strongRelations": [
            {"resultMetric": "paymentAmount", "causeMetric": "conversionRate"}
        ],
        "agentProductSnapshotPackage": {
            "version": "snapshot.package.v1",
            "timeSeriesFeatures": {
                "paymentAmount": {
                    "slope5": -0.08,
                    "streakDirection": "down",
                    "streakLength": 3,
                    "volatility10": 0.12,
                },
                "conversionRate": {
                    "slope5": -0.04,
                    "streakDirection": "down",
                    "streakLength": 3,
                },
            },
            "operatingDecision": {
                "primaryEvidence": {
                    "metricCode": "paymentAmount",
                    "streakDirection": "down",
                    "streakLength": 3,
                    "volatility10": 0.12,
                },
                "relatedEvidence": [
                    {
                        "metricCode": "conversionRate",
                        "streakDirection": "down",
                        "streakLength": 3,
                    }
                ],
                "confidence": 0.88,
            },
        },
    }


def test_agent1_v2_projection_preserves_all_signals_and_trends() -> None:
    envelope = compile_agent1_envelope(
        _source(),
        source_ref="ART-SIGNAL-001",
        source_content_hash="sha256:source",
        policy_context={"version": "policy-v1", "principles": ["真实数据"]},
    )
    assert envelope["schema"] == AGENT1_INPUT_SCHEMA
    assert envelope["projectionVersion"] == AGENT1_INPUT_PROJECTION_VERSION
    assert_agent_input_envelope(envelope, expected_schema=AGENT1_INPUT_SCHEMA)
    payload = envelope["payload"]
    signals = payload["snapshotLayer"]["fieldSignals"]
    assert len(signals) == 17
    assert signals[-1]["metricCode"] == "gmv"
    assert signals[-1]["signalStrength"] == "strong"
    assert signals[-1]["signalType"] == "continuous_decline"
    assert signals[-1]["meaningfulChange"] is True
    assert signals[-1]["windows"]["recent3"]["sampleCount"] == 3
    assert payload["trendContext"]["timeSeriesFeatures"]["paymentAmount"]["streakLength"] == 3
    assert payload["trendContext"]["primaryEvidence"]["metricCode"] == "paymentAmount"
    assert payload["inputContract"]["completeFieldSignalTransport"] is True
    assert payload["inputContract"]["trendContextTransport"] is True


def test_v2257_fact_card_has_one_structured_signal_channel() -> None:
    envelope = compile_agent1_envelope(
        _source(),
        source_ref="ART-SIGNAL-001",
        source_content_hash="sha256:source",
        policy_context={"version": "policy-v1"},
    )
    card = _fact_card(envelope["payload"])
    assert len(card["fieldSignals"]) == 17
    assert "factDigest" not in card
    assert "fieldSignals" not in card["metricSnapshot"]
    assert card["trendContext"]["primaryEvidence"]["streakLength"] == 3
    assert card["factLayerValidation"]["status"] == "passed"
    assert card["signalSummary"]["sourceVersionCount"] == 3
    assert card["signalSummary"]["changedMetricCount"] == 17
    assert card["signalSummary"]["meaningfulSignalCount"] == 17


def test_single_worker_uses_v2257_runtime_without_replacing_downstream() -> None:
    worker = (ROOT / "src/services/station_agent_worker_v225_service.py").read_text(
        encoding="utf-8"
    )
    runtime = (
        ROOT / "src/services/agent_runtime_hard_interface_v2257_service.py"
    ).read_text(encoding="utf-8")
    assert "agent_runtime_hard_interface_v2257_service" in worker
    assert 'STATION_AGENT_WORKER_VERSION = "22.5.7"' in worker
    assert "legacy.run_agent_pipeline_tick_hard" in runtime
    assert "run_agent1_microbatch_hard" in runtime
    assert "agent1InputRef.v2" in runtime
    assert "monkey" not in runtime.lower()


def test_targeted_recovery_is_explicit_and_idempotent_by_stage() -> None:
    recovery = (
        ROOT / "src/services/agent1_input_recovery_v2257_service.py"
    ).read_text(encoding="utf-8")
    script = (ROOT / "scripts/requeue_agent1_input_v2257.py").read_text(
        encoding="utf-8"
    )
    assert "apply: bool = False" in recovery
    assert "current_stage='observed_soft_gate'" in recovery
    assert "agent_input.agent1.v2" not in recovery  # imported contract is the authority
    assert "agent1_input_recovery_v2257" in recovery
    assert 'parser.add_argument("--apply", action="store_true")' in script
