from __future__ import annotations

import json
from pathlib import Path

from src.services.agent2_action_draft_core_v225_service import _normalize_draft
from src.services.agent_execution_lock_v2255_service import (
    execution_lock_from,
    missing_execution_lock,
)
from src.services.agent_runtime_hard_interface_v2255_service import (
    agent_runtime_hard_interface_status,
)
from src.services.llm_input_projection_v211_service import (
    parse_projected_dynamic_payload,
    prepare_llm_request,
)
from src.services.pipeline_live_read_model_v225_service import read_pipeline_live_model
from src.services import pipeline_live_read_model_v208_service as legacy_live
from src.services import real_product_judgment_agent_v2255_service as agent1


def _complete_lock() -> dict:
    return {
        "version": "22.5.5",
        "locked": True,
        "evidenceStatus": "sufficient",
        "selectedOperatingRoute": "conversion_repair",
        "selectedActionFamily": "conversion_repair",
        "primaryProblemNode": "详情页首屏未承接高意向搜索流量",
        "primaryAction": "重构详情页首屏卖点顺序并做单变量验证",
        "primaryExecutionTarget": {
            "targetType": "product_detail_page",
            "targetId": "P10008",
            "owner": "operator",
        },
        "primaryOwner": "operator",
        "decisiveFacts": [
            {"metric": "clickRate", "change": "+18%"},
            {"metric": "conversionRate", "change": "-31%"},
        ],
        "supportingCoordination": ["客服补充近7日高频咨询词"],
        "forbiddenActionDomains": ["inventory_shutdown"],
        "singlePrimaryAction": True,
        "singlePrimaryExecutionTarget": True,
        "forbiddenOverride": True,
    }


def _signal() -> dict:
    return {
        "dataVersion": "DV-TEST",
        "productId": "P10008",
        "storeId": "S001",
        "signalId": "SIG-1",
        "productIdentity": {
            "productId": "P10008",
            "storeId": "S001",
            "productTitle": "测试商品",
            "platform": "天猫",
        },
    }


def test_execution_lock_is_complete_only_with_one_problem_action_owner_and_target() -> None:
    source = {
        "productId": "P10008",
        "agent1OperatingJudgment": {
            "executionLock": _complete_lock(),
            "routeLock": {"locked": True, "selectedOperatingRoute": "conversion_repair"},
            "actionFamilyLock": {
                "locked": True,
                "forbiddenOverride": True,
                "selectedActionFamily": "conversion_repair",
            },
        },
    }
    lock = execution_lock_from(source)
    assert lock["locked"] is True
    assert missing_execution_lock(lock) == []
    assert lock["primaryExecutionTarget"]["targetId"] == "P10008"


def test_execution_lock_fails_closed_when_primary_action_is_missing() -> None:
    lock = _complete_lock()
    lock.pop("primaryAction")
    normalized = execution_lock_from({"productId": "P10008", "executionLock": lock})
    assert normalized["locked"] is False
    assert "executionLock.primaryAction" in missing_execution_lock(normalized)


def test_agent1_converts_unresolved_act_to_observation_hold() -> None:
    signal = _signal()
    payload = {
        "judgments": [
            {
                "correlationId": agent1._fact_card(signal)["correlationId"],
                "productId": "P10008",
                "storeId": "S001",
                "signalId": "SIG-1",
                "decisionType": "act",
                "decisionHint": "risk_candidate",
                "selectedOperatingRoute": "conversion_repair",
                "selectedActionFamilyHint": "conversion_repair",
                "severity": "high",
                "confidence": 0.82,
                "finding": "点击增长但支付转化下降",
                "coreProblem": "原因仍未确认",
                "facts": [{"factRef": "F1", "text": "转化率下降31%"}],
                "causalHypotheses": [{"hypothesis": "详情页承接不足"}],
                "rejectedHypotheses": [],
                "decisionSummary": "需要更多证据",
                "evidenceStatus": "insufficient",
                "missingEvidence": ["详情页分区跳失率"],
            }
        ]
    }
    items, diagnostics = agent1._normalize_judgments(
        payload,
        agent1._source_maps([signal]),
        "DV-TEST",
    )
    assert len(items) == 1
    item = items[0]
    assert item["decisionType"] == "observe"
    assert item["selectedActionFamilyHint"] is None
    assert item["diagnosticHold"] is True
    assert item["taskAdmissionAllowed"] is False
    assert diagnostics["unresolvedActConvertedToObserveCount"] == 1


