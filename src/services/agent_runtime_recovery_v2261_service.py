"""Deterministic Agent lease, recovery and ROAS target helpers.

V22.3.0 keeps these functions as infrastructure used explicitly by the hard Agent
runtime. This module no longer installs, replaces or rebinds runtime functions.
Agent execution ownership belongs to ``agent_runtime_hard_interface_v230_service``.
"""
from __future__ import annotations

import copy
import os
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, dumps, ensure_columns, loads

AGENT_RUNTIME_RECOVERY_VERSION = "22.2.6.1"
AGENT1_PENDING_STAGE = "agent1_pending"
AGENT1_RUNNING_STAGE = "agent1_running"
AGENT1_FAILED_STAGE = "agent1_failed"
ACTION_PACK_READY_STAGE = "action_pack_ready"
AGENT2_OUTPUT_INVALID_STAGE = "agent2_output_invalid"
ROAS_FAMILIES = {"roas_scale", "roas_guard"}


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


def agent1_lease_seconds() -> int:
    provider_timeout = _env_int("PRODUCT_JUDGMENT_AGENT_TIMEOUT", 180, 30, 1800)
    default = max(420, provider_timeout + 120)
    return _env_int("AGENT1_ITEM_LEASE_SECONDS", default, 180, 3600)


def _load_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return row.get(key) if isinstance(row, dict) else None


def ensure_agent1_runtime_columns() -> None:
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
                "agent1_claim_owner": "TEXT",
                "agent2_target_repair_count": "INTEGER DEFAULT 0",
            },
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_agent1_lease "
            "ON pipeline_items(current_stage,status,lease_expires_at,updated_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pipeline_agent2_target_repair "
            "ON pipeline_items(current_stage,status,action_family,agent2_target_repair_count)"
        )
        conn.commit()


def _claim_owner() -> str:
    return str(os.getenv("AGENT1_WORKER_ID") or f"{socket.gethostname()}:{os.getpid()}")


def claim_agent1_items(items: List[Dict[str, Any]]) -> None:
    """Atomically claim pending Agent1 rows and mutate ``items`` to claimed rows only."""
    ensure_agent1_runtime_columns()
    owner = _claim_owner()
    now_dt = _now_dt()
    now = now_dt.isoformat()
    expires = (now_dt + timedelta(seconds=agent1_lease_seconds())).isoformat()
    claimed: List[Dict[str, Any]] = []
    with connect() as conn:
        for item in items:
            item_id = str(item.get("item_id") or "")
            if not item_id:
                continue
            claim_id = f"A1L-{uuid.uuid4().hex[:20].upper()}"
            cursor = conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?, status='running', claim_id=?,
                    lease_expires_at=?, agent1_claim_owner=?, retry_after=NULL,
                    failure_code=NULL, failure_class=NULL, error_reason=NULL,
                    last_error_code=NULL, updated_at=?
                WHERE item_id=? AND current_stage=?
                  AND status IN ('queued','ready','retry')
                  AND (retry_after IS NULL OR retry_after<=?)
                """,
                (
                    AGENT1_RUNNING_STAGE,
                    claim_id,
                    expires,
                    owner,
                    now,
                    item_id,
                    AGENT1_PENDING_STAGE,
                    now,
                ),
            )
            if cursor.rowcount != 1:
                continue
            next_item = dict(item)
            next_item.update(
                current_stage=AGENT1_RUNNING_STAGE,
                status="running",
                claim_id=claim_id,
                lease_expires_at=expires,
                agent1_claim_owner=owner,
                updated_at=now,
            )
            claimed.append(next_item)
        conn.commit()
    items[:] = claimed


def clear_agent1_runtime_control(item_id: str | None) -> None:
    if not item_id:
        return
    ensure_agent1_runtime_columns()
    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_items
            SET claim_id=NULL, lease_expires_at=NULL, retry_after=NULL,
                agent1_claim_owner=NULL, failure_code=NULL, failure_class=NULL
            WHERE item_id=?
            """,
            (item_id,),
        )
        conn.commit()


def _signal_ref_from_row(row: Any) -> str:
    from src.services.pipeline_artifact_contract_service import artifact_refs_from_row

    refs = artifact_refs_from_row(row)
    return str(refs.get("signalRef") or "").strip()


