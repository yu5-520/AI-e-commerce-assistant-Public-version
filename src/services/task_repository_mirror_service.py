"""TaskRepository PostgreSQL mirror service."""

from __future__ import annotations

from typing import Any, Dict

from src.core.context import UserContext
from src.db.repositories import ProductionTaskRepository, production_repository_summary
from src.db.session import get_session_factory
from src.services.repository_mirror_base_service import mirror_enabled, mirror_failed, mirror_skipped, mirror_summary, repository_mode, run_mirror
from src.services.trace_audit_service import write_audit_log

TASK_REPOSITORY_MIRROR_VERSION = "5.3.8"


async def _mirror_task_async(ctx: UserContext, task: Dict[str, Any], action: str) -> Dict[str, Any]:
    async with get_session_factory()() as session:
        repo = ProductionTaskRepository(session, ctx)
        mirrored = await repo.upsert(task)
        await session.commit()
    write_audit_log(ctx, trace_id=task.get("traceId") or task.get("trace_id") or "TASK_MIRROR", event_type="task.production_mirrored", resource_type="decision_task", resource_id=mirrored.get("taskId"), action=action, status="mirrored", payload={"mode": repository_mode()})
    return {"version": TASK_REPOSITORY_MIRROR_VERSION, "action": action, "mode": repository_mode(), "mirrored": True, "status": "mirrored", "task": mirrored}


async def _soft_delete_async(ctx: UserContext, reason: str, trace_id: str) -> Dict[str, Any]:
    async with get_session_factory()() as session:
        repo = ProductionTaskRepository(session, ctx)
        deleted = await repo.soft_delete_visible(reason=reason)
        await session.commit()
    write_audit_log(ctx, trace_id=trace_id, event_type="task.production_soft_deleted", resource_type="decision_task", resource_id="visible_tasks", action="reset_tasks", status="mirrored", payload={"softDeletedTasks": deleted, "mode": repository_mode()})
    return {"version": TASK_REPOSITORY_MIRROR_VERSION, "action": "reset_tasks", "mode": repository_mode(), "mirrored": True, "status": "mirrored", "softDeletedTasks": deleted}


def mirror_task_to_production(ctx: UserContext, task: Dict[str, Any] | None, *, action: str) -> Dict[str, Any]:
    if not mirror_enabled():
        return mirror_skipped(action, version=TASK_REPOSITORY_MIRROR_VERSION)
    if not task:
        return mirror_skipped(action, reason="task is empty", version=TASK_REPOSITORY_MIRROR_VERSION)
    try:
        return run_mirror(_mirror_task_async(ctx, task, action), action, version=TASK_REPOSITORY_MIRROR_VERSION)
    except Exception as exc:  # noqa: BLE001
        return mirror_failed(action, exc, version=TASK_REPOSITORY_MIRROR_VERSION)


def mirror_task_reset_to_production(ctx: UserContext, *, reason: str, trace_id: str) -> Dict[str, Any]:
    if not mirror_enabled():
        return mirror_skipped("reset_tasks", version=TASK_REPOSITORY_MIRROR_VERSION)
    try:
        return run_mirror(_soft_delete_async(ctx, reason, trace_id), "reset_tasks", version=TASK_REPOSITORY_MIRROR_VERSION)
    except Exception as exc:  # noqa: BLE001
        return mirror_failed("reset_tasks", exc, version=TASK_REPOSITORY_MIRROR_VERSION)


def task_repository_mirror_summary() -> Dict[str, Any]:
    return mirror_summary(name="taskHybridMirror", resources=["DecisionTask"], version=TASK_REPOSITORY_MIRROR_VERSION, extra={"productionRepository": production_repository_summary()})
