from __future__ import annotations

from pathlib import Path

from src.services.agent2_hash_proof_bridge_v22515_service import (
    AGENT2_HASH_PROOF_BRIDGE_VERSION,
    hash_proof_provider_summary,
)
from src.services.agent2_provenance_v2141_service import (
    valid_agent2_execution_proof,
)
from src.services.station_agent_worker_v2259_service import (
    STATION_AGENT_WORKER_VERSION,
    worker_config,
)

ROOT = Path(__file__).resolve().parents[1]


def _proof() -> dict:
    return {
        "version": "22.5.15",
        "proofMode": "accepted_hash_execution_artifact",
        "stage": "action_plan_judgment_agent",
        "packageId": "PKG-001",
        "itemCorrelationId": "PKG-001",
        "semanticCallId": "A2HASH-0123456789ABCDEF",
        "providerRequestId": None,
        "providerCallExecuted": False,
        "exactReplayValidated": True,
        "replayFingerprint": "sha256:input",
        "resultMatched": True,
        "fallbackUsed": False,
        "passed": True,
        "executionHash": "0123456789abcdef",
        "itemExecutionId": "EXE-0123456789ABCDEF",
        "inputArtifactRef": "ART-INPUT",
        "inputContentHash": "sha256:input",
        "outputArtifactRef": "ART-OUTPUT",
        "outputContentHash": "sha256:output",
        "acceptedExecutionStatus": "accepted",
        "providerOriginNotReconstructed": True,
    }


def test_hash_bridge_proof_satisfies_existing_downstream_contract() -> None:
    proof = _proof()
    assert valid_agent2_execution_proof(proof) is True
    summary = hash_proof_provider_summary(proof)
    assert summary["itemProvenance"]["PKG-001"] == proof
    assert summary["hashDirectedExecution"] is True
    assert summary["batchCountersAcceptedAsItemProof"] is False
    assert summary["fallbackAllowed"] is False


def test_bridge_does_not_fabricate_provider_request_id() -> None:
    proof = _proof()
    assert proof["providerRequestId"] is None
    assert proof["providerCallExecuted"] is False
    assert proof["exactReplayValidated"] is True
    assert proof["providerOriginNotReconstructed"] is True


def test_active_worker_uses_v22515_hash_proof_authority() -> None:
    assert STATION_AGENT_WORKER_VERSION == "22.5.15"
    config = worker_config()
    assert config["hardAgentRuntimeVersion"] == "22.5.15"
    assert config["agent2EvidenceSliceVersion"] == "22.5.14"
    assert config["agent2HashProofBridgeVersion"] == "22.5.15"
    assert config["legacyItemProvenanceAuthority"] is False
    assert config["acceptedHashOutputBlindRetryAllowed"] is False
    assert config["providerRequestIdReconstructionAllowed"] is False
    assert config["secondWorkerAllowed"] is False


def test_runtime_reads_hash_execution_before_old_provenance() -> None:
    source = (
        ROOT / "src/services/agent2_runtime_v22515_service.py"
    ).read_text(encoding="utf-8")
    assert "bridge_agent2_hash_proof" in source
    assert "artifact_execution_index_v2259" in (
        ROOT / "src/services/agent2_hash_proof_bridge_v22515_service.py"
    ).read_text(encoding="utf-8")
    assert "proof_for_package" in source
    assert source.index("_bridge_candidate(") < source.index("proof_for_package(provider")
    assert "agent2_draft_item_provenance_missing" in source
    assert "reconcile_agent2_hash_proof_dead_letters_v22515" in source
    assert '"providerCallsExecuted": 0' in source


def test_hard_runtime_recovers_dead_letters_before_selection() -> None:
    source = (
        ROOT / "src/services/agent_runtime_hard_interface_v22515_service.py"
    ).read_text(encoding="utf-8")
    assert "hashProofDeadLetters" in source
    assert "reconcile_agent2_hash_proof_dead_letters_v22515" in source
    assert "_recover_agent2(None)" in source
    assert "before_selection_and_startup" in source
    assert AGENT2_HASH_PROOF_BRIDGE_VERSION == "22.5.15"
