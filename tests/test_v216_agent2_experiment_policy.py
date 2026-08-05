from __future__ import annotations

import src  # noqa: F401

from src.services import agent2_action_plan_core_v20_service as agent2


def _package() -> dict:
    return {
        "packageId": "PKG-1",
        "itemId": "ITEM-1",
        "dataVersion": "DV-1",
        "productId": "P1",
        "storeId": "S1",
        "productIdentity": {"productId": "P1", "storeId": "S1"},
        "selectedOperatingRoute": "title_image_test_route",
        "selectedActionFamily": "title_image_test",
        "actionFamily": "title_image_test",
        "agent1OperatingJudgment": {
            "selectedOperatingRoute": "title_image_test_route",
            "selectedActionFamily": "title_image_test",
        },
        "matrixDispatch": {
            "selectedActionFamily": "title_image_test",
            "routeId": "title_image_test_route",
        },
        "actionParameterPack": {},
        "crossValidation": {
            "observationMaturity": {
                "version": "21.6.0",
                "maturity": "M1_pair_delta",
                "alignedObservationCount": 2,
            },
            "experimentPolicy": {
                "version": "21.6.0",
                "experimentMode": "isolated_test",
                "actionFamily": "title_image_test",
                "actionIntensity": "L2",
                "targetObject": "new_test_link",
                "trafficShareCeiling": 0.10,
                "budgetChangeCeiling": 0.10,
                "durationHours": 72,
                "mainlineMutationAllowed": False,
                "rollbackRequired": True,
                "allowed": True,
            },
        },
    }


def _raw_plan() -> dict:
    return {
        "packageId": "PKG-1",
        "productId": "P1",
        "storeId": "S1",
        "actionFamily": "title_image_test",
        "actionPlanStatus": "ready",
        "finalTaskTitle": "新建标题主图测试链接",
        "operationMode": "isolated_test",
        "differentiationReason": "点击承接下降，先做小流量隔离实验",
        "executionObject": {"targetId": "P1-TEST"},
        "executionParameters": {
            "trafficShare": 0.10,
            "budgetChangeRate": 0.10,
            "durationHours": 72,
        },
        "creativeTestPlan": {
            "groups": [
                {
                    "fullTitle": "方案一完整标题",
                    "mainImageStructure": {"focus": "场景"},
                    "testFocusWords": ["场景"],
                },
                {
                    "fullTitle": "方案二完整标题",
                    "mainImageStructure": {"focus": "卖点"},
                    "testFocusWords": ["卖点"],
                },
            ]
        },
        "operatorActionSteps": ["步骤1", "步骤2", "步骤3", "步骤4"],
        "executionSteps": [{"step": 1}, {"step": 2}, {"step": 3}],
        "decisionBranches": [{"when": "成功"}, {"when": "失败"}],
        "submissionEvidence": [{"type": "截图"}, {"type": "数据"}],
        "ragUsedCaseIds": [],
        "ragRejectedCaseIds": [],
        "ragApplicationReason": "无动态案例",
    }


def test_agent2_component_versions_remain_independent_after_all_overlays() -> None:
    assert agent2.AGENT2_ACTION_PLAN_CORE_VERSION == "21.4.1"
    assert agent2.AGENT2_EXPERIMENT_POLICY_VERSION == "21.6.0"
    assert getattr(agent2, "_V216_AGENT2_POLICY_INSTALLED", False)
    assert getattr(agent2, "_V2177_SINGLE_ACTION_CONTRACT_INSTALLED", False)


def test_agent2_compact_package_contains_experiment_policy() -> None:
    compact = agent2._compact_package(_package())

    assert compact["observationMaturity"]["maturity"] == "M1_pair_delta"
    assert compact["experimentPolicy"]["targetObject"] == "new_test_link"
    assert compact["experimentPolicy"]["mainlineMutationAllowed"] is False


def test_agent2_prompt_requires_direct_isolated_action() -> None:
    messages, payload = agent2._build_messages("DV-1", [_package()])
    prompt = messages[0]["content"]

    assert "禁止直接修改主链接、主计划或整体预算" in prompt
    assert "标题主图必须新建测试链接" in prompt
    assert "ROAS必须新建独立投放计划" in prompt
    assert "不得输出核查、复查、确认信息" in prompt
    assert payload["experimentPolicyVersion"] == "21.6.0"


def test_agent2_normalized_plan_keeps_experiment_permission() -> None:
    plan = agent2._normalize_plan(
        _raw_plan(),
        _package(),
        {"exactReplayValidated": False},
    )

    assert plan["experimentPolicy"]["trafficShareCeiling"] == 0.10
    assert plan["observationMaturity"]["maturity"] == "M1_pair_delta"
    assert plan["experimentPermissionApplied"] is True
    assert plan["experimentPermissionStatus"] == "passed"
    assert plan["experimentPermissionViolations"] == []


def test_agent2_rejects_plan_above_experiment_ceiling() -> None:
    raw = _raw_plan()
    raw["operationMode"] = "mainline_optimization"
    raw["executionParameters"] = {
        "trafficShare": 0.30,
        "budgetChangeRate": 0.25,
        "durationHours": 168,
    }

    plan = agent2._normalize_plan(
        raw,
        _package(),
        {"exactReplayValidated": False},
    )

    assert plan["experimentPermissionStatus"] == "rejected"
    assert plan["actionPlanStatus"] == "conflict_requires_rejudgment"
    assert plan["taskAdmissionAllowed"] is False
    assert set(plan["experimentPermissionViolations"]) == {
        "operation_mode_must_be_isolated_or_test",
        "traffic_share_exceeds_ceiling",
        "budget_change_exceeds_ceiling",
        "duration_exceeds_permission",
    }
