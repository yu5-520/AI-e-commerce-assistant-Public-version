"""V22.5.15 binding for the existing single station worker.

V23.3 Runtime Generation Barrier keeps the existing one-thread/one-queue ownership
model and serializes each complete worker iteration against demo Reset. A Reset waits
for any in-flight Provider/station iteration to finish before rotating the generation
and deleting mutable runtime state, so an old Agent result cannot repopulate the new
empty generation.
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
from src.services.runtime_generation_barrier_v1_service import (
    RUNTIME_GENERATION_VERSION,
    current_runtime_generation,
    ensure_runtime_generation_state,
    mark_runtime_generation_active,
    runtime_execution_guard,
)

THREE_AGENT_PIPELINE_VERSION = legacy.THREE_AGENT_PIPELINE_VERSION
STATION_AGENT_WORKER_VERSION = "22.5.15"

# The legacy worker resolves these names from its module globals at tick time.
# Rebinding them before start preserves one thread and one state owner.
legacy.run_agent_pipeline_tick_hard = run_agent_pipeline_tick_hard
legacy.select_runnable_data_version_v225 = select_runnable_data_version_v225
_original_run_one = legacy._run_one


def _selected_data_version(result: Dict[str, Any]) -> str | None:
    inner = result.get("result") if isinstance(result.get("result"), dict) else {}
    for value in (
        inner.get("selectedDataVersion"),
        inner.get("dataVersion"),
        result.get("selectedDataVersion"),
        result.get("dataVersion"),
    ):
        if value:
            return str(value)
    return None


def _competition_run_one(worker_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    """Run one complete iteration under the same exclusive barrier used by Reset."""

    with runtime_execution_guard(f"station_worker:{worker_id}") as generation:
        handoff = seed_ready_competition_handoffs(
            limit_versions=max(1, min(8, int(config.get("maxJobsPerTick") or 4))),
        )
        result = _original_run_one(worker_id, config)
        selected = _selected_data_version(result if isinstance(result, dict) else {})
        if selected:
            mark_runtime_generation_active(selected)

    if isinstance(result, dict):
        result["competitionSignalHandoff"] = handoff
        result["competitionLegacyStationQueueCriticalPath"] = False
        result["runtimeGeneration"] = {
            "version": RUNTIME_GENERATION_VERSION,
            "generationSeq": generation.get("generationSeq"),
            "generationHash": generation.get("generationHash"),
            "claimGenerationHash": generation.get("claimGenerationHash"),
            "commitGenerationHash": generation.get("commitGenerationHash"),
            "generationMatch": generation.get("generationMatch") is True,
            "state": current_runtime_generation().get("state"),
        }
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
        runtimeGenerationBarrierVersion=RUNTIME_GENERATION_VERSION,
        runtimeGeneration=current_runtime_generation(),
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
        runtimeResetConcurrency=(
            "one_complete_worker_iteration_and_reset_share_exclusive_generation_barrier"
        ),
        oldGenerationWriteAllowed=False,
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
        runtimeGenerationBarrierVersion=RUNTIME_GENERATION_VERSION,
        runtimeGeneration=current_runtime_generation(),
        activeAgent1RuntimeBinding=active_agent1_runtime_binding(),
        config=worker_config(),
        executionMode=(
            "agent1_full_audit_then_agent2_evidence_slice_then_hash_proof"
        ),
        competitionSignalHandoff="registered_signalRef_to_agent1_pending_v1",
        competitionLegacyStationQueueCriticalPath=False,
        oldGenerationWriteAllowed=False,
        secondWorkerAllowed=False,
        fallbackAllowed=False,
    )
    return result


def start_station_queue_worker(
    worker_id: str = "fastapi-three-agent-worker",
) -> Dict[str, Any]:
    assert_active_agent1_runtime_binding()
    ensure_runtime_generation_state()
    with runtime_execution_guard(f"worker_start_seed:{worker_id}"):
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
    ensure_runtime_generation_state()
    # Every actual legacy _run_one call is already rebound to _competition_run_one,
    # so manual ticks use the same barrier as the background thread.
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
