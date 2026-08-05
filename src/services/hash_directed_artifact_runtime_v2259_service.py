"""V22.5.9 hash-directed Artifact execution runtime.

This module gives each projected Agent input one immutable identity, records one
execution decision per exact execution hash, materializes provider batch manifests,
and stores raw batch responses and per-item outputs as immutable Artifacts. Caches
are indexes only; they never rebind an old business result to a new input identity.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.artifact_storage_service import canonical_json_bytes, content_hash
from src.services.artifact_transport_service import (
    inspect_artifact,
    resolve_artifact,
    store_artifact,
    validate_artifact,
)

HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION = "22.5.9"
EXECUTION_LEASE_SECONDS = 600


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def hash_value(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now().isoformat()


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or ""))
    except Exception:
        return None


def ensure_hash_directed_runtime_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_execution_index_v2259 (
                execution_hash TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                item_execution_id TEXT NOT NULL,
                input_artifact_ref TEXT NOT NULL,
                input_content_hash TEXT NOT NULL,
                input_schema TEXT,
                projection_version TEXT,
                prompt_version TEXT,
                policy_hash TEXT,
                provider TEXT,
                model TEXT,
                generation_parameters_hash TEXT,
                status TEXT NOT NULL,
                claim_id TEXT,
                lease_expires_at TEXT,
                accepted_output_ref TEXT,
                accepted_output_hash TEXT,
                raw_batch_output_ref TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_batch_execution_v2259 (
                batch_execution_hash TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                batch_manifest_ref TEXT NOT NULL,
                batch_manifest_hash TEXT NOT NULL,
                expected_count INTEGER NOT NULL,
                returned_count INTEGER NOT NULL DEFAULT 0,
                accepted_count INTEGER NOT NULL DEFAULT 0,
                missing_json TEXT,
                extra_json TEXT,
                duplicate_json TEXT,
                raw_batch_output_ref TEXT,
                status TEXT NOT NULL,
                provider_request_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_input_v2259 ON artifact_execution_index_v2259(stage,input_artifact_ref,input_content_hash,status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_execution_output_v2259 ON artifact_execution_index_v2259(accepted_output_ref,status)"
        )
        conn.commit()


def resolve_input_binding(envelope: Dict[str, Any], *, expected_type: str) -> Dict[str, Any]:
    """Resolve the exact persisted input Artifact by its immutable full-content hash."""
    digest = content_hash(canonical_json_bytes(envelope))
    with connect() as conn:
        row = conn.execute(
            """
            SELECT artifact_id,content_hash,artifact_type,schema_version,tenant_id,
                   store_id,product_id,data_version,metadata_json
            FROM artifact_registry
            WHERE artifact_type=? AND content_hash=? AND status='valid'
            ORDER BY created_at DESC LIMIT 1
            """,
            (expected_type, digest),
        ).fetchone()
    if not row:
        raise RuntimeError(
            f"hash_directed_input_artifact_not_registered:{expected_type}:{digest}"
        )
    record = dict(row)
    artifact_id = str(record.get("artifact_id") or "")
    validation = validate_artifact(artifact_id, expected_type=expected_type)
    if validation.get("ok") is not True:
        raise RuntimeError(
            f"hash_directed_input_artifact_invalid:{artifact_id}:{validation.get('status')}"
        )
    return {
        "inputArtifactRef": artifact_id,
        "inputContentHash": str(record.get("content_hash") or digest),
        "inputArtifactType": str(record.get("artifact_type") or expected_type),
        "inputSchemaVersion": str(record.get("schema_version") or ""),
        "tenantId": record.get("tenant_id"),
        "storeId": record.get("store_id"),
        "productId": record.get("product_id"),
        "dataVersion": record.get("data_version"),
        "metadata": loads(record.get("metadata_json")) if record.get("metadata_json") else {},
    }


def build_execution_descriptor(
    *,
    stage: str,
    binding: Dict[str, Any],
    input_schema: str,
    projection_version: str,
    prompt_version: str,
    policy_hash: str,
    provider: str,
    model: str,
    generation_parameters: Dict[str, Any],
) -> Dict[str, Any]:
    generation_hash = hash_value(generation_parameters)
    execution_hash = hash_value(
        {
            "runtimeVersion": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
            "stage": stage,
            "inputArtifactRef": binding.get("inputArtifactRef"),
            "inputContentHash": binding.get("inputContentHash"),
            "inputSchema": input_schema,
            "projectionVersion": projection_version,
            "promptVersion": prompt_version,
            "policyHash": policy_hash,
            "provider": provider,
            "model": model,
            "generationParametersHash": generation_hash,
        }
    )
    return {
        "version": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "stage": stage,
        "itemExecutionId": "EXE-" + execution_hash[:24].upper(),
        "executionHash": execution_hash,
        "inputArtifactRef": binding.get("inputArtifactRef"),
        "inputContentHash": binding.get("inputContentHash"),
        "inputSchema": input_schema,
        "projectionVersion": projection_version,
        "promptVersion": prompt_version,
        "policyHash": policy_hash,
        "provider": provider,
        "model": model,
        "generationParameters": generation_parameters,
        "generationParametersHash": generation_hash,
        "tenantId": binding.get("tenantId"),
        "storeId": binding.get("storeId"),
        "productId": binding.get("productId"),
        "dataVersion": binding.get("dataVersion"),
    }


def accepted_execution(execution_hash: str) -> Dict[str, Any] | None:
    ensure_hash_directed_runtime_tables()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM artifact_execution_index_v2259
            WHERE execution_hash=? AND status='accepted'
            LIMIT 1
            """,
            (execution_hash,),
        ).fetchone()
    if not row:
        return None
    record = dict(row)
    artifact_id = str(record.get("accepted_output_ref") or "")
    if not artifact_id.startswith("ART-"):
        return None
    validation = validate_artifact(artifact_id)
    if validation.get("ok") is not True:
        return None
    value = resolve_artifact(artifact_id)
    return {
        "execution": record,
        "outputArtifactRef": artifact_id,
        "outputContentHash": record.get("accepted_output_hash"),
        "output": value,
    }


