"""V13.1 Snapshot Task Handoff service.

This is the light bridge from the external data line to the internal task judgment
line. It does not directly create tasks. After an operating snapshot is ready, it
records a handoff that says: this snapshot is ready for task signal, RAG context
and Agent judgment.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, ensure_columns

SNAPSHOT_TASK_HANDOFF_VERSION = "13.1.0"
FROM_STATION = "operating_snapshot_station"
TO_STATION = "task_signal_station"
NEXT_AFTER_SIGNAL = "rag_context_station"


def now_iso() -> str:
    return datetime.now().isoformat()


def make_handoff_id() -> str:
    return f"HND-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


def ensure_handoff_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS station_handoffs (
                handoff_id TEXT PRIMARY KEY,
                data_version TEXT,
                from_station TEXT NOT NULL,
                to_station TEXT NOT NULL,
                status TEXT NOT NULL,
                decision_status TEXT,
                input_ref TEXT,
                output_ref TEXT,
                reason TEXT,
                signal_count INTEGER DEFAULT 0,
                task_snapshot_count INTEGER DEFAULT 0,
                created_task_count INTEGER DEFAULT 0,
                payload TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(
            conn,
            "station_handoffs",
            {
                "decision_status": "TEXT",
                "input_ref": "TEXT",
                "output_ref": "TEXT",
                "signal_count": "INTEGER DEFAULT 0",
                "task_snapshot_count": "INTEGER DEFAULT 0",
                "created_task_count": "INTEGER DEFAULT 0",
                "payload": "TEXT",
                "created_by": "TEXT",
            },
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_station_handoffs_version ON station_handoffs(data_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_station_handoffs_status ON station_handoffs(status, decision_status, created_at)")
        conn.commit()


def _row_to_handoff(row: Any) -> Dict[str, Any]:
    payload = {}
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    return {
        "version": SNAPSHOT_TASK_HANDOFF_VERSION,
        "handoffId": row["handoff_id"],
        "dataVersion": row["data_version"],
        "fromStation": row["from_station"],
        "toStation": row["to_station"],
        "status": row["status"],
        "decisionStatus": row["decision_status"],
        "inputRef": row["input_ref"],
        "outputRef": row["output_ref"],
        "reason": row["reason"],
        "signalCount": int(row["signal_count"] or 0),
        "taskSnapshotCount": int(row["task_snapshot_count"] or 0),
        "createdTaskCount": int(row["created_task_count"] or 0),
        "payload": payload,
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def create_snapshot_task_handoff(
    *,
    data_version: str | None,
    snapshot_ref: str | None = None,
    source: str = "operating_snapshot_station",
    user_id: str | None = None,
    import_result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create an idempotent handoff from snapshot to task judgment.

    V13.1 deliberately stops at `pending_agent_judgment`. V13.2 should consume
    this handoff, call RAG context and Agent judgment, and then decide whether a
    task snapshot should enter the task pool.
    """

    ensure_handoff_tables()
    data_version = str(data_version or "latest")
    snapshot_ref = snapshot_ref or f"operating_unit_snapshot:{data_version}"
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT * FROM station_handoffs
            WHERE data_version = ? AND from_station = ? AND to_station = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (data_version, FROM_STATION, TO_STATION),
        ).fetchone()
        if existing and existing["status"] in {"pending_agent_judgment", "completed"}:
            item = _row_to_handoff(existing)
            item["idempotent"] = True
            item["rule"] = "同一数据版本的经营快照交接已存在，不重复制造任务。"
            return item

        handoff_id = make_handoff_id()
        created_at = now_iso()
        payload = {
            "version": SNAPSHOT_TASK_HANDOFF_VERSION,
            "handoffId": handoff_id,
            "dataVersion": data_version,
            "source": source,
            "fromStation": FROM_STATION,
            "toStation": TO_STATION,
            "nextAfterSignal": NEXT_AFTER_SIGNAL,
            "snapshotRef": snapshot_ref,
            "importPipeline": (import_result or {}).get("pipelineSync"),
            "operatingUnitSnapshotSync": (import_result or {}).get("operatingUnitSnapshotSync"),
            "agentRequired": True,
            "ragContextRequired": True,
            "directTaskCreationAllowed": False,
            "rule": "系统只完成经营快照到任务判断线的交接；任务是否生成必须经过RAG参照和Agent趋势判断。",
        }
        conn.execute(
            """
            INSERT INTO station_handoffs (
                handoff_id, data_version, from_station, to_station, status,
                decision_status, input_ref, output_ref, reason, signal_count,
                task_snapshot_count, created_task_count, payload, created_by,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                handoff_id,
                data_version,
                FROM_STATION,
                TO_STATION,
                "pending_agent_judgment",
                "awaiting_rag_agent",
                snapshot_ref,
                f"task_signal_candidates:{data_version}",
                "经营快照已完成，进入任务信号、RAG上下文和Agent判断线；V13.1不直接创建任务。",
                0,
                0,
                0,
                json.dumps(payload, ensure_ascii=False),
                user_id,
                created_at,
                created_at,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM station_handoffs WHERE handoff_id = ?", (handoff_id,)).fetchone()
    item = _row_to_handoff(row)
    item["idempotent"] = False
    item["rule"] = payload["rule"]
    return item


def create_handoffs_from_import_result(result: Dict[str, Any], *, source: str, user_id: str | None = None) -> Dict[str, Any]:
    versions = []
    if result.get("dataVersion"):
        versions.append(str(result["dataVersion"]))
    pipeline_versions = ((result.get("pipelineSync") or {}).get("dataVersions") or []) if isinstance(result.get("pipelineSync"), dict) else []
    versions.extend(str(item) for item in pipeline_versions if item)
    for item in result.get("results") or []:
        if isinstance(item, dict) and item.get("dataVersion"):
            versions.append(str(item["dataVersion"]))
    versions = list(dict.fromkeys([item for item in versions if item])) or ["latest"]
    snapshot_ref = None
    snapshot = result.get("operatingUnitSnapshotSync") if isinstance(result.get("operatingUnitSnapshotSync"), dict) else {}
    if snapshot:
        snapshot_ref = snapshot.get("snapshotKey") or snapshot.get("outputRef")
    handoffs = [create_snapshot_task_handoff(data_version=version, snapshot_ref=snapshot_ref, source=source, user_id=user_id, import_result=result) for version in versions]
    return {
        "version": SNAPSHOT_TASK_HANDOFF_VERSION,
        "mode": "snapshot_to_task_judgment_handoff",
        "handoffCount": len(handoffs),
        "handoffs": handoffs,
        "createdTaskCount": 0,
        "decisionStatus": "awaiting_rag_agent",
        "rule": "V13.1只建立经营快照到任务判断线的交接，不由系统规则直接生成任务。",
    }


def list_station_handoffs(data_version: str | None = None, limit: int = 50) -> Dict[str, Any]:
    ensure_handoff_tables()
    with connect() as conn:
        if data_version:
            rows = conn.execute("SELECT * FROM station_handoffs WHERE data_version = ? ORDER BY created_at DESC LIMIT ?", (data_version, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM station_handoffs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = [_row_to_handoff(row) for row in rows]
    return {"version": SNAPSHOT_TASK_HANDOFF_VERSION, "handoffs": items, "handoffCount": len(items), "dataVersion": data_version}


def latest_station_handoff(data_version: str | None = None) -> Dict[str, Any]:
    result = list_station_handoffs(data_version=data_version, limit=1)
    if not result["handoffs"]:
        return {"version": SNAPSHOT_TASK_HANDOFF_VERSION, "status": "missing", "dataVersion": data_version, "message": "No snapshot task handoff yet."}
    return result["handoffs"][0]


def handoff_summary(limit: int = 40) -> Dict[str, Any]:
    ensure_handoff_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM station_handoffs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = [_row_to_handoff(row) for row in rows]
    by_status: Dict[str, int] = {}
    by_decision: Dict[str, int] = {}
    for item in items:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
        by_decision[item["decisionStatus"]] = by_decision.get(item["decisionStatus"], 0) + 1
    return {
        "version": SNAPSHOT_TASK_HANDOFF_VERSION,
        "total": len(items),
        "byStatus": by_status,
        "byDecisionStatus": by_decision,
        "latest": items[0] if items else None,
        "items": items,
        "rule": "经营快照只交接到任务判断线；RAG和Agent判断后才允许生成任务快照。",
    }
