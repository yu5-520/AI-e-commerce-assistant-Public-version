"""V4.4+ feedback flywheel routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Query, Request

from src.services.account_service import user_id_from_headers
from src.services.agent_llm_enrichment_service import enrich_feedback_draft_result, enrich_feedback_summary_result
from src.services.feedback_flywheel_service import cycle_feedback_agent, draft_cycle_memory, feedback_flywheel_summary

router = APIRouter()


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


@router.get("/feedback-flywheel")
def feedback_flywheel(request: Request) -> Dict[str, Any]:
    return enrich_feedback_summary_result(feedback_flywheel_summary(user_id=request_user_id(request)))


@router.get("/feedback-flywheel/cycle/{target}")
def feedback_cycle(request: Request, target: str = "日报", limit: int = Query(default=8, ge=1, le=30)) -> Dict[str, Any]:
    return enrich_feedback_summary_result(cycle_feedback_agent(target=target, user_id=request_user_id(request), limit=limit))


@router.post("/feedback-flywheel/cycle/{target}/draft")
def feedback_cycle_draft(request: Request, target: str = "日报", body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    result = draft_cycle_memory(
        target=target,
        task_ids=body.get("taskIds") or body.get("task_ids"),
        user_id=request_user_id(request),
        limit=int(body.get("limit") or 6),
    )
    return enrich_feedback_draft_result(result)
