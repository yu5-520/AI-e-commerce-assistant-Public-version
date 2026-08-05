"""V21.7.7 component-version ownership finalizer.

The V21.6.1 Agent-entry installer intentionally replaces runtime callables and
public interfaces, but historical V21.3 scheduler/adapter/worker components keep
their own immutable component identities.  This finalizer restores those core
identities after all runtime overlays are installed and publishes the active
Agent-entry interface through separate metadata fields.

This module changes metadata ownership only.  It does not create a fallback
runtime, reroute tasks, replay Agents, or modify persistent data.
"""

from __future__ import annotations

from typing import Any, Dict

COMPONENT_VERSION_LAYERING_VERSION = "21.7.7"
AGENT_ENTRY_INTERFACE_VERSION = "21.6.1"
AGENT_PIPELINE_GOVERNANCE_CORE_VERSION = "21.3"
STATION_ADAPTER_CORE_VERSION = "21.3"
STATION_QUEUE_WORKER_CORE_VERSION = "21.3"


def install_v2177_component_version_layering() -> None:
    from src.services import agent_pipeline_governance_v213_service as governance
    from src.services import station_adapter_service as adapter
    from src.services import station_contract_service as contract
    from src.services import station_queue_worker_service as background_worker

    if getattr(governance, "_V2177_COMPONENT_VERSION_LAYERING_INSTALLED", False):
        return

    # Keep immutable component identity separate from the active interface.
    governance.AGENT_PIPELINE_GOVERNANCE_VERSION = (
        AGENT_PIPELINE_GOVERNANCE_CORE_VERSION
    )
    governance.AGENT_ENTRY_INTERFACE_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    governance.COMPONENT_VERSION_LAYERING_VERSION = (
        COMPONENT_VERSION_LAYERING_VERSION
    )

    adapter.STATION_ADAPTER_VERSION = STATION_ADAPTER_CORE_VERSION
    adapter.AGENT_PIPELINE_GOVERNANCE_VERSION = (
        AGENT_PIPELINE_GOVERNANCE_CORE_VERSION
    )
    adapter.AGENT_ENTRY_INTERFACE_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    adapter.COMPONENT_VERSION_LAYERING_VERSION = COMPONENT_VERSION_LAYERING_VERSION

    # station_contract imported adapter metadata by value; refresh that binding.
    contract.STATION_ADAPTER_VERSION = STATION_ADAPTER_CORE_VERSION
    contract.AGENT_ENTRY_INTERFACE_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    contract.COMPONENT_VERSION_LAYERING_VERSION = COMPONENT_VERSION_LAYERING_VERSION

    background_worker.STATION_QUEUE_WORKER_VERSION = (
        STATION_QUEUE_WORKER_CORE_VERSION
    )
    background_worker.AGENT_PIPELINE_GOVERNANCE_VERSION = (
        AGENT_PIPELINE_GOVERNANCE_CORE_VERSION
    )
    background_worker.AGENT_ENTRY_INTERFACE_VERSION = (
        AGENT_ENTRY_INTERFACE_VERSION
    )
    background_worker.COMPONENT_VERSION_LAYERING_VERSION = (
        COMPONENT_VERSION_LAYERING_VERSION
    )

    original_governance_summary = governance.runtime_governance_summary
    original_worker_config = background_worker.worker_config
    original_worker_status = background_worker.worker_status

    def runtime_governance_summary_layered() -> Dict[str, Any]:
        result = original_governance_summary()
        result["version"] = AGENT_ENTRY_INTERFACE_VERSION
        result["interfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["agentEntryInterfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["governanceCoreVersion"] = (
            AGENT_PIPELINE_GOVERNANCE_CORE_VERSION
        )
        result["componentVersionLayeringVersion"] = (
            COMPONENT_VERSION_LAYERING_VERSION
        )
        return result

    def worker_config_layered() -> Dict[str, Any]:
        result = original_worker_config()
        result["version"] = STATION_QUEUE_WORKER_CORE_VERSION
        result["governanceVersion"] = (
            AGENT_PIPELINE_GOVERNANCE_CORE_VERSION
        )
        result["interfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["agentEntryInterfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["componentVersionLayeringVersion"] = (
            COMPONENT_VERSION_LAYERING_VERSION
        )
        return result

    def worker_status_layered(include_queue: bool = True) -> Dict[str, Any]:
        result = original_worker_status(include_queue=include_queue)
        result["version"] = STATION_QUEUE_WORKER_CORE_VERSION
        result["governanceVersion"] = (
            AGENT_PIPELINE_GOVERNANCE_CORE_VERSION
        )
        result["interfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["agentEntryInterfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["componentVersionLayeringVersion"] = (
            COMPONENT_VERSION_LAYERING_VERSION
        )
        return result

    governance.runtime_governance_summary = runtime_governance_summary_layered
    background_worker.runtime_governance_summary = (
        runtime_governance_summary_layered
    )
    background_worker.worker_config = worker_config_layered
    background_worker.worker_status = worker_status_layered

    governance._V2177_COMPONENT_VERSION_LAYERING_INSTALLED = True
    adapter._V2177_COMPONENT_VERSION_LAYERING_INSTALLED = True
    contract._V2177_COMPONENT_VERSION_LAYERING_INSTALLED = True
    background_worker._V2177_COMPONENT_VERSION_LAYERING_INSTALLED = True


__all__ = [
    "COMPONENT_VERSION_LAYERING_VERSION",
    "AGENT_ENTRY_INTERFACE_VERSION",
    "AGENT_PIPELINE_GOVERNANCE_CORE_VERSION",
    "STATION_ADAPTER_CORE_VERSION",
    "STATION_QUEUE_WORKER_CORE_VERSION",
    "install_v2177_component_version_layering",
]
