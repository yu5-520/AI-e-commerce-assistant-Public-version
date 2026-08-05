"""V16.11 account, role, store responsibility, and permission routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException, Request

from src.services.account_service import (
    account_summary,
    current_user,
    get_user,
    list_pending_store_migrations,
    list_permissions,
    list_role_change_logs,
    list_roles,
    list_store_assignments,
    list_store_groups,
    list_stores,
    list_users,
    resolve_user_id,
    schedule_store_assignment_migration,
    update_role_permissions,
    update_user_role,
    update_user_stores,
    user_has_permission,
    user_id_from_headers,
)
from src.services.backend_isolation_service import demo_account_switch_enabled, production_mode

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


def require_role_manager(request: Request) -> str:
    user_id = request_user_id(request)
    if not user_has_permission(user_id, "manage_roles"):
        user = current_user(user_id)
        raise HTTPException(status_code=403, detail=f"{user['roleName']} cannot manage account permissions")
    return user_id


def _scrub_account_summary_for_production(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Account center must not leak organization membership in production."""
    if not production_mode() or demo_account_switch_enabled():
        return payload
    return {
        **payload,
        "users": [payload.get("currentUser")],
        "stores": [],
        "storeAssignments": [],
        "pendingStoreMigrations": [],
        "roleChangeLogs": [],
        "demoSwitchDisabled": True,
        "isolationNote": "Production account summary is scoped to the current login identity. Organization/store membership is available only in permission governance routes.",
    }


@router.get("")
def accounts(request: Request) -> Dict[str, Any]:
    return _scrub_account_summary_for_production(account_summary(request_user_id(request)))


@router.get("/me")
def me(request: Request) -> Dict[str, Any]:
    return current_user(request_user_id(request))


@router.post("/switch")
def switch_account(body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    """Return a validated mock user context for frontend role switching."""
    if production_mode() and not demo_account_switch_enabled():
        raise HTTPException(status_code=403, detail="MVP account switch is disabled in production. Set DEMO_ACCOUNT_SWITCH=true for ECS demo validation.")
    user_id = resolve_user_id(body.get("user_id") or body.get("userId"))
    return {"currentUser": current_user(user_id), "account": account_summary(user_id), "demoAccountSwitchEnabled": demo_account_switch_enabled()}


@router.get("/users")
def users(request: Request) -> List[Dict[str, Any]]:
    require_role_manager(request)
    return list_users()


@router.get("/users/{user_id}")
def user_detail(request: Request, user_id: str) -> Dict[str, Any]:
    operator_id = require_role_manager(request)
    user = get_user(user_id if user_id != "me" else operator_id)
    if not user:
        raise HTTPException(status_code=404, detail="user not found")
    return user


@router.post("/users/{user_id}/role")
def change_user_role(request: Request, user_id: str, body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    operator_id = require_role_manager(request)
    user = update_user_role(user_id, body.get("role_id") or body.get("roleId"), operator_id=operator_id)
    if not user:
        raise HTTPException(status_code=400, detail="cannot update user role")
    return {"user": user, "account": _scrub_account_summary_for_production(account_summary(operator_id))}


@router.post("/users/{user_id}/stores")
def change_user_stores(request: Request, user_id: str, body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    operator_id = require_role_manager(request)
    user = update_user_stores(user_id, body.get("store_ids") or body.get("storeIds") or [], operator_id=operator_id)
    if not user:
        raise HTTPException(status_code=400, detail="cannot update user stores")
    return {"user": user, "account": _scrub_account_summary_for_production(account_summary(operator_id))}


@router.get("/store-assignments")
def store_assignments(request: Request) -> List[Dict[str, Any]]:
    require_role_manager(request)
    return list_store_assignments()


@router.get("/store-migrations")
def store_migrations(request: Request) -> List[Dict[str, Any]]:
    require_role_manager(request)
    return list_pending_store_migrations()


@router.post("/store-assignments/{store_id}")
def change_store_assignment(request: Request, store_id: str, body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    operator_id = require_role_manager(request)
    try:
        migration = schedule_store_assignment_migration(
            store_id,
            body.get("primary_operator_id") or body.get("primaryOperatorId"),
            reviewer_id=body.get("reviewer_id") or body.get("reviewerId"),
            password=body.get("password") or body.get("managementPassword"),
            operator_id=operator_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not migration:
        raise HTTPException(status_code=400, detail="cannot schedule store assignment migration")
    return {"migration": migration, "account": _scrub_account_summary_for_production(account_summary(operator_id))}
