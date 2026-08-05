"""V21 ultra-light task list read model.

The task page reads task_status only.  V21 additionally projects the compact
authority result so the UI never guesses whether a ROAS task belongs to the
operator or manager.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads

TASK_FAST_READ_MODEL_VERSION = "21.0"


def _table_exists(conn: Any, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        try:
            data = json.loads(value or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value not in [None, "", [], {}, "UNKNOWN", "未识别", "—"]:
            return value
    return default


def _actions(task_layer: str | None, status: str | None) -> List[Dict[str, Any]]:
    status_text = str(status or "")
    if task_layer in {"manager_dispatch", "manager_approval"} or "派发" in status_text or "复核" in status_text or "审批" in status_text:
        return [{"action": "review", "label": "复核", "primary": True}, {"action": "detail", "label": "详情"}]
    if "接收" in status_text or status_text in {"待办", "待处理", "pending", "ready"}:
        return [{"action": "accept", "label": "接收", "primary": True}, {"action": "detail", "label": "详情"}]
    if "提交" in status_text or "处理中" in status_text or "已接收" in status_text:
        return [{"action": "submit", "label": "提交", "primary": True}, {"action": "detail", "label": "详情"}]
    return [{"action": "detail", "label": "详情", "primary": True}]


def _authorization_label(decision: str | None) -> str:
    return {
        "auto_execute": "运营权限内",
        "manager_approval_required": "超权限待总管审批",
        "owner_approval_required": "重大边界待老板确认",
        "authorization_data_missing": "权限参数不完整",
    }.get(str(decision or ""), "沿用现有权限")


def _card(row: Any) -> Dict[str, Any]:
    payload = _load(row["payload"])
    product = _dict(payload.get("productIdentity"))
    task_plan = _dict(payload.get("taskPlan"))
    task_card = _dict(payload.get("taskCard"))
    lifecycle = _dict(payload.get("taskLifecycle"))
    authorization = _dict(payload.get("authorizationDecision") or payload.get("actionAuthorization") or task_plan.get("authorizationDecision"))

    task_id = str(_first(row["task_id"], payload.get("taskId"), payload.get("id"), default=""))
    status = _first(row["workflow_status"], row["status"], payload.get("workflowStatus"), payload.get("status"), default="待接收")
    task_layer = _first(payload.get("taskLayer"), payload.get("task_layer"), default="operator_execution")
    title = _first(payload.get("title"), task_card.get("title"), task_plan.get("title"), product.get("productTitle"), product.get("title"), default="经营任务")
    action_family = _first(payload.get("actionFamily"), task_plan.get("selectedActionFamily"), task_plan.get("actionFamily"), default="经营动作")
    deadline = _first(payload.get("executionDeadline"), task_card.get("executionDeadline"), task_plan.get("executionDeadline"), payload.get("deadline"), default="6小时内")
    actions = payload.get("visibleTaskActions") if isinstance(payload.get("visibleTaskActions"), list) else payload.get("availableActions") if isinstance(payload.get("availableActions"), list) else _actions(str(task_layer), str(status))
    auth_decision = authorization.get("decision")

    return {
        "id": task_id,
        "taskId": task_id,
        "task_id": task_id,
        "dataVersion": _first(payload.get("dataVersion"), row["workflow_run_id"]),
        "title": title,
        "status": status,
        "workflowStatus": status,
        "displayStatus": status,
        "taskLayer": task_layer,
        "taskType": _first(payload.get("taskType"), row["task_type"]),
        "riskLevel": _first(payload.get("riskLevel"), row["risk_level"]),
        "priority": _first(payload.get("priority"), row["risk_level"], default="中"),
        "assigneeId": _first(payload.get("assigneeId"), row["assignee_id"]),
        "reviewerId": _first(payload.get("reviewerId"), row["reviewer_id"]),
        "storeId": _first(payload.get("storeId"), product.get("storeId")),
        "storeName": _first(payload.get("storeName"), product.get("storeName"), default="任务池"),
        "store": _first(payload.get("store"), payload.get("storeName"), product.get("storeName"), default="任务池"),
        "platform": _first(payload.get("platform"), product.get("platform"), default="经营单元"),
        "productId": _first(payload.get("productId"), product.get("productId")),
        "productTitle": _first(payload.get("productTitle"), product.get("productTitle"), product.get("title")),
        "productIdentity": product,
        "actionFamily": action_family,
        "decision": _first(payload.get("decision"), payload.get("taskDecision")),
        "reason": _first(payload.get("reason"), task_plan.get("displayReason"), task_plan.get("reason")),
        "executionDeadline": deadline,
        "deadline": deadline,
        "taskLifecycle": lifecycle or {"stage": str(status), "stageLabel": str(status), "nextExpected": "查看详情"},
        "visibleTaskActions": actions,
        "availableActions": actions,
        "authorizationDecision": authorization,
        "actionAuthorization": authorization,
        "authorizationLabel": _authorization_label(auth_decision),
        "authorizationReason": authorization.get("reason"),
        "approvalRequired": bool(authorization.get("approvalRequired")),
        "autoAccepted": bool(payload.get("autoAcceptedAt") or payload.get("autoAcceptedBy")) and auth_decision == "auto_execute",
        "autoAcceptedAt": payload.get("autoAcceptedAt"),
        "effectiveAuthorityLimits": authorization.get("effectiveLimits") or {},
        "taskReadModelSource": "task_status_fast_card_v21",
        "taskFastReadModelVersion": TASK_FAST_READ_MODEL_VERSION,
        "updatedAt": row["updated_at"] or payload.get("updatedAt"),
        "createdAt": payload.get("createdAt") or row["updated_at"],
    }


def read_task_fast_views_v2021(status: str | None = None, data_version: str | None = None, limit: int = 80) -> Dict[str, Any]:
    with connect() as conn:
        if not _table_exists(conn, "task_status"):
            return {"version": TASK_FAST_READ_MODEL_VERSION, "ready": True, "count": 0, "items": [], "reason": "no_task_status_table"}
        params: List[Any] = []
        where: List[str] = []
        if status:
            where.append("(status = ? OR workflow_status = ?)")
            params.extend([status, status])
        if data_version:
            where.append("workflow_run_id = ?")
            params.append(data_version)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        rows = conn.execute(
            f"""
            SELECT task_id, workflow_run_id, task_type, risk_level,
                   approval_status, status, workflow_status, assignee_id,
                   reviewer_id, auto_execution_allowed, payload, updated_at
            FROM task_status
            {where_sql}
            ORDER BY COALESCE(updated_at, '') DESC
            LIMIT ?
            """,
            (*params, max(1, min(200, int(limit or 80)))),
        ).fetchall()
    items = [_card(row) for row in rows]
    current_data_version = items[0].get("dataVersion") if items else data_version
    return {
        "version": TASK_FAST_READ_MODEL_VERSION,
        "ready": True,
        "count": len(items),
        "items": items,
        "currentDataVersion": current_data_version,
        "taskReadModelVersion": TASK_FAST_READ_MODEL_VERSION,
        "heavyPayloadLoaded": False,
        "detailEndpoint": "/api/view/tasks/{task_id}",
        "rule": "V21: task list reads task_status only and exposes compact authority decisions without recomputation.",
    }
