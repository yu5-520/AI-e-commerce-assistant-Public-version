from __future__ import annotations

import copy

import pytest

from src.services import agent3_runtime_v23215_service as runtime
from src.services import agent_token_runtime_v225_service as active_runtime
from src.services.agent3_sop_core_v225_service import (
    SOP_MISSING_DATA,
    SOP_READY,
    _build_auxiliary_repair_messages,
    _normalize_sop,
    apply_agent3_auxiliary_repair,
)
from src.services.agent3_system_constraint_v23215_service import (
    AGENT3_SYSTEM_CONSTRAINT_VERSION,
    family_policy,
)


def _package() -> dict:
    return {
        "packageId": "PKG-COND-1",
        "itemId": "PI-COND-1",
        "dataVersion": "DV-1",
        "productId": "P10001",
        "storeId": "STORE-1",
        "productTitle": "轻薄防晒外套",
        "productIdentity": {
            "productId": "P10001",
            "storeId": "STORE-1",
            "productTitle": "轻薄防晒外套",
        },
        "lockedActionFamily": "title_image_test",
        "actionParameterPack": {
            "executionObjects": [{"targetType": "product", "targetId": "P10001"}],
            "creativeTest": {"variantCount": 3, "testHours": 6},
        },
        "recentFiveOrLatestFacts": [
            {"metric": "clickRate", "previous": 0.0205, "current": 0.0191},
        ],
        "agent2ActionDraft": {
            "packageId": "PKG-COND-1",
            "productId": "P10001",
            "storeId": "STORE-1",
            "actionFamily": "title_image_test",
            "draftStatus": "draft_ready",
            "actionIntent": "制作三组标题主图并做唯一变量实验",
            "executionTargets": [{"targetType": "product", "targetId": "P10001"}],
            "riskBoundaries": ["价格、投放和详情页保持不变"],
            "validationMetrics": ["点击率", "转化率", "支付金额"],
            "requiredEvidence": ["素材截图", "实验结果"],
        },
        "companyOperatingPolicySnapshot": {"managementStyle": "少而准"},
        "companySopRagSnapshot": {"companyExecutionPrinciples": ["只改变一个变量"]},
        "inputContract": {
            "schema": "agent_input.agent3_sop.v1",
            "agent3SystemConstraintRequired": True,
        },
    }


def _valid_execution_steps() -> list[dict]:
    return [
        {
            "stepId": "STEP-1",
            "actionFamily": "title_image_test",
            "actionType": "creative_brief",
            "executionObject": "P10001三组创意简报",
            "executorRole": "design_team",
            "instruction": "制作三组差异化标题主图创意简报",
            "deadline": "6小时内",
            "completionCriteria": "三组简报完成并通过审核",
        },
        {
            "stepId": "STEP-2",
            "actionFamily": "title_image_test",
            "actionType": "experiment_grouping",
            "executionObject": "P10001原素材与三组新素材实验组",
            "executorRole": "operation_specialist",
            "instruction": "建立四组实验并均分流量，只改变标题和主图",
            "deadline": "素材审核后2小时内",
            "completionCriteria": "四组实验上线且控制变量一致",
        },
        {
            "stepId": "STEP-3",
            "actionFamily": "title_image_test",
            "actionType": "result_review",
            "executionObject": "P10001实验结果数据",
            "executorRole": "data_analyst",
            "instruction": "第3天和第7天复核点击率、转化率和支付金额",
            "deadline": "实验第3天和第7天",
            "completionCriteria": "形成胜出或回滚结论并提交统计证据",
        },
    ]


def _raw_sop(*, invalid_stop: bool) -> dict:
    stop = (
        {
            "conditionId": "STOP-2",
            "actionFamily": "title_image_test",
            "conditionType": "inventory_risk",
            "condition": "库存不足导致无法承接新增流量",
            "responseAction": "停止测试并等待补货",
            "evidenceRequired": "库存截图",
        }
        if invalid_stop
        else {
            "conditionId": "STOP-1",
            "actionFamily": "title_image_test",
            "conditionType": "metric_guardrail",
            "condition": "任一实验组转化率较系统冻结证据下降超过15%",
            "responseAction": "停止该实验组并恢复原素材",
            "evidenceRequired": "实验组指标截图",
        }
    )
    return {
        "packageId": "PKG-COND-1",
        "productId": "P10001",
        "storeId": "STORE-1",
        "actionFamily": "title_image_test",
        "sopStatus": "sop_ready",
        "finalTaskTitle": "轻薄防晒外套三组标题主图测试",
        "executionObjective": "验证三组素材的点击承接差异",
        "executionSteps": _valid_execution_steps(),
        "decisionBranches": [],
        "submissionEvidence": ["三组素材截图", "实验分组截图", "指标对比报告"],
        "crossDepartmentActions": [],
        "approvalFlow": {"approvalRequired": False},
        "reviewMetrics": ["点击率", "转化率", "支付金额"],
        "verificationPeriod": "7天",
        "stopConditions": [stop],
        "rollbackConditions": [
            {
                "conditionId": "ROLLBACK-1",
                "actionFamily": "title_image_test",
                "conditionType": "restore_previous_asset",
                "condition": "触发停止条件",
                "rollbackAction": "恢复原标题和主图",
                "evidenceRequired": "恢复后页面截图",
            }
        ],
        "reviewCycle": ["3天", "7天"],
        "companyStyleReason": "保持唯一变量并以真实结果决策",
        "ragUsedCaseIds": [],
        "ragRejectedCaseIds": [],
        "ragApplicationReason": "结合当前商品草案生成",
        "semanticContractMissing": [],
    }


