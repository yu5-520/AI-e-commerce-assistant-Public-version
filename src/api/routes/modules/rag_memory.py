"""V4.1 RAG memory routes for the operation experience flywheel."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.services.account_service import current_user, user_id_from_headers
from src.services.experience_memory_service import (
    approve_case,
    draft_experience_from_task,
    list_cases,
    memory_summary,
    reject_case,
    search_cases,
)

router = APIRouter()
REVIEW_ROLE_IDS = {"owner", "manager"}


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


def require_memory_reviewer(user_id: str) -> None:
    user = current_user(user_id)
    if user.get("roleId") not in REVIEW_ROLE_IDS:
        raise HTTPException(status_code=403, detail="当前账号无权复核 RAG 经验入库")


@router.get("/rag-memory")
def rag_memory_summary() -> Dict[str, Any]:
    return memory_summary()


@router.get("/rag-memory/cases")
def rag_memory_cases(
    status: str | None = Query(default=None),
    level: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=300),
) -> Dict[str, Any]:
    return {"summary": memory_summary(), "items": list_cases(status=status, level=level, limit=limit)}


@router.get("/rag-memory/search")
def rag_memory_search(
    q: str | None = Query(default=None),
    category_id: str | None = Query(default=None),
    platform: str | None = Query(default=None),
    store_id: str | None = Query(default=None),
    problem_type: str | None = Query(default=None),
    operator_style: str | None = Query(default=None),
    effective_only: bool = Query(default=True),
    min_quality: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=5, ge=1, le=30),
) -> Dict[str, Any]:
    return search_cases(
        query=q,
        category_id=category_id,
        platform=platform,
        store_id=store_id,
        problem_type=problem_type,
        operator_style=operator_style,
        effective_only=effective_only,
        min_quality=min_quality,
        limit=limit,
    )


@router.post("/rag-memory/feedback/tasks/{task_id}")
def feedback_task_memory(request: Request, task_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    result = draft_experience_from_task(
        task_id,
        operator_submission=body.get("operatorSubmission") or body.get("operator_submission") or "",
        manager_review=body.get("managerReview") or body.get("manager_review") or "",
        before_metrics=body.get("beforeMetrics") or body.get("before_metrics") or {},
        after_metrics=body.get("afterMetrics") or body.get("after_metrics") or {},
        user_id=request_user_id(request),
    )
    if not result:
        raise HTTPException(status_code=404, detail="task not found for feedback memory")
    return result


@router.post("/rag-memory/cases/{case_id}/approve")
def approve_rag_case(request: Request, case_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    user_id = request_user_id(request)
    require_memory_reviewer(user_id)
    body = body or {}
    case = approve_case(case_id, reviewer_id=user_id, reason=body.get("reason") or body.get("note") or "")
    if not case:
        raise HTTPException(status_code=404, detail="experience case not found")
    return {"case": case, "summary": memory_summary()}


@router.post("/rag-memory/cases/{case_id}/reject")
def reject_rag_case(request: Request, case_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    user_id = request_user_id(request)
    require_memory_reviewer(user_id)
    body = body or {}
    case = reject_case(case_id, reviewer_id=user_id, reason=body.get("reason") or body.get("note") or "")
    if not case:
        raise HTTPException(status_code=404, detail="experience case not found")
    return {"case": case, "summary": memory_summary()}
