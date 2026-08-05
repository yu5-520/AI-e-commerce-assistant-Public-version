"""V22.5.14 Agent2 runtime.

Repairs two runtime gaps without rerunning Agent1:
1. projection-budget failures are requeued after the compact evidence-slice compiler;
2. Agent2 drafts consume only the V22.5.14 projected Artifact.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect
from src.services import agent_runtime_hard_interface_v225_service as legacy_runtime
from src.services.agent2_provenance_v2141_service import valid_agent2_execution_proof
from src.services.agent2_runtime_resilience_v2143_service import (
    claim_agent2_items,
    schedule_agent2_failure,
)
from src.services.agent_input_contract_v225_service import AGENT2_DRAFT_INPUT_SCHEMA
from src.services.agent_input_transport_v22514_service import (
    AGENT2_CONTEXT_DEDUP_VERSION,
    AGENT2_EVIDENCE_SLICE_VERSION,
    AGENT_INPUT_TRANSPORT_VERSION,
    ensure_agent2_draft_input_ref,
    resolve_agent2_draft_source,
    resolve_agent_input_ref,
)
from src.services.agent_input_transport_v225_service import AgentInputProjectionError
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

AGENT2_RUNTIME_VERSION = "22.5.14"
AGENT2_DRAFT_INPUT_INVALID_STAGE = "agent2_draft_input_invalid"
_PROJECTION_FAILURE_STAGES = (
    "action_pack_invalid",
    AGENT2_DRAFT_INPUT_INVALID_STAGE,
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


def migrate_agent2_projection_failures_v22514(
    data_version: str | None = None,
    *,
    limit: int = 500,
) -> Dict[str, Any]:
    """Requeue only valid Action Packs stopped by the old oversized projection."""
    marks = ",".join("?" for _ in _PROJECTION_FAILURE_STAGES)
    where = [f"current_stage IN ({marks})", "status='failed'"]
    params: List[Any] = list(_PROJECTION_FAILURE_STAGES)
    if data_version is not None:
        where.append("COALESCE(data_version,'')=COALESCE(?,'')")
        params.append(data_version)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(where)} "
            "ORDER BY updated_at ASC LIMIT ?",
            (*params, max(1, min(2000, int(limit)))),
        ).fetchall()

    recovered: List[str] = []
    rejected: Dict[str, str] = {}
    by_stage: Counter[str] = Counter()
    for raw in rows:
        row = dict(raw)
        item_id = str(row.get("item_id") or "")
        stage = str(row.get("current_stage") or "")
        by_stage[stage] += 1
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
                  AND current_stage IN ('action_pack_invalid','agent2_draft_input_invalid')
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
        "version": AGENT2_RUNTIME_VERSION,
        "candidateCount": len(rows),
        "candidateByStage": dict(by_stage),
        "recoveredItemCount": len(recovered),
        "recoveredItemIds": recovered,
        "rejected": rejected,
        "agent1Rerun": False,
        "observedItemsTouched": False,
        "providerCallsExecuted": 0,
        "recoveryRule": (
            "projection_budget_failure + valid capability Artifact + "
            "valid Action Pack -> action_pack_ready/retry"
        ),
    }


def run_agent2_draft_microbatch_hard(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = 5,
    action_family: str | None = None,
) -> Dict[str, Any]:
    from src.services import pipeline_action_microbatch_v205_service as worker

    family = action_family or worker._choose_next_family(data_version)
    selected = worker._pending_action_items(
        data_version,
        max(1, min(12, int(batch_size or 5))),
        family,
    )
    if not selected:
        return {
            "version": AGENT2_RUNTIME_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "claimedItemCount": 0,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {
                "providerStatus": "skipped_no_due_action_pack_ready_items",
                "actualCalls": 0,
            },
            "runtimeSource": "agent2DraftInputRef.v22514",
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
            legacy_runtime._mark_agent2_draft_input_invalid(
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
            legacy_runtime._mark_agent2_draft_input_invalid(
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
            legacy_runtime._mark_agent2_draft_input_invalid(
                worker,
                item,
                source,
                reason="agent2_draft_input_contract_invalid",
                missing=["agent2DraftInputRef", str(exc)[:180]],
            )

    if not prepared:
        return {
            "version": AGENT2_RUNTIME_VERSION,
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
            "runtimeSource": "agent2DraftInputRef.v22514",
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
            "version": AGENT2_RUNTIME_VERSION,
            "dataVersion": data_version,
            "ran": False,
            "selectedItemCount": len(selected),
            "claimedItemCount": 0,
            "invalidActionPackCount": invalid_action_pack_count,
            "invalidAgent2DraftInputCount": invalid_input_count,
            "pendingItemCount": worker.pending_agent2_item_count(data_version),
            "provider": {"providerStatus": "claim_conflict", "actualCalls": 0},
            "runtimeSource": "agent2DraftInputRef.v22514",
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
            runtimeSource="agent2DraftInputRef.v22514",
            sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
            inputProjectionAudit=envelope.get("projectionAudit"),
            outputContract="V22.5.14.agent2_draft_ready",
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
        "version": AGENT2_RUNTIME_VERSION,
        "contextDedupVersion": AGENT2_CONTEXT_DEDUP_VERSION,
        "evidenceSliceVersion": AGENT2_EVIDENCE_SLICE_VERSION,
        "transportVersion": AGENT_INPUT_TRANSPORT_VERSION,
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
        "runtimeSource": "agent2DraftInputRef.v22514",
        "executionMode": "action_evidence_slice_artifact_only",
        "fallbackAllowed": False,
    }


__all__ = [
    "AGENT2_RUNTIME_VERSION",
    "migrate_agent2_projection_failures_v22514",
    "run_agent2_draft_microbatch_hard",
]
