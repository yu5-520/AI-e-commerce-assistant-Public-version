"""Report alert task sync into TaskRepository.

The report alert service still owns alert generation and the legacy demo task
normalization. This bridge is called by official data import APIs immediately
after alert-driven auto task creation, so report-created tasks are persisted via
TaskRepository without rewriting the full import pipeline in one step.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from src.core.context import UserContext
from src.repositories.task_repository import TaskRepository
from src.services import module_task_service
from src.services.task_state_machine_service import mirror_all

REPORT_TASK_SYNC_VERSION = "5.1.6"


def _alert_task_ids(result: Dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for alert in result.get("alerts") or []:
        task_id = alert.get("taskId")
        if task_id:
            ids.append(str(task_id))
    for child in result.get("results") or []:
        ids.extend(_alert_task_ids(child))
    return list(dict.fromkeys(ids))


def sync_report_import_tasks_to_repository(result: Dict[str, Any], ctx: UserContext) -> Dict[str, Any]:
    """Persist report-created tasks into TaskRepository and annotate the result."""

    output = deepcopy(result)
    task_ids = _alert_task_ids(output)
    repo = TaskRepository(ctx)
    synced_ids: list[str] = []
    missing_ids: list[str] = []
    for task_id in task_ids:
        task = module_task_service.find_task(task_id)
        if task:
            repo.upsert(task)
            synced_ids.append(task_id)
        else:
            missing_ids.append(task_id)
    mirror_summary = mirror_all(module_task_service.TASKS, module_task_service.TASK_EVENTS, module_task_service.LOGS)
    output["taskRepositorySync"] = {
        "version": REPORT_TASK_SYNC_VERSION,
        "mode": "report_alert_auto_task_sync",
        "requestedTaskIds": task_ids,
        "syncedTaskIds": synced_ids,
        "missingTaskIds": missing_ids,
        "syncedTaskCount": len(synced_ids),
        "repository": repo.summary(),
        "mirror": mirror_summary,
        "rule": "报表预警仍负责生成任务草案；导入确认后立即同步到 TaskRepository，保持报表入库和总览刷新稳定。",
    }
    return output


def sync_report_tasks(result: Dict[str, Any], ctx: UserContext) -> Dict[str, Any]:
    """Backward-compatible alias for older import and worker callers."""

    return sync_report_import_tasks_to_repository(result, ctx)
