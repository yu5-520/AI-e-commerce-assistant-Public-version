from __future__ import annotations

import json

from src.services.agent3_sop_core_v225_service import (
    SOP_MISSING_DATA,
    SOP_READY,
    _build_messages,
    _normalize_sop,
)
from src.services.agent3_system_constraint_v23213_service import (
    AGENT3_SYSTEM_CONSTRAINT_VERSION,
    compile_agent3_provider_package,
    validate_agent3_sop_system_contract,
)


def _polluted_title_package() -> dict:
    return {
        "packageId": "PKG-TITLE-1",
        "itemId": "PI-TITLE-1",
        "dataVersion": "DV-1",
        "productId": "P10001",
        "storeId": "STORE-1",
        "productTitle": "轻薄防晒外套",
        "productIdentity": {
            "productId": "P10001",
            "storeId": "STORE-1",
            "productTitle": "轻薄防晒外套",
            "platform": "抖音",
            "verticalCategory": "服饰",
        },
        "lockedActionFamily": "title_image_test",
        "agent1DecisionIR": {
            "decisionType": "act",
            "decisionSummary": "点击率连续下降",
            "selectedActionFamily": "title_image_test",
            "evidenceSlice": {
                "constraints": {
                    "inventoryCoordination": {
                        "operatorResponsibility": "发起仓储协同并等待真实库存反馈",
                        "warehouseRequiredResponse": ["实际库存", "在途库存", "补货数量"],
                    }
                }
            },
        },
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "primaryBusinessSignal": "点击率下降",
        },
        "actionParameterPack": {
            "executionObjects": [{"targetType": "product", "targetId": "P10001"}],
            "creativeTest": {
                "requiredVariants": 3,
                "controlVariables": ["价格", "投放", "详情页"],
            },
            "inventoryCoordination": {
                "operatorResponsibility": "发起仓储协同并等待真实库存反馈",
                "trafficControlRule": "库存不能单独触发暂停广告",
                "warehouseRequiredResponse": ["实际库存", "在途库存", "补货数量"],
            },
        },
        "recentFiveOrLatestFacts": [
            {"metric": "clickRate", "previous": 0.0205, "current": 0.0191},
            {"metric": "conversionRate", "previous": 0.0378, "current": 0.0346},
        ],
        "agent2ActionDraft": {
            "packageId": "PKG-TITLE-1",
            "productId": "P10001",
            "storeId": "STORE-1",
            "actionFamily": "title_image_test",
            "draftStatus": "draft_ready",
            "actionIntent": "制作三组标题主图并进行同流量条件测试",
            "executionTargets": [{"targetType": "product", "targetId": "P10001"}],
            "parameterRanges": {"variantCount": 3, "testHours": 6},
            "riskBoundaries": ["价格、投放和详情页保持不变"],
            "validationMetrics": ["点击率", "转化率", "支付金额"],
            "requiredEvidence": ["三组素材截图", "分组测试结果"],
            "differentiationReason": "点击率下降是主问题，转化率作为风险边界",
        },
        "companyOperatingPolicySnapshot": {
            "managementStyle": "少而准、运营可直接执行",
            "principles": [
                "库存问题归仓储协同，不归运营绩效",
                "SOP必须有对象、时限和完成标准",
            ],
            "taskTimingPolicy": {"urgent": "6小时内", "normal": "12小时内"},
        },
        "companySopRagSnapshot": {
            "companyExecutionPrinciples": [
                "先保留执行前基线，再改变唯一变量",
                "SOP只在Agent1动作族和Agent2草案边界内展开",
            ]
        },
        "approvalPolicySnapshot": {"operatorAutoExecuteWithinAuthority": True},
        "brandStyleSnapshot": {"platform": "抖音", "brandTone": "场景直接、利益点前置"},
        "inputContract": {
            "schema": "agent_input.agent3_sop.v1",
            "agent3SystemConstraintRequired": True,
        },
    }


