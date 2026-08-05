"""V21.4 manager authorization decision with projection synchronization."""

from __future__ import annotations

from typing import Any, Dict

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.action_authority_v21_service import decide_task_authorization as _decide
from src.services.action_plan_ir_v214_service import authority_parameters_from_plan_ir
from src.services.task_detail_snapshot_v2024_service import upsert_task_detail_snapshot_in_conn

APPROVED_ACTIONS = {"approve_as_is", "approve_modified"}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float | None:
    if value in {None, "", "—", "未识别"}: return None
    try: return float(value)
    except (TypeError, ValueError): return None


def _apply_operation_approval(operation_plan: Dict[str, Any], body: Dict[str, Any]) -> tuple[Dict[str, Any], list[str]]:
    plan = dict(operation_plan); operations = [dict(x) for x in plan.get("operations", []) if isinstance(x, dict)]; errors: list[str] = []
    approved_amount = _float(body.get("approvedAdjustmentAmount")); approved_roas = _float(body.get("approvedTargetROAS"))
    if approved_amount is not None:
        budgets = [x for x in operations if x.get("operationType") == "budget_update"]
        if len(budgets) != 1:
            errors.append("approvedAdjustmentAmount_requires_one_budget_operation")
        else:
            op = budgets[0]; direction = str(op.get("direction") or ""); current = _float(_dict(op.get("currentValue")).get("budget"))
            if direction not in {"increase", "decrease"} or current is None:
                errors.append("budget_operation_requires_explicit_direction_and_current_budget")
            else:
                amount = abs(approved_amount); target = max(0.0, current + (amount if direction == "increase" else -amount)); op["adjustmentAmount"] = amount; op["targetValue"] = {**_dict(op.get("targetValue")), "budget": target}; op["approvalSource"] = "manager_approved_adjustment_amount"
    if approved_roas is not None:
        roas_ops = [x for x in operations if x.get("operationType") == "target_roas_update"]
        if len(roas_ops) != 1:
            errors.append("approvedTargetROAS_requires_one_target_roas_operation")
        else:
            op = roas_ops[0]; current = _float(_dict(op.get("currentValue")).get("roas")); op["targetValue"] = {**_dict(op.get("targetValue")), "roas": approved_roas}; op["direction"] = "increase" if current is not None and approved_roas > current else "decrease" if current is not None and approved_roas < current else "keep"; op["approvalSource"] = "manager_approved_target_roas"
    plan["operations"] = operations; validation = dict(_dict(plan.get("validation"))); validation["passed"] = not errors; validation["missing"] = errors; validation["genericAdjustmentRateUsedAsBudget"] = False; validation["familyNameUsedAsDirection"] = False; plan["validation"] = validation
    return plan, errors


def _normalize_approved_task(task: Dict[str, Any], body: Dict[str, Any]) -> Dict[str, Any]:
    action = str(body.get("decision") or "")
    if action not in APPROVED_ACTIONS: return task
    authorization = dict(_dict(task.get("authorizationDecision") or task.get("actionAuthorization"))); plan = dict(_dict(task.get("taskPlan"))); operation_plan = dict(_dict(task.get("operationPlan") or plan.get("operationPlan") or _dict(task.get("agent2ActionPlan")).get("operationPlan")))
    operation_plan, errors = _apply_operation_approval(operation_plan, body)
    if errors:
        task.update({"status": "权限参数缺失", "workflowStatus": "权限参数缺失", "displayStatus": "权限参数缺失", "decision": "authorization_data_missing"}); authorization.update({"decision": "authorization_data_missing", "approvalRequired": False, "reason": "主管修改缺少唯一操作对象或明确方向，禁止按动作族猜测。", "missing": errors, "operationPlan": operation_plan}); task["authorizationDecision"] = authorization; task["actionAuthorization"] = authorization; task["operationPlan"] = operation_plan; plan["operationPlan"] = operation_plan; task["taskPlan"] = plan; return task
    plan["operationPlan"] = operation_plan; params = authority_parameters_from_plan_ir(plan, plan.get("selectedActionFamily")); authorization.update({"parameters": params, "operationPlan": operation_plan, "approvedAdjustmentAmount": _float(body.get("approvedAdjustmentAmount")), "approvedTargetROAS": _float(body.get("approvedTargetROAS")), "decision": "auto_execute", "approvalRequired": False, "reason": "主管已基于明确操作对象、方向与目标值完成授权。"})
    plan.update({"approvalRequired": False, "needManagerReview": False, "authorizationDecision": authorization, "actionAuthorization": authorization})
    task.update({"decision": "create_task_snapshot", "authorizationDecision": authorization, "actionAuthorization": authorization, "operationPlan": operation_plan, "taskPlan": plan, "taskLayer": "operator_execution", "status": "处理中", "workflowStatus": "处理中", "displayStatus": "处理中", "visibleTaskActions": [{"action": "submit", "label": "提交", "primary": True}, {"action": "detail", "label": "详情"}], "availableActions": [{"action": "submit", "label": "提交", "primary": True}, {"action": "detail", "label": "详情"}]})
    return task


