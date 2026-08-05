"""V23.2.16 Agent3 SOP pipeline with canonical Agent2 proof inheritance."""
from __future__ import annotations

import os
import socket
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Tuple

from src.repositories.sqlite_repository import connect, ensure_columns
from src.services.agent3_system_constraint_v23215_service import (
    AGENT2_PROOF_BRIDGE_VERSION,
    canonicalize_agent2_draft_proof,
    ensure_agent3_sop_input_ref_v23216,
)
from src.services.agent_input_contract_v225_service import AGENT3_SOP_INPUT_SCHEMA
from src.services.agent_input_transport_v225_service import resolve_agent_input_ref
from src.services.agent_runtime_contract_v225_service import (
    AGENT_RUNTIME_CONTRACT_VERSION,
    missing_agent2_draft_completed_contract,
    missing_agent3_sop_completed_contract,
    normalize_agent3_sop_completed_contract,
    payload_from_row,
)
from src.services.agent_token_runtime_v225_service import run_agent3_sop_projected_inputs
from src.services.pipeline_artifact_contract_service import (
    artifact_refs_from_row,
    attach_pipeline_artifact_ref,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    pipeline_item_summary,
    record_pipeline_item_event,
    upsert_pipeline_item,
)

THREE_AGENT_PIPELINE_VERSION = "22.5.0"
PIPELINE_AGENT3_SOP_VERSION = "23.2.16"
AGENT2_DRAFT_READY_STAGE = "agent2_draft_ready"
AGENT3_SOP_RUNNING_STAGE = "agent3_sop_running"
AGENT3_SOP_READY_STAGE = "agent3_sop_ready"
AGENT3_SOP_OUTPUT_INVALID_STAGE = "agent3_sop_output_invalid"
AGENT3_SOP_FAILED_STAGE = "agent3_sop_failed"
DEFAULT_AGENT3_BATCH_SIZE = 2


def _now_dt() -> datetime:
    return datetime.now()


def _now() -> str:
    return _now_dt().isoformat()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def lease_seconds() -> int:
    return _env_int("AGENT3_ITEM_LEASE_SECONDS", 900, 300, 3600)


def max_attempts() -> int:
    return _env_int("AGENT3_MAX_ATTEMPTS", 2, 1, 5)


def ensure_agent3_runtime_columns() -> None:
    from src.services.pipeline_item_service import ensure_pipeline_item_tables

    ensure_pipeline_item_tables()
    with connect() as conn:
        ensure_columns(
            conn,
            "pipeline_items",
            {
                "claim_id": "TEXT",
                "lease_expires_at": "TEXT",
                "retry_after": "TEXT",
                "failure_code": "TEXT",
                "failure_class": "TEXT",
                "agent3_claim_owner": "TEXT",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_agent3_due "
            "ON pipeline_items(current_stage,status,retry_after,priority)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_agent3_lease "
            "ON pipeline_items(current_stage,status,lease_expires_at)"
        )
        conn.commit()


def _pending_items(data_version: str | None, limit: int) -> List[Dict[str, Any]]:
    ensure_agent3_runtime_columns()
    now = _now()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM pipeline_items
            WHERE COALESCE(data_version,'')=COALESCE(?,'')
              AND current_stage=?
              AND status IN ('queued','ready','retry')
              AND (retry_after IS NULL OR retry_after<=?)
            ORDER BY priority ASC,updated_at ASC
            LIMIT ?
            """,
            (data_version, AGENT2_DRAFT_READY_STAGE, now, int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def pending_agent3_sop_item_count(data_version: str | None) -> int:
    return len(_pending_items(data_version, 100000))


def recover_stale_agent3_claims(
    data_version: str | None = None,
    *,
    limit: int = 200,
) -> Dict[str, Any]:
    ensure_agent3_runtime_columns()
    now = _now()
    where = [
        "current_stage=?",
        "status='running'",
        "lease_expires_at IS NOT NULL",
        "lease_expires_at<=?",
    ]
    params: List[Any] = [AGENT3_SOP_RUNNING_STAGE, now]
    if data_version is not None:
        where.append("COALESCE(data_version,'')=COALESCE(?,'')")
        params.append(data_version)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT item_id,retry_count FROM pipeline_items WHERE {' AND '.join(where)} "
            "ORDER BY lease_expires_at ASC LIMIT ?",
            (*params, int(limit)),
        ).fetchall()
        requeued = failed = 0
        for row in rows:
            attempts = int(row["retry_count"] or 0) + 1
            terminal = attempts >= max_attempts()
            conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?,status=?,retry_count=?,claim_id=NULL,
                    agent3_claim_owner=NULL,lease_expires_at=NULL,retry_after=?,
                    failure_code=?,failure_class=?,updated_at=?
                WHERE item_id=? AND current_stage=? AND status='running'
                """,
                (
                    AGENT3_SOP_FAILED_STAGE if terminal else AGENT2_DRAFT_READY_STAGE,
                    "failed" if terminal else "retry",
                    attempts,
                    None
                    if terminal
                    else (_now_dt() + timedelta(seconds=60)).isoformat(),
                    "agent3_stale_lease_exhausted"
                    if terminal
                    else "agent3_stale_lease_recovered",
                    "runtime_lease" if terminal else None,
                    now,
                    row["item_id"],
                    AGENT3_SOP_RUNNING_STAGE,
                ),
            )
            failed += 1 if terminal else 0
            requeued += 0 if terminal else 1
        conn.commit()
    return {
        "version": PIPELINE_AGENT3_SOP_VERSION,
        "requeuedItemCount": requeued,
        "failedItemCount": failed,
        "candidateCount": len(rows),
        "bounded": True,
    }