def _requeue_agent1_row(conn: Any, row: Any, signal_ref: str, now: str) -> None:
    refs = _load_mapping(_row_value(row, "artifact_refs_json"))
    refs["signalRef"] = signal_ref
    refs["currentStageRef"] = signal_ref
    conn.execute(
        """
        UPDATE pipeline_items
        SET current_stage=?, status='retry', retry_count=COALESCE(retry_count,0)+1,
            claim_id=NULL, lease_expires_at=NULL, retry_after=NULL,
            agent1_claim_owner=NULL, failure_code=NULL, failure_class=NULL,
            error_reason=NULL, last_error_code=?, artifact_refs_json=?,
            payload_artifact_ref=?, payload=NULL, updated_at=?
        WHERE item_id=? AND current_stage=? AND status='running'
        """,
        (
            AGENT1_PENDING_STAGE,
            "agent1_running_lease_expired_requeued",
            dumps(refs),
            signal_ref,
            now,
            _row_value(row, "item_id"),
            AGENT1_RUNNING_STAGE,
        ),
    )


def _fail_unrecoverable_agent1_row(conn: Any, row: Any, reason: str, now: str) -> None:
    conn.execute(
        """
        UPDATE pipeline_items
        SET current_stage=?, status='failed', claim_id=NULL,
            lease_expires_at=NULL, retry_after=NULL, agent1_claim_owner=NULL,
            failure_code='agent1_artifact_recovery_failed',
            failure_class='artifact_transport', error_reason=?,
            last_error_code=?, updated_at=?
        WHERE item_id=? AND current_stage=? AND status='running'
        """,
        (
            AGENT1_FAILED_STAGE,
            reason,
            reason,
            now,
            _row_value(row, "item_id"),
            AGENT1_RUNNING_STAGE,
        ),
    )


def recover_stale_agent1_items(
    data_version: str | None = None,
    *,
    limit: int = 500,
    force: bool = False,
) -> Dict[str, Any]:
    """Recover expired/pre-lease Agent1 rows from signalRef, then re-project later."""
    from src.services.artifact_transport_service import validate_artifact

    ensure_agent1_runtime_columns()
    now_dt = _now_dt()
    now = now_dt.isoformat()
    legacy_cutoff = (now_dt - timedelta(seconds=agent1_lease_seconds())).isoformat()
    where = ["current_stage=?", "status='running'"]
    params: List[Any] = [AGENT1_RUNNING_STAGE]
    if data_version is not None:
        where.append("COALESCE(data_version,'')=COALESCE(?,'')")
        params.append(data_version)
    if not force:
        where.append(
            "(lease_expires_at<=? OR (lease_expires_at IS NULL AND updated_at<=?))"
        )
        params.extend([now, legacy_cutoff])

    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(where)} "
            "ORDER BY updated_at ASC LIMIT ?",
            (*params, max(1, min(2000, int(limit)))),
        ).fetchall()
        requeued = failed = 0
        for row in rows:
            signal_ref = _signal_ref_from_row(row)
            validation = validate_artifact(signal_ref) if signal_ref else {"ok": False, "status": "missing"}
            if signal_ref and validation.get("ok") is True:
                _requeue_agent1_row(conn, row, signal_ref, now)
                requeued += 1
            else:
                reason = (
                    f"agent1_recovery_signal_ref_invalid:{signal_ref or 'missing'}:"
                    f"{validation.get('status') or 'invalid'}"
                )
                _fail_unrecoverable_agent1_row(conn, row, reason, now)
                failed += 1
        conn.commit()

    return {
        "version": AGENT_RUNTIME_RECOVERY_VERSION,
        "dataVersion": data_version,
        "staleItemCount": len(rows),
        "requeuedItemCount": requeued,
        "failedArtifactItemCount": failed,
        "leaseSeconds": agent1_lease_seconds(),
        "recoverySource": "artifactRefs.signalRef",
        "nextRuntimeSource": "artifactRefs.agent1InputRef",
        "legacyPayloadFallbackAllowed": False,
    }


def _identity(package: Dict[str, Any]) -> Dict[str, Any]:
    product = package.get("productIdentity") if isinstance(package.get("productIdentity"), dict) else {}
    return {
        "storeId": package.get("storeId") or product.get("storeId"),
        "productId": package.get("productId") or product.get("productId"),
        "productTitle": package.get("productTitle")
        or package.get("title")
        or product.get("productTitle")
        or product.get("title"),
    }


