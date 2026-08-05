"""V6.6 approval lifecycle service.

V6.5 decides the role quota and approval chain. V6.6 persists that chain as a
lifecycle: application submitted, staged approval, final approval, and execution
handoff. Approval does not execute business actions directly; it creates a
separate execution task candidate.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services import module_task_service

APPROVAL_LIFECYCLE_VERSION = "6.6.0"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}".upper()


def ensure_approval_lifecycle_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_flows_v6 (
                flow_id TEXT PRIMARY KEY,
                task_id TEXT,
                product_id TEXT,
                store_id TEXT,
                risk_level TEXT,
                status TEXT NOT NULL,
                requester_role_id TEXT,
                current_stage TEXT,
                approval_chain TEXT,
                execution_task_id TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS approval_events_v6 (
                event_id TEXT PRIMARY KEY,
                flow_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_role_id TEXT,
                note TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_flows_status_v6 ON approval_flows_v6(status, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_flows_task_v6 ON approval_flows_v6(task_id, product_id)")
        conn.commit()


def _insert_event(flow_id: str, event_type: str, actor_role_id: str | None, note: str | None, payload: Dict[str, Any] | None = None) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO approval_events_v6 (event_id, flow_id, event_type, actor_role_id, note, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (make_id("AEVT"), flow_id, event_type, actor_role_id, note, dumps(payload or {}), now_iso()),
        )
        conn.commit()


def _flow_payload(row: Any) -> Dict[str, Any]:
    payload = loads(row["payload"])
    if payload:
        return payload
    return dict(row)


def _load_flow(flow_id: str) -> Dict[str, Any] | None:
    ensure_approval_lifecycle_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM approval_flows_v6 WHERE flow_id = ?", (flow_id,)).fetchone()
    return _flow_payload(row) if row else None


def _create_execution_task(flow: Dict[str, Any]) -> Dict[str, Any]:
    original = flow.get("sourceTask") or {}
    budget = original.get("permissionBudgetGate") or flow.get("permissionBudgetGate") or {}
    payload = {
        **original,
        "id": make_id("EXEC"),
        "title": f"{original.get('title') or flow.get('productId')} · 审批后执行",
        "task": "审批通过后的执行任务",
        "taskType": "审批通过后的执行任务",
        "priority": original.get("priority") or flow.get("riskLevel") or "中",
        "source": "审批中心",
        "sourceModule": "审批中心",
        "sourceRoute": "trend-center",
        "actionType": "执行",
        "taskLayer": "operator_execution",
        "approvalFlowId": flow.get("flowId"),
        "approvalStatus": "approved",
        "executionAllowed": True,
        "investmentApplicationAllowed": False,
        "executionRequirements": [
            "审批已通过，执行动作仍必须遵守 RAG 指标和最终审批额度。",
            f"审批额度：{budget.get('suggestedTotalBudget', 0)} 元。",
        ],
        "sourceTrail": [*(original.get("sourceTrail") or []), "审批生命周期", "执行任务"],
    }
    return module_task_service.create_task(payload)


def create_approval_flow_for_task(task: Dict[str, Any], requester_role_id: str = "operator") -> Dict[str, Any]:
    """Persist an approval flow for a task when quota/risk requires approval."""
    ensure_approval_lifecycle_tables()
    budget = task.get("permissionBudgetGate") or {}
    chain = list(task.get("approvalChain") or budget.get("approvalChain") or [])
    needs_approval = bool(task.get("riskGrade") == "高" or budget.get("needsApproval") or chain)
    status = "pending_approval" if needs_approval else "approved"
    current_stage = chain[0] if chain else None
    now = now_iso()
    flow = {
        "version": APPROVAL_LIFECYCLE_VERSION,
        "flowId": make_id("AFLOW"),
        "taskId": task.get("id"),
        "productId": task.get("productId") or task.get("entityId"),
        "storeId": (task.get("storeIds") or [None])[0],
        "riskLevel": task.get("riskGrade") or task.get("priority"),
        "status": status,
        "requesterRoleId": requester_role_id,
        "currentStage": current_stage,
        "approvalChain": chain,
        "approvedStages": [],
        "executionTaskId": None,
        "sourceTask": task,
        "permissionBudgetGate": budget,
        "createdAt": now,
        "updatedAt": now,
        "rule": "V6.6 申请和执行分离：审批通过后才生成执行任务，审批本身不直接执行业务动作。",
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO approval_flows_v6 (
                flow_id, task_id, product_id, store_id, risk_level, status, requester_role_id,
                current_stage, approval_chain, execution_task_id, payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flow["flowId"], flow["taskId"], flow["productId"], flow["storeId"], flow["riskLevel"], flow["status"],
                requester_role_id, current_stage, dumps(chain), None, dumps(flow), now, now,
            ),
        )
        conn.commit()
    _insert_event(flow["flowId"], "submitted" if needs_approval else "auto_approved", requester_role_id, "V6.6 自动创建审批生命周期。", flow)
    return flow


def approve_flow(flow_id: str, approver_role_id: str, note: str | None = None) -> Dict[str, Any]:
    flow = _load_flow(flow_id)
    if not flow:
        raise ValueError(f"approval flow not found: {flow_id}")
    chain = list(flow.get("approvalChain") or [])
    approved = list(flow.get("approvedStages") or [])
    current = flow.get("currentStage")
    if current and approver_role_id != current and approver_role_id != "owner":
        raise ValueError(f"current stage requires {current}, got {approver_role_id}")
    if current and current not in approved:
        approved.append(current)
    remaining = [stage for stage in chain if stage not in approved]
    execution_task = None
    if remaining:
        flow["status"] = "pending_approval"
        flow["currentStage"] = remaining[0]
    else:
        flow["status"] = "approved"
        flow["currentStage"] = None
        execution_task = _create_execution_task(flow)
        flow["executionTaskId"] = execution_task.get("id")
    flow["approvedStages"] = approved
    flow["updatedAt"] = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE approval_flows_v6 SET status=?, current_stage=?, execution_task_id=?, payload=?, updated_at=? WHERE flow_id=?",
            (flow["status"], flow.get("currentStage"), flow.get("executionTaskId"), dumps(flow), flow["updatedAt"], flow_id),
        )
        conn.commit()
    _insert_event(flow_id, "approved", approver_role_id, note or "审批通过。", {"flow": flow, "executionTask": execution_task})
    return {"version": APPROVAL_LIFECYCLE_VERSION, "flow": flow, "executionTask": execution_task}


def reject_flow(flow_id: str, approver_role_id: str, note: str | None = None) -> Dict[str, Any]:
    flow = _load_flow(flow_id)
    if not flow:
        raise ValueError(f"approval flow not found: {flow_id}")
    flow["status"] = "rejected"
    flow["currentStage"] = None
    flow["rejectedBy"] = approver_role_id
    flow["updatedAt"] = now_iso()
    with connect() as conn:
        conn.execute("UPDATE approval_flows_v6 SET status=?, current_stage=?, payload=?, updated_at=? WHERE flow_id=?", (flow["status"], None, dumps(flow), flow["updatedAt"], flow_id))
        conn.commit()
    _insert_event(flow_id, "rejected", approver_role_id, note or "审批驳回。", flow)
    return {"version": APPROVAL_LIFECYCLE_VERSION, "flow": flow}


def approval_lifecycle_summary(limit: int = 30) -> Dict[str, Any]:
    ensure_approval_lifecycle_tables()
    with connect() as conn:
        flows = conn.execute("SELECT * FROM approval_flows_v6 ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        events = conn.execute("SELECT * FROM approval_events_v6 ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    flow_items = [_flow_payload(row) for row in flows]
    event_items = [{"eventId": row["event_id"], "flowId": row["flow_id"], "eventType": row["event_type"], "actorRoleId": row["actor_role_id"], "note": row["note"], "payload": loads(row["payload"]), "createdAt": row["created_at"]} for row in events]
    by_status: Dict[str, int] = defaultdict(int)
    for item in flow_items:
        by_status[str(item.get("status") or "unknown")] += 1
    return {"version": APPROVAL_LIFECYCLE_VERSION, "total": len(flow_items), "byStatus": dict(by_status), "latestFlows": flow_items, "latestEvents": event_items}
