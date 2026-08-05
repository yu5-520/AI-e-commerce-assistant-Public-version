"""Data version rollback, delete, linked-task strategy, and detail service.

Report imports create data versions. Wrong report uploads can be soft-rolled back,
linked tasks can be handled by a clear strategy, and demo-stage records can be
fully deleted so repeated tests do not stack stale imports.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.account_service import user_display
from src.services.import_row_store_service import ensure_import_row_table
from src.services.module_task_service import DONE_STATUS, find_task, update_task
from src.services.report_alert_service import ACTIVE_ALERT_STATUSES, ensure_v3_tables, list_alert_events, now_iso

DATA_VERSION_SERVICE_VERSION = "5.0.9"
ROLLBACK_VERSION = DATA_VERSION_SERVICE_VERSION
ROLLBACK_STATUSES = {"rolled_back", "rollback_pending"}
TASK_STRATEGIES = {"review", "archive", "keep"}


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def ensure_version_tables() -> None:
    ensure_v3_tables()
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_version_rollbacks (
                rollback_id TEXT PRIMARY KEY,
                data_version TEXT NOT NULL,
                snapshot_id TEXT,
                dataset_name TEXT,
                reason TEXT,
                operator_id TEXT,
                operator_name TEXT,
                affected_alert_count INTEGER NOT NULL,
                affected_task_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_version_rollbacks_version ON data_version_rollbacks(data_version, created_at)")
        conn.commit()


def _snapshot(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    return payload or {
        "snapshotId": row["snapshot_id"],
        "importId": row["import_id"],
        "datasetName": row["dataset_name"],
        "dataVersion": row["data_version"],
        "rowCount": row["row_count"],
        "createdAt": row["created_at"],
    }


def _rollback_rows(limit: int = 200) -> List[Dict[str, Any]]:
    ensure_version_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM data_version_rollbacks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        payload = loads(row["payload"])
        result.append(payload or {
            "rollbackId": row["rollback_id"],
            "dataVersion": row["data_version"],
            "snapshotId": row["snapshot_id"],
            "datasetName": row["dataset_name"],
            "reason": row["reason"],
            "operatorId": row["operator_id"],
            "operatorName": row["operator_name"],
            "affectedAlertCount": row["affected_alert_count"],
            "affectedTaskCount": row["affected_task_count"],
            "status": row["status"],
            "createdAt": row["created_at"],
        })
    return result


def _latest_rollback_map() -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for item in _rollback_rows(500):
        result.setdefault(item.get("dataVersion"), item)
    return result


def list_import_records(limit: int = 50) -> Dict[str, Any]:
    ensure_version_tables()
    with connect() as conn:
        snapshots = conn.execute("SELECT * FROM data_snapshots ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        alert_rows = conn.execute(
            """
            SELECT data_version, status, COUNT(*) AS count, COUNT(task_id) AS task_count
            FROM alert_events
            GROUP BY data_version, status
            """
        ).fetchall()
    rollback_by_version = _latest_rollback_map()
    alert_summary: Dict[str, Dict[str, int]] = {}
    for row in alert_rows:
        version = row["data_version"]
        alert_summary.setdefault(version, {"alertCount": 0, "activeAlertCount": 0, "taskCount": 0, "rolledBackAlertCount": 0})
        alert_summary[version]["alertCount"] += int(row["count"] or 0)
        alert_summary[version]["taskCount"] += int(row["task_count"] or 0)
        if row["status"] in ACTIVE_ALERT_STATUSES:
            alert_summary[version]["activeAlertCount"] += int(row["count"] or 0)
        if row["status"] in ROLLBACK_STATUSES:
            alert_summary[version]["rolledBackAlertCount"] += int(row["count"] or 0)
    records: List[Dict[str, Any]] = []
    for row in snapshots:
        item = _snapshot(row)
        version = item.get("dataVersion") or row["data_version"]
        summary = alert_summary.get(version, {"alertCount": 0, "activeAlertCount": 0, "taskCount": 0, "rolledBackAlertCount": 0})
        rollback = rollback_by_version.get(version)
        status = "rolled_back" if rollback else "active"
        records.append({
            **item,
            "versionStatus": status,
            "rollback": rollback,
            "alertCount": summary["alertCount"],
            "activeAlertCount": summary["activeAlertCount"],
            "taskCount": summary["taskCount"],
            "rolledBackAlertCount": summary["rolledBackAlertCount"],
            "canRollback": status != "rolled_back" and summary["activeAlertCount"] > 0,
            "canDelete": True,
        })
    return {
        "version": DATA_VERSION_SERVICE_VERSION,
        "total": len(records),
        "activeCount": len([item for item in records if item.get("versionStatus") == "active"]),
        "rolledBackCount": len([item for item in records if item.get("versionStatus") == "rolled_back"]),
        "records": records,
        "rollbacks": _rollback_rows(limit=limit),
    }


def _linked_task_ids(rows: List[Any]) -> List[str]:
    ids: List[str] = []
    for row in rows:
        if row["task_id"]:
            ids.append(row["task_id"])
    return list(dict.fromkeys(ids))


def _task_patch_for_strategy(task: Dict[str, Any], strategy: str, data_version: str, reason: str, created_at: str) -> Dict[str, Any]:
    base = {
        "rollbackImpact": {
            "dataVersion": data_version,
            "reason": reason,
            "strategy": strategy,
            "createdAt": created_at,
        },
        "reportDataVersionStatus": "rolled_back",
        "rollbackReviewRequired": strategy == "review",
        "judgmentTags": list(dict.fromkeys([*(task.get("judgmentTags") or []), "数据版本已回滚", data_version]))[:8],
    }
    if strategy == "archive":
        return {**base, "status": "已归档", "workflowStatus": "数据回滚归档", "candidateStatus": "completed_archived", "reviewNote": "来源数据版本已回滚，任务已保留审计并从待办移除。"}
    if strategy == "keep" or task.get("status") in DONE_STATUS:
        return {**base, "reviewNote": "来源数据版本已回滚，当前任务状态保留。"}
    roles = list(dict.fromkeys([*(task.get("visibleRoleIds") or []), "manager", "operator", "finance"]))
    return {**base, "status": "待复核", "workflowStatus": "数据回滚待复核", "visibleRoleIds": roles, "reviewNote": "来源数据版本已回滚，请总管确认该任务取消、保留或转人工处理。"}


def apply_task_strategy(task_ids: List[str], *, strategy: str, data_version: str, reason: str, operator_id: str | None, created_at: str) -> List[Dict[str, Any]]:
    strategy = strategy if strategy in TASK_STRATEGIES else "review"
    handled: List[Dict[str, Any]] = []
    for task_id in task_ids:
        task = find_task(task_id)
        if not task:
            handled.append({"taskId": task_id, "status": "not_found", "strategy": strategy})
            continue
        before_status = task.get("status")
        patch = _task_patch_for_strategy(task, strategy, data_version, reason, created_at)
        updated = update_task(task_id, patch, log_type="数据版本回滚", action=f"关联任务按{strategy}策略处理", result=f"来源数据版本 {data_version} 已回滚：{reason}")
        handled.append({
            "taskId": task_id,
            "title": task.get("title"),
            "fromStatus": before_status,
            "toStatus": updated.get("status") if updated else task.get("status"),
            "workflowStatus": updated.get("workflowStatus") if updated else task.get("workflowStatus"),
            "strategy": strategy,
            "operatorId": operator_id,
        })
    return handled


def get_data_version_detail(data_version: str, *, user_id: str | None = None) -> Dict[str, Any]:
    records_payload = list_import_records(limit=200)
    record = next((item for item in records_payload["records"] if item.get("dataVersion") == data_version), None)
    if not record:
        raise ValueError(f"data version not found: {data_version}")
    alerts = [item for item in list_alert_events(limit=1000, active_only=False, user_id=user_id) if item.get("dataVersion") == data_version]
    task_ids = list(dict.fromkeys([item.get("taskId") for item in alerts if item.get("taskId")]))
    tasks = []
    for task_id in task_ids:
        task = find_task(task_id)
        if task:
            tasks.append(deepcopy(task))
    rollback = record.get("rollback")
    return {
        "version": DATA_VERSION_SERVICE_VERSION,
        "record": record,
        "alerts": alerts,
        "tasks": tasks,
        "rollback": rollback,
        "summary": {
            "alertCount": len(alerts),
            "activeAlertCount": len([item for item in alerts if item.get("status") in ACTIVE_ALERT_STATUSES]),
            "taskCount": len(tasks),
            "rolledBack": record.get("versionStatus") == "rolled_back",
        },
    }


def rollback_data_version(data_version: str, *, operator_id: str | None = None, reason: str | None = None, task_strategy: str = "review") -> Dict[str, Any]:
    ensure_version_tables()
    reason = reason or "上传报表有误，回滚该数据版本产生的预警。"
    task_strategy = task_strategy if task_strategy in TASK_STRATEGIES else "review"
    operator_name = user_display(operator_id, "系统管理员")
    created_at = now_iso()
    with connect() as conn:
        snap = conn.execute("SELECT * FROM data_snapshots WHERE data_version = ? ORDER BY created_at DESC LIMIT 1", (data_version,)).fetchone()
        if not snap:
            raise ValueError(f"data version not found: {data_version}")
        rows = conn.execute("SELECT * FROM alert_events WHERE data_version = ?", (data_version,)).fetchall()
        active_rows = [row for row in rows if row["status"] in ACTIVE_ALERT_STATUSES]
        task_ids = _linked_task_ids(active_rows)
        handled_tasks = apply_task_strategy(task_ids, strategy=task_strategy, data_version=data_version, reason=reason, operator_id=operator_id, created_at=created_at)
        for row in active_rows:
            payload = loads(row["payload"])
            if payload:
                payload = deepcopy(payload)
                payload["status"] = "rolled_back"
                payload["rollbackAt"] = created_at
                payload["rollbackReason"] = reason
                payload["rollbackOperatorId"] = operator_id
                payload["rollbackOperatorName"] = operator_name
                payload["taskStrategy"] = task_strategy
            conn.execute(
                "UPDATE alert_events SET status = ?, payload = ?, updated_at = ? WHERE alert_id = ?",
                ("rolled_back", dumps(payload), created_at, row["alert_id"]),
            )
        rollback = {
            "rollbackId": make_id("ROLLBACK"),
            "version": DATA_VERSION_SERVICE_VERSION,
            "dataVersion": data_version,
            "snapshotId": snap["snapshot_id"],
            "datasetName": snap["dataset_name"],
            "reason": reason,
            "operatorId": operator_id,
            "operatorName": operator_name,
            "affectedAlertCount": len(active_rows),
            "affectedTaskCount": len(task_ids),
            "taskStrategy": task_strategy,
            "handledTasks": handled_tasks,
            "status": "rolled_back",
            "createdAt": created_at,
            "impact": ["该版本活跃预警已从看板移除", "关联待办已按策略处理", "后续复盘仍可查看回滚记录"],
        }
        conn.execute(
            """
            INSERT OR REPLACE INTO data_version_rollbacks (
                rollback_id, data_version, snapshot_id, dataset_name, reason,
                operator_id, operator_name, affected_alert_count, affected_task_count,
                status, created_at, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rollback["rollbackId"], data_version, rollback["snapshotId"], rollback["datasetName"], reason,
                operator_id, operator_name, rollback["affectedAlertCount"], rollback["affectedTaskCount"],
                rollback["status"], created_at, dumps(rollback),
            ),
        )
        conn.commit()
    return {"version": DATA_VERSION_SERVICE_VERSION, "rollback": rollback, "records": list_import_records(limit=50)["records"]}


