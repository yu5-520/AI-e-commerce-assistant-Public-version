"""V12.9 task lifecycle orchestrator.

The state machine is the write entrance; this orchestrator attaches lifecycle
snapshots, recap cycles and RAG candidate status to the same task_id.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

from src.services import module_task_service
from src.services.rag_feedback_loop_service import RAG_FEEDBACK_LOOP_VERSION, build_rag_candidate_from_recap
from src.services.task_recap_scheduler_service import RECAP_SCHEDULER_VERSION, complete_recap_cycle, list_recap_cycles_for_task, schedule_recap_cycles

TASK_LIFECYCLE_VERSION = "12.9.0"

STAGE_BY_STATUS = {
    "待拆分": "generated",
    "待接收": "generated",
    "待确认": "generated",
    "已派发": "generated",
    "处理中": "accepted",
    "待复核": "evidence_submitted",
    "已提交": "evidence_submitted",
    "已退回": "returned",
    "已完成": "recap_scheduled",
    "等待自动复盘": "recap_scheduled",
    "已通过": "recap_scheduled",
    "已写入复盘": "recap_scheduled",
    "已归档": "archived",
}

STAGE_LABELS = {
    "generated": "生成任务",
    "accepted": "接收任务",
    "evidence_submitted": "提交处理材料",
    "manager_reviewed": "主管复核",
    "recap_scheduled": "生成自动复盘周期",
    "recap_completed": "复盘完成",
    "rag_candidate_created": "进入RAG候选",
    "rag_approved": "RAG增强任务生成",
    "returned": "退回补充",
    "archived": "归档",
}


def lifecycle_stage(task: Dict[str, Any]) -> str:
    lifecycle = task.get("taskLifecycle") or {}
    if lifecycle.get("stage") in STAGE_LABELS:
        return lifecycle["stage"]
    if task.get("lifecycleStage") in STAGE_LABELS:
        return task["lifecycleStage"]
    if task.get("workflowStatus") == "等待自动复盘":
        return "recap_scheduled"
    return STAGE_BY_STATUS.get(task.get("status"), "generated")


def _next_expected(stage: str) -> str:
    return {
        "generated": "运营接收任务",
        "accepted": "运营提交处理材料",
        "evidence_submitted": "主管复核材料或系统判断自动复盘",
        "manager_reviewed": "系统生成复盘周期",
        "recap_scheduled": "系统按后续报表完成自动复盘",
        "recap_completed": "生成RAG候选并等待审核",
        "rag_candidate_created": "审核通过后参与后续任务生成",
        "returned": "运营补充材料后再次提交",
        "archived": "生命周期结束",
    }.get(stage, "继续处理")


def lifecycle_snapshot(task: Dict[str, Any]) -> Dict[str, Any]:
    stage = lifecycle_stage(task)
    cycles = list_recap_cycles_for_task(task.get("id")) if task.get("id") else []
    if any(item.get("status") == "completed" for item in cycles) and stage == "recap_scheduled":
        stage = "recap_completed"
    if task.get("ragCandidate") and stage in {"recap_completed", "recap_scheduled", "manager_reviewed"}:
        stage = "rag_candidate_created"
    events = [event for event in module_task_service.TASK_EVENTS if event.get("taskId") == task.get("id")][:20]
    return {
        "version": TASK_LIFECYCLE_VERSION,
        "stage": stage,
        "stageLabel": STAGE_LABELS.get(stage, stage),
        "taskId": task.get("id"),
        "status": task.get("status"),
        "workflowStatus": task.get("workflowStatus"),
        "recapSchedulerVersion": RECAP_SCHEDULER_VERSION,
        "ragFeedbackLoopVersion": RAG_FEEDBACK_LOOP_VERSION,
        "recapCycles": cycles,
        "ragCandidate": task.get("ragCandidate"),
        "nextExpected": _next_expected(stage),
        "timeline": events,
        "rule": "同一个task_id贯穿生成、接收、提交材料、复核、自动复盘、RAG候选和后续增强。",
    }


def attach_lifecycle(task_id: str, *, stage: str | None = None, event: str = "lifecycle_updated", payload: Dict[str, Any] | None = None, actor_user_id: str | None = None) -> Dict[str, Any] | None:
    task = module_task_service.find_task(task_id)
    if not task:
        return None
    next_stage = stage or lifecycle_stage(task)
    snapshot = lifecycle_snapshot({**task, "taskLifecycle": {"stage": next_stage}})
    snapshot["lastEvent"] = event
    snapshot["actorUserId"] = actor_user_id
    snapshot["payload"] = payload or {}
    return module_task_service.update_task(task_id, {"taskLifecycle": snapshot, "lifecycleStage": next_stage, "lifecycleVersion": TASK_LIFECYCLE_VERSION}, log_type="任务生命周期", action=event, result=STAGE_LABELS.get(next_stage, next_stage))


def handle_task_generated(task: Dict[str, Any]) -> Dict[str, Any] | None:
    return attach_lifecycle(task.get("id"), stage="generated", event="task_generated") if task and task.get("id") else None


def handle_task_accepted(task_id: str, *, actor_user_id: str | None = None) -> Dict[str, Any] | None:
    return attach_lifecycle(task_id, stage="accepted", event="task_accepted", actor_user_id=actor_user_id)


def handle_evidence_submitted(task_id: str, *, evidence: Dict[str, Any] | None = None, actor_user_id: str | None = None) -> Dict[str, Any] | None:
    return attach_lifecycle(task_id, stage="evidence_submitted", event="evidence_submitted", payload={"evidence": evidence or {}}, actor_user_id=actor_user_id)


def handle_manager_reviewed(task_id: str, *, approved: bool = True, review: Dict[str, Any] | None = None, actor_user_id: str | None = None) -> Dict[str, Any] | None:
    task = module_task_service.find_task(task_id)
    if not task:
        return None
    if not approved:
        return attach_lifecycle(task_id, stage="returned", event="manager_returned", payload={"review": review or {}}, actor_user_id=actor_user_id)
    recap = schedule_recap_cycles(task, trigger="manager_reviewed", actor_user_id=actor_user_id)
    return attach_lifecycle(task_id, stage="recap_scheduled", event="manager_reviewed_recap_scheduled", payload={"review": review or {}, "recap": recap}, actor_user_id=actor_user_id)


def complete_recap_and_create_rag_candidate(task_id: str, *, before_metrics: Dict[str, Any] | None = None, after_metrics: Dict[str, Any] | None = None, reviewer_id: str | None = None, conclusion: str | None = None) -> Dict[str, Any] | None:
    task = module_task_service.find_task(task_id)
    if not task:
        return None
    recap_result = complete_recap_cycle(task, before_metrics=before_metrics or {}, after_metrics=after_metrics or {}, reviewer_id=reviewer_id, conclusion=conclusion)
    rag = build_rag_candidate_from_recap(task_id, recap_result=recap_result, user_id=reviewer_id)
    stage = "rag_candidate_created" if rag else "recap_completed"
    updated = module_task_service.update_task(task_id, {"autoRecapResult": recap_result, "ragCandidate": rag, "recapCompletedAt": recap_result.get("completedAt"), "lifecycleStage": stage}, log_type="自动复盘", action="复盘完成", result=recap_result.get("conclusion") or "复盘完成。")
    attach_lifecycle(task_id, stage=stage, event="recap_completed_rag_candidate_created", payload={"recap": recap_result, "rag": rag}, actor_user_id=reviewer_id)
    return updated


def lifecycle_summary(limit: int = 50) -> Dict[str, Any]:
    tasks = module_task_service.list_tasks(active_only=False)[:limit]
    snapshots = [lifecycle_snapshot(task) for task in tasks]
    counts: Dict[str, int] = {}
    for item in snapshots:
        counts[item["stage"]] = counts.get(item["stage"], 0) + 1
    return {"version": TASK_LIFECYCLE_VERSION, "counts": counts, "items": snapshots[:limit]}


def apply_lifecycle_to_task_projection(task: Dict[str, Any]) -> Dict[str, Any]:
    item = deepcopy(task)
    snapshot = lifecycle_snapshot(task)
    if task.get("id") and not task.get("taskLifecycle"):
        try:
            module_task_service.update_task(task["id"], {"taskLifecycle": snapshot, "lifecycleStage": snapshot.get("stage"), "lifecycleVersion": TASK_LIFECYCLE_VERSION})
        except Exception:
            pass
    item["taskLifecycle"] = snapshot
    item["lifecycleStage"] = snapshot.get("stage")
    item["lifecycleVersion"] = TASK_LIFECYCLE_VERSION
    return item
