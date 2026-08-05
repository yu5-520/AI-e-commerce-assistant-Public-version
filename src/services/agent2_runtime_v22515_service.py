"""V22.5.15 Agent2 runtime and accepted-output reconciliation.

The Provider-facing input remains the compact V22.5.14 evidence slice. Output
acceptance now uses the V22.5.9 hash execution index and immutable output Artifact
as authority. V21 item provenance remains a compatibility fallback only.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect
from src.services import agent_runtime_hard_interface_v225_service as legacy_runtime
from src.services import agent2_runtime_v22514_service as legacy_v22514
from src.services.agent2_action_draft_core_v225_service import (
    DRAFT_CONFLICT,
    DRAFT_MISSING_DATA,
    DRAFT_READY,
    DRAFT_REJECTED,
    missing_agent2_draft_contract,
    repairable_agent2_contract_missing,
)
from src.services.agent2_hash_proof_bridge_v22515_service import (
    AGENT2_HASH_PROOF_BRIDGE_VERSION,
    Agent2HashProofError,
    bridge_agent2_hash_proof,
    build_agent2_contract_repair_envelope,
    build_agent2_generation_envelope,
    build_agent2_regeneration_envelope,
    finalize_agent2_execution_acceptance,
    hash_proof_provider_summary,
    record_agent2_runtime_outcome,
    revoked_agent2_execution_for_input,
    revoke_agent2_execution,
)
from src.services.agent2_provenance_v2141_service import (
    proof_for_package,
    valid_agent2_execution_proof,
)
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
)
from src.services.agent_token_runtime_v225_service import run_agent2_draft_projected_inputs
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)

AGENT2_RUNTIME_VERSION = "22.5.15"
AGENT2_NONREADY_ROUTING_VERSION = "23.2.5"
AGENT2_RUNTIME_SOLID_HASH_VERSION = "23.2.6"
AGENT2_GENERATION_COMPILER_VERSION = "23.2.8"
AGENT2_DEAD_LETTER_STAGE = "agent2_dead_letter"
_AGENT2_PROOF_FAILURE_MARKERS = (
    "agent2_draft_item_provenance_missing",
    "agent2_item_provenance_missing",
    "agent2_execution_proof_invalid",
    "agent2_not_dispatched_or_replay_missing",
)
_NONREADY_STAGE_BY_STATUS = {
    DRAFT_MISSING_DATA: "agent2_missing_data_hold",
    DRAFT_CONFLICT: "agent2_conflict_hold",
    DRAFT_REJECTED: "agent2_rejected_hold",
}
_NONREADY_LABEL_BY_STATUS = {
    DRAFT_MISSING_DATA: "Agent2等待补充数据",
    DRAFT_CONFLICT: "Agent2发现输入冲突",
    DRAFT_REJECTED: "Agent2拒绝生成草案",
}
_PRECISE_CONTRACT_FAILURE_CODES = (
    "agent2_missing_data_reason_missing",
    "agent2_conflict_reason_missing",
    "agent2_rejected_reason_missing",
)

migrate_agent2_projection_failures_v22514 = (
    legacy_v22514.migrate_agent2_projection_failures_v22514
)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 800) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mentions_hash_bridge_candidate(value: Any) -> bool:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return any(marker in text for marker in _AGENT2_PROOF_FAILURE_MARKERS)


def _clear_failure_state(item_id: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_items
            SET retry_count=0,retry_after=NULL,claim_id=NULL,lease_expires_at=NULL,
                failure_code=NULL,failure_class=NULL,error_reason=NULL,
                last_error_code=NULL,last_error_artifact_ref=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE item_id=?
            """,
            (item_id,),
        )
        conn.commit()



