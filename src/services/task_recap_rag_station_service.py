"""V20.28 task recap and RAG feedback lifecycle stations.

Recap scheduling and metric collection remain system-driven. Completed recaps
create pending experience candidates; only manager-approved, effective cards are
eligible for later Agent retrieval.
"""

from __future__ import annotations

from typing import Any, Dict

from src.services import module_task_service
from src.services.rag_feedback_loop_service import (
    RAG_FEEDBACK_LOOP_VERSION,
    build_rag_candidate_from_recap,
)
from src.services.task_lifecycle_state_machine_service import (
    lifecycle_state_summary,
    transition_lifecycle_task,
)
from src.services.task_recap_scheduler_service import (
    complete_recap_cycle,
    recap_schedule_summary,
    schedule_recap_cycles,
)

TASK_RECAP_RAG_STATION_VERSION = "20.28"


def schedule_task_recap(
    task_id: str,
    *,
    actor_user_id: str | None = None,
    trigger: str = "task_review_station",
) -> Dict[str, Any]:
    task = module_task_service.find_task(task_id)
    if not task:
        return {
            "version": TASK_RECAP_RAG_STATION_VERSION,
            "ok": False,
            "stationId": "recap_schedule_station",
            "error": "task_not_found",
            "taskId": task_id,
        }
    result = schedule_recap_cycles(task, trigger=trigger, actor_user_id=actor_user_id)
    return {
        "version": TASK_RECAP_RAG_STATION_VERSION,
        "ok": True,
        "stationId": "recap_schedule_station",
        "taskId": task_id,
        "recapSchedule": result,
        "rule": "复核通过后由系统生成复盘周期；运营不手填预测结论。",
    }


def complete_task_recap(
    task_id: str,
    body: Dict[str, Any] | None = None,
    *,
    reviewer_id: str | None = None,
) -> Dict[str, Any]:
    body = body or {}
    task = module_task_service.find_task(task_id)
    if not task:
        return {
            "version": TASK_RECAP_RAG_STATION_VERSION,
            "ok": False,
            "stationId": "recap_complete_station",
            "error": "task_not_found",
            "taskId": task_id,
        }
    recap_result = complete_recap_cycle(
        task,
        cycle_id=body.get("cycleId") or body.get("cycle_id"),
        before_metrics=body.get("beforeMetrics") or {},
        after_metrics=body.get("afterMetrics") or {},
        reviewer_id=reviewer_id or body.get("reviewerId"),
        conclusion=body.get("conclusion") or body.get("note"),
    )
    transition = transition_lifecycle_task(
        task_id,
        "recap_complete",
        actor_user_id=reviewer_id or body.get("reviewerId") or "system",
        payload={
            "beforeMetrics": body.get("beforeMetrics") or {},
            "afterMetrics": body.get("afterMetrics") or {},
            "conclusion": body.get("conclusion") or body.get("note"),
            "stationId": "recap_complete_station",
        },
    )
    return {
        "version": TASK_RECAP_RAG_STATION_VERSION,
        "ok": bool(recap_result.get("taskId") or transition.get("ok")),
        "stationId": "recap_complete_station",
        "taskId": task_id,
        "recapResult": recap_result,
        "transition": transition,
        "rule": "复盘完成后系统记录前后指标和结论，并准备待人工审核的RAG经验候选。",
    }


def build_task_rag_candidate(
    task_id: str,
    body: Dict[str, Any] | None = None,
    *,
    user_id: str | None = None,
) -> Dict[str, Any]:
    body = body or {}
    result = build_rag_candidate_from_recap(
        task_id,
        recap_result=body.get("recapResult") or body,
        user_id=user_id or body.get("userId"),
    )
    if not result:
        return {
            "version": TASK_RECAP_RAG_STATION_VERSION,
            "ok": False,
            "stationId": "rag_feedback_station",
            "error": "rag_candidate_not_created",
            "taskId": task_id,
        }
    result["stationVersion"] = TASK_RECAP_RAG_STATION_VERSION
    result["feedbackLoopVersion"] = RAG_FEEDBACK_LOOP_VERSION
    result["stationId"] = "rag_feedback_station"
    result["agentRetrievalEligible"] = False
    result["approvalRequired"] = True
    return result


def recap_rag_summary() -> Dict[str, Any]:
    return {
        "version": TASK_RECAP_RAG_STATION_VERSION,
        "feedbackLoopVersion": RAG_FEEDBACK_LOOP_VERSION,
        "recapSchedule": recap_schedule_summary(),
        "lifecycle": lifecycle_state_summary(),
        "writeMode": "recap_creates_pending_review_candidate",
        "readMode": "only_approved_effective_real_task_cards_enter_agent_rag",
        "rule": "V20.28 closes the recap-to-RAG write boundary without allowing unreviewed experience into Agent generation.",
    }
