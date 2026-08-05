"""V19.3 Task Pool Station service.

Task Pool admits only operator_growth lifecycle tasks with backend multi-route
judgment trace, frontend-safe operatorJudgmentView and dynamic creative SOP.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, ensure_columns
from src.services.account_service import user_display
from src.services.lifecycle_task_v183_service import create_lifecycle_task_from_snapshot
from src.services.task_snapshot_station_service import get_task_snapshot, list_task_snapshots

TASK_POOL_STATION_VERSION = "19.3"
READY_SNAPSHOT_STATUSES = {"snapshot_ready", "manager_review_required"}
READY_DECISIONS = {"create_task_snapshot", "manager_review_required"}


def now_iso() -> str:
    return datetime.now().isoformat()


def make_pool_entry_id() -> str:
    return f"TPE-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


def ensure_task_pool_tables() -> None:
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_pool_entries (
                pool_entry_id TEXT PRIMARY KEY,
                task_snapshot_id TEXT NOT NULL,
                task_id TEXT,
                data_version TEXT,
                status TEXT NOT NULL,
                decision TEXT,
                task_layer TEXT,
                assignee_id TEXT,
                reviewer_id TEXT,
                dedupe_key TEXT,
                reason TEXT,
                payload TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        ensure_columns(conn, "task_pool_entries", {"task_id": "TEXT", "data_version": "TEXT", "decision": "TEXT", "task_layer": "TEXT", "assignee_id": "TEXT", "reviewer_id": "TEXT", "dedupe_key": "TEXT", "reason": "TEXT", "payload": "TEXT", "created_by": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_pool_entries_snapshot ON task_pool_entries(task_snapshot_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_pool_entries_task ON task_pool_entries(task_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_pool_entries_status ON task_pool_entries(status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_pool_entries_version ON task_pool_entries(data_version, updated_at)")
        conn.commit()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value not in {None, ""}:
        return [value]
    return []


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _row_to_pool_entry(row: Any) -> Dict[str, Any]:
    return {"version": TASK_POOL_STATION_VERSION, "poolEntryId": row["pool_entry_id"], "taskSnapshotId": row["task_snapshot_id"], "taskId": row["task_id"], "dataVersion": row["data_version"], "status": row["status"], "decision": row["decision"], "taskLayer": row["task_layer"], "assigneeId": row["assignee_id"], "assigneeName": user_display(row["assignee_id"], "未派发"), "reviewerId": row["reviewer_id"], "reviewerName": user_display(row["reviewer_id"], "未设置复核人"), "dedupeKey": row["dedupe_key"], "reason": row["reason"], "payload": _loads(row["payload"], {}), "createdBy": row["created_by"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}


def _table(conn: Any, name: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _task_generation_decision(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
    system_facts = payload.get("systemFacts") if isinstance(payload.get("systemFacts"), dict) else {}
    decision = system_facts.get("taskGenerationDecision") if isinstance(system_facts.get("taskGenerationDecision"), dict) else {}
    return decision or (payload.get("rawTaskGenerationDecision") if isinstance(payload.get("rawTaskGenerationDecision"), dict) else {}) or {}


def _agent_evidence(snapshot: Dict[str, Any], decision: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(snapshot.get("taskMappingAgentEvidence"), dict) and snapshot.get("taskMappingAgentEvidence"):
        return snapshot.get("taskMappingAgentEvidence") or {}
    payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
    if isinstance(payload.get("taskMappingAgentEvidence"), dict):
        return payload.get("taskMappingAgentEvidence") or {}
    return decision.get("taskMappingAgentEvidence") if isinstance(decision.get("taskMappingAgentEvidence"), dict) else {}


def _creative_contract_ok(plan: Dict[str, Any]) -> bool:
    return bool(len(str(plan.get("businessHypothesis") or "")) >= 18 and plan.get("operatingScenario") and _list_len(plan.get("titleVariants")) >= 2 and _list_len(plan.get("mainImageStructures")) >= 2 and _list_len(plan.get("testVariables")) >= 1 and _list_len(plan.get("successCriteria")) >= 1 and _list_len(plan.get("failureCriteria")) >= 1 and _list_len(plan.get("submissionConclusionOptions")) >= 2 and plan.get("sopSource") == "llm_agent_multi_route_dynamic_creative")


def _trace_ok(plan: Dict[str, Any]) -> bool:
    trace = plan.get("agentJudgmentTrace") if isinstance(plan.get("agentJudgmentTrace"), dict) else {}
    selected = trace.get("selectedRoute") if isinstance(trace.get("selectedRoute"), dict) else {}
    return bool(_list_len(trace.get("metricRouteCandidates")) >= 3 and selected.get("routeId") and selected.get("routeName") and _list_len(trace.get("rejectedRoutes")) >= 2 and trace.get("platformRead") and trace.get("categoryRead") and trace.get("metricRead") and len(str(trace.get("businessHypothesis") or "")) >= 18)


def _view_ok(plan: Dict[str, Any]) -> bool:
    view = plan.get("operatorJudgmentView") if isinstance(plan.get("operatorJudgmentView"), dict) else {}
    return all(str(view.get(key) or "").strip() for key in ["selectedDirection", "displayReason", "testFocus", "recapBasis"])


def _validate_real_agent_snapshot(snapshot: Dict[str, Any]) -> tuple[bool, str]:
    decision = _task_generation_decision(snapshot)
    evidence = _agent_evidence(snapshot, decision)
    plan = snapshot.get("taskPlan") if isinstance(snapshot.get("taskPlan"), dict) else {}
    if evidence.get("source") != "real_task_mapping_agent":
        return False, "missing_real_task_mapping_agent_evidence"
    if evidence.get("businessEventRouter") != "v19.3_multi_route_judgment":
        return False, "not_v19_3_multi_route_task"
    if snapshot.get("decision") not in READY_DECISIONS:
        return False, "not_a_formal_task_decision"
    if plan.get("taskType") == "observation_task" or snapshot.get("decision") == "system_watch":
        return False, "watch_only_item_not_operator_task"
    if plan.get("taskResponsibility") != "operator_growth" or plan.get("departmentTaskType") != "operator_growth":
        return False, "task_plan_not_operator_growth"
    if not plan.get("productIdentity"):
        return False, "task_plan_missing_product_identity"
    if not plan.get("businessEventId"):
        return False, "task_plan_missing_business_event_id"
    if not _trace_ok(plan):
        return False, "task_plan_missing_backend_multi_route_trace"
    if not _view_ok(plan):
        return False, "task_plan_missing_frontend_operator_judgment_view"
    if not _creative_contract_ok(plan):
        return False, "task_plan_missing_dynamic_creative_contract"
    if len(_as_list(plan.get("sopSteps") or plan.get("steps"))) < 4:
        return False, "task_plan_missing_agent_sop_steps"
    if len(_as_list(plan.get("evidenceRequirements"))) < 2:
        return False, "task_plan_missing_agent_evidence_requirements"
    if not plan.get("title") or not plan.get("reason"):
        return False, "task_plan_missing_agent_title_or_reason"
    return True, "v19_3_multi_route_operator_snapshot_valid"


def _existing_pool_entry(snapshot_id: str) -> Dict[str, Any] | None:
    ensure_task_pool_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM task_pool_entries WHERE task_snapshot_id = ? ORDER BY created_at DESC LIMIT 1", (snapshot_id,)).fetchone()
    return _row_to_pool_entry(row) if row else None


def _update_snapshot_pool_status(snapshot_id: str, status: str, task_id: str | None = None, reason: str | None = None) -> None:
    with connect() as conn:
        if not _table(conn, "task_snapshots"):
            return
        row = conn.execute("SELECT * FROM task_snapshots WHERE task_snapshot_id = ?", (snapshot_id,)).fetchone()
        if not row:
            return
        payload = _loads(row["payload"], {})
        payload["taskPool"] = {"status": status, "taskId": task_id, "reason": reason, "version": TASK_POOL_STATION_VERSION, "rule": "V19.3 TaskPool admits only operator tasks with multi-route judgment trace and operator view."}
        conn.execute("UPDATE task_snapshots SET task_pool_status = ?, payload = ?, updated_at = ? WHERE task_snapshot_id = ?", (status, _json(payload), now_iso(), snapshot_id))
        conn.commit()


def _task_from_existing_entry(entry: Dict[str, Any]) -> Dict[str, Any] | None:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    return payload.get("task") if isinstance(payload.get("task"), dict) else None


def enter_task_pool_from_snapshot(task_snapshot_id: str, *, created_by: str | None = None, force: bool = False) -> Dict[str, Any]:
    ensure_task_pool_tables()
    snapshot = get_task_snapshot(task_snapshot_id)
    if not snapshot:
        return {"version": TASK_POOL_STATION_VERSION, "ok": False, "status": "failed", "error": "task_snapshot_not_found", "taskSnapshotId": task_snapshot_id}
    if snapshot.get("decision") not in READY_DECISIONS or snapshot.get("status") not in READY_SNAPSHOT_STATUSES:
        _update_snapshot_pool_status(task_snapshot_id, "not_eligible", reason="snapshot_not_eligible_for_task_pool")
        return {"version": TASK_POOL_STATION_VERSION, "ok": True, "status": "skipped", "reason": "snapshot_not_eligible_for_task_pool", "snapshot": snapshot, "createdTaskCount": 0}
    valid, reason = _validate_real_agent_snapshot(snapshot)
    if not valid:
        _update_snapshot_pool_status(task_snapshot_id, "rejected_invalid_agent_task", reason=reason)
        return {"version": TASK_POOL_STATION_VERSION, "ok": False, "status": "rejected_invalid_agent_task", "reason": reason, "snapshot": snapshot, "createdTaskCount": 0, "rule": "V19.3 rejects tasks without selected route/operator view/creative SOP."}
    existing = _existing_pool_entry(task_snapshot_id)
    if existing and not force:
        return {"version": TASK_POOL_STATION_VERSION, "ok": True, "status": "idempotent", "poolEntry": existing, "task": _task_from_existing_entry(existing), "createdTaskCount": 0}
    try:
        task = create_lifecycle_task_from_snapshot(snapshot, created_by=created_by)
    except Exception as exc:
        reason = f"lifecycle_task_creation_failed:{str(exc)[:300]}"
        _update_snapshot_pool_status(task_snapshot_id, "rejected_lifecycle_task_error", reason=reason)
        return {"version": TASK_POOL_STATION_VERSION, "ok": False, "status": "rejected_lifecycle_task_error", "reason": reason, "snapshot": snapshot, "createdTaskCount": 0}
    pool_entry_id = make_pool_entry_id()
    created_at = now_iso()
    with connect() as conn:
        conn.execute("""
            INSERT INTO task_pool_entries (pool_entry_id, task_snapshot_id, task_id, data_version, status, decision, task_layer, assignee_id, reviewer_id, dedupe_key, reason, payload, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (pool_entry_id, task_snapshot_id, task.get("id"), snapshot.get("dataVersion"), "entered_task_pool", snapshot.get("decision"), task.get("taskLayer"), task.get("assigneeId"), task.get("reviewerId"), task.get("dedupeKey"), "V19.3多路线经营判断任务已进入任务池。", _json({"snapshot": snapshot, "task": task, "source": "v19_3_multi_route_lifecycle_task_service"}), created_by, created_at, created_at))
        conn.commit()
        row = conn.execute("SELECT * FROM task_pool_entries WHERE pool_entry_id = ?", (pool_entry_id,)).fetchone()
    _update_snapshot_pool_status(task_snapshot_id, "entered_task_pool", task.get("id"), reason="v19_3_multi_route_task_entered_pool")
    return {"version": TASK_POOL_STATION_VERSION, "ok": True, "status": "entered_task_pool", "poolEntry": _row_to_pool_entry(row), "task": task, "createdTaskCount": 1}


