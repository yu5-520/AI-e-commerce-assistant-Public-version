"""V22.5.20 Agent2 dead-letter repair authority.

Proof-misclassified rows are promoted only from a unique strictly validated accepted
Artifact.  ``agent2_draft_returned_no_plan`` rows are requeued for the new exact Agent2
runtime; Agent1, Action Pack and input projection are never rerun.
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
from src.services.pipeline_artifact_contract_service import artifact_refs_from_row

AGENT2_RUNTIME_VERSION = "22.5.20"
AGENT2_DEAD_LETTER_STAGE = "agent2_dead_letter"
_NO_PLAN_MARKER = "agent2_draft_returned_no_plan"


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any, limit: int = 800) -> str:
    return " ".join(str(value or "").split())[:limit]


def _dead_letter_payload(item: Dict[str, Any]) -> Any:
    try:
        return legacy_runtime.legacy_v22514.payload_from_row(item)
    except Exception:
        return {
            "errorReason": item.get("error_reason"),
            "lastErrorCode": item.get("last_error_code"),
            "failureCode": item.get("failure_code"),
        }


def _contains(value: Any, marker: str) -> bool:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return marker in text


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
        runtimeSource="agent2DraftInputHistory+acceptedHashOutput.v22520",
        outputContract="V22.5.20.agent2_draft_ready",
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


def reconcile_agent2_hash_proof_dead_letters_v22520(
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
    for raw in rows:
        item = dict(raw)
        item_id = str(item.get("item_id") or "")
        failure_value = _dead_letter_payload(item)
        if not legacy_runtime._mentions_hash_bridge_candidate(failure_value):
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
            "unique historical/current input Artifact + "
            "artifact_execution_index_v2259 + accepted output Artifact"
        ),
        "hashValidationRelaxed": False,
        "fallbackAllowed": False,
    }


def requeue_agent2_true_missing_dead_letters_v22520(
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
    for raw in rows:
        item = dict(raw)
        item_id = str(item.get("item_id") or "")
        failure_value = _dead_letter_payload(item)
        if not _contains(failure_value, _NO_PLAN_MARKER):
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


def repair_agent2_dead_letters_v22520(
    data_version: str,
    *,
    execute_true_missing: bool = True,
    batch_size: int = 5,
    max_agent2_passes: int = 12,
) -> Dict[str, Any]:
    proof_recovery = reconcile_agent2_hash_proof_dead_letters_v22520(data_version)
    true_missing_requeue = requeue_agent2_true_missing_dead_letters_v22520(data_version)
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
        "completed": dead_letters == 0,
        "fallbackAllowed": False,
    }


__all__ = [
    "AGENT2_RUNTIME_VERSION",
    "reconcile_agent2_hash_proof_dead_letters_v22520",
    "requeue_agent2_true_missing_dead_letters_v22520",
    "repair_agent2_dead_letters_v22520",
]
