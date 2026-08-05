"""P0 task state machine and persistence mirror.

This module is a safe bridge between the current demo in-memory task runtime and
P0 SaaS task persistence. It does not force the existing UI to switch storage in
one step; instead it mirrors task status, task events and task logs into SQLite
with strict transition validation contracts.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable

from src.repositories.sqlite_repository import connect, dumps, init_db, loads

TASK_STATE_VERSION = "5.1.1"

DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "待拆分": {"待接收", "已归档"},
    "待接收": {"处理中", "待接收", "已归档"},
    "待确认": {"处理中", "待接收", "已归档"},
    "已派发": {"处理中", "待接收", "已归档"},
    "处理中": {"待复核", "已完成", "已退回", "已归档"},
    "已退回": {"处理中", "待复核", "已归档"},
    "待复核": {"已完成", "已退回", "已归档"},
    "已提交": {"待复核", "已完成", "已退回", "已归档"},
    "已完成": {"已写入复盘", "已归档"},
    "复核通过": {"已写入复盘", "已归档"},
    "已通过": {"已写入复盘", "已归档"},
    "已写入复盘": {"已归档"},
    "已归档": set(),
}

ACTION_TARGET_STATUS = {
    "task_created": None,
    "task_merged": None,
    "manager_assigned": "待接收",
    "manager_split": "待接收",
    "operator_accepted": "处理中",
    "operator_submitted": "待复核",
    "manager_returned": "已退回",
    "manager_approved": "已完成",
    "task_completed": "已完成",
    "task_written_to_recap": "已写入复盘",
    "task_pinned": None,
    "task_reordered": None,
    "demo_reset": None,
    "数据版本回滚": "待复核",
}


def ensure_task_persistence_tables() -> None:
    """Create P0 task mirror tables and add SaaS columns to legacy task_status."""

    init_db()
    with connect() as conn:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(task_status)").fetchall()}
        task_status_columns = {
            "tenant_id": "TEXT DEFAULT 'tenant_demo'",
            "org_id": "TEXT DEFAULT 'org_demo'",
            "store_group_id": "TEXT",
            "store_id": "TEXT",
            "dedupe_key": "TEXT",
            "deleted_at": "TEXT",
            "deleted_by": "TEXT",
            "delete_reason": "TEXT",
            "created_at": "TEXT",
        }
        for name, definition in task_status_columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE task_status ADD COLUMN {name} {definition}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_events (
                event_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                tenant_id TEXT DEFAULT 'tenant_demo',
                org_id TEXT DEFAULT 'org_demo',
                event_type TEXT NOT NULL,
                event_label TEXT,
                actor_user_id TEXT,
                actor_role TEXT,
                actor_name TEXT,
                from_status TEXT,
                to_status TEXT,
                from_workflow_status TEXT,
                to_workflow_status TEXT,
                message TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                deleted_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_logs (
                log_id TEXT PRIMARY KEY,
                task_id TEXT,
                tenant_id TEXT DEFAULT 'tenant_demo',
                org_id TEXT DEFAULT 'org_demo',
                log_type TEXT NOT NULL,
                action TEXT,
                result TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                deleted_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_evidence (
                evidence_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                tenant_id TEXT DEFAULT 'tenant_demo',
                org_id TEXT DEFAULT 'org_demo',
                submitter_id TEXT,
                evidence_type TEXT,
                note TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                deleted_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status_tenant_store ON task_status(tenant_id, store_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status_dedupe ON task_status(tenant_id, dedupe_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_status_active ON task_status(tenant_id, assignee_id, status, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_events_task_time ON task_events(task_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_logs_task_time ON task_logs(task_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_evidence_task_time ON task_evidence(task_id, created_at)")
        conn.commit()


def task_store_id(task: Dict[str, Any]) -> str | None:
    store_ids = task.get("storeIds") or task.get("visibleStoreIds") or []
    if isinstance(store_ids, list) and store_ids:
        return str(store_ids[0])
    return task.get("storeId") or task.get("store_id")


def assert_transition_allowed(from_status: str | None, to_status: str | None, *, action: str = "") -> None:
    """Raise ValueError when a task status transition violates the P0 state machine."""

    if not to_status or to_status == from_status:
        return
    if not from_status:
        return
    allowed = ALLOWED_TRANSITIONS.get(str(from_status), set())
    if allowed and to_status in allowed:
        return
    # Keep old demo compatibility for states not yet normalized, but block done-state reopening.
    if from_status in DONE_STATUS and to_status not in {"已归档", "已写入复盘"}:
        raise ValueError(f"非法任务状态跃迁：{from_status} -> {to_status} ({action})")


def mirror_task(task: Dict[str, Any], *, tenant_id: str = "tenant_demo", org_id: str = "org_demo") -> None:
    """Persist the latest task snapshot to task_status."""

    if not task.get("id"):
        return
    ensure_task_persistence_tables()
    store_id = task_store_id(task)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO task_status (
                task_id, workflow_run_id, task_type, risk_level, approval_status,
                status, workflow_status, assignee_id, reviewer_id,
                auto_execution_allowed, payload, updated_at,
                tenant_id, org_id, store_group_id, store_id, dedupe_key,
                deleted_at, deleted_by, delete_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                workflow_run_id=excluded.workflow_run_id,
                task_type=excluded.task_type,
                risk_level=excluded.risk_level,
                approval_status=excluded.approval_status,
                status=excluded.status,
                workflow_status=excluded.workflow_status,
                assignee_id=excluded.assignee_id,
                reviewer_id=excluded.reviewer_id,
                auto_execution_allowed=excluded.auto_execution_allowed,
                payload=excluded.payload,
                updated_at=excluded.updated_at,
                tenant_id=excluded.tenant_id,
                org_id=excluded.org_id,
                store_group_id=excluded.store_group_id,
                store_id=excluded.store_id,
                dedupe_key=excluded.dedupe_key,
                deleted_at=excluded.deleted_at,
                deleted_by=excluded.deleted_by,
                delete_reason=excluded.delete_reason
            """,
            (
                task.get("id"),
                task.get("workflowRunId") or task.get("workflow_run_id"),
                task.get("taskType") or task.get("task_type"),
                task.get("priority") or task.get("riskLevel"),
                task.get("approvalStatus") or task.get("approval_status"),
                task.get("status"),
                task.get("workflowStatus"),
                task.get("assigneeId"),
                task.get("reviewerId"),
                1 if task.get("autoExecutionAllowed") or task.get("auto_execution_allowed") else 0,
                dumps(task),
                task.get("updatedAt") or task.get("updated_at"),
                task.get("tenantId") or task.get("tenant_id") or tenant_id,
                task.get("orgId") or task.get("org_id") or org_id,
                task.get("storeGroupId") or task.get("store_group_id"),
                store_id,
                task.get("dedupeKey") or task.get("dedupe_key"),
                task.get("deletedAt") or task.get("deleted_at"),
                task.get("deletedBy") or task.get("deleted_by"),
                task.get("deleteReason") or task.get("delete_reason"),
                task.get("createdAt") or task.get("created_at"),
            ),
        )
        conn.commit()


