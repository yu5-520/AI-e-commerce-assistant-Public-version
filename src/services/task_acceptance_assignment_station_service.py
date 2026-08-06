"""V13.7 Task Acceptance / Assignment Station service.

This service wraps the existing unified lifecycle state machine as explicit
stations. It does not create tasks and does not submit evidence. It only moves
visible task-pool tasks through acceptance and manager assignment gates.
"""

from __future__ import annotations

from typing import Any, Dict

from src.services import module_task_service
from src.services.competition_operator_context_service import default_reviewer
from src.services.task_lifecycle_state_machine_service import auto_accept_ready_tasks, lifecycle_state_summary, transition_lifecycle_task

TASK_ACCEPT_ASSIGN_STATION_VERSION = "13.7.0"


def accept_task(task_id: str, *, actor_user_id: str | None = None, note: str | None = None, auto: bool = False) -> Dict[str, Any]:
    action = "auto_accept" if auto else "accept"
    result = transition_lifecycle_task(
        task_id,
        action,
        actor_user_id="system" if auto else "competition_operator",
        payload={"note": note or ("系统自动接收权限内任务。" if auto else "运营接收任务，进入处理中。"), "stationId": "task_acceptance_station"},
    )
    result["stationVersion"] = TASK_ACCEPT_ASSIGN_STATION_VERSION
    result["stationId"] = "task_acceptance_station"
    return result


def auto_accept_ready_task_pool_tasks(*, viewer_id: str | None = None) -> Dict[str, Any]:
    tasks = module_task_service.list_tasks(active_only=True, viewer_id=viewer_id)
    result = auto_accept_ready_tasks(tasks, viewer_id=viewer_id)
    result["stationVersion"] = TASK_ACCEPT_ASSIGN_STATION_VERSION
    result["stationId"] = "task_acceptance_station"
    result["rule"] = "比赛版只自动接收固定运营工作台可执行任务；企业组织审批任务不在公开运行链路执行。"
    return result


def assign_task(task_id: str, *, actor_user_id: str | None = None, assignee_id: str | None = None, reviewer_id: str | None = None, note: str | None = None, split: bool = False) -> Dict[str, Any]:
    _ = actor_user_id, assignee_id, reviewer_id, note, split
    return {"version": TASK_ACCEPT_ASSIGN_STATION_VERSION, "ok": False, "stationId": "task_assignment_station", "taskId": task_id, "error": "enterprise_organization_collaboration_not_enabled", "message": "老板、主管、部门派发与审批属于企业组织协同增值能力，比赛版暂未开放。"}



def acceptance_assignment_summary() -> Dict[str, Any]:
    summary = lifecycle_state_summary()
    tasks = module_task_service.list_tasks(active_only=True)
    return {
        "version": TASK_ACCEPT_ASSIGN_STATION_VERSION,
        "waitingAccept": len([task for task in tasks if task.get("status") in {"待接收", "待确认", "已派发"}]),
        "waitingAssignment": 0,
        "processing": len([task for task in tasks if task.get("status") == "处理中"]),
        "lifecycle": summary,
        "rule": "比赛版仅开放固定运营工作台接收与执行；组织派发为企业增值能力。",
    }
