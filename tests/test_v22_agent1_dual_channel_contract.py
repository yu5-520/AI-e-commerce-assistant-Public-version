from __future__ import annotations

import pytest


def _signal() -> dict:
    return {
        "dataVersion": "DV-22",
        "productId": "P-001",
        "storeId": "S-001",
        "signalId": "SIG-001",
        "payload": {
            "productIdentity": {
                "productId": "P-001",
                "storeId": "S-001",
                "productTitle": "测试商品",
                "platform": "demo",
                "verticalCategory": "收纳",
            },
            "snapshotLayer": {
                "fieldSignals": [
                    {
                        "metricCode": "roi",
                        "previous": 2.8,
                        "current": 2.1,
                        "changeRatio": -0.25,
                    }
                ]
            },
        },
    }


def _raw_judgment(correlation_id: str) -> dict:
    return {
        "correlationId": correlation_id,
        "productId": "P-001",
        "storeId": "S-001",
        "signalId": "SIG-001",
        "metricCode": "roi",
        "severity": "high",
        "finding": "新增付费流量没有形成对应成交",
        "decisionCore": {
            "decisionType": "act",
            "confidence": 0.88,
            "coreProblem": "低效付费流量占比上升",
            "decisionSummary": "按计划和时段校准低效投放，不整体降预算",
            "selectedOperatingRoute": "paid_efficiency_route",
            "selectedActionFamily": "roas_guard",
            "actionIntent": "限制低效计划并保留稳定计划",
            "preconditions": ["确认真实广告计划"],
            "riskBoundaries": ["不得整体断流"],
            "missingEvidence": [],
            "selectedHypothesisId": "H1",
        },
        "diagnosis": {
            "facts": [
                {"factRef": "F1", "role": "result", "text": "ROI由2.8降至2.1"},
                {"factRef": "F2", "role": "cause", "text": "晚间消耗增长31%"},
            ],
            "causalHypotheses": [
                {
                    "hypothesisId": "H1",
                    "statement": "晚间新增计划带来低转化流量",
                    "supportFactRefs": ["F1", "F2"],
                }
            ],
            "selectedHypothesisId": "H1",
            "rejectedHypotheses": [],
            "alternatives": [],
        },
        "diagnosticExtensions": [
            {
                "extensionId": "EXT-TIME",
                "type": "time_segment_anomaly",
                "summary": "效率下降集中在晚间新增计划",
                "reasoning": "晚间消耗增长，但成交没有同步增长",
                "supportFactRefs": ["F2"],
                "confidence": 0.79,
                "impact": "Agent2按计划和时段拆分调整",
            },
            {
                "extensionId": "EXT-CONFLICT",
                "type": "creative_candidate",
                "summary": "也可尝试更换主图",
                "suggestedActionFamily": "title_image_test",
                "confidence": 0.42,
                "impact": "尝试切换动作族",
            },
        ],
        "diagnosticNarrative": "自然流量增长掩盖了晚间付费效率下降。",
        "platformWeightConflict": {
            "summary": "自然流量增长与付费扩张可能发生权重竞争",
            "supportFactRefs": ["F1", "F2"],
        },
    }


def test_missing_decision_type_fails_closed() -> None:
    from src.services.agent1_dual_channel_contract_service import _decision_core
    from src.services.real_product_judgment_agent_v196_service import (
        ALLOWED_ACTION_FAMILIES,
    )

    with pytest.raises(ValueError, match="agent1_decision_type_missing_or_invalid"):
        _decision_core(
            {
                "coreProblem": "未知问题",
                "decisionSummary": "未知结论",
                "selectedOperatingRoute": "paid_efficiency_route",
                "selectedActionFamilyHint": "roas_guard",
            },
            set(ALLOWED_ACTION_FAMILIES),
        )


