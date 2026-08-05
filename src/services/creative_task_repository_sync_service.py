"""Sync creative Agent tasks into TaskRepository."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from src.core.context import UserContext
from src.repositories.task_repository import TaskRepository
from src.services.creative_vertical_agent_service import create_creative_task
from src.services.task_state_machine_service import mirror_all
from src.services import module_task_service

CREATIVE_TASK_SYNC_VERSION = "5.1.7"


def create_creative_task_with_repository(product_id: str, body: Dict[str, Any] | None, ctx: UserContext) -> Dict[str, Any] | None:
    """Create a creative Agent task and persist it into TaskRepository."""

    result = create_creative_task(product_id, body=body or {}, user_id=ctx.user_id)
    if not result:
        return None
    output = deepcopy(result)
    task = output.get("task") or {}
    repo = TaskRepository(ctx)
    if task.get("id"):
        repo.upsert(task)
    mirror = mirror_all(module_task_service.TASKS, module_task_service.TASK_EVENTS, module_task_service.LOGS)
    output["taskRepositorySync"] = {
        "version": CREATIVE_TASK_SYNC_VERSION,
        "mode": "creative_agent_task_sync",
        "taskId": task.get("id"),
        "synced": bool(task.get("id")),
        "repository": repo.summary(),
        "mirror": mirror,
        "rule": "创意 Agent 仍只生成标题 / 主图测试任务草案；入池后同步到 TaskRepository，不直接发布商品。",
    }
    if isinstance(output.get("task"), dict):
        output["task"]["repositoryWrite"] = output["taskRepositorySync"]
    return output
