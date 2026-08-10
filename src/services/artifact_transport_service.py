"""Unified code-only artifact transport facade.

Business services store and resolve immutable artifacts through this module. Agent
workers exchange references; they do not need to know whether content lives on the
local filesystem, OSS, S3 or MinIO. V22.3 adds explicit model-facing input refs;
full signal and capability artifacts remain outside Agent execution.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.runtime_version import VERSION
from src.services.artifact_registry_service import (
    artifact_metadata,
    register_json_artifact,
    resolve_json_artifact,
    validate_artifact,
)

ARTIFACT_TRANSPORT_VERSION = "22.3.1"

# These keys describe business-stage outputs. Agent input refs are created only by
# agent_input_transport services and are never aliases for full stage payloads.
_STAGE_REF_KEYS = {
    "data_received": "reportRef",
    "schema_ready": "schemaRef",
    "fact_ready": "factRef",
    "product_master_ready": "productMasterRef",
    "metric_snapshot_ready": "productSnapshotRef",
    "context_bundle_ready": "signalSnapshotRef",
    "quality_gate_ready": "validatedSignalSnapshotRef",
    "signal_admitted": "signalRef",
    "agent1_input_ready": "agent1InputRef",
    "agent1_pending": "admissionRef",
    "agent1_running": "agent1RuntimeReceiptRef",
    "observed_soft_gate": "observationRef",
    "agent1_completed": "agent1Ref",
    "agent1_output_invalid": "agent1InvalidRef",
    "agent1_failed": "agent1FailureRef",
    "action_pack_ready": "capabilityRef",
    "agent2_input_ready": "agent2InputRef",
    "agent2_running": "agent2RuntimeReceiptRef",
    "action_pack_invalid": "capabilityFailureRef",
    "agent2_completed": "agent2Ref",
    "agent2_output_invalid": "agent2FailureRef",
    "agent2_failed": "agent2FailureRef",
    "agent2_dead_letter": "agent2FailureRef",
    "agent2_draft_ready": "agent2DraftRef",
    "agent2_draft_output_invalid": "agent2DraftFailureRef",
    "agent2_draft_failed": "agent2DraftFailureRef",
    "agent3_sop_running": "agent3SopRuntimeReceiptRef",
    "agent3_sop_ready": "agent3SopRef",
    "agent3_sop_output_invalid": "agent3SopFailureRef",
    "agent3_sop_failed": "agent3SopFailureRef",
    "sop_mapped": "sopRef",
    "task_mapped": "taskMappingRef",
    "task_admitted": "taskRef",
    "read_model_ready": "readModelRef",
    "task_loop_ready": "acceptanceRef",
}


def artifact_type_for_stage(stage: str | None) -> str:
    value = str(stage or "unknown_stage").strip().lower()
    return f"pipeline_stage.{value}"


def _artifact_ids(value: Any) -> List[str]:
    result: List[str] = []
    if isinstance(value, str) and value.startswith("ART-"):
        result.append(value)
    elif isinstance(value, list):
        for item in value:
            result.extend(_artifact_ids(item))
    elif isinstance(value, dict):
        for item in value.values():
            result.extend(_artifact_ids(item))
    return list(dict.fromkeys(result))


def merge_artifact_refs(*values: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if item in (None, "", [], {}):
                continue
            result[str(key)] = item
    return result


def store_artifact(
    *,
    artifact_type: str,
    value: Any,
    schema_version: str | None = None,
    tenant_id: str | None = None,
    store_id: str | None = None,
    product_id: str | None = None,
    data_version: str | None = None,
    created_by: str | None = None,
    parent_refs: Iterable[str] = (),
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return register_json_artifact(
        artifact_type=artifact_type,
        value=value,
        schema_version=schema_version or VERSION,
        tenant_id=tenant_id,
        store_id=store_id,
        product_id=product_id,
        data_version=data_version,
        created_by=created_by,
        parent_refs=parent_refs,
        metadata=metadata,
    )


def resolve_artifact(artifact_id: str) -> Any:
    return resolve_json_artifact(artifact_id)


def inspect_artifact(artifact_id: str) -> Dict[str, Any]:
    return artifact_metadata(artifact_id)


def pipeline_payload_artifact(
    *,
    envelope: Dict[str, Any],
    stage: str,
    payload: Dict[str, Any],
    station_id: str | None = None,
    previous_artifact_refs: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    inherited = merge_artifact_refs(
        previous_artifact_refs,
        payload.get("artifactRefs") if isinstance(payload.get("artifactRefs"), dict) else {},
        envelope.get("artifactRefs") if isinstance(envelope.get("artifactRefs"), dict) else {},
    )
    parents = _artifact_ids(inherited)
    artifact = store_artifact(
        artifact_type=artifact_type_for_stage(stage),
        value=payload,
        tenant_id=(payload.get("tenantId") or payload.get("tenant_id")),
        store_id=envelope.get("storeId") or payload.get("storeId"),
        product_id=envelope.get("productId") or payload.get("productId"),
        data_version=envelope.get("dataVersion") or payload.get("dataVersion"),
        created_by=station_id or stage,
        parent_refs=parents,
        metadata={
            "pipelineItemId": envelope.get("itemId"),
            "stage": stage,
            "stationId": station_id,
            "outputRef": envelope.get("outputRef"),
            "transportMode": "reference_only_hard_agent_inputs",
        },
    )
    artifact_id = str(artifact["artifactId"])
    ref_key = _STAGE_REF_KEYS.get(stage, "stagePayloadRef")
    refs = merge_artifact_refs(
        inherited,
        {
            ref_key: artifact_id,
            "currentStageRef": artifact_id,
        },
    )
    return {
        "version": ARTIFACT_TRANSPORT_VERSION,
        "payloadArtifactRef": artifact_id,
        "artifactRefs": refs,
        "contentHash": artifact.get("contentHash"),
        "storageUri": artifact.get("storageUri"),
        "idempotentHit": bool(artifact.get("idempotentHit")),
    }


__all__ = [
    "ARTIFACT_TRANSPORT_VERSION",
    "artifact_type_for_stage",
    "merge_artifact_refs",
    "store_artifact",
    "resolve_artifact",
    "inspect_artifact",
    "validate_artifact",
    "pipeline_payload_artifact",
]
