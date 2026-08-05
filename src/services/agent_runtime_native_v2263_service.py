"""V22.2.6.3 native Agent runtime integrity.

This module is the executable Agent pipeline entry. It does not depend on import-
time monkey patches:

* Agent1 claims are written through a finite SQLite lease before the provider call.
* Agent1 terminal writes always release the lease.
* Agent2 ROAS plans receive a factual plan id or a reviewable store/product selector
  before the final semantic contract is evaluated.
* The failed V22.2.6.1 replay may run once more under the native implementation,
  then remains terminal if it still fails.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, dumps

AGENT_RUNTIME_NATIVE_VERSION = "22.2.6.3"
MAX_TARGET_REPAIRS = 2


def _now() -> str:
    return datetime.now().isoformat()


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return row.get(key) if isinstance(row, dict) else None


def latest_data_version() -> str | None:
    from src.services.pipeline_item_service import ensure_pipeline_item_tables

    ensure_pipeline_item_tables()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT data_version
            FROM pipeline_items
            WHERE COALESCE(data_version,'')!=''
            GROUP BY data_version
            ORDER BY MAX(updated_at) DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row["data_version"]) if row and row["data_version"] else None


def recover_target_only_agent2_failures_native(
    data_version: str | None = None,
    *,
    limit: int = 100,
) -> Dict[str, Any]:
    """Replay target-only failures at most twice.

    Repair number one was consumed by the import-hook implementation. Repair number
    two is reserved for migration to this native implementation and cannot loop.
    """
    from src.services.agent_runtime_recovery_v2261_service import (
        AGENT2_OUTPUT_INVALID_STAGE,
        ACTION_PACK_READY_STAGE,
        _target_only_error,
        ensure_agent1_runtime_columns,
    )
    from src.services.artifact_transport_service import validate_artifact
    from src.services.pipeline_artifact_contract_service import artifact_refs_from_row

    ensure_agent1_runtime_columns()
    where = [
        "current_stage=?",
        "status='failed'",
        "action_family IN ('roas_scale','roas_guard')",
        "COALESCE(agent2_target_repair_count,0)<?",
    ]
    params: List[Any] = [AGENT2_OUTPUT_INVALID_STAGE, MAX_TARGET_REPAIRS]
    if data_version is not None:
        where.append("COALESCE(data_version,'')=COALESCE(?,'')")
        params.append(data_version)

    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(where)} "
            "ORDER BY updated_at ASC LIMIT ?",
            (*params, max(1, min(500, int(limit)))),
        ).fetchall()
        recovered = skipped = 0
        now = _now()
        for row in rows:
            error = _row_value(row, "last_error_code") or _row_value(row, "error_reason")
            if not _target_only_error(error):
                skipped += 1
                continue
            refs = artifact_refs_from_row(row)
            capability_ref = str(refs.get("capabilityRef") or "").strip()
            validation = validate_artifact(capability_ref) if capability_ref else {"ok": False}
            if not capability_ref or validation.get("ok") is not True:
                skipped += 1
                continue
            refs["currentStageRef"] = capability_ref
            cursor = conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?, status='retry',
                    agent2_target_repair_count=COALESCE(agent2_target_repair_count,0)+1,
                    claim_id=NULL, lease_expires_at=NULL, retry_after=NULL,
                    failure_code=NULL, failure_class=NULL, error_reason=NULL,
                    last_error_code=?, artifact_refs_json=?, payload_artifact_ref=?,
                    payload=NULL, updated_at=?
                WHERE item_id=? AND current_stage=? AND status='failed'
                  AND COALESCE(agent2_target_repair_count,0)<?
                """,
                (
                    ACTION_PACK_READY_STAGE,
                    "agent2_target_contract_requeued_for_native_runtime",
                    dumps(refs),
                    capability_ref,
                    now,
                    _row_value(row, "item_id"),
                    AGENT2_OUTPUT_INVALID_STAGE,
                    MAX_TARGET_REPAIRS,
                ),
            )
            if cursor.rowcount == 1:
                recovered += 1
            else:
                skipped += 1
        conn.commit()

    return {
        "version": AGENT_RUNTIME_NATIVE_VERSION,
        "dataVersion": data_version,
        "candidateCount": len(rows),
        "recoveredItemCount": recovered,
        "skippedItemCount": skipped,
        "maxAutomaticRepairsPerItem": MAX_TARGET_REPAIRS,
        "replaySource": "artifactRefs.capabilityRef",
        "nativeTargetProjection": True,
    }


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