def _repair_payload() -> dict:
    return {
        "repair": {
            "packageId": "PKG-COND-1",
            "stopConditions": [
                {
                    "conditionId": "STOP-1",
                    "actionFamily": "title_image_test",
                    "conditionType": "metric_guardrail",
                    "condition": "任一实验组转化率较系统冻结证据下降超过15%",
                    "responseAction": "停止该实验组并恢复原素材",
                    "evidenceRequired": "实验组指标截图",
                },
                {
                    "conditionId": "STOP-2",
                    "actionFamily": "title_image_test",
                    "conditionType": "creative_compliance",
                    "condition": "当前标题或主图触发平台素材违规",
                    "responseAction": "下线违规实验组并保留合规组",
                    "evidenceRequired": "平台违规提示截图",
                },
            ],
            "rollbackConditions": [
                {
                    "conditionId": "ROLLBACK-1",
                    "actionFamily": "title_image_test",
                    "conditionType": "restore_previous_asset",
                    "condition": "触发任一停止条件",
                    "rollbackAction": "恢复原标题和主图",
                    "evidenceRequired": "恢复后页面截图",
                }
            ],
        }
    }


def _usage(request_id: str) -> dict:
    return {
        "provider": "aliyun_bailian",
        "model": "qwen3.7-plus",
        "providerRequestId": request_id,
        "providerCallExecuted": True,
        "inputFingerprint": f"fp-{request_id}",
        "input": 100,
        "output": 80,
        "reasoningTokens": 10,
        "projectionVersion": "23.2.15",
        "gatewayVersion": "22.5.9",
    }


def test_title_image_condition_policy_excludes_inventory_risk() -> None:
    policy = family_policy("title_image_test")

    assert policy["version"] == AGENT3_SYSTEM_CONSTRAINT_VERSION
    assert "metric_guardrail" in policy["allowedStopConditionTypes"]
    assert "creative_compliance" in policy["allowedStopConditionTypes"]
    assert "inventory_risk" not in policy["allowedStopConditionTypes"]
    assert policy["maxAuxiliaryRepairAttempts"] == 1


def test_cross_family_stop_condition_is_repairable_without_touching_steps() -> None:
    raw = _raw_sop(invalid_stop=True)
    normalized = _normalize_sop(raw, _package(), {})

    assert normalized["sopStatus"] == SOP_MISSING_DATA
    assert normalized["contractValidation"]["repairableAuxiliaryOnly"] is True
    assert any(
        item.startswith("agent3_sop_cross_family_contamination:")
        for item in normalized["contractValidation"]["repairableMissing"]
    )

    messages, payload = _build_auxiliary_repair_messages(
        "DV-1", _package(), raw, normalized
    )
    assert payload["immutableSopDigest"]["executionSteps"] == normalized["executionSteps"]
    assert "只能修复stopConditions和rollbackConditions" in messages[0]["content"]


def test_repair_replaces_only_conditions_and_restores_ready_status() -> None:
    raw = _raw_sop(invalid_stop=True)
    before_steps = copy.deepcopy(raw["executionSteps"])
    patched = apply_agent3_auxiliary_repair(
        raw,
        _repair_payload(),
        package_id="PKG-COND-1",
    )
    normalized = _normalize_sop(patched, _package(), {})

    assert patched["executionSteps"] == before_steps
    assert normalized["executionSteps"] == before_steps
    assert normalized["sopStatus"] == SOP_READY
    assert normalized["semanticContractMissing"] == []
    assert normalized["auxiliaryConditionRepairApplied"] is True


def test_repair_rejects_wrong_package_identity() -> None:
    payload = _repair_payload()
    payload["repair"]["packageId"] = "PKG-OTHER"

    with pytest.raises(ValueError, match="agent3_auxiliary_repair_package_mismatch"):
        apply_agent3_auxiliary_repair(
            _raw_sop(invalid_stop=True),
            payload,
            package_id="PKG-COND-1",
        )


def test_provider_runtime_performs_exactly_one_auxiliary_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_call_json(**kwargs):
        calls.append(str(kwargs["prompt_version"]))
        if len(calls) == 1:
            return {"sops": [_raw_sop(invalid_stop=True)]}, _usage("REQ-INITIAL")
        return _repair_payload(), _usage("REQ-REPAIR")

    monkeypatch.setattr(runtime, "assert_agent_input_envelope", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, "call_json", fake_call_json)

    outputs, provider = runtime.run_agent3_sop_provider_isolated(
        [{"payload": _package()}],
        data_version="DV-1",
    )

    sop = outputs["PKG-COND-1"]
    assert calls == ["23.2.15", "23.2.15.auxiliary-repair"]
    assert provider["actualCalls"] == 2
    assert provider["auxiliaryRepairAttempts"] == 1
    assert provider["auxiliaryRepairApplied"] == 1
    assert sop["sopStatus"] == SOP_READY
    assert sop["agent3AuxiliaryRepair"]["attempted"] is True
    assert sop["agent3AuxiliaryRepair"]["applied"] is True
    assert sop["agent3AuxiliaryRepair"]["executionStepsImmutable"] is True
    assert sop["semanticContractMissing"] == []


def test_active_runtime_binds_v23215_agent3_runtime() -> None:
    assert active_runtime.run_agent3_sop_projected_inputs.__module__ == (
        "src.services.agent3_runtime_v23215_service"
    )
