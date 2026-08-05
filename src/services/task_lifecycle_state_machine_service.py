"""V12.11.1 unified task lifecycle state machine.

This service is the only write entrance for visible task lifecycle transitions.
V12.11.1 keeps auto-accept and repository hydration, and also routes manager
assign/split through the same state machine instead of direct update_task calls.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

from src.repositories.task_repository import TaskRepository
from src.services import module_task_service
from src.services.account_service import current_user
from src.services.task_lifecycle_orchestrator_service import TASK_LIFECYCLE_VERSION as ORCHESTRATOR_VERSION
from src.services.task_lifecycle_orchestrator_service import attach_lifecycle, complete_recap_and_create_rag_candidate, handle_evidence_submitted, handle_manager_reviewed
from src.services.task_state_machine_service import mirror_all

TASK_LIFECYCLE_STATE_MACHINE_VERSION = "12.11.1"
DONE_STATUS = {"已完成", "已拒绝", "已确认", "已归档", "已通过", "已写入复盘"}
WAITING_ACCEPT = {"待接收", "待确认", "已派发", "待处理", "待拆分"}
PROCESSING = {"处理中", "已退回"}
REVIEWING = {"已提交", "待复核", "待审批", "待老板确认"}
WAITING_RECAP = {"等待自动复盘", "复盘待生成"}

EVENT_BY_ACTION = {
    "auto_accept": "system_auto_accepted",
    "accept": "operator_accepted",
    "assign": "manager_assigned",
    "split": "manager_assigned",
    "submit": "operator_submitted",
    "review_approve": "manager_approved",
    "review_return": "manager_returned",
    "complete": "task_completed",
    "recap_complete": "task_written_to_recap",
}


def now_iso() -> str:
    return datetime.now().isoformat()


def _task_id(task: Dict[str, Any] | None) -> str | None:
    return (task or {}).get("id") or (task or {}).get("taskId")


def _hydrate_memory_task(task: Dict[str, Any] | None) -> Dict[str, Any] | None:
    if not task or not _task_id(task):
        return task
    task_id = str(_task_id(task))
    normalized = dict(task)
    normalized["id"] = task_id
    existing = module_task_service.find_task(task_id)
    if existing:
        existing.update(normalized)
        return existing
    module_task_service.TASKS.insert(0, normalized)
    return normalized


def _repository_task(task_id: str, ctx: Any | None = None) -> Dict[str, Any] | None:
    if not ctx:
        return None
    try:
        return TaskRepository(ctx).get(task_id)
    except Exception:
        return None


def _repository_linked_task(task_id: str, ctx: Any | None = None) -> Dict[str, Any] | None:
    if not ctx:
        return None
    try:
        for task in TaskRepository(ctx).list(active_only=False, limit=1000):
            linked = set(task.get("clusterTaskIds") or []) | set(task.get("childTaskIds") or [])
            linked |= {str(item.get("taskId") or item.get("id") or item.get("productId")) for item in (task.get("affectedProducts") or []) if isinstance(item, dict)}
            if task_id in linked:
                return task
    except Exception:
        return None
    return None


def _find_primary_task(task_id: str | None, ctx: Any | None = None) -> Tuple[Dict[str, Any] | None, Dict[str, Any]]:
    if not task_id:
        return None, {"requestedTaskId": task_id, "resolvedBy": "missing_id"}
    exact = module_task_service.find_task(task_id)
    if exact:
        if exact.get("queueType") == "merged_duplicate" and exact.get("clusterParentTaskId"):
            parent = module_task_service.find_task(exact.get("clusterParentTaskId")) or _repository_task(str(exact.get("clusterParentTaskId")), ctx)
            if parent:
                parent = _hydrate_memory_task(parent)
                return parent, {"requestedTaskId": task_id, "resolvedTaskId": parent.get("id"), "resolvedBy": "merged_duplicate_parent"}
        return exact, {"requestedTaskId": task_id, "resolvedTaskId": exact.get("id"), "resolvedBy": "memory_exact"}
    for task in module_task_service.TASKS:
        linked = set(task.get("clusterTaskIds") or []) | set(task.get("childTaskIds") or [])
        linked |= {str(item.get("taskId") or item.get("id") or item.get("productId")) for item in (task.get("affectedProducts") or []) if isinstance(item, dict)}
        if task_id in linked:
            return task, {"requestedTaskId": task_id, "resolvedTaskId": task.get("id"), "resolvedBy": "memory_cluster_or_affected_child"}
    repo_exact = _repository_task(task_id, ctx)
    if repo_exact:
        repo_exact = _hydrate_memory_task(repo_exact)
        return repo_exact, {"requestedTaskId": task_id, "resolvedTaskId": repo_exact.get("id"), "resolvedBy": "repository_exact_hydrated"}
    repo_linked = _repository_linked_task(task_id, ctx)
    if repo_linked:
        repo_linked = _hydrate_memory_task(repo_linked)
        return repo_linked, {"requestedTaskId": task_id, "resolvedTaskId": repo_linked.get("id"), "resolvedBy": "repository_cluster_or_affected_child_hydrated"}
    return None, {"requestedTaskId": task_id, "resolvedBy": "not_found"}


def _is_manager_required(task: Dict[str, Any]) -> bool:
    gate = task.get("actionAuthorization") or task.get("v1282ActionGate") or task.get("v127ActionGate") or {}
    decision = gate.get("decision")
    return bool(decision in {"manager_approval_required", "owner_approval_required"} or task.get("taskLayer") == "manager_approval")


def should_auto_accept(task: Dict[str, Any]) -> bool:
    if not task or task.get("displayState") == "backend_only" or task.get("queueType") == "merged_duplicate":
        return False
    if _is_manager_required(task):
        return False
    status = str(task.get("status") or "")
    if status not in WAITING_ACCEPT:
        return False
    if task.get("taskLayer") not in {None, "operator_execution"}:
        return False
    gate = task.get("actionAuthorization") or task.get("v1282ActionGate") or task.get("v127ActionGate") or {}
    decision = gate.get("decision") or "auto_execute"
    return decision == "auto_execute"


def _status_patch(task: Dict[str, Any], action: str, actor_user_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    note = payload.get("note") or payload.get("summary") or ""
    if action in {"accept", "auto_accept"}:
        patch = {"status": "处理中", "workflowStatus": "处理中", "displayStatus": "处理中", "lifecycleStage": "accepted", "lifecycleVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION}
        if action == "auto_accept":
            patch.update({"autoAcceptedBy": "system", "autoAcceptedAt": now_iso(), "autoAcceptReason": note or "运营权限内任务生成后自动接收。"})
        else:
            patch.update({"acceptedById": actor_user_id, "acceptedAt": now_iso()})
        return patch
    if action in {"assign", "split"}:
        assignee_id = payload.get("operator_id") or payload.get("operatorId") or payload.get("assignee_id") or payload.get("assigneeId") or task.get("assigneeId")
        reviewer_id = payload.get("reviewer_id") or payload.get("reviewerId") or task.get("reviewerId")
        visible_user_ids = list(dict.fromkeys([value for value in [*list(task.get("visibleUserIds") or []), assignee_id, reviewer_id] if value]))
        return {
            "status": "待接收",
            "workflowStatus": "已派发",
            "displayStatus": "待接收",
            "assigneeId": assignee_id,
            "reviewerId": reviewer_id,
            "assignedById": actor_user_id,
            "assignedAt": now_iso(),
            "visibleUserIds": visible_user_ids,
            "lifecycleStage": "assigned",
            "lifecycleVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION,
        }
    if action == "submit":
        if _is_manager_required(task):
            return {"status": "待复核", "workflowStatus": "待复核", "displayStatus": "待复核", "submissionNote": note or "运营已提交处理材料。", "submittedById": actor_user_id, "submittedAt": now_iso(), "lifecycleStage": "evidence_submitted", "lifecycleVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION}
        return {"status": "已完成", "workflowStatus": "等待自动复盘", "displayStatus": "等待自动复盘", "submissionNote": note or "运营已提交处理材料，系统进入自动复盘等待。", "submittedById": actor_user_id, "submittedAt": now_iso(), "lifecycleStage": "recap_scheduled", "lifecycleVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION}
    if action == "review_approve":
        return {"status": "已完成", "workflowStatus": "等待自动复盘", "displayStatus": "等待自动复盘", "reviewResult": "通过", "reviewNote": note or "复核通过，系统生成自动复盘周期。", "reviewerId": actor_user_id, "reviewedAt": now_iso(), "lifecycleStage": "recap_scheduled", "lifecycleVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION}
    if action == "review_return":
        return {"status": "已退回", "workflowStatus": "已退回", "displayStatus": "已退回", "reviewResult": "退回", "reviewNote": note or "复核退回，运营补充材料后再次提交。", "reviewerId": actor_user_id, "reviewedAt": now_iso(), "lifecycleStage": "returned", "lifecycleVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION}
    if action == "complete":
        return {"status": "已完成", "workflowStatus": "等待自动复盘", "displayStatus": "等待自动复盘", "completedById": actor_user_id, "completedAt": now_iso(), "lifecycleStage": "recap_scheduled", "lifecycleVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION}
    return {}


def _transition_message(action: str, task: Dict[str, Any], payload: Dict[str, Any]) -> str:
    note = payload.get("note") or ""
    return {
        "auto_accept": note or "系统已自动接收运营权限内任务，进入处理中。",
        "accept": note or "运营已接收任务，进入处理中。",
        "assign": note or "总管已派发任务，等待运营自动接收或手动接收。",
        "split": note or "总管已拆分并派发任务，等待运营自动接收或手动接收。",
        "submit": note or ("运营已提交处理材料，等待总管复核。" if _is_manager_required(task) else "运营已提交处理材料，系统进入自动复盘等待。"),
        "review_approve": note or "总管复核通过，系统生成自动复盘周期。",
        "review_return": note or "总管复核退回，运营补充材料后再次提交。",
        "complete": note or "任务已完成，系统进入自动复盘等待。",
        "recap_complete": note or "系统复盘完成，生成RAG候选。",
    }.get(action, note or "任务生命周期已更新。")


def _mirror_runtime() -> Dict[str, Any]:
    try:
        return mirror_all(module_task_service.TASKS, module_task_service.TASK_EVENTS, module_task_service.LOGS)
    except Exception as exc:
        return {"ok": False, "mirrorError": str(exc)}


def _persist_primary_task(task: Dict[str, Any] | None, ctx: Any | None = None) -> Dict[str, Any] | None:
    if not task or not ctx:
        return task
    try:
        return TaskRepository(ctx).upsert(task)
    except Exception:
        return task


def _apply_orchestrator(task_id: str, action: str, actor_user_id: str, payload: Dict[str, Any], ctx: Any | None = None) -> Dict[str, Any] | None:
    if action in {"accept", "auto_accept"}:
        return attach_lifecycle(task_id, stage="accepted", event=EVENT_BY_ACTION.get(action, "operator_accepted"), payload=payload, actor_user_id=actor_user_id)
    if action in {"assign", "split"}:
        return attach_lifecycle(task_id, stage="assigned", event="manager_assigned", payload=payload, actor_user_id=actor_user_id)
    if action == "submit":
        task, _ = _find_primary_task(task_id, ctx)
        if task and _is_manager_required(task):
            return handle_evidence_submitted(task_id, evidence={"summary": payload.get("note") or "运营已提交处理材料。"}, actor_user_id=actor_user_id)
        return handle_manager_reviewed(task_id, approved=True, review={"comment": payload.get("note") or "运营提交后自动进入复盘周期。"}, actor_user_id=actor_user_id)
    if action == "review_approve":
        return handle_manager_reviewed(task_id, approved=True, review={"comment": payload.get("note")}, actor_user_id=actor_user_id)
    if action == "review_return":
        return handle_manager_reviewed(task_id, approved=False, review={"comment": payload.get("note")}, actor_user_id=actor_user_id)
    if action == "complete":
        return attach_lifecycle(task_id, stage="recap_scheduled", event="task_completed_recap_scheduled", payload=payload, actor_user_id=actor_user_id)
    return None


def project_lifecycle_task(task: Dict[str, Any], viewer_id: str | None = None) -> Dict[str, Any]:
    from src.services.task_lifecycle_orchestrator_service import apply_lifecycle_to_task_projection
    from src.services.v105_cross_account_flow_service import apply_v105_cross_account_flow, projected_task_for_role
    from src.services.v106_task_action_simplifier import apply_v106_task_actions

    user = current_user(viewer_id) if viewer_id else None
    role_task = projected_task_for_role(apply_v105_cross_account_flow(deepcopy(task)), (user or {}).get("roleId"))
    role_task = apply_lifecycle_to_task_projection(role_task)
    return apply_v106_task_actions(role_task)


def get_lifecycle_task_projection(task_id: str, viewer_id: str | None = None, ctx: Any | None = None) -> Dict[str, Any] | None:
    task, _ = _find_primary_task(task_id, ctx)
    return project_lifecycle_task(task, viewer_id) if task else None


def _idempotent_result(task: Dict[str, Any], resolution: Dict[str, Any], action: str, actor_user_id: str, payload: Dict[str, Any], ctx: Any | None = None) -> Dict[str, Any]:
    task = _hydrate_memory_task(task) or task
    _persist_primary_task(task, ctx)
    mirror_result = _mirror_runtime()
    projected = project_lifecycle_task(task, actor_user_id)
    return {"ok": True, "idempotent": True, "version": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "orchestratorVersion": ORCHESTRATOR_VERSION, "action": action, "eventType": "lifecycle_noop", "message": f"任务已处于{task.get('status')}，接收动作保持幂等。", "resolution": resolution, "task": projected, "mirror": mirror_result}


def _is_idempotent_accept(task: Dict[str, Any], action: str) -> bool:
    if action not in {"accept", "auto_accept"}:
        return False
    status = str(task.get("status") or "")
    workflow = str(task.get("workflowStatus") or "")
    return status in PROCESSING or status in REVIEWING or status in DONE_STATUS or workflow in WAITING_RECAP or task.get("lifecycleStage") in {"accepted", "evidence_submitted", "recap_scheduled", "rag_candidate_created"}


def transition_lifecycle_task(task_id: str, action: str, *, actor_user_id: str, payload: Dict[str, Any] | None = None, ctx: Any | None = None) -> Dict[str, Any]:
    payload = payload or {}
    action = {"manager_assign": "assign", "manager_assigned": "assign"}.get(action, action)
    task, resolution = _find_primary_task(task_id, ctx)
    if not task:
        return {"ok": False, "version": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "action": action, "message": "任务不存在或聚合主任务未找到。", "resolution": resolution}
    if task.get("displayState") == "backend_only" or task.get("queueType") == "merged_duplicate":
        return {"ok": False, "version": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "action": action, "message": "该任务为后端合并子任务，不能直接流转。", "resolution": resolution}
    if _is_idempotent_accept(task, action):
        return _idempotent_result(task, resolution, action, actor_user_id, payload, ctx)
    primary_id = str(task.get("id"))
    before_status = task.get("status")
    before_workflow = task.get("workflowStatus")
    if action == "recap_complete":
        updated = complete_recap_and_create_rag_candidate(primary_id, before_metrics=payload.get("beforeMetrics") or {}, after_metrics=payload.get("afterMetrics") or {}, reviewer_id=actor_user_id, conclusion=payload.get("conclusion") or payload.get("note"))
    else:
        patch = _status_patch(task, action, actor_user_id, payload)
        if not patch:
            return {"ok": False, "version": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "action": action, "message": f"不支持的生命周期动作：{action}", "resolution": resolution}
        message = _transition_message(action, task, payload)
        updated = module_task_service.update_task(primary_id, patch, log_type="任务生命周期", action=EVENT_BY_ACTION.get(action, action), result=message)
        if updated:
            orchestrated = _apply_orchestrator(primary_id, action, actor_user_id, payload, ctx)
            if orchestrated:
                updated = orchestrated
    latest, _ = _find_primary_task(primary_id, ctx)
    latest = latest or updated or task
    latest = _hydrate_memory_task(latest) or latest
    _persist_primary_task(latest, ctx)
    event_type = EVENT_BY_ACTION.get(action, "task_updated")
    event = module_task_service.create_task_event(latest, event_type, actor_user_id=actor_user_id, from_status=before_status, from_workflow=before_workflow, message=_transition_message(action, latest, payload))
    mirror_result = _mirror_runtime()
    projected = project_lifecycle_task(latest, actor_user_id)
    return {"ok": True, "version": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "orchestratorVersion": ORCHESTRATOR_VERSION, "action": action, "eventType": event_type, "message": _transition_message(action, latest, payload), "resolution": resolution, "fromStatus": before_status, "toStatus": latest.get("status"), "fromWorkflowStatus": before_workflow, "toWorkflowStatus": latest.get("workflowStatus"), "task": projected, "event": event, "mirror": mirror_result, "rule": "V12.11.1：接收、派发、提交、复核、复盘必须通过统一生命周期状态机写状态、事件、日志、SQLite镜像和前端投影。"}


def auto_accept_ready_tasks(tasks: Iterable[Dict[str, Any]], *, viewer_id: str | None = None, ctx: Any | None = None) -> Dict[str, Any]:
    accepted: List[Dict[str, Any]] = []
    skipped = 0
    for task in list(tasks or []):
        if should_auto_accept(task):
            result = transition_lifecycle_task(str(task.get("id")), "auto_accept", actor_user_id="system", payload={"note": "运营权限内任务生成后自动接收。"}, ctx=ctx)
            if result.get("ok"):
                accepted.append(result.get("task") or {})
            else:
                skipped += 1
        else:
            skipped += 1
    return {"version": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "autoAcceptedCount": len(accepted), "skippedCount": skipped, "tasks": accepted, "rule": "运营权限内且无需主管/老板审批的任务生成后自动进入处理中，前端直接展示提交按钮。"}


def lifecycle_state_summary(limit: int = 80) -> Dict[str, Any]:
    tasks = module_task_service.list_tasks(active_only=False)[:limit]
    counts: Dict[str, int] = {}
    for task in tasks:
        stage = task.get("lifecycleStage") or (task.get("taskLifecycle") or {}).get("stage") or "generated"
        counts[stage] = counts.get(stage, 0) + 1
    return {"version": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "counts": counts, "taskCount": len(tasks), "eventCount": len(module_task_service.TASK_EVENTS), "rule": "同一个task_id贯穿生成、接收、派发、提交、复核、自动复盘和RAG候选；权限内任务自动接收。"}
