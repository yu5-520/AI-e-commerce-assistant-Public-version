"""V22.5.21 Agent2 dead-letter classification and repair authority.

V22.5.20 correctly separated strict hash recovery from true-missing Agent2 reruns,
but classification still treated ``pipeline_items.payload`` as the only normal source
and read row-level failure columns only when payload decoding raised.  Historical rows
therefore contained real errors in ``error_reason`` while a readable stale payload hid
them from both repair branches.

This version always builds one immutable classification envelope from:

- the readable pipeline payload;
- row-level failure columns;
- the referenced last-error Artifact when available.

The merged envelope is used only for classification.  Hash acceptance remains owned by
the strict V22.5.20 bridge, and true-missing recovery still reruns Agent2 only.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services import agent2_runtime_v22515_service as legacy_runtime
from src.services.agent2_hash_proof_bridge_v22520_service import (
    AGENT2_HASH_PROOF_BRIDGE_VERSION,
    Agent2HashProofError,
    bridge_agent2_hash_proof,
    hash_proof_provider_summary,
)
from src.services.agent_input_contract_v225_service import AGENT2_DRAFT_INPUT_SCHEMA
from src.services.agent_input_transport_v22514_service import resolve_agent_input_ref
from src.services.agent_runtime_contract_v225_service import (
    missing_agent2_draft_completed_contract,
    normalize_agent2_draft_completed_contract,
)
from src.services.artifact_transport_service import resolve_artifact
from src.services.pipeline_artifact_contract_service import artifact_refs_from_row

AGENT2_RUNTIME_VERSION = "22.5.21"
AGENT2_DEAD_LETTER_STAGE = "agent2_dead_letter"
_NO_PLAN_MARKER = "agent2_draft_returned_no_plan"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 800) -> str:
    return " ".join(str(value or "").split())[:limit]


def _read_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    try:
        value = legacy_runtime.legacy_v22514.payload_from_row(item)
    except Exception as exc:
        return {
            "payloadReadError": _text(exc, 500),
        }
    return dict(value) if isinstance(value, dict) else {
        "payloadValue": value,
    }


def _read_last_error_artifact(item: Dict[str, Any]) -> Dict[str, Any]:
    artifact_ref = _text(item.get("last_error_artifact_ref"), 220)
    if not artifact_ref.startswith("ART-"):
        return {}
    try:
        value = resolve_artifact(artifact_ref)
    except Exception as exc:
        return {
            "artifactRef": artifact_ref,
            "artifactReadError": _text(exc, 500),
        }
    return {
        "artifactRef": artifact_ref,
        "value": value,
    }


def _row_failure_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "currentStage": item.get("current_stage"),
        "status": item.get("status"),
        "retryCount": item.get("retry_count"),
        "failureCode": item.get("failure_code"),
        "failureClass": item.get("failure_class"),
        "errorReason": item.get("error_reason"),
        "lastErrorCode": item.get("last_error_code"),
        "lastErrorArtifactRef": item.get("last_error_artifact_ref"),
    }


def dead_letter_classification_evidence_v22521(item: Dict[str, Any]) -> Dict[str, Any]:
    """Return the complete fail-closed evidence envelope used for classification."""
    payload = _read_payload(item)
    row_failure = _row_failure_fields(item)
    artifact = _read_last_error_artifact(item)
    sources: List[str] = ["pipeline_items.payload", "pipeline_items.failure_columns"]
    if artifact:
        sources.append("pipeline_items.last_error_artifact_ref")
    return {
        "version": AGENT2_RUNTIME_VERSION,
        "itemId": item.get("item_id"),
        "packageId": item.get("package_id"),
        "dataVersion": item.get("data_version"),
        "classificationSources": sources,
        "payload": payload,
        "rowFailure": row_failure,
        "lastErrorArtifact": artifact,
        "rowFailureMergedUnconditionally": True,
        "payloadReadabilityDoesNotSuppressRowFailure": True,
    }


def _contains(value: Any, marker: str) -> bool:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return marker in text


def classify_agent2_dead_letter_v22521(item: Dict[str, Any]) -> Dict[str, Any]:
    evidence = dead_letter_classification_evidence_v22521(item)
    hash_candidate = legacy_runtime._mentions_hash_bridge_candidate(evidence)
    true_missing_candidate = _contains(evidence, _NO_PLAN_MARKER)
    if hash_candidate and true_missing_candidate:
        classification = "ambiguous_hash_and_true_missing"
    elif hash_candidate:
        classification = "hash_proof_candidate"
    elif true_missing_candidate:
        classification = "true_missing_candidate"
    else:
        classification = "unclassified"
    return {
        "version": AGENT2_RUNTIME_VERSION,
        "classification": classification,
        "hashProofCandidate": bool(hash_candidate),
        "trueMissingCandidate": bool(true_missing_candidate),
        "evidence": evidence,
        "fallbackAllowed": False,
    }


def _candidate_from_resolved(
    *,
    item: Dict[str, Any],
    envelope: Dict[str, Any],
    package: Dict[str, Any],
    resolved: Dict[str, Any],
) -> Dict[str, Any]:
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
    provider = hash_proof_provider_summary(proof, base={})
    candidate = normalize_agent2_draft_completed_contract(package, draft, provider)
    candidate.update(
        agent2DraftHashExecutionProof=proof,
        agent2DraftExecutionProof=proof,
        agent2DraftProvider=provider,
        hashProofBridgeVersion=AGENT2_HASH_PROOF_BRIDGE_VERSION,
        acceptedOutputArtifactRef=resolved.get("outputArtifactRef"),
        acceptedOutputContentHash=resolved.get("outputContentHash"),
        sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
        inputProjectionAudit=envelope.get("projectionAudit"),
        runtimeSource="agent2DraftInputHistory+acceptedHashOutput.v22521",
        outputContract="V22.5.21.agent2_draft_ready",
        taskAdmissionAllowed=False,
        historicalInputRecovery=(resolved.get("recoveryMode") == "historical_exact_input"),
        currentAgent2DraftInputRef=artifact_refs_from_row(item).get("agent2DraftInputRef"),
        recoveredAgent2DraftInputRef=(
            resolved.get("historicalInputArtifactRef")
            or resolved.get("currentInputArtifactRef")
        ),
        fallbackAllowed=False,
    )
    return candidate


def reconcile_agent2_hash_proof_dead_letters_v22521(
    data_version: str | None = None,
    *,
    limit: int = 500,
) -> Dict[str, Any]:
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
    recovered_current: List[str] = []
    recovered_historical: List[str] = []
    rejected: Dict[str, str] = {}
    classification_audit: Dict[str, Dict[str, Any]] = {}
    by_classification: Counter[str] = Counter()
    for raw in rows:
        item = dict(raw)
        item_id = str(item.get("item_id") or "")
        classified = classify_agent2_dead_letter_v22521(item)
        classification = str(classified.get("classification") or "unclassified")
        by_classification[classification] += 1
        classification_audit[item_id] = classified
        if classification == "ambiguous_hash_and_true_missing":
            rejected[item_id] = "dead_letter_classification_ambiguous"
            continue
        if classification != "hash_proof_candidate":
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
            resolved = bridge_agent2_hash_proof(
                input_ref=input_ref,
                package_id=str(package.get("packageId") or item.get("package_id") or item_id),
                runtime_draft=None,
            )
            candidate = _candidate_from_resolved(
                item=item,
                envelope=envelope,
                package=package,
                resolved=resolved,
            )
            missing = missing_agent2_draft_completed_contract(candidate)
            if missing:
                rejected[item_id] = (
                    "accepted_hash_output_contract_invalid:" + ",".join(missing[:16])
                )
                continue
            legacy_runtime._finish_ready(
                __import__(
                    "src.services.pipeline_action_microbatch_v205_service",
                    fromlist=["_finish_item"],
                ),
                item,
                candidate,
            )
            recovered.append(item_id)
            if resolved.get("recoveryMode") == "historical_exact_input":
                recovered_historical.append(item_id)
            else:
                recovered_current.append(item_id)
        except Agent2HashProofError as exc:
            rejected[item_id] = f"hash_proof_reconcile_failed:{exc.code}:{_text(exc.detail)}"
        except Exception as exc:
            rejected[item_id] = f"hash_proof_reconcile_failed:{_text(exc)}"

    return {
        "version": AGENT2_RUNTIME_VERSION,
        "hashProofBridgeVersion": AGENT2_HASH_PROOF_BRIDGE_VERSION,
        "candidateCount": len(rows),
        "candidateByClassification": dict(by_classification),
        "classificationAudit": classification_audit,
        "recoveredItemCount": len(recovered),
        "recoveredItemIds": recovered,
        "recoveredCurrentInputCount": len(recovered_current),
        "recoveredCurrentInputIds": recovered_current,
        "recoveredHistoricalInputCount": len(recovered_historical),
        "recoveredHistoricalInputIds": recovered_historical,
        "rejected": rejected,
        "agent1Rerun": False,
        "actionPackRerun": False,
        "agent2InputProjectionRerun": False,
        "providerCallsExecuted": 0,
        "recoveryAuthority": (
            "merged failure evidence classification + unique historical/current input "
            "Artifact + artifact_execution_index_v2259 + accepted output Artifact"
        ),
        "hashValidationRelaxed": False,
        "fallbackAllowed": False,
    }


def requeue_agent2_true_missing_dead_letters_v22521(
    data_version: str | None = None,
    *,
    limit: int = 500,
) -> Dict[str, Any]:
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

    requeued: List[str] = []
    rejected: Dict[str, str] = {}
    classification_audit: Dict[str, Dict[str, Any]] = {}
    by_classification: Counter[str] = Counter()
    for raw in rows:
        item = dict(raw)
        item_id = str(item.get("item_id") or "")
        classified = classify_agent2_dead_letter_v22521(item)
        classification = str(classified.get("classification") or "unclassified")
        by_classification[classification] += 1
        classification_audit[item_id] = classified
        if classification == "ambiguous_hash_and_true_missing":
            rejected[item_id] = "dead_letter_classification_ambiguous"
            continue
        if classification != "true_missing_candidate":
            rejected[item_id] = "dead_letter_not_true_missing_candidate"
            continue
        refs = artifact_refs_from_row(item)
        input_ref = str(refs.get("agent2DraftInputRef") or "")
        try:
            resolve_agent_input_ref(
                input_ref,
                expected_schema=AGENT2_DRAFT_INPUT_SCHEMA,
            )
        except Exception as exc:
            rejected[item_id] = f"current_agent2_input_invalid:{_text(exc)}"
            continue
        with connect() as conn:
            cursor = conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage='action_pack_ready',status='retry',retry_count=0,
                    retry_after=NULL,claim_id=NULL,lease_expires_at=NULL,
                    failure_code=NULL,failure_class=NULL,error_reason=NULL,
                    last_error_code=NULL,last_error_artifact_ref=NULL,
                    updated_at=CURRENT_TIMESTAMP
                WHERE item_id=? AND current_stage='agent2_dead_letter' AND status='failed'
                """,
                (item_id,),
            )
            conn.commit()
        if cursor.rowcount == 1:
            requeued.append(item_id)
        else:
            rejected[item_id] = "state_changed_before_true_missing_requeue"

    return {
        "version": AGENT2_RUNTIME_VERSION,
        "candidateCount": len(rows),
        "candidateByClassification": dict(by_classification),
        "classificationAudit": classification_audit,
        "requeuedItemCount": len(requeued),
        "requeuedItemIds": requeued,
        "rejected": rejected,
        "agent1Rerun": False,
        "actionPackRerun": False,
        "agent2InputProjectionRerun": False,
        "providerCallsExecuted": 0,
        "nextRuntime": "Agent2 V22.5.20 exact itemExecutionId+inputContentHash",
        "fallbackAllowed": False,
    }


