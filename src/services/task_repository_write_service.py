"""TaskRepository write-path transition service with trace audit and hybrid mirror."""

from __future__ import annotations

from typing import Any, Dict

from src.core.context import UserContext
from src.repositories.task_repository import TaskRepository
from src.services import module_task_service
from src.services.task_repository_mirror_service import mirror_task_reset_to_production, mirror_task_to_production
from src.services.task_state_machine_service import ACTION_TARGET_STATUS, assert_transition_allowed, mirror_all
from src.services.trace_audit_service import resolve_trace_id, write_audit_log

TASK_WRITE_VERSION = "12.11.1"


def _task_id(task: Dict[str, Any] | None) -> str | None:
    if not task:
        return None
    return task.get("id") or task.get("taskId") or task.get("task_id")


def _repository_response(ctx: UserContext, task: Dict[str, Any] | None, *, action: str, message: str, trace_id: str | None = None, production_mirror: Dict[str, Any] | None = None) -> Dict[str, Any]:
    repo = TaskRepository(ctx)
    return {
        "version": TASK_WRITE_VERSION,
        "traceId": trace_id,
        "action": action,
        "message": message,
        "task": task,
        "repository": repo.summary(),
        "productionMirror": production_mirror or {"status": "not_attempted"},
        "source": {"inMemoryTasks": len(module_task_service.TASKS), "inMemoryEvents": len(module_task_service.TASK_EVENTS), "inMemoryLogs": len(module_task_service.LOGS)},
        "nextStep": "TaskRepository write path supports SQLite-first PostgreSQL mirror in hybrid/postgres mode.",
    }


def create_task_with_repository(payload: Dict[str, Any], ctx: UserContext) -> Dict[str, Any]:
    trace_id = resolve_trace_id(payload, "TASKTRACE")
    task_payload = {**dict(payload), "traceId": trace_id}
    task_payload.setdefault("tenantId", ctx.tenant_id)
    task_payload.setdefault("orgId", ctx.org_id)
    task = module_task_service.create_task(task_payload)
    if isinstance(task, dict):
        task["traceId"] = task.get("traceId") or trace_id
        task.setdefault("tenantId", ctx.tenant_id)
        task.setdefault("orgId", ctx.org_id)
    repo = TaskRepository(ctx)
    repo.upsert(task)
    mirror_all(module_task_service.TASKS, module_task_service.TASK_EVENTS, module_task_service.LOGS)
    write_audit_log(ctx, trace_id=trace_id, event_type="task.created", resource_type="task", resource_id=_task_id(task), action="create_task", status=(task or {}).get("status"), payload={"sourceModule": (task or {}).get("sourceModule"), "title": (task or {}).get("title")})
    production_mirror = mirror_task_to_production(ctx, task, action="create_task")
    return _repository_response(ctx, task, action="create_task", message="任务已写入 SQLite Demo，并按配置尝试镜像到 PostgreSQL Repository。", trace_id=trace_id, production_mirror=production_mirror)


def transition_task_with_repository(task_id: str, action: str, payload: Dict[str, Any] | None, ctx: UserContext) -> Dict[str, Any]:
    payload = payload or {}
    existing = module_task_service.find_task(task_id) or TaskRepository(ctx).get(task_id)
    trace_id = resolve_trace_id(payload or existing or {"taskId": task_id}, "TASKTRACE")
    if not existing:
        write_audit_log(ctx, trace_id=trace_id, event_type="task.transition_missing", resource_type="task", resource_id=task_id, action=action, status="not_found", payload={})
        return _repository_response(ctx, None, action=action, message="任务不存在或当前账号无权访问。", trace_id=trace_id)
    from_status = existing.get("status")
    normalized_action = {
        "accept": "operator_accepted",
        "assign": "manager_assigned",
        "split": "manager_assigned",
        "submit": "operator_submitted",
        "review_approve": "manager_approved",
        "review_return": "manager_returned",
        "complete": "task_completed",
        "recap_complete": "task_written_to_recap",
    }.get(action, action)
    target_status = ACTION_TARGET_STATUS.get(normalized_action)
    if target_status is not None:
        assert_transition_allowed(from_status, target_status, action=normalized_action)
    from src.services.task_lifecycle_state_machine_service import transition_lifecycle_task

    result = transition_lifecycle_task(task_id, action, actor_user_id=payload.get("actorUserId") or ctx.user_id, payload={**payload, "traceId": trace_id}, ctx=ctx)
    task = result.get("task") if isinstance(result, dict) else None
    if not task:
        write_audit_log(ctx, trace_id=trace_id, event_type="task.transition_failed", resource_type="task", resource_id=task_id, action=action, status="failed", payload={"fromStatus": from_status, "targetStatus": target_status})
        return _repository_response(ctx, None, action=action, message="任务流转失败。", trace_id=trace_id)
    task["traceId"] = task.get("traceId") or trace_id
    task.setdefault("tenantId", ctx.tenant_id)
    task.setdefault("orgId", ctx.org_id)
    repo = TaskRepository(ctx)
    repo.upsert(task)
    mirror_all(module_task_service.TASKS, module_task_service.TASK_EVENTS, module_task_service.LOGS)
    write_audit_log(ctx, trace_id=trace_id, event_type="task.transitioned", resource_type="task", resource_id=task_id, action=action, status=task.get("status"), payload={"fromStatus": from_status, "targetStatus": target_status})
    production_mirror = mirror_task_to_production(ctx, task, action=action)
    return _repository_response(ctx, task, action=action, message="任务状态已通过 V12.11.1 生命周期状态机写入，并按配置尝试镜像到 PostgreSQL Repository。", trace_id=trace_id, production_mirror=production_mirror)


def reset_tasks_with_repository(ctx: UserContext, *, reason: str = "demo reset") -> Dict[str, Any]:
    trace_id = resolve_trace_id({"reason": reason}, "TASKRESET")
    repo = TaskRepository(ctx)
    deleted = repo.soft_delete_all_visible(deleted_by=ctx.user_id, reason=reason)
    module_task_service.reset_tasks()
    production_mirror = mirror_task_reset_to_production(ctx, reason=reason, trace_id=trace_id)
    write_audit_log(ctx, trace_id=trace_id, event_type="task.reset", resource_type="task", resource_id="visible_tasks", action="reset_tasks", status="completed", payload={"reason": reason, "softDeletedTasks": deleted, "productionMirror": production_mirror})
    return {"version": TASK_WRITE_VERSION, "traceId": trace_id, "action": "reset_tasks", "message": "当前可见任务已软删除，Demo 内存任务池已清空，并按配置尝试镜像重置到 PostgreSQL Repository。", "softDeletedTasks": deleted, "productionMirror": production_mirror, "repository": repo.summary(), "source": {"inMemoryTasks": len(module_task_service.TASKS), "inMemoryEvents": len(module_task_service.TASK_EVENTS), "inMemoryLogs": len(module_task_service.LOGS)}}
