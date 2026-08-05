"""V21.4.2/V21.4.3 Agent2 runtime resilience.

V21.4.2 gives every claimed Agent2 item a finite lease. A process crash can no
longer leave a product in agent2_running forever. V21.4.3 classifies failures,
retries only transient provider/protocol failures with bounded backoff, and
moves exhausted or permanent failures to an explicit dead-letter stage.

Business/semantic output failures are never retried here. They remain owned by
agent2_output_invalid so a model-quality problem cannot be disguised as an
infrastructure retry.
"""

from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads
from src.services.agent_runtime_contract_v2141_service import payload_from_row
from src.services.pipeline_item_service import STAGE_ORDER, ensure_pipeline_item_tables

AGENT2_LEASE_VERSION = "21.4.2"
AGENT2_FAILURE_GOVERNANCE_VERSION = "21.4.3"
AGENT2_RESILIENCE_VERSION = AGENT2_FAILURE_GOVERNANCE_VERSION

ACTION_PACK_READY_STAGE = "action_pack_ready"
AGENT2_RUNNING_STAGE = "agent2_running"
AGENT2_FAILED_STAGE = "agent2_failed"
AGENT2_OUTPUT_INVALID_STAGE = "agent2_output_invalid"
AGENT2_DEAD_LETTER_STAGE = "agent2_dead_letter"

STAGE_ORDER.setdefault(AGENT2_OUTPUT_INVALID_STAGE, 77)
STAGE_ORDER.setdefault(AGENT2_DEAD_LETTER_STAGE, 78)

_TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "temporarily unavailable",
    "connection reset",
    "connection refused",
    "connection aborted",
    "remote disconnected",
    "rate limit",
    "too many requests",
    "http 429",
    "status 429",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
    "status 500",
    "status 502",
    "status 503",
    "status 504",
    "service unavailable",
    "gateway timeout",
    "provider_partial_response",
    "agent2_returned_no_plan",
    "agent2_item_provenance_missing",
    "agent2_response_product_unmatched",
)
_PERMANENT_PROVIDER_MARKERS = (
    "http 400",
    "status 400",
    "http 401",
    "status 401",
    "http 403",
    "status 403",
    "unauthorized",
    "forbidden",
    "invalid api key",
    "api key",
    "model not found",
    "invalid model",
    "permission denied",
    "invalid endpoint",
)
_SEMANTIC_MARKERS = (
    "agent2_output_contract_invalid",
    "creative_test_groups_insufficient",
    "semanticcontractmissing",
    "action_plan_missing_data",
    "conflict_requires_rejudgment",
)


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
    # Provider timeout is normally 180 seconds. The default lease leaves enough
    # room for transport cleanup while still recovering a crashed worker quickly.
    return _env_int("AGENT2_ITEM_LEASE_SECONDS", 420, 120, 3600)


def max_attempts() -> int:
    return _env_int("AGENT2_MAX_ATTEMPTS", 3, 1, 8)


def ensure_agent2_runtime_columns() -> None:
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
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_agent2_lease "
            "ON pipeline_items(current_stage,status,lease_expires_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_agent2_retry "
            "ON pipeline_items(current_stage,status,retry_after,priority)"
        )
        conn.commit()


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _wrapper(row: Any, business_payload: Dict[str, Any]) -> Dict[str, Any]:
    raw = _load(row["payload"] if row is not None else None)
    if isinstance(raw.get("payload"), dict):
        raw["payload"] = business_payload
        return raw
    return {
        "envelope": {},
        "payload": business_payload,
        "version": AGENT2_RESILIENCE_VERSION,
    }


def _proof_present(payload: Dict[str, Any]) -> bool:
    proof = payload.get("agent2ExecutionProof")
    if not isinstance(proof, dict):
        plan = payload.get("agent2ActionPlan")
        proof = plan.get("agent2ExecutionProof") if isinstance(plan, dict) else None
    if not isinstance(proof, dict):
        return False
    return bool(
        proof.get("resultMatched") is True
        and (
            proof.get("providerCallExecuted") is True
            or proof.get("exactReplayValidated") is True
        )
        and proof.get("fallbackUsed") is not True
    )


