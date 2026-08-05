"""V16.24 TaskRepository backed by the SQLite task persistence mirror.

TaskRepository remains part of the active V16 task lifecycle path, but it no
longer imports the deleted ``src.core.context`` module. Scope is normalized by
``src.repositories.scoped_repository.UserContext``.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

from src.repositories.scoped_repository import ScopedRepositoryBase, UserContext, item_visible_to_context
from src.repositories.sqlite_repository import connect, dumps, init_db, loads
from src.services.task_state_machine_service import ensure_task_persistence_tables, mirror_task


TASK_REPOSITORY_VERSION = "16.24"
DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}


class TaskRepository(ScopedRepositoryBase):
    resource_name = "task_status"

    def __init__(self, ctx: Any | None = None) -> None:
        super().__init__(UserContext.from_any(ctx))
        ensure_task_persistence_tables()

    def _row_to_task(self, row: Any) -> Dict[str, Any]:
        payload = loads(row["payload"])
        payload.setdefault("id", row["task_id"])
        payload.setdefault("taskId", row["task_id"])
        payload.setdefault("status", row["status"])
        payload.setdefault("workflowStatus", row["workflow_status"])
        payload.setdefault("assigneeId", row["assignee_id"])
        payload.setdefault("reviewerId", row["reviewer_id"])
        payload.setdefault("tenantId", row["tenant_id"])
        payload.setdefault("orgId", row["org_id"])
        payload.setdefault("storeGroupId", row["store_group_id"])
        payload.setdefault("storeId", row["store_id"])
        payload.setdefault("dedupeKey", row["dedupe_key"])
        payload.setdefault("updatedAt", row["updated_at"])
        payload.setdefault("createdAt", row["created_at"])
        payload.setdefault("repositoryVersion", TASK_REPOSITORY_VERSION)
        return payload

    def list(self, *, active_only: bool = False, assignee_id: str | None = None, limit: int = 200) -> list[Dict[str, Any]]:
        where = ["deleted_at IS NULL", "tenant_id = ?", "org_id = ?"]
        params: list[Any] = [self.ctx.tenant_id, self.ctx.org_id]
        if active_only:
            where.append("status NOT IN ('已完成','已拒绝','已确认','已归档','已通过','已写入复盘')")
        if assignee_id:
            where.append("assignee_id = ?")
            params.append(assignee_id)
        sql = f"SELECT * FROM task_status WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        tasks = [self._row_to_task(row) for row in rows]
        return [task for task in tasks if item_visible_to_context(task, self.ctx)]

    def get(self, task_id: str) -> Dict[str, Any] | None:
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_status WHERE task_id = ? AND tenant_id = ? AND org_id = ? AND deleted_at IS NULL",
                (task_id, self.ctx.tenant_id, self.ctx.org_id),
            ).fetchone()
        if not row:
            return None
        task = self._row_to_task(row)
        return task if item_visible_to_context(task, self.ctx) else None

    def find_by_dedupe_key(self, dedupe_key: str, *, active_only: bool = False) -> Dict[str, Any] | None:
        where = ["tenant_id = ?", "org_id = ?", "dedupe_key = ?", "deleted_at IS NULL"]
        params: list[Any] = [self.ctx.tenant_id, self.ctx.org_id, dedupe_key]
        if active_only:
            where.append("status NOT IN ('已完成','已拒绝','已确认','已归档','已通过','已写入复盘')")
        with connect() as conn:
            row = conn.execute(
                f"SELECT * FROM task_status WHERE {' AND '.join(where)} ORDER BY updated_at DESC LIMIT 1",
                params,
            ).fetchone()
        if not row:
            return None
        task = self._row_to_task(row)
        return task if item_visible_to_context(task, self.ctx) else None

    def upsert(self, task: Dict[str, Any]) -> Dict[str, Any]:
        enriched = dict(task)
        enriched.setdefault("tenantId", self.ctx.tenant_id)
        enriched.setdefault("orgId", self.ctx.org_id)
        mirror_task(enriched, tenant_id=self.ctx.tenant_id, org_id=self.ctx.org_id)
        return enriched

    def bulk_upsert(self, tasks: Iterable[Dict[str, Any]]) -> int:
        count = 0
        for task in tasks:
            self.upsert(task)
            count += 1
        return count

    def soft_delete(self, task_id: str, *, deleted_by: str | None = None, reason: str = "soft delete") -> bool:
        with connect() as conn:
            result = conn.execute(
                """
                UPDATE task_status
                SET deleted_at = datetime('now'), deleted_by = ?, delete_reason = ?
                WHERE task_id = ? AND tenant_id = ? AND org_id = ? AND deleted_at IS NULL
                """,
                (deleted_by or self.ctx.user_id, reason, task_id, self.ctx.tenant_id, self.ctx.org_id),
            )
            conn.commit()
        return result.rowcount > 0

    def soft_delete_all_visible(self, *, deleted_by: str | None = None, reason: str = "runtime reset") -> int:
        visible_ids = [task.get("id") for task in self.list(active_only=False, limit=5000) if task.get("id")]
        if not visible_ids:
            return 0
        placeholders = ",".join("?" for _ in visible_ids)
        with connect() as conn:
            result = conn.execute(
                f"""
                UPDATE task_status
                SET deleted_at = datetime('now'), deleted_by = ?, delete_reason = ?
                WHERE tenant_id = ? AND org_id = ? AND deleted_at IS NULL AND task_id IN ({placeholders})
                """,
                [deleted_by or self.ctx.user_id, reason, self.ctx.tenant_id, self.ctx.org_id, *visible_ids],
            )
            conn.commit()
        return result.rowcount

    def summary(self) -> dict[str, Any]:
        visible = self.list(active_only=False, limit=1000)
        active = [task for task in visible if task.get("status") not in DONE_STATUS]
        return {
            "source": "TaskRepository(SQLite mirror)",
            "version": TASK_REPOSITORY_VERSION,
            "tenantId": self.ctx.tenant_id,
            "orgId": self.ctx.org_id,
            "visibleTasks": len(visible),
            "activeTasks": len(active),
            "queryPlan": self.query_plan().where,
        }


def bootstrap_task_repository() -> None:
    """Ensure DB tables exist without needing a request context."""

    init_db()
    ensure_task_persistence_tables()
