"""Install the V25 knowledge runtime projection without widening runtime files.

The control-plane governance JSON remains outside the production package.  This
installer rebinds the V25 knowledge service's cached registry/table readers to the
CI-verified projection embedded under ``src/services`` before any business call can
compose Agent knowledge.
"""
from __future__ import annotations

from typing import Any, Dict

V25_KNOWLEDGE_RUNTIME_PROJECTION_INSTALL_VERSION = "25.9.0"
_INSTALLED = False


def install_v25_knowledge_runtime_projection() -> Dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return runtime_projection_status(idempotent=True)

    from src.services import unified_agent_knowledge_v25_service as knowledge
    from src.services.v25_knowledge_runtime_projection import (
        knowledge_composition_table_v25,
        rag_field_registry_v25,
    )

    # Clear any source-checkout cache before replacing the readers.  In production
    # these functions must never resolve governance/v25 from the filesystem.
    for name in ("_field_registry", "_composition_table"):
        current = getattr(knowledge, name, None)
        clear = getattr(current, "cache_clear", None)
        if callable(clear):
            clear()

    knowledge._field_registry = rag_field_registry_v25
    knowledge._composition_table = knowledge_composition_table_v25
    knowledge.V25_RUNTIME_PROJECTION_INSTALLED = True
    knowledge.V25_RUNTIME_PROJECTION_VERSION = (
        V25_KNOWLEDGE_RUNTIME_PROJECTION_INSTALL_VERSION
    )
    _INSTALLED = True
    return runtime_projection_status(idempotent=False)


def runtime_projection_status(*, idempotent: bool = False) -> Dict[str, Any]:
    return {
        "version": V25_KNOWLEDGE_RUNTIME_PROJECTION_INSTALL_VERSION,
        "installed": _INSTALLED,
        "idempotent": idempotent,
        "runtimeSource": "src.services.v25_knowledge_runtime_projection",
        "governanceFilesystemReadRequired": False,
        "runtimePackageGovernanceExpansionRequired": False,
        "projectionVerifiedByReleaseGate": True,
    }


__all__ = [
    "V25_KNOWLEDGE_RUNTIME_PROJECTION_INSTALL_VERSION",
    "install_v25_knowledge_runtime_projection",
    "runtime_projection_status",
]