def test_agent1_normalizes_fixed_core_and_free_extensions() -> None:
    from src.services import real_product_judgment_agent_v196_service as agent1

    signal = _signal()
    correlation_id = agent1._correlation_id(signal)
    judgments, diagnostics = agent1._normalize_judgments(
        {"judgments": [_raw_judgment(correlation_id)]},
        agent1._source_maps([signal]),
        "DV-22",
    )

    assert diagnostics["invalidDualChannelContractCount"] == 0
    assert len(judgments) == 1
    result = judgments[0]
    ir = result["agent1DecisionIR"]
    assert ir["decisionCore"]["selectedActionFamily"] == "roas_guard"
    assert ir["decisionCore"]["authority"] == "agent2_primary_judgment_source"
    assert ir["diagnosis"]["selectedHypothesisId"] == "H1"

    extensions = {item["extensionId"]: item for item in ir["diagnosticExtensions"]}
    assert extensions["EXT-TIME"]["usableByAgent2"] is True
    assert extensions["EXT-CONFLICT"]["usableByAgent2"] is False
    assert extensions["EXT-CONFLICT"]["validationStatus"] == "conflict_with_action_family_lock"
    assert any(
        item["type"] == "agent_defined.platformWeightConflict"
        for item in extensions.values()
    )
    assert ir["extensionValidation"]["conflictingExtensionCount"] == 1
    assert ir["extensionValidation"]["unmappedTopLevelFieldNames"] == [
        "platformWeightConflict"
    ]


def test_agent2_uses_core_as_authority_and_only_validated_extensions() -> None:
    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import real_product_judgment_agent_v196_service as agent1

    signal = _signal()
    correlation_id = agent1._correlation_id(signal)
    judgments, _ = agent1._normalize_judgments(
        {"judgments": [_raw_judgment(correlation_id)]},
        agent1._source_maps([signal]),
        "DV-22",
    )
    judgment = judgments[0]
    package = {
        **signal,
        **judgment,
        "packageId": "PKG-001",
        "productTitle": "测试商品",
        "actionParameterPack": {
            "actionFamily": "roas_guard",
            "status": "valid",
            "compilerRole": "facts_permissions_and_numeric_limits_only",
            "permissionBounds": {"budgetChangeCeiling": 0.2},
            "currentROI": 2.1,
        },
        "ragContextSnapshot": {
            "version": "22.0.0",
            "status": "ready",
            "matchedCount": 0,
            "approvedCaseIds": [],
        },
    }

    compact = agent2._compact_package(package)
    assert compact["agent1DecisionCore"]["selectedActionFamily"] == "roas_guard"
    assert compact["agent1DecisionCore"]["authority"] == "agent2_primary_judgment_source"
    assert "agent1OperatingJudgment" not in compact
    extension_ids = {
        item["extensionId"] for item in compact["diagnosticExtensions"]
    }
    assert "EXT-TIME" in extension_ids
    assert "EXT-CONFLICT" not in extension_ids
    assert compact["diagnosticExtensionContract"]["authority"] == "context_only"
    assert "lockedActionFamily" in compact["diagnosticExtensionContract"]["cannotOverride"]


def test_agent2_extension_audit_is_required_when_extensions_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import agent1_dual_channel_contract_service as dual

    package = {
        "agent1DecisionIR": {
            "diagnosticExtensions": [
                {
                    "extensionId": "EXT-1",
                    "summary": "晚间异常",
                    "usableByAgent2": True,
                }
            ]
        }
    }

    original = dual._ORIGINALS["agent2_normalize_plan"]
    monkeypatch.setitem(
        dual._ORIGINALS,
        "agent2_normalize_plan",
        lambda raw, package, proof: {
            "actionPlanStatus": "ready",
            "semanticContractMissing": [],
            "activeActionContract": {},
        },
    )
    try:
        result = dual._normalize_agent2_plan({}, package, {})
    finally:
        monkeypatch.setitem(dual._ORIGINALS, "agent2_normalize_plan", original)

    assert result["actionPlanStatus"] == "action_plan_missing_data"
    assert "diagnosticExtensionApplicationReason" in result["semanticContractMissing"]
    assert "diagnostic_extension_audit_must_cover_available_ids" in result["semanticContractMissing"]