def canonical_roas_execution_object(package: Dict[str, Any]) -> Dict[str, Any]:
    pack = package.get("actionParameterPack") if isinstance(package.get("actionParameterPack"), dict) else package
    identity = _identity(package)
    plans = [item for item in pack.get("adPlanFacts") or [] if isinstance(item, dict)]
    plan_ids = [str(item.get("planId")) for item in plans if item.get("planId")]
    plan_names = [str(item.get("planName")) for item in plans if item.get("planName")]
    if len(plan_ids) == 1:
        return {
            "targetType": "ad_plan",
            "targetId": plan_ids[0],
            "targetName": plan_names[0] if len(plan_names) == 1 else None,
            "selectionMode": "explicit_report_plan",
            "verificationRequired": False,
            "source": "action_capability.adPlanFacts",
        }
    selector = {
        "scope": "store_product_bound_active_ad_plans",
        "storeId": identity.get("storeId"),
        "productId": identity.get("productId"),
        "productTitle": identity.get("productTitle"),
        "requiredStatus": "active",
        "allowedPlanIds": plan_ids,
        "allowedPlanNames": plan_names,
        "bindingRule": "select active ad plans verifiably bound to this store and product",
    }
    selector = {key: value for key, value in selector.items() if value not in (None, "", [])}
    return {
        "targetType": "ad_plan",
        "targetSelector": selector,
        "selectionMode": "report_plan_set" if plans else "store_product_selector",
        "verificationRequired": True,
        "verificationRule": "运营执行前在广告后台核对店铺、商品绑定和计划启用状态。",
        "source": "action_capability.canonical_selector",
    }


def _operation_target(execution: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in {
            "type": execution.get("targetType") or "ad_plan",
            "id": execution.get("targetId"),
            "name": execution.get("targetName"),
            "selector": execution.get("targetSelector"),
        }.items()
        if value not in (None, "", [], {})
    }


def enrich_roas_pack_with_execution_object(
    original: Any,
    package: Dict[str, Any],
    family: str = "roas_scale",
) -> Dict[str, Any]:
    pack = dict(original(package, family))
    execution = canonical_roas_execution_object({**package, "actionParameterPack": pack})
    pack["executionObject"] = execution
    pack["operationTarget"] = _operation_target(execution)
    contract = pack.get("executionObjectContract") if isinstance(pack.get("executionObjectContract"), dict) else {}
    pack["executionObjectContract"] = {
        **contract,
        "version": AGENT_RUNTIME_RECOVERY_VERSION,
        "mode": "explicit_plan" if execution.get("targetId") else "canonical_selector",
        "normalizedExecutionObjectProvided": True,
        "targetIdOrSelectorGuaranteed": True,
        "fabricatedPlanIdAllowed": False,
    }
    return pack


def apply_roas_execution_target(raw: Dict[str, Any], package: Dict[str, Any]) -> Dict[str, Any]:
    family = str(package.get("actionFamily") or raw.get("actionFamily") or "").strip()
    if family not in ROAS_FAMILIES:
        return dict(raw)
    result = copy.deepcopy(raw)
    pack = package.get("actionParameterPack") if isinstance(package.get("actionParameterPack"), dict) else {}
    execution = pack.get("executionObject") if isinstance(pack.get("executionObject"), dict) else canonical_roas_execution_object(package)
    current_execution = result.get("executionObject") if isinstance(result.get("executionObject"), dict) else {}
    if not current_execution.get("targetId") and not current_execution.get("targetSelector"):
        result["executionObject"] = {**execution, **current_execution}

    target = _operation_target(result.get("executionObject") if isinstance(result.get("executionObject"), dict) else execution)
    operation_plan = result.get("operationPlan") if isinstance(result.get("operationPlan"), dict) else {}
    raw_operations = operation_plan.get("operations") if isinstance(operation_plan.get("operations"), list) else result.get("operations")
    if isinstance(raw_operations, list):
        operations: List[Dict[str, Any]] = []
        for raw_operation in raw_operations:
            if not isinstance(raw_operation, dict):
                continue
            operation = copy.deepcopy(raw_operation)
            existing = operation.get("target") if isinstance(operation.get("target"), dict) else {}
            if not existing.get("id") and not existing.get("selector"):
                operation["target"] = {**target, **existing}
            operations.append(operation)
        operation_plan = dict(operation_plan)
        operation_plan["operations"] = operations
        result["operationPlan"] = operation_plan
        result.pop("operations", None)
    result["executionTargetProjection"] = {
        "version": AGENT_RUNTIME_RECOVERY_VERSION,
        "source": execution.get("source"),
        "filledExecutionObject": True,
        "filledOperationTargets": True,
        "fabricatedPlanId": False,
    }
    return result


