from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Query, Request

from src.services.competition_operator_context_service import user_id_from_headers
from src.services.frontend_read_model_service import read_task_views, refresh_task_views
from src.services.task_acceptance_assignment_station_service import accept_task, auto_accept_ready_task_pool_tasks, assign_task, acceptance_assignment_summary
from src.services.task_recap_rag_station_service import build_task_rag_candidate, complete_task_recap, recap_rag_summary, schedule_task_recap
from src.services.task_submission_review_station_service import review_task, submission_review_summary, submit_task, task_evidence_detail

router = APIRouter(prefix="/todo", tags=["todo-lifecycle-bridge"])
TODO_BRIDGE_VERSION = "17.8"


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


@router.get("")
def list_todo(status: str | None = Query(default=None), limit: int = Query(default=200, ge=1, le=500)) -> Dict[str, Any]:
    result = read_task_views(status=status, limit=limit)
    items = result.get("items") or []
    result["tasks"] = items
    result["activeTasks"] = [item for item in items if item.get("status") not in {"已完成", "已归档", "已写入复盘"}]
    result["bridgeVersion"] = TODO_BRIDGE_VERSION
    result["routeRule"] = "V17.8 /api/modules/todo reads current frontend_task_view."
    return result


@router.get("/events")
def todo_events() -> Dict[str, Any]:
    summary = submission_review_summary()
    return {"version": TODO_BRIDGE_VERSION, "events": summary.get("recentEvents") or [], "summary": summary}


@router.get("/counters")
def todo_counters() -> Dict[str, Any]:
    tasks = read_task_views(limit=500).get("items") or []
    return {
        "version": TODO_BRIDGE_VERSION,
        "counts": {
            "visibleActive": len(tasks),
            "waitingAccept": len([task for task in tasks if task.get("status") in {"待接收", "待确认", "已派发"}]),
            "processing": len([task for task in tasks if task.get("status") == "处理中"]),
            "submitted": len([task for task in tasks if task.get("status") in {"已提交", "待复核"}]),
        },
    }


@router.get("/lifecycle/summary")
def lifecycle_summary() -> Dict[str, Any]:
    return {"version": TODO_BRIDGE_VERSION, "acceptanceAssignment": acceptance_assignment_summary(), "submissionReview": submission_review_summary(), "recapRag": recap_rag_summary()}


@router.post("/lifecycle/sync")
def lifecycle_sync(request: Request) -> Dict[str, Any]:
    refresh = refresh_task_views()
    auto = auto_accept_ready_task_pool_tasks(viewer_id=request_user_id(request))
    return {"version": TODO_BRIDGE_VERSION, "readModelRefresh": refresh, "autoAccept": auto}


@router.post("/{task_id}/accept")
def accept_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return accept_task(task_id, actor_user_id=request_user_id(request), note=(body or {}).get("note"), auto=False)


@router.post("/{task_id}/assign")
def assign_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    return assign_task(task_id, actor_user_id=request_user_id(request), assignee_id=body.get("assigneeId") or body.get("assignee_id"), reviewer_id=body.get("reviewerId") or body.get("reviewer_id"), note=body.get("note"), split=bool(body.get("split")))


@router.post("/{task_id}/split")
def split_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    body["split"] = True
    return assign_todo(request, task_id, body)


@router.get("/{task_id}/evidence")
def evidence_todo(request: Request, task_id: str) -> Dict[str, Any]:
    return task_evidence_detail(task_id, viewer_id=request_user_id(request))


@router.post("/{task_id}/submit")
def submit_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return submit_task(task_id, body or {}, submitter_id=request_user_id(request))


@router.post("/{task_id}/submit-evidence")
def submit_evidence_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return submit_task(task_id, body or {}, submitter_id=request_user_id(request))


@router.post("/{task_id}/review")
def review_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return review_task(task_id, body or {}, reviewer_id=request_user_id(request))


@router.post("/{task_id}/review-evidence")
def review_evidence_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return review_task(task_id, body or {}, reviewer_id=request_user_id(request))


@router.post("/{task_id}/recap")
def schedule_recap_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return schedule_task_recap(task_id, actor_user_id=request_user_id(request), trigger=(body or {}).get("trigger") or "todo_bridge")


@router.post("/{task_id}/recap/complete")
def complete_recap_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return complete_task_recap(task_id, body or {}, reviewer_id=request_user_id(request))


@router.post("/{task_id}/complete")
def complete_todo(request: Request, task_id: str) -> Dict[str, Any]:
    return schedule_task_recap(task_id, actor_user_id=request_user_id(request), trigger="manual_complete")


@router.post("/{task_id}/pin")
def pin_todo(task_id: str) -> Dict[str, Any]:
    return {"version": TODO_BRIDGE_VERSION, "ok": True, "taskId": task_id, "status": "pin_not_persisted", "rule": "V17.8 pin is a frontend-only ordering hint."}


@router.post("/{task_id}/reorder")
def reorder_todo(task_id: str, direction: str = Query(default="down")) -> Dict[str, Any]:
    return {"version": TODO_BRIDGE_VERSION, "ok": True, "taskId": task_id, "direction": direction, "status": "reorder_not_persisted", "rule": "V17.8 reorder is a frontend-only ordering hint."}


@router.post("/reset")
def reset_todo() -> Dict[str, Any]:
    refresh = refresh_task_views()
    return {"version": TODO_BRIDGE_VERSION, "ok": True, "readModelRefresh": refresh, "rule": "V17.8 reset only refreshes current read model; persistent task pool is not cleared here."}


@router.post("/rag-feedback/{task_id}")
def rag_feedback_todo(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    return build_task_rag_candidate(task_id, body or {}, user_id=request_user_id(request))
