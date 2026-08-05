from __future__ import annotations

from src.runtime_version import VERSION, runtime_versions
from src.services import pipeline_agent1_microbatch_v20101_service as worker
from src.services import real_product_judgment_agent_v196_service as agent1


def _source() -> dict:
    return {
        "dataVersion": "DV-V22-OBSERVATION",
        "productId": "天猫::TB-SH-001::P10004::SKU10004-A",
        "storeId": "TB-SH-001",
        "signalId": "PSIGV-V22-0001",
        "payload": {
            "productId": "天猫::TB-SH-001::P10004::SKU10004-A",
            "storeId": "TB-SH-001",
            "signalId": "PSIGV-V22-0001",
        },
    }


def _base_raw() -> dict:
    return {
        "correlationId": agent1._correlation_id(_source()),
        "productId": "P10004",
        "storeId": "TB-SH-001",
        "signalId": "PSIGV-V22-0001",
        "metricCode": "sellable_days",
        "severity": "medium",
        "confidence": 0.78,
        "finding": "库存消耗加快，但当前仅构成仓储协同约束",
        "evidence": ["sellableDays下降", "ROI保持稳定"],
        "primaryBusinessSignal": "inventory_depletion_risk",
        "primaryOperatingGap": "库存预警需要仓储介入",
        "businessHypothesis": "运营不应因库存信号自动修改主链",
        "excludedActions": ["roas_guard", "title_image_test"],
        "requiredActionData": [],
        "capacityConstraints": ["sellableDays=6.6天"],
        "companyHooks": ["向仓储发起补货催办"],
    }


def test_native_observation_is_a_terminal_non_task_decision() -> None:
    raw = {
        **_base_raw(),
        "decisionHint": "risk_candidate",
        "decisionType": "observe",
        "selectedOperatingRoute": "inventory_alert",
        "selectedActionFamilyHint": "observe_only",
        "routeLock": {
            "locked": True,
            "selectedOperatingRoute": "inventory_alert",
            "lockReason": "provider supplied a noncanonical observation route",
        },
        "actionFamilyLock": {
            "locked": True,
            "selectedActionFamily": "observe_only",
            "lockReason": "provider supplied a noncanonical observation family",
            "forbiddenOverride": True,
        },
    }

    judgments, diagnostics = agent1._normalize_judgments(
        {"judgments": [raw]},
        agent1._source_maps([_source()]),
        "DV-V22-OBSERVATION",
    )

    assert len(judgments) == 1
    judgment = judgments[0]
    assert judgment["decisionHint"] == "observe_only"
    assert judgment["decisionType"] == "observe"
    assert judgment["selectedOperatingRoute"] == "observe"
    assert judgment["selectedActionFamilyHint"] is None
    assert judgment["observationOnly"] is True
    assert judgment["taskAdmissionAllowed"] is False
    assert judgment["fallbackAllowed"] is False
    assert judgment["routeLock"]["selectedOperatingRoute"] == "observe"
    assert judgment["actionFamilyLock"]["selectedActionFamily"] is None
    assert judgment["agent1OperatingJudgment"]["selectedActionFamily"] is None
    assert diagnostics["nativeObservationCount"] == 1
    assert diagnostics["invalidProviderContractCount"] == 0


def test_actionable_judgment_keeps_one_valid_action_family() -> None:
    raw = {
        **_base_raw(),
        "decisionHint": "risk_candidate",
        "decisionType": "act",
        "selectedOperatingRoute": "platform_activity_test",
        "selectedActionFamilyHint": "platform_activity",
        "decisionSummary": "自然流量存在活动承接机会，使用次链接进行小流量活动测试",
    }

    judgments, diagnostics = agent1._normalize_judgments(
        {"judgments": [raw]},
        agent1._source_maps([_source()]),
        "DV-V22-OBSERVATION",
    )

    assert len(judgments) == 1
    judgment = judgments[0]
    assert judgment["decisionHint"] == "risk_candidate"
    assert judgment["decisionType"] == "act"
    assert judgment["selectedOperatingRoute"] == "platform_activity_test"
    assert judgment["selectedActionFamilyHint"] == "platform_activity"
    assert judgment["observationOnly"] is False
    assert judgment["taskAdmissionAllowed"] is True
    assert judgment["actionFamilyLock"]["selectedActionFamily"] == "platform_activity"
    assert diagnostics["nativeObservationCount"] == 0
    assert diagnostics["invalidProviderContractCount"] == 0


def test_invalid_action_family_is_rejected_without_compatibility_fallback() -> None:
    raw = {
        **_base_raw(),
        "decisionHint": "risk_candidate",
        "decisionType": "act",
        "selectedOperatingRoute": "inventory_alert",
        "selectedActionFamilyHint": "inventory_alert",
    }

    judgments, diagnostics = agent1._normalize_judgments(
        {"judgments": [raw]},
        agent1._source_maps([_source()]),
        "DV-V22-OBSERVATION",
    )

    assert judgments == []
    assert diagnostics["invalidProviderContractCount"] == 1
    assert diagnostics["nativeObservationCount"] == 0
    assert "inventory_alert" not in agent1.ALLOWED_ACTION_FAMILIES


def test_v22_runtime_owns_agent1_observation_and_worker_entry() -> None:
    versions = runtime_versions()

    assert versions["api"] == VERSION == "22.4.0"
    assert versions["stationAgentWorker"] == VERSION
    assert agent1.REAL_PRODUCT_AGENT_V196_VERSION == VERSION
    assert worker._real_agent_judgments is agent1._real_agent_judgments
    assert agent1.PRODUCT_AGENT_MODE == "v22_contextual_diagnosis_before_family_lock"
