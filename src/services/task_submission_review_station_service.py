"""V13.7 Task Submission / Review Station service.

Submission records operator evidence first, then asks the unified lifecycle state
machine to move the task. Review records manager review evidence first, then
moves the task into return or recap scheduling.
"""

from __future__ import annotations

from typing import Any, Dict

from src.services.task_evidence_service import get_task_evidence, review_task_evidence, submit_task_evidence
from src.services.task_lifecycle_state_machine_service import lifecycle_state_summary, transition_lifecycle_task

TASK_SUBMISSION_REVIEW_STATION_VERSION = "13.7.0"


def submit_task(task_id: str, body: Dict[str, Any] | None = None, *, submitter_id: str | None = None) -> Dict[str, Any]:
    body = body or {}
    evidence_task = submit_task_evidence(task_id, body, submitter_id=submitter_id or body.get("submitterId"))
    if not evidence_task:
        return {"version": TASK_SUBMISSION_REVIEW_STATION_VERSION, "ok": False, "stationId": "task_submission_station", "error": "task_not_found", "taskId": task_id}
    transition = transition_lifecycle_task(
        task_id,
        "submit",
        actor_user_id=submitter_id or body.get("submitterId") or evidence_task.get("assigneeId") or "U003",
        payload={"note": body.get("summary") or body.get("note") or "运营提交执行材料。", "stationId": "task_submission_station"},
    )
    return {
        "version": TASK_SUBMISSION_REVIEW_STATION_VERSION,
        "ok": bool(transition.get("ok")),
        "stationId": "task_submission_station",
        "taskId": task_id,
        "evidenceTask": evidence_task,
        "transition": transition,
        "rule": "提交站先保存证据，再通过统一生命周期状态机推进到待复核或等待自动复盘。",
    }


def review_task(task_id: str, body: Dict[str, Any] | None = None, *, reviewer_id: str | None = None) -> Dict[str, Any]:
    body = body or {}
    reviewed = review_task_evidence(task_id, body, reviewer_id=reviewer_id or body.get("reviewerId"))
    if not reviewed:
        return {"version": TASK_SUBMISSION_REVIEW_STATION_VERSION, "ok": False, "stationId": "task_review_station", "error": "task_not_found", "taskId": task_id}
    decision = str(body.get("decision") or "approve")
    action = "review_return" if decision in {"return", "returned", "reject", "rejected", "退回", "驳回"} else "review_approve"
    transition = transition_lifecycle_task(
        task_id,
        action,
        actor_user_id=reviewer_id or body.get("reviewerId") or reviewed.get("reviewerId") or "U002",
        payload={"note": body.get("note") or body.get("comment") or ("复核退回。" if action == "review_return" else "复核通过。"), "stationId": "task_review_station"},
    )
    return {
        "version": TASK_SUBMISSION_REVIEW_STATION_VERSION,
        "ok": bool(transition.get("ok")),
        "stationId": "task_review_station",
        "taskId": task_id,
        "decision": decision,
        "reviewedTask": reviewed,
        "transition": transition,
        "rule": "复核站记录复核证据，再通过统一生命周期状态机推进到退回或自动复盘周期。",
    }


def task_evidence_detail(task_id: str, *, viewer_id: str | None = None) -> Dict[str, Any]:
    evidence = get_task_evidence(task_id, viewer_id=viewer_id)
    return evidence or {"version": TASK_SUBMISSION_REVIEW_STATION_VERSION, "ok": False, "error": "task_not_found", "taskId": task_id}


def submission_review_summary() -> Dict[str, Any]:
    return {
        "version": TASK_SUBMISSION_REVIEW_STATION_VERSION,
        "lifecycle": lifecycle_state_summary(),
        "rule": "V13.7：提交和复核是任务生命周期站点，不再只是todo页面按钮。",
    }
