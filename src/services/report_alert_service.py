"""V16.16 report alert service.

Report alert persistence is kept as a lightweight import evidence layer. It does
not import deleted V11 governance services and it does not create tasks. Task
generation belongs to the V16 station queue and Agent/task-pool mainline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads

REPORT_ALERT_SERVICE_VERSION = "16.16"
ACTIVE_ALERT_STATUSES = {"new", "observed", "logged"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def _rows_from_payload(rows: Any) -> List[Dict[str, Any]]:
    if not rows:
        return []
    if not isinstance(rows, list):
        raise ValueError("rows must be a list of objects")
    return [{str(key): value for key, value in row.items()} for row in rows if isinstance(row, dict)]


def _normalize_dataset_name(dataset_name: str | None) -> str:
    value = (dataset_name or "auto").strip().lower().replace("-", "_")
    return value or "auto"


def ensure_v3_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                import_id TEXT NOT NULL,
                dataset_name TEXT NOT NULL,
                data_version TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_events (
                alert_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                import_id TEXT NOT NULL,
                data_version TEXT NOT NULL,
                source_dataset TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                store_id TEXT,
                alert_type TEXT NOT NULL,
                risk_domain TEXT NOT NULL,
                priority TEXT NOT NULL,
                evidence TEXT,
                status TEXT NOT NULL,
                task_id TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_data_snapshots_version ON data_snapshots(data_version)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_events_status ON alert_events(status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_events_version ON alert_events(data_version, source_dataset)")
        conn.commit()


def _save_snapshot(snapshot: Dict[str, Any]) -> None:
    ensure_v3_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO data_snapshots
            (snapshot_id, import_id, dataset_name, data_version, row_count, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (snapshot["snapshotId"], snapshot["importId"], snapshot["datasetName"], snapshot["dataVersion"], snapshot["rowCount"], dumps(snapshot), snapshot["createdAt"]),
        )
        conn.commit()


def _save_alert_event(alert: Dict[str, Any]) -> None:
    ensure_v3_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO alert_events (
                alert_id, snapshot_id, import_id, data_version, source_dataset,
                entity_type, entity_id, store_id, alert_type, risk_domain, priority,
                evidence, status, task_id, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert["alertId"],
                alert["snapshotId"],
                alert["importId"],
                alert["dataVersion"],
                alert["sourceDataset"],
                alert["entityType"],
                alert["entityId"],
                alert.get("storeId"),
                alert["alertType"],
                alert["riskDomain"],
                alert["priority"],
                dumps({"items": alert.get("evidence", [])}),
                alert.get("status") or "logged",
                alert.get("taskId"),
                dumps(alert),
                alert["createdAt"],
                alert.get("updatedAt") or now_iso(),
            ),
        )
        conn.commit()


def _pick(row: Dict[str, Any], *fields: str, default: Any = None) -> Any:
    for field in fields:
        if row.get(field) not in {None, ""}:
            return row.get(field)
    return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    if value in {None, ""}:
        return default
    text = str(value).replace(",", "").replace("%", "").replace("¥", "").strip()
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _detect_observation_alerts(rows: List[Dict[str, Any]], snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Create low-pressure observation alerts only.

    V16.16 deliberately does not create front-end tasks from report alerts. These
    records are evidence/diagnostic breadcrumbs for import visibility.
    """
    alerts: List[Dict[str, Any]] = []
    for index, row in enumerate(rows[:50]):
        product_id = str(_pick(row, "product_id", "productId", "商品ID", "sku", "SKU", default="") or "").strip()
        store_id = str(_pick(row, "store_id", "storeId", "店铺ID", "店铺编号", default="") or "").strip() or None
        roi = _as_float(_pick(row, "roi", "ROI", "投产比"))
        refund_rate = _as_float(_pick(row, "refund_rate", "refundRate", "退款率"))
        inventory = _as_float(_pick(row, "inventory", "inventory_qty", "库存", "库存数量"))
        reasons: List[Dict[str, Any]] = []
        if roi is not None and roi <= 0:
            reasons.append({"label": "ROI", "value": str(roi)})
        if refund_rate is not None and refund_rate >= 0.08:
            reasons.append({"label": "退款率", "value": str(refund_rate)})
        if inventory is not None and inventory <= 0:
            reasons.append({"label": "库存", "value": str(inventory)})
        if not reasons:
            continue
        created_at = now_iso()
        alerts.append(
            {
                "alertId": make_id("ALERT"),
                "snapshotId": snapshot["snapshotId"],
                "importId": snapshot["importId"],
                "dataVersion": snapshot["dataVersion"],
                "sourceDataset": snapshot["datasetName"],
                "entityType": "商品" if product_id else "报表行",
                "entityId": product_id or f"row_{index + 1}",
                "storeId": store_id,
                "alertType": "导入观察信号",
                "riskDomain": "数据观察",
                "priority": "低",
                "evidence": reasons,
                "suggestion": "V16.16仅记录观察信号；任务生成由V16 station queue和Agent主链路处理。",
                "status": "observed",
                "taskId": None,
                "createdAt": created_at,
                "updatedAt": created_at,
                "v16Rule": "report_alert_service_does_not_create_tasks",
            }
        )
    return alerts