def claim_execution(descriptor: Dict[str, Any]) -> Dict[str, Any]:
    ensure_hash_directed_runtime_tables()
    execution_hash = str(descriptor["executionHash"])
    existing = accepted_execution(execution_hash)
    if existing:
        return {"status": "accepted_replay", **existing}
    claim_id = "CLAIM-" + uuid.uuid4().hex.upper()
    now = datetime.now()
    lease_expires = now + timedelta(seconds=EXECUTION_LEASE_SECONDS)
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status,claim_id,lease_expires_at,accepted_output_ref FROM artifact_execution_index_v2259 WHERE execution_hash=?",
            (execution_hash,),
        ).fetchone()
        if row:
            current = dict(row)
            if current.get("status") == "accepted" and current.get("accepted_output_ref"):
                conn.rollback()
                replay = accepted_execution(execution_hash)
                return {"status": "accepted_replay", **(replay or {})}
            lease = _parse_time(current.get("lease_expires_at"))
            if current.get("status") == "running" and lease and lease > now:
                conn.rollback()
                return {
                    "status": "already_running",
                    "executionHash": execution_hash,
                    "claimId": current.get("claim_id"),
                    "leaseExpiresAt": current.get("lease_expires_at"),
                }
        conn.execute(
            """
            INSERT INTO artifact_execution_index_v2259 (
                execution_hash,stage,item_execution_id,input_artifact_ref,input_content_hash,
                input_schema,projection_version,prompt_version,policy_hash,provider,model,
                generation_parameters_hash,status,claim_id,lease_expires_at,attempt_count,
                metadata_json,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(execution_hash) DO UPDATE SET
                status='running',claim_id=excluded.claim_id,
                lease_expires_at=excluded.lease_expires_at,
                attempt_count=artifact_execution_index_v2259.attempt_count+1,
                last_error=NULL,updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                execution_hash,
                descriptor.get("stage"),
                descriptor.get("itemExecutionId"),
                descriptor.get("inputArtifactRef"),
                descriptor.get("inputContentHash"),
                descriptor.get("inputSchema"),
                descriptor.get("projectionVersion"),
                descriptor.get("promptVersion"),
                descriptor.get("policyHash"),
                descriptor.get("provider"),
                descriptor.get("model"),
                descriptor.get("generationParametersHash"),
                "running",
                claim_id,
                lease_expires.isoformat(),
                1,
                dumps(descriptor),
                now.isoformat(),
                now.isoformat(),
            ),
        )
        conn.commit()
    return {
        "status": "claimed",
        "executionHash": execution_hash,
        "itemExecutionId": descriptor.get("itemExecutionId"),
        "claimId": claim_id,
        "leaseExpiresAt": lease_expires.isoformat(),
    }


def complete_execution(
    descriptor: Dict[str, Any],
    *,
    claim_id: str,
    output_artifact_ref: str,
    output_content_hash: str,
    raw_batch_output_ref: str | None = None,
) -> Dict[str, Any]:
    ensure_hash_directed_runtime_tables()
    execution_hash = str(descriptor["executionHash"])
    validation = validate_artifact(output_artifact_ref)
    if validation.get("ok") is not True:
        raise RuntimeError(
            f"hash_directed_output_artifact_invalid:{output_artifact_ref}:{validation.get('status')}"
        )
    with connect() as conn:
        conn.execute(
            """
            UPDATE artifact_execution_index_v2259
            SET status='accepted',accepted_output_ref=?,accepted_output_hash=?,
                raw_batch_output_ref=?,lease_expires_at=NULL,last_error=NULL,updated_at=?
            WHERE execution_hash=? AND claim_id=? AND status='running'
            """,
            (
                output_artifact_ref,
                output_content_hash,
                raw_batch_output_ref,
                now_iso(),
                execution_hash,
                claim_id,
            ),
        )
        changed = int(conn.execute("SELECT changes() AS n").fetchone()["n"] or 0)
        conn.commit()
    if changed != 1:
        replay = accepted_execution(execution_hash)
        if replay:
            return {"status": "accepted_replay", **replay}
        raise RuntimeError(f"hash_directed_execution_claim_lost:{execution_hash}")
    return {
        "status": "accepted",
        "executionHash": execution_hash,
        "itemExecutionId": descriptor.get("itemExecutionId"),
        "outputArtifactRef": output_artifact_ref,
        "outputContentHash": output_content_hash,
    }


def fail_execution(descriptor: Dict[str, Any], *, claim_id: str, error: str) -> None:
    ensure_hash_directed_runtime_tables()
    with connect() as conn:
        conn.execute(
            """
            UPDATE artifact_execution_index_v2259
            SET status='failed',last_error=?,lease_expires_at=NULL,updated_at=?
            WHERE execution_hash=? AND claim_id=?
            """,
            (str(error)[:1000], now_iso(), descriptor.get("executionHash"), claim_id),
        )
        conn.commit()


def create_batch_manifest(
    *,
    stage: str,
    descriptors: List[Dict[str, Any]],
    data_version: str | None,
    prompt_version: str,
    provider: str,
    model: str,
) -> Dict[str, Any]:
    ordered = []
    for slot, descriptor in enumerate(descriptors, start=1):
        ordered.append(
            {
                "slot": slot,
                "itemExecutionId": descriptor.get("itemExecutionId"),
                "executionHash": descriptor.get("executionHash"),
                "inputArtifactRef": descriptor.get("inputArtifactRef"),
                "inputContentHash": descriptor.get("inputContentHash"),
                "productId": descriptor.get("productId"),
                "storeId": descriptor.get("storeId"),
                "dataVersion": descriptor.get("dataVersion") or data_version,
            }
        )
    batch_execution_hash = hash_value(
        {
            "runtimeVersion": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
            "stage": stage,
            "orderedExecutionHashes": [item["executionHash"] for item in ordered],
            "promptVersion": prompt_version,
            "provider": provider,
            "model": model,
        }
    )
    manifest = {
        "schema": "agent_batch_manifest.v2259",
        "version": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "batchExecutionId": "BATCH-" + batch_execution_hash[:24].upper(),
        "batchExecutionHash": batch_execution_hash,
        "stage": stage,
        "dataVersion": data_version,
        "expectedItemCount": len(ordered),
        "items": ordered,
        "promptVersion": prompt_version,
        "provider": provider,
        "model": model,
        "matchingContract": "itemExecutionId_then_inputContentHash",
        "fallbackIdentityMatchingAllowed": False,
    }
    artifact = store_artifact(
        artifact_type="agent_batch_manifest.v2259",
        value=manifest,
        schema_version=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        data_version=data_version,
        created_by="hash_directed_artifact_runtime_v2259",
        parent_refs=[str(item.get("inputArtifactRef")) for item in ordered],
        metadata={
            "stage": stage,
            "batchExecutionHash": batch_execution_hash,
            "expectedItemCount": len(ordered),
        },
    )
    ensure_hash_directed_runtime_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_batch_execution_v2259 (
                batch_execution_hash,stage,batch_manifest_ref,batch_manifest_hash,
                expected_count,status,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(batch_execution_hash) DO UPDATE SET
                batch_manifest_ref=excluded.batch_manifest_ref,
                batch_manifest_hash=excluded.batch_manifest_hash,
                expected_count=excluded.expected_count,
                updated_at=excluded.updated_at
            """,
            (
                batch_execution_hash,
                stage,
                artifact["artifactId"],
                artifact["contentHash"],
                len(ordered),
                "prepared",
                now_iso(),
                now_iso(),
            ),
        )
        conn.commit()
    return {
        "manifest": manifest,
        "batchManifestRef": artifact["artifactId"],
        "batchManifestHash": artifact["contentHash"],
    }


