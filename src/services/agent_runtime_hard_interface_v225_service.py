"""V22.5 three-Agent hard-interface runtime.

V22.5.1 separates Action Pack failures from Agent2 input-transport failures and
recovers only rows that were previously misclassified because a valid Action Pack
produced an over-budget duplicated projection. Agent1 and observed items are never
requeued by this repair.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect
from src.services.agent_input_contract_v225_service import AGENT2_DRAFT_INPUT_SCHEMA
from src.services.agent_input_transport_v225_service import (
    AGENT_INPUT_TRANSPORT_VERSION,
    AgentInputProjectionError,
    ensure_agent2_draft_input_ref,
    resolve_agent2_draft_source,
    resolve_agent_input_ref,
)
from src.services.agent_runtime_contract_v225_service import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    missing_action_pack_contract,
    missing_agent2_draft_completed_contract,
    normalize_agent2_draft_completed_contract,
    payload_from_row,
)
from src.services.agent_token_runtime_v225_service import run_agent2_draft_projected_inputs
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)
from src.services.pipeline_item_service import STAGE_ORDER

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
AGENT_RUNTIME_HARD_INTERFACE_VERSION = THREE_AGENT_PIPELINE_VERSION
AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION = "22.5.1"
AGENT2_DRAFT_INPUT_INVALID_STAGE = "agent2_draft_input_invalid"

STAGE_ORDER.update(
    {
        AGENT2_DRAFT_INPUT_INVALID_STAGE: 81,
        "agent2_draft_ready": 82,
        "agent2_draft_output_invalid": 83,
        "agent2_draft_failed": 84,
        "agent3_sop_running": 85,
        "agent3_sop_ready": 88,
        "agent3_sop_output_invalid": 89,
        "agent3_sop_failed": 89,
        "task_mapped": 94,
        "task_mapping_failed": 95,
    }
)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _payload_mentions_projection_budget(value: Any) -> bool:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return (
        "projection_item_budget_exceeded" in text
        or "agent2_draft_input_projection_budget_exceeded" in text
    )


def migrate_legacy_agent2_outputs(data_version: str | None = None) -> Dict[str, Any]:
    """Requeue only old final-plan semantic failures under the new draft contract."""
    where = ["current_stage='agent2_output_invalid'", "status='failed'"]
    params: List[Any] = []
    if data_version is not None:
        where.append("COALESCE(data_version,'')=COALESCE(?,'')")
        params.append(data_version)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT item_id FROM pipeline_items WHERE {' AND '.join(where)}",
            tuple(params),
        ).fetchall()
        item_ids = [str(row["item_id"]) for row in rows]
        for item_id in item_ids:
            conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage='action_pack_ready',status='retry',retry_count=0,
                    claim_id=NULL,lease_expires_at=NULL,retry_after=NULL,
                    failure_code=NULL,failure_class=NULL,error_reason=NULL,
                    last_error_code=NULL,last_error_artifact_ref=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE item_id=? AND current_stage='agent2_output_invalid' AND status='failed'
                """,
                (item_id,),
            )
        conn.commit()
    return {
        "version": THREE_AGENT_PIPELINE_VERSION,
        "legacyAgent2OutputInvalidFound": len(item_ids),
        "requeuedAsAgent2DraftPending": len(item_ids),
        "agent1Rerun": False,
        "observedItemsTouched": False,
    }