def _runtime_history(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    value = payload.get("agent2RuntimeHistory")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _with_runtime_event(
    payload: Dict[str, Any],
    *,
    event_type: str,
    details: Dict[str, Any],
) -> Dict[str, Any]:
    event = {
        "version": AGENT2_RESILIENCE_VERSION,
        "type": event_type,
        "at": _now(),
        **details,
    }
    return {
        **payload,
        "agent2Runtime": event,
        "agent2RuntimeHistory": (_runtime_history(payload) + [event])[-20:],
    }


def _claim_owner(worker_id: str | None) -> str:
    return str(worker_id or os.getenv("AGENT2_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}")


def claim_agent2_items(
    items: Iterable[Dict[str, Any]],
    *,
    worker_id: str | None = None,
) -> List[Dict[str, Any]]:
    """Atomically claim ready items and attach a finite V21.4.2 lease."""

    ensure_agent2_runtime_columns()
    claimed: List[Dict[str, Any]] = []
    owner = _claim_owner(worker_id)
    now_dt = _now_dt()
    now = now_dt.isoformat()
    expires = (now_dt + timedelta(seconds=lease_seconds())).isoformat()

    with connect() as conn:
        for item in items:
            item_id = str(item.get("item_id") or "")
            if not item_id:
                continue
            row = conn.execute(
                "SELECT * FROM pipeline_items WHERE item_id=?",
                (item_id,),
            ).fetchone()
            if not row:
                continue
            if str(row["current_stage"] or "") != ACTION_PACK_READY_STAGE:
                continue
            if str(row["status"] or "") not in {"queued", "ready", "retry"}:
                continue
            retry_after = row["retry_after"] if "retry_after" in row.keys() else None
            if retry_after and str(retry_after) > now:
                continue

            claim_id = f"A2L-{uuid.uuid4().hex[:20].upper()}"
            payload = dict(payload_from_row(row))
            attempt = int(row["retry_count"] or 0) + 1
            payload = _with_runtime_event(
                payload,
                event_type="agent2_item_claimed",
                details={
                    "leaseVersion": AGENT2_LEASE_VERSION,
                    "claimId": claim_id,
                    "claimOwner": owner,
                    "claimedAt": now,
                    "leaseExpiresAt": expires,
                    "attempt": attempt,
                },
            )
            cursor = conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?, status='running', claim_id=?,
                    lease_expires_at=?, retry_after=NULL,
                    failure_code=NULL, failure_class=NULL,
                    error_reason=NULL, payload=?, updated_at=?
                WHERE item_id=? AND current_stage=?
                  AND status IN ('queued','ready','retry')
                  AND (retry_after IS NULL OR retry_after<=?)
                """,
                (
                    AGENT2_RUNNING_STAGE,
                    claim_id,
                    expires,
                    dumps(_wrapper(row, payload)),
                    now,
                    item_id,
                    ACTION_PACK_READY_STAGE,
                    now,
                ),
            )
            if cursor.rowcount == 1:
                next_item = dict(row)
                next_item.update(
                    {
                        "current_stage": AGENT2_RUNNING_STAGE,
                        "status": "running",
                        "claim_id": claim_id,
                        "lease_expires_at": expires,
                        "updated_at": now,
                    }
                )
                claimed.append(next_item)
        conn.commit()
    return claimed


def classify_agent2_failure(
    provider: Dict[str, Any] | None,
    reason: str | None,
) -> Dict[str, Any]:
    provider = provider if isinstance(provider, dict) else {}
    errors = provider.get("errors") if isinstance(provider.get("errors"), list) else []
    text = " ".join([str(reason or ""), *(str(item or "") for item in errors)]).lower()

    if any(marker in text for marker in _SEMANTIC_MARKERS):
        return {
            "failureClass": "semantic_output",
            "failureCode": "agent2_semantic_output_invalid",
            "retryEligible": False,
            "owner": "agent2_output_quality",
        }
    if any(marker in text for marker in _PERMANENT_PROVIDER_MARKERS):
        return {
            "failureClass": "permanent_provider_configuration",
            "failureCode": "agent2_provider_configuration_error",
            "retryEligible": False,
            "owner": "llm_provider_configuration",
        }
    if any(marker in text for marker in _TRANSIENT_MARKERS):
        return {
            "failureClass": "transient_provider_or_protocol",
            "failureCode": "agent2_transient_provider_failure",
            "retryEligible": True,
            "owner": "agent2_runtime",
        }
    return {
        "failureClass": "unclassified_provider_failure",
        "failureCode": "agent2_unclassified_provider_failure",
        "retryEligible": True,
        "owner": "agent2_runtime",
    }


def _backoff_seconds(attempt: int) -> int:
    base = _env_int("AGENT2_RETRY_BASE_SECONDS", 15, 5, 300)
    maximum = _env_int("AGENT2_RETRY_MAX_SECONDS", 300, base, 3600)
    return min(maximum, base * (2 ** max(0, attempt - 1)))


def schedule_agent2_failure(
    item: Dict[str, Any],
    package: Dict[str, Any],
    provider: Dict[str, Any],
    reason: str,
) -> Dict[str, Any]:
    """Retry only governed transient failures; otherwise dead-letter the item."""

    ensure_agent2_runtime_columns()
    item_id = str(item.get("item_id") or "")
    classification = classify_agent2_failure(provider, reason)
    with connect() as conn:
        row = conn.execute("SELECT * FROM pipeline_items WHERE item_id=?", (item_id,)).fetchone()
        if not row:
            return {**classification, "updated": False, "reason": "pipeline_item_missing"}
        attempt = int(row["retry_count"] or 0) + 1
        retry_eligible = bool(classification["retryEligible"]) and attempt < max_attempts()
        now_dt = _now_dt()
        next_retry = (
            now_dt + timedelta(seconds=_backoff_seconds(attempt))
        ).isoformat() if retry_eligible else None
        stage = ACTION_PACK_READY_STAGE if retry_eligible else AGENT2_DEAD_LETTER_STAGE
        status = "retry" if retry_eligible else "failed"
        payload = {
            **package,
            "agent2Provider": provider,
            "agent2Source": "not_completed",
            "actionPlanStatus": "blocked",
            "reason": reason,
            "blockedReason": reason,
            "failureOwner": classification["owner"],
            "frontendFailureLabel": (
                "Agent2暂时失败，系统将在退避后重试"
                if retry_eligible
                else "Agent2已进入人工处理队列"
            ),
            "taskAdmissionAllowed": False,
            "fallbackAllowed": False,
            "agent2RetryPolicy": {
                "version": AGENT2_FAILURE_GOVERNANCE_VERSION,
                **classification,
                "attempt": attempt,
                "maxAttempts": max_attempts(),
                "nextRetryAt": next_retry,
                "terminal": not retry_eligible,
            },
        }
        payload = _with_runtime_event(
            payload,
            event_type="agent2_retry_scheduled" if retry_eligible else "agent2_dead_lettered",
            details={
                **classification,
                "fromStage": str(row["current_stage"] or ""),
                "toStage": stage,
                "attempt": attempt,
                "nextRetryAt": next_retry,
            },
        )
        conn.execute(
            """
            UPDATE pipeline_items
            SET current_stage=?, status=?, retry_count=?, retry_after=?,
                claim_id=NULL, lease_expires_at=NULL,
                failure_code=?, failure_class=?, error_reason=?,
                payload=?, updated_at=?
            WHERE item_id=?
            """,
            (
                stage,
                status,
                attempt,
                next_retry,
                classification["failureCode"],
                classification["failureClass"],
                reason[:500],
                dumps(_wrapper(row, payload)),
                now_dt.isoformat(),
                item_id,
            ),
        )
        conn.commit()
    return {
        **classification,
        "updated": True,
        "stage": stage,
        "status": status,
        "attempt": attempt,
        "nextRetryAt": next_retry,
        "terminal": not retry_eligible,
    }


def clear_agent2_runtime_control(item_id: str | None) -> None:
    if not item_id:
        return
    ensure_agent2_runtime_columns()
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_items
            SET claim_id=NULL, lease_expires_at=NULL, retry_after=NULL,
                failure_code=NULL, failure_class=NULL
            WHERE item_id=?
            """,
            (item_id,),
        )
        conn.commit()


def recover_stale_agent2_claims(
    data_version: str | None = None,
    *,
    limit: int = 500,
) -> Dict[str, Any]:
    """Recover expired V21.4.2 leases without retrying semantic failures."""

    ensure_agent2_runtime_columns()
    now_dt = _now_dt()
    now = now_dt.isoformat()
    legacy_cutoff = (now_dt - timedelta(seconds=lease_seconds())).isoformat()
    params: List[Any] = [AGENT2_RUNNING_STAGE, now, legacy_cutoff]
    where = "current_stage=? AND status='running' AND ((lease_expires_at IS NOT NULL AND lease_expires_at<=?) OR (lease_expires_at IS NULL AND updated_at<=?))"
    if data_version:
        where += " AND data_version=?"
        params.append(data_version)
    params.append(max(1, min(2000, int(limit))))

    recovered = dead_lettered = proof_quarantined = 0
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {where} ORDER BY updated_at ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        for row in rows:
            payload = dict(payload_from_row(row))
            attempt = int(row["retry_count"] or 0) + 1
            has_proof = _proof_present(payload)
            retry_eligible = not has_proof and attempt < max_attempts()
            stage = ACTION_PACK_READY_STAGE if retry_eligible else AGENT2_DEAD_LETTER_STAGE
            status = "retry" if retry_eligible else "failed"
            next_retry = now if retry_eligible else None
            code = (
                "agent2_lease_expired"
                if retry_eligible
                else "agent2_stale_claim_with_uncommitted_proof"
                if has_proof
                else "agent2_lease_retry_exhausted"
            )
            failure_class = (
                "stale_runtime_lease"
                if retry_eligible
                else "uncommitted_provider_result"
                if has_proof
                else "retry_exhausted"
            )
            payload = _with_runtime_event(
                payload,
                event_type="agent2_stale_claim_requeued" if retry_eligible else "agent2_stale_claim_dead_lettered",
                details={
                    "leaseVersion": AGENT2_LEASE_VERSION,
                    "claimId": row["claim_id"],
                    "fromStage": AGENT2_RUNNING_STAGE,
                    "toStage": stage,
                    "attempt": attempt,
                    "proofPresent": has_proof,
                    "failureCode": code,
                },
            )
            payload["agent2RetryPolicy"] = {
                "version": AGENT2_FAILURE_GOVERNANCE_VERSION,
                "failureClass": failure_class,
                "failureCode": code,
                "retryEligible": retry_eligible,
                "attempt": attempt,
                "maxAttempts": max_attempts(),
                "nextRetryAt": next_retry,
                "terminal": not retry_eligible,
            }
            conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?, status=?, retry_count=?, retry_after=?,
                    claim_id=NULL, lease_expires_at=NULL,
                    failure_code=?, failure_class=?, error_reason=?,
                    payload=?, updated_at=?
                WHERE item_id=? AND current_stage=? AND status='running'
                """,
                (
                    stage,
                    status,
                    attempt,
                    next_retry,
                    code,
                    failure_class,
                    code,
                    dumps(_wrapper(row, payload)),
                    now,
                    row["item_id"],
                    AGENT2_RUNNING_STAGE,
                ),
            )
            if retry_eligible:
                recovered += 1
            else:
                dead_lettered += 1
                proof_quarantined += 1 if has_proof else 0
        conn.commit()

    return {
        "version": AGENT2_RESILIENCE_VERSION,
        "leaseVersion": AGENT2_LEASE_VERSION,
        "failureGovernanceVersion": AGENT2_FAILURE_GOVERNANCE_VERSION,
        "dataVersion": data_version,
        "ran": bool(recovered or dead_lettered),
        "staleClaimCount": len(rows),
        "requeuedCount": recovered,
        "deadLetteredCount": dead_lettered,
        "proofQuarantinedCount": proof_quarantined,
        "leaseSeconds": lease_seconds(),
        "maxAttempts": max_attempts(),
        "rule": "Expired leases are retried only without a committed proof; semantic output failures are never auto-retried.",
    }


def agent2_resilience_summary(data_version: str | None = None) -> Dict[str, Any]:
    ensure_agent2_runtime_columns()
    where = ""
    params: List[Any] = []
    if data_version:
        where = "WHERE data_version=?"
        params.append(data_version)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT current_stage,status,failure_class,COUNT(*) AS count
            FROM pipeline_items
            {where}
            GROUP BY current_stage,status,failure_class
            ORDER BY current_stage,status,failure_class
            """,
            tuple(params),
        ).fetchall()
        stale = conn.execute(
            f"""
            SELECT COUNT(*) AS count FROM pipeline_items
            {where + (' AND ' if where else 'WHERE ')}
            current_stage='agent2_running' AND status='running'
              AND lease_expires_at IS NOT NULL AND lease_expires_at<=?
            """,
            (*params, _now()),
        ).fetchone()
    return {
        "version": AGENT2_RESILIENCE_VERSION,
        "leaseVersion": AGENT2_LEASE_VERSION,
        "failureGovernanceVersion": AGENT2_FAILURE_GOVERNANCE_VERSION,
        "dataVersion": data_version,
        "leaseSeconds": lease_seconds(),
        "maxAttempts": max_attempts(),
        "staleRunningCount": int(stale["count"] or 0) if stale else 0,
        "byStageStatusFailureClass": [dict(row) for row in rows],
    }
