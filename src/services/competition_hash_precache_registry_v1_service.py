"""Unified layered semantic-hash precache for the competition deterministic chain.

This module does not replace any strict Hash Lineage identity. Existing
productSnapshotHash/setSnapshotHash/evidenceInputHash/Artifact content hashes and Agent
ExecutionHash values remain the current-run authority. The additional semantic hashes
exist only to answer one question: has the same business computation already been
performed under the same registered compute contract?

A semantic hit reuses only an immutable business body. Callers must rebind the current
``dataVersion`` and create current strict Artifacts/Execution identities before the
result is allowed downstream.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, dumps, loads
from src.services.artifact_transport_service import resolve_artifact, store_artifact, validate_artifact

HASH_PRECACHE_VERSION = "1.0.0"
HASH_PRECACHE_SCHEMA = "competition.hash_precache.index.v1"
HASH_PRECACHE_TABLE = "competition_hash_precache_v1"
HASH_PRECACHE_ARTIFACT_TYPE = "competition.pre_agent_semantic_cache"

# Execution/transport/strict-lineage identities are deliberately excluded. The semantic
# key is a sibling of strict Hash Lineage, never a replacement for it.
SEMANTIC_EXCLUDED_KEYS = {
    "dataVersion",
    "data_version",
    "sourceDataVersion",
    "sourceDataVersions",
    "snapshotId",
    "signalSnapshotId",
    "productSnapshotId",
    "previousSnapshotId",
    "signalId",
    "signal_id",
    "packageId",
    "package_id",
    "bundleId",
    "correlationId",
    "itemExecutionId",
    "runtimeStateHash",
    "executionHash",
    "ExecutionHash",
    "inputContentHash",
    "contentHash",
    "sourceContentHash",
    "sourceContentHashes",
    "productSnapshotHash",
    "snapshotHash",
    "parentSnapshotHash",
    "setSnapshotHash",
    "projectionHash",
    "observationHash",
    "factHash",
    "artifactRefs",
    "sourceArtifactRefs",
    "sourceReportRef",
    "sourceReportRefs",
    "sourceArtifactRef",
    "permissionStampId",
    "permissionScopeRef",
    "factRefs",
    "factHashRefs",
    "metricLineage",
    "createdAt",
    "updatedAt",
    "created_at",
    "updated_at",
    "historyEpochId",
    "historyEpochStartedAt",
    "currentProductSetHash",
    "currentObservationHash",
    "previousProductSetHashes",
    "previousObservationHashes",
    "evidenceInputHash",
    "outputRef",
    "productSignalSnapshotRef",
}


def _now() -> str:
    return datetime.now().isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def stable_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def semantic_projection(value: Any) -> Any:
    """Remove execution-only identities while preserving business semantics."""
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if name in SEMANTIC_EXCLUDED_KEYS:
                continue
            projected = semantic_projection(child)
            if projected in (None, "", [], {}):
                if child not in (0, False):
                    continue
            result[name] = projected
        return result
    if isinstance(value, list):
        return [semantic_projection(child) for child in value]
    return value


def semantic_hash(value: Any, *, namespace: str, contract: Dict[str, Any] | None = None) -> str:
    material = {
        "namespace": namespace,
        "registryVersion": HASH_PRECACHE_VERSION,
        "contract": semantic_projection(contract or {}),
        "business": semantic_projection(value),
    }
    return stable_sha256(material)


def _product_sort_key(item: Dict[str, Any]) -> tuple[str, str, str, str]:
    profile = item.get("profileSnapshot") if isinstance(item.get("profileSnapshot"), dict) else {}
    return (
        str(item.get("storeId") or profile.get("storeId") or ""),
        str(item.get("productId") or profile.get("productId") or ""),
        str(item.get("objectId") or profile.get("objectId") or ""),
        str(profile.get("skuId") or ""),
    )


def canonical_business_projection(snapshot: Dict[str, Any] | None) -> Dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    products = [semantic_projection(item) for item in snapshot.get("products") or [] if isinstance(item, dict)]
    products.sort(key=_product_sort_key)
    return {
        "schema": "competition.canonical_business_projection.v1",
        "productCount": len(products),
        "products": products,
    }


def canonical_semantic_hash(snapshot: Dict[str, Any] | None, *, contract: Dict[str, Any] | None = None) -> str:
    return semantic_hash(
        canonical_business_projection(snapshot),
        namespace="canonical_semantic_hash",
        contract=contract,
    )


def input_sequence_hash(
    current: Dict[str, Any],
    history: Iterable[Dict[str, Any]],
    *,
    contract: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    history_items = [item for item in history if isinstance(item, dict)]
    current_hash = canonical_semantic_hash(current, contract=contract)
    history_hashes = [canonical_semantic_hash(item, contract=contract) for item in history_items]
    value = stable_sha256(
        {
            "schema": "competition.input_sequence_semantic.v1",
            "registryVersion": HASH_PRECACHE_VERSION,
            "contract": semantic_projection(contract or {}),
            "current": current_hash,
            "historyNewestFirst": history_hashes,
        }
    )
    return {
        "normalizedReportHash": current_hash,
        "inputSequenceHash": value,
        "canonicalSemanticHash": current_hash,
        "historyCanonicalSemanticHashes": history_hashes,
    }


def build_pre_agent_hashes(
    current: Dict[str, Any],
    history: Iterable[Dict[str, Any]],
    *,
    contract: Dict[str, Any],
) -> Dict[str, Any]:
    sequence = input_sequence_hash(current, history, contract=contract)
    evidence_semantic_hash = stable_sha256(
        {
            "schema": "competition.evidence_semantic.v1",
            "inputSequenceHash": sequence["inputSequenceHash"],
            "evidenceContract": semantic_projection(contract),
        }
    )
    admission_semantic_hash = stable_sha256(
        {
            "schema": "competition.admission_semantic.v1",
            "evidenceSemanticHash": evidence_semantic_hash,
            "admissionContract": semantic_projection(contract),
        }
    )
    pre_agent_compute_hash = stable_sha256(
        {
            "schema": "competition.pre_agent_compute_semantic.v1",
            "registryVersion": HASH_PRECACHE_VERSION,
            "inputSequenceHash": sequence["inputSequenceHash"],
            "evidenceSemanticHash": evidence_semantic_hash,
            "admissionSemanticHash": admission_semantic_hash,
            "contract": semantic_projection(contract),
        }
    )
    return {
        **sequence,
        "evidenceSemanticHash": evidence_semantic_hash,
        "admissionSemanticHash": admission_semantic_hash,
        "preAgentComputeHash": pre_agent_compute_hash,
        "hashPrecacheVersion": HASH_PRECACHE_VERSION,
    }


def compute_contract_hash(contract: Dict[str, Any]) -> str:
    return stable_sha256(
        {
            "schema": "competition.pre_agent_compute_contract.v1",
            "registryVersion": HASH_PRECACHE_VERSION,
            "contract": semantic_projection(contract),
        }
    )


def ensure_hash_precache_tables() -> None:
    with connect() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {HASH_PRECACHE_TABLE} (
                pre_agent_compute_hash TEXT PRIMARY KEY,
                layer TEXT NOT NULL,
                contract_hash TEXT NOT NULL,
                artifact_ref TEXT NOT NULL,
                canonical_semantic_hash TEXT,
                input_sequence_hash TEXT,
                evidence_semantic_hash TEXT,
                admission_semantic_hash TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{HASH_PRECACHE_TABLE}_sequence "
            f"ON {HASH_PRECACHE_TABLE}(input_sequence_hash, contract_hash)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{HASH_PRECACHE_TABLE}_evidence "
            f"ON {HASH_PRECACHE_TABLE}(evidence_semantic_hash, contract_hash)"
        )
        conn.commit()


def lookup_pre_agent_cache(hashes: Dict[str, Any], *, contract_hash: str) -> Dict[str, Any]:
    ensure_hash_precache_tables()
    key = str(hashes.get("preAgentComputeHash") or "")
    if not key.startswith("sha256:"):
        return {"hit": False, "status": "semantic_hash_missing"}
    with connect() as conn:
        row = conn.execute(
            f"SELECT * FROM {HASH_PRECACHE_TABLE} WHERE pre_agent_compute_hash=? LIMIT 1",
            (key,),
        ).fetchone()
    if not row:
        return {"hit": False, "status": "cache_miss", "preAgentComputeHash": key}
    record = dict(row)
    if str(record.get("contract_hash") or "") != str(contract_hash):
        return {"hit": False, "status": "contract_hash_mismatch", "preAgentComputeHash": key}
    artifact_ref = str(record.get("artifact_ref") or "")
    validation = validate_artifact(artifact_ref, expected_type=HASH_PRECACHE_ARTIFACT_TYPE)
    if validation.get("ok") is not True:
        return {
            "hit": False,
            "status": "cache_artifact_invalid",
            "preAgentComputeHash": key,
            "artifactRef": artifact_ref,
            "validation": validation,
        }
    body = resolve_artifact(artifact_ref)
    if not isinstance(body, dict):
        return {"hit": False, "status": "cache_body_invalid", "preAgentComputeHash": key}
    if str(body.get("preAgentComputeHash") or "") != key:
        return {"hit": False, "status": "cache_body_hash_mismatch", "preAgentComputeHash": key}
    if str(body.get("computeContractHash") or "") != str(contract_hash):
        return {"hit": False, "status": "cache_body_contract_mismatch", "preAgentComputeHash": key}
    return {
        "hit": True,
        "status": "semantic_cache_hit",
        "preAgentComputeHash": key,
        "artifactRef": artifact_ref,
        "body": body,
        "metadata": loads(record.get("metadata_json") or "{}"),
    }


def store_pre_agent_cache(
    hashes: Dict[str, Any],
    *,
    contract_hash: str,
    business_body: Dict[str, Any],
    metadata: Dict[str, Any] | None = None,
    parent_refs: Iterable[str] = (),
) -> Dict[str, Any]:
    ensure_hash_precache_tables()
    key = str(hashes.get("preAgentComputeHash") or "")
    if not key.startswith("sha256:"):
        raise RuntimeError("pre_agent_compute_hash_required")
    cache_body = {
        "schema": "competition.pre_agent_semantic_cache.v1",
        "version": HASH_PRECACHE_VERSION,
        "preAgentComputeHash": key,
        "computeContractHash": contract_hash,
        "hashes": {
            name: hashes.get(name)
            for name in (
                "normalizedReportHash",
                "inputSequenceHash",
                "canonicalSemanticHash",
                "historyCanonicalSemanticHashes",
                "evidenceSemanticHash",
                "admissionSemanticHash",
            )
        },
        "businessBody": semantic_projection(business_body),
    }
    artifact = store_artifact(
        artifact_type=HASH_PRECACHE_ARTIFACT_TYPE,
        value=cache_body,
        created_by="competition_hash_precache_registry_v1",
        parent_refs=[str(ref) for ref in parent_refs if str(ref).startswith("ART-")],
        metadata={
            "classification": "semantic_cache_key",
            "layer": "L0-L3",
            "preAgentComputeHash": key,
            "contractHash": contract_hash,
            **(metadata or {}),
        },
    )
    artifact_ref = str(artifact.get("artifactId") or "")
    if not artifact_ref.startswith("ART-"):
        raise RuntimeError("hash_precache_artifact_store_failed")
    now = _now()
    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO {HASH_PRECACHE_TABLE}
            (pre_agent_compute_hash,layer,contract_hash,artifact_ref,canonical_semantic_hash,input_sequence_hash,evidence_semantic_hash,admission_semantic_hash,metadata_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(pre_agent_compute_hash) DO UPDATE SET
              layer=excluded.layer,
              contract_hash=excluded.contract_hash,
              artifact_ref=excluded.artifact_ref,
              canonical_semantic_hash=excluded.canonical_semantic_hash,
              input_sequence_hash=excluded.input_sequence_hash,
              evidence_semantic_hash=excluded.evidence_semantic_hash,
              admission_semantic_hash=excluded.admission_semantic_hash,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                key,
                "L0-L3",
                contract_hash,
                artifact_ref,
                hashes.get("canonicalSemanticHash"),
                hashes.get("inputSequenceHash"),
                hashes.get("evidenceSemanticHash"),
                hashes.get("admissionSemanticHash"),
                dumps(metadata or {}),
                now,
                now,
            ),
        )
        conn.commit()
    return {
        "stored": True,
        "artifactRef": artifact_ref,
        "preAgentComputeHash": key,
        "computeContractHash": contract_hash,
        "idempotentHit": bool(artifact.get("idempotentHit")),
    }


def hash_precache_status() -> Dict[str, Any]:
    ensure_hash_precache_tables()
    with connect() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS c, MAX(updated_at) AS latest FROM {HASH_PRECACHE_TABLE}"
        ).fetchone()
    return {
        "schema": HASH_PRECACHE_SCHEMA,
        "version": HASH_PRECACHE_VERSION,
        "cacheEntryCount": int(row["c"] or 0) if row else 0,
        "latestUpdatedAt": row["latest"] if row else None,
        "artifactType": HASH_PRECACHE_ARTIFACT_TYPE,
        "strictExecutionHashDefinitionsChanged": False,
        "frontendChanged": False,
        "workerCountChanged": False,
    }


__all__ = [
    "HASH_PRECACHE_VERSION",
    "HASH_PRECACHE_SCHEMA",
    "HASH_PRECACHE_TABLE",
    "HASH_PRECACHE_ARTIFACT_TYPE",
    "SEMANTIC_EXCLUDED_KEYS",
    "semantic_projection",
    "semantic_hash",
    "canonical_business_projection",
    "canonical_semantic_hash",
    "input_sequence_hash",
    "build_pre_agent_hashes",
    "compute_contract_hash",
    "ensure_hash_precache_tables",
    "lookup_pre_agent_cache",
    "store_pre_agent_cache",
    "hash_precache_status",
]