def _claim_items(
    items: List[Dict[str, Any]],
    *,
    worker_id: str | None = None,
) -> List[Dict[str, Any]]:
    ensure_agent3_runtime_columns()
    owner = str(
        worker_id
        or os.getenv("AGENT3_WORKER_ID")
        or f"{socket.gethostname()}:{os.getpid()}"
    )
    now_dt = _now_dt()
    now = now_dt.isoformat()
    expires = (now_dt + timedelta(seconds=lease_seconds())).isoformat()
    claimed: List[Dict[str, Any]] = []
    with connect() as conn:
        for item in items:
            claim_id = "A3L-" + uuid.uuid4().hex[:20].upper()
            cursor = conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?,status='running',claim_id=?,agent3_claim_owner=?,
                    lease_expires_at=?,retry_after=NULL,failure_code=NULL,
                    failure_class=NULL,error_reason=NULL,updated_at=?
                WHERE item_id=? AND current_stage=?
                  AND status IN ('queued','ready','retry')
                  AND (retry_after IS NULL OR retry_after<=?)
                """,
                (
                    AGENT3_SOP_RUNNING_STAGE,
                    claim_id,
                    owner,
                    expires,
                    now,
                    item.get("item_id"),
                    AGENT2_DRAFT_READY_STAGE,
                    now,
                ),
            )
            if cursor.rowcount == 1:
                next_item = dict(item)
                next_item.update(
                    current_stage=AGENT3_SOP_RUNNING_STAGE,
                    status="running",
                    claim_id=claim_id,
                    agent3_claim_owner=owner,
                    lease_expires_at=expires,
                    updated_at=now,
                )
                claimed.append(next_item)
        conn.commit()
    return claimed


def _claim_owned(item: Dict[str, Any]) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT current_stage,status,claim_id FROM pipeline_items WHERE item_id=?",
            (item.get("item_id"),),
        ).fetchone()
    return bool(
        row
        and row["current_stage"] == AGENT3_SOP_RUNNING_STAGE
        and row["status"] == "running"
        and row["claim_id"] == item.get("claim_id")
    )


def _finish_item(
    item: Dict[str, Any],
    *,
    stage: str,
    status: str,
    output_ref: str,
    payload: Dict[str, Any],
    ref_key: str | None = None,
) -> Dict[str, Any] | None:
    if not _claim_owned(item):
        return None
    envelope = build_item_envelope(
        data_version=item.get("data_version"),
        item_id=item.get("item_id"),
        product_id=item.get("product_id") or payload.get("productId"),
        store_id=item.get("store_id") or payload.get("storeId"),
        signal_id=item.get("signal_id") or payload.get("signalId"),
        package_id=item.get("package_id") or payload.get("packageId"),
        action_family=item.get("action_family") or payload.get("actionFamily"),
        route=item.get("route") or payload.get("route"),
        input_ref=f"pipeline_items:{AGENT3_SOP_RUNNING_STAGE}:{item.get('item_id')}",
        output_ref=output_ref,
        stage=stage,
        artifact_refs=artifact_refs_from_row(item),
    )
    envelope = upsert_pipeline_item(
        envelope,
        stage=stage,
        status=status,
        priority=int(item.get("priority") or 50),
        output_ref=output_ref,
        payload=payload,
    )
    artifact_ref = str(envelope.get("payloadArtifactRef") or "")
    if ref_key and artifact_ref.startswith("ART-"):
        attach_pipeline_artifact_ref(str(item.get("item_id")), ref_key, artifact_ref)
    record_pipeline_item_event(
        envelope,
        station_id="agent3_sop_station",
        stage=stage,
        status=status,
        output_ref=output_ref,
        payload=payload,
    )
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_items
            SET claim_id=NULL,agent3_claim_owner=NULL,lease_expires_at=NULL,
                retry_after=NULL,updated_at=?
            WHERE item_id=?
            """,
            (_now(), item.get("item_id")),
        )
        conn.commit()
    return envelope