def run_agent1_microbatch_native(
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
    from src.services.end_to_end_agent_flow_v226_service import _signal_from_item
    from src.services.operating_policy_context_v2028_service import (
        OPERATING_POLICY_CONTEXT_VERSION,
        build_operating_policy_context,
    )
    from src.services.pipeline_item_service import pipeline_item_summary

    stale = recover_stale_agent1_items(data_version)
    items = core._pending_items(data_version, max(1, min(20, int(batch_size or 8))))
    if not items:
        return {
            "version": AGENT_RUNTIME_NATIVE_VERSION,
            "dataVersion": data_version,
            "ran": bool(stale.get("requeuedItemCount")),
            "claimedItemCount": 0,
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "skipped_no_pending_items", "actualCalls": 0},
            "staleRunningRecovery": stale,
            "claimMode": "finite_sqlite_lease_native",
        }

    claim_agent1_items(items)
    if not items:
        return {
            "version": AGENT_RUNTIME_NATIVE_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "claimedItemCount": 0,
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "claim_conflict", "actualCalls": 0},
            "staleRunningRecovery": stale,
            "claimMode": "finite_sqlite_lease_native",
        }

    valid_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    missing_signal_items: List[Tuple[Dict[str, Any], str]] = []
    for item in items:
        try:
            valid_pairs.append((item, _signal_from_item(item)))
        except Exception as exc:
            missing_signal_items.append((item, str(exc)))

    for item, reason in missing_signal_items:
        _finish_agent1(
            core,
            item,
            stage=core.AGENT1_FAILED_STAGE,
            status="failed",
            output_ref=f"agent1_failed:{data_version or 'latest'}:{item.get('item_id')}",
            payload={
                "reason": reason,
                "failureOwner": "artifact_transport",
                "version": AGENT_RUNTIME_NATIVE_VERSION,
                "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
                "runtimeSource": "artifactRefs.signalRef",
            },
        )

    if not valid_pairs:
        return {
            "version": AGENT_RUNTIME_NATIVE_VERSION,
            "dataVersion": data_version,
            "ran": True,
            "claimedItemCount": len(items),
            "failedItemCount": len(items),
            "pendingItemCount": core.pending_agent1_item_count(data_version),
            "provider": {"providerStatus": "failed_signal_artifact_missing", "actualCalls": 0},
            "staleRunningRecovery": stale,
            "claimMode": "finite_sqlite_lease_native",
        }

    judgments, provider = core._real_agent_judgments(
        [signal for _, signal in valid_pairs],
        data_version,
        build_operating_policy_context(),
    )
    indexed = core._index_judgments(judgments)
    completed = invalid = failed = observed = 0
    missing_counter: Counter[str] = Counter()
    by_family: Counter[str] = Counter()

    for item, signal in valid_pairs:
        signal_id = str(item.get("signal_id") or "")
        matched = core._match(item, signal, indexed)
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
                    "version": AGENT_RUNTIME_NATIVE_VERSION,
                    "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
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
                    "policyContextVersion": OPERATING_POLICY_CONTEXT_VERSION,
                    "runtimeSource": "artifactRefs.signalRef",
                    "agent1ClaimMode": "finite_sqlite_lease_native",
                },
            )
            continue

        payload = normalize_agent1_completed_contract(
            item=item,
            signal=core._signal_payload(signal),
            judgment=judgment,
            provider=provider,
            data_version=data_version,
        )
        payload.update(
            version=AGENT_RUNTIME_NATIVE_VERSION,
            agent1MicroBatchVersion=AGENT_RUNTIME_NATIVE_VERSION,
            contractVersion=AGENT_RUNTIME_CONTRACT_VERSION,
            policyContextVersion=OPERATING_POLICY_CONTEXT_VERSION,
            rawAgent1Judgment=judgment,
            runtimeSource="artifactRefs.signalRef",
            outputContract="V22.2.6.3.agent1_completed",
            agent1ClaimMode="finite_sqlite_lease_native",
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
        "version": AGENT_RUNTIME_NATIVE_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "ran": True,
        "claimedItemCount": len(items),
        "validSignalArtifactCount": len(valid_pairs),
        "completedItemCount": completed,
        "observedItemCount": observed,
        "invalidItemCount": invalid,
        "failedItemCount": failed + len(missing_signal_items),
        "missingCounter": dict(missing_counter),
        "bySelectedActionFamily": dict(by_family),
        "agentJudgmentCount": len(judgments),
        "pendingItemCount": core.pending_agent1_item_count(data_version),
        "provider": provider,
        "pipelineItemSummary": pipeline_item_summary(data_version=data_version, limit=30),
        "staleRunningRecovery": stale,
        "claimMode": "finite_sqlite_lease_native",
        "runtimeSource": "artifactRefs.signalRef",
    }