def repair_agent2_dead_letters_v22521(
    data_version: str,
    *,
    execute_true_missing: bool = True,
    batch_size: int = 5,
    max_agent2_passes: int = 12,
) -> Dict[str, Any]:
    proof_recovery = reconcile_agent2_hash_proof_dead_letters_v22521(data_version)
    true_missing_requeue = requeue_agent2_true_missing_dead_letters_v22521(data_version)
    runs: List[Dict[str, Any]] = []
    if execute_true_missing and int(true_missing_requeue.get("requeuedItemCount") or 0):
        for _ in range(max(1, min(50, int(max_agent2_passes)))):
            result = legacy_runtime.run_agent2_draft_microbatch_hard(
                data_version,
                batch_size=max(1, min(12, int(batch_size or 5))),
            )
            runs.append(result)
            if not result.get("ran") or int(result.get("pendingItemCount") or 0) <= 0:
                break

    with connect() as conn:
        states = [
            dict(row)
            for row in conn.execute(
                """
                SELECT current_stage,status,COUNT(*) AS count
                FROM pipeline_items
                WHERE data_version=? AND product_id IS NOT NULL
                GROUP BY current_stage,status
                ORDER BY current_stage,status
                """,
                (data_version,),
            ).fetchall()
        ]
        dead_letters = int(
            conn.execute(
                """
                SELECT COUNT(*) AS n FROM pipeline_items
                WHERE data_version=? AND current_stage='agent2_dead_letter' AND status='failed'
                """,
                (data_version,),
            ).fetchone()["n"]
            or 0
        )

    provider_calls = sum(
        int(_dict(run.get("provider")).get("actualCalls") or 0)
        for run in runs
    )
    combined_classification: Counter[str] = Counter()
    combined_classification.update(proof_recovery.get("candidateByClassification") or {})
    combined_classification.update(true_missing_requeue.get("candidateByClassification") or {})
    return {
        "version": AGENT2_RUNTIME_VERSION,
        "dataVersion": data_version,
        "proofRecovery": proof_recovery,
        "trueMissingRequeue": true_missing_requeue,
        "agent2ExactRuns": runs,
        "providerCallsExecuted": provider_calls,
        "agent1Rerun": False,
        "actionPackRerun": False,
        "agent2InputProjectionRerun": False,
        "finalDeadLetterCount": dead_letters,
        "finalStates": states,
        "classificationCountsAcrossPasses": dict(combined_classification),
        "completed": dead_letters == 0,
        "fallbackAllowed": False,
    }


# Compatibility aliases keep existing operational scripts on the corrected authority.
reconcile_agent2_hash_proof_dead_letters_v22520 = (
    reconcile_agent2_hash_proof_dead_letters_v22521
)
requeue_agent2_true_missing_dead_letters_v22520 = (
    requeue_agent2_true_missing_dead_letters_v22521
)
repair_agent2_dead_letters_v22520 = repair_agent2_dead_letters_v22521


__all__ = [
    "AGENT2_RUNTIME_VERSION",
    "dead_letter_classification_evidence_v22521",
    "classify_agent2_dead_letter_v22521",
    "reconcile_agent2_hash_proof_dead_letters_v22521",
    "requeue_agent2_true_missing_dead_letters_v22521",
    "repair_agent2_dead_letters_v22521",
    "reconcile_agent2_hash_proof_dead_letters_v22520",
    "requeue_agent2_true_missing_dead_letters_v22520",
    "repair_agent2_dead_letters_v22520",
]