def test_agent1_preserves_native_observation_as_legal_terminal() -> None:
    signal = _signal()
    payload = {
        "judgments": [
            {
                "correlationId": agent1._fact_card(signal)["correlationId"],
                "productId": "P10008",
                "storeId": "S001",
                "signalId": "SIG-1",
                "decisionType": "observe",
                "decisionHint": "observe_only",
                "severity": "normal",
                "confidence": 0.76,
                "finding": "当前波动未形成稳定方向",
                "decisionSummary": "继续观察下一份报表",
                "evidenceStatus": "sufficient",
            }
        ]
    }
    items, diagnostics = agent1._normalize_judgments(
        payload,
        agent1._source_maps([signal]),
        "DV-TEST",
    )
    item = items[0]
    assert item["decisionType"] == "observe"
    assert item["diagnosticHold"] is False
    assert item["diagnosticHoldReason"] == "native_observation"
    assert item["executionLock"]["locked"] is False
    assert diagnostics["nativeObservationCount"] == 1


def test_agent1_emits_complete_execution_lock_for_act() -> None:
    signal = _signal()
    lock = _complete_lock()
    payload = {
        "judgments": [
            {
                "correlationId": agent1._fact_card(signal)["correlationId"],
                "productId": "P10008",
                "storeId": "S001",
                "signalId": "SIG-1",
                "decisionType": "act",
                "decisionHint": "risk_candidate",
                "selectedOperatingRoute": "conversion_repair",
                "selectedActionFamilyHint": "conversion_repair",
                "severity": "high",
                "confidence": 0.91,
                "finding": "点击增长但支付转化持续下降",
                "coreProblem": lock["primaryProblemNode"],
                "facts": lock["decisiveFacts"],
                "causalHypotheses": [{"hypothesis": "详情页首屏承接不足"}],
                "rejectedHypotheses": [{"hypothesis": "库存导致", "reason": "库存稳定"}],
                "decisionSummary": lock["primaryAction"],
                "evidenceStatus": "sufficient",
                "primaryProblemNode": lock["primaryProblemNode"],
                "primaryAction": lock["primaryAction"],
                "primaryExecutionTarget": lock["primaryExecutionTarget"],
                "primaryOwner": lock["primaryOwner"],
                "decisiveFacts": lock["decisiveFacts"],
                "supportingCoordination": lock["supportingCoordination"],
                "forbiddenActionDomains": lock["forbiddenActionDomains"],
            }
        ]
    }
    items, _ = agent1._normalize_judgments(
        payload,
        agent1._source_maps([signal]),
        "DV-TEST",
    )
    item = items[0]
    assert item["decisionType"] == "act"
    assert item["executionLock"]["locked"] is True
    assert item["primaryAction"] == lock["primaryAction"]
    assert item["taskAdmissionAllowed"] is True


def test_agent2_cannot_expand_one_lock_into_compound_direct_actions() -> None:
    lock = _complete_lock()
    package = {
        "packageId": "PKG-1",
        "productId": "P10008",
        "storeId": "S001",
        "lockedActionFamily": "conversion_repair",
        "executionLock": lock,
        "actionParameterPack": {
            "permissionBounds": {"operatorCanExecute": True},
        },
    }
    raw = {
        "packageId": "PKG-1",
        "productId": "P10008",
        "storeId": "S001",
        "actionFamily": "conversion_repair",
        "draftStatus": "draft_ready",
        "primaryProblemNode": lock["primaryProblemNode"],
        "primaryAction": lock["primaryAction"],
        "primaryExecutionTarget": lock["primaryExecutionTarget"],
        "primaryOwner": "operator",
        "executionTargets": [
            lock["primaryExecutionTarget"],
            {"targetType": "customer_service", "targetId": "CS", "owner": "customer_service"},
        ],
        "parameterRanges": {"testDays": [3, 5]},
        "permissionBoundary": {"operatorCanExecute": True},
        "validationMetrics": ["conversionRate"],
        "differentiationReason": "针对当前详情页首屏承接断点",
        "repairDraft": {
            "repairDetail": "调整首屏卖点顺序并保持其余变量不变",
            "parameterRanges": {"testDays": [3, 5]},
            "validationMetrics": ["conversionRate"],
        },
    }
    draft = _normalize_draft(raw, package, {"passed": True})
    assert draft["primaryAction"] == lock["primaryAction"]
    assert draft["executionTargets"] == [lock["primaryExecutionTarget"]]
    assert draft["compoundActionRejected"] is True
    assert draft["draftStatus"] == "draft_missing_data"
    assert "compound_action_invalid" in draft["semanticContractMissing"]