def _target_only_error(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if ":" in text:
        text = text.split(":", 1)[1]
    fields = [part.strip() for part in text.split(",") if part.strip()]
    if not fields:
        return False
    return all(
        "target" in field
        and ("id_or_selector" in field or "targetId_or_targetSelector" in field)
        for field in fields
    )


def recover_target_only_agent2_failures(
    data_version: str | None = None,
    *,
    limit: int = 100,
) -> Dict[str, Any]:
    """Replay one historical target-only failure from capabilityRef for migration."""
    from src.services.artifact_transport_service import validate_artifact
    from src.services.pipeline_artifact_contract_service import artifact_refs_from_row

    ensure_agent1_runtime_columns()
    where = [
        "current_stage=?",
        "status='failed'",
        "action_family IN ('roas_scale','roas_guard')",
        "COALESCE(agent2_target_repair_count,0)<1",
    ]
    params: List[Any] = [AGENT2_OUTPUT_INVALID_STAGE]
    if data_version is not None:
        where.append("COALESCE(data_version,'')=COALESCE(?,'')")
        params.append(data_version)

    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM pipeline_items WHERE {' AND '.join(where)} "
            "ORDER BY updated_at ASC LIMIT ?",
            (*params, max(1, min(500, int(limit)))),
        ).fetchall()
        recovered = skipped = 0
        now = _now()
        for row in rows:
            error = _row_value(row, "last_error_code") or _row_value(row, "error_reason")
            if not _target_only_error(error):
                skipped += 1
                continue
            refs = artifact_refs_from_row(row)
            capability_ref = str(refs.get("capabilityRef") or "").strip()
            validation = validate_artifact(capability_ref) if capability_ref else {"ok": False}
            if not capability_ref or validation.get("ok") is not True:
                skipped += 1
                continue
            refs["currentStageRef"] = capability_ref
            cursor = conn.execute(
                """
                UPDATE pipeline_items
                SET current_stage=?, status='retry',
                    agent2_target_repair_count=COALESCE(agent2_target_repair_count,0)+1,
                    claim_id=NULL, lease_expires_at=NULL, retry_after=NULL,
                    failure_code=NULL, failure_class=NULL, error_reason=NULL,
                    last_error_code=?, artifact_refs_json=?, payload_artifact_ref=?,
                    payload=NULL, updated_at=?
                WHERE item_id=? AND current_stage=? AND status='failed'
                  AND COALESCE(agent2_target_repair_count,0)<1
                """,
                (
                    ACTION_PACK_READY_STAGE,
                    "agent2_missing_target_contract_requeued_once",
                    dumps(refs),
                    capability_ref,
                    now,
                    _row_value(row, "item_id"),
                    AGENT2_OUTPUT_INVALID_STAGE,
                ),
            )
            if cursor.rowcount == 1:
                recovered += 1
            else:
                skipped += 1
        conn.commit()

    return {
        "version": AGENT_RUNTIME_RECOVERY_VERSION,
        "dataVersion": data_version,
        "candidateCount": len(rows),
        "recoveredItemCount": recovered,
        "skippedItemCount": skipped,
        "replaySource": "artifactRefs.capabilityRef",
        "nextRuntimeSource": "artifactRefs.agent2InputRef",
        "maxAutomaticRepairsPerItem": 1,
    }


def runtime_recovery_helper_status() -> Dict[str, Any]:
    return {
        "version": AGENT_RUNTIME_RECOVERY_VERSION,
        "mode": "explicit_helpers_only",
        "runtimeBindingInstalled": False,
        "monkeyPatchAvailable": False,
        "activeRuntimeOwner": "agent_runtime_hard_interface_v230",
        "agent1Lease": True,
        "agent1RestartRecovery": True,
        "roasCanonicalExecutionTarget": True,
        "fallbackAllowed": False,
    }


__all__ = [
    "AGENT_RUNTIME_RECOVERY_VERSION",
    "AGENT1_PENDING_STAGE",
    "AGENT1_RUNNING_STAGE",
    "AGENT1_FAILED_STAGE",
    "ACTION_PACK_READY_STAGE",
    "AGENT2_OUTPUT_INVALID_STAGE",
    "ROAS_FAMILIES",
    "agent1_lease_seconds",
    "ensure_agent1_runtime_columns",
    "claim_agent1_items",
    "clear_agent1_runtime_control",
    "recover_stale_agent1_items",
    "canonical_roas_execution_object",
    "enrich_roas_pack_with_execution_object",
    "apply_roas_execution_target",
    "_target_only_error",
    "recover_target_only_agent2_failures",
    "runtime_recovery_helper_status",
]
