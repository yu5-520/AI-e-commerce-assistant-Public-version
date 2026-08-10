"""V22.5.15 metadata facade for the single hash-directed station worker.

No second queue, thread or state machine is created here.  The active worker keeps
V22.5.9 exact Artifact-hash execution, V22.5.14 Agent2 evidence slices and adds the
V22.5.15 accepted-output hash-proof bridge.  The V22.5.15 binding owns the registered
competition signal handoff inside the existing worker loop; this facade only exposes
that state and delegates execution.
"""
from __future__ import annotations

from typing import Any, Dict

from src.services import station_agent_worker_v22515_service as legacy

THREE_AGENT_PIPELINE_VERSION = legacy.THREE_AGENT_PIPELINE_VERSION
STATION_AGENT_WORKER_VERSION = "22.5.15"


def _upgrade(value: Any) -> Any:
    if isinstance(value, dict):
        result = {key: _upgrade(child) for key, child in value.items()}
        if "version" in result and str(result.get("version") or "").startswith("22.5."):
            result["version"] = STATION_AGENT_WORKER_VERSION
        if "hardAgentRuntimeVersion" in result:
            result["hardAgentRuntimeVersion"] = STATION_AGENT_WORKER_VERSION
        if "agent1InputProjectionVersion" in result:
            result["agent1InputProjectionVersion"] = "22.5.8"
        if "agent2EvidenceSliceVersion" in result:
            result["agent2EvidenceSliceVersion"] = "22.5.14"
        if "agent2HashProofBridgeVersion" in result:
            result["agent2HashProofBridgeVersion"] = "22.5.15"
        if "executionMode" in result:
            result["executionMode"] = (
                "hash_directed_agent1_audit_then_agent2_evidence_slice_then_hash_proof"
            )
        return result
    if isinstance(value, list):
        return [_upgrade(item) for item in value]
    return value


def worker_config() -> Dict[str, Any]:
    result = _upgrade(legacy.worker_config())
    result.update(
        version=STATION_AGENT_WORKER_VERSION,
        hardAgentRuntimeVersion=STATION_AGENT_WORKER_VERSION,
        agent1InputProjectionVersion="22.5.8",
        agent2EvidenceSliceVersion="22.5.14",
        agent2HashProofBridgeVersion="22.5.15",
        agent1BatchSize=int(result.get("agent1BatchSize") or 8),
        dataVersionSelection=(
            "oldest_highest_priority_after_registered_signal_handoff_and_agent2_hash_reconciliation_v22515"
        ),
        agent1RuntimeSource="artifactRefs.agent1InputRef.v3+inputContentHash",
        agent2RuntimeSource=(
            "artifactRefs.agent2DraftInputRef.v22514+acceptedHashOutput.v22515"
        ),
        agent3RuntimeSource="artifactRefs.agent3SopInputRef+inputContentHash",
        agent2ProofAuthority=(
            "artifact_execution_index_v2259+accepted_output_artifact"
        ),
        executionMode=(
            "hash_directed_agent1_audit_then_agent2_evidence_slice_then_hash_proof"
        ),
        executionIndex="artifact_execution_index_v2259",
        batchManifestContract="agent_batch_manifest.v2259",
        frontendViewMode="manifestHash_plus_moduleContentHash",
        agent2StaleLeaseRecovery="before_selection_and_startup",
        agent2HashProofDeadLetterRecovery="before_selection_and_startup",
        agent2ReceivesActionEvidenceSliceOnly=True,
        fullReportReadByAgent2Allowed=False,
        legacyItemProvenanceAuthority=False,
        acceptedHashOutputBlindRetryAllowed=False,
        providerRequestIdReconstructionAllowed=False,
        cachedOutputRebindingAllowed=False,
        competitionSignalHandoff="registered_signalRef_to_agent1_pending_v1",
        competitionLegacyStationQueueCriticalPath=False,
        competitionHandoffRegeneratesIdentity=False,
        competitionHandoffProviderCalls=False,
        secondWorkerAllowed=False,
        fallbackAllowed=False,
    )
    return result


def worker_status(include_queue: bool = True) -> Dict[str, Any]:
    result = _upgrade(legacy.worker_status(include_queue=include_queue))
    result.update(
        version=STATION_AGENT_WORKER_VERSION,
        hardAgentRuntimeVersion=STATION_AGENT_WORKER_VERSION,
        agent2EvidenceSliceVersion="22.5.14",
        agent2HashProofBridgeVersion="22.5.15",
        config=worker_config(),
        executionMode=(
            "hash_directed_agent1_audit_then_agent2_evidence_slice_then_hash_proof"
        ),
        executionIndex="artifact_execution_index_v2259",
        competitionSignalHandoff="registered_signalRef_to_agent1_pending_v1",
        competitionLegacyStationQueueCriticalPath=False,
        secondWorkerAllowed=False,
        fallbackAllowed=False,
    )
    return result


def start_station_queue_worker(
    worker_id: str = "fastapi-three-agent-worker",
) -> Dict[str, Any]:
    legacy.start_station_queue_worker(worker_id=worker_id)
    return worker_status(include_queue=False)


def stop_station_queue_worker() -> Dict[str, Any]:
    legacy.stop_station_queue_worker()
    return worker_status(include_queue=False)


def run_worker_tick(
    worker_id: str = "manual-three-agent-tick",
    limit: int | None = None,
) -> Dict[str, Any]:
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
