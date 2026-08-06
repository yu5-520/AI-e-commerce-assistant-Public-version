"""Server-owned context for the public competition operator workspace.

This module is deliberately not an authentication, account, role-management or
multi-tenant service. It only supplies the fixed runtime actor and the sanitized
competition store catalog needed by projections after the mock account system was
removed from the public runtime.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

COMPETITION_OPERATOR_CONTEXT_VERSION = "1.0"
COMPETITION_OPERATOR_ID = "competition_operator"
COMPETITION_OPERATOR_ROLE = "operator"
COMPETITION_WORKSPACE_ID = "competition_demo"

_FIXED_OPERATOR: Dict[str, Any] = {
    "id": COMPETITION_OPERATOR_ID,
    "actorId": COMPETITION_OPERATOR_ID,
    "name": "赛事运营工作台",
    "displayName": "赛事运营工作台",
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
    "competition_operator",
    "default_operator",
    "competition_stores",
    "competition_operator_context",
]
