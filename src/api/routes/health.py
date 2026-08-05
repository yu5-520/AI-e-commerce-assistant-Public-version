from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from src.runtime_version import RUNTIME_MODE, VERSION, runtime_versions
from src.services.release_identity_service import release_identity
from src.services.station_queue_worker_service import worker_config

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> Dict[str, Any]:
    background = worker_config()
    identity = release_identity(verify_content=False)
    release_hash = str(identity.get("releaseHash") or "")
    return {
        "ok": True,
        "version": VERSION,
        "productVersion": VERSION,
        "contractVersion": VERSION,
        "runtimeMode": RUNTIME_MODE,
        "runtimeVersions": runtime_versions(),
        "releaseVerified": bool(identity.get("verified")),
        "releaseStatus": identity.get("status"),
        "releaseHashShort": release_hash.split(":", 1)[-1][:12] if release_hash else None,
        "sourceCommit": identity.get("sourceCommit"),
        "product": "AI ERP Operating Advisor",
        "currentEntry": "/",
        "versionEntry": "/api/version",
        "releaseIdentityEntry": "/api/system/release-identity",
        "frontendTaskViewEntry": "/api/view/tasks",
        "frontendTaskDetailEntry": "/api/view/tasks/{task_id}",
        "runtimeDiagnosticsEntry": "/api/system/agent-pipeline-status",
        "runtimeResetEntry": "/api/system/reset-runtime-data?confirm=true&scope=demo",
        "canonicalActionField": "activeActionContract",
        "fallbackAllowed": False,
        "backgroundWorker": {
            "version": VERSION,
            "governanceVersion": VERSION,
            "enabledByEnv": background.get("enabledByEnv"),
            "agentPipelineEnabled": background.get("agentPipelineEnabled"),
            "dataVersionSelection": background.get("dataVersionSelection"),
            "forceNewSnapshot": background.get("forceNewSnapshot"),
        },
        "rule": "Health reports product version and immutable release identity without scanning business data.",
    }
