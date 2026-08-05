"""Approval compatibility routes delegated to V21 action authority."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, Request

from src.services.account_service import user_id_from_headers
from src.services.approval_service import get_task_status_overrides, list_approval_records
from src.services.task_authority_decision_v21_service import decide_task_authorization_v21

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


@router.get("")
def list_approval_status() -> Dict[str, Dict[str, Any]]:
    return get_task_status_overrides()


@router.get("/records")
def approval_records() -> List[Dict[str, Any]]:
    return list_approval_records()


@router.post("/{task_id}/approve")
def approve_task(
    request: Request,
    task_id: str,
    body: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    return decide_task_authorization_v21(
        task_id,
        {
            "decision": body.get("decision") or "approve_as_is",
            "approvedAdjustmentAmount": body.get("approvedAdjustmentAmount"),
            "approvedTargetROAS": body.get("approvedTargetROAS"),
            "assigneeId": body.get("assigneeId"),
            "note": body.get("note") or "通过V21统一权限入口批准。",
        },
        actor_user_id=request_user_id(request),
    )


@router.post("/{task_id}/reject")
def reject_task(
    request: Request,
    task_id: str,
    body: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    return decide_task_authorization_v21(
        task_id,
        {"decision": "reject", "note": body.get("note") or "通过V21统一权限入口拒绝。"},
        actor_user_id=request_user_id(request),
    )
