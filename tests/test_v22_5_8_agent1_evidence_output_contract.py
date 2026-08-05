from __future__ import annotations

import json
from pathlib import Path

from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_PROJECTION_VERSION,
    AGENT1_INPUT_SCHEMA,
    assert_agent_input_envelope,
)
from src.services.agent_input_transport_v2258_service import compile_agent1_envelope
from src.services.real_product_judgment_agent_v2258_service import (
    _fact_card,
    _normalize_judgments,
    _source_maps,
)

ROOT = Path(__file__).resolve().parents[1]


def _source() -> dict:
    metric_codes = [
        "paymentAmount", "roi", "adSpend", "grossMargin", "organicVisitors",
        "paidVisitors", "clickRate", "conversionRate", "refundRate", "inventory",
        "availableDays", "orderCount", "avgOrderValue", "refundAmount", "visitorCount",
        "paidOrderCount", "gmv",
    ]
    signals = []
    features = {}
    for index, code in enumerate(metric_codes):
        signals.append({
            "metricCode": code, "previous": 100 + index, "current": 90 + index,
            "latest": 90 + index, "changeRatio": -0.10, "changeRate": -0.10,
            "changeVsPrevious": -10, "meaningfulChange": True,
            "signalStrength": "strong", "signalType": "continuous_decline",
            "direction": "down", "sampleCount": 3,
            "windows": {"recent3": {"direction": "down", "sampleCount": 3}},
            "reason": f"{code}连续下降",
        })
        features[code] = {
            "latest": 90 + index, "mom": -0.10, "yoy": -0.16,
            "seasonalResidual": -0.04, "streakDirection": "down",
            "streakLength": 3, "slope5": -0.08, "volatility10": 0.12,
            "windowCount": 3,
        }
    return {
        "productId": "P10001", "storeId": "S001", "signalId": "SIG-001",
        "dataVersion": "DV-003", "businessDate": "2026-07-02",
        "profileLayer": {"productId": "P10001", "storeId": "S001", "productTitle": "测试商品", "platform": "京东"},
        "snapshotLayer": {"fieldSignals": signals},
        "crossValidation": {
            "sourceVersionCount": 0, "sourceDatasetCount": 0, "sourceRecordCount": 0,
            "changedMetricCount": 17, "abnormalMetricCount": 12, "sameDirectionCount": 8,
            "reason": "Metric evidence exists but source identity is incomplete",
            "blockingFactors": ["sourceDatasetCount=0", "转化率和支付金额同向下降"],
        },
        "sourceVersionCount": 3, "sourceDatasetCount": 3, "sourceRecordCount": 3,
        "businessDateCount": 3, "sourceVersions": ["DV-001", "DV-002", "DV-003"],
        "businessDates": ["2026-06-25", "2026-06-28", "2026-07-02"],
        "factLayerValidation": {"status": "passed", "metricCompleteness": 1.0},
        "strongRelations": [{"resultMetric": "paymentAmount", "causeMetric": "conversionRate"}],
        "agentProductSnapshotPackage": {
            "version": "snapshot.package.v1", "timeSeriesFeatures": features,
            "operatingDecision": {
                "primaryEvidence": {"metricCode": "paymentAmount", "streakDirection": "down", "streakLength": 3, "volatility10": 0.12},
                "relatedEvidence": [{"metricCode": "conversionRate", "streakDirection": "down", "streakLength": 3, "volatility10": 0.08}],
                "confidence": 0.88,
            },
        },
    }


def _envelope() -> dict:
    return compile_agent1_envelope(
        _source(), source_ref="ART-SIGNAL-001", source_content_hash="sha256:source",
        policy_context={"version": "policy-v1", "principles": ["真实数据"]},
    )


def test_agent1_v3_has_one_lineage_owner_and_metric_only_cross_validation() -> None:
    envelope = _envelope()
    assert envelope["schema"] == AGENT1_INPUT_SCHEMA == "agent_input.agent1.v3"
    assert envelope["projectionVersion"] == AGENT1_INPUT_PROJECTION_VERSION == "22.5.8"
    assert_agent_input_envelope(envelope, expected_schema=AGENT1_INPUT_SCHEMA)
    payload = envelope["payload"]
    lineage = payload["sourceLineageValidation"]
    assert lineage["sourceVersionCount"] == 3
    assert lineage["sourceDatasetCount"] == 3
    assert lineage["businessDateCount"] == 3
    assert lineage["sourceIdentityComplete"] is True
    assert lineage["contentHashVerified"] is True
    assert lineage["blockingFactors"] == []
    cross = payload["crossValidation"]
    assert cross["changedMetricCount"] == 17
    assert cross["abnormalMetricCount"] == 12
    assert cross["lineageOwner"] == "sourceLineageValidation"
    for key in ("sourceVersionCount", "sourceDatasetCount", "sourceRecordCount", "sourceIdentityComplete", "blockingFactors"):
        assert key not in cross
    assert "source identity" not in json.dumps(cross, ensure_ascii=False).lower()


