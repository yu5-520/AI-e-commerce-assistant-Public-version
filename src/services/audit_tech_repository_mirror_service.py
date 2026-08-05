"""AuditLog and TechLog PostgreSQL mirror service.

This service mirrors already-persisted SQLite audit/tech log entries into PostgreSQL.
It intentionally does not write additional audit records to avoid recursive audit logging.
"""

from __future__ import annotations

from typing import Any, Dict

from src.core.context import UserContext
from src.db.repositories import ProductionAuditRepository, ProductionTechLogRepository
from src.db.session import get_session_factory
from src.services.repository_mirror_base_service import mirror_enabled, mirror_failed, mirror_skipped, mirror_summary, repository_mode, run_mirror

AUDIT_TECH_MIRROR_VERSION = "5.3.8"


async def _mirror_audit_async(ctx: UserContext, payload: Dict[str, Any], action: str) -> Dict[str, Any]:
    async with get_session_factory()() as session:
        repo = ProductionAuditRepository(session, ctx)
        mirrored = await repo.upsert(payload)
        await session.commit()
    return {"version": AUDIT_TECH_MIRROR_VERSION, "action": action, "mode": repository_mode(), "mirrored": True, "status": "mirrored", "auditLog": mirrored}


async def _mirror_tech_async(ctx: UserContext, payload: Dict[str, Any], action: str) -> Dict[str, Any]:
    async with get_session_factory()() as session:
        repo = ProductionTechLogRepository(session, ctx)
        mirrored = await repo.upsert(payload)
        await session.commit()
    return {"version": AUDIT_TECH_MIRROR_VERSION, "action": action, "mode": repository_mode(), "mirrored": True, "status": "mirrored", "techLog": mirrored}


def mirror_audit_log_to_production(ctx: UserContext, payload: Dict[str, Any] | None, *, action: str = "audit_log.write") -> Dict[str, Any]:
    if not mirror_enabled():
        return mirror_skipped(action, version=AUDIT_TECH_MIRROR_VERSION)
    if not payload:
        return mirror_skipped(action, reason="audit payload is empty", version=AUDIT_TECH_MIRROR_VERSION)
    try:
        return run_mirror(_mirror_audit_async(ctx, payload, action), action, version=AUDIT_TECH_MIRROR_VERSION)
    except Exception as exc:  # noqa: BLE001
        return mirror_failed(action, exc, version=AUDIT_TECH_MIRROR_VERSION)


def mirror_tech_log_to_production(ctx: UserContext, payload: Dict[str, Any] | None, *, action: str = "tech_log.write") -> Dict[str, Any]:
    if not mirror_enabled():
        return mirror_skipped(action, version=AUDIT_TECH_MIRROR_VERSION)
    if not payload:
        return mirror_skipped(action, reason="tech payload is empty", version=AUDIT_TECH_MIRROR_VERSION)
    try:
        return run_mirror(_mirror_tech_async(ctx, payload, action), action, version=AUDIT_TECH_MIRROR_VERSION)
    except Exception as exc:  # noqa: BLE001
        return mirror_failed(action, exc, version=AUDIT_TECH_MIRROR_VERSION)


def audit_tech_mirror_summary() -> Dict[str, Any]:
    return mirror_summary(name="auditTechHybridMirror", resources=["AuditLog", "TechLog"], version=AUDIT_TECH_MIRROR_VERSION, extra={"mirroredResources": ["AuditLog", "TechLog"]})