def test_agent2_projection_excludes_agent1_hypotheses() -> None:
    lock = _complete_lock()
    messages = [
        {"role": "system", "content": "test"},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "version": "22.5.5",
                    "packages": [
                        {
                            "packageId": "PKG-1",
                            "productIdentity": {"productId": "P10008", "storeId": "S001"},
                            "lockedActionFamily": "conversion_repair",
                            "executionLock": lock,
                            "agent1DecisionIR": {
                                "causalHypotheses": ["不应传给Agent2"],
                                "alternatives": ["不应传给Agent2"],
                            },
                            "actionParameterPack": {"status": "ready"},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]
    projected, _, audit = prepare_llm_request(
        "action_plan_judgment_agent",
        messages,
        None,
    )
    dynamic = parse_projected_dynamic_payload(projected)
    package = dynamic["packages"][0]
    assert package["primaryAction"] == lock["primaryAction"]
    assert "agent1DecisionIR" not in package
    assert "causalHypotheses" not in json.dumps(package, ensure_ascii=False)
    assert audit["agent2DiagnosisTransported"] is False


def test_pipeline_live_uses_stable_node_codes_and_separate_failure_bases(monkeypatch) -> None:
    monkeypatch.setattr(
        legacy_live,
        "read_pipeline_live_model",
        lambda data_version=None, limit=80: {
            "ready": True,
            "dataVersion": data_version,
            "stages": [
                {
                    "node": "Agent1 研判",
                    "queued": 1,
                    "running": 1,
                    "completed": 2,
                    "failed": 1,
                    "observed": 3,
                    "historyCompleted": 9,
                },
                {
                    "node": "Agent2 动作草案",
                    "completed": 2,
                    "failed": 1,
                },
            ],
            "summary": {"totalItems": 8, "observedDeposited": 3},
            "stageCounts": {
                "agent1_output_invalid:failed": 1,
                "station:fact_engine:failed": 1,
            },
            "batchState": {"status": "failed"},
            "byActionFamily": {"conversion_repair": 2},
        },
    )
    result = read_pipeline_live_model("DV-TEST", limit=40)
    by_code = {item["nodeCode"]: item for item in result["stages"]}
    assert by_code["agent1"]["history"]["completed"] == 9
    assert result["summary"]["productFailed"] == 1
    assert result["summary"]["batchFailed"] == 1
    assert result["summary"]["failed"] == 2
    assert result["pipelineNodes"][4] == {"nodeCode": "agent1", "label": "Agent1 研判"}


def test_runtime_and_frontend_are_bound_to_v2255_contract() -> None:
    status = agent_runtime_hard_interface_status()
    assert status["version"] == "22.5.5"
    assert status["runtimeMonkeyPatchRequired"] is False
    assert status["agent2ReceivesExecutionLockOnly"] is True

    root = Path(__file__).resolve().parents[1]
    api_client = (root / "web_demo/core/api-client.js").read_text(encoding="utf-8")
    product_page = (root / "web_demo/modules/product/page.js").read_text(encoding="utf-8")
    report_page = (root / "web_demo/modules/report/page.js").read_text(encoding="utf-8")
    assert 'API_CLIENT_VERSION = "22.5.5"' in api_client
    assert "clearApiCaches();" in api_client
    assert "AppApi.productView" in product_page
    assert 'nodeCode: "agent1"' in report_page
