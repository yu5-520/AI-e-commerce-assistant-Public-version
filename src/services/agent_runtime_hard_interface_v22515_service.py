"""V22.5.15 hard runtime facade for Agent2 hash-proof authority.

V23.1.5 adds a competition-only Agent1 ready-first claim policy inside this already
registered active facade. The Agent1 stage owner remains V22.5.7 and the Provider
execution authority remains the V22.5.9 exact Artifact-hash token runtime. No second
Worker, queue, runtime owner or cache-rebinding path is introduced.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.services import agent_runtime_hard_interface_v22514_service as legacy
from src.services import pipeline_agent1_microbatch_v20101_service as pipeline_agent1_core
from src.services.agent_input_contract_v2258_service import (
    AGENT1_INPUT_PROJECTION_VERSION,
    AGENT1_INPUT_SCHEMA,
    AGENT1_MAX_BATCH_CHARS,
)
from src.services.agent_input_transport_v2258_service import (
    ensure_agent1_input_ref,
    resolve_agent_input_ref,
)
from src.services.agent2_runtime_resilience_v2143_service import (
    recover_stale_agent2_claims,
)
from src.services.agent2_runtime_v22515_service import (
    migrate_agent2_projection_failures_v22514,
    reconcile_agent2_hash_proof_dead_letters_v22515,
    run_agent2_draft_microbatch_hard,
)
from src.services.operating_policy_context_v2028_service import (
    build_operating_policy_context,
)

AGENT_RUNTIME_HARD_INTERFACE_VERSION = "22.5.15"
THREE_AGENT_PIPELINE_VERSION = legacy.THREE_AGENT_PIPELINE_VERSION
EXECUTION_LOCK_CONTRACT = legacy.EXECUTION_LOCK_CONTRACT
AGENT2_EVIDENCE_SLICE_VERSION = "22.5.14"
AGENT2_HASH_PROOF_BRIDGE_VERSION = "22.5.15"
ACTIVE_AGENT1_BINDING_VERSION = "23.1.4"
AGENT1_READY_FIRST_RUNTIME_VERSION = "23.1.5"
AGENT1_READY_FIRST_POLICY = "ready_first_dynamic_char_budget"

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


def _agent1_capacity(batch_size: int | None) -> int:
    try:
        value = int(batch_size or 8)
    except Exception:
        value = 8
    return max(1, min(20, value))


def _agent1_item_id(item: Dict[str, Any]) -> str:
    return str(item.get("item_id") or item.get("itemId") or "")


def plan_agent1_ready_first_batch(
    data_version: str | None,
    *,
    batch_size: int = 8,
) -> Dict[str, Any]:
    """Plan the first immediately executable Agent1 prefix without claiming it.

    The configured item count remains only a capacity ceiling. Deterministic Agent1
    input Artifacts may be materialized during this local preflight; no Provider call is
    made. The selected prefix is capped by the existing V22.5.8 batch char budget.
    """

    capacity = _agent1_capacity(batch_size)
    candidates = pipeline_agent1_core._pending_items(data_version, capacity)
    pending_count = pipeline_agent1_core.pending_agent1_item_count(data_version)
    if not candidates:
        return {
            "version": AGENT1_READY_FIRST_RUNTIME_VERSION,
            "policy": AGENT1_READY_FIRST_POLICY,
            "dataVersion": data_version,
            "capacityItems": capacity,
            "candidateWindowCount": 0,
            "selectedItemCount": 0,
            "selectedProjectedChars": 0,
            "batchCharBudget": AGENT1_MAX_BATCH_CHARS,
            "remainingReadyCount": pending_count,
            "selectedItemIds": [],
            "prepareFailureItemIds": [],
            "providerCallsExecuted": 0,
            "claimScope": "none",
            "waitForFullCapacity": False,
            "reason": "no_pending_agent1_items",
        }

    policy = {
        **build_operating_policy_context(),
        "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
    }
    selected_count = 0
    selected_chars = 0
    selected_ids: List[str] = []
    prepare_failure_ids: List[str] = []
    inspected: List[Dict[str, Any]] = []

    for item in candidates:
        item_id = _agent1_item_id(item)
        try:
            input_ref = ensure_agent1_input_ref(item, policy_context=policy)
            envelope = resolve_agent_input_ref(
                input_ref,
                expected_schema=AGENT1_INPUT_SCHEMA,
            )
            projected_chars = int(
                (envelope.get("projectionAudit") or {}).get("projectedChars") or 0
            )
        except Exception as exc:
            # Keep preparation failures in the executable prefix so the existing hard
            # runtime persists the precise failure instead of starving the row pending.
            selected_count += 1
            selected_ids.append(item_id)
            prepare_failure_ids.append(item_id)
            inspected.append(
                {
                    "itemId": item_id,
                    "projectedChars": None,
                    "selected": True,
                    "prepareStatus": "failed",
                    "prepareError": str(exc)[:300],
                }
            )
            if selected_count >= capacity:
                break
            continue

        if selected_count > 0 and selected_chars + projected_chars > AGENT1_MAX_BATCH_CHARS:
            inspected.append(
                {
                    "itemId": item_id,
                    "projectedChars": projected_chars,
                    "selected": False,
                    "prepareStatus": "ready",
                    "stopReason": "batch_char_budget_would_be_exceeded",
                }
            )
            break

        selected_count += 1
        selected_chars += projected_chars
        selected_ids.append(item_id)
        inspected.append(
            {
                "itemId": item_id,
                "projectedChars": projected_chars,
                "selected": True,
                "prepareStatus": "ready",
            }
        )
        if selected_count >= capacity:
            break

    if selected_count <= 0:
        selected_count = 1
        selected_ids = [_agent1_item_id(candidates[0])]

    return {
        "version": AGENT1_READY_FIRST_RUNTIME_VERSION,
        "policy": AGENT1_READY_FIRST_POLICY,
        "dataVersion": data_version,
        "capacityItems": capacity,
        "candidateWindowCount": len(candidates),
        "selectedItemCount": selected_count,
        "selectedProjectedChars": selected_chars,
        "batchCharBudget": AGENT1_MAX_BATCH_CHARS,
        "remainingReadyCount": max(0, pending_count - selected_count),
        "selectedItemIds": selected_ids,
        "prepareFailureItemIds": prepare_failure_ids,
        "inspectedPrefix": inspected,
        "providerCallsExecuted": 0,
        "claimScope": "current_provider_subbatch_only",
        "capacityIsMinimum": False,
        "waitForFullCapacity": False,
    }


def run_agent1_ready_first_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
) -> Dict[str, Any]:
    """Run one immediately available Agent1 Provider-sized prefix.

    The delegated registered hard runtime still owns leases, exact Artifact execution,
    Provider invocation, normalization, execution-lock validation, observation routing
    and read-model refresh. Only the outer claim batch size is reduced to the first
    char-budget prefix, so not-yet-executing items remain ``agent1_pending``.
    """

    plan = plan_agent1_ready_first_batch(data_version, batch_size=batch_size)
    selected = int(plan.get("selectedItemCount") or 0)
    effective_batch_size = selected if selected > 0 else _agent1_capacity(batch_size)
    result = dict(
        legacy.run_agent1_microbatch_hard(
            data_version,
            user_id=user_id,
            batch_size=effective_batch_size,
        )
    )
    result.update(
        agent1ReadyFirstRuntimeVersion=AGENT1_READY_FIRST_RUNTIME_VERSION,
        agent1BatchPolicy=AGENT1_READY_FIRST_POLICY,
        configuredBatchCapacity=_agent1_capacity(batch_size),
        effectiveClaimBatchSize=effective_batch_size,
        claimScope="current_provider_subbatch_only",
        waitForFullCapacity=False,
        secondWorkerCreated=False,
        providerConfigurationChanged=False,
        exactHashRuntimeChanged=False,
        readyFirstPlan=plan,
    )
    return result


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
        "agent1ReadyFirstRuntimeVersion": AGENT1_READY_FIRST_RUNTIME_VERSION,
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
        agent1ReadyFirstRuntimeVersion=AGENT1_READY_FIRST_RUNTIME_VERSION,
        agent1BatchPolicy=AGENT1_READY_FIRST_POLICY,
        agent1ClaimScope="current_provider_subbatch_only",
        agent1WaitForFullCapacity=False,
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
            "agent1_ready_first_exact_hash_then_agent2_evidence_slice_then_hash_proof"
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
    from src.services import agent_pipeline_item_worker_v2010_service as pipeline
    from src.services.agent_runtime_hard_interface_v2255_service import (
        _refresh_read_models,
    )
    from src.services.pipeline_action_microbatch_v205_service import (
        pending_agent2_item_count,
    )
    from src.services.pipeline_agent1_microbatch_v20101_service import (
        pending_agent1_item_count,
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
    agent2_pending = pending_agent2_item_count(resolved)
    if not higher_priority_pending and agent2_pending > 0:
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

    agent1_blocked_by_downstream = any(
        [
            higher_priority_pending,
            agent2_pending > 0,
            bool(pipeline._load_agent1_completed_items(resolved, 1)),
        ]
    )
    if not agent1_blocked_by_downstream and pending_agent1_item_count(resolved) > 0:
        stage_result = run_agent1_ready_first_microbatch_hard(
            resolved,
            user_id=user_id,
            batch_size=agent1_batch_size,
        )
        output = {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
            "ran": bool(stage_result.get("ran")),
            "workerId": worker_id,
            "selectedStage": "agent1_ready_first_to_exact_hash_judgment",
            "dataVersion": resolved,
            "result": stage_result,
            "runtimeSource": (
                "agent1ReadyFirst.v2315+agent1InputRef.v3+exactHash.v2259"
            ),
            "executionLockContract": EXECUTION_LOCK_CONTRACT,
            "activeAgent1RuntimeBinding": binding,
            "agent1ClaimScope": "current_provider_subbatch_only",
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
        agent1ReadyFirstRuntimeVersion=AGENT1_READY_FIRST_RUNTIME_VERSION,
        agent1BatchPolicy=AGENT1_READY_FIRST_POLICY,
        agent1ClaimScope="current_provider_subbatch_only",
        agent1WaitForFullCapacity=False,
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
            "agent1_ready_first_exact_hash_then_agent2_evidence_slice_then_hash_proof"
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
    return run_agent1_ready_first_microbatch_hard(
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
    "AGENT1_READY_FIRST_RUNTIME_VERSION",
    "AGENT1_READY_FIRST_POLICY",
    "plan_agent1_ready_first_batch",
    "run_agent1_ready_first_microbatch_hard",
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
