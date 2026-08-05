"""Artifact registration, deduplication, validation and resolution."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, Iterable

from src.repositories.artifact_repository import (
    find_artifact_by_hash,
    get_artifact,
    link_artifacts,
    upsert_artifact,
)
from src.runtime_version import VERSION
from src.services.artifact_storage_service import (
    canonical_json_bytes,
    content_hash,
    read_json,
    write_json,
)

ARTIFACT_REGISTRY_VERSION = "22.2.1"


def _artifact_id(artifact_type: str, digest: str, tenant_id: str | None) -> str:
    seed = f"{tenant_id or 'GLOBAL'}|{artifact_type}|{digest}"
    return "ART-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24].upper()


def _now() -> str:
    return datetime.now().isoformat()


def register_json_artifact(
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
    payload_bytes = canonical_json_bytes(value)
    digest = content_hash(payload_bytes)
    existing = find_artifact_by_hash(
        artifact_type,
        digest,
        tenant_id=tenant_id,
    )
    if existing:
        artifact_id = str(existing["artifact_id"])
        for parent in parent_refs:
            if parent and parent != artifact_id:
                link_artifacts(str(parent), artifact_id)
        return {
            **existing,
            "artifactId": artifact_id,
            "artifactType": existing.get("artifact_type"),
            "schemaVersion": existing.get("schema_version"),
            "contentHash": existing.get("content_hash"),
            "storageUri": existing.get("storage_uri"),
            "idempotentHit": True,
        }

    artifact_id = _artifact_id(artifact_type, digest, tenant_id)
    storage = write_json(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        value=value,
    )
    record = upsert_artifact(
        {
            "artifactId": artifact_id,
            "artifactType": artifact_type,
            "schemaVersion": schema_version or VERSION,
            "contentHash": storage["contentHash"],
            "storageUri": storage["storageUri"],
            "tenantId": tenant_id,
            "storeId": store_id,
            "productId": product_id,
            "dataVersion": data_version,
            "createdBy": created_by,
            "status": "valid",
            "immutable": True,
            "sizeBytes": storage["sizeBytes"],
            "metadata": metadata or {},
            "createdAt": _now(),
        }
    )
    for parent in parent_refs:
        if parent and parent != artifact_id:
            link_artifacts(str(parent), artifact_id)
    return {
        **record,
        "artifactId": artifact_id,
        "artifactType": artifact_type,
        "schemaVersion": schema_version or VERSION,
        "contentHash": storage["contentHash"],
        "storageUri": storage["storageUri"],
        "idempotentHit": False,
    }


def artifact_metadata(artifact_id: str) -> Dict[str, Any]:
    record = get_artifact(artifact_id)
    if not record:
        raise KeyError(f"artifact_not_found:{artifact_id}")
    return {
        **record,
        "artifactId": record.get("artifact_id"),
        "artifactType": record.get("artifact_type"),
        "schemaVersion": record.get("schema_version"),
        "contentHash": record.get("content_hash"),
        "storageUri": record.get("storage_uri"),
    }


def resolve_json_artifact(artifact_id: str) -> Any:
    record = artifact_metadata(artifact_id)
    value = read_json(str(record["storageUri"]))
    actual = content_hash(canonical_json_bytes(value))
    if actual != record.get("contentHash"):
        raise RuntimeError(f"artifact_hash_mismatch:{artifact_id}")
    return value


def validate_artifact(
    artifact_id: str,
    *,
    expected_type: str | None = None,
) -> Dict[str, Any]:
    try:
        record = artifact_metadata(artifact_id)
    except KeyError as exc:
        return {
            "ok": False,
            "status": "artifact_not_found",
            "artifactId": artifact_id,
            "error": str(exc),
        }
    if expected_type and record.get("artifactType") != expected_type:
        return {
            "ok": False,
            "status": "artifact_type_mismatch",
            "artifactId": artifact_id,
            "expectedType": expected_type,
            "actualType": record.get("artifactType"),
        }
    try:
        resolve_json_artifact(artifact_id)
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "status": "artifact_content_missing",
            "artifactId": artifact_id,
            "error": str(exc),
        }
    except RuntimeError as exc:
        return {
            "ok": False,
            "status": "artifact_hash_mismatch",
            "artifactId": artifact_id,
            "error": str(exc),
        }
    return {
        "ok": True,
        "status": "valid",
        "artifactId": artifact_id,
        "artifactType": record.get("artifactType"),
        "schemaVersion": record.get("schemaVersion"),
        "contentHash": record.get("contentHash"),
    }


__all__ = [
    "ARTIFACT_REGISTRY_VERSION",
    "register_json_artifact",
    "artifact_metadata",
    "resolve_json_artifact",
    "validate_artifact",
]