def delete_data_version(data_version: str, *, operator_id: str | None = None, reason: str | None = None) -> Dict[str, Any]:
    """Hard-delete one imported data version for demo testing.

    This removes the imported rows, snapshot, metrics, alert rows and rollback row.
    Linked in-memory tasks are archived so they disappear from active queues.
    """
    ensure_version_tables()
    ensure_import_row_table()
    reason = reason or "Demo 阶段删除导入记录，避免测试数据叠加。"
    created_at = now_iso()
    with connect() as conn:
        snapshots = conn.execute("SELECT * FROM data_snapshots WHERE data_version = ?", (data_version,)).fetchall()
        if not snapshots:
            raise ValueError(f"data version not found: {data_version}")
        alert_rows = conn.execute("SELECT * FROM alert_events WHERE data_version = ?", (data_version,)).fetchall()
        task_ids = _linked_task_ids(alert_rows)
        handled_tasks = apply_task_strategy(task_ids, strategy="archive", data_version=data_version, reason=reason, operator_id=operator_id, created_at=created_at)
        snapshot_ids = [row["snapshot_id"] for row in snapshots]
        deleted = {
            "dataSnapshots": 0,
            "metricSnapshots": 0,
            "alertEvents": len(alert_rows),
            "importedRows": 0,
            "rollbacks": 0,
            "linkedTasks": len(task_ids),
        }
        for snapshot_id in snapshot_ids:
            metric_cursor = conn.execute("DELETE FROM metric_snapshots WHERE snapshot_id = ?", (snapshot_id,))
            deleted["metricSnapshots"] += metric_cursor.rowcount if metric_cursor.rowcount != -1 else 0
        imported_cursor = conn.execute("DELETE FROM imported_report_rows WHERE data_version = ?", (data_version,))
        deleted["importedRows"] = imported_cursor.rowcount if imported_cursor.rowcount != -1 else 0
        alert_cursor = conn.execute("DELETE FROM alert_events WHERE data_version = ?", (data_version,))
        deleted["alertEvents"] = alert_cursor.rowcount if alert_cursor.rowcount != -1 else deleted["alertEvents"]
        rollback_cursor = conn.execute("DELETE FROM data_version_rollbacks WHERE data_version = ?", (data_version,))
        deleted["rollbacks"] = rollback_cursor.rowcount if rollback_cursor.rowcount != -1 else 0
        snapshot_cursor = conn.execute("DELETE FROM data_snapshots WHERE data_version = ?", (data_version,))
        deleted["dataSnapshots"] = snapshot_cursor.rowcount if snapshot_cursor.rowcount != -1 else len(snapshots)
        conn.commit()
    return {
        "version": DATA_VERSION_SERVICE_VERSION,
        "deletedDataVersion": data_version,
        "reason": reason,
        "operatorId": operator_id,
        "deleted": deleted,
        "handledTasks": handled_tasks,
        "records": list_import_records(limit=50)["records"],
        "message": "导入记录已删除，相关预警和活跃任务已从演示运行态移除。",
    }