def mirror_event(event: Dict[str, Any], task: Dict[str, Any] | None = None, *, tenant_id: str = "tenant_demo", org_id: str = "org_demo") -> None:
    if not event.get("id"):
        return
    ensure_task_persistence_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_events (
                event_id, task_id, tenant_id, org_id, event_type, event_label,
                actor_user_id, actor_role, actor_name, from_status, to_status,
                from_workflow_status, to_workflow_status, message, payload,
                created_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("id"),
                event.get("taskId"),
                (task or {}).get("tenantId") or tenant_id,
                (task or {}).get("orgId") or org_id,
                event.get("eventType"),
                event.get("eventLabel"),
                event.get("actorUserId"),
                event.get("actorRole"),
                event.get("actorName"),
                event.get("fromStatus"),
                event.get("toStatus"),
                event.get("fromWorkflowStatus"),
                event.get("toWorkflowStatus"),
                event.get("message"),
                dumps(event),
                event.get("createdAt"),
                event.get("deletedAt") or event.get("deleted_at"),
            ),
        )
        conn.commit()


def mirror_log(log: Dict[str, Any], *, tenant_id: str = "tenant_demo", org_id: str = "org_demo") -> None:
    if not log.get("id"):
        return
    ensure_task_persistence_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO task_logs (
                log_id, task_id, tenant_id, org_id, log_type, action,
                result, payload, created_at, deleted_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log.get("id"),
                log.get("taskId") or log.get("productId"),
                log.get("tenantId") or tenant_id,
                log.get("orgId") or org_id,
                log.get("type"),
                log.get("action"),
                log.get("result"),
                dumps(log),
                log.get("createdAt"),
                log.get("deletedAt") or log.get("deleted_at"),
            ),
        )
        conn.commit()


def load_task_snapshots(include_deleted: bool = False) -> list[Dict[str, Any]]:
    """Load persisted task snapshots for demo hydration after server restart."""

    ensure_task_persistence_tables()
    where = "" if include_deleted else "WHERE deleted_at IS NULL"
    with connect() as conn:
        rows = conn.execute(f"SELECT * FROM task_status {where} ORDER BY updated_at DESC").fetchall()
    result: list[Dict[str, Any]] = []
    for row in rows:
        payload = loads(row["payload"])
        if not payload:
            continue
        payload.setdefault("id", row["task_id"])
        payload.setdefault("status", row["status"])
        payload.setdefault("workflowStatus", row["workflow_status"])
        payload.setdefault("assigneeId", row["assignee_id"])
        payload.setdefault("reviewerId", row["reviewer_id"])
        result.append(payload)
    return result


def task_persistence_summary() -> dict[str, Any]:
    ensure_task_persistence_tables()
    with connect() as conn:
        task_total = conn.execute("SELECT COUNT(*) AS count FROM task_status WHERE deleted_at IS NULL").fetchone()["count"]
        event_total = conn.execute("SELECT COUNT(*) AS count FROM task_events WHERE deleted_at IS NULL").fetchone()["count"]
        log_total = conn.execute("SELECT COUNT(*) AS count FROM task_logs WHERE deleted_at IS NULL").fetchone()["count"]
        active_total = conn.execute(
            "SELECT COUNT(*) AS count FROM task_status WHERE deleted_at IS NULL AND status NOT IN ('已完成','已拒绝','已确认','已归档','已通过','已写入复盘')"
        ).fetchone()["count"]
    return {
        "version": TASK_STATE_VERSION,
        "status": "sqlite_mirror_enabled",
        "taskTotal": task_total,
        "activeTaskTotal": active_total,
        "eventTotal": event_total,
        "logTotal": log_total,
        "nextStep": "Replace in-memory TASKS with TaskRepository as source of truth after demo verification.",
    }


def mirror_all(tasks: Iterable[Dict[str, Any]], events: Iterable[Dict[str, Any]], logs: Iterable[Dict[str, Any]]) -> dict[str, Any]:
    for task in tasks:
        mirror_task(task)
    for event in events:
        linked_task = next((task for task in tasks if task.get("id") == event.get("taskId")), None)
        mirror_event(event, linked_task)
    for log in logs:
        mirror_log(log)
    return task_persistence_summary()
