"""V22.5.8 targeted recovery for Agent1 rows affected by v2 evidence/output contracts."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.agent_input_contract_v2258_service import AGENT1_INPUT_SCHEMA
from src.services.artifact_transport_service import resolve_artifact
from src.services.pipeline_artifact_contract_service import artifact_refs_from_row
from src.services.pipeline_item_service import now_iso, record_pipeline_item_event

RECOVERY_VERSION = "22.5.8"
_ELIGIBLE_STAGES = {"observed_soft_gate", "agent1_failed", "agent1_output_invalid"}
_ELIGIBLE_STATUSES = {"observed", "failed"}
_AGENT1_AND_DOWNSTREAM_REF_KEYS = {
    "agent1InputRef",
    "agent1Ref",
    "agent1InvalidRef",
    "agent1FailureRef",
    "observationRef",
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


def _input_schema(refs: Dict[str, Any]) -> str:
    artifact_id = str(refs.get("agent1InputRef") or "")
    if not artifact_id.startswith("ART-"):
        return "missing"
    try:
        value = resolve_artifact(artifact_id)
    except Exception:
        return "unresolved"
    return str(value.get("schema") or "unknown") if isinstance(value, dict) else "unknown"


def _eligible(row: Dict[str, Any], refs: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    stage = str(row.get("current_stage") or "")
    status = str(row.get("status") or "")
    if stage not in _ELIGIBLE_STAGES:
        return False, {"reason": "stage_not_eligible", "stage": stage}
    if status not in _ELIGIBLE_STATUSES:
        return False, {"reason": "status_not_eligible", "status": status}
    signal_ref = str(refs.get("signalRef") or "")
    if not signal_ref.startswith("ART-"):
        return False, {"reason": "signal_ref_missing"}
    schema = _input_schema(refs)
    if schema == AGENT1_INPUT_SCHEMA:
        return False, {"reason": "already_agent1_v3", "inputSchema": schema}
    payload = _load(row.get("payload"))
    reasons = {
        str(obj.get("reason") or obj.get("diagnosticHoldReason") or "")
        for obj in _walk(payload)
    }
    return True, {
        "reason": "agent1_v2_evidence_or_output_contract_requires_rejudgment",
        "sourceStage": stage,
        "inputSchema": schema,
        "signalRef": signal_ref,
        "priorReasons": sorted(value for value in reasons if value)[:12],
    }


def latest_product_data_version() -> str | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT data_version, MAX(updated_at) AS latest_at
            FROM pipeline_items
            WHERE product_id IS NOT NULL
              AND TRIM(product_id) != ''
              AND data_version IS NOT NULL
              AND TRIM(data_version) != ''
            GROUP BY data_version
            ORDER BY latest_at DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row["data_version"]) if row else None


def requeue_agent1_v2258(
    *,
    data_version: str | None = None,
    item_id: str | None = None,
    limit: int = 500,
    apply: bool = False,
) -> Dict[str, Any]:
    resolved_version = data_version or latest_product_data_version()
    stages = sorted(_ELIGIBLE_STAGES)
    placeholders = ",".join("?" for _ in stages)
    clauses = [
        f"current_stage IN ({placeholders})",
        "status IN ('observed','failed')",
    ]
    params: list[Any] = list(stages)
    if resolved_version:
        clauses.append("data_version=?")
        params.append(resolved_version)
    if item_id:
        clauses.append("item_id=?")
        params.append(item_id)
    params.append(max(1, min(int(limit or 500), 5000)))
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(clauses)} "
            "ORDER BY updated_at DESC LIMIT ?",
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
            signal_ref = str(refs.get("signalRef") or "")
            clean_refs = {
                key: value
                for key, value in refs.items()
                if key not in _AGENT1_AND_DOWNSTREAM_REF_KEYS
            }
            clean_refs["signalRef"] = signal_ref
            clean_refs["currentStageRef"] = signal_ref
            now = now_iso()
            output_ref = f"agent1_v2258_rejudgment:{row.get('data_version')}:{row.get('item_id')}"
            recovery_payload = {
                "version": RECOVERY_VERSION,
                "reason": "agent1_v2258_evidence_output_contract_rejudgment",
                "sourceStage": row.get("current_stage"),
                "sourceStatus": row.get("status"),
                "legacyAgent1InputSchema": diagnosis.get("inputSchema"),
                "targetAgent1InputSchema": AGENT1_INPUT_SCHEMA,
                "signalRef": signal_ref,
                "fallbackAllowed": False,
            }
            with connect() as conn:
                conn.execute(
                    f"""
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
                      AND current_stage IN ({placeholders})
                      AND status IN ('observed','failed')
                    """,
                    (
                        output_ref,
                        dumps({"version": RECOVERY_VERSION, "payload": recovery_payload, "artifactRefs": clean_refs}),
                        dumps(clean_refs),
                        signal_ref,
                        now,
                        row.get("item_id"),
                        *stages,
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
                "inputRef": signal_ref,
                "outputRef": output_ref,
                "artifactRefs": clean_refs,
            }
            record_pipeline_item_event(
                envelope,
                station_id="agent1_input_recovery_v2258",
                stage="agent1_pending",
                status="ready",
                input_ref=signal_ref,
                output_ref=output_ref,
                payload=recovery_payload,
            )
            updated.append(
                {
                    "itemId": row.get("item_id"),
                    "dataVersion": row.get("data_version"),
                    "productId": row.get("product_id"),
                    "storeId": row.get("store_id"),
                    "fromStage": diagnosis.get("sourceStage"),
                    "toStage": "agent1_pending",
                    "removedAgent1AndDownstreamRefs": True,
                    "preservedSignalRef": signal_ref,
                }
            )

    return {
        "version": RECOVERY_VERSION,
        "apply": bool(apply),
        "dataVersion": resolved_version,
        "matchedCurrentAgent1TerminalCount": len(rows),
        "eligibleCount": len(eligible),
        "updatedCount": len(updated),
        "updated": updated,
        "skipped": skipped[:100],
        "rule": "Only current Agent1 observation/failure rows backed by a non-v3 input are requeued once.",
    }


__all__ = ["RECOVERY_VERSION", "latest_product_data_version", "requeue_agent1_v2258"]
