"""P0 SaaS architecture decomposition service."""

from __future__ import annotations

from typing import Any

from src.core.context import UserContext
from src.repositories.scoped_repository import query_plan_for_context
from src.services.task_state_machine_service import task_persistence_summary

P0_ARCHITECTURE_VERSION = "5.4.0"

P0_LAYERS: list[dict[str, Any]] = [
    {"id": "P0-1", "name": "多租户身份与数据隔离", "status": "scaffolded", "target": "UserContext + ScopedRepository", "currentGap": "Demo 身份仍来自 Header。"},
    {"id": "P0-2", "name": "软删除机制", "status": "soft_delete_mixin_scaffolded", "target": "deleted_at 统一字段", "currentGap": "生产模型和 Repository 已有软删除字段。"},
    {"id": "P0-3", "name": "任务系统", "status": "task_repository_hybrid_mirror", "target": "decision_tasks", "currentGap": "任务创建、流转、重置已支持 SQLite-first mirror。"},
    {"id": "P0-4", "name": "报表导入", "status": "data_alert_hybrid_mirror", "target": "import_jobs / projection_jobs / data_versions / alert_events", "currentGap": "ImportJob / ProjectionJob / DataVersion / AlertEvent 已支持导入完成后的 mirror。"},
    {"id": "P0-5", "name": "Repository Mirror Base", "status": "mirror_base_refactored", "target": "统一 skipped / failed / mode / event-loop guard", "currentGap": "各业务 mirror service 已统一使用 repository_mirror_base_service。"},
    {"id": "P0-6", "name": "Worker 队列", "status": "worker_job_hybrid_mirror", "target": "worker_jobs", "currentGap": "enqueue / claim / complete / fail / retry 已支持 mirror。"},
    {"id": "P0-7", "name": "LLM Gateway", "status": "llm_gateway_controls_scaffolded", "target": "配额、限流、缓存、熔断、Schema 校验", "currentGap": "控制层已接入。"},
    {"id": "P0-8", "name": "Audit / TechLog", "status": "audit_tech_hybrid_mirror", "target": "audit_logs / tech_logs", "currentGap": "业务审计和技术日志写路径已支持 mirror。"},
    {"id": "P0-9", "name": "文档分层", "status": "docs_split", "target": "README / CHANGELOG / PostgreSQL / P0 文档分工", "currentGap": "README 已精简，CHANGELOG 已独立，PostgreSQL 文档已聚焦迁移。"},
]

IMPLEMENTATION_SEQUENCE = [
    "核心 mirror 链路已完成",
    "前端系统状态页已完成",
    "repository_mirror_base_service 去重复已完成",
    "PostgreSQL 主写切换前检查清单已完成",
    "README / docs / CHANGELOG 文档分层已完成",
    "下一步：hybrid 抽样对账接口，或生产身份体系接入",
]


def p0_architecture_summary(ctx: UserContext) -> dict[str, Any]:
    query_plan = query_plan_for_context(ctx, table_alias="resource")
    return {"version": P0_ARCHITECTURE_VERSION, "title": "互联网大厂 SaaS P0 架构拆解", "runtimeMode": "docs_split", "currentContext": ctx.to_dict(), "mandatoryScopePlan": {"where": query_plan.where, "params": query_plan.params, "rule": query_plan.rule}, "taskPersistence": task_persistence_summary(), "layers": P0_LAYERS, "implementationSequence": IMPLEMENTATION_SEQUENCE, "definitionOfDone": ["README 只保留项目入口。", "CHANGELOG 独立记录版本变化。", "PostgreSQL 文档只记录数据库迁移和切换边界。"]}
