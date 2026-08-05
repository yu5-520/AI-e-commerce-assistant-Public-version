"""V22.5.15 hard runtime facade for Agent2 hash-proof authority.

V23.1.4 adds one read-only proof of the actual station-worker Agent1 callable chain.
The proof is checked before recovery, database preparation, worker execution or Provider
calls.  Agent2, Agent3, task mapping and task-pool owners remain unchanged.
"""
from __future__ import annotations

from typing import Any, Dict

from src.services import agent_runtime_hard_interface_v22514_service as legacy
from src.services.agent2_runtime_resilience_v2143_service import (
    recover_stale_agent2_claims,
)
from src.services.agent2_runtime_v22515_service import (
    migrate_agent2_projection_failures_v22514,
    reconcile_agent2_hash_proof_dead_letters_v22515,
    run_agent2_draft_microbatch_hard,
)

AGENT_RUNTIME_HARD_INTERFACE_VERSION = "22.5.15"
THREE_AGENT_PIPELINE_VERSION = legacy.THREE_AGENT_PIPELINE_VERSION
EXECUTION_LOCK_CONTRACT = legacy.EXECUTION_LOCK_CONTRACT
AGENT2_EVIDENCE_SLICE_VERSION = "22.5.14"
AGENT2_HASH_PROOF_BRIDGE_VERSION = "22.5.15"
ACTIVE_AGENT1_BINDING_VERSION = "23.1.4"

