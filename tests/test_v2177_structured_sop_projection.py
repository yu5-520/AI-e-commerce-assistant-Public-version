from __future__ import annotations

import src  # noqa: F401

from src.services import agent2_action_plan_core_v20_service as agent2
from src.services import pipeline_sop_task_pool_v2010_service as sop_worker
from src.services import sop_builder_core_v20_service as sop_builder
from src.services import task_detail_snapshot_v2024_service as task_detail
from src.services.v2177_structured_sop_projection_service import (
    STRUCTURED_SOP_PROJECTION_VERSION,
    normalize_step,
    project_operator_execution,
    project_task_detail_snapshot,
    sanitize_agent2_step_semantics,
)


def _plan() -> dict:
    return {
        "packageId": "PKG-SOP-2177",
        "actionFamily": "roas_scale",
        "actionPlanStatus": "ready",
        "operatorActionSteps": [
            {
                "step": 1,
                "action": "新建独立广告计划",
                "testVariable": "新建计划ID，预算1301.95元",
                "lockedVariable": "主计划预算保持1172.93元不变",
                "successCondition": "计划创建成功，预算设置为1301.95元",
                "rollbackCondition": "创建失败或预算设置错误",
                "escalationCondition": "系统限制无法创建新计划",
            },
            {
                "step": 2,
                "action": "配置广告创意和定向",
                "testVariable": "使用3组创意方案进行A/B测试",
                "lockedVariable": "商品链接、价格、SKU不变",
                "successCondition": "创意全部上线",
                "rollbackCondition": "创意审核不通过",
                "escalationCondition": "连续2组创意审核失败",
            },
            {
                "step": 3,
                "action": "确认计划状态和预算写入结果",
                "successCondition": "后台状态正常",
                "rollbackCondition": "状态异常时停止计划",
                "escalationCondition": "平台接口无法返回状态",
            },
            {
                "step": 4,
                "action": "监控新计划ROI和支付增长",
                "testVariable": "新计划ROI、支付金额、付费访客数",
                "lockedVariable": "主计划投放参数不变",
                "successCondition": "新计划ROI不低于2.13",
                "rollbackCondition": "新计划ROI低于2.13持续24小时",
                "escalationCondition": "新计划ROI低于1.85或支付金额下降",
            },
        ],
        "operationPlan": {
            "operations": [
                {
                    "operationType": "budget_update",
                    "currentValue": {"budget": 1172.93},
                    "targetValue": {"budget": 1290.22},
                    "recommendedTargetValue": {"budget": 1301.95},
                    "authorizedTargetValue": {"budget": 1290.22},
                    "executedTargetValue": {"budget": 1290.22},
                    "rollbackCondition": "ROI跌破1.6时恢复至执行前预算",
                }
            ]
        },
        "budgetPlan": {
            "currentBudget": 1172.93,
            "recommendedBudget": 1301.95,
            "recommendedBudgetUpperBound": 1301.95,
            "authorizedBudget": 1290.22,
            "executedBudget": 1290.22,
            "targetBudget": 1290.22,
        },
        "crossDepartmentActions": [
            {
                "department": "warehouse",
                "deadline": "2小时内",
                "action": "确认补货计划并反馈到仓时间",
                "requiredResponse": ["补货单号", "预计补货数量", "预计到仓时间"],
            }
        ],
    }


def test_legacy_python_dict_step_is_parsed_without_showing_payload() -> None:
    step = normalize_step(
        "{'step': 1, 'action': '新建独立广告计划', 'testVariable': '预算1301.95元', "
        "'lockedVariable': '主计划预算不变', 'successCondition': '计划创建成功'}"
    )
    assert step["title"] == "新建独立广告计划"
    assert step["action"] == "新建独立广告计划"
    assert "本次变量：预算1301.95元" in step["parameters"]
    assert "{'step'" not in step["title"]


def test_roas_projection_discards_creative_semantics_and_uses_authorized_budget() -> None:
    projection = project_operator_execution(
        _plan(),
        authority={
            "parameters": {
                "currentBudget": 1172.93,
                "targetBudget": 1290.22,
            }
        },
    )
    rendered = "\n".join(projection["operatorExecutionSop"])

    assert projection["version"] == STRUCTURED_SOP_PROJECTION_VERSION
    assert projection["actionFamily"] == "roas_scale"
    assert projection["discardedCrossFamilyStepCount"] == 1
    assert len(projection["operatorExecutionSteps"]) >= 4
    assert "创意" not in rendered
    assert "A/B" not in rendered
    assert "1301.95" not in rendered
    assert "1,290.22" in rendered
    assert projection["supportingCoordination"][0]["department"] == "warehouse"
    assert "warehouse" not in rendered


def test_new_agent2_output_fails_closed_when_roas_steps_cross_families() -> None:
    plan = sanitize_agent2_step_semantics(_plan())
    assert plan["discardedCrossFamilyStepCount"] == 1
    assert len(plan["operatorActionSteps"]) == 3
    assert plan["actionPlanStatus"] == "missing_data"
    assert "operatorActionSteps.cross_family_semantics" in plan["semanticContractMissing"]


def test_task_detail_projection_rebuilds_legacy_sop_and_separates_coordination() -> None:
    raw = {
        "taskId": "LT-SOP-2177",
        "agent2ActionPlan": _plan(),
        "operationPlan": _plan()["operationPlan"],
        "authorizationDecision": {
            "parameters": {"currentBudget": 1172.93, "targetBudget": 1290.22}
        },
        "operatorExecutionSop": [
            "{'step': 1, 'action': '新建独立广告计划', 'testVariable': '预算1301.95元'}",
            "2小时内向warehouse发起协同：确认补货计划并反馈到仓时间。",
        ],
        "activeActionContract": {
            "activeActionFamily": "roas_scale",
            "activeOperationPlan": _plan()["operationPlan"],
            "supportingCoordination": _plan()["crossDepartmentActions"],
        },
        "taskDetailReport": {"taskPlan": {}},
        "relatedTask": {"taskPlan": {}},
    }
    snapshot = project_task_detail_snapshot(raw)
    rendered = "\n".join(snapshot["operatorExecutionSop"])

    assert snapshot["structuredSopProjectionVersion"] == "21.7.7"
    assert snapshot["operatorExecutionSteps"]
    assert "{'step'" not in rendered
    assert "1301.95" not in rendered
    assert "1,290.22" in rendered
    assert "warehouse" not in rendered
    assert snapshot["supportingCoordination"][0]["department"] == "warehouse"
    assert snapshot["activeActionContract"]["activeSopPlan"]["operatorExecutionSteps"]


def test_runtime_overlay_is_installed_on_agent_sop_and_task_detail_modules() -> None:
    for module in (agent2, sop_builder, sop_worker, task_detail):
        assert getattr(module, "_V2177_STRUCTURED_SOP_PROJECTION_INSTALLED", False)
        assert module.STRUCTURED_SOP_PROJECTION_VERSION == "21.7.7"
