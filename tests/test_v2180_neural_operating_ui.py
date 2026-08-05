from __future__ import annotations

import src  # noqa: F401

from src.services.neural_operating_read_model_v218_service import (
    NEURAL_OPERATING_READ_MODEL_VERSION,
    build_neural_operating_projection,
)
from src.services.operator_growth_projection_v218_service import (
    OPERATOR_GROWTH_PROJECTION_VERSION,
    build_operator_growth_projection,
)


def _tasks() -> list[dict]:
    return [
        {
            "taskId": "T-PENDING",
            "assigneeId": "U003",
            "assigneeName": "运营 A",
            "status": "待接收",
            "priority": "高",
            "productIdentity": {"productId": "P1", "productTitle": "通勤背包"},
            "actionFamily": "roas_scale",
            "pipelineItemId": "PI-1",
        },
        {
            "taskId": "T-EXECUTING",
            "assigneeId": "U003",
            "status": "处理中",
            "priority": "中",
            "productIdentity": {"productId": "P2", "productTitle": "收纳架"},
            "actionFamily": "title_image_test",
            "pipelineItemId": "PI-2",
        },
        {
            "taskId": "T-REVIEW",
            "assigneeId": "U003",
            "status": "待复核",
            "priority": "中",
            "productIdentity": {"productId": "P3", "productTitle": "凉鞋"},
            "actionFamily": "conversion_repair",
            "pipelineItemId": "PI-3",
        },
        {
            "taskId": "T-DONE",
            "assigneeId": "U003",
            "status": "已完成",
            "productIdentity": {"productId": "P4", "productTitle": "雨伞"},
        },
        {
            "taskId": "T-LEARNED",
            "assigneeId": "U003",
            "status": "已归档",
            "productIdentity": {"productId": "P5", "productTitle": "防晒衣"},
        },
        {
            "taskId": "T-BLOCKED",
            "assigneeId": "U003",
            "status": "执行超时",
            "overdue": True,
            "productIdentity": {"productId": "P6", "productTitle": "行李箱"},
        },
        {
            "taskId": "T-OTHER",
            "assigneeId": "U004",
            "status": "已归档",
            "productIdentity": {"productId": "P7", "productTitle": "床品"},
        },
    ]


def test_operator_growth_uses_verified_personal_task_history() -> None:
    profile = build_operator_growth_projection("U003", _tasks())

    assert profile["version"] == OPERATOR_GROWTH_PROJECTION_VERSION
    assert profile["displayName"] == "小羽"
    assert profile["positionTitle"] == "中级运营"
    assert profile["tenureDays"] > 0
    assert profile["completedTaskCount"] == 2
    assert profile["reviewedTaskCount"] == 1
    assert profile["learnedTaskCount"] == 1
    assert profile["experience"] == 65
    assert profile["level"] == 1
    assert profile["publicRankingEnabled"] is False
    assert "不改变岗位、权限、薪资或组织归属" in profile["experienceRule"]


def test_neural_projection_connects_scoped_signal_lifecycle_and_route_nodes() -> None:
    projection = build_neural_operating_projection(
        "U003",
        tasks=_tasks(),
        dashboard={
            "counts": {
                "candidateSignals": 9,
                "agentJudgments": 4,
            }
        },
    )

    assert projection["version"] == NEURAL_OPERATING_READ_MODEL_VERSION
    assert projection["mode"] == "read_only_neural_operating_projection"
    assert projection["scope"]["userId"] == "U003"
    assert projection["scope"]["taskCount"] == 6
    assert projection["signalCounts"] == {
        "sensed": 4,
        "interpreted": 3,
        "actionReady": 1,
        "executing": 1,
        "reviewPending": 1,
        "learned": 2,
        "blocked": 1,
    }
    assert projection["health"]["status"] == "attention"
    assert projection["operatorProfile"]["displayName"] == "小羽"
    assert [item["stage"] for item in projection["lifecycle"]] == [
        "sensed",
        "interpreted",
        "action_ready",
        "executing",
        "review_pending",
        "learned",
    ]
    route_nodes = {item["route"]: item for item in projection["routeNodes"]}
    assert route_nodes["data-check"]["count"] == 4
    assert route_nodes["operating-unit"]["count"] == 3
    assert route_nodes["business-actions"]["count"] == 4
    assert route_nodes["business-report"]["count"] == 2
    assert route_nodes["accounts"]["count"] == 1
    assert route_nodes["system-status"]["count"] == 1
    assert projection["activeSignals"][0]["signalId"] == "PI-1"
    assert all(item["taskId"] != "T-OTHER" for item in projection["activeSignals"])
    assert any(item["currentStage"] == "blocked" for item in projection["blockedSignals"])
    assert "不触发Agent" in projection["readRule"]
