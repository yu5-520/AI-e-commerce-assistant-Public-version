"""V21.7.7 materialized task-detail read model.

Task detail GET reads a stored projection. The projection preserves numeric
authorization, operation Plan IR and Agent2 proof, but exposes one canonical
single-action contract. Cross-family plan fields are removed before storage and
no Agent is re-run on the read path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, init_db, loads
from src.runtime_version import TASK_DETAIL_PROJECTION_VERSION
from src.services.v2177_agent2_single_action_contract_service import (
    ACTIVE_ACTION_CONTRACT_VERSION,
    AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
    METRIC_DIGEST_VERSION,
    active_action_contract,
    allowed_plan_fields,
    sanitize_plan,
)

TASK_DETAIL_SNAPSHOT_VERSION = TASK_DETAIL_PROJECTION_VERSION
_PLAN_FIELDS = {
    "creativeTestPlan",
    "budgetPlan",
    "activityPlan",
    "conversionRepairPlan",
    "similarProductPlan",
}


def _ensure_table(conn: Any) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_detail_snapshots (
            task_id TEXT PRIMARY KEY,
            data_version TEXT,
            snapshot_json TEXT NOT NULL,
            source_version TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_detail_snapshots_data_version ON task_detail_snapshots(data_version,updated_at)")


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    return [item for item in value if item not in [None, "", {}, []]] if isinstance(value, list) else []


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _first(*values: Any) -> Any:
    for value in values:
        if value not in [None, "", [], {}, "UNKNOWN", "未识别", "—", "未提供"]:
            return value
    return None


def _deep_find(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value and value.get(key) not in [None, "", [], {}]:
                return value.get(key)
        for child in value.values():
            found = _deep_find(child, keys)
            if found not in [None, "", [], {}]:
                return found
    elif isinstance(value, list):
        for child in value[:30]:
            found = _deep_find(child, keys)
            if found not in [None, "", [], {}]:
                return found
    return None


def _authorization(task: Dict[str, Any], report: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    return _dict(
        _first(
            task.get("authorizationDecision"),
            task.get("actionAuthorization"),
            report.get("authorizationDecision"),
            report.get("actionAuthorization"),
            plan.get("authorizationDecision"),
            plan.get("actionAuthorization"),
            _deep_find(task.get("systemFacts"), {"authorizationDecision", "actionAuthorization"}),
            {},
        )
    )


def _clean_container(container: Dict[str, Any], action_family: str) -> Dict[str, Any]:
    result = dict(_dict(container))
    allowed = allowed_plan_fields(action_family)
    result.pop("agent2ActionPlan", None)
    for field in _PLAN_FIELDS:
        if field not in allowed:
            result.pop(field, None)
    return result


def build_task_detail_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
    task = dict(_dict(task))
    task_id = str(_first(task.get("taskId"), task.get("task_id"), task.get("id")) or "")
    report = dict(_dict(task.get("taskDetailReport")))
    plan = dict(_dict(_first(task.get("taskPlan"), report.get("taskPlan"), task.get("taskCard"), {})))
    product = dict(
        _dict(
            _first(
                task.get("productIdentity"),
                report.get("productIdentity"),
                plan.get("productIdentity"),
                (_list(task.get("productActionCards")) or [{}])[0],
                {},
            )
        )
    )
    operator_sop = _list(
        _first(
            task.get("operatorExecutionSop"),
            report.get("operatorExecutionSop"),
            plan.get("operatorExecutionSop"),
            plan.get("operatorActionSteps"),
            task.get("sopSteps"),
            report.get("sopSteps"),
            [],
        )
    )
    status = str(_first(task.get("status"), task.get("workflowStatus"), "待接收"))
    title = str(
        _first(
            task.get("title"),
            task.get("taskTitle"),
            _dict(task.get("taskCard")).get("title"),
            product.get("productTitle"),
            product.get("title"),
            "任务详情",
        )
    )
    authorization = _authorization(task, report, plan)
    operation_plan = _dict(
        _first(
            task.get("operationPlan"),
            report.get("operationPlan"),
            plan.get("operationPlan"),
            _deep_find(task.get("systemFacts"), {"operationPlan"}),
            {},
        )
    )
    proof = _dict(
        _first(
            task.get("agent2ExecutionProof"),
            report.get("agent2ExecutionProof"),
            plan.get("agent2ExecutionProof"),
            _deep_find(task.get("systemFacts"), {"agent2ExecutionProof"}),
            {},
        )
    )
    raw_agent2 = _dict(
        _first(
            task.get("agent2ActionPlan"),
            report.get("agent2ActionPlan"),
            plan.get("agent2ActionPlan"),
            _deep_find(task.get("systemFacts"), {"agent2ActionPlan"}),
            {},
        )
    )
    agent2 = sanitize_plan(raw_agent2) if raw_agent2 else {}
    action_family = str(
        _first(
            agent2.get("actionFamily"),
            task.get("actionFamily"),
            plan.get("selectedActionFamily"),
            task.get("selectedActionFamily"),
            "",
        )
        or ""
    )
    plan = _clean_container(plan, action_family)
    report = _clean_container(report, action_family)

    if operation_plan:
        plan["operationPlan"] = operation_plan
    if proof:
        plan["agent2ExecutionProof"] = proof
    if authorization:
        plan["authorizationDecision"] = authorization
        plan["actionAuthorization"] = authorization
        plan["authorizationVersion"] = task.get("authorizationVersion") or authorization.get("version")
    plan.update(
        {
            "title": plan.get("title") or title,
            "taskTitle": plan.get("taskTitle") or title,
            "productIdentity": product,
            "operatorExecutionSop": operator_sop,
            "sopSteps": operator_sop,
        }
    )

    metric_digest = _dict(
        _first(
            task.get("metricDigest"),
            report.get("metricDigest"),
            plan.get("metricDigest"),
            _dict(task.get("productJudgmentPackage")).get("metricDigest"),
            {},
        )
    )
    if metric_digest:
        metric_digest.setdefault("version", METRIC_DIGEST_VERSION)
    existing_contract = _dict(
        _first(
            task.get("activeActionContract"),
            report.get("activeActionContract"),
            plan.get("activeActionContract"),
            {},
        )
    )
    active_contract = (
        active_action_contract(agent2, sop={"operatorExecutionSop": operator_sop, "taskPlan": plan}, authority=authorization)
        if agent2
        else existing_contract
    )
    if active_contract:
        active_contract["version"] = ACTIVE_ACTION_CONTRACT_VERSION
    agent2_plan_ref = str(
        _first(
            task.get("agent2PlanRef"),
            report.get("agent2PlanRef"),
            plan.get("agent2PlanRef"),
            f"agent2_plan:{agent2.get('packageId') or task.get('packageId') or task_id}" if agent2 or task_id else None,
            "",
        )
        or ""
    )

    plan["activeActionContract"] = active_contract
    plan["agent2PlanRef"] = agent2_plan_ref
    plan["singleActionContractVersion"] = AGENT2_SINGLE_ACTION_CONTRACT_VERSION
    if metric_digest:
        plan["metricDigest"] = metric_digest

    report.update(
        {
            "title": report.get("title") or title,
            "productIdentity": product,
            "taskPlan": plan,
            "operatorExecutionSop": operator_sop,
            "operatorSopSteps": operator_sop,
            "sopSteps": operator_sop,
            "agent2ActionPlan": agent2,
            "operationPlan": operation_plan,
            "agent2ExecutionProof": proof,
            "authorizationDecision": authorization,
            "actionAuthorization": authorization,
            "metricDigest": metric_digest,
            "agent2PlanRef": agent2_plan_ref,
            "activeActionContract": active_contract,
            "singleActionContractVersion": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
        }
    )
    related = {
        **_clean_container(task, action_family),
        "id": task_id,
        "taskId": task_id,
        "task_id": task_id,
        "title": title,
        "status": status,
        "workflowStatus": status,
        "displayStatus": status,
        "productIdentity": product,
        "taskPlan": plan,
        "operatorExecutionSop": operator_sop,
        "sopSteps": operator_sop,
        "authorizationDecision": authorization,
        "actionAuthorization": authorization,
        "operationPlan": operation_plan,
        "agent2ExecutionProof": proof,
        "agent2ActionPlan": agent2,
        "metricDigest": metric_digest,
        "agent2PlanRef": agent2_plan_ref,
        "activeActionContract": active_contract,
        "singleActionContractVersion": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
    }
    discarded = _list(agent2.get("discardedCrossFamilyFields"))
    return {
        "version": TASK_DETAIL_SNAPSHOT_VERSION,
        "ready": bool(task_id),
        "id": task_id,
        "taskId": task_id,
        "task_id": task_id,
        "dataVersion": task.get("dataVersion") or task.get("workflowRunId") or task.get("workflow_run_id"),
        "title": title,
        "taskStatus": status,
        "relatedTask": related,
        "taskDetailReport": report,
        "productIdentity": product,
        "systemChangePack": _dict(_first(task.get("systemChangePack"), report.get("systemChangePack"), {})),
        "dynamicMetricChanges": _list(_first(task.get("dynamicMetricChanges"), report.get("dynamicMetricChanges"), _dict(task.get("systemChangePack")).get("dynamicMetricChanges"), [])),
        "operatorJudgmentView": _dict(_first(task.get("operatorJudgmentView"), report.get("operatorJudgmentView"), plan.get("operatorJudgmentView"), {})),
        "agentOperatingJudgment": _dict(_first(task.get("agentOperatingJudgment"), report.get("agentOperatingJudgment"), {})),
        "agentJudgment": _dict(_first(task.get("agentJudgment"), report.get("agentJudgment"), {})),
        "agent2ActionPlan": agent2,
        "operationPlan": operation_plan,
        "agent2ExecutionProof": proof,
        "authorizationDecision": authorization,
        "actionAuthorization": authorization,
        "authorizationVersion": task.get("authorizationVersion") or plan.get("authorizationVersion") or authorization.get("version"),
        "authorityParameters": _dict(authorization.get("parameters")),
        "effectiveLimits": _dict(authorization.get("effectiveLimits")),
        "authorityUsage": _dict(authorization.get("usage")),
        "approvalRequired": authorization.get("approvalRequired"),
        "requiredAuthorityLevel": authorization.get("requiredAuthorityLevel"),
        "operatorExecutionSop": operator_sop,
        "operatorSopSteps": operator_sop,
        "sopSteps": operator_sop,
        "autoReviewPlan": _dict(_first(task.get("autoReviewPlan"), report.get("autoReviewPlan"), task.get("autoRecapPlan"), {})),
        "autoRecapPlan": _dict(_first(task.get("autoRecapPlan"), report.get("autoRecapPlan"), task.get("autoReviewPlan"), {})),
        "taskLifecycle": _dict(_first(task.get("taskLifecycle"), report.get("taskLifecycle"), {})),
        "chainIntegrity": _dict(_first(task.get("chainIntegrity"), report.get("chainIntegrity"), {})),
        "singleActionContractVersion": AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
        "metricDigest": metric_digest,
        "metricDigestVersion": METRIC_DIGEST_VERSION,
        "agent2PlanRef": agent2_plan_ref,
        "activeActionContract": active_contract,
        "activeActionContractVersion": ACTIVE_ACTION_CONTRACT_VERSION,
        "discardedCrossFamilyFields": discarded,
        "snapshotVersion": TASK_DETAIL_SNAPSHOT_VERSION,
        "snapshotSource": "task_status_write_projection",
        "detailDisplayContract": {
            "version": TASK_DETAIL_SNAPSHOT_VERSION,
            "readMode": "materialized_snapshot",
            "pipelineScan": False,
            "agentRecompute": False,
            "sopRecompute": False,
            "authorizationPreserved": True,
            "operationPlanPreserved": True,
            "agent2ProofPreserved": True,
            "singleActionOnly": True,
            "canonicalField": "activeActionContract",
            "crossFamilyPlansAllowed": False,
            "rule": "任务详情GET只读取入池快照，并以activeActionContract作为唯一动作合同。",
        },
    }


def upsert_task_detail_snapshot_in_conn(conn: Any, task: Dict[str, Any]) -> Dict[str, Any]:
    _ensure_table(conn)
    snapshot = build_task_detail_snapshot(task)
    task_id = snapshot.get("taskId")
    if not task_id:
        return {"version": TASK_DETAIL_SNAPSHOT_VERSION, "stored": False, "reason": "missing_task_id"}
    updated_at = task.get("updatedAt") or task.get("updated_at") or datetime.now().isoformat()
    conn.execute("""
        INSERT INTO task_detail_snapshots(task_id,data_version,snapshot_json,source_version,updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(task_id) DO UPDATE SET data_version=excluded.data_version,snapshot_json=excluded.snapshot_json,source_version=excluded.source_version,updated_at=excluded.updated_at
    """, (task_id, snapshot.get("dataVersion"), dumps(snapshot), TASK_DETAIL_SNAPSHOT_VERSION, updated_at))
    return {"version": TASK_DETAIL_SNAPSHOT_VERSION, "stored": True, "taskId": task_id}


def read_task_detail_snapshot(task_id: str, data_version: str | None = None) -> Dict[str, Any]:
    init_db()
    with connect() as conn:
        _ensure_table(conn)
        params: List[Any] = [task_id]
        where = "WHERE task_id=?"
        if data_version:
            where += " AND data_version=?"
            params.append(data_version)
        row = conn.execute(f"SELECT snapshot_json,data_version,source_version,updated_at FROM task_detail_snapshots {where} LIMIT 1", tuple(params)).fetchone()
        if row and str(row["source_version"] or "") == TASK_DETAIL_SNAPSHOT_VERSION:
            snapshot = _load(row["snapshot_json"])
            snapshot.update(ready=True, snapshotHit=True, snapshotVersion=row["source_version"], snapshotUpdatedAt=row["updated_at"], snapshotSource="task_detail_snapshots")
            return snapshot
        status_row = conn.execute("SELECT task_id,status,workflow_status,payload,updated_at FROM task_status WHERE task_id=? LIMIT 1", (task_id,)).fetchone()
        if not status_row:
            if row:
                snapshot = _load(row["snapshot_json"])
                snapshot.update(ready=True, snapshotHit=True, snapshotVersion=row["source_version"], snapshotUpdatedAt=row["updated_at"], snapshotSource="legacy_task_detail_snapshot")
                return snapshot
            return {"version": TASK_DETAIL_SNAPSHOT_VERSION, "ready": False, "taskId": task_id, "reason": "task_detail_snapshot_not_found", "snapshotHit": False, "pipelineScan": False}
        task = _load(status_row["payload"])
        task.update(id=task_id, taskId=task_id, task_id=task_id, status=status_row["status"] or task.get("status"), workflowStatus=status_row["workflow_status"] or task.get("workflowStatus"), updatedAt=status_row["updated_at"] or task.get("updatedAt"))
        snapshot = build_task_detail_snapshot(task)
        upsert_task_detail_snapshot_in_conn(conn, task)
        conn.commit()
        snapshot.update(snapshotHit=False, snapshotSource="task_status_read_through_v21_7_7", snapshotUpdatedAt=status_row["updated_at"])
        return snapshot


def backfill_task_detail_snapshots(data_version: str | None = None, *, limit: int = 300) -> Dict[str, Any]:
    init_db()
    with connect() as conn:
        _ensure_table(conn)
        params: List[Any] = []
        where = ""
        if data_version:
            where = "WHERE workflow_run_id=?"
            params.append(data_version)
        rows = conn.execute(f"SELECT task_id,status,workflow_status,payload,updated_at FROM task_status {where} ORDER BY updated_at DESC LIMIT ?", (*params, int(limit))).fetchall()
        stored = skipped = 0
        for row in rows:
            task = _load(row["payload"])
            task.update(id=row["task_id"], taskId=row["task_id"], task_id=row["task_id"], status=row["status"] or task.get("status"), workflowStatus=row["workflow_status"] or task.get("workflowStatus"), updatedAt=row["updated_at"] or task.get("updatedAt"))
            result = upsert_task_detail_snapshot_in_conn(conn, task)
            stored += 1 if result.get("stored") else 0
            skipped += 0 if result.get("stored") else 1
        conn.commit()
    return {"version": TASK_DETAIL_SNAPSHOT_VERSION, "candidateCount": len(rows), "stored": stored, "skipped": skipped, "hotPath": False}
