from __future__ import annotations

from src.services.agent3_sop_core_v225_service import (
    SOP_MISSING_DATA,
    SOP_READY,
    _build_messages,
    _normalize_sop,
    missing_agent3_sop_contract,
)
from src.services.agent3_system_constraint_v23214_service import (
    AGENT3_SYSTEM_CONSTRAINT_VERSION,
    validate_agent3_sop_system_contract,
)


def _package() -> dict:
    return {
        "packageId": "PKG-STRUCTURED-1",
        "itemId": "PI-STRUCTURED-1",
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
            "packageId": "PKG-STRUCTURED-1",
            "productId": "P10001",
            "storeId": "STORE-1",
            "actionFamily": "title_image_test",
            "draftStatus": "draft_ready",
            "actionIntent": "制作三组标题主图并做唯一变量测试",
            "executionTargets": [{"targetType": "product", "targetId": "P10001"}],
            "riskBoundaries": ["价格、投放和详情页保持不变"],
            "validationMetrics": ["点击率", "转化率", "支付金额"],
            "requiredEvidence": ["素材截图", "测试结果"],
        },
        "companyOperatingPolicySnapshot": {"managementStyle": "少而准"},
        "companySopRagSnapshot": {"companyExecutionPrinciples": ["只改变一个变量"]},
        "inputContract": {
            "schema": "agent_input.agent3_sop.v1",
            "agent3SystemConstraintRequired": True,
        },
    }


def _raw_without_duplicate_fields() -> dict:
    return {
        "packageId": "PKG-STRUCTURED-1",
        "productId": "P10001",
        "storeId": "STORE-1",
        "actionFamily": "title_image_test",
        "sopStatus": "sop_ready",
        "finalTaskTitle": "轻薄防晒外套三组标题主图测试",
        "executionObjective": "验证三组素材的点击承接差异",
        "executionSteps": [
            {
                "stepId": "STEP-1",
                "actionFamily": "title_image_test",
                "actionType": "creative_brief",
                "executionObject": "P10001三组标题主图创意简报",
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
                "instruction": "建立四组实验并按25%均分流量，只改变标题和主图",
                "deadline": "素材审核后2小时内",
                "completionCriteria": "四组实验上线且控制变量一致",
            },
            {
                "stepId": "STEP-3",
                "actionFamily": "title_image_test",
                "actionType": "result_review",
                "executionObject": "P10001实验结果数据",
                "executorRole": "data_analyst",
                "instruction": "第3天和第7天复核点击率、转化率和支付金额并决定胜出或回滚",
                "deadline": "实验第3天和第7天",
                "completionCriteria": "形成胜出组或回滚结论并提交统计证据",
            },
        ],
        "decisionBranches": [],
        "submissionEvidence": ["三组素材截图", "实验分组截图", "指标对比报告"],
        "crossDepartmentActions": [],
        "approvalFlow": {"approvalRequired": False},
        "reviewMetrics": ["点击率", "转化率", "支付金额"],
        "verificationPeriod": "7天",
        "stopConditions": [
            {
                "conditionId": "STOP-1",
                "actionFamily": "title_image_test",
                "conditionType": "metric_guardrail",
                "condition": "任一实验组转化率较系统冻结证据下降超过15%",
                "responseAction": "停止该实验组并恢复原素材",
                "evidenceRequired": "实验组指标截图",
            }
        ],
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
        "semanticContractMissing": [
            "executionObject",
            "operatorActionSteps",
            "operatorActionSteps_min_1",
            "agent3_sop_operator_steps_min_3",
        ],
    }


def test_provider_prompt_declares_execution_steps_as_single_source() -> None:
    messages, payload = _build_messages("DV-1", [_package()])

    assert payload["version"] == "23.2.15"
    assert payload["systemConstraintVersion"] == AGENT3_SYSTEM_CONSTRAINT_VERSION
    contract = payload["packages"][0]["outputStepContract"]
    assert contract["authoritativeStepCollection"] == "executionSteps"
    assert contract["operatorActionStepsGeneratedBySystem"] is True
    assert contract["topLevelExecutionObjectGeneratedBySystem"] is True
    assert contract["structuredStopConditionsRequired"] is True
    assert contract["structuredRollbackConditionsRequired"] is True
    assert "不要输出operatorActionSteps" in messages[0]["content"]
    assert "executorRole" in messages[0]["content"]


def test_normalizer_derives_operator_steps_and_task_object() -> None:
    raw = _raw_without_duplicate_fields()
    sop = _normalize_sop(raw, _package(), {})

    assert sop["sopStatus"] == SOP_READY
    assert sop["authoritativeStepCollection"] == "executionSteps"
    assert sop["operatorActionStepsSource"] == "executionSteps[*].instruction"
    assert sop["operatorActionSteps"] == [
        item["instruction"] for item in raw["executionSteps"]
    ]
    assert sop["executionObject"]["targetId"] == "P10001"
    assert sop["semanticContractMissing"] == []
    assert sop["contractValidation"]["passed"] is True
    assert missing_agent3_sop_contract(sop, _package()) == []


def test_old_duplicate_missing_codes_do_not_downgrade_valid_structured_sop() -> None:
    sop = _normalize_sop(_raw_without_duplicate_fields(), _package(), {})

    assert sop["sopStatus"] == SOP_READY
    assert sop["semanticContractMissing"] == []
    assert sop["contractValidation"]["statusDowngraded"] is False


def test_execution_object_cannot_be_a_responsible_role() -> None:
    raw = _raw_without_duplicate_fields()
    raw["executionSteps"][0]["executionObject"] = "design_team"
    raw["executionSteps"][0]["executorRole"] = "design_team"

    sop = _normalize_sop(raw, _package(), {})

    assert sop["sopStatus"] == SOP_MISSING_DATA
    assert "agent3_execution_step_1_execution_object_is_role" in sop["semanticContractMissing"]
    assert sop["contractValidation"]["statusDowngraded"] is True


def test_executor_role_is_required_independently_from_execution_object() -> None:
    raw = _raw_without_duplicate_fields()
    raw["executionSteps"][1].pop("executorRole")

    missing = validate_agent3_sop_system_contract(raw, _package())

    assert "agent3_execution_step_2_missing:executorRole" in missing


def test_contract_report_survives_status_downgrade() -> None:
    raw = _raw_without_duplicate_fields()
    raw["executionSteps"] = raw["executionSteps"][:2]

    sop = _normalize_sop(raw, _package(), {})

    assert sop["sopStatus"] == SOP_MISSING_DATA
    assert "agent3_sop_execution_steps_min_3" in sop["contractValidation"]["missing"]
    assert "agent3_sop_execution_steps_min_3" in missing_agent3_sop_contract(sop, _package())