def test_agent1_v3_preserves_all_signals_and_key_trend_semantics() -> None:
    payload = _envelope()["payload"]
    signals = payload["snapshotLayer"]["fieldSignals"]
    assert len(signals) == 17
    assert signals[-1]["metricCode"] == "gmv"
    feature = payload["trendContext"]["timeSeriesFeatures"]["paymentAmount"]
    for key in ("mom", "yoy", "seasonalResidual", "streakDirection", "streakLength", "slope5", "volatility10", "windowCount"):
        assert key in feature
    card = _fact_card(payload)
    assert len(card["fieldSignals"]) == 17
    assert "factDigest" not in card
    assert "fieldSignals" not in card["metricSnapshot"]
    assert card["signalSummary"]["sourceVersionCount"] == 3
    assert card["signalSummary"]["sourceDatasetCount"] == 3
    assert card["sourceLineageValidation"]["sourceIdentityComplete"] is True


def test_observation_and_attention_aliases_become_legal_observations() -> None:
    payload = _envelope()["payload"]
    for alias in ("observation", "attention", "watch", "monitor", "hold"):
        raw = {"judgments": [{
            "correlationId": payload["correlationId"], "productId": payload["productId"],
            "storeId": payload["storeId"], "signalId": payload["signalId"],
            "decisionType": alias, "finding": "继续观察", "evidenceStatus": "insufficient",
        }]}
        normalized, diagnostics = _normalize_judgments(raw, _source_maps([payload]), "DV-003")
        assert len(normalized) == 1
        assert normalized[0]["decisionType"] == "observe"
        assert normalized[0]["normalizationStatus"] == "normalized_with_warning"
        assert normalized[0]["rawDecisionType"] == alias
        assert diagnostics["decisionAliasNormalizedCount"] == 1
        assert diagnostics["unmatchedProviderJudgmentCount"] == 0


def test_unknown_decision_type_fails_closed_without_dropping_product() -> None:
    payload = _envelope()["payload"]
    raw = {"judgments": [{
        "correlationId": payload["correlationId"], "productId": payload["productId"],
        "storeId": payload["storeId"], "signalId": payload["signalId"],
        "decisionType": "maybe_later", "finding": "语义不确定",
    }]}
    normalized, diagnostics = _normalize_judgments(raw, _source_maps([payload]), "DV-003")
    assert len(normalized) == 1
    assert normalized[0]["decisionType"] == "observe"
    assert normalized[0]["normalizationStatus"] == "normalized_with_warning"
    assert diagnostics["unknownDecisionFailClosedCount"] == 1


def test_runtime_packaging_recovery_and_read_model_contracts() -> None:
    policy = json.loads((ROOT / "release/release-policy.json").read_text(encoding="utf-8"))
    assert "src/**/*" in policy["runtimeGlobs"]
    recovery = (ROOT / "src/services/agent1_input_recovery_v2258_service.py").read_text(encoding="utf-8")
    cli = (ROOT / "src/services/requeue_agent1_input_v2258.py").read_text(encoding="utf-8")
    token_runtime = (ROOT / "src/services/agent_token_runtime_v2258_service.py").read_text(encoding="utf-8")
    read_model = (ROOT / "src/services/pipeline_live_read_model_v2258_service.py").read_text(encoding="utf-8")
    worker = (ROOT / "src/services/station_agent_worker_v2258_service.py").read_text(encoding="utf-8")
    assert "apply: bool = False" in recovery
    assert '"observed_soft_gate", "agent1_failed", "agent1_output_invalid"' in recovery
    assert "agent_input.agent1.v3" not in recovery
    assert 'parser.add_argument("--apply", action="store_true")' in cli
    assert "skipped_raw_identity_present" in token_runtime
    assert "providerCallStatus" in token_runtime
    assert "normalizationStatus" in token_runtime
    assert 'attentionDedupKey="dataVersion+storeId+productId"' in read_model
    assert "agent1OutputInvalid" in read_model
    assert "lastErrorCode" in read_model
    assert "threading.Thread" not in worker
    assert "station_agent_worker_v225_service" in worker
