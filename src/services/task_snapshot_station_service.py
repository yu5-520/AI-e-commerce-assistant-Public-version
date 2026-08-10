"""V18.0 Task Snapshot Station service.

TaskSnapshot is a contract boundary. It must preserve the task mapping Agent
contract exactly enough for TaskPool strict validation:
- taskMappingAgentEvidence
- fallbackForbidden / businessNoTaskForbidden
- productJudgmentPackage / RAG context / taskPlan / dataVersion

Observation and evidence-collection are formal lifecycle tasks.

Competition lineage rule:
- productRegistryKey identifies the product entity.
- productSnapshotHash identifies the immutable fact version frozen into the Task.
- an existing productSnapshotHash is strict and never falls back to another id.
- canonical snapshot materialization must commit before TaskSnapshot created_at.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict

from src.repositories.sqlite_repository import connect, ensure_columns
from src.services.system_product_snapshot_service import (
    bind_task_product_lineage,
    materialize_system_product_snapshot,
)

TASK_SNAPSHOT_STATION_VERSION = "18.0"
TASK_SNAPSHOT_LINEAGE_GUARD_VERSION = "1.0"
VALID_DECISIONS = {"create_task_snapshot", "manager_review_required", "observe_only", "ignore_noise"}
READY_DECISIONS = {"create_task_snapshot", "manager_review_required"}


def now_iso() -> str:
    return datetime.now().isoformat()


def make_snapshot_id() -> str:
    return f"TS-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _active_data_version_exists(data_version: str) -> bool:
    """Only active imported-report dataVersions may feed a newly frozen Task."""
    with connect() as conn:
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='imported_report_rows' LIMIT 1"
        ).fetchone()
        if not table_exists:
            return False
        row = conn.execute(
            "SELECT 1 FROM imported_report_rows WHERE data_version = ? LIMIT 1",
            (str(data_version),),
        ).fetchone()
    return bool(row)


def _prepare_task_product_lineage(
    task: Dict[str, Any] | None,
    *,
    user_id: str | None = None,
) -> Dict[str, Any]:
    """Commit canonical facts before TaskSnapshot establishes its time boundary."""
    prepared: Dict[str, Any] = dict(task or {})
    data_version = _first_non_empty(
        prepared.get("dataVersion"),
        prepared.get("data_version"),
        prepared.get("workflowRunId"),
        prepared.get("workflow_run_id"),
    )

    if not data_version:
        return bind_task_product_lineage(prepared)

    if not _active_data_version_exists(data_version):
        result = dict(prepared)
        result["productSnapshot"] = {}
        result["productSnapshotStatus"] = "lineage_broken"
        result["productSnapshotLineage"] = {
            "version": TASK_SNAPSHOT_LINEAGE_GUARD_VERSION,
            "ready": False,
            "status": "lineage_broken",
            "reason": "task_data_version_not_active",
            "dataVersion": data_version,
            "strictHash": bool(str(result.get("productSnapshotHash") or "").strip()),
            "writeBarrier": "active_import_required_before_task_timestamp",
        }
        return result

    materialize_system_product_snapshot(
        data_version,
        user_id=str(user_id or "task_snapshot_station"),
        force=False,
    )
    result = bind_task_product_lineage(prepared)
    lineage = dict(result.get("productSnapshotLineage") or {})
    lineage.setdefault("writeBarrierVersion", TASK_SNAPSHOT_LINEAGE_GUARD_VERSION)
    lineage.setdefault("writeBarrier", "canonical_snapshot_before_task_timestamp")
    result["productSnapshotLineage"] = lineage
    return result


def ensure_task_snapshot_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS task_snapshots (
                task_snapshot_id TEXT PRIMARY KEY,
                handoff_id TEXT,
                data_version TEXT,
                entity_type TEXT,
                entity_id TEXT,
                decision TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                trend_type TEXT,
                priority TEXT,
                task_type TEXT,
                action_type TEXT,
                need_manager_review INTEGER DEFAULT 0,
                signal_ref TEXT,
                rag_context TEXT,
                agent_judgment TEXT,
                task_plan TEXT,
                evidence_requirements TEXT,
                payload TEXT,
                task_pool_status TEXT,
                created_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_columns(conn, "task_snapshots", {"handoff_id": "TEXT", "data_version": "TEXT", "entity_type": "TEXT", "entity_id": "TEXT", "confidence": "REAL DEFAULT 0", "trend_type": "TEXT", "priority": "TEXT", "task_type": "TEXT", "action_type": "TEXT", "need_manager_review": "INTEGER DEFAULT 0", "signal_ref": "TEXT", "rag_context": "TEXT", "agent_judgment": "TEXT", "task_plan": "TEXT", "evidence_requirements": "TEXT", "payload": "TEXT", "task_pool_status": "TEXT", "created_by": "TEXT"})
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_snapshots_handoff ON task_snapshots(handoff_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_snapshots_version ON task_snapshots(data_version, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_snapshots_decision ON task_snapshots(decision, status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_snapshots_signal ON task_snapshots(signal_ref, decision, data_version)")
        conn.commit()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _normalize_decision(value: str | None) -> str:
    raw = str(value or "create_task_snapshot").strip()
    aliases = {"create_task": "create_task_snapshot", "create": "create_task_snapshot", "task": "create_task_snapshot", "manager_review": "manager_review_required", "review_required": "manager_review_required", "observe": "create_task_snapshot", "observation": "create_task_snapshot", "create_observation_task": "create_task_snapshot", "create_data_evidence_task": "create_task_snapshot", "data_evidence": "create_task_snapshot", "ignore": "ignore_noise", "noise": "ignore_noise"}
    decision = aliases.get(raw, raw)
    return decision if decision in VALID_DECISIONS else "create_task_snapshot"


def _snapshot_status(decision: str) -> str:
    if decision == "create_task_snapshot":
        return "snapshot_ready"
    if decision == "manager_review_required":
        return "manager_review_required"
    if decision == "ignore_noise":
        return "noise_ignored"
    return "observation_recorded"


def _decision_from_system_facts(system_facts: Dict[str, Any]) -> Dict[str, Any]:
    decision = system_facts.get("taskGenerationDecision") if isinstance(system_facts.get("taskGenerationDecision"), dict) else {}
    return decision


def _row_to_snapshot(row: Any) -> Dict[str, Any]:
    payload = _loads(row["payload"], {})
    system_facts = payload.get("systemFacts") if isinstance(payload.get("systemFacts"), dict) else {}
    decision = _decision_from_system_facts(system_facts)
    return {
        "version": TASK_SNAPSHOT_STATION_VERSION,
        "taskSnapshotId": row["task_snapshot_id"],
        "handoffId": row["handoff_id"],
        "dataVersion": row["data_version"],
        "entityType": row["entity_type"],
        "entityId": row["entity_id"],
        "decision": row["decision"],
        "status": row["status"],
        "confidence": float(row["confidence"] or 0),
        "trendType": row["trend_type"],
        "priority": row["priority"],
        "taskType": row["task_type"],
        "actionType": row["action_type"],
        "needManagerReview": bool(row["need_manager_review"]),
        "signalRef": row["signal_ref"],
        "ragContext": _loads(row["rag_context"], {}),
        "agentJudgment": _loads(row["agent_judgment"], {}),
        "taskPlan": _loads(row["task_plan"], {}),
        "evidenceRequirements": _loads(row["evidence_requirements"], []),
        "payload": payload,
        "productIdentity": payload.get("productIdentity") or {},
        "productRegistryKey": payload.get("productRegistryKey"),
        "productSnapshotHash": payload.get("productSnapshotHash"),
        "productSnapshot": payload.get("productSnapshot") or {},
        "productSnapshotLineage": payload.get("productSnapshotLineage") or {},
        "operatorExecutionSop": payload.get("operatorExecutionSop") or [],
        "sopSteps": payload.get("sopSteps") or [],
        "taskMappingAgentEvidence": payload.get("taskMappingAgentEvidence") or decision.get("taskMappingAgentEvidence") or {},
        "fallbackForbidden": bool(payload.get("fallbackForbidden") or decision.get("fallbackForbidden")),
        "businessNoTaskForbidden": bool(payload.get("businessNoTaskForbidden") or decision.get("businessNoTaskForbidden")),
        "taskPoolStatus": row["task_pool_status"],
        "createdBy": row["created_by"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _existing_snapshot_for_signal(data_version: str | None, signal_ref: str | None, decision: str) -> Dict[str, Any] | None:
    if not signal_ref:
        return None
    ensure_task_snapshot_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM task_snapshots WHERE signal_ref = ? AND decision = ? AND COALESCE(data_version, '') = COALESCE(?, '') ORDER BY created_at DESC LIMIT 1", (signal_ref, decision, data_version)).fetchone()
    return _row_to_snapshot(row) if row else None


def _update_handoff_for_snapshot(snapshot: Dict[str, Any]) -> None:
    handoff_id = snapshot.get("handoffId")
    if not handoff_id:
        return
    decision = snapshot.get("decision")
    with connect() as conn:
        row = conn.execute("SELECT * FROM station_handoffs WHERE handoff_id = ?", (handoff_id,)).fetchone()
        if not row:
            return
        payload = _loads(row["payload"], {})
        payload["taskSnapshot"] = {"taskSnapshotId": snapshot.get("taskSnapshotId"), "decision": decision, "status": snapshot.get("status"), "confidence": snapshot.get("confidence"), "productSnapshotHash": snapshot.get("productSnapshotHash")}
        conn.execute("UPDATE station_handoffs SET status = ?, decision_status = ?, output_ref = ?, task_snapshot_count = task_snapshot_count + 1, payload = ?, updated_at = ? WHERE handoff_id = ?", ("task_snapshot_ready" if decision in READY_DECISIONS else "agent_judgment_recorded", decision, f"task_snapshot:{snapshot.get('taskSnapshotId')}", _json(payload), now_iso(), handoff_id))
        conn.commit()


def create_task_snapshot(body: Dict[str, Any] | None = None, *, created_by: str | None = None, force: bool = False) -> Dict[str, Any]:
    body = _prepare_task_product_lineage(dict(body or {}), user_id=created_by)
    ensure_task_snapshot_tables()
    decision = _normalize_decision(body.get("decision") or (body.get("agentJudgment") or {}).get("decision"))
    task_plan = dict(body.get("taskPlan")) if isinstance(body.get("taskPlan"), dict) else {}
    product_identity = body.get("productIdentity") if isinstance(body.get("productIdentity"), dict) else task_plan.get("productIdentity") if isinstance(task_plan.get("productIdentity"), dict) else {}
    product_registry_key = body.get("productRegistryKey") or product_identity.get("productRegistryKey") or product_identity.get("objectId")
    product_snapshot_hash = body.get("productSnapshotHash") or product_identity.get("productSnapshotHash")
    product_snapshot = body.get("productSnapshot") if isinstance(body.get("productSnapshot"), dict) else {}
    product_snapshot_lineage = body.get("productSnapshotLineage") if isinstance(body.get("productSnapshotLineage"), dict) else {}
    if product_identity:
        task_plan["productIdentity"] = product_identity
    if product_registry_key:
        task_plan["productRegistryKey"] = product_registry_key
    if product_snapshot_hash:
        task_plan["productSnapshotHash"] = product_snapshot_hash
    agent_judgment = body.get("agentJudgment") if isinstance(body.get("agentJudgment"), dict) else {}
    rag_context = body.get("ragContext") if isinstance(body.get("ragContext"), dict) else {}
    data_version = body.get("dataVersion") or body.get("data_version")
    signal_ref = body.get("signalRef") or body.get("signal_ref")
    if not force:
        existing = _existing_snapshot_for_signal(data_version, signal_ref, decision)
        if existing:
            existing["idempotentHit"] = True
            return existing
    evidence = body.get("evidenceRequirements") or task_plan.get("evidenceRequirements") or []
    if not isinstance(evidence, list):
        evidence = [str(evidence)]
    try:
        confidence_value = max(0.0, min(1.0, float(body.get("confidence") if body.get("confidence") is not None else agent_judgment.get("confidence") or 0)))
    except Exception:
        confidence_value = 0.0
    system_facts = body.get("systemFacts") if isinstance(body.get("systemFacts"), dict) else {}
    generation_decision = _decision_from_system_facts(system_facts)
    task_mapping_evidence = body.get("taskMappingAgentEvidence") if isinstance(body.get("taskMappingAgentEvidence"), dict) else generation_decision.get("taskMappingAgentEvidence") if isinstance(generation_decision.get("taskMappingAgentEvidence"), dict) else {}
    product_package = body.get("productJudgmentPackage") or generation_decision.get("productJudgmentPackage") or system_facts.get("sceneDataJudgmentPackage") or {}
    operator_sop = body.get("operatorExecutionSop") or task_plan.get("operatorExecutionSop") or body.get("sopSteps") or task_plan.get("sopSteps") or []
    if not isinstance(operator_sop, list):
        operator_sop = []
    snapshot_id = make_snapshot_id()
    created_at = now_iso()
    payload = {
        "version": TASK_SNAPSHOT_STATION_VERSION,
        "taskSnapshotId": snapshot_id,
        "handoffId": body.get("handoffId") or body.get("handoff_id"),
        "dataVersion": data_version,
        "decision": decision,
        "stationId": "task_snapshot_station",
        "source": body.get("source") or "task_mapping_agent_station",
        "systemFacts": system_facts,
        "ragContext": rag_context,
        "agentJudgment": agent_judgment,
        "taskPlan": task_plan,
        "evidenceRequirements": evidence,
        "taskMappingAgentEvidence": task_mapping_evidence,
        "fallbackForbidden": bool(body.get("fallbackForbidden") or generation_decision.get("fallbackForbidden")),
        "businessNoTaskForbidden": bool(body.get("businessNoTaskForbidden") or generation_decision.get("businessNoTaskForbidden")),
        "productJudgmentPackage": product_package,
        "productIdentity": product_identity,
        "productRegistryKey": product_registry_key,
        "productSnapshotHash": product_snapshot_hash,
        "productSnapshot": product_snapshot,
        "productSnapshotLineage": product_snapshot_lineage,
        "productSnapshotStatus": body.get("productSnapshotStatus"),
        "operatorExecutionSop": operator_sop,
        "sopSteps": operator_sop,
        "rawTaskGenerationDecision": generation_decision,
        "taskPoolStatus": "not_entered",
        "rule": "V18.0 TaskSnapshot preserves Agent evidence and freezes canonical productSnapshotHash for downstream Task/SOP detail.",
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO task_snapshots (task_snapshot_id, handoff_id, data_version, entity_type, entity_id, decision, status, confidence, trend_type, priority, task_type, action_type, need_manager_review, signal_ref, rag_context, agent_judgment, task_plan, evidence_requirements, payload, task_pool_status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (snapshot_id, body.get("handoffId") or body.get("handoff_id"), data_version, body.get("entityType") or task_plan.get("entityType") or "product", body.get("entityId") or task_plan.get("entityId") or body.get("productId"), decision, _snapshot_status(decision), confidence_value, body.get("trendType") or agent_judgment.get("trendType"), task_plan.get("priority") or body.get("priority") or "中", task_plan.get("taskType") or body.get("taskType") or "经营任务快照", task_plan.get("actionType") or body.get("actionType"), 1 if decision == "manager_review_required" or bool(task_plan.get("needManagerReview")) else 0, signal_ref, _json(rag_context), _json(agent_judgment), _json(task_plan), _json(evidence), _json(payload), "not_entered", created_by, created_at, created_at),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM task_snapshots WHERE task_snapshot_id = ?", (snapshot_id,)).fetchone()
    snapshot = _row_to_snapshot(row)
    _update_handoff_for_snapshot(snapshot)
    snapshot["rule"] = "V18.0 snapshot package preserves Agent evidence and canonical product lineage; pool entry is handled by task_pool_station."
    return snapshot


def list_task_snapshots(data_version: str | None = None, handoff_id: str | None = None, limit: int = 50) -> Dict[str, Any]:
    ensure_task_snapshot_tables()
    with connect() as conn:
        if handoff_id:
            rows = conn.execute("SELECT * FROM task_snapshots WHERE handoff_id = ? ORDER BY created_at DESC LIMIT ?", (handoff_id, limit)).fetchall()
        elif data_version:
            rows = conn.execute("SELECT * FROM task_snapshots WHERE data_version = ? ORDER BY created_at DESC LIMIT ?", (data_version, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM task_snapshots ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    items = [_row_to_snapshot(row) for row in rows]
    return {"version": TASK_SNAPSHOT_STATION_VERSION, "snapshots": items, "snapshotCount": len(items), "dataVersion": data_version, "handoffId": handoff_id}


def get_task_snapshot(task_snapshot_id: str) -> Dict[str, Any] | None:
    ensure_task_snapshot_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM task_snapshots WHERE task_snapshot_id = ?", (task_snapshot_id,)).fetchone()
    return _row_to_snapshot(row) if row else None


def task_snapshot_summary(limit: int = 50) -> Dict[str, Any]:
    result = list_task_snapshots(limit=limit)
    items = result.get("snapshots") or []
    by_decision: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    for item in items:
        by_decision[item["decision"]] = by_decision.get(item["decision"], 0) + 1
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    return {"version": TASK_SNAPSHOT_STATION_VERSION, "total": len(items), "byDecision": by_decision, "byStatus": by_status, "latest": items[0] if items else None, "items": items, "rule": "V18.0 snapshots are idempotent by signalRef + decision + dataVersion and preserve Agent evidence plus canonical product lineage."}
