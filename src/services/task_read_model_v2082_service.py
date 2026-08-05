"""V20.8.5 Task Read Model Alignment + Chain Gate.

V20 task pages must not read old task_pool/frontend cached tasks. A task becomes
visible only when the same pipeline item has the complete V20 chain lineage:
agent1_completed -> action_pack_ready -> agent2_completed -> sop_mapped ->
task_admitted.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.frontend_read_model_service import ensure_frontend_read_model_tables, refresh_task_views as _legacy_refresh_task_views
from src.services.legacy_task_chain_cleanup_v2085_service import REQUIRED_CHAIN_STAGES, chain_integrity_for_item, latest_data_version

TASK_READ_MODEL_V2082_VERSION = "20.8.5"
DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}
AGENT_STAGES = [
    "agent1_pending",
    "agent1_running",
    "agent1_completed",
    "agent1_failed",
    "action_pack_ready",
    "agent2_running",
    "agent2_completed",
    "agent2_failed",
    "sop_mapped",
    "task_admitted",
]


def now_iso() -> str:
    return datetime.now().isoformat()


def _table_exists(conn: Any, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _safe_load(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return {}
    try:
        return loads(value)
    except Exception:
        return {}


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except Exception:
        return default


def _latest_data_version() -> str | None:
    return latest_data_version()


def _payload(row: Any) -> Dict[str, Any]:
    data = _safe_load(_row_get(row, "payload"))
    if isinstance(data, dict) and isinstance(data.get("payload"), dict):
        merged = dict(data.get("payload") or {})
        merged.setdefault("envelope", data.get("envelope"))
        return merged
    return data if isinstance(data, dict) else {}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in [None, "", "UNKNOWN", "未识别", "—"]:
            return value
    return None


def _find_nested(data: Any, keys: List[str]) -> Any:
    if not isinstance(data, dict):
        return None
    for key in keys:
        value = data.get(key)
        if value not in [None, "", "UNKNOWN", "未识别", "—"]:
            return value
    for value in data.values():
        if isinstance(value, dict):
            found = _find_nested(value, keys)
            if found not in [None, "", "UNKNOWN", "未识别", "—"]:
                return found
    return None


def _task_id_from_task(task: Dict[str, Any], fallback: Any = None) -> str | None:
    value = _first_non_empty(task.get("id"), task.get("taskId"), task.get("task_id"), task.get("taskPoolId"), task.get("activeTaskId"), fallback)
    return str(value) if value else None


def _title_from(task: Dict[str, Any], payload: Dict[str, Any] | None = None) -> str:
    payload = payload or {}
    card = task.get("taskCard") if isinstance(task.get("taskCard"), dict) else {}
    identity = task.get("productIdentity") if isinstance(task.get("productIdentity"), dict) else {}
    title = _first_non_empty(
        task.get("title"),
        card.get("title"),
        task.get("productTitle"),
        identity.get("productTitle"),
        _find_nested(payload, ["productTitle", "title", "商品标题", "商品名称"]),
    )
    return str(title or "经营任务")[:120]


def _default_actions(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    status = str(task.get("status") or task.get("workflowStatus") or "待接收")
    if "待接收" in status or status in {"ready", "pending", "待处理"}:
        return [{"action": "accept", "label": "接收", "primary": True}, {"action": "detail", "label": "详情"}]
    if "提交" in status or "处理中" in status or "已接收" in status:
        return [{"action": "submit", "label": "提交", "primary": True}, {"action": "detail", "label": "详情"}]
    return [{"action": "detail", "label": "详情", "primary": True}]


def normalize_task(task: Dict[str, Any], *, data_version: str | None = None, payload: Dict[str, Any] | None = None, source: str = "cached", fallback_task_id: str | None = None, chain_integrity: Dict[str, Any] | None = None) -> Dict[str, Any] | None:
    if not isinstance(task, dict):
        return None
    payload = payload or {}
    task_id = _task_id_from_task(task, fallback_task_id)
    if not task_id:
        return None
    task["id"] = task_id
    task["taskId"] = task_id
    status = task.get("status") or task.get("workflowStatus") or "待接收"
    actions = task.get("visibleTaskActions") or task.get("availableActions") or _default_actions({**task, "status": status})
    product_identity = task.get("productIdentity") if isinstance(task.get("productIdentity"), dict) else {}
    product_id = _first_non_empty(task.get("productId"), product_identity.get("productId"), _find_nested(payload, ["productId", "商品ID", "商品编码"]))
    store_id = _first_non_empty(task.get("storeId"), product_identity.get("storeId"), _find_nested(payload, ["storeId", "店铺ID"]))
    return {
        **task,
        "viewVersion": TASK_READ_MODEL_V2082_VERSION,
        "id": task_id,
        "taskId": task_id,
        "task_id": task_id,
        "dataVersion": data_version or task.get("dataVersion") or _find_nested(payload, ["dataVersion", "data_version"]),
        "decisionId": _first_non_empty(task.get("decisionId"), _find_nested(payload, ["decisionId"])),
        "packageId": _first_non_empty(task.get("packageId"), _find_nested(payload, ["packageId"])),
        "productId": product_id,
        "storeId": store_id,
        "title": _title_from(task, payload),
        "status": status,
        "workflowStatus": task.get("workflowStatus") or status,
        "displayStatus": task.get("displayStatus") or status,
        "visibleTaskActions": actions,
        "availableActions": task.get("availableActions") or actions,
        "primaryTaskAction": task.get("primaryTaskAction") or (actions[0] if actions else {"action": "detail", "label": "详情", "primary": True}),
        "taskReadModelSource": source,
        "chainIntegrity": chain_integrity or {"passed": False, "missing": REQUIRED_CHAIN_STAGES, "seen": []},
        "updatedAt": task.get("updatedAt") or now_iso(),
    }


def _task_from_pipeline_row(conn: Any, row: Any) -> Dict[str, Any] | None:
    integrity = chain_integrity_for_item(conn, row["item_id"])
    if not integrity.get("passed"):
        return None
    payload = _payload(row)
    fallback_id = _first_non_empty(_row_get(row, "task_id"), payload.get("taskId"), payload.get("id"))
    if not fallback_id:
        return None
    task = payload.get("task") if isinstance(payload.get("task"), dict) else payload.get("relatedTask") if isinstance(payload.get("relatedTask"), dict) else {}
    task = {
        **task,
        "id": fallback_id,
        "taskId": fallback_id,
        "decisionId": _first_non_empty(_row_get(row, "decision_id"), payload.get("decisionId")),
        "packageId": _first_non_empty(_row_get(row, "package_id"), payload.get("packageId")),
        "productId": _first_non_empty(_row_get(row, "product_id"), payload.get("productId"), _find_nested(payload, ["productId"])),
        "storeId": _first_non_empty(_row_get(row, "store_id"), payload.get("storeId"), _find_nested(payload, ["storeId"])),
        "title": _title_from(task, payload),
        "status": task.get("status") or "待接收",
        "workflowStatus": task.get("workflowStatus") or "待接收",
        "actionFamily": _row_get(row, "action_family") or task.get("actionFamily"),
        "taskDetailReport": task.get("taskDetailReport") or payload.get("taskDetailReport"),
        "operatorExecutionSop": task.get("operatorExecutionSop") or payload.get("operatorExecutionSop"),
        "autoReviewPlan": task.get("autoReviewPlan") or payload.get("autoReviewPlan"),
        "pipelineItemId": row["item_id"],
    }
    return normalize_task(task, data_version=_row_get(row, "data_version"), payload=payload, source="pipeline_items.task_admitted.chain_passed", fallback_task_id=str(fallback_id), chain_integrity=integrity)


def pipeline_diagnostics(data_version: str | None = None) -> Dict[str, Any]:
    data_version = data_version or _latest_data_version()
    counts: Counter[str] = Counter()
    illegal_task_items: List[Dict[str, Any]] = []
    with connect() as conn:
        if _table_exists(conn, "pipeline_items"):
            if data_version:
                rows = conn.execute(
                    """
                    SELECT current_stage, status, COUNT(*) AS cnt
                    FROM pipeline_items
                    WHERE data_version = ?
                    GROUP BY current_stage, status
                    """,
                    (data_version,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT current_stage, status, COUNT(*) AS cnt FROM pipeline_items GROUP BY current_stage, status").fetchall()
            for row in rows:
                counts[f"{row['current_stage']}:{row['status']}"] += int(row["cnt"] or 0)
            params: List[Any] = []
            where = "WHERE (current_stage = 'task_admitted' OR task_id IS NOT NULL)"
            if data_version:
                where += " AND data_version = ?"
                params.append(data_version)
            task_rows = conn.execute(f"SELECT * FROM pipeline_items {where} ORDER BY updated_at DESC LIMIT 300", params).fetchall()
            for row in task_rows:
                integrity = chain_integrity_for_item(conn, row["item_id"])
                if not integrity.get("passed"):
                    illegal_task_items.append({
                        "itemId": row["item_id"],
                        "taskId": _row_get(row, "task_id"),
                        "productId": _row_get(row, "product_id"),
                        "currentStage": row["current_stage"],
                        "missing": integrity.get("missing"),
                        "seen": integrity.get("seen"),
                    })
    agent1_failed = sum(value for key, value in counts.items() if key.startswith("agent1_failed"))
    agent1_pending = sum(value for key, value in counts.items() if key.startswith("agent1_pending"))
    agent1_completed = sum(value for key, value in counts.items() if key.startswith("agent1_completed"))
    action_pack_ready = sum(value for key, value in counts.items() if key.startswith("action_pack_ready"))
    agent2_completed = sum(value for key, value in counts.items() if key.startswith("agent2_completed"))
    sop_mapped = sum(value for key, value in counts.items() if key.startswith("sop_mapped"))
    task_admitted = sum(value for key, value in counts.items() if key.startswith("task_admitted"))
    issues = []
    if agent1_completed and not action_pack_ready:
        issues.append("agent1_completed exists but action_pack_ready is zero; Agent1 -> Action Pack handoff is broken.")
    if action_pack_ready and not agent2_completed:
        issues.append("action_pack_ready exists but agent2_completed is zero; Action Pack -> Agent2 worker is broken.")
    if agent2_completed and not sop_mapped:
        issues.append("agent2_completed exists but sop_mapped is zero; Agent2 -> SOP Builder handoff is broken.")
    if task_admitted and not sop_mapped:
        issues.append("task_admitted exists without sop_mapped; old/illegal task-chain output must be cleared.")
    if task_admitted and not agent2_completed:
        issues.append("task_admitted exists without agent2_completed; formal tasks are not complete Agent2 products.")
    if agent1_failed:
        issues.append(f"agent1_failed count = {agent1_failed}; inspect provider/config/error payloads.")
    return {
        "version": TASK_READ_MODEL_V2082_VERSION,
        "dataVersion": data_version,
        "stageCounts": dict(counts),
        "summary": {
            "agent1Pending": agent1_pending,
            "agent1Completed": agent1_completed,
            "agent1Failed": agent1_failed,
            "actionPackReady": action_pack_ready,
            "agent2Completed": agent2_completed,
            "sopMapped": sop_mapped,
            "taskAdmitted": task_admitted,
            "illegalTaskAdmitted": len(illegal_task_items),
        },
        "illegalTaskItems": illegal_task_items[:80],
        "issues": issues,
        "rule": "V20.8.5: /api/view/tasks only exposes task_admitted items whose same pipeline item has the full V20 chain lineage.",
    }


def read_task_views_v2082(status: str | None = None, limit: int = 200, data_version: str | None = None) -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    data_version = data_version or _latest_data_version()
    items: List[Dict[str, Any]] = []
    blocked = 0
    with connect() as conn:
        if _table_exists(conn, "pipeline_items"):
            params: List[Any] = []
            where = "WHERE current_stage = 'task_admitted' AND task_id IS NOT NULL AND task_id != ''"
            if data_version:
                where += " AND data_version = ?"
                params.append(data_version)
            rows = conn.execute(f"SELECT * FROM pipeline_items {where} ORDER BY updated_at DESC LIMIT ?", (*params, int(limit) * 5)).fetchall()
            for row in rows:
                task = _task_from_pipeline_row(conn, row)
                if task:
                    items.append(task)
                else:
                    blocked += 1
    if status:
        items = [item for item in items if item.get("status") == status or item.get("workflowStatus") == status]
    items = [item for item in items if item.get("status") not in DONE_STATUS and item.get("taskId")]
    items = sorted(items, key=lambda item: str(item.get("updatedAt") or ""), reverse=True)[: int(limit)]
    return {
        "version": TASK_READ_MODEL_V2082_VERSION,
        "ready": bool(items),
        "count": len(items),
        "blockedIncompleteTaskCount": blocked,
        "currentDataVersion": data_version,
        "items": items,
        "pipelineDiagnostics": pipeline_diagnostics(data_version),
        "rule": "V20.8.5 exposes only complete V20 chain tasks; old frontend_task_view/task_pool_entries are not read by product task list.",
    }


def read_task_detail_v2082(task_id: str, data_version: str | None = None) -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    data_version = data_version or _latest_data_version()
    with connect() as conn:
        if _table_exists(conn, "pipeline_items"):
            row = conn.execute("SELECT * FROM pipeline_items WHERE task_id = ? AND current_stage = 'task_admitted' ORDER BY updated_at DESC LIMIT 1", (task_id,)).fetchone()
            if row:
                task = _task_from_pipeline_row(conn, row)
                if task:
                    detail = {
                        "viewVersion": TASK_READ_MODEL_V2082_VERSION,
                        "id": task_id,
                        "taskId": task_id,
                        "dataVersion": task.get("dataVersion"),
                        "relatedTask": task,
                        "taskCard": task.get("taskCard"),
                        "taskDetailReport": task.get("taskDetailReport"),
                        "sopSteps": task.get("sopSteps") or task.get("operatorExecutionSop") or [],
                        "operatorExecutionSop": task.get("operatorExecutionSop") or [],
                        "reviewMetrics": task.get("reviewMetrics"),
                        "agentJudgment": task.get("agentJudgment"),
                        "chainIntegrity": task.get("chainIntegrity"),
                        "updatedAt": task.get("updatedAt"),
                    }
                    return {"version": TASK_READ_MODEL_V2082_VERSION, "ready": True, "currentDataVersion": data_version, "item": detail, "cachedAt": _row_get(row, "updated_at"), "source": "pipeline_items_task_admitted_chain_passed"}
                integrity = chain_integrity_for_item(conn, row["item_id"])
                return {"version": TASK_READ_MODEL_V2082_VERSION, "ready": False, "currentDataVersion": data_version, "item": None, "taskId": task_id, "chainIntegrity": integrity, "rule": "Task exists but is blocked because V20 chain integrity is not passed."}
    return {"version": TASK_READ_MODEL_V2082_VERSION, "ready": False, "currentDataVersion": data_version, "item": None, "taskId": task_id, "pipelineDiagnostics": pipeline_diagnostics(data_version), "rule": "task detail not found in V20.8.5 complete-chain task source"}


def refresh_task_views_v2082(limit: int = 300, data_version: str | None = None) -> Dict[str, Any]:
    legacy = _legacy_refresh_task_views(limit=limit, data_version=data_version)
    aligned = read_task_views_v2082(limit=limit, data_version=data_version)
    return {"version": TASK_READ_MODEL_V2082_VERSION, "legacyRefresh": legacy, "alignedCount": aligned.get("count"), "blockedIncompleteTaskCount": aligned.get("blockedIncompleteTaskCount"), "dataVersion": aligned.get("currentDataVersion"), "status": "task_views_chain_gated", "rule": "V20.8.5 refreshes legacy cache only for maintenance but product read model exposes complete-chain tasks only."}