def _persist_precise_failure_state(
    item_id: str,
    *,
    reason: str,
    artifact_ref: str | None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_items
            SET failure_code=?,failure_class='agent2_contract',error_reason=?,
                last_error_code=?,last_error_artifact_ref=?,updated_at=CURRENT_TIMESTAMP
            WHERE item_id=?
            """,
            (
                reason,
                reason,
                reason,
                artifact_ref,
                item_id,
            ),
        )
        conn.commit()


def _bridge_candidate(
    *,
    item: Dict[str, Any],
    envelope: Dict[str, Any],
    package: Dict[str, Any],
    runtime_draft: Dict[str, Any] | None,
    provider: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    refs = artifact_refs_from_row(item)
    canonical_input_ref = str(refs.get("agent2DraftInputRef") or "")
    runtime_input_ref = str(_dict(runtime_draft).get("inputArtifactRef") or "")
    input_ref = runtime_input_ref if runtime_input_ref.startswith("ART-") else canonical_input_ref
    resolved = bridge_agent2_hash_proof(
        input_ref=input_ref,
        package_id=str(package.get("packageId") or package.get("itemId") or ""),
        runtime_draft=runtime_draft,
    )
    draft = dict(_dict(resolved.get("draft")))
    proof = dict(_dict(resolved.get("proof")))
    draft.update(
        agent2DraftExecutionProof=proof,
        itemExecutionId=proof.get("itemExecutionId"),
        executionHash=proof.get("executionHash"),
        inputArtifactRef=proof.get("inputArtifactRef"),
        inputContentHash=proof.get("inputContentHash"),
        outputArtifactRef=proof.get("outputArtifactRef"),
        outputContentHash=proof.get("outputContentHash"),
        exactExecutionReplay=True,
        hashIdentityMatched=True,
        hashProofBridgeVersion=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        fallbackAllowed=False,
    )
    provider_summary = hash_proof_provider_summary(proof, base=provider)
    candidate = normalize_agent2_draft_completed_contract(
        package,
        draft,
        provider_summary,
    )
    candidate.update(
        agent2DraftHashExecutionProof=proof,
        agent2DraftExecutionProof=proof,
        agent2DraftProvider=provider_summary,
        hashProofBridgeVersion=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        acceptedOutputArtifactRef=resolved.get("outputArtifactRef"),
        acceptedOutputContentHash=resolved.get("outputContentHash"),
        sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
        inputProjectionAudit=envelope.get("projectionAudit"),
        runtimeSource="agent2DraftInputRef.v22514+acceptedHashOutput.v22515",
        outputContract="V22.5.15.agent2_draft_ready",
        taskAdmissionAllowed=False,
        fallbackAllowed=False,
    )
    return candidate, draft, provider_summary


def _finish_ready(
    worker: Any,
    item: Dict[str, Any],
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    package_id = str(
        candidate.get("packageId")
        or item.get("package_id")
        or item.get("item_id")
        or ""
    )
    result = worker._finish_item(
        item,
        stage="agent2_draft_ready",
        status="ready",
        output_ref=f"agent2_action_draft:{item.get('data_version') or 'latest'}:{package_id}",
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
    _clear_failure_state(str(item.get("item_id") or ""))
    return result


def _missing_execution_proof(candidate: Dict[str, Any]) -> List[str]:
    proof = _dict(candidate.get("agent2DraftExecutionProof"))
    if (
        proof.get("resultMatched") is True
        and (
            proof.get("providerCallExecuted") is True
            or proof.get("exactReplayValidated") is True
        )
        and proof.get("fallbackUsed") is not True
        and proof.get("semanticCallId")
    ):
        return []
    return ["agent2DraftExecutionProof"]


def _contract_failure_reason(missing: List[str]) -> str:
    values = [str(code) for code in missing]
    for value in values:
        if value.startswith("agent2_title_image_") or value.startswith("agent2_roas_"):
            return value
    for value in values:
        if value in {
            "agent2_contract_repair_no_output",
            "agent2_contract_repair_still_invalid",
            "agent2_outcome_channel_conflict",
            "agent2_output_channel_missing",
        }:
            return value
    for code in _PRECISE_CONTRACT_FAILURE_CODES:
        if code in values:
            return code
    return "agent2_draft_contract_invalid"


def _hold_stage(draft_status: str) -> str:
    return _NONREADY_STAGE_BY_STATUS.get(draft_status, "")


def _hold_detail(draft: Dict[str, Any]) -> Any:
    status = str(draft.get("draftStatus") or "")
    if status == DRAFT_MISSING_DATA:
        return draft.get("missingData") or []
    if status == DRAFT_CONFLICT:
        return draft.get("conflictReasons") or []
    if status == DRAFT_REJECTED:
        return draft.get("rejectedReason") or ""
    return None


def _finish_hold(
    worker: Any,
    item: Dict[str, Any],
    candidate: Dict[str, Any],
    draft: Dict[str, Any],
) -> Dict[str, Any]:
    draft_status = str(draft.get("draftStatus") or "")
    stage = _hold_stage(draft_status)
    if not stage:
        raise ValueError("agent2_nonready_status_not_routable")
    package_id = str(
        candidate.get("packageId")
        or item.get("package_id")
        or item.get("item_id")
        or ""
    )
    payload = {
        **candidate,
        "agent2DraftStatus": draft_status,
        "holdStatus": draft_status,
        "holdDetail": _hold_detail(draft),
        "frontendHoldLabel": _NONREADY_LABEL_BY_STATUS[draft_status],
        "nonReadyRoutingVersion": AGENT2_NONREADY_ROUTING_VERSION,
        "runtimeSolidHashVersion": AGENT2_RUNTIME_SOLID_HASH_VERSION,
        "outputContract": f"V22.5.15.{stage}",
        "lineage": {
            **_dict(candidate.get("lineage")),
            "currentStage": stage,
            "source": "pipeline_items_artifact_refs_only",
        },
        "taskAdmissionAllowed": False,
        "fallbackAllowed": False,
    }
    result = worker._finish_item(
        item,
        stage=stage,
        status="blocked",
        output_ref=f"{stage}:{item.get('data_version') or 'latest'}:{package_id}",
        payload=payload,
        station_id="agent2_action_draft_station",
    )
    artifact_ref = str(result.get("payloadArtifactRef") or "")
    if artifact_ref.startswith("ART-"):
        attach_pipeline_artifact_ref(
            str(item.get("item_id")),
            "agent2DraftRef",
            artifact_ref,
        )
    _clear_failure_state(str(item.get("item_id") or ""))
    return result


def _finish_invalid_output(
    worker: Any,
    item: Dict[str, Any],
    candidate: Dict[str, Any],
    *,
    data_version: str | None,
    package_id: str,
    missing: List[str],
) -> Dict[str, Any]:
    reason = _contract_failure_reason(missing)
    result = worker._finish_item(
        item,
        stage="agent2_draft_output_invalid",
        status="failed",
        output_ref=(
            f"agent2_draft_output_invalid:{data_version or 'latest'}:{package_id}"
        ),
        payload={
            **candidate,
            "reason": reason,
            "failureCode": reason,
            "missing": missing,
            "failureOwner": "agent2_action_draft_station",
            "frontendFailureLabel": "Agent2草案不完整",
            "nonReadyRoutingVersion": AGENT2_NONREADY_ROUTING_VERSION,
        "runtimeSolidHashVersion": AGENT2_RUNTIME_SOLID_HASH_VERSION,
            "outputContract": "V22.5.15.agent2_draft_output_invalid",
            "lineage": {
                **_dict(candidate.get("lineage")),
                "currentStage": "agent2_draft_output_invalid",
                "source": "pipeline_items_artifact_refs_only",
            },
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
    _persist_precise_failure_state(
        str(item.get("item_id") or ""),
        reason=reason,
        artifact_ref=artifact_ref if artifact_ref.startswith("ART-") else None,
    )
    return result


def reconcile_agent2_hash_proof_dead_letters_v22515(
    data_version: str | None = None,
    *,
    limit: int = 500,
) -> Dict[str, Any]:
    """Promote proof-misclassified dead letters from accepted output Artifacts.

    No Agent1, Action Pack, input projection or Provider call is rerun.
    """
    from src.services import pipeline_action_microbatch_v205_service as worker

    where = ["current_stage=?", "status='failed'"]
    params: List[Any] = [AGENT2_DEAD_LETTER_STAGE]
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
    for raw in rows:
        item = dict(raw)
        item_id = str(item.get("item_id") or "")
        try:
            failure_value = legacy_v22514.payload_from_row(item)
        except Exception as exc:
            rejected[item_id] = f"dead_letter_payload_unreadable:{_text(exc)}"
            continue
        if not _mentions_hash_bridge_candidate(failure_value):
            rejected[item_id] = "dead_letter_not_hash_proof_candidate"
            continue
        try:
            refs = artifact_refs_from_row(item)
            input_ref = str(refs.get("agent2DraftInputRef") or "")
            envelope = resolve_agent_input_ref(
                input_ref,
                expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
            )
            package = dict(_dict(envelope.get("payload")))
            candidate, _draft, _provider = _bridge_candidate(
                item=item,
                envelope=envelope,
                package=package,
                runtime_draft=None,
                provider={},
            )
            missing = missing_agent2_draft_completed_contract(candidate)
            if missing:
                rejected[item_id] = "accepted_hash_output_contract_invalid:" + ",".join(missing[:16])
                continue
            _finish_ready(worker, item, candidate)
            recovered.append(item_id)
        except Exception as exc:
            rejected[item_id] = f"hash_proof_reconcile_failed:{_text(exc)}"

    return {
        "version": AGENT2_RUNTIME_VERSION,
        "hashProofBridgeVersion": AGENT2_HASH_PROOF_BRIDGE_VERSION,
        "candidateCount": len(rows),
        "recoveredItemCount": len(recovered),
        "recoveredItemIds": recovered,
        "rejected": rejected,
        "agent1Rerun": False,
        "actionPackRerun": False,
        "agent2InputProjectionRerun": False,
        "providerCallsExecuted": 0,
        "recoveryAuthority": "artifact_execution_index_v2259+accepted_output_artifact",
        "blindDeadLetterRetryAllowed": False,
        "fallbackAllowed": False,
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
            "runtimeSource": "agent2DraftInputRef.v22514+hashProofBridge.v22515",
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
                missing=["agent2DraftInputRef", _text(exc, 180)],
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
                missing=["agent2DraftInputRef", _text(exc, 180)],
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
            "runtimeSource": "agent2DraftInputRef.v22514+hashProofBridge.v22515",
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
            "runtimeSource": "agent2DraftInputRef.v22514+hashProofBridge.v22515",
        }

    runtime_envelopes: List[Dict[str, Any]] = []
    preflight_regeneration_by_package: Dict[str, Dict[str, Any]] = {}
    generation_compilation_by_package: Dict[str, Dict[str, Any]] = {}
    for item, envelope, package in prepared.values():
        package_id = str(package.get("packageId") or item.get("package_id") or item.get("item_id") or "")
        canonical_input_ref = str(artifact_refs_from_row(item).get("agent2DraftInputRef") or "")
        revoked = revoked_agent2_execution_for_input(canonical_input_ref)
        if revoked:
            regeneration = build_agent2_regeneration_envelope(
                envelope,
                canonical_input_ref=canonical_input_ref,
                source_execution_hash=str(revoked.get("execution_hash") or ""),
            )
            runtime_envelopes.append(dict(regeneration["envelope"]))
            preflight_regeneration_by_package[package_id] = {
                **regeneration,
                "sourceExecutionHash": str(revoked.get("execution_hash") or ""),
            }
        else:
            compilation = build_agent2_generation_envelope(
                envelope,
                canonical_input_ref=canonical_input_ref,
            )
            runtime_envelopes.append(dict(compilation["envelope"]))
            generation_compilation_by_package[package_id] = compilation

    drafts, provider = run_agent2_draft_projected_inputs(
        runtime_envelopes,
        data_version=data_version,
        max_items_per_call=batch_size,
    )
    completed = held = invalid_output = retry_scheduled = dead_lettered = proof_failed = 0
    hash_proof_passed = legacy_proof_passed = 0
    contract_repair_count = contract_repair_success_count = 0
    provider_regeneration_count = len(preflight_regeneration_by_package)
    if provider_regeneration_count:
        provider["providerRegenerationCount"] = provider_regeneration_count
        provider["regenerationMode"] = "provider_regeneration_after_invalid_replay"
    by_status: Counter[str] = Counter()
    by_hold_stage: Counter[str] = Counter()
    by_failure: Counter[str] = Counter()

    for item, envelope, package in prepared.values():
        package_id = str(
            package.get("packageId")
            or item.get("package_id")
            or item.get("item_id")
            or ""
        )
        runtime_draft = drafts.get(package_id)
        preflight_regeneration = preflight_regeneration_by_package.get(package_id)
        generation_compilation = generation_compilation_by_package.get(package_id)
        if isinstance(runtime_draft, dict) and runtime_draft and preflight_regeneration:
            runtime_draft = {
                **runtime_draft,
                "agent2RuntimeExecutionMode": "provider_regeneration_after_invalid_replay",
                "sourceExecutionHash": preflight_regeneration["sourceExecutionHash"],
                "canonicalInputArtifactRef": preflight_regeneration["canonicalInputArtifactRef"],
                "runtimeInputArtifactRef": preflight_regeneration["runtimeInputArtifactRef"],
                "semanticInputHash": preflight_regeneration["semanticInputHash"],
            }
        elif isinstance(runtime_draft, dict) and runtime_draft and generation_compilation:
            runtime_draft = {
                **runtime_draft,
                "agent2RuntimeExecutionMode": "system_compiled_family_payload",
                "canonicalInputArtifactRef": generation_compilation["canonicalInputArtifactRef"],
                "compilerInputArtifactRef": generation_compilation["compilerInputArtifactRef"],
                "semanticInputHash": generation_compilation["semanticInputHash"],
                "generationCompilerVersion": generation_compilation["generationCompilerVersion"],
            }
        candidate: Dict[str, Any] | None = None
        draft: Dict[str, Any] | None = None
        provider_for_contract = dict(provider)
        bridge_error: str | None = None

        if isinstance(runtime_draft, dict) and runtime_draft:
            input_ref = str(artifact_refs_from_row(item).get("agent2DraftInputRef") or "")
            direct_missing = missing_agent2_draft_contract(runtime_draft)
            replay_rejected = bool(
                direct_missing and runtime_draft.get("exactExecutionReplay") is True
            )
            source_execution_hash = str(runtime_draft.get("executionHash") or "")
            if replay_rejected:
                record_agent2_runtime_outcome(
                    input_ref=input_ref,
                    draft=runtime_draft,
                    execution_mode="contract_revalidation",
                    status="replay_rejected",
                    contract_version=AGENT2_RUNTIME_SOLID_HASH_VERSION,
                    contract_missing=direct_missing,
                    source_execution_hash=source_execution_hash or None,
                )
                revoke_agent2_execution(source_execution_hash, direct_missing)
                regeneration = build_agent2_regeneration_envelope(
                    envelope,
                    canonical_input_ref=input_ref,
                    source_execution_hash=source_execution_hash,
                )
                retry_envelope = dict(regeneration["envelope"])
                retry_drafts, retry_provider = run_agent2_draft_projected_inputs(
                    [retry_envelope],
                    data_version=data_version,
                    max_items_per_call=1,
                )
                provider_regeneration_count += 1
                runtime_draft = retry_drafts.get(package_id)
                provider_for_contract = dict(retry_provider)
                provider["actualCalls"] = int(provider.get("actualCalls") or 0) + int(
                    retry_provider.get("actualCalls") or 0
                )
                provider["providerRegenerationCount"] = provider_regeneration_count
                provider["regenerationMode"] = "provider_regeneration_after_invalid_replay"
                if isinstance(runtime_draft, dict) and runtime_draft:
                    runtime_draft = {
                        **runtime_draft,
                        "agent2RuntimeExecutionMode": "provider_regeneration_after_invalid_replay",
                        "sourceExecutionHash": source_execution_hash,
                        "canonicalInputArtifactRef": input_ref,
                        "runtimeInputArtifactRef": regeneration["runtimeInputArtifactRef"],
                        "semanticInputHash": regeneration["semanticInputHash"],
                    }
                    direct_missing = missing_agent2_draft_contract(runtime_draft)
                else:
                    direct_missing = ["agent2_draft_returned_no_plan_after_invalid_replay"]

            repair_attempted = False
            repair_source_execution_hash = str(runtime_draft.get("executionHash") or source_execution_hash) if isinstance(runtime_draft, dict) else source_execution_hash
            if (
                direct_missing
                and isinstance(runtime_draft, dict)
                and runtime_draft
                and repairable_agent2_contract_missing(direct_missing)
                and not _dict(_dict(package.get("diagnosticExtensions")).get("agent2ContractRepair"))
            ):
                repair_attempted = True
                record_agent2_runtime_outcome(
                    input_ref=str(runtime_draft.get("inputArtifactRef") or input_ref),
                    draft=runtime_draft,
                    execution_mode="agent2_contract_revalidation",
                    status="repair_required",
                    contract_version=AGENT2_GENERATION_COMPILER_VERSION,
                    contract_missing=direct_missing,
                    source_execution_hash=repair_source_execution_hash or None,
                )
                revoke_agent2_execution(repair_source_execution_hash, direct_missing)
                repair = build_agent2_contract_repair_envelope(
                    envelope,
                    canonical_input_ref=input_ref,
                    source_execution_hash=repair_source_execution_hash,
                    previous_output=runtime_draft,
                    missing=direct_missing,
                )
                repair_drafts, repair_provider = run_agent2_draft_projected_inputs(
                    [dict(repair["envelope"])],
                    data_version=data_version,
                    max_items_per_call=1,
                )
                contract_repair_count += 1
                provider["actualCalls"] = int(provider.get("actualCalls") or 0) + int(
                    repair_provider.get("actualCalls") or 0
                )
                provider["contractRepairAttemptCount"] = contract_repair_count
                provider["contractRepairMode"] = "family_payload_only"
                provider_for_contract = dict(repair_provider)
                repaired_draft = repair_drafts.get(package_id)
                if isinstance(repaired_draft, dict) and repaired_draft:
                    runtime_draft = {
                        **repaired_draft,
                        "agent2RuntimeExecutionMode": "agent2_contract_repair",
                        "sourceExecutionHash": repair_source_execution_hash,
                        "canonicalInputArtifactRef": input_ref,
                        "runtimeInputArtifactRef": repair["runtimeInputArtifactRef"],
                        "semanticInputHash": repair["semanticInputHash"],
                        "contractRepairAttemptNo": 1,
                    }
                    direct_missing = missing_agent2_draft_contract(runtime_draft)
                    if not direct_missing:
                        contract_repair_success_count += 1
                else:
                    direct_missing = ["agent2_contract_repair_no_output"]

            should_finalize = (
                isinstance(runtime_draft, dict)
                and bool(runtime_draft)
                and not (
                    repair_attempted
                    and direct_missing == ["agent2_contract_repair_no_output"]
                )
            )
            if should_finalize:
                acceptance = finalize_agent2_execution_acceptance(
                    runtime_draft,
                    contract_version=AGENT2_GENERATION_COMPILER_VERSION,
                )
                direct_missing = list(acceptance.get("missing") or direct_missing)
                if repair_attempted and direct_missing:
                    direct_missing = list(dict.fromkeys([*direct_missing, "agent2_contract_repair_still_invalid"]))

            if direct_missing and isinstance(runtime_draft, dict) and runtime_draft:
                runtime_identity = record_agent2_runtime_outcome(
                    input_ref=str(runtime_draft.get("inputArtifactRef") or input_ref),
                    draft=runtime_draft,
                    execution_mode=(
                        "agent2_contract_repair"
                        if repair_attempted
                        else "provider_regeneration_after_invalid_replay"
                        if replay_rejected
                        else "provider_call"
                    ),
                    status="failed",
                    contract_version=AGENT2_RUNTIME_SOLID_HASH_VERSION,
                    contract_missing=direct_missing,
                    source_execution_hash=(
                        repair_source_execution_hash
                        if repair_attempted
                        else source_execution_hash
                        if replay_rejected
                        else None
                    ),
                )
                runtime_draft = {**runtime_draft, **runtime_identity}
                candidate = normalize_agent2_draft_completed_contract(
                    package,
                    runtime_draft,
                    provider_for_contract,
                )
                invalid_output += 1
                by_status[str(runtime_draft.get("draftStatus") or "missing")] += 1
                _finish_invalid_output(
                    worker,
                    item,
                    candidate,
                    data_version=data_version,
                    package_id=package_id,
                    missing=direct_missing,
                )
                continue

        try:
            candidate, draft, provider_for_contract = _bridge_candidate(
                item=item,
                envelope=envelope,
                package=package,
                runtime_draft=(runtime_draft if isinstance(runtime_draft, dict) else None),
                provider=provider_for_contract,
            )
            hash_proof_passed += 1
        except Agent2HashProofError as exc:
            bridge_error = exc.code
        except Exception as exc:
            bridge_error = f"agent2_hash_bridge_unexpected:{_text(exc, 300)}"

        if candidate is None and isinstance(runtime_draft, dict) and runtime_draft:
            legacy_proof = proof_for_package(provider_for_contract, package_id)
            if valid_agent2_execution_proof(legacy_proof):
                draft = dict(runtime_draft)
                draft["agent2DraftExecutionProof"] = legacy_proof
                candidate = normalize_agent2_draft_completed_contract(
                    package,
                    draft,
                    provider_for_contract,
                )
                legacy_proof_passed += 1

        if candidate is None or not isinstance(draft, dict) or not draft:
            proof_failed += 1
            reason = (
                f"agent2_hash_proof_bridge_missing:{bridge_error}"
                if bridge_error
                else "agent2_draft_returned_no_plan"
            )
            outcome = schedule_agent2_failure(
                item,
                package,
                provider_for_contract,
                reason,
            )
            by_failure[str(outcome.get("failureClass"))] += 1
            retry_scheduled += 1 if outcome.get("status") == "retry" else 0
            dead_lettered += 1 if outcome.get("terminal") else 0
            continue

        draft_status = str(draft.get("draftStatus") or "missing")
        by_status[draft_status] += 1
        semantic_missing = missing_agent2_draft_contract(draft)
        execution_missing = list(
            dict.fromkeys([*semantic_missing, *_missing_execution_proof(candidate)])
        )
        if execution_missing:
            invalid_output += 1
            _finish_invalid_output(
                worker,
                item,
                candidate,
                data_version=data_version,
                package_id=package_id,
                missing=execution_missing,
            )
            continue

        if draft_status == DRAFT_READY:
            ready_missing = missing_agent2_draft_completed_contract(candidate)
            if ready_missing:
                invalid_output += 1
                _finish_invalid_output(
                    worker,
                    item,
                    candidate,
                    data_version=data_version,
                    package_id=package_id,
                    missing=ready_missing,
                )
                continue
            _finish_ready(worker, item, candidate)
            completed += 1
            continue

        hold_stage = _hold_stage(draft_status)
        if hold_stage:
            _finish_hold(worker, item, candidate, draft)
            held += 1
            by_hold_stage[hold_stage] += 1
            continue

        invalid_output += 1
        _finish_invalid_output(
            worker,
            item,
            candidate,
            data_version=data_version,
            package_id=package_id,
            missing=["draftStatus"],
        )

    return {
        "version": AGENT2_RUNTIME_VERSION,
        "nonReadyRoutingVersion": AGENT2_NONREADY_ROUTING_VERSION,
        "runtimeSolidHashVersion": AGENT2_RUNTIME_SOLID_HASH_VERSION,
        "hashProofBridgeVersion": AGENT2_HASH_PROOF_BRIDGE_VERSION,
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
        "heldItemCount": held,
        "invalidOutputItemCount": invalid_output,
        "retryScheduledItemCount": retry_scheduled,
        "deadLetteredItemCount": dead_lettered,
        "proofFailedItemCount": proof_failed,
        "hashProofPassedItemCount": hash_proof_passed,
        "legacyProofPassedItemCount": legacy_proof_passed,
        "failedItemCount": (
            invalid_action_pack_count + invalid_input_count + invalid_output + dead_lettered
        ),
        "draftCount": len(drafts),
        "byDraftStatus": dict(by_status),
        "byHoldStage": dict(by_hold_stage),
        "pendingItemCount": worker.pending_agent2_item_count(data_version),
        "provider": provider,
        "runtimeSource": "agent2DraftInputRef.v22514+acceptedHashOutput.v22515",
        "executionMode": "action_evidence_slice_then_hash_accepted_output",
        "providerRegenerationCount": provider_regeneration_count,
        "compiledGenerationInputCount": len(generation_compilation_by_package),
        "contractRepairAttemptCount": contract_repair_count,
        "contractRepairSuccessCount": contract_repair_success_count,
        "generationCompilerVersion": AGENT2_GENERATION_COMPILER_VERSION,
        "runtimeSolidHashVersion": AGENT2_RUNTIME_SOLID_HASH_VERSION,
        "legacyItemProvenanceAuthority": False,
        "fallbackAllowed": False,
    }


__all__ = [
    "AGENT2_RUNTIME_VERSION",
    "AGENT2_NONREADY_ROUTING_VERSION",
    "AGENT2_RUNTIME_SOLID_HASH_VERSION",
    "migrate_agent2_projection_failures_v22514",
    "reconcile_agent2_hash_proof_dead_letters_v22515",
    "run_agent2_draft_microbatch_hard",
]
