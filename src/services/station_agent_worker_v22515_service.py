"""V22.5.15 binding for the existing single station worker.

This module does not create a second thread or queue. It replaces runtime callables
imported by the V22.5.14 worker before that worker is started, then reuses the same
state, loop and lifecycle owner.  Competition mode also injects the registered
formal-signal handoff into that same loop, so signalRef reaches Agent1 without making
the legacy station queue a critical dependency.
"""
from __future__ import annotations

from typing import Any, Dict

from src.services import station_agent_worker_v22514_service as legacy
from src.services.agent_runtime_hard_interface_v22515_service import (
    AGENT_RUNTIME_HARD_INTERFACE_VERSION,
    active_agent1_runtime_binding,
    assert_active_agent1_runtime_binding,
    run_agent_pipeline_tick_hard,
    select_runnable_data_version_v225,
)
from src.services.competition_signal_handoff_service import (
    seed_ready_competition_handoffs,
)

THREE_AGENT_PIPELINE_VERSION = legacy.THREE_AGENT_PIPELINE_VERSION
STATION_AGENT_WORKER_VERSION = "22.5.15"

# The legacy worker resolves these names from its module globals at tick time.
# Rebinding them before start preserves one thread and one state owner.
legacy.run_agent_pipeline_tick_hard = run_agent_pipeline_tick_hard
legacy.select_runnable_data_version_v225 = select_runnable_data_version_v225
_original_run_one = legacy._run_one


def _competition_run_one(worker_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Seed exact formal signalRefs, then delegate to the existing single worker."""

    handoff = seed_ready_competition_handoffs(
        limit_versions=max(1, min(8, int(config.get("maxJobsPerTick") or 4))),
    )
    result = _original_run_one(worker_id, config)
    if isinstance(result, dict):
        result["competitionSignalHandoff"] = handoff
        result["competitionLegacyStationQueueCriticalPath"] = False
    return result


legacy._run_one = _competition_run_one


def _upgrade(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _upgrade(child) for key, child in value.items()}
        if "version" in result and str(result.get("version") or "").startswith("22.5."):
            result["version"] = STATION_AGENT_WORKER_VERSION
        if "hardAgentRuntimeVersion" in result:
            result["hardAgentRuntimeVersion"] = AGENT_RUNTIME_HARD_INTERFACE_VERSION
        if "agent2EvidenceSliceVersion" in result:
            result["agent2EvidenceSliceVersion"] = "22.5.14"
        if "agent2HashProofBridgeVersion" in result:
            result["agent2HashProofBridgeVersion"] = "22.5.15"
        if "executionMode" in result:
            result["executionMode"] = (
                "agent1_full_audit_then_agent2_evidence_slice_then_hash_proof"
            )
        return result
    if isinstance(value, list):
        return [_upgrade(item) for item in value]
    return value


def worker_config() -> Dict[str, Any]:
    result = _upgrade(legacy.worker_config())
    result.update(
        version=STATION_AGENT_WORKER_VERSION,
        hardAgentRuntimeVersion=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        agent2EvidenceSliceVersion="22.5.14",
        agent2HashProofBridgeVersion="22.5.15",
        activeAgent1RuntimeBinding=active_agent1_runtime_binding(),
        dataVersionSelection=(
            "oldest_highest_priority_after_registered_signal_handoff_and_agent2_hash_reconciliation"
        ),
        competitionSignalHandoff="registered_signalRef_to_agent1_pending_v1",
        competitionLegacyStationQueueCriticalPath=False,
        agent2RuntimeSource=(
            "agent2DraftInputRef.v22514+acceptedHashOutput.v22515"
        ),
        agent2ProofAuthority=(
            "artifact_execution_index_v2259+accepted_output_artifact"
        ),
        legacyItemProvenanceAuthority=False,
        acceptedHashOutputBlindRetryAllowed=False,
        providerRequestIdReconstructionAllowed=False,
        agent2HashProofDeadLetterRecovery="before_selection_and_startup",
        executionMode=(
            "agent1_full_audit_then_agent2_evidence_slice_then_hash_proof"
        ),
        secondWorkerAllowed=False,
        fallbackAllowed=False,
    )
    return result


def worker_status(include_queue: bool = True) -> Dict[str, Any]:
    result = _upgrade(legacy.worker_status(include_queue=include_queue))
    result.update(
        version=STATION_AGENT_WORKER_VERSION,
        hardAgentRuntimeVersion=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        agent2EvidenceSliceVersion="22.5.14",
        agent2HashProofBridgeVersion="22.5.15",
        activeAgent1RuntimeBinding=active_agent1_runtime_binding(),
        config=worker_config(),
        executionMode=(
            "agent1_full_audit_then_agent2_evidence_slice_then_hash_proof"
        ),
        competitionSignalHandoff="registered_signalRef_to_agent1_pending_v1",
        competitionLegacyStationQueueCriticalPath=False,
        secondWorkerAllowed=False,
        fallbackAllowed=False,
    )
    return result


def start_station_queue_worker(
    worker_id: str = "fastapi-three-agent-worker",
) -> Dict[str, Any]:
    assert_active_agent1_runtime_binding()
    seed_ready_competition_handoffs(limit_versions=8)
    legacy.start_station_queue_worker(worker_id=worker_id)
    return worker_status(include_queue=False)


def stop_station_queue_worker() -> Dict[str, Any]:
    legacy.stop_station_queue_worker()
    return worker_status(include_queue=False)


def run_worker_tick(
    worker_id: str = "manual-three-agent-tick",
    limit: int | None = None,
) -> Dict[str, Any]:
    assert_active_agent1_runtime_binding()
    seed_ready_competition_handoffs(
        limit_versions=max(1, min(8, int(limit or 4))),
    )
    return _upgrade(
        legacy.run_worker_tick(
            worker_id=worker_id,
            limit=limit,
        )
    )


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "STATION_AGENT_WORKER_VERSION",
    "worker_config",
    "worker_status",
    "start_station_queue_worker",
    "stop_station_queue_worker",
    "run_worker_tick",
]
