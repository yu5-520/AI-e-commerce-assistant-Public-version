"""V13.7 Task Lifecycle Station routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Query, Request

from src.services.account_service import user_id_from_headers
from src.services.task_acceptance_assignment_station_service import accept_task, acceptance_assignment_summary, assign_task, auto_accept_ready_task_pool_tasks
from src.services.task_recap_rag_station_service import build_task_rag_candidate, complete_task_recap, recap_rag_summary, schedule_task_recap
from src.services.task_submission_review_station_service import review_task, submission_review_summary, submit_task, task_evidence_detail

router = APIRouter(prefix="/api/task-lifecycle-stations", tags=["task-lifecycle-stations"])
TASK_LIFECYCLE_STATIONS_ROUTE_VERSION = "13.7.0"


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


@router.get("/summary")
def lifecycle_station_summary() -> Dict[str, Any]:
    return {
        "version": TASK_LIFECYCLE_STATIONS_ROUTE_VERSION,
        "acceptanceAssignment": acceptance_assignment_summary(),
        "submissionReview": submission_review_summary(),
        "recapRag": recap_rag_summary(),
        "rule": "V13.7：任务入池后的接收、派发、提交、复核、复盘和RAG回流都暴露为站点接口。",
    }


@router.post("/acceptance/{task_id}/accept")
def accept_station(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    return accept_task(task_id, actor_user_id=request_user_id(request), note=body.get("note"), auto=False)


@router.post("/acceptance/auto-sync")
def auto_accept_station(request: Request, viewerId: str | None = Query(default=None)) -> Dict[str, Any]:
    return auto_accept_ready_task_pool_tasks(viewer_id=viewerId or request_user_id(request))


@router.post("/assignment/{task_id}/assign")
def assignment_station(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    return assign_task(task_id, actor_user_id=request_user_id(request), assignee_id=body.get("assigneeId") or body.get("assignee_id"), reviewer_id=body.get("reviewerId") or body.get("reviewer_id"), note=body.get("note"), split=bool(body.get("split")))


@router.get("/submission/{task_id}/evidence")
def evidence_station(request: Request, task_id: str) -> Dict[str, Any]:
    return task_evidence_detail(task_id, viewer_id=request_user_id(request))


@router.post("/submission/{task_id}/submit")
def submission_station(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return submit_task(task_id, body or {}, submitter_id=request_user_id(request))


@router.post("/review/{task_id}/review")
def review_station(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return review_task(task_id, body or {}, reviewer_id=request_user_id(request))


@router.post("/recap/{task_id}/schedule")
def recap_schedule_station(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    return schedule_task_recap(task_id, actor_user_id=request_user_id(request), trigger=body.get("trigger") or "manual_station_schedule")


@router.post("/recap/{task_id}/complete")
def recap_complete_station(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return complete_task_recap(task_id, body or {}, reviewer_id=request_user_id(request))


@router.post("/rag-feedback/{task_id}")
def rag_feedback_station(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return build_task_rag_candidate(task_id, body or {}, user_id=request_user_id(request))
