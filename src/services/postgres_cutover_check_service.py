"""PostgreSQL primary-write cutover readiness checks.

This service does not switch write paths. It only reports whether the current
runtime is ready to move from SQLite-first mirror to a PostgreSQL-first plan.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import text

from src.core.context import UserContext
from src.db.session import database_runtime_summary, get_session_factory
from src.services.repository_runtime_service import repository_mode, repository_runtime_summary

POSTGRES_CUTOVER_CHECK_VERSION = "5.3.9"
ROOT_DIR = Path(__file__).resolve().parents[2]
REQUIRED_MIGRATIONS = [
    "alembic/versions/20260623_530_initial_p0_schema.py",
    "alembic/versions/20260623_535_data_version_alert_event.py",
]
REQUIRED_MIRRORS = [
    "taskHybridMirror",
    "importWorkerHybridMirror",
    "auditTechHybridMirror",
    "projectionDataHybridMirror",
    "dataAlertWriteMirror",
]


def _item(check_id: str, name: str, status: str, evidence: str, next_action: str = "") -> Dict[str, Any]:
    return {"id": check_id, "name": name, "status": status, "evidence": evidence, "nextAction": next_action}


def _migration_status() -> Dict[str, Any]:
    files = []
    missing = []
    for rel_path in REQUIRED_MIGRATIONS:
        exists = (ROOT_DIR / rel_path).exists()
        files.append({"path": rel_path, "exists": exists})
        if not exists:
            missing.append(rel_path)
    return {"files": files, "missing": missing, "ok": not missing}


async def _postgres_ping() -> Dict[str, Any]:
    try:
        async with get_session_factory()() as session:
            value = (await session.execute(text("SELECT 1 AS ok"))).mappings().first()
        return {"checked": True, "ok": bool(value and value.get("ok") == 1), "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"checked": True, "ok": False, "error": str(exc)}


def _mirror_items(repository: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for key in REQUIRED_MIRRORS:
        mirror = repository.get(key) or {}
        enabled = mirror.get("enabled") is True
        resources = ", ".join(mirror.get("resources") or [])
        items.append(_item(key, f"Mirror 链路：{key}", "pass" if enabled else "warn", f"mode={mirror.get('mode')}; resources={resources}", "DB_REPOSITORY_MODE=hybrid 后再做抽样 mirror 对账" if not enabled else ""))
    return items


async def postgres_cutover_check(ctx: UserContext) -> Dict[str, Any]:
    mode = repository_mode()
    repository = repository_runtime_summary(ctx)
    database = database_runtime_summary()
    migrations = _migration_status()
    postgres = await _postgres_ping() if mode in {"hybrid", "postgres"} else {"checked": False, "ok": False, "error": "DB_REPOSITORY_MODE=sqlite"}

    items: List[Dict[str, Any]] = [
        _item("repository_mode", "Repository 运行模式", "pass" if mode in {"hybrid", "postgres"} else "warn", f"current={mode}", "先使用 hybrid 验证 mirror，再考虑 postgres"),
        _item("postgres_connection", "PostgreSQL 连接", "pass" if postgres.get("ok") else "blocked", "ok" if postgres.get("ok") else str(postgres.get("error")), "配置 DATABASE_URL 并执行连接检查"),
        _item("alembic_files", "Alembic 迁移文件", "pass" if migrations["ok"] else "blocked", f"missing={migrations['missing']}", "补齐缺失迁移文件"),
        _item("mirror_base", "Mirror 公共控制层", "pass" if repository.get("mirrorBase", {}).get("enabled") else "blocked", f"version={repository.get('mirrorBase', {}).get('version')}", "确认 repository_mirror_base_service 可用"),
        _item("production_models", "生产模型覆盖", "pass", "Task / ImportJob / ProjectionJob / DataVersion / AlertEvent / WorkerJob / AuditLog / TechLog", ""),
        _item("demo_fallback", "Demo 回退链路", "pass" if repository.get("sqliteDemoFallback") else "warn", f"sqliteDemoFallback={repository.get('sqliteDemoFallback')}", "切主写前保留回滚开关"),
        _item("auth_boundary", "生产身份边界", "warn", "Demo 当前仍使用 Header 账号模拟", "切主写前接入生产 JWT / Session"),
        _item("rollback_plan", "回滚策略", "warn", "当前已有 sqlite/hybrid/postgres 开关", "切主写前写入 rollback runbook"),
    ]
    items.extend(_mirror_items(repository))

    blocked = [item for item in items if item["status"] == "blocked"]
    warns = [item for item in items if item["status"] == "warn"]
    pass_items = [item for item in items if item["status"] == "pass"]
    recommended_next = "保持 sqlite 或 hybrid；先完成 blocked 项，再做 postgres 主写切换。" if blocked else "可以进入 hybrid 抽样对账；仍不建议直接切 postgres 主写。"

    return {
        "version": POSTGRES_CUTOVER_CHECK_VERSION,
        "title": "PostgreSQL 主写切换前检查清单",
        "readyForPostgresPrimary": not blocked and mode == "postgres",
        "readyForHybridValidation": not blocked and mode == "hybrid",
        "summary": {"total": len(items), "pass": len(pass_items), "warn": len(warns), "blocked": len(blocked)},
        "mode": mode,
        "database": database,
        "postgres": postgres,
        "migrations": migrations,
        "items": items,
        "recommendedNext": recommended_next,
        "rule": "This endpoint is read-only. It reports readiness and never changes DB_REPOSITORY_MODE or write paths.",
    }
