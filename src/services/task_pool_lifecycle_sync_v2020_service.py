"""V20.24 Task Pool -> Lifecycle and task-detail projection sync.

Accepted task_pool_entries are projected into task_status and a compact
materialized task_detail_snapshot in the same write-side transaction.  Neither
projection runs on the hot task-list or task-detail GET path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, init_db, loads
from src.services.task_detail_snapshot_v2024_service import (
    TASK_DETAIL_SNAPSHOT_VERSION,
    upsert_task_detail_snapshot_in_conn,
)

TASK_POOL_LIFECYCLE_SYNC_VERSION = "20.24"
DEFAULT_MANAGER_ID = "U002"
DEFAULT_OPERATOR_ID = "U003"


def _table_exists(conn: Any, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value not in [None, "", [], {}, "UNKNOWN", "未识别", "—"]:
            return value
    return None


def _status_for_task(task: Dict[str, Any], task_layer: str | None) -> str:
    status = str(task.get("status") or task.get("workflowStatus") or "").strip()
    if status and status not in {"ready", "pending", "entered_task_pool", "completed"}:
        return status
    if task_layer == "manager_dispatch":
        return "待派发"
    return "待接收"


def _visible_actions(task_layer: str | None, status: str) -> List[Dict[str, Any]]:
    if task_layer == "manager_dispatch" or "派发" in status or "复核" in status:
        return [{"action": "review", "label": "复核", "primary": True}, {"action": "detail", "label": "详情"}]
    if "接收" in status or status in {"待处理", "待办"}:
        return [{"action": "accept", "label": "接收", "primary": True}, {"action": "detail", "label": "详情"}]
    if "提交" in status or "处理中" in status or "已接收" in status:
        return [{"action": "submit", "label": "提交", "primary": True}, {"action": "detail", "label": "详情"}]
    return [{"action": "detail", "label": "详情", "primary": True}]


def _task_from_entry(row: Any) -> Dict[str, Any]:
    payload = _load(row["payload"])
    snapshot = _dict(payload.get("snapshot"))
    task = dict(_dict(payload.get("task")))
    if not task:
        task = dict(_dict(snapshot.get("task")))
    plan = _dict(task.get("taskPlan")) or _dict(snapshot.get("taskPlan"))
    ownership = _dict(task.get("ownership"))
    product = _dict(task.get("productIdentity")) or _dict(snapshot.get("productIdentity")) or _dict(plan.get("productIdentity"))

    task_id = _first(row["task_id"], task.get("taskId"), task.get("id"), payload.get("taskId"))
    task_layer = _first(row["task_layer"], task.get("taskLayer"), "operator_execution")
    reviewer_id = _first(row["reviewer_id"], task.get("reviewerId"), ownership.get("reviewerId"), DEFAULT_MANAGER_ID)
    assignee_id = _first(row["assignee_id"], task.get("assigneeId"), ownership.get("assignedOperatorId"))
    if not assignee_id:
        assignee_id = reviewer_id if task_layer == "manager_dispatch" else DEFAULT_OPERATOR_ID

    status = _status_for_task(task, str(task_layer))
    now = datetime.now().isoformat()
    title = _first(task.get("title"), _dict(task.get("taskCard")).get("title"), product.get("productTitle"), product.get("title"), row["task_id"], "经营任务")

    task.update({
        "id": str(task_id),
        "taskId": str(task_id),
        "task_id": str(task_id),
        "dataVersion": row["data_version"] or task.get("dataVersion") or snapshot.get("dataVersion"),
        "taskSnapshotId": row["task_snapshot_id"] or task.get("taskSnapshotId") or snapshot.get("taskSnapshotId"),
        "title": title,
        "status": status,
        "workflowStatus": status,
        "displayStatus": status,
        "taskLayer": task_layer,
        "assigneeId": assignee_id,
        "assignee_id": assignee_id,
        "reviewerId": reviewer_id,
        "reviewer_id": reviewer_id,
        "visibleUserIds": list(dict.fromkeys([x for x in [assignee_id, reviewer_id, "U001"] if x])),
        "visibleRoleIds": task.get("visibleRoleIds") or ["owner", "manager", "operator"],
        "productIdentity": product or task.get("productIdentity") or {},
        "productId": _first(task.get("productId"), product.get("productId"), row["task_id"]),
        "storeId": _first(task.get("storeId"), product.get("storeId")),
        "taskPoolEntryId": row["pool_entry_id"],
        "poolEntryId": row["pool_entry_id"],
        "source": task.get("source") or payload.get("source") or "task_pool_entries",
        "taskReadModelSource": "task_pool_entries.projected_to_task_status_v2024",
        "visibleTaskActions": task.get("visibleTaskActions") or task.get("availableActions") or _visible_actions(str(task_layer), status),
        "availableActions": task.get("availableActions") or _visible_actions(str(task_layer), status),
        "updatedAt": row["updated_at"] or task.get("updatedAt") or now,
        "createdAt": row["created_at"] or task.get("createdAt") or now,
        "lifecycleProjectionVersion": TASK_POOL_LIFECYCLE_SYNC_VERSION,
        "taskDetailSnapshotVersion": TASK_DETAIL_SNAPSHOT_VERSION,
    })
    return task


def _upsert_task_status_in_conn(conn: Any, task: Dict[str, Any]) -> None:
    task_id = task.get("taskId") or task.get("task_id") or task.get("id")
    conn.execute("""
        INSERT INTO task_status (
            task_id, workflow_run_id, task_type, risk_level, approval_status,
            status, workflow_status, assignee_id, reviewer_id,
            auto_execution_allowed, payload, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            updated_at=excluded.updated_at
    """, (
        task_id,
        task.get("dataVersion"),
        task.get("taskType"),
        task.get("riskLevel") or task.get("priority"),
        "pending" if task.get("taskLayer") == "manager_dispatch" else "not_required",
        task.get("status"),
        task.get("workflowStatus"),
        task.get("assigneeId"),
        task.get("reviewerId"),
        0 if task.get("taskLayer") == "manager_dispatch" else 1,
        dumps(task),
        task.get("updatedAt"),
    ))


def sync_task_pool_entries_to_task_status(data_version: str | None = None, *, limit: int = 300) -> Dict[str, Any]:
    """Project accepted task-pool entries into lifecycle and detail snapshots."""
    init_db()
    with connect() as conn:
        if not _table_exists(conn, "task_pool_entries"):
            return {"version": TASK_POOL_LIFECYCLE_SYNC_VERSION, "synced": 0, "reason": "no_task_pool_entries"}
        params: List[Any] = []
        where = "WHERE status = 'entered_task_pool'"
        if data_version:
            where += " AND data_version = ?"
            params.append(data_version)
        rows = conn.execute(
            f"""
            SELECT *
            FROM task_pool_entries
            {where}
            ORDER BY COALESCE(updated_at, created_at) DESC
            LIMIT ?
            """,
            (*params, int(limit)),
        ).fetchall()

        synced = 0
        detail_snapshots = 0
        skipped = 0
        errors: List[Dict[str, Any]] = []
        for row in rows:
            try:
                task = _task_from_entry(row)
                if not task.get("taskId"):
                    skipped += 1
                    continue
                _upsert_task_status_in_conn(conn, task)
                snapshot_result = upsert_task_detail_snapshot_in_conn(conn, task)
                if snapshot_result.get("stored"):
                    detail_snapshots += 1
                synced += 1
            except Exception as exc:
                errors.append({"taskPoolEntryId": row["pool_entry_id"], "taskId": row["task_id"], "error": str(exc)[:240]})
        conn.commit()
    return {
        "version": TASK_POOL_LIFECYCLE_SYNC_VERSION,
        "dataVersion": data_version,
        "candidateCount": len(rows),
        "synced": synced,
        "detailSnapshots": detail_snapshots,
        "taskDetailSnapshotVersion": TASK_DETAIL_SNAPSHOT_VERSION,
        "skipped": skipped,
        "errors": errors[:20],
        "hotPath": False,
        "rule": "V20.24: task_status and task_detail_snapshots are materialized together on the write side; GET paths only read projections.",
    }
