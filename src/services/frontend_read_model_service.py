"""V15.1 frontend read model service.

Frontend pages read cached view models only. V15.1 adds a hard current-run
isolation gate: task views are rebuilt and read by the latest dataVersion/run,
so old demo/seed/global task-pool entries cannot pollute the current execution
queue.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads

FRONTEND_READ_MODEL_VERSION = "15.1"
VIEW_TABLES = [
    "frontend_dashboard_view",
    "frontend_product_view",
    "frontend_task_view",
    "frontend_task_detail_view",
    "frontend_store_view",
    "frontend_system_status_view",
]
DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}
BACKGROUND_TASK_TYPES = {"observe_only", "backend_observation", "candidate_only", "report_seed_only", "merged_duplicate"}
DEMO_SOURCE_MARKERS = {"demo", "mock", "seed", "fallback_seed", "sample", "preset"}
GENERIC_TITLES = {"经营任务", "后台观察", "商品经营观察", "任务"}


def now_iso() -> str:
    return datetime.now().isoformat()


def ensure_frontend_read_model_tables() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS frontend_dashboard_view (
                view_key TEXT PRIMARY KEY,
                payload TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS frontend_product_view (
                view_key TEXT PRIMARY KEY,
                product_id TEXT,
                store_id TEXT,
                data_version TEXT,
                payload TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS frontend_task_view (
                task_id TEXT PRIMARY KEY,
                status TEXT,
                workflow_status TEXT,
                task_layer TEXT,
                priority TEXT,
                deadline TEXT,
                store_id TEXT,
                product_id TEXT,
                data_version TEXT,
                payload TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS frontend_task_detail_view (
                task_id TEXT PRIMARY KEY,
                data_version TEXT,
                payload TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS frontend_store_view (
                store_id TEXT PRIMARY KEY,
                payload TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS frontend_system_status_view (
                view_key TEXT PRIMARY KEY,
                payload TEXT,
                updated_at TEXT NOT NULL
            )
        """)
        ensure_columns(conn, "frontend_product_view", {"product_id": "TEXT", "store_id": "TEXT", "data_version": "TEXT", "payload": "TEXT", "updated_at": "TEXT"})
        ensure_columns(conn, "frontend_task_view", {"status": "TEXT", "workflow_status": "TEXT", "task_layer": "TEXT", "priority": "TEXT", "deadline": "TEXT", "store_id": "TEXT", "product_id": "TEXT", "data_version": "TEXT", "payload": "TEXT", "updated_at": "TEXT"})
        ensure_columns(conn, "frontend_task_detail_view", {"data_version": "TEXT", "payload": "TEXT", "updated_at": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_frontend_product_view_product ON frontend_product_view(product_id, store_id, data_version, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_frontend_task_view_status ON frontend_task_view(data_version, status, workflow_status, priority, updated_at)")
        conn.commit()


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone())


def _safe_load(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return loads(value)
    except Exception:
        return {}


def _latest_run_payload(conn: Any) -> Dict[str, Any] | None:
    if not _table_exists(conn, "task_generation_runs_v14"):
        return None
    row = conn.execute("SELECT payload FROM task_generation_runs_v14 ORDER BY created_at DESC LIMIT 1").fetchone()
    return _safe_load(row["payload"]) if row else None


def _latest_current_data_version(conn: Any) -> str | None:
    latest_run = _latest_run_payload(conn)
    if latest_run and latest_run.get("dataVersion"):
        return str(latest_run.get("dataVersion"))
    if _table_exists(conn, "product_signal_snapshots_v14"):
        row = conn.execute("SELECT data_version FROM product_signal_snapshots_v14 ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row and row["data_version"]:
            return str(row["data_version"])
    if _table_exists(conn, "task_pool_entries"):
        row = conn.execute("SELECT data_version FROM task_pool_entries WHERE data_version IS NOT NULL AND data_version != '' ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row and row["data_version"]:
            return str(row["data_version"])
    return None


def _product_bundle_summary(bundle: Dict[str, Any]) -> Dict[str, Any]:
    profile = bundle.get("profileLayer") if isinstance(bundle.get("profileLayer"), dict) else {}
    metric = bundle.get("metricLayer") if isinstance(bundle.get("metricLayer"), dict) else {}
    cross = bundle.get("crossValidation") if isinstance(bundle.get("crossValidation"), dict) else {}
    return {
        "viewVersion": FRONTEND_READ_MODEL_VERSION,
        "bundleId": bundle.get("bundleId"),
        "signalId": bundle.get("signalId"),
        "dataVersion": bundle.get("dataVersion"),
        "productId": bundle.get("productId") or profile.get("productId"),
        "storeId": bundle.get("storeId") or profile.get("storeId"),
        "storeName": profile.get("storeName"),
        "platform": bundle.get("platform") or profile.get("platform"),
        "title": profile.get("title") or profile.get("shortName") or bundle.get("productId"),
        "verticalCategory": bundle.get("verticalCategory") or profile.get("verticalCategory") or "未归类",
        "productRole": profile.get("productRole"),
        "lifecycleStage": profile.get("lifecycleStage"),
        "signalStrength": bundle.get("signalStrength"),
        "primarySignalType": bundle.get("primarySignalType"),
        "metricCode": bundle.get("metricCode"),
        "metrics": {key: metric.get(key) for key in ["paymentAmount", "roas", "roi", "adSpend", "clickRate", "conversionRate", "refundRate", "grossMargin", "inventory"]},
        "crossValidation": {"sourceVersionCount": cross.get("sourceVersionCount"), "changedMetricCount": cross.get("changedMetricCount"), "abnormalMetricCount": cross.get("abnormalMetricCount")},
        "bundleFingerprint": bundle.get("bundleFingerprint"),
        "dataFingerprint": bundle.get("dataFingerprint"),
        "updatedAt": now_iso(),
        "readRule": "Frontend reads cached product view only; page switching does not trigger materialize/generate/Agent.",
    }


def refresh_product_views(data_version: str | None = None) -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        if not _table_exists(conn, "product_signal_snapshots_v14"):
            return {"version": FRONTEND_READ_MODEL_VERSION, "updated": 0, "status": "no_source_table"}
        if data_version:
            row = conn.execute("SELECT * FROM product_signal_snapshots_v14 WHERE data_version = ? ORDER BY updated_at DESC LIMIT 1", (data_version,)).fetchone()
        else:
            row = conn.execute("SELECT * FROM product_signal_snapshots_v14 ORDER BY updated_at DESC LIMIT 1").fetchone()
        if not row:
            return {"version": FRONTEND_READ_MODEL_VERSION, "updated": 0, "status": "no_snapshot"}
        payload = loads(row["payload"])
        bundles = payload.get("productSignalPackages") or payload.get("signals") or []
        now = now_iso()
        data_version = row["data_version"] or data_version
        conn.execute("DELETE FROM frontend_product_view WHERE data_version = ?", (data_version,))
        for bundle in bundles:
            summary = _product_bundle_summary(bundle)
            summary["dataVersion"] = summary.get("dataVersion") or data_version
            key = f"{summary.get('dataVersion') or 'LATEST'}::{summary.get('storeId') or 'GLOBAL'}::{summary.get('productId') or summary.get('bundleId')}"
            conn.execute(
                """
                INSERT OR REPLACE INTO frontend_product_view (view_key, product_id, store_id, data_version, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (key, summary.get("productId"), summary.get("storeId"), summary.get("dataVersion"), dumps(summary), now),
            )
        conn.commit()
    return {"version": FRONTEND_READ_MODEL_VERSION, "updated": len(bundles), "dataVersion": data_version, "status": "product_views_refreshed", "rule": "Refreshed from persisted current fullProductBundle snapshot only."}


def _first_store_id(task: Dict[str, Any]) -> str | None:
    stores = task.get("storeIds") or task.get("visibleStoreIds") or []
    if isinstance(stores, list) and stores:
        return stores[0]
    return task.get("storeId")


def _task_data_version(task: Dict[str, Any], pool_payload: Dict[str, Any] | None = None, pool_row: Any | None = None) -> str | None:
    pool_payload = pool_payload or {}
    snapshot = pool_payload.get("snapshot") if isinstance(pool_payload.get("snapshot"), dict) else {}
    for value in [task.get("dataVersion"), snapshot.get("dataVersion"), (task.get("taskDetailReport") or {}).get("dataVersion") if isinstance(task.get("taskDetailReport"), dict) else None, pool_payload.get("dataVersion")]:
        if value:
            return str(value)
    if pool_row is not None:
        try:
            return str(pool_row["data_version"]) if pool_row["data_version"] else None
        except Exception:
            return None
    return None


def _task_title(task: Dict[str, Any]) -> str:
    card = task.get("taskCard") if isinstance(task.get("taskCard"), dict) else {}
    intent = task.get("taskIntent") if isinstance(task.get("taskIntent"), dict) else {}
    plan = (task.get("taskDetailReport") or {}).get("taskPlan") if isinstance(task.get("taskDetailReport"), dict) else {}
    title = task.get("title") or card.get("title") or plan.get("title") or intent.get("title")
    if str(title or "").strip() in GENERIC_TITLES:
        title = plan.get("title") or intent.get("title") or task.get("productTitle") or title
    return title or "经营任务"


def _looks_like_demo_seed(task: Dict[str, Any], pool_payload: Dict[str, Any] | None = None) -> bool:
    pool_payload = pool_payload or {}
    text = " ".join(str(value or "") for value in [task.get("source"), task.get("sourceModule"), task.get("queueType"), task.get("taskGenerationMode"), task.get("sourceEvent"), pool_payload.get("source")]).lower()
    return any(marker in text for marker in DEMO_SOURCE_MARKERS) or task.get("demoOnly") is True or task.get("backendOnly") is True


def _visible_task(task: Dict[str, Any], *, pool_payload: Dict[str, Any] | None = None, expected_data_version: str | None = None) -> bool:
    if not isinstance(task, dict):
        return False
    if task.get("status") in DONE_STATUS or task.get("workflowStatus") in DONE_STATUS:
        return False
    if expected_data_version:
        task_dv = _task_data_version(task, pool_payload)
        if task_dv and str(task_dv) != str(expected_data_version):
            return False
    if _looks_like_demo_seed(task, pool_payload):
        return False
    card = task.get("taskCard") if isinstance(task.get("taskCard"), dict) else {}
    detail = task.get("taskDetailReport") if isinstance(task.get("taskDetailReport"), dict) else {}
    judgment = task.get("agentJudgment") if isinstance(task.get("agentJudgment"), dict) else {}
    intent = task.get("taskIntent") if isinstance(task.get("taskIntent"), dict) else {}
    task_type = str(task.get("taskType") or intent.get("taskType") or "")
    action_type = str(task.get("actionType") or (task.get("actionAuthorization") or {}).get("actionType") or "")
    deadline = str(task.get("deadline") or card.get("deadline") or "")
    decision = str(judgment.get("decision") or detail.get("decision") or task.get("decision") or "")
    if deadline == "后台观察" or task_type in BACKGROUND_TASK_TYPES or action_type in BACKGROUND_TASK_TYPES or decision == "observe_only":
        return False
    return bool(task.get("id") or task.get("taskId"))


def _task_summary(task: Dict[str, Any], *, data_version: str | None = None) -> Dict[str, Any]:
    card = task.get("taskCard") if isinstance(task.get("taskCard"), dict) else {}
    auth = task.get("actionAuthorization") if isinstance(task.get("actionAuthorization"), dict) else {}
    task_id = task.get("id") or task.get("taskId")
    return {
        "viewVersion": FRONTEND_READ_MODEL_VERSION,
        "id": task_id,
        "taskId": task_id,
        "dataVersion": data_version or task.get("dataVersion"),
        "title": _task_title(task),
        "subtitle": card.get("subtitle") or task.get("subtitle"),
        "status": task.get("status"),
        "workflowStatus": task.get("workflowStatus"),
        "displayStatus": task.get("displayStatus"),
        "taskLayer": task.get("taskLayer"),
        "priority": task.get("priority"),
        "deadline": task.get("deadline") or card.get("deadline"),
        "productId": task.get("productId"),
        "entityId": task.get("entityId"),
        "storeIds": task.get("storeIds") or task.get("visibleStoreIds") or [],
        "storeId": _first_store_id(task),
        "store": task.get("store") or task.get("storeName"),
        "storeName": task.get("storeName") or task.get("store"),
        "platform": task.get("platform"),
        "riskDomain": task.get("riskDomain"),
        "actionType": task.get("actionType"),
        "taskType": task.get("taskType"),
        "reason": task.get("reason") or ((task.get("taskDetailReport") or {}).get("warningSummary") if isinstance(task.get("taskDetailReport"), dict) else None),
        "assigneeId": task.get("assigneeId"),
        "assigneeName": task.get("assigneeName"),
        "reviewerId": task.get("reviewerId"),
        "reviewerName": task.get("reviewerName"),
        "managerApproval": auth.get("decision") in {"manager_approval_required", "owner_approval_required"} or task.get("taskLayer") in {"manager_dispatch", "manager_approval"},
        "actionAuthorization": auth,
        "primaryTaskAction": task.get("primaryTaskAction"),
        "visibleTaskActions": task.get("visibleTaskActions") or task.get("availableActions") or [],
        "availableActions": task.get("availableActions") or [],
        "updatedAt": task.get("updatedAt") or now_iso(),
    }


def refresh_task_views(limit: int = 300, data_version: str | None = None) -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        if not _table_exists(conn, "task_pool_entries"):
            return {"version": FRONTEND_READ_MODEL_VERSION, "updated": 0, "status": "no_source_table"}
        current_data_version = data_version or _latest_current_data_version(conn)
        if current_data_version:
            rows = conn.execute("SELECT * FROM task_pool_entries WHERE data_version = ? ORDER BY updated_at DESC LIMIT ?", (current_data_version, limit)).fetchall()
        else:
            rows = []
        updated = 0
        skipped = 0
        now = now_iso()
        conn.execute("DELETE FROM frontend_task_view")
        conn.execute("DELETE FROM frontend_task_detail_view")
        for row in rows:
            pool_payload = loads(row["payload"])
            task = pool_payload.get("task") if isinstance(pool_payload.get("task"), dict) else None
            task_data_version = _task_data_version(task or {}, pool_payload, row) or current_data_version
            if not task or not _visible_task(task, pool_payload=pool_payload, expected_data_version=current_data_version):
                skipped += 1
                continue
            summary = _task_summary(task, data_version=task_data_version)
            task_id = summary.get("taskId")
            if not task_id:
                skipped += 1
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO frontend_task_view (task_id, status, workflow_status, task_layer, priority, deadline, store_id, product_id, data_version, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, summary.get("status"), summary.get("workflowStatus"), summary.get("taskLayer"), summary.get("priority"), summary.get("deadline"), summary.get("storeId"), summary.get("productId"), task_data_version, dumps(summary), now),
            )
            detail = {"viewVersion": FRONTEND_READ_MODEL_VERSION, "id": task_id, "taskId": task_id, "dataVersion": task_data_version, "relatedTask": task, "taskCard": task.get("taskCard"), "taskDetailReport": task.get("taskDetailReport"), "evidencePack": task.get("evidencePack"), "sopSteps": task.get("sopSteps"), "reviewMetrics": task.get("reviewMetrics"), "completionGate": task.get("completionGate"), "failureThreshold": task.get("failureThreshold"), "agentJudgment": task.get("agentJudgment"), "ownership": task.get("ownership"), "updatedAt": summary.get("updatedAt")}
            conn.execute("INSERT OR REPLACE INTO frontend_task_detail_view (task_id, data_version, payload, updated_at) VALUES (?, ?, ?, ?)", (task_id, task_data_version, dumps(detail), now))
            updated += 1
        conn.commit()
    refresh_dashboard_view()
    return {"version": FRONTEND_READ_MODEL_VERSION, "updated": updated, "skippedBackgroundOrInvalid": skipped, "dataVersion": current_data_version, "status": "task_views_refreshed", "rule": "V15.1 refreshed current-run task view from task_pool_entries WHERE data_version = latestRun.dataVersion only."}


def refresh_dashboard_view() -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        current_data_version = _latest_current_data_version(conn)
        if current_data_version:
            tasks = [loads(row["payload"]) for row in conn.execute("SELECT payload FROM frontend_task_view WHERE data_version = ? ORDER BY updated_at DESC LIMIT 500", (current_data_version,)).fetchall()]
            products = [loads(row["payload"]) for row in conn.execute("SELECT payload FROM frontend_product_view WHERE data_version = ? ORDER BY updated_at DESC LIMIT 500", (current_data_version,)).fetchall()]
        else:
            tasks = []
            products = []
        queues = []
        if _table_exists(conn, "station_queue"):
            queues = [dict(row) for row in conn.execute("SELECT station_id, status, COUNT(*) AS count FROM station_queue GROUP BY station_id, status").fetchall()]
        active = [item for item in tasks if item.get("status") not in DONE_STATUS]
        manager = [item for item in active if item.get("managerApproval") or item.get("status") in {"待复核", "待拆分"}]
        processing = [item for item in active if item.get("status") == "处理中"]
        recap = [item for item in active if item.get("status") in {"已完成", "待复盘"}]
        high_products = [item for item in products if item.get("signalStrength") == "high"]
        dashboard = {"viewVersion": FRONTEND_READ_MODEL_VERSION, "currentDataVersion": current_data_version, "topTasks": active[:5], "counts": {"activeTasks": len(active), "managerReview": len(manager), "processing": len(processing), "waitingRecap": len(recap), "products": len(products), "highRiskProducts": len(high_products)}, "workerQueue": queues, "updatedAt": now_iso(), "readRule": "Dashboard reads current-run cached view rows only; no compute chain is triggered by page switching."}
        conn.execute("INSERT OR REPLACE INTO frontend_dashboard_view (view_key, payload, updated_at) VALUES ('main', ?, ?)", (dumps(dashboard), dashboard["updatedAt"]))
        conn.execute("INSERT OR REPLACE INTO frontend_system_status_view (view_key, payload, updated_at) VALUES ('main', ?, ?)", (dumps({"viewVersion": FRONTEND_READ_MODEL_VERSION, "currentDataVersion": current_data_version, "queue": queues, "updatedAt": dashboard["updatedAt"], "readModelReady": True}), dashboard["updatedAt"]))
        conn.commit()
    return {"version": FRONTEND_READ_MODEL_VERSION, "status": "dashboard_refreshed", "currentDataVersion": current_data_version, "counts": dashboard["counts"]}


def refresh_after_station(station_id: str | None = None, data_version: str | None = None, output: Dict[str, Any] | None = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"version": FRONTEND_READ_MODEL_VERSION, "stationId": station_id, "dataVersion": data_version, "updates": []}
    if station_id in {"product_signal_snapshot_station", "task_signal_station"}:
        result["updates"].append(refresh_product_views(data_version=data_version))
    if station_id in {"task_pool_station", "task_snapshot_station", "agent_judgment_station"}:
        result["updates"].append(refresh_task_views(data_version=data_version))
    if station_id in {"rag_context_station", "station_queue"}:
        result["updates"].append(refresh_dashboard_view())
    if not result["updates"]:
        result["updates"].append(refresh_dashboard_view())
    return result


def read_dashboard_view() -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        row = conn.execute("SELECT payload, updated_at FROM frontend_dashboard_view WHERE view_key = 'main'").fetchone()
    if not row:
        return {"version": FRONTEND_READ_MODEL_VERSION, "ready": False, "topTasks": [], "counts": {}, "rule": "No cached dashboard view yet. Worker or manual refresh must build read model."}
    return {**loads(row["payload"]), "ready": True, "cachedAt": row["updated_at"]}


def read_product_views(store_id: str | None = None, limit: int = 200, data_version: str | None = None) -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        current_data_version = data_version or _latest_current_data_version(conn)
        where_parts = []
        params: List[Any] = []
        if store_id:
            where_parts.append("store_id = ?")
            params.append(store_id)
        if current_data_version:
            where_parts.append("data_version = ?")
            params.append(current_data_version)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        rows = conn.execute(f"SELECT payload, updated_at FROM frontend_product_view {where} ORDER BY updated_at DESC LIMIT ?", (*params, limit)).fetchall()
    items = [loads(row["payload"]) for row in rows]
    return {"version": FRONTEND_READ_MODEL_VERSION, "ready": bool(rows), "count": len(items), "currentDataVersion": current_data_version, "items": items, "rule": "Read-only cached current-run product view."}


def read_product_detail(product_id: str, store_id: str | None = None) -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        current_data_version = _latest_current_data_version(conn)
        if store_id and current_data_version:
            row = conn.execute("SELECT payload, updated_at FROM frontend_product_view WHERE product_id = ? AND store_id = ? AND data_version = ? ORDER BY updated_at DESC LIMIT 1", (product_id, store_id, current_data_version)).fetchone()
        elif current_data_version:
            row = conn.execute("SELECT payload, updated_at FROM frontend_product_view WHERE product_id = ? AND data_version = ? ORDER BY updated_at DESC LIMIT 1", (product_id, current_data_version)).fetchone()
        else:
            row = None
    return {"version": FRONTEND_READ_MODEL_VERSION, "ready": bool(row), "currentDataVersion": current_data_version, "item": loads(row["payload"]) if row else None, "cachedAt": row["updated_at"] if row else None}


def read_task_views(status: str | None = None, limit: int = 200, data_version: str | None = None) -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        current_data_version = data_version or _latest_current_data_version(conn)
        where_parts = []
        params: List[Any] = []
        if status:
            where_parts.append("status = ?")
            params.append(status)
        if current_data_version:
            where_parts.append("data_version = ?")
            params.append(current_data_version)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else "WHERE 1 = 0"
        rows = conn.execute(f"SELECT payload, updated_at FROM frontend_task_view {where} ORDER BY updated_at DESC LIMIT ?", (*params, limit)).fetchall()
    items = [loads(row["payload"]) for row in rows]
    items = [item for item in items if item.get("id") or item.get("taskId")]
    return {"version": FRONTEND_READ_MODEL_VERSION, "ready": bool(items), "count": len(items), "currentDataVersion": current_data_version, "items": items, "rule": "Read-only cached task list view filtered to latest run dataVersion."}


def read_task_detail(task_id: str) -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        current_data_version = _latest_current_data_version(conn)
        if current_data_version:
            row = conn.execute("SELECT payload, updated_at FROM frontend_task_detail_view WHERE task_id = ? AND data_version = ?", (task_id, current_data_version)).fetchone()
        else:
            row = None
    return {"version": FRONTEND_READ_MODEL_VERSION, "ready": bool(row), "currentDataVersion": current_data_version, "item": loads(row["payload"]) if row else None, "cachedAt": row["updated_at"] if row else None}


def read_system_status_view() -> Dict[str, Any]:
    ensure_frontend_read_model_tables()
    with connect() as conn:
        row = conn.execute("SELECT payload, updated_at FROM frontend_system_status_view WHERE view_key = 'main'").fetchone()
    return {"version": FRONTEND_READ_MODEL_VERSION, "ready": bool(row), "item": loads(row["payload"]) if row else None, "cachedAt": row["updated_at"] if row else None}


def refresh_all_read_models(data_version: str | None = None) -> Dict[str, Any]:
    return {"version": FRONTEND_READ_MODEL_VERSION, "product": refresh_product_views(data_version=data_version), "task": refresh_task_views(data_version=data_version), "dashboard": refresh_dashboard_view(), "rule": "Manual read-model refresh is explicit compute; normal GET /api/view/* remains read-only and current-run isolated."}