def _valid_title_sop() -> dict:
    return {
        "packageId": "PKG-TITLE-1",
        "productId": "P10001",
        "storeId": "STORE-1",
        "actionFamily": "title_image_test",
        "sopStatus": "sop_ready",
        "finalTaskTitle": "轻薄防晒外套三组标题主图测试",
        "executionObjective": "在价格、投放和详情页不变的条件下验证点击承接",
        "executionObject": {"targetType": "product", "targetId": "P10001"},
        "operatorActionSteps": [
            "6小时内完成三组标题与主图创意稿，每组突出一个不同卖点。",
            "审核三组素材后按相近曝光量建立三组测试，价格、投放和详情页保持不变。",
            "测试满6小时或每组达到1000次曝光后，对比点击率、转化率和支付金额。",
        ],
        "executionSteps": [
            {
                "stepId": "STEP-1",
                "actionFamily": "title_image_test",
                "actionType": "creative_production",
                "executionObject": "P10001三组标题主图",
                "executorRole": "design_team",
                "instruction": "制作三组标题与主图创意稿，每组突出一个不同卖点",
                "deadline": "6小时内",
                "completionCriteria": "三组标题与主图均完成并可进入审核",
            },
            {
                "stepId": "STEP-2",
                "actionFamily": "title_image_test",
                "actionType": "experiment_grouping",
                "executionObject": "P10001三组测试素材",
                "executorRole": "operation_specialist",
                "instruction": "按相近曝光量建立三组测试并保持其他变量不变",
                "deadline": "素材审核通过后1小时内",
                "completionCriteria": "三组测试均上线且控制变量一致",
            },
            {
                "stepId": "STEP-3",
                "actionFamily": "title_image_test",
                "actionType": "result_review",
                "executionObject": "P10001测试结果",
                "executorRole": "data_analyst",
                "instruction": "对比点击率、转化率和支付金额并选择胜出组",
                "deadline": "测试满6小时或每组1000次曝光后",
                "completionCriteria": "形成胜出组、继续条件和回滚判断",
            },
        ],
        "decisionBranches": [],
        "submissionEvidence": ["三组素材截图", "测试分组截图", "指标对比截图"],
        "crossDepartmentActions": [],
        "approvalFlow": {"approvalRequired": False},
        "reviewMetrics": ["点击率", "转化率", "支付金额"],
        "verificationPeriod": "6小时或每组1000次曝光",
        "stopConditions": [
            {
                "conditionId": "STOP-1",
                "actionFamily": "title_image_test",
                "conditionType": "metric_guardrail",
                "condition": "任一组转化率较系统冻结证据下降超过15%",
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
                "rollbackAction": "恢复测试前素材",
                "evidenceRequired": "恢复后页面截图",
            }
        ],
        "reviewCycle": ["6小时", "3天", "7天"],
        "companyStyleReason": "保持唯一变量并用真实指标选择胜出素材",
        "ragUsedCaseIds": [],
        "ragRejectedCaseIds": [],
        "ragApplicationReason": "按当前商品数据和公司执行边界生成",
        "semanticContractMissing": [],
    }


def test_agent3_provider_package_is_system_compiled_and_cross_family_clean() -> None:
    provider_package = compile_agent3_provider_package(_polluted_title_package())
    serialized = json.dumps(provider_package, ensure_ascii=False)

    assert provider_package["systemConstraintContract"]["version"] == AGENT3_SYSTEM_CONSTRAINT_VERSION
    assert provider_package["lockedActionFamily"] == "title_image_test"
    assert "agent1DecisionIR" not in provider_package
    assert "agent1OperatingJudgment" not in provider_package
    assert "actionParameterPack" not in provider_package
    assert "inventoryCoordination" not in serialized
    assert "仓储" not in serialized
    assert "库存" not in serialized
    assert "补货" not in serialized
    assert provider_package["systemCompletedFacts"]["baselineRetentionHandledBySystem"] is True
    assert provider_package["constraints"]["systemFactsCannotBecomeOperatorActions"] is True
    assert "creative_production" in provider_package["allowedActionTypes"]
    assert "inventory_coordination" in provider_package["forbiddenActions"]


def test_agent3_actual_user_payload_contains_only_system_compiled_packages() -> None:
    messages, payload = _build_messages("DV-1", [_polluted_title_package()])
    user_payload = json.loads(messages[1]["content"])
    serialized = json.dumps(user_payload, ensure_ascii=False)

    assert payload == user_payload
    assert user_payload["systemConstraintVersion"] == AGENT3_SYSTEM_CONSTRAINT_VERSION
    assert "actionSources" in user_payload["packages"][0]
    assert "constraints" in user_payload["packages"][0]
    assert "systemCompletedFacts" in user_payload["packages"][0]
    assert "仓储" not in serialized
    assert "库存" not in serialized
    assert "补货" not in serialized


def test_agent3_validator_rejects_cross_family_and_system_fact_actions() -> None:
    raw = _valid_title_sop()
    raw["executionSteps"][0]["instruction"] = "先执行基线数据留存，再联系仓储确认实际库存和补货数量。"
    raw["executionSteps"][0]["actionType"] = "inventory_coordination"

    sop = _normalize_sop(raw, _polluted_title_package(), {})

    assert sop["sopStatus"] == SOP_MISSING_DATA
    missing = sop["semanticContractMissing"]
    assert any(item.startswith("agent3_sop_cross_family_contamination:") for item in missing)
    assert any(item.startswith("agent3_system_fact_converted_to_action:") for item in missing)
    assert any("action_type_forbidden:inventory_coordination" in item for item in missing)


def test_agent3_validator_requires_executable_structured_steps() -> None:
    raw = _valid_title_sop()
    raw["executionSteps"][1].pop("deadline")
    raw["executionSteps"][2].pop("completionCriteria")

    missing = validate_agent3_sop_system_contract(raw, _polluted_title_package())

    assert "agent3_execution_step_2_missing:deadline" in missing
    assert "agent3_execution_step_3_missing:completionCriteria" in missing


def test_agent3_valid_title_image_sop_passes_system_contract() -> None:
    sop = _normalize_sop(_valid_title_sop(), _polluted_title_package(), {})

    assert sop["sopStatus"] == SOP_READY
    assert sop["semanticContractMissing"] == []
    assert sop["systemConstraintVersion"] == AGENT3_SYSTEM_CONSTRAINT_VERSION
