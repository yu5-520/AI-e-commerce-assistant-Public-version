"""Repair V22.2.5 observations that were blocked only by the legacy score gate."""
from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.artifact_transport_service import resolve_artifact, validate_artifact
from src.services.end_to_end_agent_flow_v226_service import _agent1_eligibility
from src.services.pipeline_artifact_contract_service import artifact_refs_from_row
from src.services.pipeline_item_service import (
    build_item_envelope,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.product_signal_admission_v197_service import score_signal

OBSERVED_SIGNAL_REPAIR_VERSION = "22.2.6"
REPAIR_MARKER = "v22_2_6_legacy_score_gate_reclassified"


def _rows(data_version: str | None, limit: int) -> List[Any]:
    where = ["current_stage='observed_soft_gate'", "status='observed'"]
    params: List[Any] = []
    if data_version:
        where.append("data_version=?")
        params.append(data_version)
    params.append(max(1, min(10000, int(limit or 5000))))
    with connect() as conn:
        return list(
            conn.execute(
                f"""
                SELECT * FROM pipeline_items
                WHERE {' AND '.join(where)}
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        )


def _restore_signal_admission_boundary(item_id: str) -> None:
    """Move a proven false observation to the last valid deterministic stage.

    V22.2.6 intentionally orders ``observed_soft_gate`` after Agent1 running so a
    native Agent1 observe result is terminal. Historical V22.2.5 false observations
    must therefore replay through ``signal_admitted`` before entering
    ``agent1_pending``. This update changes only stage/status; immutable Artifact
    references remain untouched.
    """
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_items
            SET current_stage='signal_admitted', status='completed',
                error_reason=NULL, updated_at=CURRENT_TIMESTAMP
            WHERE item_id=? AND current_stage='observed_soft_gate' AND status='observed'
            """,
            (item_id,),
        )
        conn.commit()


def repair_misclassified_observations_v226(
    data_version: str | None = None,
    *,
    limit: int = 5000,
) -> Dict[str, Any]:
    inspected = requeued = preserved = missing_ref = invalid_ref = 0
    errors: List[Dict[str, Any]] = []
    for row in _rows(data_version, limit):
        inspected += 1
        refs = artifact_refs_from_row(row)
        signal_ref = str(refs.get("signalRef") or "").strip()
        if not signal_ref:
            missing_ref += 1
            continue
        validation = validate_artifact(signal_ref)
        if validation.get("ok") is not True:
            invalid_ref += 1
            continue
        try:
            signal = resolve_artifact(signal_ref)
        except Exception as exc:
            invalid_ref += 1
            errors.append({"itemId": row["item_id"], "error": str(exc)[:240]})
            continue
        if not isinstance(signal, dict) or not signal:
            invalid_ref += 1
            continue
        eligibility = _agent1_eligibility(signal, baseline_only=False)
        if eligibility.get("eligible") is not True:
            preserved += 1
            continue
        score = score_signal(signal)
        _restore_signal_admission_boundary(str(row["item_id"]))
        envelope = build_item_envelope(
            data_version=row["data_version"],
            item_id=row["item_id"],
            product_id=row["product_id"],
            store_id=row["store_id"],
            signal_id=row["signal_id"],
            package_id=row["package_id"],
            decision_id=row["decision_id"],
            task_id=row["task_id"],
            action_family=row["action_family"],
            route=row["route"],
            input_ref=signal_ref,
            output_ref=f"agent1_pending:{row['signal_id'] or row['item_id']}",
            stage="agent1_pending",
            artifact_refs=refs,
        )
        handle = {
            "version": OBSERVED_SIGNAL_REPAIR_VERSION,
            "source": "observed_signal_repair_v226",
            "repairMarker": REPAIR_MARKER,
            "signalRef": signal_ref,
            "productId": row["product_id"],
            "storeId": row["store_id"],
            "signalId": row["signal_id"],
            "admissionScore": {
                **score,
                "scoreRole": "priority_only",
                "agent1Eligible": True,
                "eligibilityReason": eligibility.get("reason"),
            },
            "admissionDecision": "admitted_after_score_gate_repair",
            "legacyScoreGateRemoved": True,
            "legacySignalPoolRead": False,
            "replayFromStage": "signal_admitted",
        }
        stored = upsert_pipeline_item(
            envelope,
            stage="agent1_pending",
            status="queued",
            priority=max(1, min(100, 100 - int(score.get("score") or 0))),
            output_ref=envelope.get("outputRef"),
            payload=handle,
        )
        record_pipeline_item_event(
            stored,
            station_id="v22_2_6_observed_signal_repair",
            stage="agent1_pending",
            status="queued",
            input_ref=signal_ref,
            output_ref=envelope.get("outputRef"),
            payload=handle,
        )
        requeued += 1
    return {
        "version": OBSERVED_SIGNAL_REPAIR_VERSION,
        "dataVersion": data_version,
        "inspectedObservedItemCount": inspected,
        "requeuedAgent1PendingCount": requeued,
        "preservedTrueObservationCount": preserved,
        "missingSignalRefCount": missing_ref,
        "invalidSignalRefCount": invalid_ref,
        "errors": errors[:20],
        "idempotent": True,
        "scoreCanBlockAgent1": False,
        "legacySignalPoolRead": False,
        "replayBoundary": "signal_admitted",
        "rule": "Only observations proven meaningful by their immutable signalRef are replayed through signal_admitted; baseline and zero-change observations stay terminal.",
    }


__all__ = [
    "OBSERVED_SIGNAL_REPAIR_VERSION",
    "REPAIR_MARKER",
    "repair_misclassified_observations_v226",
]
