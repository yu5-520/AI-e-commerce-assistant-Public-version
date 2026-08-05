"""ProjectionJob PostgreSQL mirror service."""

from __future__ import annotations

from typing import Any, Dict

from src.core.context import UserContext
from src.db.projection_repositories import ProductionProjectionJobRepository, projection_repository_summary
from src.db.session import get_session_factory
from src.services.repository_mirror_base_service import mirror_enabled, mirror_failed, mirror_skipped, mirror_summary, repository_mode, run_mirror

PROJECTION_MIRROR_VERSION = "5.3.8"


async def _mirror_projection_async(ctx: UserContext, payload: Dict[str, Any], action: str) -> Dict[str, Any]:
    async with get_session_factory()() as session:
        repo = ProductionProjectionJobRepository(session, ctx)
        mirrored = await repo.upsert(payload)
        await session.commit()
    return {"version": PROJECTION_MIRROR_VERSION, "action": action, "mode": repository_mode(), "mirrored": True, "status": "mirrored", "projectionJob": mirrored}


def mirror_projection_job_to_production(ctx: UserContext, payload: Dict[str, Any] | None, *, action: str = "projection_job.write") -> Dict[str, Any]:
    if not mirror_enabled():
        return mirror_skipped(action, version=PROJECTION_MIRROR_VERSION)
    if not payload:
        return mirror_skipped(action, reason="projection payload is empty", version=PROJECTION_MIRROR_VERSION)
    try:
        return run_mirror(_mirror_projection_async(ctx, payload, action), action, version=PROJECTION_MIRROR_VERSION)
    except Exception as exc:  # noqa: BLE001
        return mirror_failed(action, exc, version=PROJECTION_MIRROR_VERSION)


def projection_mirror_summary() -> Dict[str, Any]:
    return mirror_summary(name="projectionDataHybridMirror", resources=["ProjectionJob"], version=PROJECTION_MIRROR_VERSION, extra={"mirroredResources": ["ProjectionJob"], "productionRepository": projection_repository_summary()})
