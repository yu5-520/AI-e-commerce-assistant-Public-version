"""Server-owned context for the public competition operator workspace.

This module is deliberately not an authentication, account, role-management or
multi-tenant service. It supplies only the fixed runtime actor and sanitized
competition store catalog required by the public operating workflow.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

COMPETITION_OPERATOR_CONTEXT_VERSION = "1.1"
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
}

_COMPETITION_STORES: List[Dict[str, Any]] = [
    {
        "id": "COMP-STORE-1",
        "name": "比赛脱敏店铺",
        "platform": "天猫",
        "workspaceId": COMPETITION_WORKSPACE_ID,
    }
]


def competition_operator() -> Dict[str, Any]:
    return deepcopy(_FIXED_OPERATOR)


def default_operator(_: Any | None = None) -> Dict[str, Any]:
    """Return the only server-owned task operator in the competition runtime."""
    return competition_operator()


def competition_stores() -> List[Dict[str, Any]]:
    return deepcopy(_COMPETITION_STORES)


def competition_store(store_id: str | None = None) -> Dict[str, Any] | None:
    """Resolve a sanitized competition store without exposing account assignment."""
    stores = competition_stores()
    if store_id:
        for store in stores:
            if str(store.get("id")) == str(store_id):
                return store
    return stores[0] if stores else None


def operator_display(actor_id: str | None = None, fallback: str = "赛事运营工作台") -> str:
    """Display the fixed operator; unknown client supplied IDs are never trusted."""
    if actor_id in {None, "", COMPETITION_OPERATOR_ID}:
        return COMPETITION_OPERATOR_DISPLAY_NAME
    return fallback


def competition_operator_context() -> Dict[str, Any]:
    return {
        "version": COMPETITION_OPERATOR_CONTEXT_VERSION,
        "actor": competition_operator(),
        "stores": competition_stores(),
        "applicationLoginEnabled": False,
        "applicationAccountSystemEnabled": False,
        "tenantIsolationClaimed": False,
        "identityOwner": "server_fixed_competition_context",
    }


__all__ = [
    "COMPETITION_OPERATOR_CONTEXT_VERSION",
    "COMPETITION_OPERATOR_ID",
    "COMPETITION_OPERATOR_ROLE",
    "COMPETITION_WORKSPACE_ID",
    "COMPETITION_OPERATOR_DISPLAY_NAME",
    "competition_operator",
    "default_operator",
    "competition_stores",
    "competition_store",
    "operator_display",
    "competition_operator_context",
]
