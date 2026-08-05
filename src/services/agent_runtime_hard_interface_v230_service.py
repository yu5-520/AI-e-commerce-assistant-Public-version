"""V22.3.0 hard-interface Agent runtime.

The runtime may resolve only ``agent1InputRef`` and ``agent2InputRef`` for model
work. Full signal/capability artifacts stay outside Agent and token execution.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Tuple

from src.services.agent_input_contract_v230_service import (
    AGENT1_INPUT_SCHEMA,
    AGENT2_INPUT_SCHEMA,
)
from src.services.agent_input_transport_v230_service import (
    ensure_agent1_input_ref,
    ensure_agent2_input_ref,
    migrate_pending_agent_inputs,
    resolve_agent_input_ref,
)
from src.services.agent_token_runtime_v230_service import (
    run_agent1_projected_inputs,
    run_agent2_projected_inputs,
)

AGENT_RUNTIME_HARD_INTERFACE_VERSION = "22.3.0"


def _finish_agent1(
    core: Any,
    item: Dict[str, Any],
    *,
    stage: str,
    status: str,
    output_ref: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    from src.services.agent_runtime_recovery_v2261_service import clear_agent1_runtime_control

    result = core._finish_item(
        item,
        stage=stage,
        status=status,
        output_ref=output_ref,
        payload=payload,
    )
    clear_agent1_runtime_control(str(item.get("item_id") or result.get("itemId") or ""))
    return result


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

    stale = recover_stale_agent1_items(data_version)
    items = core._pending_items(data_version, max(1, min(20, int(batch_size or 8))))
    if not items:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "dataVersion": data_version,
            "ran": bool(stale.get("requeuedItemCount")),
            "claimedItemCount": 0,
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "skipped_no_pending_items", "actualCalls": 0},
            "staleRunningRecovery": stale,
            "runtimeSource": "agent1InputRef",
        }

    policy = build_operating_policy_context()
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
    claim_agent1_items(claimable)
    claimed_ids = {str(item.get("item_id")) for item in claimable}
    prepared = {key: value for key, value in prepared.items() if key in claimed_ids}

    for item, reason in prepare_failures:
        _finish_agent1(
            core,
            item,
            stage=core.AGENT1_FAILED_STAGE,
            status="failed",
            output_ref=f"agent1_input_contract_failed:{data_version or 'latest'}:{item.get('item_id')}",
            payload={
                "reason": reason,
                "failureOwner": "agent_input_transport",
                "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
                "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                "runtimeSource": "agent1InputRef",
                "fallbackAllowed": False,
            },
        )

    if not prepared:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "dataVersion": data_version,
            "ran": bool(prepare_failures),
            "claimedItemCount": 0,
            "failedItemCount": len(prepare_failures),
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "failed_hard_input_contract", "actualCalls": 0},
            "runtimeSource": "agent1InputRef",
            "fallbackAllowed": False,
        }

    envelopes = [value[1] for value in prepared.values()]
    judgments, provider = run_agent1_projected_inputs(
        envelopes,
        data_version=data_version,
        max_items_per_call=batch_size,
    )
    indexed = core._index_judgments(judgments)
    completed = invalid = failed = observed = 0
    missing_counter: Counter[str] = Counter()
    by_family: Counter[str] = Counter()

    for item, envelope, projected_signal in prepared.values():
        signal_id = str(item.get("signal_id") or projected_signal.get("signalId") or "")
        matched = core._match(item, projected_signal, indexed)
        refs = artifact_refs_from_row(item)
        input_ref = str(refs.get("agent1InputRef") or "")
        if not matched:
            failed += 1
            _finish_agent1(
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
                    "fallbackAllowed": False,
                },
            )
            continue

        judgment = matched[0]
        decision_core = judgment.get("decisionCore") if isinstance(judgment.get("decisionCore"), dict) else {}
        decision_type = str(judgment.get("decisionType") or decision_core.get("decisionType") or "").strip().lower()
        decision_hint = str(judgment.get("decisionHint") or "").strip().lower()
        if decision_type == "observe" or decision_hint in {
            "observe_only",
            "metric_observation",
            "product_level_observation",
        }:
            observed += 1
            _finish_agent1(
                core,
                item,
                stage=core.OBSERVED_STAGE,
                status="observed",
                output_ref=f"agent1_observed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    **judgment,
                    "observationDeposited": True,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "runtimeSource": "agent1InputRef",
                    "agent1InputRef": input_ref,
                    "inputProjectionAudit": envelope.get("projectionAudit"),
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
            runtimeSource="agent1InputRef",
            agent1InputRef=input_ref,
            sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
            inputProjectionAudit=envelope.get("projectionAudit"),
            outputContract="V22.3.agent1_completed",
            fallbackAllowed=False,
        )
        missing = missing_agent1_contract(payload)
        if missing:
            invalid += 1
            missing_counter.update(missing)
            _finish_agent1(
                core,
                item,
                stage=core.AGENT1_OUTPUT_INVALID_STAGE,
                status="failed",
                output_ref=f"agent1_output_invalid:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
                payload={
                    "reason": "agent1_contract_missing",
                    "missing": missing,
                    "partialPayload": payload,
                    "providerStatus": provider.get("providerStatus"),
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                    "agent1InputRef": input_ref,
                    "fallbackAllowed": False,
                },
            )
            continue

        completed += 1
        by_family[str(payload.get("actionFamily") or "missing")] += 1
        _finish_agent1(
            core,
            item,
            stage=core.AGENT1_COMPLETED_STAGE,
            status="ready",
            output_ref=f"pipeline_items.agent1_completed:{data_version or 'latest'}:{signal_id or item.get('item_id')}",
            payload=payload,
        )

    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "ran": True,
        "claimedItemCount": len(prepared),
        "validAgentInputCount": len(prepared),
        "completedItemCount": completed,
        "observedItemCount": observed,
        "invalidItemCount": invalid,
        "failedItemCount": failed + len(prepare_failures),
        "missingCounter": dict(missing_counter),
        "bySelectedActionFamily": dict(by_family),
        "agentJudgmentCount": len(judgments),
        "pendingItemCount": core.pending_agent1_item_count(data_version),
        "provider": provider,
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=30),
        "staleRunningRecovery": stale,
        "runtimeSource": "agent1InputRef",
        "executionMode": "hard_interface_projection_artifact_only",
        "fallbackAllowed": False,
    }


def run_agent2_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 5,
    action_family: str | None = None,
) -> Dict[str, Any]:
    from src.services import pipeline_action_microbatch_v205_service as worker
    from src.services.agent2_action_plan_core_v20_service import (
        provider_has_real_agent2_call,
        real_agent2_provider_missing_reason,
    )
    from src.services.agent2_runtime_resilience_v2143_service import (
        claim_agent2_items,
        schedule_agent2_failure,
    )
    from src.services.agent_runtime_contract_v2141_service import (
        missing_action_pack_contract,
        missing_agent2_contract,
        normalize_agent2_completed_contract,
    )
    from src.services.agent_runtime_native_v2263_service import repair_agent2_plan_native
    from src.services.pipeline_artifact_contract_service import artifact_refs_from_row

    family = action_family or worker._choose_next_family(data_version)
    selected = worker._pending_action_items(
        data_version,
        max(1, min(12, int(batch_size or 5))),
        family,
    )
    if not selected:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "claimedItemCount": 0,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "skipped_no_due_action_pack_ready_items", "actualCalls": 0},
            "runtimeSource": "agent2InputRef",
        }

    prepared: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = {}
    invalid_missing: Counter[str] = Counter()
    for item in selected:
        try:
            input_ref = ensure_agent2_input_ref(item)
            envelope = resolve_agent_input_ref(input_ref, expected_schema=AGENT2_INPUT_SCHEMA)
            package = dict(envelope["payload"])
            missing = missing_action_pack_contract(package)
            if missing:
                invalid_missing.update(missing)
                worker._mark_action_pack_invalid(item, missing, package)
            else:
                prepared[str(item.get("item_id"))] = (item, envelope, package)
        except Exception as exc:
            invalid_missing.update(["agent2InputRef"])
            worker._mark_action_pack_invalid(
                item,
                ["agent2InputRef", str(exc)[:180]],
                {"runtimeSource": "agent2InputRef", "fallbackAllowed": False},
            )

    if not prepared:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "dataVersion": data_version,
            "ran": True,
            "claimedItemCount": len(selected),
            "validActionPackCount": 0,
            "invalidActionPackCount": len(selected),
            "invalidMissing": dict(invalid_missing),
            "failedItemCount": len(selected),
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "skipped_invalid_agent2_input_contract", "actualCalls": 0},
            "runtimeSource": "agent2InputRef",
            "fallbackAllowed": False,
        }

    claimed = claim_agent2_items(
        [value[0] for value in prepared.values()],
        worker_id=user_id,
    )
    claimed_by_id = {str(item.get("item_id")): item for item in claimed}
    prepared = {
        item_id: (claimed_by_id[item_id], envelope, package)
        for item_id, (_, envelope, package) in prepared.items()
        if item_id in claimed_by_id
    }
    if not prepared:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "claimedItemCount": 0,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "claim_conflict", "actualCalls": 0},
            "runtimeSource": "agent2InputRef",
        }

    envelopes = [value[1] for value in prepared.values()]
    plans, provider = run_agent2_projected_inputs(
        envelopes,
        data_version=data_version,
        max_items_per_call=batch_size,
    )
    completed = invalid_output = retry_scheduled = dead_lettered = proof_failed = 0
    by_status: Counter[str] = Counter()
    by_failure_class: Counter[str] = Counter()

    for item, envelope, package in prepared.values():
        package_id = str(package.get("packageId") or item.get("package_id") or item.get("item_id") or "")
        plan = plans.get(package_id)
        if not isinstance(plan, dict) or not plan:
            outcome = schedule_agent2_failure(
                item,
                package,
                provider,
                real_agent2_provider_missing_reason(provider, package_id) or "agent2_returned_no_plan",
            )
            by_failure_class[str(outcome.get("failureClass"))] += 1
            retry_scheduled += 1 if outcome.get("status") == "retry" else 0
            dead_lettered += 1 if outcome.get("terminal") else 0
            continue

        proof = plan.get("agent2ExecutionProof") if isinstance(plan.get("agent2ExecutionProof"), dict) else {}
        if not provider_has_real_agent2_call(provider, package_id, proof):
            proof_failed += 1
            outcome = schedule_agent2_failure(
                item,
                package,
                provider,
                real_agent2_provider_missing_reason(provider, package_id, proof) or "agent2_item_provenance_missing",
            )
            by_failure_class[str(outcome.get("failureClass"))] += 1
            retry_scheduled += 1 if outcome.get("status") == "retry" else 0
            dead_lettered += 1 if outcome.get("terminal") else 0
            continue

        plan = repair_agent2_plan_native(plan, package)
        status = str(plan.get("actionPlanStatus") or "missing")
        by_status[status] += 1
        candidate = normalize_agent2_completed_contract(package, plan, provider)
        missing = missing_agent2_contract(candidate)
        refs = artifact_refs_from_row(item)
        input_ref = str(refs.get("agent2InputRef") or "")
        if status != "ready" or missing:
            invalid_output += 1
            worker._mark_agent2_output_invalid(item, package, plan, provider, missing)
            continue

        completed += 1
        candidate.update(
            agent2Source=plan.get("agent2Source"),
            fallbackAllowed=False,
            taskAdmissionAllowed=True,
            agentRuntimeHardInterfaceVersion=AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            runtimeSource="agent2InputRef",
            agent2InputRef=input_ref,
            sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
            inputProjectionAudit=envelope.get("projectionAudit"),
            outputContract="V22.3.agent2_completed",
        )
        worker._finish_item(
            item,
            stage=worker.AGENT2_COMPLETED_STAGE,
            status="ready",
            output_ref=f"agent2_action_plan:{data_version or 'latest'}:{package_id}",
            payload=candidate,
        )

    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "dataVersion": data_version,
        "ran": True,
        "actionFamily": family,
        "claimedItemCount": len(prepared),
        "validAgent2InputCount": len(prepared),
        "invalidActionPackCount": len(selected) - len(prepared),
        "invalidMissing": dict(invalid_missing),
        "completedItemCount": completed,
        "invalidOutputItemCount": invalid_output,
        "retryScheduledItemCount": retry_scheduled,
        "deadLetteredItemCount": dead_lettered,
        "proofFailedItemCount": proof_failed,
        "failedItemCount": invalid_output + dead_lettered,
        "actionPlanCount": len(plans),
        "byActionPlanStatus": dict(by_status),
        "byFailureClass": dict(by_failure_class),
        "pendingItemCount": worker.pending_agent2_item_count(data_version),
        "provider": provider,
        "runtimeSource": "agent2InputRef",
        "executionMode": "hard_interface_projection_artifact_only",
        "fallbackAllowed": False,
    }


def run_agent_pipeline_tick_hard(
    data_version: str | None = None,
    *,
    user_id: str | None = None,
    worker_id: str | None = None,
    agent1_batch_size: int = 8,
    action_pack_batch_size: int = 8,
    agent2_batch_size: int = 5,
    sop_batch_size: int = 8,
    pool_batch_size: int = 8,
    force_new_snapshot: bool = False,
) -> Dict[str, Any]:
    from src.services import agent_pipeline_item_worker_v2010_service as legacy
    from src.services.agent_runtime_native_v2263_service import (
        latest_data_version,
        recover_target_only_agent2_failures_native,
    )
    from src.services.agent_runtime_recovery_v2261_service import recover_stale_agent1_items
    from src.services.pipeline_action_microbatch_v205_service import pending_agent2_item_count
    from src.services.pipeline_agent1_microbatch_v20101_service import pending_agent1_item_count
    from src.services.pipeline_sop_task_pool_v2010_service import (
        pending_sop_item_count,
        pending_task_pool_item_count,
        run_sop_mapping_microbatch_v206,
        run_task_pool_admission_microbatch_v207,
    )

    resolved = data_version or latest_data_version()
    if not resolved:
        return {"version": AGENT_RUNTIME_HARD_INTERFACE_VERSION, "ran": False, "reason": "no_data_version"}

    stale = recover_stale_agent1_items(resolved)
    target_recovery = recover_target_only_agent2_failures_native(resolved)
    contract_recovery = legacy.recover_version_only_action_pack_invalid(resolved)
    input_migration = migrate_pending_agent_inputs(resolved)

    if pending_task_pool_item_count(resolved) > 0:
        result = run_task_pool_admission_microbatch_v207(
            data_version=resolved,
            user_id=user_id,
            batch_size=pool_batch_size,
            force_new_snapshot=force_new_snapshot,
        )
        selected = "sop_mapped_to_task_admitted"
    elif pending_sop_item_count(resolved) > 0:
        result = run_sop_mapping_microbatch_v206(
            data_version=resolved,
            user_id=user_id,
            batch_size=sop_batch_size,
        )
        selected = "agent2_completed_to_sop_mapped"
    elif pending_agent2_item_count(resolved) > 0:
        result = run_agent2_microbatch_hard(
            data_version=resolved,
            user_id=user_id,
            batch_size=agent2_batch_size,
        )
        selected = "agent2InputRef_to_agent2_completed"
    elif legacy._load_agent1_completed_items(resolved, 1):
        result = legacy.seed_action_pack_from_agent1_items(
            resolved,
            batch_size=action_pack_batch_size,
            source="agent_runtime_hard_interface_v230",
        )
        selected = "agent1_completed_to_action_pack_ready"
    elif pending_agent1_item_count(resolved) > 0:
        result = run_agent1_microbatch_hard(
            data_version=resolved,
            user_id=user_id,
            batch_size=agent1_batch_size,
        )
        selected = "agent1InputRef_to_agent1_completed_or_observed"
    else:
        result = {"ran": False, "claimedItemCount": 0, "reason": "no_runnable_agent_pipeline_items"}
        selected = "idle"

    ran = bool(result.get("ran")) if "ran" in result else int(result.get("claimedItemCount") or 0) > 0
    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "contractVersion": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "ran": ran,
        "workerId": worker_id,
        "selectedStage": selected,
        "dataVersion": resolved,
        "contractRecovery": contract_recovery,
        "agent1StaleRunningRecovery": stale,
        "agent2TargetContractRecovery": target_recovery,
        "agentInputMigration": input_migration,
        "result": result,
        "runtimeSource": "agent1InputRef_or_agent2InputRef",
        "executionMode": "hard_interface_projection_artifact_only",
        "fallbackAllowed": False,
    }


def startup_agent_runtime_hard() -> Dict[str, Any]:
    from src.services.agent_runtime_native_v2263_service import (
        latest_data_version,
        recover_target_only_agent2_failures_native,
    )
    from src.services.agent_runtime_recovery_v2261_service import (
        ensure_agent1_runtime_columns,
        recover_stale_agent1_items,
    )

    ensure_agent1_runtime_columns()
    latest = latest_data_version()
    migration = migrate_pending_agent_inputs(latest) if latest else {}
    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "dataVersion": latest,
        "agentInputMigration": migration,
        "agent1": recover_stale_agent1_items(latest) if latest else {},
        "agent2": recover_target_only_agent2_failures_native(latest) if latest else {},
        "executionMode": "hard_interface_projection_artifact_only",
        "fallbackAllowed": False,
    }


def agent_runtime_hard_interface_status() -> Dict[str, Any]:
    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "hardInterface": True,
        "agent1RuntimeSource": "artifactRefs.agent1InputRef",
        "agent2RuntimeSource": "artifactRefs.agent2InputRef",
        "fullSignalReadByAgentAllowed": False,
        "fullCapabilityReadByAgentAllowed": False,
        "unprojectedProviderInputAllowed": False,
        "gatewayBusinessCompactionOwner": False,
        "tokenRuntimeOwner": "agent_token_runtime_v230",
        "transportOwner": "agent_input_transport_v230",
        "fallbackAllowed": False,
        "executionMode": "hard_interface_projection_artifact_only",
    }


__all__ = [
    "AGENT_RUNTIME_HARD_INTERFACE_VERSION",
    "run_agent1_microbatch_hard",
    "run_agent2_microbatch_hard",
    "run_agent_pipeline_tick_hard",
    "startup_agent_runtime_hard",
    "agent_runtime_hard_interface_status",
]
