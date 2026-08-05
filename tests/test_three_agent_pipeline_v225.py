from __future__ import annotations

from src.services.agent2_action_draft_core_v225_service import (
    DRAFT_READY,
    _normalize_draft,
    missing_agent2_draft_contract,
)
from src.services.agent3_sop_core_v225_service import (
    SOP_READY,
    _normalize_sop,
    missing_agent3_sop_contract,
)
from src.services.agent_runtime_contract_v225_service import (
    build_task_mapping_decision,
    missing_task_mapping_contract,
    normalize_agent2_draft_completed_contract,
    normalize_agent3_sop_completed_contract,
)
from src.services.agent_runtime_hard_interface_v225_service import (
    agent_runtime_hard_interface_status,
)


def _proof(stage: str = "action_plan_judgment_agent") -> dict:
    return {
        "version": "22.5.0",
        "stage": stage,
        "packageId": "PKG-1",
        "semanticCallId": "CALL-1",
        "provider": "aliyun_bailian",
        "model": "qwen3.7-plus",
        "providerRequestId": "REQ-1",
        "providerCallExecuted": True,
        "exactReplayValidated": False,
        "itemCorrelationId": "PKG-1",
        "resultMatched": True,
        "resultOrigin": "provider_call",
        "inputFingerprint": "fp-1",
        "promptVersion": "22.5.0",
        "fallbackUsed": False,
        "passed": True,
    }


def _base_package() -> dict:
    return {
        "version": "22.5.0",
        "dataVersion": "DV-1",
        "itemId": "PI-1",
        "packageId": "PKG-1",
        "productId": "P10008",
        "storeId": "STORE-1",
        "productIdentity": {
            "productId": "P10008",
            "storeId": "STORE-1",
            "productTitle": "测试商品",
            "platform": "天猫",
            "verticalCategory": "服饰",
        },
        "lockedActionFamily": "conversion_repair",
        "actionFamily": "conversion_repair",
        "agent1DecisionIR": {
            "decisionType": "act",
            "decisionSummary": "流量稳定但支付转化率连续下降",
            "selectedActionFamily": "conversion_repair",
        },
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "primaryBusinessSignal": "转化下降",
            "agent1DecisionIR": {
                "decisionType": "act",
                "selectedActionFamily": "conversion_repair",
            },
        },
        "matrixDispatch": {
            "selectedActionFamily": "conversion_repair",
            "lockedByAgent1": True,
        },
        "actionParameterPack": {
            "permissionBounds": {
                "operatorCanExecute": True,
                "managerApprovalRequired": False,
            },
            "executionObjects": [
                {
                    "targetType": "product_page",
                    "targetId": "P10008",
                }
            ],
        },
        "recentFiveOrLatestFacts": [
            {"metric": "conversionRate", "trend": "down"}
        ],
        "companyOperatingPolicySnapshot": {
            "taskTimingPolicy": {
                "urgent": "6小时内",
                "normal": "12小时内",
            }
        },
    }


def _draft_raw() -> dict:
    return {
        "packageId": "PKG-1",
        "productId": "P10008",
        "storeId": "STORE-1",
        "actionFamily": "conversion_repair",
        # Old Agent2 returned this task-lifecycle word. V22.5 treats it only as
        # a compatibility alias while validating the actual draft content.
        "actionPlanStatus": "pending_execution",
        "problemNode": "详情页首屏信任承接",
        "actionIntent": "修复商品详情页到支付的承接损耗",
        "executionTargets": [
            {"targetType": "product_page", "targetId": "P10008"}
        ],
        "parameterRanges": {"testDays": [3, 5]},
        "permissionBoundary": {
            "operatorCanExecute": True,
            "managerApprovalRequired": False,
        },
        "riskBoundaries": ["不修改价格和广告计划"],
        "validationMetrics": ["支付转化率", "收藏加购率"],
        "requiredEvidence": ["修改前截图", "修改后截图"],
        "missingData": [],
        "differentiationReason": "当前损耗集中在详情页首屏，而非流量入口",
        "repairDraft": {
            "problemNode": "详情页首屏信任承接",
            "repairDirections": ["重组卖点", "补充信任证明"],
            "executionTargets": [
                {"targetType": "product_page", "targetId": "P10008"}
            ],
            "parameterRanges": {"testDays": [3, 5]},
        },
        "ragUsedCaseIds": [],
        "ragRejectedCaseIds": [],
        "ragApplicationReason": "没有可用历史案例，按当前事实形成独立草案",
    }