def import_report_dataset(dataset_name: str, rows: Any = None, auto_create_tasks: bool = False) -> Dict[str, Any]:
    dataset = _normalize_dataset_name(dataset_name)
    normalized_rows = _rows_from_payload(rows)
    created_at = now_iso()
    data_version = f"DV-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
    snapshot = {
        "snapshotId": make_id("SNAPSHOT"),
        "importId": make_id("IMPORT"),
        "datasetName": dataset,
        "dataVersion": data_version,
        "rowCount": len(normalized_rows),
        "createdAt": created_at,
        "version": REPORT_ALERT_SERVICE_VERSION,
        "taskCreation": "disabled_in_report_alert_layer",
    }
    _save_snapshot(snapshot)
    alerts = _detect_observation_alerts(normalized_rows, snapshot)
    for alert in alerts:
        _save_alert_event(alert)
    return {
        "version": REPORT_ALERT_SERVICE_VERSION,
        "datasetName": dataset,
        "dataVersion": data_version,
        "rowCount": len(normalized_rows),
        "alertCount": len(alerts),
        "taggedAlertCount": len(alerts),
        "createdTaskCount": 0,
        "autoCreateTasksRequested": bool(auto_create_tasks),
        "legacyGovernanceDependencyRemoved": True,
        "taskCreation": "disabled_in_report_alert_layer",
        "alerts": alerts,
        "summary": get_v3_dashboard_summary(),
        "rule": "V16.16：报表预警只留观察证据，不直接生成任务；任务生成走V16 station queue。",
    }


def _row_to_alert(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"]) if row["payload"] else {}
    if isinstance(payload, dict) and payload:
        return payload
    evidence = loads(row["evidence"]) if row["evidence"] else {}
    return {
        "alertId": row["alert_id"],
        "snapshotId": row["snapshot_id"],
        "importId": row["import_id"],
        "dataVersion": row["data_version"],
        "sourceDataset": row["source_dataset"],
        "entityType": row["entity_type"],
        "entityId": row["entity_id"],
        "storeId": row["store_id"],
        "alertType": row["alert_type"],
        "riskDomain": row["risk_domain"],
        "priority": row["priority"],
        "evidence": evidence.get("items", []) if isinstance(evidence, dict) else [],
        "status": row["status"],
        "taskId": row["task_id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def list_alert_events(active_only: bool = True, limit: int = 200) -> List[Dict[str, Any]]:
    ensure_v3_tables()
    with connect() as conn:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM alert_events WHERE status IN ('new','observed','logged') ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM alert_events ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_alert(row) for row in rows]


def list_alerts_for_entity(entity_id: str | None = None, active_only: bool = True, limit: int = 200) -> List[Dict[str, Any]]:
    alerts = list_alert_events(active_only=active_only, limit=limit)
    if entity_id:
        return [alert for alert in alerts if str(alert.get("entityId")) == str(entity_id)]
    return alerts


def get_v3_dashboard_summary() -> Dict[str, Any]:
    ensure_v3_tables()
    with connect() as conn:
        snapshot_count = conn.execute("SELECT COUNT(*) AS count FROM data_snapshots").fetchone()["count"]
        alert_count = conn.execute("SELECT COUNT(*) AS count FROM alert_events").fetchone()["count"]
        latest = conn.execute("SELECT * FROM data_snapshots ORDER BY created_at DESC LIMIT 1").fetchone()
    return {
        "version": REPORT_ALERT_SERVICE_VERSION,
        "snapshotCount": snapshot_count,
        "alertCount": alert_count,
        "createdTaskCount": 0,
        "latestDataVersion": latest["data_version"] if latest else None,
        "latestDatasetName": latest["dataset_name"] if latest else None,
        "taskCreation": "disabled_in_report_alert_layer",
        "rule": "V16.16 report alerts are observation evidence only; task generation is owned by station queue and Agent mainline.",
    }
