"""V21.8.0 neural operating read model.

The read model exposes one quiet business-language projection over existing task
and dashboard read models. It never runs Agent, RAG, SOP compilation, task sync or
snapshot computation. The same signal identity and lifecycle are available to the
home page, navigation rail, module headers and system health view.

Every projection is scoped to the signed-in user's assignment and store data
scope. Global dashboard counts are never shown as an individual operator's
personal neural state.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.services.competition_operator_context_service import current_user, visible_store_ids_for_user
from src.services.operator_growth_projection_v218_service import (
    build_operator_growth_projection,
)

NEURAL_OPERATING_READ_MODEL_VERSION = "21.8.0"

_DONE_STATUS = {
    "已完成",
    "已通过",
    "已确认",
    "已归档",
    "已写入复盘",
    "completed",
    "reviewed",
    "archived",
    "learned",
}
_REVIEW_STATUS = {"待复核", "已提交", "待审核", "复核中", "review_pending", "submitted"}
_PROCESSING_STATUS = {"执行中", "处理中", "已接收", "待提交", "executing", "processing", "accepted"}
_FAILURE_TERMS = ("失败", "超时", "逾期", "阻塞", "缺失", "invalid", "dead_letter", "blocked")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _status(task: Dict[str, Any]) -> str:
    return _text(task.get("workflowStatus") or task.get("status") or task.get("displayStatus"))


def _stage(task: Dict[str, Any]) -> str:
    status = _status(task)
    if status in _DONE_STATUS:
        return "learned"
    if status in _REVIEW_STATUS:
        return "review_pending"
    if status in _PROCESSING_STATUS:
        return "executing"
    if any(term in status.lower() for term in _FAILURE_TERMS) or task.get("overdue"):
        return "blocked"
    return "action_ready"


def _signal_id(task: Dict[str, Any]) -> str:
    return _text(
        task.get("signalId")
        or task.get("pipelineItemId")
        or task.get("itemId")
        or task.get("taskId")
        or task.get("id")
    )


def _identity(task: Dict[str, Any]) -> Dict[str, Any]:
    detail = _dict(task.get("taskDetailReport"))
    card = _dict(task.get("taskCard"))
    return _dict(
        task.get("productIdentity")
        or detail.get("productIdentity")
        or card.get("productIdentity")
    )


def _task_store_id(task: Dict[str, Any]) -> str:
    identity = _identity(task)
    return _text(identity.get("storeId") or task.get("storeId"))


def _task_assignees(task: Dict[str, Any]) -> set[str]:
    return {
        _text(task.get("assigneeId")),
        _text(task.get("assigneeUserId")),
        _text(task.get("operatorId")),
        _text(task.get("ownerUserId")),
        _text(task.get("assigneeName")),
        _text(task.get("operatorName")),
    } - {""}


def _task_reviewers(task: Dict[str, Any]) -> set[str]:
    return {
        _text(task.get("reviewerId")),
        _text(task.get("reviewerUserId")),
        _text(task.get("reviewerName")),
    } - {""}


def visible_tasks_for_user(
    user_id: str | None,
    tasks: Iterable[Dict[str, Any]] | None,
) -> List[Dict[str, Any]]:
    """Return only tasks inside the signed-in user's current data scope.

    Explicit assignment wins. A task with an assignee belonging to another user is
    never pulled into the current operator's queue merely because a stale store
    reference overlaps. Unassigned tasks may fall back to the user's visible store
    scope. Owners keep the organization-wide view.
    """

    user = current_user(user_id)
    role = _text(user.get("roleId"))
    if role == "owner":
        return [item for item in (tasks or []) if isinstance(item, dict)]

    user_keys = {
        _text(user.get("id")),
        _text(user.get("name")),
        _text(user.get("displayName")),
    } - {""}
    visible_stores = set(visible_store_ids_for_user(user.get("id")))
    result: List[Dict[str, Any]] = []

    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        assignees = _task_assignees(task)
        reviewers = _task_reviewers(task)
        store_id = _task_store_id(task)

        if assignees:
            if user_keys & assignees:
                result.append(task)
                continue
            if role == "manager" and user_keys & reviewers:
                result.append(task)
            continue

        if role == "manager" and user_keys & reviewers:
            result.append(task)
            continue
        if store_id and store_id in visible_stores:
            result.append(task)
            continue
        if role in {"manager", "finance", "observer"} and not store_id:
            result.append(task)

    return result


def _signal_packet(task: Dict[str, Any]) -> Dict[str, Any]:
    identity = _identity(task)
    stage = _stage(task)
    next_stage = {
        "action_ready": "executing",
        "executing": "review_pending",
        "review_pending": "learned",
        "blocked": "attention",
        "learned": "settled",
    }.get(stage, "attention")
    return {
        "signalId": _signal_id(task),
        "pipelineItemId": task.get("pipelineItemId") or task.get("itemId"),
        "taskId": task.get("taskId") or task.get("id"),
        "productId": identity.get("productId") or task.get("productId"),
        "productTitle": identity.get("productTitle") or task.get("productTitle") or task.get("title") or "经营对象",
        "storeId": identity.get("storeId") or task.get("storeId"),
        "dataVersion": task.get("dataVersion"),
        "currentStage": stage,
        "nextStage": next_stage,
        "status": _status(task) or "待执行",
        "priority": task.get("priority") or "中",
        "deadline": task.get("deadline") or task.get("executionDeadline"),
        "actionFamily": task.get("actionFamily") or task.get("selectedActionFamily"),
        "summary": task.get("subtitle") or task.get("reason") or "经营动作等待推进",
    }


def _dashboard_count(dashboard: Dict[str, Any], *keys: str) -> int:
    counts = _dict(dashboard.get("counts"))
    for key in keys:
        value = counts.get(key)
        try:
            if value is not None:
                return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return 0


def build_neural_operating_projection(
    user_id: str | None,
    *,
    tasks: Iterable[Dict[str, Any]] | None = None,
    dashboard: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    dashboard = _dict(dashboard)
    user = current_user(user_id)
    task_list = visible_tasks_for_user(user.get("id"), tasks)
    packets = [_signal_packet(task) for task in task_list]

    task_counts = {
        "actionReady": len([item for item in packets if item["currentStage"] == "action_ready"]),
        "executing": len([item for item in packets if item["currentStage"] == "executing"]),
        "reviewPending": len([item for item in packets if item["currentStage"] == "review_pending"]),
        "learned": len([item for item in packets if item["currentStage"] == "learned"]),
        "blocked": len([item for item in packets if item["currentStage"] == "blocked"]),
    }
    active_tasks = [task for task in task_list if _stage(task) != "learned"]
    interpreted_from_tasks = len(
        [
            item
            for item in active_tasks
            if item.get("actionFamily")
            or item.get("selectedActionFamily")
            or item.get("agentJudgment")
            or item.get("agentOperatingJudgment")
        ]
    )
    role = _text(user.get("roleId"))
    if role == "owner":
        sensed = _dashboard_count(
            dashboard,
            "candidateSignals",
            "signalCount",
            "signals",
            "observedSignals",
            "changedProducts",
        ) or len({_signal_id(task) for task in active_tasks if _signal_id(task)})
        interpreted = _dashboard_count(
            dashboard,
            "interpretedSignals",
            "agentJudgments",
            "judgmentCount",
            "confirmedEvents",
        ) or interpreted_from_tasks
    else:
        sensed = len({_signal_id(task) for task in active_tasks if _signal_id(task)})
        interpreted = interpreted_from_tasks

    signal_counts = {
        "sensed": sensed,
        "interpreted": interpreted,
        **task_counts,
    }
    active_packets = [item for item in packets if item["currentStage"] != "learned"][:20]
    blocked_packets = [item for item in packets if item["currentStage"] == "blocked"][:10]
    operator_profile = build_operator_growth_projection(user.get("id"), task_list)
    active_transmission_count = (
        task_counts["actionReady"]
        + task_counts["executing"]
        + task_counts["reviewPending"]
        + task_counts["blocked"]
    )

    route_nodes = [
        {"route": "dashboard", "stage": "central", "label": "经营中枢", "count": active_transmission_count},
        {"route": "data-check", "stage": "sensed", "label": "数据感知", "count": sensed},
        {"route": "operating-unit", "stage": "interpreted", "label": "经营判断", "count": interpreted},
        {"route": "business-actions", "stage": "action_ready", "label": "任务传导", "count": active_transmission_count},
        {"route": "business-report", "stage": "learned", "label": "经营记忆", "count": task_counts["learned"]},
        {"route": "accounts", "stage": "growth", "label": "个人成长", "count": operator_profile["level"]},
        {"route": "system-status", "stage": "health", "label": "链路健康", "count": task_counts["blocked"]},
    ]

    return {
        "version": NEURAL_OPERATING_READ_MODEL_VERSION,
        "mode": "read_only_neural_operating_projection",
        "scope": {
            "userId": user.get("id"),
            "roleId": user.get("roleId"),
            "visibleStoreIds": visible_store_ids_for_user(user.get("id")),
            "taskCount": len(task_list),
        },
        "operatorProfile": operator_profile,
        "signalCounts": signal_counts,
        "routeNodes": route_nodes,
        "activeSignals": active_packets,
        "blockedSignals": blocked_packets,
        "health": {
            "status": "attention" if blocked_packets else "healthy",
            "label": "存在阻塞信号" if blocked_packets else "经营神经链路正常",
            "blockedCount": len(blocked_packets),
        },
        "lifecycle": [
            {"stage": "sensed", "label": "感知"},
            {"stage": "interpreted", "label": "判断"},
            {"stage": "action_ready", "label": "待执行"},
            {"stage": "executing", "label": "执行中"},
            {"stage": "review_pending", "label": "待验证"},
            {"stage": "learned", "label": "已沉淀"},
        ],
        "readRule": "复用现有只读模型并强制应用用户与店铺数据范围；不触发Agent、RAG、SOP、任务同步或快照重算。",
    }


__all__ = [
    "NEURAL_OPERATING_READ_MODEL_VERSION",
    "build_neural_operating_projection",
    "visible_tasks_for_user",
]
