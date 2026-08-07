"""Server-owned context for the public competition operator workspace.

This module is deliberately not an authentication, account, role-management or
multi-tenant service. It supplies only the fixed runtime actor and sanitized
competition store catalog required by the public operating workflow.

Legacy runtime consumers may import compatibility helpers from this module, but
all of them ignore client-selected identity and resolve to the same fixed actor.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping

COMPETITION_OPERATOR_CONTEXT_VERSION = "1.2"
COMPETITION_OPERATOR_ID = "competition_operator"
COMPETITION_OPERATOR_ROLE = "operator"
COMPETITION_WORKSPACE_ID = "competition_demo"
COMPETITION_OPERATOR_DISPLAY_NAME = "赛事运营工作台"

_FIXED_OPERATOR: Dict[str, Any] = {
    "id": COMPETITION_OPERATOR_ID,
    "actorId": COMPETITION_OPERATOR_ID,
    "name": COMPETITION_OPERATOR_DISPLAY_NAME,
    "displayName": COMPETITION_OPERATOR_DISPLAY_NAME,
    "roleId": COMPETITION_OPERATOR_ROLE,
    "role": COMPETITION_OPERATOR_ROLE,
    "workspaceId": COMPETITION_WORKSPACE_ID,
    "serverInjected": True,
    "clientOverrideAllowed": False,
    "permissions": [
        "view_managed_stores",
        "view_own_tasks",
        "handle_tasks",
        "submit_tasks",
        "view_only",
    ],
}

_COMPETITION_STORES: List[Dict[str, Any]] = [
    {
        "id": "COMP-STORE-1",
        "name": "比赛脱敏店铺",
        "platform": "天猫",
        "workspaceId": COMPETITION_WORKSPACE_ID,
        "primaryOperatorId": COMPETITION_OPERATOR_ID,
    }
]


def competition_operator() -> Dict[str, Any]:
    return deepcopy(_FIXED_OPERATOR)


def current_user(_: str | None = None) -> Dict[str, Any]:
    """Return the fixed actor and ignore all client-selected identity."""
    return competition_operator()


def get_user(user_id: str | None) -> Dict[str, Any] | None:
    """Resolve only the fixed actor; arbitrary IDs never gain an identity."""
    if user_id in {None, "", COMPETITION_OPERATOR_ID}:
        return competition_operator()
    return None


def user_id_from_headers(_: Mapping[str, str] | None = None) -> str:
    """Ignore request identity headers and return the server-owned actor ID."""
    return COMPETITION_OPERATOR_ID


def default_operator(_: Any | None = None) -> Dict[str, Any]:
    """Return the only server-owned task operator in the competition runtime."""
    return competition_operator()


def default_reviewer() -> Dict[str, Any]:
    """Return a non-account sentinel for legacy callers expecting a mapping.

    Department review remains unavailable in the public competition runtime. The
    mapping deliberately carries no reviewer identity, so compatibility callers
    can safely read ``.get('id')`` without reintroducing a manager demo account.
    """
    return {
        "id": None,
        "available": False,
        "enterpriseOnly": True,
        "source": "competition_enterprise_review_boundary",
    }


def competition_stores() -> List[Dict[str, Any]]:
    return deepcopy(_COMPETITION_STORES)


def list_stores() -> List[Dict[str, Any]]:
    return competition_stores()


def visible_store_ids_for_user(_: str | None = None) -> List[str]:
    return [str(store["id"]) for store in _COMPETITION_STORES]


def competition_store(store_id: str | None = None) -> Dict[str, Any] | None:
    """Resolve a sanitized competition store without exposing account assignment."""
    stores = competition_stores()
    if store_id:
        for store in stores:
            if str(store.get("id")) == str(store_id):
                return store
    return stores[0] if stores else None


def store_raw(store_id: str | None = None) -> Dict[str, Any] | None:
    return competition_store(store_id)


def assignment_for_store(store_id: str | None = None) -> Dict[str, Any]:
    store = competition_store(store_id) or {}
    return {
        "storeId": store.get("id") or store_id or "COMP-STORE-1",
        "primaryOperatorId": COMPETITION_OPERATOR_ID,
        "reviewerId": None,
        "source": "competition_fixed_operator_context",
    }


def operator_display(actor_id: str | None = None, fallback: str = "赛事运营工作台") -> str:
    """Display the fixed operator; unknown client supplied IDs are never trusted."""
    if actor_id in {None, "", COMPETITION_OPERATOR_ID}:
        return COMPETITION_OPERATOR_DISPLAY_NAME
    if actor_id == "system":
        return "系统经营链路"
    return fallback


def user_display(actor_id: str | None = None, fallback: str = "赛事运营工作台") -> str:
    return operator_display(actor_id, fallback)


def competition_operator_context() -> Dict[str, Any]:
    return {
        "version": COMPETITION_OPERATOR_CONTEXT_VERSION,
        "actor": competition_operator(),
        "stores": competition_stores(),
        "applicationLoginEnabled": False,
        "applicationAccountSystemEnabled": False,
        "tenantIsolationClaimed": False,
        "identityOwner": "server_fixed_competition_context",
        "reviewerAccountAvailable": False,
        "clientIdentityOverrideAllowed": False,
    }


__all__ = [
    "COMPETITION_OPERATOR_CONTEXT_VERSION",
    "COMPETITION_OPERATOR_ID",
    "COMPETITION_OPERATOR_ROLE",
    "COMPETITION_WORKSPACE_ID",
    "COMPETITION_OPERATOR_DISPLAY_NAME",
    "competition_operator",
    "current_user",
    "get_user",
    "user_id_from_headers",
    "default_operator",
    "default_reviewer",
    "competition_stores",
    "list_stores",
    "visible_store_ids_for_user",
    "competition_store",
    "store_raw",
    "assignment_for_store",
    "operator_display",
    "user_display",
    "competition_operator_context",
]
