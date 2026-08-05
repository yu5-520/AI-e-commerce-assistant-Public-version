"""V20.28 runtime breakpoint recovery.

Only proven protocol/state defects are repaired. Business failures and incomplete
Agent/SOP outputs remain failed. Recovery is idempotent through named history
markers on each pipeline item.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.agent_runtime_contract_v2010_service import (
    missing_action_pack_contract,
    missing_sop_contract,
    payload_from_row,
)

PIPELINE_RUNTIME_RECOVERY_VERSION = "20.28"
_AGENT_PRODUCT_STAGES = {
    "agent1_pending",
    "agent1_running",
    "agent1_failed",
    "agent1_completed",
    "agent1_output_invalid",
    "action_pack_ready",
    "action_pack_invalid",
    "agent2_running",
    "agent2_failed",
    "agent2_output_invalid",
    "agent2_completed",
    "sop_mapped",
    "task_admitted",
}
_OLD_TASK_POOL_FAILURE_MARKERS = {
    "sopSource_v20_27",
    "missing_v20_27_chain_integrity",
    "rejected_by_v20_27_decision_contract",
}
_LEGACY_LIFECYCLE_FAILURE_MARKERS = {
    "rejected_by_lifecycle_validator",
    "缺少真实任务映射Agent证据",
    "V18.10拒绝入池",
}


def _now() -> str:
    return datetime.now().isoformat()


def _table_exists(conn: Any, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    )


def _latest_data_version() -> str | None:
    with connect() as conn:
        if not _table_exists(conn, "pipeline_items"):
            return None
        row = conn.execute(
            """
            SELECT data_version
            FROM pipeline_items
            WHERE data_version IS NOT NULL AND TRIM(data_version) != ''
            GROUP BY data_version
            ORDER BY MAX(updated_at) DESC
            LIMIT 1
            """
        ).fetchone()
    return str(row["data_version"]) if row and row["data_version"] else None


def _wrapper(row: Any, business_payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        raw = loads(row["payload"])
    except Exception:
        raw = {}
    if isinstance(raw, dict) and isinstance(raw.get("payload"), dict):
        raw["payload"] = business_payload
        raw["version"] = raw.get("version") or PIPELINE_RUNTIME_RECOVERY_VERSION
        return raw
    return {
        "envelope": {},
        "payload": business_payload,
        "version": PIPELINE_RUNTIME_RECOVERY_VERSION,
    }


def _reason(payload: Dict[str, Any]) -> str:
    return str(
        payload.get("reason")
        or payload.get("blockedReason")
        or payload.get("error")
        or ""
    ).strip()


def _history(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    value = payload.get("runtimeRecoveryHistory")
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _already_recovered(payload: Dict[str, Any], recovery_type: str) -> bool:
    return any(item.get("type") == recovery_type for item in _history(payload))


def _mark_recovery(
    payload: Dict[str, Any],
    *,
    recovery_type: str,
    from_stage: str,
    to_stage: str,
) -> Dict[str, Any]:
    entry = {
        "version": PIPELINE_RUNTIME_RECOVERY_VERSION,
        "type": recovery_type,
        "fromStage": from_stage,
        "toStage": to_stage,
        "recoveredAt": _now(),
    }
    return {
        **payload,
        "runtimeRecovery": entry,
        "runtimeRecoveryHistory": (_history(payload) + [entry])[-12:],
    }


def _update_row(
    conn: Any,
    row: Any,
    *,
    stage: str,
    status: str,
    recovery_type: str,
    retry: bool,
    error_reason: str | None = None,
) -> None:
    payload = dict(payload_from_row(row))
    payload = _mark_recovery(
        payload,
        recovery_type=recovery_type,
        from_stage=str(row["current_stage"] or ""),
        to_stage=stage,
    )
    conn.execute(
        """
        UPDATE pipeline_items
        SET current_stage=?,
            status=?,
            retry_count=retry_count + ?,
            error_reason=?,
            payload=?,
            updated_at=?
        WHERE item_id=?
        """,
        (
            stage,
            status,
            1 if retry else 0,
            error_reason,
            dumps(_wrapper(row, payload)),
            _now(),
            row["item_id"],
        ),
    )


def _rows(
    conn: Any,
    data_version: str,
    where_sql: str,
    params: Iterable[Any] = (),
    *,
    limit: int = 500,
) -> List[Any]:
    return list(
        conn.execute(
            f"""
            SELECT *
            FROM pipeline_items
            WHERE data_version=?
              AND ({where_sql})
            ORDER BY updated_at ASC
            LIMIT ?
            """,
            (data_version, *list(params), max(1, min(2000, int(limit)))),
        ).fetchall()
    )


def _task_admission(payload: Dict[str, Any]) -> Dict[str, Any]:
    value = payload.get("taskAdmission")
    return value if isinstance(value, dict) else {}


def _marker_text(payload: Dict[str, Any]) -> str:
    admission = _task_admission(payload)
    failures = admission.get("failures") if isinstance(admission.get("failures"), list) else []
    values = [
        admission.get("status"),
        admission.get("reason"),
        payload.get("reason"),
        payload.get("errorReason"),
        *failures,
    ]
    return " ".join(str(value or "") for value in values)


def _semantic_reason_exists(payload: Dict[str, Any]) -> bool:
    decision = payload.get("sopDecision") if isinstance(payload.get("sopDecision"), dict) else {}
    plan = decision.get("taskPlan") if isinstance(decision.get("taskPlan"), dict) else {}
    agent2 = decision.get("agent2ActionPlan") if isinstance(decision.get("agent2ActionPlan"), dict) else {}
    package = decision.get("productJudgmentPackage") if isinstance(decision.get("productJudgmentPackage"), dict) else {}
    agent1 = package.get("agent1OperatingJudgment") if isinstance(package.get("agent1OperatingJudgment"), dict) else {}
    return any(
        str(value or "").strip()
        for value in (
            plan.get("reason"),
            decision.get("reason"),
            agent2.get("reason"),
            agent2.get("differentiationReason"),
            agent1.get("businessHypothesis"),
            agent1.get("primaryOperatingGap"),
            agent1.get("finding"),
        )
    )


def recover_pipeline_runtime_breakpoints(
    data_version: str | None = None,
    *,
    limit: int = 500,
) -> Dict[str, Any]:
    resolved = data_version or _latest_data_version()
    if not resolved:
        return {
            "version": PIPELINE_RUNTIME_RECOVERY_VERSION,
            "dataVersion": None,
            "ran": False,
            "recoveredItemCount": 0,
            "reason": "no_data_version",
        }

    counts = {
        "batchAnchorQuarantinedCount": 0,
        "agent1ObservedNormalizedCount": 0,
        "agent1IdentityRetryCount": 0,
        "actionPackRetryCount": 0,
        "actionPackInvalidNormalizedCount": 0,
        "taskPoolContractRetryCount": 0,
        "lifecycleContractRetryCount": 0,
        "skippedBusinessFailureCount": 0,
    }

    with connect() as conn:
        if not _table_exists(conn, "pipeline_items"):
            return {
                "version": PIPELINE_RUNTIME_RECOVERY_VERSION,
                "dataVersion": resolved,
                "ran": False,
                "recoveredItemCount": 0,
                "reason": "pipeline_items_missing",
            }

        for row in _rows(conn, resolved, "item_id LIKE 'PI-BATCH-%'", limit=limit):
            if str(row["current_stage"] or "") not in _AGENT_PRODUCT_STAGES:
                continue
            payload = dict(payload_from_row(row))
            recovery_type = "batch_anchor_removed_from_product_agent_chain"
            if _already_recovered(payload, recovery_type):
                continue
            _update_row(
                conn,
                row,
                stage="quality_gate_ready",
                status="completed",
                recovery_type=recovery_type,
                retry=False,
                error_reason=None,
            )
            counts["batchAnchorQuarantinedCount"] += 1

        for row in _rows(
            conn,
            resolved,
            "current_stage='agent1_running' AND status='observed'",
            limit=limit,
        ):
            payload = dict(payload_from_row(row))
            recovery_type = "agent1_observed_terminal_state_normalized"
            if _already_recovered(payload, recovery_type):
                continue
            _update_row(
                conn,
                row,
                stage="observed_soft_gate",
                status="observed",
                recovery_type=recovery_type,
                retry=False,
                error_reason=None,
            )
            counts["agent1ObservedNormalizedCount"] += 1

        for row in _rows(
            conn,
            resolved,
            "current_stage='agent1_failed' AND status='failed' AND retry_count < 3",
            limit=limit,
        ):
            payload = dict(payload_from_row(row))
            recovery_type = "agent1_stable_correlation_retry"
            if _reason(payload) != "agent_returned_no_matching_judgment" or _already_recovered(payload, recovery_type):
                counts["skippedBusinessFailureCount"] += 1
                continue
            _update_row(
                conn,
                row,
                stage="agent1_pending",
                status="retry",
                recovery_type=recovery_type,
                retry=True,
                error_reason=None,
            )
            counts["agent1IdentityRetryCount"] += 1

        for row in _rows(
            conn,
            resolved,
            "current_stage='action_pack_ready' AND status='failed' AND retry_count < 3",
            limit=limit,
        ):
            payload = dict(payload_from_row(row))
            missing = missing_action_pack_contract(payload)
            if missing:
                recovery_type = "action_pack_failure_stage_normalized"
                if _already_recovered(payload, recovery_type):
                    continue
                _update_row(
                    conn,
                    row,
                    stage="action_pack_invalid",
                    status="failed",
                    recovery_type=recovery_type,
                    retry=False,
                    error_reason=",".join(missing),
                )
                counts["actionPackInvalidNormalizedCount"] += 1
                continue
            recovery_type = "semantically_ready_action_pack_requeued"
            if _already_recovered(payload, recovery_type):
                continue
            _update_row(
                conn,
                row,
                stage="action_pack_ready",
                status="retry",
                recovery_type=recovery_type,
                retry=True,
                error_reason=None,
            )
            counts["actionPackRetryCount"] += 1

        for row in _rows(
            conn,
            resolved,
            "current_stage='task_admitted' AND status='failed' AND retry_count < 5",
            limit=limit,
        ):
            payload = dict(payload_from_row(row))
            if missing_sop_contract(payload):
                counts["skippedBusinessFailureCount"] += 1
                continue
            marker_text = _marker_text(payload)
            admission = _task_admission(payload)
            old_task_pool = any(
                marker in marker_text for marker in _OLD_TASK_POOL_FAILURE_MARKERS
            ) or str(admission.get("taskPoolAdmissionCoreVersion") or "") == "20.27"
            legacy_lifecycle = any(
                marker in marker_text for marker in _LEGACY_LIFECYCLE_FAILURE_MARKERS
            )

            if old_task_pool:
                recovery_type = "v20_27_task_pool_string_gate_removed"
                if _already_recovered(payload, recovery_type):
                    continue
                _update_row(
                    conn,
                    row,
                    stage="sop_mapped",
                    status="retry",
                    recovery_type=recovery_type,
                    retry=True,
                    error_reason=None,
                )
                counts["taskPoolContractRetryCount"] += 1
                continue

            if legacy_lifecycle:
                recovery_type = "v18_lifecycle_source_gate_removed"
                if _already_recovered(payload, recovery_type):
                    continue
                if "title/reason" in marker_text and not _semantic_reason_exists(payload):
                    counts["skippedBusinessFailureCount"] += 1
                    continue
                _update_row(
                    conn,
                    row,
                    stage="sop_mapped",
                    status="retry",
                    recovery_type=recovery_type,
                    retry=True,
                    error_reason=None,
                )
                counts["lifecycleContractRetryCount"] += 1
                continue

            counts["skippedBusinessFailureCount"] += 1

        conn.commit()

    recovered = sum(
        value
        for key, value in counts.items()
        if key != "skippedBusinessFailureCount"
    )
    return {
        "version": PIPELINE_RUNTIME_RECOVERY_VERSION,
        "dataVersion": resolved,
        "ran": recovered > 0,
        "recoveredItemCount": recovered,
        **counts,
        "rule": "Only proven runtime protocol/state defects are repaired; business and semantic failures remain failed.",
    }
