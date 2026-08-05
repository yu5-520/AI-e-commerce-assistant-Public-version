"""Revalidate unfinished ROAS tasks under V21.4 operation authority.

Pending tasks and system-auto-accepted processing tasks are rechecked. Manually
accepted, submitted, reviewed or completed tasks are never changed by migration.
A legacy task without operation Plan IR becomes authority-blocked.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.account_service import assignment_for_store, default_operator
from src.services.action_authority_v214_service import (
    ACTION_AUTHORITY_VERSION,
    AUTHORIZATION_DATA_MISSING,
    AUTO_EXECUTE,
    authorize_decision,
    record_authorized_usage,
)
from src.services.action_plan_ir_v214_service import ROAS_FAMILIES
from src.services.task_detail_snapshot_v2024_service import upsert_task_detail_snapshot_in_conn

PENDING_STATUSES = {
    "待拆分", "待派发", "待审批", "待复核", "待主管审批", "权限参数缺失",
}
SYSTEM_PROCESSING_STATUSES = {"处理中", "已接收"}
TERMINAL_OR_MANUAL_MARKERS = (
    "submittedAt", "reviewedAt", "completedAt", "closedAt",
    "authorizationApprovedAt", "authorizationRejectedAt",
)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _family(payload: Dict[str, Any], plan: Dict[str, Any]) -> str:
    return str(
        payload.get("actionFamily")
        or plan.get("selectedActionFamily")
        or plan.get("actionFamily")
        or ""
    )


def _revalidatable(payload: Dict[str, Any], status: str) -> bool:
    if any(payload.get(key) for key in TERMINAL_OR_MANUAL_MARKERS):
        return False
    if status in PENDING_STATUSES:
        return not any(payload.get(key) for key in ("acceptedAt", "assignedAt"))
    if status in SYSTEM_PROCESSING_STATUSES:
        if payload.get("acceptedAt") or payload.get("assignedAt"):
            return False
        return str(payload.get("autoAcceptedBy") or "").startswith("system")
    return False


def _operator_for_task(payload: Dict[str, Any], plan: Dict[str, Any]) -> str | None:
    explicit = plan.get("assignedOperatorId") or payload.get("assigneeId")
    if explicit:
        return str(explicit)
    product = _dict(payload.get("productIdentity"))
    store_id = payload.get("storeId") or plan.get("storeId") or product.get("storeId")
    assignment = assignment_for_store(str(store_id)) if store_id else None
    assigned = (assignment or {}).get("primaryOperatorId")
    if assigned:
        return str(assigned)
    return str(
        (default_operator(plan.get("riskDomain") or plan.get("taskType")) or {}).get("id")
        or ""
    ) or None


def _update_pool_payload(conn: Any, task_id: str, task: Dict[str, Any]) -> None:
    try:
        rows = conn.execute(
            "SELECT pool_entry_id,payload FROM task_pool_entries WHERE task_id=?",
            (task_id,),
        ).fetchall()
    except Exception:
        return
    for row in rows:
        payload = loads(row["payload"]) if row["payload"] else {}
        payload = payload if isinstance(payload, dict) else {}
        payload["task"] = task
        payload["authorizationDecision"] = task.get("authorizationDecision")
        payload["operationPlan"] = (
            task.get("operationPlan") or _dict(task.get("taskPlan")).get("operationPlan")
        )
        conn.execute(
            """
            UPDATE task_pool_entries
            SET task_layer=?,assignee_id=?,decision=?,reason=?,payload=?,updated_at=datetime('now')
            WHERE pool_entry_id=?
            """,
            (
                task.get("taskLayer"),
                task.get("assigneeId"),
                task.get("decision"),
                _dict(task.get("authorizationDecision")).get("reason"),
                dumps(payload),
                row["pool_entry_id"],
            ),
        )


def recalculate_pending_roas_authority(
    *,
    actor_user_id: str | None = None,
    limit: int = 300,
) -> Dict[str, Any]:
    results: List[Dict[str, Any]] = []
    authorized_tasks: List[Dict[str, Any]] = []
    system_processing_revalidated = 0

    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM task_status ORDER BY COALESCE(updated_at,'') DESC LIMIT ?",
            (max(1, min(1000, int(limit or 300))),),
        ).fetchall()
        for row in rows:
            payload = loads(row["payload"]) if row["payload"] else {}
            if not isinstance(payload, dict):
                continue
            plan = dict(_dict(payload.get("taskPlan")))
            family = _family(payload, plan)
            status = str(
                row["workflow_status"]
                or row["status"]
                or payload.get("status")
                or ""
            )
            if family not in ROAS_FAMILIES or not _revalidatable(payload, status):
                continue
            was_system_processing = status in SYSTEM_PROCESSING_STATUSES
            if was_system_processing:
                system_processing_revalidated += 1

            operator_id = _operator_for_task(payload, plan)
            if operator_id:
                plan["assignedOperatorId"] = operator_id
            operation_plan = _dict(
                payload.get("operationPlan")
                or plan.get("operationPlan")
                or _dict(payload.get("agent2ActionPlan")).get("operationPlan")
            )
            plan["operationPlan"] = operation_plan
            decision = {
                "decisionId": payload.get("decisionId") or payload.get("taskId") or row["task_id"],
                "dataVersion": payload.get("dataVersion") or row["workflow_run_id"],
                "productId": payload.get("productId") or _dict(payload.get("productIdentity")).get("productId"),
                "storeId": payload.get("storeId") or _dict(payload.get("productIdentity")).get("storeId"),
                "actionFamily": family,
                "taskPlan": plan,
                "agent2ActionPlan": payload.get("agent2ActionPlan") or plan.get("agent2ActionPlan"),
                "operationPlan": operation_plan,
            }
            authorization = authorize_decision(decision)
            payload.update(
                {
                    "authorizationDecision": authorization,
                    "actionAuthorization": authorization,
                    "authorizationVersion": ACTION_AUTHORITY_VERSION,
                    "operationPlan": authorization.get("operationPlan") or operation_plan,
                    "authorityRevalidatedFromStatus": status,
                    "authorityRevalidatedBy": actor_user_id or "system_deploy_v21_4",
                }
            )
            plan.update(
                {
                    "authorizationDecision": authorization,
                    "actionAuthorization": authorization,
                    "authorizationVersion": ACTION_AUTHORITY_VERSION,
                    "operationPlan": payload["operationPlan"],
                    "approvalRequired": bool(authorization.get("approvalRequired")),
                    "needManagerReview": bool(authorization.get("approvalRequired")),
                }
            )
            payload["taskPlan"] = plan

            if authorization.get("decision") == AUTO_EXECUTE:
                operator_id = authorization.get("operatorId")
                payload.update(
                    {
                        "decision": "create_task_snapshot",
                        "taskLayer": "operator_execution",
                        "assigneeId": operator_id,
                        "status": "处理中",
                        "workflowStatus": "处理中",
                        "displayStatus": "处理中",
                        "lifecycleStage": "accepted",
                        "autoAcceptedBy": "system_v21_4_reauthorization",
                        "autoAcceptedAt": payload.get("autoAcceptedAt") or row["updated_at"],
                        "autoAcceptReason": authorization.get("reason"),
                        "visibleTaskActions": [
                            {"action": "submit", "label": "提交", "primary": True},
                            {"action": "detail", "label": "详情"},
                        ],
                        "availableActions": [
                            {"action": "submit", "label": "提交", "primary": True},
                            {"action": "detail", "label": "详情"},
                        ],
                    }
                )
                approval_status = "not_required"
                auto_execution = 1
                result_status = "auto_accepted"
                authorized_tasks.append(payload)
            elif authorization.get("decision") == AUTHORIZATION_DATA_MISSING:
                payload.update(
                    {
                        "decision": "authorization_data_missing",
                        "taskLayer": "authorization_blocked",
                        "assigneeId": None,
                        "status": "权限参数缺失",
                        "workflowStatus": "权限参数缺失",
                        "displayStatus": "权限参数缺失",
                        "lifecycleStage": "authorization_blocked",
                        "visibleTaskActions": [
                            {"action": "detail", "label": "详情", "primary": True}
                        ],
                        "availableActions": [
                            {"action": "detail", "label": "详情", "primary": True}
                        ],
                    }
                )
                approval_status = "blocked"
                auto_execution = 0
                result_status = AUTHORIZATION_DATA_MISSING
            else:
                payload.update(
                    {
                        "decision": "manager_review_required",
                        "taskLayer": "manager_dispatch",
                        "assigneeId": None,
                        "status": "待审批",
                        "workflowStatus": "待审批",
                        "displayStatus": "待审批",
                        "visibleTaskActions": [
                            {"action": "review", "label": "复核", "primary": True},
                            {"action": "detail", "label": "详情"},
                        ],
                        "availableActions": [
                            {"action": "review", "label": "复核", "primary": True},
                            {"action": "detail", "label": "详情"},
                        ],
                    }
                )
                approval_status = "pending"
                auto_execution = 0
                result_status = authorization.get("decision")

            conn.execute(
                """
                UPDATE task_status
                SET approval_status=?,status=?,workflow_status=?,assignee_id=?,
                    auto_execution_allowed=?,payload=?,updated_at=datetime('now')
                WHERE task_id=?
                """,
                (
                    approval_status,
                    payload.get("status"),
                    payload.get("workflowStatus"),
                    payload.get("assigneeId"),
                    auto_execution,
                    dumps(payload),
                    row["task_id"],
                ),
            )
            _update_pool_payload(conn, row["task_id"], payload)
            try:
                upsert_task_detail_snapshot_in_conn(conn, payload)
            except Exception:
                pass
            results.append(
                {
                    "taskId": row["task_id"],
                    "previousStatus": status,
                    "status": result_status,
                    "operatorId": authorization.get("operatorId"),
                    "reason": authorization.get("reason"),
                    "missing": authorization.get("missing") or [],
                    "triggeredReasons": authorization.get("triggeredReasons") or [],
                }
            )
        conn.commit()

    for task in authorized_tasks:
        record_authorized_usage(task)
    return {
        "version": ACTION_AUTHORITY_VERSION,
        "actorUserId": actor_user_id,
        "scanned": len(rows),
        "recalculated": len(results),
        "systemProcessingRevalidated": system_processing_revalidated,
        "autoAccepted": len([x for x in results if x["status"] == "auto_accepted"]),
        "managerApproval": len(
            [x for x in results if x["status"] == "manager_approval_required"]
        ),
        "ownerApproval": len(
            [x for x in results if x["status"] == "owner_approval_required"]
        ),
        "dataMissing": len(
            [x for x in results if x["status"] == AUTHORIZATION_DATA_MISSING]
        ),
        "results": results,
        "rule": (
            "Pending and system-auto-accepted unfinished ROAS tasks are revalidated "
            "from operation Plan IR; missing Plan IR is blocked."
        ),
    }