def store_raw_batch_output(
    *,
    batch: Dict[str, Any],
    provider_payload: Dict[str, Any],
    provider_usage: Dict[str, Any],
    data_version: str | None,
) -> Dict[str, Any]:
    manifest = batch["manifest"]
    value = {
        "schema": "agent_raw_batch_output.v2259",
        "version": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "batchExecutionId": manifest.get("batchExecutionId"),
        "batchExecutionHash": manifest.get("batchExecutionHash"),
        "stage": manifest.get("stage"),
        "dataVersion": data_version,
        "providerPayload": provider_payload,
        "providerUsage": provider_usage,
    }
    artifact = store_artifact(
        artifact_type="agent_raw_batch_output.v2259",
        value=value,
        schema_version=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        data_version=data_version,
        created_by="hash_directed_artifact_runtime_v2259",
        parent_refs=[batch["batchManifestRef"]],
        metadata={
            "stage": manifest.get("stage"),
            "batchExecutionHash": manifest.get("batchExecutionHash"),
            "providerRequestId": provider_usage.get("providerRequestId"),
        },
    )
    with connect() as conn:
        conn.execute(
            """
            UPDATE agent_batch_execution_v2259
            SET raw_batch_output_ref=?,status='provider_returned',provider_request_id=?,updated_at=?
            WHERE batch_execution_hash=?
            """,
            (
                artifact["artifactId"],
                provider_usage.get("providerRequestId"),
                now_iso(),
                manifest.get("batchExecutionHash"),
            ),
        )
        conn.commit()
    return artifact


