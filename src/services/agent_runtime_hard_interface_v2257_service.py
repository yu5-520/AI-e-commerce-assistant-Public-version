"""V22.5.7 Agent1 semantic-continuity hard runtime.

Only Agent1 input projection and model-facing semantics change. Agent2, Agent3, task
mapping, task pool and the execution-lock contract remain owned by the sealed V22.5.5
runtime. No runtime replacement, second worker or fallback path is installed.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Tuple

from src.services import agent_runtime_hard_interface_v2255_service as legacy
from src.services.agent_execution_lock_v2255_service import (
    execution_lock_from,
    missing_execution_lock,
)
from src.services.agent_input_contract_v2257_service import (
    AGENT1_INPUT_PROJECTION_VERSION,
    AGENT1_INPUT_SCHEMA,
)
from src.services.agent_input_transport_v2257_service import (
    ensure_agent1_input_ref,
    resolve_agent_input_ref,
)
from src.services.agent_token_runtime_v2257_service import run_agent1_projected_inputs

AGENT_RUNTIME_HARD_INTERFACE_VERSION = "22.5.7"
THREE_AGENT_PIPELINE_VERSION = "22.5.5"
EXECUTION_LOCK_CONTRACT = "one_problem_one_action_one_owner_one_target"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def run_agent1_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
) -> Dict[str, Any]:
    del user_id
    from src.services import pipeline_agent1_microbatch_v20101_service as core
    from src.services.agent_runtime_contract_v2010_service import (
        AGENT_RUNTIME_CONTRACT_VERSION,
        missing_agent1_contract,
        normalize_agent1_completed_contract,
    )
    from src.services.agent_runtime_recovery_v2261_service import (
        claim_agent1_items,
        recover_stale_agent1_items,
    )
    from src.services.operating_policy_context_v2028_service import build_operating_policy_context
    from src.services.pipeline_artifact_contract_service import artifact_refs_from_row
    from src.services.pipeline_item_service import pipeline_item_summary

    recovery = legacy._recover_unresolved_once()
    stale = recover_stale_agent1_items(data_version)
    items = core._pending_items(data_version, max(1, min(20, int(batch_size or 8))))
    if not items:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
            "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
            "dataVersion": data_version,
            "ran": bool(stale.get("requeuedItemCount")),
            "claimedItemCount": 0,
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "skipped_no_pending_items", "actualCalls": 0},
            "staleRunningRecovery": stale,
            "executionLockRecovery": recovery,
            "runtimeSource": "agent1InputRef.v2",
            "executionLockContract": EXECUTION_LOCK_CONTRACT,
            "fallbackAllowed": False,
        }

    policy = {
        **build_operating_policy_context(),
        "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
    }
    prepared: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = {}
    prepare_failures: List[Tuple[Dict[str, Any], str]] = []
    for item in items:
        try:
            input_ref = ensure_agent1_input_ref(item, policy_context=policy)
            envelope = resolve_agent_input_ref(input_ref, expected_schema=AGENT1_INPUT_SCHEMA)
            prepared[str(item.get("item_id"))] = (item, envelope, dict(envelope["payload"]))
        except Exception as exc:
            prepare_failures.append((item, str(exc)))

    claimable = [value[0] for value in prepared.values()]
    claimed = claim_agent1_items(claimable)
    claimed_ids = {
        str(item.get("item_id"))
        for item in (claimed if isinstance(claimed, list) else claimable)
    }
    prepared = {key: value for key, value in prepared.items() if key in claimed_ids}

    for item, reason in prepare_failures:
        legacy._finish_agent1(
            core,
            item,
            stage=core.AGENT1_FAILED_STAGE,
            status="failed",
            output_ref=f"agent1_input_contract_failed:{data_version or 'latest'}:{item.get('item_id')}",
            payload={
                "reason": reason,
                "failureOwner": "agent_input_transport_v2257",
                "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
                "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                "runtimeSource": "agent1InputRef.v2",
                "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
                "executionLockContract": EXECUTION_LOCK_CONTRACT,
                "fallbackAllowed": False,
            },
        )

    if not prepared:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
            "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
            "dataVersion": data_version,
            "ran": bool(prepare_failures),
            "claimedItemCount": 0,
            "failedItemCount": len(prepare_failures),
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "failed_hard_input_contract", "actualCalls": 0},
            "runtimeSource": "agent1InputRef.v2",
            "executionLockContract": EXECUTION_LOCK_CONTRACT,
            "executionLockRecovery": recovery,
            "fallbackAllowed": False,
        }

    judgments, provider = run_agent1_projected_inputs(
        [value[1] for value in prepared.values()],
        data_version=data_version,
        max_items_per_call=batch_size,
    )
    indexed = core._index_judgments(judgments)
    completed = invalid = failed = observed = diagnostic_hold = 0
    missing_counter: Counter[str] = Counter()
    by_family: Counter[str] = Counter()

    for item, envelope, projected_signal in prepared.values():
        signal_id = str(item.get("signal_id") or projected_signal.get("signalId") or "")
        matched = core._match(item, projected_signal, indexed)
        refs = artifact_refs_from_row(item)
        input_ref = str(refs.get("agent1InputRef") or "")
        if not matched:
            failed += 1
            legacy._finish_agent1(
                core,
                item,
                stage=core.AGENT1_FAILED_STAGE,
                status="failed",
                output_ref=f"agent1_failed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    "reason": "agent_returned_no_matching_judgment",
                    "providerStatus": provider.get("providerStatus"),
                    "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "agent1InputRef": input_ref,
                    "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
                    "executionLockContract": EXECUTION_LOCK_CONTRACT,
                    "fallbackAllowed": False,
                },
            )
            continue

        judgment = dict(matched[0])
        decision_core = _dict(judgment.get("decisionCore"))
        decision_type = str(
            judgment.get("decisionType") or decision_core.get("decisionType") or ""
        ).strip().lower()
        decision_hint = str(judgment.get("decisionHint") or "").strip().lower()
        if decision_type == "observe" or decision_hint in {
            "observe_only",
            "metric_observation",
            "product_level_observation",
        }:
            observed += 1
            diagnostic_hold += 1 if judgment.get("diagnosticHold") is True else 0
            legacy._finish_agent1(
                core,
                item,
                stage=core.OBSERVED_STAGE,
                status="observed",
                output_ref=f"agent1_observed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    **judgment,
                    "observationDeposited": True,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "runtimeSource": "agent1InputRef.v2",
                    "agent1InputRef": input_ref,
                    "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
                    "inputProjectionAudit": envelope.get("projectionAudit"),
                    "executionLockContract": EXECUTION_LOCK_CONTRACT,
                    "taskAdmissionAllowed": False,
                    "fallbackAllowed": False,
                },
            )
            continue

        lock = execution_lock_from(judgment)
        lock_missing = missing_execution_lock(lock)
        if lock_missing:
            observed += 1
            diagnostic_hold += 1
            missing_counter.update(lock_missing)
            legacy._finish_agent1(
                core,
                item,
                stage=core.OBSERVED_STAGE,
                status="observed",
                output_ref=f"agent1_unresolved:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    **judgment,
                    "decisionType": "observe",
                    "decisionHint": "observe_only",
                    "selectedOperatingRoute": "observe",
                    "selectedActionFamilyHint": None,
                    "actionFamily": None,
                    "route": "observe",
                    "executionLock": {**lock, "locked": False},
                    "diagnosticHold": True,
                    "diagnosticHoldReason": "agent1_execution_lock_incomplete",
                    "missingEvidence": list(
                        dict.fromkeys([*(judgment.get("missingEvidence") or []), *lock_missing])
                    ),
                    "observationDeposited": True,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "runtimeSource": "agent1InputRef.v2",
                    "agent1InputRef": input_ref,
                    "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
                    "inputProjectionAudit": envelope.get("projectionAudit"),
                    "executionLockContract": EXECUTION_LOCK_CONTRACT,
                    "taskAdmissionAllowed": False,
                    "fallbackAllowed": False,
                },
            )
            continue

        payload = normalize_agent1_completed_contract(
            item=item,
            signal=projected_signal,
            judgment=judgment,
            provider=provider,
            data_version=data_version,
        )
        payload.update(
            version=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            contractVersion=AGENT_RUNTIME_CONTRACT_VERSION,
            rawAgent1Judgment=judgment,
            executionLock=lock,
            evidenceStatus=lock.get("evidenceStatus"),
            primaryProblemNode=lock.get("primaryProblemNode"),
            primaryAction=lock.get("primaryAction"),
            primaryExecutionTarget=lock.get("primaryExecutionTarget"),
            primaryOwner=lock.get("primaryOwner"),
            decisiveFacts=lock.get("decisiveFacts") or [],
            supportingCoordination=lock.get("supportingCoordination") or [],
            forbiddenActionDomains=lock.get("forbiddenActionDomains") or [],
            runtimeSource="agent1InputRef.v2",
            agent1InputRef=input_ref,
            agent1InputProjectionVersion=AGENT1_INPUT_PROJECTION_VERSION,
            sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
            inputProjectionAudit=envelope.get("projectionAudit"),
            outputContract="V22.5.7.agent1_completed",
            executionLockContract=EXECUTION_LOCK_CONTRACT,
            fallbackAllowed=False,
        )
        missing = list(
            dict.fromkeys([*missing_agent1_contract(payload), *missing_execution_lock(lock)])
        )
        if missing:
            invalid += 1
            missing_counter.update(missing)
            legacy._finish_agent1(
                core,
                item,
                stage=core.AGENT1_OUTPUT_INVALID_STAGE,
                status="failed",
                output_ref=f"agent1_output_invalid:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    "reason": "agent1_execution_lock_contract_missing",
                    "missing": missing,
                    "partialPayload": payload,
                    "providerStatus": provider.get("providerStatus"),
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "agent1InputRef": input_ref,
                    "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
                    "executionLockContract": EXECUTION_LOCK_CONTRACT,
                    "fallbackAllowed": False,
                },
            )
            continue

        completed += 1
        by_family[str(payload.get("actionFamily") or "missing")] += 1
        legacy._finish_agent1(
            core,
            item,
            stage=core.AGENT1_COMPLETED_STAGE,
            status="ready",
            output_ref=f"pipeline_items.agent1_completed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
            payload=payload,
        )

    result = {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "ran": True,
        "claimedItemCount": len(prepared),
        "validAgentInputCount": len(prepared),
        "completedItemCount": completed,
        "observedItemCount": observed,
        "diagnosticHoldItemCount": diagnostic_hold,
        "invalidItemCount": invalid,
        "failedItemCount": failed + len(prepare_failures),
        "missingCounter": dict(missing_counter),
        "bySelectedActionFamily": dict(by_family),
        "agentJudgmentCount": len(judgments),
        "pendingItemCount": core.pending_agent1_item_count(data_version),
        "provider": provider,
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=30),
        "staleRunningRecovery": stale,
        "executionLockRecovery": recovery,
        "runtimeSource": "agent1InputRef.v2",
        "executionLockContract": EXECUTION_LOCK_CONTRACT,
        "executionMode": "semantic_continuity_then_execution_lock",
        "fallbackAllowed": False,
    }
    legacy._refresh_read_models(result, data_version)
    return result


def select_runnable_data_version_v225(preferred: str | None = None) -> str | None:
    return legacy.select_runnable_data_version_v225(preferred)


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
    from src.services import agent_pipeline_item_worker_v2010_service as pipeline
    from src.services.pipeline_action_microbatch_v205_service import pending_agent2_item_count
    from src.services.pipeline_agent1_microbatch_v20101_service import pending_agent1_item_count
    from src.services.pipeline_agent3_sop_v225_service import pending_agent3_sop_item_count
    from src.services.pipeline_task_mapping_v225_service import (
        pending_task_mapping_item_count,
        pending_task_pool_item_count,
    )

    resolved = data_version or select_runnable_data_version_v225()
    if not resolved:
        return legacy.run_agent_pipeline_tick_hard(
            data_version=data_version,
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

    downstream_pending = any(
        [
            pending_task_pool_item_count(resolved) > 0,
            pending_task_mapping_item_count(resolved) > 0,
            pending_agent3_sop_item_count(resolved) > 0,
            pending_agent2_item_count(resolved) > 0,
            bool(pipeline._load_agent1_completed_items(resolved, 1)),
        ]
    )
    if downstream_pending or pending_agent1_item_count(resolved) <= 0:
        return legacy.run_agent_pipeline_tick_hard(
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

    result = run_agent1_microbatch_hard(
        resolved,
        user_id=user_id,
        batch_size=agent1_batch_size,
    )
    output = {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "agent1InputProjectionVersion": AGENT1_INPUT_PROJECTION_VERSION,
        "ran": bool(result.get("ran")),
        "workerId": worker_id,
        "selectedStage": "agent1InputRef.v2_to_agent1_completed_or_observed",
        "dataVersion": resolved,
        "result": result,
        "runtimeSource": "agent1InputRef.v2_or_v22_5_5_downstream_inputs",
        "executionLockContract": EXECUTION_LOCK_CONTRACT,
        "executionMode": "semantic_continuity_then_execution_lock",
        "fallbackAllowed": False,
    }
    legacy._refresh_read_models(output, resolved)
    return output


startup_agent_runtime_hard = legacy.startup_agent_runtime_hard


__all__ = [
    "AGENT_RUNTIME_HARD_INTERFACE_VERSION",
    "THREE_AGENT_PIPELINE_VERSION",
    "EXECUTION_LOCK_CONTRACT",
    "run_agent1_microbatch_hard",
    "select_runnable_data_version_v225",
    "run_agent_pipeline_tick_hard",
    "startup_agent_runtime_hard",
]