def _sop_raw() -> dict:
    return {
        "packageId": "PKG-1",
        "productId": "P10008",
        "storeId": "STORE-1",
        "actionFamily": "conversion_repair",
        "sopStatus": "sop_ready",
        "finalTaskTitle": "修复P10008详情页首屏信任承接",
        "executionObjective": "在不改变价格和投放的条件下验证详情页承接修复",
        "executionObject": {
            "targetType": "product_page",
            "targetId": "P10008",
        },
        "operatorActionSteps": [
            "2小时内保留当前详情页首屏截图并记录支付转化率基线。",
            "按草案重组卖点和信任证明，只修改详情页首屏。",
            "持续验证3天，对比支付转化率和收藏加购率。",
        ],
        "executionSteps": [
            {"stepId": "STEP-1", "instruction": "保存基线"},
            {"stepId": "STEP-2", "instruction": "修改详情页首屏"},
            {"stepId": "STEP-3", "instruction": "验证3天"},
        ],
        "decisionBranches": [],
        "submissionEvidence": [
            {"title": "页面截图", "requiredFields": ["before", "after"]}
        ],
        "crossDepartmentActions": [],
        "approvalFlow": {"approvalRequired": False},
        "reviewMetrics": ["支付转化率", "收藏加购率"],
        "verificationPeriod": "3天",
        "stopConditions": ["支付转化率连续两天低于执行前基线"],
        "rollbackConditions": ["触发停止条件后恢复执行前页面"],
        "reviewCycle": ["3天", "7天"],
        "companyStyleReason": "符合少而准、唯一变量和数据可追溯的执行规范",
        "ragUsedCaseIds": [],
        "ragRejectedCaseIds": [],
        "ragApplicationReason": "本次按公司执行规则组织，不照抄历史SOP",
        "semanticContractMissing": [],
    }


def test_agent2_is_a_draft_agent_not_a_final_sop_agent() -> None:
    package = _base_package()
    draft = _normalize_draft(_draft_raw(), package, _proof())

    assert draft["draftStatus"] == DRAFT_READY
    assert draft["actionFamily"] == "conversion_repair"
    assert draft["semanticContractMissing"] == []
    assert draft["finalSopGenerated"] is False
    assert "finalTaskTitle" not in draft
    assert "operatorActionSteps" not in draft
    assert "executionSteps" not in draft
    assert "approvalFlow" not in draft
    assert missing_agent2_draft_contract(draft) == []


def test_agent3_owns_company_aware_executable_sop() -> None:
    package = _base_package()
    draft = _normalize_draft(_draft_raw(), package, _proof())
    package["agent2ActionDraft"] = draft
    package["agent2DraftExecutionProof"] = _proof()

    sop = _normalize_sop(
        _sop_raw(),
        package,
        _proof("agent3_sop_agent"),
    )

    assert sop["sopStatus"] == SOP_READY
    assert sop["actionFamily"] == package["lockedActionFamily"]
    assert len(sop["operatorActionSteps"]) == 3
    assert sop["semanticContractMissing"] == []
    assert missing_agent3_sop_contract(sop, package) == []


def test_task_mapping_adds_zero_steps_beyond_agent3() -> None:
    package = _base_package()
    draft = _normalize_draft(_draft_raw(), package, _proof())
    draft_package = normalize_agent2_draft_completed_contract(
        package,
        draft,
        {"itemProvenance": {"PKG-1": _proof()}},
    )
    sop = _normalize_sop(
        _sop_raw(),
        draft_package,
        _proof("agent3_sop_agent"),
    )
    completed = normalize_agent3_sop_completed_contract(
        draft_package,
        sop,
        {"itemProvenance": {"PKG-1": _proof("agent3_sop_agent")}},
    )
    decision = build_task_mapping_decision(completed, pipeline_item_id="PI-1")

    assert decision["taskPlan"]["titleSource"] == "agent3Sop.finalTaskTitle"
    assert decision["taskPlan"]["operatorExecutionSop"] == sop["operatorActionSteps"]
    assert decision["taskPlan"]["compilerAddedStepCount"] == 0
    assert decision["taskPlan"]["sopSource"] == "v22_5_agent3_company_sop"
    assert decision["taskMappingAgentEvidence"]["noMappingLlm"] is True
    assert decision["taskMappingAgentEvidence"]["agent3ProviderTracePassed"] is True
    assert missing_task_mapping_contract(decision) == []


def test_active_runtime_exposes_three_separate_input_refs() -> None:
    status = agent_runtime_hard_interface_status()

    assert status["threeAgentPipelineVersion"] == "22.5.0"
    assert status["agent1RuntimeSource"] == "artifactRefs.agent1InputRef"
    assert status["agent2RuntimeSource"] == "artifactRefs.agent2DraftInputRef"
    assert status["agent3RuntimeSource"] == "artifactRefs.agent3SopInputRef"
    assert status["taskMappingMode"] == "deterministic_agent3_projection_only"
    assert status["fallbackAllowed"] is False
