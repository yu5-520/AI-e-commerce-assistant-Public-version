"""ImportJob and WorkerJob PostgreSQL mirror service."""

from __future__ import annotations

from typing import Any, Dict

from src.core.context import UserContext
from src.db.repositories import ProductionImportJobRepository, ProductionWorkerJobRepository
from src.db.session import get_session_factory
from src.services.repository_mirror_base_service import mirror_enabled, mirror_failed, mirror_skipped, mirror_summary, repository_mode, run_mirror
from src.services.trace_audit_service import write_audit_log

IMPORT_WORKER_MIRROR_VERSION = "5.3.8"


async def _mirror_import_async(ctx: UserContext, job: Dict[str, Any], action: str) -> Dict[str, Any]:
    async with get_session_factory()() as session:
        repo = ProductionImportJobRepository(session, ctx)
        mirrored = await repo.upsert(job)
        await session.commit()
    write_audit_log(ctx, trace_id=mirrored.get("traceId") or job.get("traceId") or "IMPORT_MIRROR", event_type="import_job.production_mirrored", resource_type="import_job", resource_id=mirrored.get("importJobId"), action=action, status="mirrored", payload={"mode": repository_mode(), "status": mirrored.get("status")})
    return {"version": IMPORT_WORKER_MIRROR_VERSION, "action": action, "mode": repository_mode(), "mirrored": True, "status": "mirrored", "importJob": mirrored}


async def _mirror_worker_async(ctx: UserContext, job: Dict[str, Any], action: str) -> Dict[str, Any]:
    async with get_session_factory()() as session:
        repo = ProductionWorkerJobRepository(session, ctx)
        mirrored = await repo.upsert(job)
        await session.commit()
    write_audit_log(ctx, trace_id=mirrored.get("traceId") or job.get("traceId") or "WORKER_MIRROR", event_type="worker_job.production_mirrored", resource_type="worker_job", resource_id=mirrored.get("workerJobId"), action=action, status="mirrored", payload={"mode": repository_mode(), "status": mirrored.get("status")})
    return {"version": IMPORT_WORKER_MIRROR_VERSION, "action": action, "mode": repository_mode(), "mirrored": True, "status": "mirrored", "workerJob": mirrored}


def mirror_import_job_to_production(ctx: UserContext, job: Dict[str, Any] | None, *, action: str) -> Dict[str, Any]:
    if not mirror_enabled():
        return mirror_skipped(action, version=IMPORT_WORKER_MIRROR_VERSION)
    if not job:
        return mirror_skipped(action, reason="import job is empty", version=IMPORT_WORKER_MIRROR_VERSION)
    try:
        return run_mirror(_mirror_import_async(ctx, job, action), action, version=IMPORT_WORKER_MIRROR_VERSION)
    except Exception as exc:  # noqa: BLE001
        return mirror_failed(action, exc, version=IMPORT_WORKER_MIRROR_VERSION)


def mirror_worker_job_to_production(ctx: UserContext, job: Dict[str, Any] | None, *, action: str) -> Dict[str, Any]:
    if not mirror_enabled():
        return mirror_skipped(action, version=IMPORT_WORKER_MIRROR_VERSION)
    if not job:
        return mirror_skipped(action, reason="worker job is empty", version=IMPORT_WORKER_MIRROR_VERSION)
    try:
        return run_mirror(_mirror_worker_async(ctx, job, action), action, version=IMPORT_WORKER_MIRROR_VERSION)
    except Exception as exc:  # noqa: BLE001
        return mirror_failed(action, exc, version=IMPORT_WORKER_MIRROR_VERSION)


def import_worker_mirror_summary() -> Dict[str, Any]:
    return mirror_summary(name="importWorkerHybridMirror", resources=["ImportJob", "WorkerJob"], version=IMPORT_WORKER_MIRROR_VERSION, extra={"mirroredResources": ["ImportJob", "WorkerJob"]})
