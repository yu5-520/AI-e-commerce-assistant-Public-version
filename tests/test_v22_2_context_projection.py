from __future__ import annotations

import json

from src.services.llm_input_projection_v211_service import (
    LLM_INPUT_PROJECTION_VERSION,
    parse_projected_dynamic_payload,
    prepare_llm_request,
    semantic_item_fingerprint,
)


def _messages(payload: dict) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "contract"},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def test_agent1_projection_preserves_diagnostic_policy_and_cross_validation() -> None:
    messages, semantic, audit = prepare_llm_request(
        "product_judgment_agent",
        _messages(
            {
                "version": "22.2.0",
                "diagnosticRag": {
                    "principles": ["趋势与强关联交叉判断"],
                    "familyGuidance": {"roas_guard": "只处理真实效率问题"},
                },
                "products": [
                    {
                        "correlationId": "CORR-1",
                        "signalId": "SIG-1",
                        "productIdentity": {
                            "productId": "P-1",
                            "storeId": "S-1",
                            "productTitle": "测试商品",
                        },
                        "factDigest": ["ROI由2.8下降到2.1"],
                        "metricSnapshot": {"roi": 2.1, "fieldSignals": [{"metricCode": "roi"}]},
                        "strongRelations": [{"metricCode": "paidVisitors", "changeRate": 0.31}],
                        "crossValidation": {
                            "decision": {"status": "passed"},
                            "changedMetricCount": 2,
                        },
                    }
                ],
            }
        ),
        None,
    )

    assert audit["version"] == LLM_INPUT_PROJECTION_VERSION == "22.2.0"
    assert audit["semanticContinuity"] == "passed"
    assert "diagnosticRag" in messages[0]["content"]
    assert "趋势与强关联交叉判断" in messages[0]["content"]

    dynamic = parse_projected_dynamic_payload(messages)
    product = dynamic["products"][0]
    assert product["crossValidation"]["decision"]["status"] == "passed"
    assert product["strongRelations"][0]["metricCode"] == "paidVisitors"
    assert product["metricSnapshot"]["roi"] == 2.1
    assert "fieldSignals" not in product["metricSnapshot"]
    assert semantic["stableContext"]["diagnosticRag"]["principles"] == [
        "趋势与强关联交叉判断"
    ]


def _agent2_package() -> dict:
    return {
        "packageId": "PKG-1",
        "identity": {"productId": "P-1", "storeId": "S-1", "productTitle": "测试商品"},
        "lockedActionFamily": "roas_guard",
        "agent1DecisionCore": {
            "decisionType": "act",
            "coreProblem": "低效付费流量占比上升",
            "decisionSummary": "按计划和时段校准低效投放",
            "selectedOperatingRoute": "paid_efficiency_route",
            "selectedActionFamily": "roas_guard",
            "authority": "agent2_primary_judgment_source",
        },
        "agent1Diagnosis": {
            "facts": [{"factRef": "F1", "text": "晚间消耗增长31%"}],
            "causalHypotheses": [{"id": "H1", "statement": "晚间新增计划低效"}],
        },
        "diagnosticExtensions": [
            {
                "extensionId": "EXT-1",
                "type": "time_segment_anomaly",
                "summary": "效率下降集中在晚间",
                "usableByAgent2": True,
            }
        ],
        "diagnosticNarrative": "自然流量增长掩盖付费效率下降。",
        "capabilityPack": {
            "status": "valid",
            "permissionBounds": {"budgetChangeCeiling": 0.2},
            "currentROI": 2.1,
        },
        "metricDigest": {"roi": {"current": 2.1, "previous": 2.8}},
        "ragContext": {
            "status": "matched",
            "queryFingerprint": "RAG-1",
            "approvedCaseIds": ["CASE-1"],
            "positiveExperienceCards": [{"caseId": "CASE-1", "experiencePrinciples": ["按时段拆分"]}],
        },
        "diagnosticExtensionContract": {
            "authority": "context_only",
            "availableExtensionIds": ["EXT-1"],
        },
        "agent1OperatingJudgment": {"legacy": "must_not_be_sent"},
        "systemFacts": {"huge": "must_not_be_sent"},
        "agent1DecisionIR": {"decisionCore": {"duplicate": True}},
    }


def test_agent2_projection_uses_one_canonical_copy_of_each_semantic_layer() -> None:
    messages, _, audit = prepare_llm_request(
        "action_plan_judgment_agent",
        _messages({"version": "22.2.0", "packages": [_agent2_package()]}),
        None,
    )
    assert audit["semanticContinuity"] == "passed"
    dynamic = parse_projected_dynamic_payload(messages)
    package = dynamic["packages"][0]

    assert package["agent1DecisionCore"]["coreProblem"] == "低效付费流量占比上升"
    assert package["agent1Diagnosis"]["facts"][0]["factRef"] == "F1"
    assert package["diagnosticExtensions"][0]["extensionId"] == "EXT-1"
    assert package["capabilityPack"]["permissionBounds"]["budgetChangeCeiling"] == 0.2
    assert package["metricDigest"]["roi"]["previous"] == 2.8
    assert package["ragContext"]["approvedCaseIds"] == ["CASE-1"]
    assert package["diagnosticExtensionContract"]["authority"] == "context_only"

    assert "agent1OperatingJudgment" not in package
    assert "agent1DecisionIR" not in package
    assert "systemFacts" not in package
    assert "actionDataPack" not in package
    assert "metricEvidence" not in package
    assert "ragContextSnapshot" not in package


def test_agent2_cache_fingerprint_changes_with_real_semantic_inputs() -> None:
    base = _agent2_package()
    messages, _, _ = prepare_llm_request(
        "action_plan_judgment_agent",
        _messages({"packages": [base]}),
        None,
    )
    projected = parse_projected_dynamic_payload(messages)["packages"][0]
    baseline = semantic_item_fingerprint("action_plan_judgment_agent", projected)

    changed_extension = _agent2_package()
    changed_extension["diagnosticExtensions"][0]["summary"] = "异常集中在凌晨新计划"
    messages, _, _ = prepare_llm_request(
        "action_plan_judgment_agent",
        _messages({"packages": [changed_extension]}),
        None,
    )
    extension_fingerprint = semantic_item_fingerprint(
        "action_plan_judgment_agent",
        parse_projected_dynamic_payload(messages)["packages"][0],
    )

    changed_permission = _agent2_package()
    changed_permission["capabilityPack"]["permissionBounds"]["budgetChangeCeiling"] = 0.1
    messages, _, _ = prepare_llm_request(
        "action_plan_judgment_agent",
        _messages({"packages": [changed_permission]}),
        None,
    )
    permission_fingerprint = semantic_item_fingerprint(
        "action_plan_judgment_agent",
        parse_projected_dynamic_payload(messages)["packages"][0],
    )

    changed_rag = _agent2_package()
    changed_rag["ragContext"]["approvedCaseIds"] = ["CASE-2"]
    messages, _, _ = prepare_llm_request(
        "action_plan_judgment_agent",
        _messages({"packages": [changed_rag]}),
        None,
    )
    rag_fingerprint = semantic_item_fingerprint(
        "action_plan_judgment_agent",
        parse_projected_dynamic_payload(messages)["packages"][0],
    )

    assert baseline != extension_fingerprint
    assert baseline != permission_fingerprint
    assert baseline != rag_fingerprint