def store_item_output(
    *,
    descriptor: Dict[str, Any],
    output: Dict[str, Any],
    raw_batch_output_ref: str | None,
    artifact_type: str,
) -> Dict[str, Any]:
    value = {
        "schema": artifact_type,
        "version": HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        "itemExecutionId": descriptor.get("itemExecutionId"),
        "executionHash": descriptor.get("executionHash"),
        "inputArtifactRef": descriptor.get("inputArtifactRef"),
        "inputContentHash": descriptor.get("inputContentHash"),
        "rawBatchOutputRef": raw_batch_output_ref,
        "stage": descriptor.get("stage"),
        "dataVersion": descriptor.get("dataVersion"),
        "output": output,
    }
    parents = [str(descriptor.get("inputArtifactRef"))]
    if raw_batch_output_ref:
        parents.append(raw_batch_output_ref)
    return store_artifact(
        artifact_type=artifact_type,
        value=value,
        schema_version=HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION,
        tenant_id=descriptor.get("tenantId"),
        store_id=descriptor.get("storeId"),
        product_id=descriptor.get("productId"),
        data_version=descriptor.get("dataVersion"),
        created_by="hash_directed_artifact_runtime_v2259",
        parent_refs=parents,
        metadata={
            "stage": descriptor.get("stage"),
            "itemExecutionId": descriptor.get("itemExecutionId"),
            "executionHash": descriptor.get("executionHash"),
            "inputContentHash": descriptor.get("inputContentHash"),
        },
    )