def sync_ready_task_snapshots(*, data_version: str | None = None, limit: int = 50, created_by: str | None = None) -> Dict[str, Any]:
    ensure_task_pool_tables()
    snapshot_result = list_task_snapshots(data_version=data_version, limit=limit)
    snapshots = [item for item in snapshot_result.get("snapshots", []) if item.get("decision") in READY_DECISIONS and item.get("status") in READY_SNAPSHOT_STATUSES and item.get("taskPoolStatus") != "entered_task_pool"]
    results = [enter_task_pool_from_snapshot(item["taskSnapshotId"], created_by=created_by) for item in snapshots]
    return {"version": TASK_POOL_STATION_VERSION, "status": "completed", "dataVersion": data_version, "candidateSnapshotCount": len(snapshots), "createdTaskCount": sum(int(item.get("createdTaskCount") or 0) for item in results), "rejectedCount": sum(1 for item in results if str(item.get("status") or "").startswith("rejected")), "results": results, "rule": "V19.3 only multi-route operator tasks enter task pool."}


def list_task_pool_entries(limit: int = 80) -> Dict[str, Any]:
    ensure_task_pool_tables()
    with connect() as conn:
        rows = conn.execute("SELECT * FROM task_pool_entries ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = [_row_to_pool_entry(row) for row in rows]
    return {"version": TASK_POOL_STATION_VERSION, "entries": items, "entryCount": len(items)}


def task_pool_summary(limit: int = 80) -> Dict[str, Any]:
    entries = list_task_pool_entries(limit=limit).get("entries") or []
    by_status: Dict[str, int] = {}
    by_layer: Dict[str, int] = {}
    for item in entries:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
        by_layer[item["taskLayer"]] = by_layer.get(item["taskLayer"], 0) + 1
    return {"version": TASK_POOL_STATION_VERSION, "entryCount": len(entries), "byStatus": by_status, "byTaskLayer": by_layer, "byResponsibility": {"operator_growth": len(entries)}, "latest": entries[0] if entries else None, "entries": entries, "rule": "V19.3任务池只展示最终选定方向的运营任务；后端排除路线不在运营页展开。"}
