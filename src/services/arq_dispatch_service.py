"""ARQ dispatch helper with SQLite fallback awareness."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from arq import create_pool
from arq.connections import RedisSettings

from src.core.context import UserContext
from src.services.worker_runtime_config_service import WORKER_RUNTIME_VERSION, worker_runtime_config, worker_runtime_summary

ARQ_DISPATCH_VERSION = "5.2.3"


def _payload_with_context(ctx: UserContext, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(payload or {})
    payload.setdefault("tenantId", ctx.tenant_id)
    payload.setdefault("orgId", ctx.org_id)
    payload.setdefault("userId", ctx.user_id)
    payload.setdefault("roleId", ctx.role_id)
    payload.setdefault("roleName", ctx.role_name)
    payload.setdefault("storeIds", list(ctx.store_ids))
    payload.setdefault("permissions", list(ctx.permissions))
    return payload


async def _enqueue_arq_task(task_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    config = worker_runtime_config()
    redis = await create_pool(RedisSettings.from_dsn(config.redis_url))
    try:
        job = await redis.enqueue_job("arq_dispatch", task_name, payload, _queue_name=config.queue_name)
        return {
            "version": ARQ_DISPATCH_VERSION,
            "mode": "redis_arq_dispatch",
            "taskName": task_name,
            "arqJobId": getattr(job, "job_id", None) if job else None,
            "enqueued": bool(job),
            "queueName": config.queue_name,
        }
    finally:
        close = getattr(redis, "close", None)
        if close:
            result = close()
            if hasattr(result, "__await__"):
                await result


def dispatch_arq_or_fallback(ctx: UserContext, task_name: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Try to enqueue an ARQ dispatch job; fall back to SQLite queue on any issue."""

    config = worker_runtime_config()
    enriched_payload = _payload_with_context(ctx, payload)
    if not config.redis_enabled:
        return {
            "version": ARQ_DISPATCH_VERSION,
            "mode": "sqlite_fallback",
            "taskName": task_name,
            "enqueued": False,
            "activeBackend": "sqlite_fallback",
            "runtimeVersion": WORKER_RUNTIME_VERSION,
            "runtime": worker_runtime_summary(),
            "reason": "WORKER_RUNTIME is not redis/arq, so the local worker_jobs queue remains the execution source.",
        }
    try:
        return {**asyncio.run(_enqueue_arq_task(task_name, enriched_payload)), "activeBackend": "redis_arq"}
    except Exception as exc:  # noqa: BLE001 - dispatch failure must not break demo import.
        return {
            "version": ARQ_DISPATCH_VERSION,
            "mode": "fallback_sqlite_after_arq_error",
            "taskName": task_name,
            "enqueued": False,
            "activeBackend": "sqlite_fallback",
            "error": str(exc),
            "runtime": worker_runtime_summary(),
            "rule": "ARQ 投递失败不影响业务请求；WorkerJob 已保留在 SQLite 队列表，可手动或后续 Worker 消费。",
        }
