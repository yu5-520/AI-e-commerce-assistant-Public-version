from __future__ import annotations


def _internal_task() -> dict:
    return {
        "id": "TASK-1",
        "taskId": "TASK-1",
        "task_id": "TASK-1",
        "dataVersion": "DV-1",
        "title": "校准晚间低效投放",
        "status": "处理中",
        "workflowStatus": "处理中",
        "displayStatus": "处理中",
        "taskLayer": "operator_execution",
        "priority": "高",
        "productId": "P-1",
        "productTitle": "测试商品",
        "storeId": "S-1",
        "productIdentity": {
            "productId": "P-1",
            "productTitle": "测试商品",
            "storeId": "S-1",
            "storeName": "测试店铺",
            "platform": "天猫",
            "privateCost": 123,
        },
        "actionFamily": "roas_guard",
        "reason": "晚间新增计划拖低ROI",
        "executionDeadline": "6小时内",
        "visibleTaskActions": [
            {"action": "submit", "label": "提交", "primary": True},
            {"action": "detail", "label": "详情"},
        ],
        "availableActions": [{"action": "legacy", "label": "旧按钮"}],
        "authorizationDecision": {
            "decision": "auto_execute",
            "reason": "权限内",
            "approvalRequired": False,
            "effectiveLimits": {"budgetChangeCeiling": 0.2, "internalFormula": "secret"},
            "usage": {"internal": True},
        },
        "actionAuthorization": {"duplicate": True},
        "taskLifecycle": {"stage": "accepted", "stageLabel": "处理中", "internalHistory": [1, 2]},
        "agent2ActionPlan": {"secret": "provider output"},
        "agent2ExecutionProof": {"requestId": "SECRET"},
        "systemFacts": {"huge": "internal"},
        "artifactRefs": {"agent2Ref": "ART-SECRET"},
        "updatedAt": "2026-07-19T00:00:00",
    }


def test_task_list_item_has_one_id_status_action_and_authority_field() -> None:
    from src.services.public_task_dto_service import project_task_list_item

    item = project_task_list_item(_internal_task())
    assert item["taskId"] == "TASK-1"
    assert item["status"] == "处理中"
    assert item["visibleTaskActions"][0]["action"] == "submit"
    assert item["authorizationDecision"]["effectiveLimits"] == {
        "budgetChangeCeiling": 0.2
    }

    for forbidden in (
        "id",
        "task_id",
        "workflowStatus",
        "displayStatus",
        "availableActions",
        "actionAuthorization",
        "effectiveAuthorityLimits",
        "agent2ActionPlan",
        "agent2ExecutionProof",
        "systemFacts",
        "artifactRefs",
    ):
        assert forbidden not in item
    assert "privateCost" not in item["productIdentity"]
    assert "internalHistory" not in item["taskLifecycle"]
    assert "usage" not in item["authorizationDecision"]


def test_task_detail_exposes_operator_contract_not_internal_agent_package() -> None:
    from src.services.public_task_dto_service import project_task_detail

    snapshot = {
        **_internal_task(),
        "ready": True,
        "taskStatus": "处理中",
        "operatorJudgmentView": {
            "summary": "付费效率下降集中在晚间",
            "coreProblem": "晚间新增计划低效",
            "confidence": 0.88,
            "facts": ["ROI 2.8→2.1"],
            "riskBoundaries": ["预算调整不超过20%"],
        },
        "activeActionContract": {
            "actionFamily": "roas_guard",
            "target": "晚间新增计划",
            "executionObjects": ["计划A"],
        },
        "operatorExecutionSop": ["拆分晚间计划", "将预算降低10%并观察6小时"],
        "metricDigest": {"roi": {"current": 2.1, "previous": 2.8}},
        "autoReviewPlan": {
            "reviewCycle": "6小时",
            "reviewMetrics": ["ROI", "消耗"],
            "internalSchedulerId": "SECRET",
        },
        "taskDetailReport": {
            "evidenceRequirements": ["投放后台截图"],
            "agent2Provider": {"secret": True},
        },
        "operationPlan": {"internalIR": True},
        "chainIntegrity": {"internal": True},
        "relatedTask": {"duplicate": True},
    }
    detail = project_task_detail(snapshot)

    assert detail["taskId"] == "TASK-1"
    assert detail["taskStatus"] == "处理中"
    assert detail["judgmentSummary"]["coreProblem"] == "晚间新增计划低效"
    assert detail["activeActionContract"]["actionFamily"] == "roas_guard"
    assert detail["operatorExecutionSop"] == [
        "拆分晚间计划",
        "将预算降低10%并观察6小时",
    ]
    assert detail["evidenceRequirements"] == ["投放后台截图"]
    assert detail["publicContract"]["providerProofReturned"] is False

    for forbidden in (
        "id",
        "task_id",
        "status",
        "workflowStatus",
        "displayStatus",
        "relatedTask",
        "taskDetailReport",
        "agent2ActionPlan",
        "operationPlan",
        "agent2ExecutionProof",
        "actionAuthorization",
        "systemFacts",
        "artifactRefs",
        "chainIntegrity",
        "discardedCrossFamilyFields",
    ):
        assert forbidden not in detail
    assert "internalSchedulerId" not in detail["autoReviewPlan"]


def test_task_list_response_does_not_reintroduce_aliases() -> None:
    from src.services.public_task_dto_service import project_task_list_response

    response = project_task_list_response(
        {
            "ready": True,
            "currentDataVersion": "DV-1",
            "items": [_internal_task()],
            "taskReadModelVersion": "legacy",
            "heavyPayloadLoaded": False,
        }
    )
    assert response["count"] == 1
    assert response["publicContract"] == {
        "version": "22.2.3",
        "duplicateIdAliases": False,
        "duplicateStatusAliases": False,
        "duplicateActionAliases": False,
        "heavyPayloadReturned": False,
    }
    assert "tasks" not in response
    assert "activeTasks" not in response
    assert "runtimeVersion" not in response
    assert "contractVersion" not in response


def test_public_task_routes_use_dto_boundary() -> None:
    from src.api.main import app

    schema = app.openapi()
    paths = schema.get("paths") or {}
    assert "/api/view/tasks" in paths
    assert "/api/view/tasks/{task_id}" in paths

    source = __import__("pathlib").Path("src/api/routes/frontend_views.py").read_text(
        encoding="utf-8"
    )
    assert "project_task_list_response" in source
    assert "project_task_detail" in source
    assert 'return _align(result, "V22 task detail' not in source
