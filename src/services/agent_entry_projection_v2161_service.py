"""Finalize V21.6.1 bindings that are imported by value in worker/read routes."""

from __future__ import annotations

from typing import Any, Dict

from src.services.agent_entry_interface_v2161_service import (
    AGENT_ENTRY_INTERFACE_VERSION,
    AGENT_ENTRY_OWNER,
    PRE_AGENT_QUEUE_TERMINAL_STATION,
    _latest_admission_projection,
)

_INSTALLED = False


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def install_v2161_agent_entry_projection() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from src.services import agent_entry_interface_v2161_service as base
    from src.services import agent_pipeline_governance_v213_service as governance
    from src.services import agent_pipeline_item_worker_v2010_service as agent_worker
    from src.services import pipeline_live_read_model_v208_service as live_model
    from src.services import station_adapter_service as adapter
    from src.services import station_contract_service as contract
    from src.services import station_queue_service as station_queue
    from src.services import station_queue_worker_service as background_worker
    from src.services import station_registry_service as registry

    if getattr(live_model, "_V2161_AGENT_ENTRY_PROJECTION_INSTALLED", False):
        _INSTALLED = True
        base._INSTALLED = True
        return

    # station_queue_worker imported these functions/constants by value.
    background_worker.queue_summary = station_queue.queue_summary
    background_worker.runtime_governance_summary = governance.runtime_governance_summary
    background_worker.STATION_QUEUE_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    background_worker.STATION_QUEUE_WORKER_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    background_worker.AGENT_PIPELINE_GOVERNANCE_VERSION = (
        AGENT_ENTRY_INTERFACE_VERSION
    )

    original_worker_config = background_worker.worker_config

    def worker_config_v2161() -> Dict[str, Any]:
        result = original_worker_config()
        result.update(
            version=AGENT_ENTRY_INTERFACE_VERSION,
            governanceVersion=AGENT_ENTRY_INTERFACE_VERSION,
            queueVersion=AGENT_ENTRY_INTERFACE_VERSION,
            automaticAgentEntryOwner=AGENT_ENTRY_OWNER,
            preAgentQueueTerminalStation=PRE_AGENT_QUEUE_TERMINAL_STATION,
            agent1PendingRuntime="pipeline_items.agent1_pending",
            rule=(
                "The report queue ends at signal admission; one background "
                "pipeline-item worker advances Agent1 through Task Pool."
            ),
        )
        return result

    background_worker.worker_config = worker_config_v2161

    original_live_reader = live_model.read_pipeline_live_model

    def read_pipeline_live_model_v2161(
        data_version: str | None = None,
        *,
        limit: int = 80,
    ) -> Dict[str, Any]:
        result = original_live_reader(data_version=data_version, limit=limit)
        resolved = result.get("dataVersion") or data_version
        admission = _latest_admission_projection(resolved)
        result["version"] = AGENT_ENTRY_INTERFACE_VERSION
        result["agentEntryInterfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["automaticAgentEntryOwner"] = AGENT_ENTRY_OWNER
        result["preAgentQueueTerminalStation"] = PRE_AGENT_QUEUE_TERMINAL_STATION
        result["admission"] = admission
        if admission:
            summary = result.setdefault("summary", {})
            summary["qualifiedSignalCount"] = _safe_int(
                admission.get("qualifiedSignalCount")
            )
            summary["selectedRepresentativeCount"] = _safe_int(
                admission.get("selectedRepresentativeCount")
            )
            summary["agent1PendingItemCount"] = _safe_int(
                admission.get("agent1PendingItemCount")
            )
            budget = (
                admission.get("agentBudget")
                if isinstance(admission.get("agentBudget"), dict)
                else {}
            )
            summary["deferredQualifiedCount"] = _safe_int(
                budget.get("deferredQualifiedCount")
            )
            result["admissionHeadline"] = (
                f"信号{admission.get('fullSignalCount', 0)} · "
                f"可行动{admission.get('qualifiedSignalCount', 0)} · "
                f"进入Agent{admission.get('selectedRepresentativeCount', 0)} · "
                f"观察{admission.get('observedSignalCount', 0)}"
            )
        return result

    live_model.read_pipeline_live_model = read_pipeline_live_model_v2161
    live_model.PIPELINE_LIVE_READ_MODEL_VERSION = AGENT_ENTRY_INTERFACE_VERSION

    station_queue._V2161_AGENT_ENTRY_INSTALLED = True
    agent_worker._V2161_AGENT_ENTRY_INSTALLED = True
    contract._V2161_AGENT_ENTRY_INSTALLED = True
    adapter._V2161_AGENT_ENTRY_INSTALLED = True
    registry._V2161_AGENT_ENTRY_INSTALLED = True
    live_model._V2161_AGENT_ENTRY_INSTALLED = True
    live_model._V2161_AGENT_ENTRY_PROJECTION_INSTALLED = True
    background_worker._V2161_AGENT_ENTRY_INSTALLED = True
    base._INSTALLED = True
    _INSTALLED = True


__all__ = ["install_v2161_agent_entry_projection"]
