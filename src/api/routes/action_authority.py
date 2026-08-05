"""V21.4 operation-level action-authority and approval routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.services.account_service import get_user, user_id_from_headers
from src.services.action_authority_v214_service import ACTION_AUTHORITY_VERSION
from src.services.action_authority_v21_service import (
    ROAS_FAMILIES,
    authority_summary,
    get_operator_authority,
    get_store_policy,
    list_operator_authorities,
    list_store_policies,
    update_operator_authority,
    update_store_policy,
)
from src.services.pending_authority_migration_v21_service import recalculate_pending_roas_authority
from src.services.task_authority_decision_v21_service import decide_task_authorization_v21

router = APIRouter(prefix="/api/action-authority", tags=["action-authority"])

AUTHORITY_NUMERIC_FIELDS = {
    "singleAdjustmentLimit",
    "dailyAdjustmentLimit",
    "rolling24hLimit",
    "roasChangeRateLimit",
    "minimumTargetRoas",
    "ownerApprovalLimit",
}
STORE_NUMERIC_FIELDS = {
    "budgetLimitMultiplier",
    "roasChangeMultiplier",
    "ownerApprovalMultiplier",
}


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


def require_management_view(request: Request) -> str:
    viewer = request_user_id(request)
    list_operator_authorities(viewer)
    return viewer


def validate_numeric_fields(body: Dict[str, Any], fields: set[str]) -> None:
    for key in fields:
        if key not in body or body.get(key) is None:
            continue
        try:
            value = float(body[key])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"{key} must be numeric") from exc
        if value < 0:
            raise HTTPException(status_code=400, detail=f"{key} cannot be negative")


def validate_action_family(action_family: str) -> None:
    if action_family not in ROAS_FAMILIES:
        raise HTTPException(
            status_code=400,
            detail="V21.4 operation authority currently supports roas_scale and roas_guard",
        )


@router.get("/summary")
def summary(request: Request) -> Dict[str, Any]:
    result = dict(authority_summary(request_user_id(request)))
    result.update(
        {
            "version": ACTION_AUTHORITY_VERSION,
            "mode": "operation_plan_ir_numeric_authority",
            "genericAdjustmentRateUsedAsBudget": False,
            "familyNameUsedAsDirection": False,
        }
    )
    return result


@router.get("/operators")
def operators(request: Request) -> List[Dict[str, Any]]:
    return list_operator_authorities(request_user_id(request))


@router.get("/operators/{user_id}/{action_family}")
def operator_authority(request: Request, user_id: str, action_family: str) -> Dict[str, Any]:
    validate_action_family(action_family)
    if not get_user(user_id):
        raise HTTPException(status_code=404, detail="operator not found")
    viewer = request_user_id(request)
    if viewer != user_id:
        require_management_view(request)
    return get_operator_authority(user_id, action_family)


@router.put("/operators/{user_id}/{action_family}")
def change_operator_authority(
    request: Request,
    user_id: str,
    action_family: str,
    body: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    validate_action_family(action_family)
    user = get_user(user_id)
    if not user or user.get("roleId") != "operator":
        raise HTTPException(status_code=404, detail="operator not found")
    validate_numeric_fields(body, AUTHORITY_NUMERIC_FIELDS)
    viewer = require_management_view(request)
    return update_operator_authority(user_id, action_family, body, updated_by=viewer)


@router.get("/stores")
def stores(request: Request) -> List[Dict[str, Any]]:
    return list_store_policies(request_user_id(request))


@router.get("/stores/{store_id}")
def store_policy(request: Request, store_id: str) -> Dict[str, Any]:
    require_management_view(request)
    return get_store_policy(store_id)


@router.put("/stores/{store_id}")
def change_store_policy(
    request: Request,
    store_id: str,
    body: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    validate_numeric_fields(body, STORE_NUMERIC_FIELDS)
    viewer = require_management_view(request)
    return update_store_policy(store_id, body, updated_by=viewer)


@router.post("/tasks/{task_id}/decide")
def decide_task(
    request: Request,
    task_id: str,
    body: Dict[str, Any] = Body(default_factory=dict),
) -> Dict[str, Any]:
    validate_numeric_fields(body, {"approvedAdjustmentAmount", "approvedTargetROAS"})
    return decide_task_authorization_v21(
        task_id,
        body,
        actor_user_id=request_user_id(request),
    )


@router.post("/recalculate-pending")
def recalculate_pending(
    request: Request,
    limit: int = Query(default=300, ge=1, le=1000),
) -> Dict[str, Any]:
    viewer = require_management_view(request)
    return recalculate_pending_roas_authority(actor_user_id=viewer, limit=limit)


@router.get("/version")
def version() -> Dict[str, Any]:
    return {
        "version": ACTION_AUTHORITY_VERSION,
        "mode": "operation_plan_ir_numeric_authority",
        "operationTypes": [
            "budget_update",
            "bid_update",
            "target_roas_update",
            "stop_rule_update",
        ],
        "genericAdjustmentRateUsedAsBudget": False,
        "familyNameUsedAsDirection": False,
        "rule": (
            "权限只读取操作级Plan IR；预算、出价、目标ROAS和停止规则分别校验，"
            "禁止跨作用域百分比与动作族方向猜测。"
        ),
    }