_EXPECTED_AGENT1_OWNERS = {
    "activeFacadeOwner": "src.services.agent_runtime_hard_interface_v22515_service",
    "agent1StageOwner": "src.services.agent_runtime_hard_interface_v2257_service",
    "inputContractOwner": "src.services.agent_input_contract_v2258_service",
    "inputTransportOwner": "src.services.agent_input_transport_v2258_service",
    "inputResolverOwner": "src.services.agent_input_transport_v2258_service",
    "tokenRuntimeOwner": "src.services.agent_token_runtime_hash_exact_v2259_service",
    "stationWorkerFacade": "src.services.station_agent_worker_v2259_service",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def active_agent1_runtime_binding() -> Dict[str, Any]:
    """Return the exact active Agent1 owners without touching data or a Provider."""

    agent1_stage = legacy.legacy
    actual = {
        "activeFacadeOwner": __name__,
        "agent1StageOwner": getattr(
            agent1_stage.run_agent1_microbatch_hard,
            "__module__",
            "",
        ),
        "inputContractOwner": getattr(
            agent1_stage.AGENT1_INPUT_SCHEMA,
            "__module__",
            "",
        )
        or "src.services.agent_input_contract_v2258_service",
        "inputTransportOwner": getattr(
            agent1_stage.ensure_agent1_input_ref,
            "__module__",
            "",
        ),
        "inputResolverOwner": getattr(
            agent1_stage.resolve_agent_input_ref,
            "__module__",
            "",
        ),
        "tokenRuntimeOwner": getattr(
            agent1_stage.run_agent1_projected_inputs,
            "__module__",
            "",
        ),
        "stationWorkerFacade": "src.services.station_agent_worker_v2259_service",
    }
    # AGENT1_INPUT_SCHEMA is a string, so its owning module is proved by the stage
    # import contract and exposed explicitly rather than inferred from the value.
    actual["inputContractOwner"] = "src.services.agent_input_contract_v2258_service"
    matched = all(
        actual.get(key) == expected
        for key, expected in _EXPECTED_AGENT1_OWNERS.items()
    )
    return {
        "schema": "runtime.active_agent1_binding.v1",
        "version": ACTIVE_AGENT1_BINDING_VERSION,
        **actual,
        "expectedOwners": dict(_EXPECTED_AGENT1_OWNERS),
        "matched": matched,
        "databaseMutated": False,
        "providerCallsExecuted": 0,
        "secondWorkerCreated": False,
        "runtimeChain": [
            "src.services.station_agent_worker_v2259_service",
            "src.services.station_agent_worker_v22515_service",
            "src.services.agent_runtime_hard_interface_v22515_service",
            "src.services.agent_runtime_hard_interface_v22514_service",
            "src.services.agent_runtime_hard_interface_v2257_service",
            "src.services.agent_input_transport_v2258_service",
            "src.services.agent_token_runtime_hash_exact_v2259_service",
        ],
    }


def assert_active_agent1_runtime_binding() -> Dict[str, Any]:
    binding = active_agent1_runtime_binding()
    if binding.get("matched") is not True:
        raise RuntimeError(
            "active_agent1_runtime_binding_mismatch:"
            + str(
                {
                    key: binding.get(key)
                    for key in _EXPECTED_AGENT1_OWNERS
                }
            )
        )
    if binding.get("databaseMutated") is not False:
        raise RuntimeError("active_agent1_binding_probe_mutated_database")
    if int(binding.get("providerCallsExecuted") or 0) != 0:
        raise RuntimeError("active_agent1_binding_probe_called_provider")
    if binding.get("secondWorkerCreated") is not False:
        raise RuntimeError("active_agent1_binding_probe_created_second_worker")
    return binding


def _recover_agent2(data_version: str | None) -> Dict[str, Any]:
    return {
        "staleRunning": recover_stale_agent2_claims(
            data_version,
            limit=500,
        ),
        "projectionFailures": migrate_agent2_projection_failures_v22514(
            data_version,
            limit=500,
        ),
        "hashProofDeadLetters": (
            reconcile_agent2_hash_proof_dead_letters_v22515(
                data_version,
                limit=500,
            )
        ),
    }


def select_runnable_data_version_v225(
    preferred: str | None = None,
) -> str | None:
    assert_active_agent1_runtime_binding()
    # Proof-misclassified dead letters must be promoted to agent2_draft_ready before
    # runnable selection; otherwise the selector cannot see the Agent3 work they create.
    _recover_agent2(None)
    return legacy.legacy.select_runnable_data_version_v225(preferred)


def _augment(
    value: Dict[str, Any],
    *,
    recovery: Dict[str, Any],
    data_version: str | None,
) -> Dict[str, Any]:
    result = dict(value)
    result.update(
        version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        threeAgentPipelineVersion=THREE_AGENT_PIPELINE_VERSION,
        activeAgent1RuntimeBinding=active_agent1_runtime_binding(),
        agent2EvidenceSliceVersion=AGENT2_EVIDENCE_SLICE_VERSION,
        agent2HashProofBridgeVersion=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        agent2StaleRunningRecovery=recovery.get("staleRunning") or {},
        agent2ProjectionFailureRecovery=(
            recovery.get("projectionFailures") or {}
        ),
        agent2HashProofDeadLetterRecovery=(
            recovery.get("hashProofDeadLetters") or {}
        ),
        dataVersion=result.get("dataVersion") or data_version,
        agent2RuntimeSource=(
            "agent2DraftInputRef.v22514+acceptedHashOutput.v22515"
        ),
        agent2ProofAuthority=(
            "artifact_execution_index_v2259+accepted_output_artifact"
        ),
        executionMode=(
            "agent1_full_audit_then_agent2_evidence_slice_then_hash_proof"
        ),
        fallbackAllowed=False,
    )
    return result


def run_agent_pipeline_tick_hard(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    worker_id: str | None = None,
    agent1_batch_size: int = 8,
    action_pack_batch_size: int = 8,
    agent2_batch_size: int = 5,
    agent3_batch_size: int = 2,
    mapping_batch_size: int = 8,
    pool_batch_size: int = 8,
    force_new_snapshot: bool = False,
    **kwargs: Any,
) -> Dict[str, Any]:
    binding = assert_active_agent1_runtime_binding()
    from src.services.agent_runtime_hard_interface_v2255_service import (
        _refresh_read_models,
    )
    from src.services.pipeline_action_microbatch_v205_service import (
        pending_agent2_item_count,
    )
    from src.services.pipeline_agent3_sop_v225_service import (
        pending_agent3_sop_item_count,
    )
    from src.services.pipeline_task_mapping_v225_service import (
        pending_task_mapping_item_count,
        pending_task_pool_item_count,
    )

    recovery = _recover_agent2(data_version)
    resolved = data_version or legacy.legacy.select_runnable_data_version_v225()
    if not resolved:
        recovered_count = sum(
            int(_dict(recovery.get(key)).get("recoveredItemCount") or 0)
            for key in (
                "staleRunning",
                "projectionFailures",
                "hashProofDeadLetters",
            )
        )
        return _augment(
            {
                "ran": recovered_count > 0,
                "reason": "no_runnable_agent_pipeline_items",
                "selectedStage": "agent2_recovery_only",
                "dataVersion": data_version,
                "activeAgent1RuntimeBinding": binding,
            },
            recovery=recovery,
            data_version=data_version,
        )

    higher_priority_pending = any(
        [
            pending_task_pool_item_count(resolved) > 0,
            pending_task_mapping_item_count(resolved) > 0,
            pending_agent3_sop_item_count(resolved) > 0,
        ]
    )
    if not higher_priority_pending and pending_agent2_item_count(resolved) > 0:
        stage_result = run_agent2_draft_microbatch_hard(
            resolved,
            user_id=user_id,
            batch_size=agent2_batch_size,
        )
        output = {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
            "ran": bool(stage_result.get("ran")),
            "workerId": worker_id,
            "selectedStage": (
                "agent2DraftInputRef.v22514_to_hashAcceptedDraft.v22515"
            ),
            "dataVersion": resolved,
            "result": stage_result,
            "runtimeSource": (
                "agent2DraftInputRef.v22514+acceptedHashOutput.v22515"
            ),
            "executionLockContract": EXECUTION_LOCK_CONTRACT,
            "activeAgent1RuntimeBinding": binding,
            "fallbackAllowed": False,
        }
        _refresh_read_models(output, resolved)
        return _augment(
            output,
            recovery=recovery,
            data_version=resolved,
        )

    delegated = legacy.run_agent_pipeline_tick_hard(
        data_version=resolved,
        user_id=user_id,
        worker_id=worker_id,
        agent1_batch_size=agent1_batch_size,
        action_pack_batch_size=action_pack_batch_size,
        agent2_batch_size=agent2_batch_size,
        agent3_batch_size=agent3_batch_size,
        mapping_batch_size=mapping_batch_size,
        pool_batch_size=pool_batch_size,
        force_new_snapshot=force_new_snapshot,
        **kwargs,
    )
    delegated["activeAgent1RuntimeBinding"] = binding
    return _augment(
        delegated,
        recovery=recovery,
        data_version=resolved,
    )


def startup_agent_runtime_hard() -> Dict[str, Any]:
    binding = assert_active_agent1_runtime_binding()
    recovery = _recover_agent2(None)
    result = legacy.startup_agent_runtime_hard()
    result["activeAgent1RuntimeBinding"] = binding
    return _augment(
        result,
        recovery=recovery,
        data_version=result.get("dataVersion"),
    )


def agent_runtime_hard_interface_status() -> Dict[str, Any]:
    binding = active_agent1_runtime_binding()
    result = legacy.agent_runtime_hard_interface_status()
    result.update(
        version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        activeAgent1RuntimeBinding=binding,
        inputTransportOwner=binding.get("inputTransportOwner"),
        tokenRuntimeOwner=binding.get("tokenRuntimeOwner"),
        agent2EvidenceSliceVersion=AGENT2_EVIDENCE_SLICE_VERSION,
        agent2HashProofBridgeVersion=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        agent2RuntimeSource=(
            "agent2DraftInputRef.v22514+acceptedHashOutput.v22515"
        ),
        agent2ProofAuthority=(
            "artifact_execution_index_v2259+accepted_output_artifact"
        ),
        legacyItemProvenanceAuthority=False,
        acceptedHashOutputBlindRetryAllowed=False,
        providerRequestIdReconstructionAllowed=False,
        agent1FullDiagnosisAuditOnly=True,
        agent2ReceivesActionEvidenceSliceOnly=True,
        fullReportReadByAgent2Allowed=False,
        rawAgent1OutputReadByAgent2Allowed=False,
        agent2StaleLeaseRecovery="before_selection_and_startup",
        agent2HashProofDeadLetterRecovery="before_selection_and_startup",
        executionMode=(
            "agent1_full_audit_then_agent2_evidence_slice_then_hash_proof"
        ),
        fallbackAllowed=False,
    )
    return result


def run_agent1_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
) -> Dict[str, Any]:
    assert_active_agent1_runtime_binding()
    return legacy.run_agent1_microbatch_hard(
        data_version,
        user_id=user_id,
        batch_size=batch_size,
    )


run_agent2_microbatch_hard = run_agent2_draft_microbatch_hard
migrate_legacy_agent2_outputs = legacy.migrate_legacy_agent2_outputs
migrate_misclassified_agent2_input_failures = (
    migrate_agent2_projection_failures_v22514
)


__all__ = [
    "AGENT_RUNTIME_HARD_INTERFACE_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "EXECUTION_LOCK_CONTRACT",
    "AGENT2_EVIDENCE_SLICE_VERSION",
    "AGENT2_HASH_PROOF_BRIDGE_VERSION",
    "ACTIVE_AGENT1_BINDING_VERSION",
    "active_agent1_runtime_binding",
    "assert_active_agent1_runtime_binding",
    "run_agent1_microbatch_hard",
    "run_agent2_draft_microbatch_hard",
    "run_agent2_microbatch_hard",
    "run_agent_pipeline_tick_hard",
    "select_runnable_data_version_v225",
    "startup_agent_runtime_hard",
    "agent_runtime_hard_interface_status",
    "migrate_legacy_agent2_outputs",
    "migrate_misclassified_agent2_input_failures",
]