def migrate_misclassified_agent2_input_failures(
    data_version: str | None = None,
    *,
    limit: int = 200,
) -> Dict[str, Any]:
    """Recover valid Action Packs mislabelled as action_pack_invalid.

    A row is eligible only when its stored failure mentions the projection budget,
    its capability Artifact is valid, and the original Action Pack currently passes
    the Action Pack contract. This deliberately excludes Agent1 and observation rows.
    """
    where = ["current_stage='action_pack_invalid'", "status='failed'"]
    params: List[Any] = []
    if data_version is not None:
        where.append("COALESCE(data_version,'')=COALESCE(?,'')")
        params.append(data_version)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(where)} "
            "ORDER BY updated_at ASC LIMIT ?",
            (*params, max(1, min(1000, int(limit)))),
        ).fetchall()

    recovered: List[str] = []
    rejected: Dict[str, str] = {}
    for raw in rows:
        row = dict(raw)
        item_id = str(row.get("item_id") or "")
        try:
            failure_payload = dict(payload_from_row(row))
        except Exception as exc:
            rejected[item_id] = f"failure_payload_unreadable:{str(exc)[:180]}"
            continue
        if not _payload_mentions_projection_budget(failure_payload):
            rejected[item_id] = "not_projection_budget_failure"
            continue
        try:
            _source_ref, _source_hash, source = resolve_agent2_draft_source(row)
        except Exception as exc:
            rejected[item_id] = f"capability_source_invalid:{str(exc)[:180]}"
            continue
        missing = missing_action_pack_contract(source)
        if missing:
            rejected[item_id] = "action_pack_still_invalid:" + ",".join(missing[:12])
            continue
        with connect() as conn:
            cursor = conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage='action_pack_ready',status='retry',retry_count=0,
                    claim_id=NULL,lease_expires_at=NULL,retry_after=NULL,
                    failure_code=NULL,failure_class=NULL,error_reason=NULL,
                    last_error_code=NULL,last_error_artifact_ref=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE item_id=?
                  AND current_stage='action_pack_invalid'
                  AND status='failed'
                """,
                (item_id,),
            )
            conn.commit()
        if cursor.rowcount == 1:
            recovered.append(item_id)
        else:
            rejected[item_id] = "state_changed_before_recovery"
    return {
        "version": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
        "candidateCount": len(rows),
        "recoveredItemCount": len(recovered),
        "recoveredItemIds": recovered,
        "rejected": rejected,
        "agent1Rerun": False,
        "observedItemsTouched": False,
        "recoveryRule": (
            "action_pack_invalid + projection_item_budget_exceeded + "
            "valid capability Artifact + action pack contract passed"
        ),
    }


def run_agent1_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 8,
) -> Dict[str, Any]:
    from src.services.agent_runtime_hard_interface_v230_service import run_agent1_microbatch_hard as legacy

    result = legacy(
        data_version,
        user_id=user_id,
        batch_size=batch_size,
    )
    result["threeAgentPipelineVersion"] = THREE_AGENT_PIPELINE_VERSION
    return result


def _mark_agent2_draft_input_invalid(
    worker: Any,
    item: Dict[str, Any],
    package: Dict[str, Any],
    *,
    reason: str,
    missing: List[str],
    projection_audit: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    package_id = str(
        package.get("packageId")
        or item.get("package_id")
        or item.get("item_id")
        or ""
    )
    result = worker._finish_item(
        item,
        stage=AGENT2_DRAFT_INPUT_INVALID_STAGE,
        status="failed",
        output_ref=(
            f"agent2_draft_input_invalid:{item.get('data_version') or 'latest'}:{package_id}"
        ),
        payload={
            **package,
            "reason": reason,
            "lastErrorCode": reason,
            "missing": list(dict.fromkeys(missing)),
            "projectionAudit": projection_audit or {},
            "runtimeSource": "agent2DraftInputRef",
            "inputTransportVersion": AGENT_INPUT_TRANSPORT_VERSION,
            "failureOwner": "agent_input_transport_v225",
            "frontendFailureLabel": "Agent2输入投影失败",
            "taskAdmissionAllowed": False,
            "fallbackAllowed": False,
        },
        station_id="agent2_draft_input_transport_station",
    )
    artifact_ref = str(result.get("payloadArtifactRef") or "")
    if artifact_ref.startswith("ART-"):
        attach_pipeline_artifact_ref(
            str(item.get("item_id")),
            "agent2DraftInputFailureRef",
            artifact_ref,
        )
    return result


def run_agent2_draft_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 5,
    action_family: str | None = None,
) -> Dict[str, Any]:
    from src.services import pipeline_action_microbatch_v205_service as worker
    from src.services.agent2_provenance_v2141_service import valid_agent2_execution_proof
    from src.services.agent2_runtime_resilience_v2143_service import (
        claim_agent2_items,
        schedule_agent2_failure,
    )

    family = action_family or worker._choose_next_family(data_version)
    selected = worker._pending_action_items(
        data_version,
        max(1, min(12, int(batch_size or 5))),
        family,
    )
    if not selected:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "contextDedupHotfixVersion": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "claimedItemCount": 0,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {
                "providerStatus": "skipped_no_due_action_pack_ready_items",
                "actualCalls": 0,
            },
            "runtimeSource": "agent2DraftInputRef",
            "fallbackAllowed": False,
        }

    prepared: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = {}
    invalid_action_pack_missing: Counter[str] = Counter()
    invalid_input_missing: Counter[str] = Counter()
    invalid_action_pack_count = 0
    invalid_input_count = 0

    for item in selected:
        try:
            _source_ref, _source_hash, source = resolve_agent2_draft_source(item)
        except Exception as exc:
            invalid_input_count += 1
            invalid_input_missing.update(["agent2DraftInputRef", "capability_source_unreadable"])
            _mark_agent2_draft_input_invalid(
                worker,
                item,
                {
                    "packageId": item.get("package_id") or item.get("item_id"),
                    "productId": item.get("product_id"),
                    "storeId": item.get("store_id"),
                    "lockedActionFamily": item.get("action_family"),
                },
                reason="agent2_draft_input_source_invalid",
                missing=["agent2DraftInputRef", str(exc)[:180]],
            )
            continue

        missing = missing_action_pack_contract(source)
        if missing:
            invalid_action_pack_count += 1
            invalid_action_pack_missing.update(missing)
            worker._mark_action_pack_invalid(item, missing, source)
            continue

        try:
            input_ref = ensure_agent2_draft_input_ref(item)
            envelope = resolve_agent_input_ref(
                input_ref,
                expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
            )
            package = dict(envelope["payload"])
            prepared[str(item.get("item_id"))] = (item, envelope, package)
        except AgentInputProjectionError as exc:
            invalid_input_count += 1
            invalid_input_missing.update(
                ["agent2DraftInputRef", "projection_item_budget_exceeded"]
            )
            _mark_agent2_draft_input_invalid(
                worker,
                item,
                source,
                reason=exc.code,
                missing=["agent2DraftInputRef", "projection_item_budget_exceeded"],
                projection_audit=exc.audit,
            )
        except Exception as exc:
            invalid_input_count += 1
            invalid_input_missing.update(["agent2DraftInputRef"])
            _mark_agent2_draft_input_invalid(
                worker,
                item,
                source,
                reason="agent2_draft_input_contract_invalid",
                missing=["agent2DraftInputRef", str(exc)[:180]],
            )

    if not prepared:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "contextDedupHotfixVersion": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
            "dataVersion": data_version,
            "ran": True,
            "selectedItemCount": len(selected),
            "claimedItemCount": 0,
            "validAgent2DraftInputCount": 0,
            "invalidActionPackCount": invalid_action_pack_count,
            "invalidAgent2DraftInputCount": invalid_input_count,
            "invalidActionPackMissing": dict(invalid_action_pack_missing),
            "invalidAgent2DraftInputMissing": dict(invalid_input_missing),
            "failedItemCount": invalid_action_pack_count + invalid_input_count,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {
                "providerStatus": "skipped_invalid_action_pack_or_agent2_input",
                "actualCalls": 0,
            },
            "runtimeSource": "agent2DraftInputRef",
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
            "contextDedupHotfixVersion": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "selectedItemCount": len(selected),
            "claimedItemCount": 0,
            "invalidActionPackCount": invalid_action_pack_count,
            "invalidAgent2DraftInputCount": invalid_input_count,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "claim_conflict", "actualCalls": 0},
            "runtimeSource": "agent2DraftInputRef",
        }

    drafts, provider = run_agent2_draft_projected_inputs(
        [value[1] for value in prepared.values()],
        data_version=data_version,
        max_items_per_call=batch_size,
    )
    completed = invalid_output = retry_scheduled = dead_lettered = proof_failed = 0
    by_status: Counter[str] = Counter()
    by_failure: Counter[str] = Counter()

    for item, envelope, package in prepared.values():
        package_id = str(
            package.get("packageId")
            or item.get("package_id")
            or item.get("item_id")
            or ""
        )
        draft = drafts.get(package_id)
        proof = _dict(_dict(provider.get("itemProvenance")).get(package_id))
        if not isinstance(draft, dict) or not draft:
            outcome = schedule_agent2_failure(
                item,
                package,
                provider,
                next(
                    (
                        str(value)
                        for value in provider.get("errors") or []
                        if package_id in str(value)
                    ),
                    None,
                )
                or "agent2_draft_returned_no_plan",
            )
            by_failure[str(outcome.get("failureClass"))] += 1
            retry_scheduled += 1 if outcome.get("status") == "retry" else 0
            dead_lettered += 1 if outcome.get("terminal") else 0
            continue
        if not valid_agent2_execution_proof(proof):
            proof_failed += 1
            outcome = schedule_agent2_failure(
                item,
                package,
                provider,
                "agent2_draft_item_provenance_missing",
            )
            by_failure[str(outcome.get("failureClass"))] += 1
            retry_scheduled += 1 if outcome.get("status") == "retry" else 0
            dead_lettered += 1 if outcome.get("terminal") else 0
            continue
        by_status[str(draft.get("draftStatus") or "missing")] += 1
        candidate = normalize_agent2_draft_completed_contract(package, draft, provider)
        missing = missing_agent2_draft_completed_contract(candidate)
        if missing:
            invalid_output += 1
            result = worker._finish_item(
                item,
                stage="agent2_draft_output_invalid",
                status="failed",
                output_ref=(
                    f"agent2_draft_output_invalid:{data_version or 'latest'}:{package_id}"
                ),
                payload={
                    **candidate,
                    "reason": "agent2_draft_contract_invalid",
                    "missing": missing,
                    "failureOwner": "agent2_action_draft_station",
                    "frontendFailureLabel": "Agent2草案不完整",
                    "taskAdmissionAllowed": False,
                    "fallbackAllowed": False,
                },
                station_id="agent2_action_draft_station",
            )
            artifact_ref = str(result.get("payloadArtifactRef") or "")
            if artifact_ref.startswith("ART-"):
                attach_pipeline_artifact_ref(
                    str(item.get("item_id")),
                    "agent2DraftFailureRef",
                    artifact_ref,
                )
            continue
        refs = artifact_refs_from_row(item)
        candidate.update(
            agent2DraftInputRef=str(refs.get("agent2DraftInputRef") or ""),
            runtimeSource="agent2DraftInputRef",
            sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
            inputProjectionAudit=envelope.get("projectionAudit"),
            outputContract="V22.5.agent2_draft_ready",
            fallbackAllowed=False,
            taskAdmissionAllowed=False,
        )
        result = worker._finish_item(
            item,
            stage="agent2_draft_ready",
            status="ready",
            output_ref=f"agent2_action_draft:{data_version or 'latest'}:{package_id}",
            payload=candidate,
            station_id="agent2_action_draft_station",
        )
        artifact_ref = str(result.get("payloadArtifactRef") or "")
        if artifact_ref.startswith("ART-"):
            attach_pipeline_artifact_ref(
                str(item.get("item_id")),
                "agent2DraftRef",
                artifact_ref,
            )
        completed += 1

    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "contextDedupHotfixVersion": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "dataVersion": data_version,
        "ran": True,
        "actionFamily": family,
        "selectedItemCount": len(selected),
        "claimedItemCount": len(prepared),
        "validAgent2DraftInputCount": len(prepared),
        "invalidActionPackCount": invalid_action_pack_count,
        "invalidAgent2DraftInputCount": invalid_input_count,
        "invalidActionPackMissing": dict(invalid_action_pack_missing),
        "invalidAgent2DraftInputMissing": dict(invalid_input_missing),
        "completedItemCount": completed,
        "invalidOutputItemCount": invalid_output,
        "retryScheduledItemCount": retry_scheduled,
        "deadLetteredItemCount": dead_lettered,
        "proofFailedItemCount": proof_failed,
        "failedItemCount": (
            invalid_action_pack_count + invalid_input_count + invalid_output + dead_lettered
        ),
        "draftCount": len(drafts),
        "byDraftStatus": dict(by_status),
        "byFailureClass": dict(by_failure),
        "pendingItemCount": worker.pending_agent2_item_count(data_version),
        "provider": provider,
        "runtimeSource": "agent2DraftInputRef",
        "executionMode": "three_agent_projection_artifact_only",
        "fallbackAllowed": False,
    }


def _runnable_stages() -> tuple[str, ...]:
    return (
        "agent1_pending",
        "agent1_completed",
        "action_pack_ready",
        "agent2_draft_ready",
        "agent3_sop_ready",
        "task_mapped",
    )


def select_runnable_data_version_v225(preferred: str | None = None) -> str | None:
    stages = _runnable_stages()
    marks = ",".join("?" for _ in stages)
    with connect() as conn:
        if preferred:
            row = conn.execute(
                f"""
                SELECT 1 FROM pipeline_items
                WHERE data_version=? AND current_stage IN ({marks})
                  AND status IN ('queued','ready','retry','completed')
                LIMIT 1
                """,
                (preferred, *stages),
            ).fetchone()
            if row:
                return preferred
        row = conn.execute(
            f"""
            SELECT data_version,MIN(COALESCE(priority,50)) AS p,
                   MIN(COALESCE(updated_at,created_at)) AS oldest
            FROM pipeline_items
            WHERE data_version IS NOT NULL AND TRIM(data_version)!=''
              AND current_stage IN ({marks})
              AND status IN ('queued','ready','retry','completed')
            GROUP BY data_version
            ORDER BY p ASC,oldest ASC,data_version ASC
            LIMIT 1
            """,
            stages,
        ).fetchone()
    return str(row["data_version"]) if row and row["data_version"] else None


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
    **_: Any,
) -> Dict[str, Any]:
    from src.services import agent_pipeline_item_worker_v2010_service as legacy
    from src.services.agent_runtime_recovery_v2261_service import recover_stale_agent1_items
    from src.services.pipeline_action_microbatch_v205_service import pending_agent2_item_count
    from src.services.pipeline_agent1_microbatch_v20101_service import pending_agent1_item_count
    from src.services.pipeline_agent3_sop_v225_service import (
        pending_agent3_sop_item_count,
        recover_stale_agent3_claims,
        run_agent3_sop_microbatch_v225,
    )
    from src.services.pipeline_task_mapping_v225_service import (
        pending_task_mapping_item_count,
        pending_task_pool_item_count,
        run_task_mapping_microbatch_v225,
        run_task_pool_admission_microbatch_v225,
    )

    legacy_migration = migrate_legacy_agent2_outputs(data_version)
    input_migration = migrate_misclassified_agent2_input_failures(data_version)
    resolved = data_version or select_runnable_data_version_v225()
    if not resolved:
        return {
            "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
            "contextDedupHotfixVersion": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
            "ran": False,
            "reason": "no_data_version",
            "legacyAgent2Migration": legacy_migration,
            "agent2InputMigration": input_migration,
        }
    stale1 = recover_stale_agent1_items(resolved)
    stale3 = recover_stale_agent3_claims(resolved)

    if pending_task_pool_item_count(resolved) > 0:
        result = run_task_pool_admission_microbatch_v225(
            resolved,
            user_id=user_id,
            batch_size=pool_batch_size,
            force_new_snapshot=force_new_snapshot,
        )
        selected = "task_mapped_to_task_admitted"
    elif pending_task_mapping_item_count(resolved) > 0:
        result = run_task_mapping_microbatch_v225(
            resolved,
            batch_size=mapping_batch_size,
        )
        selected = "agent3_sop_ready_to_task_mapped"
    elif pending_agent3_sop_item_count(resolved) > 0:
        result = run_agent3_sop_microbatch_v225(
            resolved,
            user_id=user_id,
            batch_size=agent3_batch_size,
        )
        selected = "agent2_draft_ready_to_agent3_sop_ready"
    elif pending_agent2_item_count(resolved) > 0:
        result = run_agent2_draft_microbatch_hard(
            resolved,
            user_id=user_id,
            batch_size=agent2_batch_size,
        )
        selected = "agent2DraftInputRef_to_agent2_draft_ready"
    elif legacy._load_agent1_completed_items(resolved, 1):
        result = legacy.seed_action_pack_from_agent1_items(
            resolved,
            batch_size=action_pack_batch_size,
            source="agent_runtime_hard_interface_v225",
        )
        selected = "agent1_completed_to_action_pack_ready"
    elif pending_agent1_item_count(resolved) > 0:
        result = run_agent1_microbatch_hard(
            resolved,
            user_id=user_id,
            batch_size=agent1_batch_size,
        )
        selected = "agent1InputRef_to_agent1_completed_or_observed"
    else:
        result = {
            "ran": False,
            "claimedItemCount": 0,
            "reason": "no_runnable_agent_pipeline_items",
        }
        selected = "idle"
    ran = (
        bool(result.get("ran"))
        if "ran" in result
        else int(result.get("claimedItemCount") or 0) > 0
    )
    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "contextDedupHotfixVersion": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "ran": ran,
        "workerId": worker_id,
        "selectedStage": selected,
        "dataVersion": resolved,
        "legacyAgent2Migration": legacy_migration,
        "agent2InputMigration": input_migration,
        "agent1StaleRunningRecovery": stale1,
        "agent3StaleRunningRecovery": stale3,
        "result": result,
        "runtimeSource": "agent1InputRef_or_agent2DraftInputRef_or_agent3SopInputRef",
        "executionMode": "three_agent_projection_artifact_only",
        "fallbackAllowed": False,
    }


def startup_agent_runtime_hard() -> Dict[str, Any]:
    from src.services.agent_runtime_hard_interface_v230_service import startup_agent_runtime_hard as legacy_startup
    from src.services.pipeline_agent3_sop_v225_service import recover_stale_agent3_claims

    legacy = legacy_startup()
    legacy_migration = migrate_legacy_agent2_outputs(None)
    input_migration = migrate_misclassified_agent2_input_failures(None)
    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "contextDedupHotfixVersion": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
        "legacyRuntimeStartup": legacy,
        "legacyAgent2Migration": legacy_migration,
        "agent2InputMigration": input_migration,
        "agent3": recover_stale_agent3_claims(None),
        "executionMode": "three_agent_projection_artifact_only",
        "fallbackAllowed": False,
    }


def agent_runtime_hard_interface_status() -> Dict[str, Any]:
    return {
        "version": AGENT_RUNTIME_HARD_INTERFACE_VERSION,
        "threeAgentPipelineVersion": THREE_AGENT_PIPELINE_VERSION,
        "contextDedupHotfixVersion": AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION,
        "agentInputTransportVersion": AGENT_INPUT_TRANSPORT_VERSION,
        "hardInterface": True,
        "agent1RuntimeSource": "artifactRefs.agent1InputRef",
        "agent2RuntimeSource": "artifactRefs.agent2DraftInputRef",
        "agent3RuntimeSource": "artifactRefs.agent3SopInputRef",
        "agent2OutputContract": "agent2.action_draft.v1",
        "agent3OutputContract": "agent3.sop.v1",
        "agent2InputFailureStage": AGENT2_DRAFT_INPUT_INVALID_STAGE,
        "taskMappingMode": "deterministic_agent3_projection_only",
        "fullSignalReadByAgentAllowed": False,
        "fullCapabilityReadByAgentAllowed": False,
        "fullUpstreamArtifactReadByAgent3Allowed": False,
        "unprojectedProviderInputAllowed": False,
        "tokenRuntimeOwner": "agent_token_runtime_v225",
        "transportOwner": "agent_input_transport_v225",
        "agent1FullArtifactDownstreamReadAllowed": False,
        "agent1HandoffDeduplicated": True,
        "fallbackAllowed": False,
        "executionMode": "three_agent_projection_artifact_only",
    }


run_agent2_microbatch_hard = run_agent2_draft_microbatch_hard


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "AGENT_RUNTIME_HARD_INTERFACE_VERSION",
    "AGENT2_CONTEXT_DEDUP_HOTFIX_VERSION",
    "AGENT2_DRAFT_INPUT_INVALID_STAGE",
    "migrate_legacy_agent2_outputs",
    "migrate_misclassified_agent2_input_failures",
    "run_agent1_microbatch_hard",
    "run_agent2_draft_microbatch_hard",
    "run_agent2_microbatch_hard",
    "select_runnable_data_version_v225",
    "run_agent_pipeline_tick_hard",
    "startup_agent_runtime_hard",
    "agent_runtime_hard_interface_status",
]
