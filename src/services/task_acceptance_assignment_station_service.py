"""V13.7 Task Acceptance / Assignment Station service.

This service wraps the existing unified lifecycle state machine as explicit
stations. It does not create tasks and does not submit evidence. It only moves
visible task-pool tasks through acceptance and manager assignment gates.
"""

from __future__ import annotations

from typing import Any, Dict

from src.services import module_task_service
from src.services.account_service import default_reviewer
from src.services.task_lifecycle_state_machine_service import auto_accept_ready_tasks, lifecycle_state_summary, transition_lifecycle_task

TASK_ACCEPT_ASSIGN_STATION_VERSION = "13.7.0"


def accept_task(task_id: str, *, actor_user_id: str | None = None, note: str | None = None, auto: bool = False) -> Dict[str, Any]:
    action = "auto_accept" if auto else "accept"
    result = transition_lifecycle_task(
        task_id,
        action,
        actor_user_id=actor_user_id or ("system" if auto else "U003"),
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
    result["rule"] = "只自动接收运营权限内、无需主管/老板确认的任务；待拆分/需复核任务留给总管派发站。"
    return result


def assign_task(task_id: str, *, actor_user_id: str | None = None, assignee_id: str | None = None, reviewer_id: str | None = None, note: str | None = None, split: bool = False) -> Dict[str, Any]:
    reviewer_id = reviewer_id or (default_reviewer() or {}).get("id") or "U002"
    result = transition_lifecycle_task(
        task_id,
        "split" if split else "assign",
        actor_user_id=actor_user_id or "U002",
        payload={
            "assigneeId": assignee_id,
            "reviewerId": reviewer_id,
            "note": note or "总管派发任务，等待运营接收。",
            "stationId": "task_assignment_station",
        },
    )
    result["stationVersion"] = TASK_ACCEPT_ASSIGN_STATION_VERSION
    result["stationId"] = "task_assignment_station"
    return result


def acceptance_assignment_summary() -> Dict[str, Any]:
    summary = lifecycle_state_summary()
    tasks = module_task_service.list_tasks(active_only=True)
    return {
        "version": TASK_ACCEPT_ASSIGN_STATION_VERSION,
        "waitingAccept": len([task for task in tasks if task.get("status") in {"待接收", "待确认", "已派发"}]),
        "waitingAssignment": len([task for task in tasks if task.get("status") == "待拆分" or task.get("taskLayer") == "manager_dispatch"]),
        "processing": len([task for task in tasks if task.get("status") == "处理中"]),
        "lifecycle": summary,
        "rule": "V13.7：任务入池后，接收和派发通过独立站点写统一生命周期状态机。",
    }
