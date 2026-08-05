"""Operations-only metadata endpoints for Artifact Hub diagnostics.

These endpoints never return raw artifact content. They expose hashes, references,
lineage and exact failure ownership so transport faults are not blamed on Agents.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from src.repositories.artifact_repository import list_artifacts
from src.repositories.sqlite_repository import connect, loads
from src.services.artifact_lineage_service import lineage_graph
from src.services.artifact_registry_service import artifact_metadata, validate_artifact
from src.services.pipeline_payload_retirement_service import payload_retirement_status

router = APIRouter(prefix="/api/ops/artifacts", tags=["ops-artifacts"])


def _public(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "artifactId": record.get("artifact_id") or record.get("artifactId"),
        "artifactType": record.get("artifact_type") or record.get("artifactType"),
        "schemaVersion": record.get("schema_version") or record.get("schemaVersion"),
        "contentHash": record.get("content_hash") or record.get("contentHash"),
        "storageUri": record.get("storage_uri") or record.get("storageUri"),
        "dataVersion": record.get("data_version") or record.get("dataVersion"),
        "tenantId": record.get("tenant_id") or record.get("tenantId"),
        "storeId": record.get("store_id") or record.get("storeId"),
        "productId": record.get("product_id") or record.get("productId"),
        "createdBy": record.get("created_by") or record.get("createdBy"),
        "status": record.get("status"),
        "immutable": bool(record.get("immutable")),
        "sizeBytes": record.get("size_bytes") or record.get("sizeBytes") or 0,
        "metadata": record.get("metadata") or {},
        "createdAt": record.get("created_at") or record.get("createdAt"),
        "updatedAt": record.get("updated_at") or record.get("updatedAt"),
    }


@router.get("")
def artifacts(
    data_version: str | None = Query(default=None, alias="dataVersion"),
    artifact_type: str | None = Query(default=None, alias="artifactType"),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    records = list_artifacts(
        data_version=data_version,
        artifact_type=artifact_type,
        limit=limit,
    )
    return {
        "version": "22.2.4",
        "count": len(records),
        "artifacts": [_public(record) for record in records],
        "contentReturned": False,
    }


@router.get("/retirement-status")
def artifact_payload_retirement_status() -> Dict[str, Any]:
    return {
        "version": "22.2.4",
        "pipelinePayloadRetirement": payload_retirement_status(),
        "runtimePayloadSource": "artifact_refs_only",
        "rawPayloadReturned": False,
    }


@router.get("/pipeline-items/{item_id}/lineage")
def pipeline_item_lineage(item_id: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT item_id, data_version, product_id, store_id, current_stage,
                   status, artifact_refs_json, payload_artifact_ref,
                   last_error_code, last_error_artifact_ref, updated_at
            FROM pipeline_items WHERE item_id=?
            """,
            (item_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"pipeline_item_not_found:{item_id}")
    refs = loads(row["artifact_refs_json"]) if row["artifact_refs_json"] else {}
    root = row["payload_artifact_ref"] or refs.get("currentStageRef")
    return {
        "version": "22.2.4",
        "pipelineItem": {
            "itemId": row["item_id"],
            "dataVersion": row["data_version"],
            "productId": row["product_id"],
            "storeId": row["store_id"],
            "currentStage": row["current_stage"],
            "status": row["status"],
            "artifactRefs": refs,
            "payloadArtifactRef": row["payload_artifact_ref"],
            "lastErrorCode": row["last_error_code"],
            "lastErrorArtifactRef": row["last_error_artifact_ref"],
            "updatedAt": row["updated_at"],
        },
        "lineage": lineage_graph(str(root), max_depth=8) if root else None,
        "failureOwner": (
            "artifact_transport"
            if row["last_error_code"] and row["last_error_artifact_ref"]
            else None
        ),
        "contentReturned": False,
    }


@router.get("/{artifact_id}")
def artifact_detail(artifact_id: str) -> Dict[str, Any]:
    try:
        record = artifact_metadata(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "version": "22.2.4",
        "artifact": _public(record),
        "validation": validate_artifact(artifact_id),
        "contentReturned": False,
    }


@router.get("/{artifact_id}/lineage")
def artifact_lineage(
    artifact_id: str,
    max_depth: int = Query(default=5, ge=1, le=12, alias="maxDepth"),
) -> Dict[str, Any]:
    try:
        artifact_metadata(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return lineage_graph(artifact_id, max_depth=max_depth)