def _schedule_provider_failure(
    item: Dict[str, Any],
    package: Dict[str, Any],
    provider: Dict[str, Any],
    reason: str,
) -> str:
    errors = " ".join(str(value or "") for value in provider.get("errors") or [])
    transient = any(
        marker in (reason + " " + errors).lower()
        for marker in (
            "timeout",
            "timed out",
            "429",
            "500",
            "502",
            "503",
            "504",
            "temporarily unavailable",
            "connection",
            "agent3_response_package_unmatched",
        )
    )
    attempt = int(item.get("retry_count") or 0) + 1
    retry = transient and attempt < max_attempts()
    next_retry = (
        (_now_dt() + timedelta(seconds=60 * attempt)).isoformat() if retry else None
    )
    if retry:
        if not _claim_owned(item):
            return "claim_lost"
        with connect() as conn:
            conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?,status='retry',retry_count=?,retry_after=?,
                    claim_id=NULL,agent3_claim_owner=NULL,lease_expires_at=NULL,
                    failure_code='agent3_transient_provider_failure',
                    failure_class='transient_provider_or_protocol',error_reason=?,updated_at=?
                WHERE item_id=? AND claim_id=?
                """,
                (
                    AGENT2_DRAFT_READY_STAGE,
                    attempt,
                    next_retry,
                    reason[:500],
                    _now(),
                    item.get("item_id"),
                    item.get("claim_id"),
                ),
            )
            conn.commit()
        return "retry"
    _finish_item(
        item,
        stage=AGENT3_SOP_FAILED_STAGE,
        status="failed",
        output_ref=(
            f"agent3_sop_failed:{item.get('data_version') or 'latest'}:"
            f"{item.get('package_id') or item.get('item_id')}"
        ),
        payload={
            **package,
            "agent3Provider": provider,
            "reason": reason,
            "failureOwner": "agent3_sop_station",
            "frontendFailureLabel": "Agent3 SOP生成失败",
            "taskAdmissionAllowed": False,
            "fallbackAllowed": False,
        },
        ref_key="agent3SopFailureRef",
    )
    return "failed"


def run_agent3_sop_microbatch_v225(
    data_version: str | None,
    *,
    user_id: str | None = None,
    batch_size: int = DEFAULT_AGENT3_BATCH_SIZE,
) -> Dict[str, Any]:
    stale = recover_stale_agent3_claims(data_version)
    selected = _pending_items(
        data_version,
        max(1, min(6, int(batch_size or DEFAULT_AGENT3_BATCH_SIZE))),
    )
    prepared: Dict[str, Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = {}
    invalid_input = 0
    for item in selected:
        package = canonicalize_agent2_draft_proof(dict(payload_from_row(item)))
        missing = missing_agent2_draft_completed_contract(package)
        if missing:
            invalid_input += 1
            envelope = build_item_envelope(
                data_version=item.get("data_version"),
                item_id=item.get("item_id"),
                product_id=item.get("product_id"),
                store_id=item.get("store_id"),
                signal_id=item.get("signal_id"),
                package_id=item.get("package_id"),
                action_family=item.get("action_family"),
                route=item.get("route"),
                stage=AGENT3_SOP_OUTPUT_INVALID_STAGE,
            )
            payload = {
                **package,
                "reason": "agent2_draft_contract_invalid_before_agent3",
                "missing": missing,
                "failureOwner": "agent2_action_draft_station",
                "frontendFailureLabel": "Agent2草案不完整",
                "taskAdmissionAllowed": False,
                "fallbackAllowed": False,
            }
            upsert_pipeline_item(
                envelope,
                stage=AGENT3_SOP_OUTPUT_INVALID_STAGE,
                status="failed",
                output_ref=(
                    f"agent3_input_invalid:{data_version or 'latest'}:"
                    f"{item.get('item_id')}"
                ),
                payload=payload,
            )
            continue
        try:
            input_ref = ensure_agent3_sop_input_ref_v23216(item)
            envelope = resolve_agent_input_ref(
                input_ref,
                expected_schema=AGENT3_SOP_INPUT_SCHEMA,
            )
            prepared[str(item.get("item_id"))] = (
                item,
                envelope,
                dict(envelope["payload"]),
            )
        except Exception as exc:
            invalid_input += 1
            envelope = build_item_envelope(
                data_version=item.get("data_version"),
                item_id=item.get("item_id"),
                product_id=item.get("product_id"),
                store_id=item.get("store_id"),
                signal_id=item.get("signal_id"),
                package_id=item.get("package_id"),
                action_family=item.get("action_family"),
                route=item.get("route"),
                stage=AGENT3_SOP_OUTPUT_INVALID_STAGE,
            )
            upsert_pipeline_item(
                envelope,
                stage=AGENT3_SOP_OUTPUT_INVALID_STAGE,
                status="failed",
                output_ref=(
                    f"agent3_input_contract_failed:{data_version or 'latest'}:"
                    f"{item.get('item_id')}"
                ),
                payload={
                    **package,
                    "reason": str(exc)[:500],
                    "failureOwner": "agent3_system_constraint_v23215",
                    "frontendFailureLabel": "Agent3输入不完整",
                    "taskAdmissionAllowed": False,
                    "fallbackAllowed": False,
                },
            )
    claimed = _claim_items(
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
            "version": PIPELINE_AGENT3_SOP_VERSION,
            "agent2ProofBridgeVersion": AGENT2_PROOF_BRIDGE_VERSION,
            "dataVersion": data_version,
            "ran": bool(invalid_input or stale.get("requeuedItemCount")),
            "claimedItemCount": 0,
            "invalidInputItemCount": invalid_input,
            "pendingItemCount": pending_agent3_sop_item_count(data_version),
            "staleRunningRecovery": stale,
            "provider": {
                "providerStatus": "skipped_no_valid_agent3_inputs",
                "actualCalls": 0,
            },
        }
    sops, provider = run_agent3_sop_projected_inputs(
        [value[1] for value in prepared.values()],
        data_version=data_version,
        max_items_per_call=1,
    )
    completed = invalid_output = retry_scheduled = failed = stale_results = 0
    by_status: Counter[str] = Counter()
    for item, envelope, projected in prepared.values():
        package_id = str(
            projected.get("packageId")
            or item.get("package_id")
            or item.get("item_id")
            or ""
        )
        sop = sops.get(package_id)
        if not isinstance(sop, dict) or not sop:
            outcome = _schedule_provider_failure(
                item,
                canonicalize_agent2_draft_proof(dict(payload_from_row(item))),
                provider,
                next(
                    (
                        value
                        for value in provider.get("errors") or []
                        if package_id in str(value)
                    ),
                    None,
                )
                or "agent3_returned_no_matching_sop",
            )
            retry_scheduled += 1 if outcome == "retry" else 0
            failed += 1 if outcome == "failed" else 0
            stale_results += 1 if outcome == "claim_lost" else 0
            continue
        by_status[str(sop.get("sopStatus") or "missing")] += 1
        current_package = canonicalize_agent2_draft_proof(
            dict(payload_from_row(item))
        )
        candidate = normalize_agent3_sop_completed_contract(
            current_package,
            sop,
            provider,
        )
        missing = missing_agent3_sop_completed_contract(candidate)
        if missing:
            invalid_output += 1
            result = _finish_item(
                item,
                stage=AGENT3_SOP_OUTPUT_INVALID_STAGE,
                status="failed",
                output_ref=(
                    f"agent3_sop_output_invalid:{data_version or 'latest'}:"
                    f"{package_id}"
                ),
                payload={
                    **candidate,
                    "reason": "agent3_sop_contract_invalid",
                    "missing": missing,
                    "failureOwner": "agent3_sop_station",
                    "frontendFailureLabel": "Agent3 SOP不完整",
                    "taskAdmissionAllowed": False,
                    "fallbackAllowed": False,
                },
                ref_key="agent3SopFailureRef",
            )
            stale_results += 1 if result is None else 0
            continue
        candidate.update(
            agent2ProofBridgeVersion=AGENT2_PROOF_BRIDGE_VERSION,
            agent3SopInputRef=str(
                artifact_refs_from_row(item).get("agent3SopInputRef") or ""
            ),
            runtimeSource="agent3SopInputRef",
            sourceArtifactRefs=envelope.get("sourceArtifactRefs"),
            inputProjectionAudit=envelope.get("projectionAudit"),
            outputContract="V22.5.agent3_sop_ready",
            taskAdmissionAllowed=False,
            fallbackAllowed=False,
        )
        result = _finish_item(
            item,
            stage=AGENT3_SOP_READY_STAGE,
            status="ready",
            output_ref=f"agent3_sop:{data_version or 'latest'}:{package_id}",
            payload=candidate,
            ref_key="agent3SopRef",
        )
        if result is None:
            stale_results += 1
        else:
            completed += 1
    return {
        "version": PIPELINE_AGENT3_SOP_VERSION,
        "contractVersion": AGENT_RUNTIME_CONTRACT_VERSION,
        "agent2ProofBridgeVersion": AGENT2_PROOF_BRIDGE_VERSION,
        "dataVersion": data_version,
        "ran": True,
        "claimedItemCount": len(prepared),
        "completedItemCount": completed,
        "invalidInputItemCount": invalid_input,
        "invalidOutputItemCount": invalid_output,
        "retryScheduledItemCount": retry_scheduled,
        "failedItemCount": failed + invalid_output,
        "staleResultIgnoredCount": stale_results,
        "bySopStatus": dict(by_status),
        "pendingItemCount": pending_agent3_sop_item_count(data_version),
        "provider": provider,
        "pipelineItemSummary": pipeline_item_summary(
            data_version=data_version,
            limit=40,
        ),
        "staleRunningRecovery": stale,
        "runtimeSource": "agent3SopInputRef",
        "fallbackAllowed": False,
    }


# Registry V23 keeps the historical symbol name as the public runtime owner.
run_agent3_sop_microbatch = run_agent3_sop_microbatch_v225


__all__ = [
    "THREE_AGENT_PIPELINE_VERSION",
    "PIPELINE_AGENT3_SOP_VERSION",
    "AGENT2_DRAFT_READY_STAGE",
    "AGENT3_SOP_RUNNING_STAGE",
    "AGENT3_SOP_READY_STAGE",
    "AGENT3_SOP_OUTPUT_INVALID_STAGE",
    "AGENT3_SOP_FAILED_STAGE",
    "pending_agent3_sop_item_count",
    "recover_stale_agent3_claims",
    "run_agent3_sop_microbatch",
    "run_agent3_sop_microbatch_v225",
]