def repair_agent2_plan_native(
    plan: Dict[str, Any],
    package: Dict[str, Any],
) -> Dict[str, Any]:
    from src.services import agent2_action_plan_core_v20_service as core
    from src.services.agent_runtime_recovery_v2261_service import apply_roas_execution_target
    from src.services.route_action_department_matrix_v1915_service import selected_family

    result = apply_roas_execution_target(plan, package)
    family = selected_family(package)
    missing = core._contract_missing(result, package, family)
    result["semanticContractMissing"] = missing
    previous_reason = str(result.get("conflictReason") or result.get("reason") or "")
    target_only_previous = bool(previous_reason) and all(
        "target" in part
        and ("id_or_selector" in part or "targetId_or_targetSelector" in part)
        for part in previous_reason.split(":", 1)[-1].split(",")
        if part.strip()
    )
    if not missing:
        result["actionPlanStatus"] = "ready"
        if target_only_previous:
            result["conflictReason"] = None
            result["reason"] = None
    elif result.get("actionPlanStatus") == "ready":
        result["actionPlanStatus"] = "action_plan_missing_data"
        result["conflictReason"] = "Agent2 output did not satisfy V22 contract: " + ",".join(missing)
        result["reason"] = result["conflictReason"]
    result["activeActionContract"] = core.active_action_contract(result)
    projection = result.get("executionTargetProjection") if isinstance(result.get("executionTargetProjection"), dict) else {}
    result["executionTargetProjection"] = {
        **projection,
        "version": AGENT_RUNTIME_NATIVE_VERSION,
        "nativeRuntime": True,
        "validatedBeforeFinalContract": True,
    }
    return result


def run_agent2_microbatch_native(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 5,
    action_family: str | None = None,
) -> Dict[str, Any]:
    from src.services import pipeline_action_microbatch_v205_service as worker
    from src.services.agent2_action_plan_core_v20_service import (
        attach_agent2_action_plans,
        call_agent2_action_plans,
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

    family = action_family or worker._choose_next_family(data_version)
    selected = worker._pending_action_items(
        data_version,
        max(1, min(12, int(batch_size or 5))),
        family,
    )
    if not selected:
        return {
            "version": AGENT_RUNTIME_NATIVE_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "claimedItemCount": 0,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "skipped_no_due_action_pack_ready_items", "actualCalls": 0},
            "targetProjectionMode": "native_before_final_contract",
        }

    valid: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    invalid_missing: Counter[str] = Counter()
    for item in selected:
        package = worker._package_from_action_item(item)
        missing = missing_action_pack_contract(package)
        if missing:
            invalid_missing.update(missing)
            worker._mark_action_pack_invalid(item, missing, package)
        else:
            valid.append((item, package))

    if not valid:
        return {
            "version": AGENT_RUNTIME_NATIVE_VERSION,
            "dataVersion": data_version,
            "ran": True,
            "claimedItemCount": len(selected),
            "validActionPackCount": 0,
            "invalidActionPackCount": len(selected),
            "invalidMissing": dict(invalid_missing),
            "failedItemCount": len(selected),
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "skipped_invalid_action_pack_contract", "actualCalls": 0},
        }

    claimed = claim_agent2_items([item for item, _ in valid], worker_id=user_id)
    claimed_by_id = {str(item.get("item_id")): item for item in claimed}
    valid = [
        (claimed_by_id[str(item.get("item_id"))], package)
        for item, package in valid
        if str(item.get("item_id")) in claimed_by_id
    ]
    if not valid:
        return {
            "version": AGENT_RUNTIME_NATIVE_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "claimedItemCount": 0,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "claim_conflict", "actualCalls": 0},
        }

    packages = [package for _, package in valid]
    plans, provider = call_agent2_action_plans(packages, data_version)
    enriched = attach_agent2_action_plans(packages, plans)
    plan_by_package = {
        str(package.get("packageId") or package.get("itemId")): package.get("agent2ActionPlan")
        for package in enriched
        if package.get("packageId") or package.get("itemId")
    }

    completed = invalid_output = retry_scheduled = dead_lettered = proof_failed = 0
    by_status: Counter[str] = Counter()
    by_failure_class: Counter[str] = Counter()

    for item, package in valid:
        package_id = str(package.get("packageId") or item.get("package_id") or item.get("item_id") or "")
        plan = plan_by_package.get(package_id)
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
        if status != "ready" or missing:
            invalid_output += 1
            worker._mark_agent2_output_invalid(item, package, plan, provider, missing)
            continue

        completed += 1
        candidate.update(
            agent2Source=plan.get("agent2Source"),
            fallbackAllowed=False,
            taskAdmissionAllowed=True,
            agentRuntimeNativeVersion=AGENT_RUNTIME_NATIVE_VERSION,
            targetProjectionMode="native_before_final_contract",
        )
        worker._finish_item(
            item,
            stage=worker.AGENT2_COMPLETED_STAGE,
            status="ready",
            output_ref=f"agent2_action_plan:{data_version or 'latest'}:{package_id}",
            payload=candidate,
        )

    return {
        "version": AGENT_RUNTIME_NATIVE_VERSION,
        "dataVersion": data_version,
        "ran": True,
        "actionFamily": family,
        "claimedItemCount": len(valid),
        "validActionPackCount": len(valid),
        "invalidActionPackCount": len(selected) - len(valid),
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
        "targetProjectionMode": "native_before_final_contract",
    }


