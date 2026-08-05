"""V20.28 sealed legacy RAG evidence-context compatibility module.

The former V17/V18 station read product_judgment_packages_v15 and wrote
rag_context_snapshots_v14. Both runtime paths are retired. Dynamic experience RAG
now runs inside the current Action Pack pipeline-item stage through
agent_rag_context_v2028_service.
"""

from __future__ import annotations

from typing import Any, Dict

from src.services.agent_rag_context_v2028_service import AGENT_RAG_CONTEXT_VERSION

RAG_EVIDENCE_CONTEXT_VERSION = "20.28-sealed"
RAG_CONTRACT_VERSION = AGENT_RAG_CONTEXT_VERSION
DEFAULT_RAG_CARDS: list[dict[str, Any]] = []


def ensure_rag_context_tables() -> None:
    """No-op: the retired snapshot table is not created by current runtime."""
    return None


def build_rag_evidence_context_snapshot(
    data_version: str | None = None,
    *,
    package_ref: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    return {
        "version": RAG_EVIDENCE_CONTEXT_VERSION,
        "contractVersion": RAG_CONTRACT_VERSION,
        "stationId": "rag_permission_context_station",
        "dataVersion": data_version,
        "packageRef": package_ref,
        "ok": False,
        "disabled": True,
        "error": "legacy_rag_station_retired",
        "replacement": "src.services.agent_rag_context_v2028_service.build_agent_rag_context_snapshot",
        "runtimeSourceUsed": False,
        "legacyPackageTableRead": False,
        "legacySnapshotTableWrite": False,
        "rule": "V20.28: old package-table RAG station is sealed and cannot participate in business runtime.",
    }


def latest_rag_evidence_context(data_version: str | None = None) -> Dict[str, Any] | None:
    del data_version
    return None


def rag_permission_context_station_v176(
    data_version: str | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    return build_rag_evidence_context_snapshot(data_version=data_version, **kwargs)
