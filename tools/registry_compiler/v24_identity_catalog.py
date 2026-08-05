"""V24.0 registry-only module identity anchors.

These callables exist so the unified registry can bind stable owner/runner identities
before any V24 business runtime is activated. They do not read business data, mutate
tasks, call Providers, or participate in the active station graph.
"""
from __future__ import annotations

from typing import Any, Dict


_VERSION = "24.0.0"
_ACTIVATION_STATE = "REGISTERED_ONLY"


def _identity(module_id: str) -> Dict[str, Any]:
    return {
        "schema": "registry.module_identity.v24",
        "version": _VERSION,
        "moduleId": module_id,
        "activationState": _ACTIVATION_STATE,
        "runtimeBindingEnabled": False,
        "businessRuntimeMutated": False,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }


def operating_plan_compiler_identity() -> Dict[str, Any]:
    return _identity("operating_plan_compiler")


def action_node_transport_identity() -> Dict[str, Any]:
    return _identity("action_node_transport")


def execution_resource_orchestrator_identity() -> Dict[str, Any]:
    return _identity("execution_resource_orchestrator")


def node_authorization_identity() -> Dict[str, Any]:
    return _identity("node_authorization")


def task_blueprint_compiler_identity() -> Dict[str, Any]:
    return _identity("task_blueprint_compiler")


def stage_lifecycle_identity() -> Dict[str, Any]:
    return _identity("stage_lifecycle")


def stage_frontend_projection_identity() -> Dict[str, Any]:
    return _identity("stage_frontend_projection")


__all__ = [
    "operating_plan_compiler_identity",
    "action_node_transport_identity",
    "execution_resource_orchestrator_identity",
    "node_authorization_identity",
    "task_blueprint_compiler_identity",
    "stage_lifecycle_identity",
    "stage_frontend_projection_identity",
]