def _sync_usage(conn: Any, task_id: str, task: Dict[str, Any]) -> None:
    authorization = _dict(task.get("authorizationDecision") or task.get("actionAuthorization")); parameters = _dict(authorization.get("parameters")); amount = _float(parameters.get("adjustmentAmount"))
    if authorization.get("decision") != "auto_execute" or amount is None: return
    conn.execute("UPDATE action_authority_usage SET adjustment_amount=?,current_budget=?,target_budget=?,current_roas=?,target_roas=? WHERE task_id=? AND status IN ('authorized','executed')", (abs(amount), parameters.get("currentBudget"), parameters.get("targetBudget"), parameters.get("currentTargetRoas"), parameters.get("targetRoas"), task_id))


def _sync_task_status(conn: Any, task_id: str, task: Dict[str, Any]) -> None:
    conn.execute("UPDATE task_status SET approval_status=?,status=?,workflow_status=?,assignee_id=?,auto_execution_allowed=?,payload=?,updated_at=datetime('now') WHERE task_id=?", ("approved" if task.get("decision") == "create_task_snapshot" else "blocked" if task.get("decision") == "authorization_data_missing" else "rejected" if task.get("status") == "已拒绝" else "regenerate" if task.get("status") == "待重新生成" else "pending", task.get("status"), task.get("workflowStatus"), task.get("assigneeId"), 1 if task.get("decision") == "create_task_snapshot" else 0, dumps(task), task_id))


def _sync_pool_entry(conn: Any, task_id: str, task: Dict[str, Any]) -> bool:
    try: rows = conn.execute("SELECT pool_entry_id,payload FROM task_pool_entries WHERE task_id=?", (task_id,)).fetchall()
    except Exception: return False
    for row in rows:
        payload = loads(row["payload"]) if row["payload"] else {}; payload = payload if isinstance(payload, dict) else {}; payload["task"] = task; payload["authorizationDecision"] = task.get("authorizationDecision"); payload["operationPlan"] = task.get("operationPlan")
        conn.execute("UPDATE task_pool_entries SET decision=?,task_layer=?,assignee_id=?,reason=?,payload=?,updated_at=datetime('now') WHERE pool_entry_id=?", (task.get("decision"), task.get("taskLayer"), task.get("assigneeId"), _dict(task.get("authorizationDecision")).get("reason"), dumps(payload), row["pool_entry_id"]))
    return True


def decide_task_authorization_v21(task_id: str, body: Dict[str, Any], *, actor_user_id: str | None) -> Dict[str, Any]:
    result = _decide(task_id, body, actor_user_id=actor_user_id); task = result.get("task") if isinstance(result.get("task"), dict) else {}
    if not task: return result
    task = _normalize_approved_task(task, body); result["task"] = task; pool_synced = detail_synced = False
    with connect() as conn:
        _sync_task_status(conn, task_id, task); _sync_usage(conn, task_id, task); pool_synced = _sync_pool_entry(conn, task_id, task)
        try: upsert_task_detail_snapshot_in_conn(conn, task); detail_synced = True
        except Exception as exc: result["detailProjectionWarning"] = str(exc)
        conn.commit()
    result["projectionSync"] = {"taskStatus": True, "taskPoolEntry": pool_synced, "taskDetailSnapshot": detail_synced, "authorityUsage": True}; return result
