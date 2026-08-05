"""Bind the existing V22 workers to the V22.2.4 reference-only contract.

The scheduler entry points remain unchanged, but every Agent-stage read resolves an
immutable Artifact Hub reference. Runtime payload fallback and Signal Pool fallback
are retired. Migration must complete before the worker is started.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.pipeline_artifact_contract_service import (
    PIPELINE_ARTIFACT_CONTRACT_VERSION,
    PipelineArtifactContractError,
    artifact_refs_from_row,
    resolve_pipeline_row,
)
from src.services.artifact_transport_service import resolve_artifact, validate_artifact

PIPELINE_REFERENCE_RUNTIME_VERSION = "22.2.4"
_BOUND = False


def _row_get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except Exception:
        return row.get(key) if isinstance(row, dict) else None


def artifact_only_payload_from_row(row: Any) -> Dict[str, Any]:
    """Resolve the current artifact and return a fail-closed package on transport error."""
    try:
        resolved = resolve_pipeline_row(row, allow_legacy_payload=False)
    except PipelineArtifactContractError as exc:
        return {
            "version": PIPELINE_REFERENCE_RUNTIME_VERSION,
            "contractVersion": PIPELINE_ARTIFACT_CONTRACT_VERSION,
            "dataVersion": _row_get(row, "data_version"),
            "itemId": _row_get(row, "item_id"),
            "productId": _row_get(row, "product_id"),
            "storeId": _row_get(row, "store_id"),
            "signalId": _row_get(row, "signal_id"),
            "packageId": _row_get(row, "package_id"),
            "reason": "artifact_input_contract_invalid",
            "missing": [exc.code],
            "artifactInputError": exc.as_dict(),
            "failureOwner": "artifact_transport",
            "frontendFailureLabel": "制品输入无效",
            "taskAdmissionAllowed": False,
            "fallbackAllowed": False,
        }
    payload = dict(resolved.payload)
    payload["artifactInputContract"] = {
        "version": PIPELINE_ARTIFACT_CONTRACT_VERSION,
        "source": resolved.source,
        "artifactId": resolved.artifact_id,
        "legacyPayloadUsed": False,
        "fallbackAllowed": False,
    }
    payload["artifactRefs"] = resolved.artifact_refs
    return payload


artifact_first_payload_from_row = artifact_only_payload_from_row


def _artifact_signals(
    data_version: str | None = None,
    limit: int = 160,
    **_: Any,
) -> Dict[str, Any]:
    """Present Agent1 only with validated ``signalRef`` artifacts."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM pipeline_items
            WHERE COALESCE(data_version,'')=COALESCE(?,'')
              AND current_stage IN ('signal_admitted','agent1_pending','agent1_running')
            ORDER BY updated_at DESC LIMIT ?
            """,
            (data_version, max(1, min(2000, int(limit or 160) * 4))),
        ).fetchall()
    signals: List[Dict[str, Any]] = []
    seen: set[str] = set()
    missing_ref_count = 0
    invalid_ref_count = 0
    for row in rows:
        refs = artifact_refs_from_row(row)
        signal_ref = str(refs.get("signalRef") or "").strip()
        if not signal_ref:
            missing_ref_count += 1
            continue
        validation = validate_artifact(signal_ref)
        if validation.get("ok") is not True:
            invalid_ref_count += 1
            continue
        try:
            signal = resolve_artifact(signal_ref)
        except Exception:
            invalid_ref_count += 1
            continue
        if not isinstance(signal, dict) or not signal:
            invalid_ref_count += 1
            continue
        signal_id = str(
            signal.get("signalId")
            or signal.get("signal_id")
            or _row_get(row, "signal_id")
            or ""
        )
        marker = signal_id or signal_ref
        if marker in seen:
            continue
        seen.add(marker)
        signals.append(signal)
        if len(signals) >= limit:
            break
    return {
        "version": PIPELINE_REFERENCE_RUNTIME_VERSION,
        "dataVersion": data_version,
        "count": len(signals),
        "signals": signals,
        "runtimeSource": "artifactRefs.signalRef",
        "legacyMigrationFallbackUsed": False,
        "legacySignalPoolFallbackAllowed": False,
        "missingSignalRefCount": missing_ref_count,
        "invalidArtifactSignalCount": invalid_ref_count,
        "invalidExistingRefFallbackAllowed": False,
    }


def _binding_summary(*, idempotent: bool = False) -> Dict[str, Any]:
    return {
        "version": PIPELINE_REFERENCE_RUNTIME_VERSION,
        "contractVersion": PIPELINE_ARTIFACT_CONTRACT_VERSION,
        "bound": True,
        "idempotent": idempotent,
        "runtimeSource": "pipeline_items.artifact_refs_json",
        "patchedEntrypoints": [
            "Agent1.signalRef",
            "ActionCapability.agent1Ref",
            "Agent2.capabilityRef",
            "SOP.agent2Ref",
            "TaskPool.sopRef",
            "PipelineStatus.referenceOnly",
        ],
        "pipelinePayloadWriteMode": "artifact_ref_only",
        "legacyPayloadRole": "retired",
        "legacyPayloadRuntimeFallbackAllowed": False,
        "legacySignalPoolFallbackAllowed": False,
        "invalidExistingRefFallbackAllowed": False,
    }


def bind_pipeline_reference_runtime() -> Dict[str, Any]:
    global _BOUND
    if _BOUND:
        return _binding_summary(idempotent=True)

    from src.services import agent_runtime_contract_v2010_service as contract_core
    from src.services import agent_runtime_contract_v2141_service as contract_plan
    from src.services import agent_pipeline_item_worker_v2010_service as capability_worker
    from src.services import pipeline_action_microbatch_v205_service as agent2_worker
    from src.services import pipeline_agent1_microbatch_v20101_service as agent1_worker
    from src.services import pipeline_sop_task_pool_v2010_service as sop_worker

    original_status = capability_worker.agent_pipeline_status

    def reference_pipeline_status(data_version: str | None = None) -> Dict[str, Any]:
        from src.services.pipeline_payload_retirement_service import payload_retirement_status

        status = dict(original_status(data_version))
        retirement = payload_retirement_status()
        status.update(
            version=PIPELINE_REFERENCE_RUNTIME_VERSION,
            contractVersion=PIPELINE_ARTIFACT_CONTRACT_VERSION,
            runtimeSource="pipeline_items.artifact_refs_json",
            artifactInputMode="reference_only",
            pipelinePayloadWriteMode="artifact_ref_only",
            legacyPayloadRole="retired",
            legacyPayloadRuntimeFallbackAllowed=False,
            legacySignalPoolFallbackAllowed=False,
            invalidExistingRefFallbackAllowed=False,
            payloadRetirement=retirement,
        )
        return status

    contract_core.payload_from_row = artifact_only_payload_from_row
    contract_plan.payload_from_row = artifact_only_payload_from_row
    capability_worker.payload_from_row = artifact_only_payload_from_row
    agent2_worker.payload_from_row = artifact_only_payload_from_row
    sop_worker.payload_from_row = artifact_only_payload_from_row
    agent1_worker.list_signals = _artifact_signals
    capability_worker.agent_pipeline_status = reference_pipeline_status

    capability_worker.AGENT_PIPELINE_ITEM_WORKER_VERSION = PIPELINE_REFERENCE_RUNTIME_VERSION
    agent2_worker.PIPELINE_ACTION_MICROBATCH_VERSION = PIPELINE_REFERENCE_RUNTIME_VERSION
    agent1_worker.PIPELINE_AGENT1_MICROBATCH_VERSION = PIPELINE_REFERENCE_RUNTIME_VERSION
    sop_worker.PIPELINE_SOP_TASK_POOL_VERSION = PIPELINE_REFERENCE_RUNTIME_VERSION

    _BOUND = True
    return _binding_summary(idempotent=False)


__all__ = [
    "PIPELINE_REFERENCE_RUNTIME_VERSION",
    "artifact_only_payload_from_row",
    "artifact_first_payload_from_row",
    "bind_pipeline_reference_runtime",
]