def run_agent_pipeline_tick_native(
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
        return {"version": AGENT_RUNTIME_NATIVE_VERSION, "ran": False, "reason": "no_data_version"}

    stale = recover_stale_agent1_items(resolved)
    target_recovery = recover_target_only_agent2_failures_native(resolved)
    contract_recovery = legacy.recover_version_only_action_pack_invalid(resolved)

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
        result = run_agent2_microbatch_native(
            data_version=resolved,
            user_id=user_id,
            batch_size=agent2_batch_size,
        )
        selected = "action_pack_ready_to_agent2_completed_native"
    elif legacy._load_agent1_completed_items(resolved, 1):
        result = legacy.seed_action_pack_from_agent1_items(
            resolved,
            batch_size=action_pack_batch_size,
            source="agent_runtime_native_v2263",
        )
        selected = "agent1_completed_to_action_pack_ready"
    elif pending_agent1_item_count(resolved) > 0:
        result = run_agent1_microbatch_native(
            data_version=resolved,
            user_id=user_id,
            batch_size=agent1_batch_size,
        )
        selected = "agent1_pending_to_agent1_completed_or_observed_native"
    else:
        result = {"ran": False, "claimedItemCount": 0, "reason": "no_runnable_agent_pipeline_items"}
        selected = "idle"

    ran = bool(result.get("ran")) if "ran" in result else int(result.get("claimedItemCount") or 0) > 0
    return {
        "version": AGENT_RUNTIME_NATIVE_VERSION,
        "contractVersion": AGENT_RUNTIME_NATIVE_VERSION,
        "ran": ran,
        "workerId": worker_id,
        "selectedStage": selected,
        "dataVersion": resolved,
        "contractRecovery": contract_recovery,
        "agent1StaleRunningRecovery": stale,
        "agent2TargetContractRecovery": target_recovery,
        "result": result,
        "agent1PendingHandled": selected.endswith("_native") and selected.startswith("agent1_pending"),
        "runtimeSource": "pipeline_items.artifact_refs_json",
        "executionMode": "native_functions_no_monkey_patch",
    }


def startup_agent_runtime_native() -> Dict[str, Any]:
    from src.services.agent_runtime_recovery_v2261_service import (
        ensure_agent1_runtime_columns,
        recover_stale_agent1_items,
    )

    ensure_agent1_runtime_columns()
    latest = latest_data_version()
    return {
        "version": AGENT_RUNTIME_NATIVE_VERSION,
        "dataVersion": latest,
        "agent1": recover_stale_agent1_items(latest) if latest else {},
        "agent2": recover_target_only_agent2_failures_native(latest) if latest else {},
        "executionMode": "native_functions_no_monkey_patch",
    }


def agent_runtime_integrity_status() -> Dict[str, Any]:
    return {
        "version": AGENT_RUNTIME_NATIVE_VERSION,
        "native": True,
        "monkeyPatchRequired": False,
        "agent1ClaimMode": "finite_sqlite_lease_native",
        "agent1TerminalLeaseRelease": True,
        "agent2TargetProjection": "native_before_final_contract",
        "agent2TargetRepairLimit": MAX_TARGET_REPAIRS,
        "runtimeEntry": "agent_runtime_native_v2263_service.run_agent_pipeline_tick_native",
        "legacyPayloadFallbackAllowed": False,
        "legacySignalPoolFallbackAllowed": False,
    }


__all__ = [
    "AGENT_RUNTIME_NATIVE_VERSION",
    "MAX_TARGET_REPAIRS",
    "latest_data_version",
    "recover_target_only_agent2_failures_native",
    "run_agent1_microbatch_native",
    "repair_agent2_plan_native",
    "run_agent2_microbatch_native",
    "run_agent_pipeline_tick_native",
    "startup_agent_runtime_native",
    "agent_runtime_integrity_status",
]
