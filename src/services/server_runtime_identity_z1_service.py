"""Canonical Z1 server runtime identity.

Service unit names remain transport metadata. The application identity is stable across
systemd aliases and is bound to the ASGI entry, runtime root and lineage version.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

SERVER_RUNTIME_IDENTITY_VERSION = "Z1.0.3"
CANONICAL_APPLICATION_ID = "ai-ecommerce-assistant"
CANONICAL_ASGI_ENTRY = "src.api.main:app"
CANONICAL_RUNTIME_LINEAGE_VERSION = "Z1.0.4"
DEFAULT_RUNTIME_ROOT = "/opt/ai-ecommerce-assistant"
DEFAULT_PORT = 3000


def server_runtime_identity() -> Dict[str, Any]:
    runtime_root = str(
        os.getenv("AI_RELEASE_ROOT")
        or os.getenv("AI_ECOMMERCE_ROOT")
        or DEFAULT_RUNTIME_ROOT
    )
    application_id = str(
        os.getenv("AI_RUNTIME_APPLICATION_ID") or CANONICAL_APPLICATION_ID
    )
    asgi_entry = str(os.getenv("AI_RUNTIME_ENTRY") or CANONICAL_ASGI_ENTRY)
    lineage_version = str(
        os.getenv("AI_RUNTIME_LINEAGE_VERSION") or CANONICAL_RUNTIME_LINEAGE_VERSION
    )
    try:
        port = int(os.getenv("APP_PORT", str(DEFAULT_PORT)))
    except Exception:
        port = DEFAULT_PORT
    return {
        "version": SERVER_RUNTIME_IDENTITY_VERSION,
        "applicationId": application_id,
        "applicationIdMatch": application_id == CANONICAL_APPLICATION_ID,
        "asgiEntry": asgi_entry,
        "asgiEntryMatch": asgi_entry == CANONICAL_ASGI_ENTRY,
        "runtimeLineageVersion": lineage_version,
        "runtimeLineageVersionMatch": (
            lineage_version == CANONICAL_RUNTIME_LINEAGE_VERSION
        ),
        "runtimeRoot": str(Path(runtime_root)),
        "runtimeRootExists": Path(runtime_root).exists(),
        "port": port,
        "serviceUnit": os.getenv("AI_ECOMMERCE_SERVICE"),
        "serviceIdentityMode": "listener_pid_systemd_cgroup_and_runtime_root",
        "verified": bool(
            application_id == CANONICAL_APPLICATION_ID
            and asgi_entry == CANONICAL_ASGI_ENTRY
            and lineage_version == CANONICAL_RUNTIME_LINEAGE_VERSION
        ),
        "databaseMutated": False,
        "providerCallsExecuted": 0,
    }


__all__ = [
    "CANONICAL_APPLICATION_ID",
    "CANONICAL_ASGI_ENTRY",
    "CANONICAL_RUNTIME_LINEAGE_VERSION",
    "SERVER_RUNTIME_IDENTITY_VERSION",
    "server_runtime_identity",
]
