"""V22.5.5 recovery for historical Agent2 items created without an execution lock.

Only Agent2 draft failures that are both business-missing-data and lack a complete
Agent1 execution lock are requeued. Legal observations and unrelated failures are
never touched. The original signal/Agent1 input refs are preserved for audit-safe
rejudgment; all downstream draft/SOP/task refs are removed.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.agent_execution_lock_v2255_service import (
    execution_lock_from,
    missing_execution_lock,
)
from src.services.artifact_transport_service import resolve_artifact
from src.services.pipeline_artifact_contract_service import artifact_refs_from_row
from src.services.pipeline_item_service import now_iso, record_pipeline_item_event

RECOVERY_VERSION = "22.5.5"
_REQUEUE_STAGES = {"agent2_draft_output_invalid", "agent2_output_invalid"}
_DOWNSTREAM_REF_KEYS = {
    "agent1Ref",
    "agent1InvalidRef",
    "agent1FailureRef",
    "capabilityRef",
    "capabilityFailureRef",
    "agent2InputRef",
    "agent2DraftInputRef",
    "agent2Ref",
    "agent2DraftRef",
    "agent2FailureRef",
    "agent2DraftFailureRef",
    "agent3SopInputRef",
    "agent3SopRef",
    "agent3SopFailureRef",
    "sopRef",
    "taskMappingRef",
    "taskRef",
    "readModelRef",
    "acceptanceRef",
    "currentStageRef",
}


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value in (None, ""):
        return {}
    try:
        parsed = loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _walk(value: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _failure_payload(row: Dict[str, Any], refs: Dict[str, Any]) -> Dict[str, Any]:
    candidates = [_load(row.get("payload"))]
    for key in ("agent2DraftFailureRef", "agent2FailureRef", "currentStageRef"):
        ref = str(refs.get(key) or "")
        if not ref.startswith("ART-"):
            continue
        try:
            value = resolve_artifact(ref)
        except Exception:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    for candidate in candidates:
        for obj in _walk(candidate):
            draft = _dict(obj.get("agent2ActionDraft"))
            reason = str(obj.get("reason") or row.get("last_error_code") or "")
            if draft or reason in {"agent2_draft_contract_invalid", "agent2_contract_invalid"}:
                return obj
    return candidates[0] if candidates else {}


def _eligible(row: Dict[str, Any], refs: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    if str(row.get("current_stage") or "") not in _REQUEUE_STAGES:
        return False, {"reason": "stage_not_eligible"}
    if not str(refs.get("agent1InputRef") or "").startswith("ART-"):
        return False, {"reason": "agent1_input_ref_missing"}
    failure = _failure_payload(row, refs)
    draft = _dict(failure.get("agent2ActionDraft"))
    status = str(draft.get("draftStatus") or failure.get("draftStatus") or "")
    if status != "draft_missing_data":
        return False, {"reason": "not_business_missing_data", "draftStatus": status}
    lock_missing = missing_execution_lock(execution_lock_from(failure or row))
    if not lock_missing:
        return False, {"reason": "execution_lock_already_complete"}
    return True, {
        "reason": "historical_agent2_without_execution_lock",
        "draftStatus": status,
        "missingExecutionLock": lock_missing,
    }


def requeue_unresolved_agent2_items_v2255(
    *,
    data_version: str | None = None,
    item_id: str | None = None,
    limit: int = 200,
    apply: bool = True,
) -> Dict[str, Any]:
    clauses = ["current_stage IN ('agent2_draft_output_invalid','agent2_output_invalid')"]
    params: list[Any] = []
    if data_version:
        clauses.append("data_version=?")
        params.append(data_version)
    if item_id:
        clauses.append("item_id=?")
        params.append(item_id)
    params.append(max(1, min(int(limit or 200), 5000)))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()

    eligible: list[tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    skipped = []
    for raw in rows:
        row = dict(raw)
        refs = artifact_refs_from_row(row)
        ok, diagnosis = _eligible(row, refs)
        if ok:
            eligible.append((row, refs, diagnosis))
        else:
            skipped.append({"itemId": row.get("item_id"), **diagnosis})

    updated = []
    if apply:
        for row, refs, diagnosis in eligible:
            agent1_input_ref = str(refs.get("agent1InputRef") or "")
            clean_refs = {key: value for key, value in refs.items() if key not in _DOWNSTREAM_REF_KEYS}
            clean_refs["agent1InputRef"] = agent1_input_ref
            clean_refs["currentStageRef"] = agent1_input_ref
            now = now_iso()
            recovery_payload = {
                "version": RECOVERY_VERSION,
                "reason": "execution_lock_rejudgment_required",
                "sourceStage": row.get("current_stage"),
                "sourceErrorCode": row.get("last_error_code"),
                "missingExecutionLock": diagnosis.get("missingExecutionLock") or [],
                "agent1InputRef": agent1_input_ref,
                "fallbackAllowed": False,
            }
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE pipeline_items
                    SET package_id=NULL,
                        decision_id=NULL,
                        task_id=NULL,
                        current_stage='agent1_pending',
                        status='ready',
                        route=NULL,
                        action_family=NULL,
                        output_ref=?,
                        retry_count=0,
                        error_reason=NULL,
                        payload=?,
                        artifact_refs_json=?,
                        payload_artifact_ref=?,
                        last_error_code=NULL,
                        last_error_artifact_ref=NULL,
                        updated_at=?
                    WHERE item_id=?
                      AND current_stage IN ('agent2_draft_output_invalid','agent2_output_invalid')
                    """,
                    (
                        f"execution_lock_rejudgment:{row.get('data_version')}:{row.get('item_id')}",
                        dumps({"version": RECOVERY_VERSION, "payload": recovery_payload, "artifactRefs": clean_refs}),
                        dumps(clean_refs),
                        agent1_input_ref,
                        now,
                        row.get("item_id"),
                    ),
                )
                changed = int(conn.execute("SELECT changes() AS n").fetchone()["n"] or 0)
                conn.commit()
            if not changed:
                continue
            envelope = {
                "itemId": row.get("item_id"),
                "dataVersion": row.get("data_version"),
                "productId": row.get("product_id"),
                "storeId": row.get("store_id"),
                "signalId": row.get("signal_id"),
                "stage": "agent1_pending",
                "inputRef": agent1_input_ref,
                "outputRef": f"execution_lock_rejudgment:{row.get('data_version')}:{row.get('item_id')}",
                "artifactRefs": clean_refs,
            }
            record_pipeline_item_event(
                envelope,
                station_id="agent_execution_lock_recovery_v2255",
                stage="agent1_pending",
                status="ready",
                input_ref=agent1_input_ref,
                output_ref=envelope["outputRef"],
                payload=recovery_payload,
            )
            updated.append(
                {
                    "itemId": row.get("item_id"),
                    "dataVersion": row.get("data_version"),
                    "productId": row.get("product_id"),
                    "storeId": row.get("store_id"),
                    "fromStage": row.get("current_stage"),
                    "toStage": "agent1_pending",
                    "preservedAgent1InputRef": agent1_input_ref,
                    "missingExecutionLock": diagnosis.get("missingExecutionLock") or [],
                }
            )

    return {
        "version": RECOVERY_VERSION,
        "apply": bool(apply),
        "matchedFailureCount": len(rows),
        "eligibleCount": len(eligible),
        "updatedCount": len(updated),
        "updated": updated,
        "skipped": skipped[:100],
        "observedItemsTouched": 0,
        "rule": "Only draft_missing_data Agent2 failures without a complete execution lock return to Agent1.",
    }


__all__ = ["RECOVERY_VERSION", "requeue_unresolved_agent2_items_v2255"]