def finalize_batch(
    *,
    batch: Dict[str, Any],
    returned_item_execution_ids: Iterable[str],
    accepted_item_execution_ids: Iterable[str],
    raw_batch_output_ref: str | None,
) -> Dict[str, Any]:
    manifest = batch["manifest"]
    expected = [str(item.get("itemExecutionId")) for item in manifest.get("items") or []]
    returned = [str(value) for value in returned_item_execution_ids if str(value)]
    accepted = [str(value) for value in accepted_item_execution_ids if str(value)]
    expected_set = set(expected)
    returned_set = set(returned)
    missing = sorted(expected_set - returned_set)
    extra = sorted(returned_set - expected_set)
    duplicate = sorted({value for value in returned if returned.count(value) > 1})
    status = (
        "completed"
        if not missing and not extra and not duplicate and len(accepted) == len(expected)
        else "partial_completed"
        if accepted
        else "failed"
    )
    with connect() as conn:
        conn.execute(
            """
            UPDATE agent_batch_execution_v2259
            SET returned_count=?,accepted_count=?,missing_json=?,extra_json=?,duplicate_json=?,
                raw_batch_output_ref=COALESCE(?,raw_batch_output_ref),status=?,updated_at=?
            WHERE batch_execution_hash=?
            """,
            (
                len(returned),
                len(set(accepted)),
                dumps(missing),
                dumps(extra),
                dumps(duplicate),
                raw_batch_output_ref,
                status,
                now_iso(),
                manifest.get("batchExecutionHash"),
            ),
        )
        conn.commit()
    return {
        "batchExecutionId": manifest.get("batchExecutionId"),
        "batchExecutionHash": manifest.get("batchExecutionHash"),
        "status": status,
        "expectedCount": len(expected),
        "returnedCount": len(returned),
        "acceptedCount": len(set(accepted)),
        "missingItemExecutionIds": missing,
        "extraItemExecutionIds": extra,
        "duplicateItemExecutionIds": duplicate,
        "rawBatchOutputRef": raw_batch_output_ref,
    }


__all__ = [
    "HASH_DIRECTED_ARTIFACT_RUNTIME_VERSION",
    "stable_json",
    "hash_value",
    "ensure_hash_directed_runtime_tables",
    "resolve_input_binding",
    "build_execution_descriptor",
    "accepted_execution",
    "claim_execution",
    "complete_execution",
    "fail_execution",
    "create_batch_manifest",
    "store_raw_batch_output",
    "store_item_output",
    "finalize_batch",
]
