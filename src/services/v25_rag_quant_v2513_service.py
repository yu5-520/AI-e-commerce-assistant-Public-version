"""V25.13 receipt-bound RAG quantification and retrieval observability authority."""
from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Any, Dict, Mapping, Sequence

from src.repositories.sqlite_repository import connect
from src.services.v25_knowledge_index_v2512_service import current_manifest, ensure_active_manifest
from src.services.v25_knowledge_revision_v2510_service import hash_value

VERSION = "25.13.0"
OBSERVATION_SCHEMA = "rag.retrieval_observation.v1"
METRIC_SNAPSHOT_SCHEMA = "rag.retrieval_metric_snapshot.v1"
KNOWLEDGE_HEALTH_SCHEMA = "rag.knowledge_health_snapshot.v1"


def _now() -> str:
    return datetime.now().isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 4)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 4)


def ensure_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_retrieval_observations(
                observation_hash TEXT PRIMARY KEY,
                query_fingerprint TEXT NOT NULL,
                index_version TEXT NOT NULL,
                index_manifest_hash TEXT NOT NULL,
                knowledge_snapshot_hash TEXT NOT NULL,
                retrieval_policy_version TEXT NOT NULL,
                candidate_count INTEGER NOT NULL,
                eligible_count INTEGER NOT NULL,
                matched_count INTEGER NOT NULL,
                filtered_lifecycle_count INTEGER NOT NULL,
                latency_ms REAL NOT NULL,
                matched_revision_ids_json TEXT NOT NULL,
                retrieval_receipt_hash TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_metric_snapshots(
                metric_snapshot_hash TEXT PRIMARY KEY,
                index_manifest_hash TEXT NOT NULL,
                metric_version TEXT NOT NULL,
                observation_count INTEGER NOT NULL,
                metrics_json TEXT NOT NULL,
                knowledge_health_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rag_observation_manifest ON rag_retrieval_observations(index_manifest_hash, recorded_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rag_observation_receipt ON rag_retrieval_observations(retrieval_receipt_hash)"
        )
        conn.commit()


def _verify_receipt(receipt: Mapping[str, Any]) -> Dict[str, Any]:
    value = dict(receipt)
    declared = str(value.pop("retrievalReceiptHash", "")).strip()
    if not declared:
        raise ValueError("retrieval receipt hash is required")
    expected = hash_value(value)
    if declared != expected:
        raise ValueError("retrieval receipt hash mismatch")
    required = (
        "queryFingerprint",
        "knowledgeSnapshotHash",
        "indexVersion",
        "indexManifestHash",
        "retrievalPolicyVersion",
        "matchedRevisionIds",
    )
    for field in required:
        if field not in receipt:
            raise ValueError(f"retrieval receipt missing field: {field}")
    return dict(receipt)


def record_retrieval_observation(
    receipt: Mapping[str, Any],
    *,
    candidate_count: int,
    eligible_count: int,
    matched_count: int | None = None,
    filtered_lifecycle_count: int,
    latency_ms: float,
) -> Dict[str, Any]:
    """Persist one immutable observation around an already-issued Phase4 receipt.

    This does not implement or replace retrieval. It measures the exact receipt emitted by
    the current production knowledge authority.
    """
    ensure_tables()
    verified_receipt = _verify_receipt(receipt)
    matched_revision_ids = sorted({str(value) for value in verified_receipt.get("matchedRevisionIds") or []})
    resolved_matched_count = len(matched_revision_ids) if matched_count is None else int(matched_count)
    candidate = int(candidate_count)
    eligible = int(eligible_count)
    filtered = int(filtered_lifecycle_count)
    latency = float(latency_ms)
    if min(candidate, eligible, resolved_matched_count, filtered) < 0 or latency < 0:
        raise ValueError("retrieval observation values must be non-negative")
    if eligible > candidate:
        raise ValueError("eligible_count cannot exceed candidate_count")
    if resolved_matched_count > eligible:
        raise ValueError("matched_count cannot exceed eligible_count")
    if candidate - eligible != filtered:
        raise ValueError("filtered_lifecycle_count must equal candidate_count - eligible_count")
    if resolved_matched_count != len(matched_revision_ids):
        raise ValueError("matched_count must equal retrieval receipt revision count")

    material = {
        "schema": OBSERVATION_SCHEMA,
        "version": VERSION,
        "queryFingerprint": str(verified_receipt["queryFingerprint"]),
        "indexVersion": str(verified_receipt["indexVersion"]),
        "indexManifestHash": str(verified_receipt["indexManifestHash"]),
        "knowledgeSnapshotHash": str(verified_receipt["knowledgeSnapshotHash"]),
        "retrievalPolicyVersion": str(verified_receipt["retrievalPolicyVersion"]),
        "candidateCount": candidate,
        "eligibleCount": eligible,
        "matchedCount": resolved_matched_count,
        "filteredLifecycleCount": filtered,
        "latencyMs": round(latency, 4),
        "matchedRevisionIds": matched_revision_ids,
        "retrievalReceiptHash": str(verified_receipt["retrievalReceiptHash"]),
    }
    observation_hash = hash_value(material)
    recorded_at = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO rag_retrieval_observations(
                observation_hash, query_fingerprint, index_version, index_manifest_hash,
                knowledge_snapshot_hash, retrieval_policy_version, candidate_count,
                eligible_count, matched_count, filtered_lifecycle_count, latency_ms,
                matched_revision_ids_json, retrieval_receipt_hash, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation_hash,
                material["queryFingerprint"],
                material["indexVersion"],
                material["indexManifestHash"],
                material["knowledgeSnapshotHash"],
                material["retrievalPolicyVersion"],
                candidate,
                eligible,
                resolved_matched_count,
                filtered,
                material["latencyMs"],
                _canonical(matched_revision_ids),
                material["retrievalReceiptHash"],
                recorded_at,
            ),
        )
        conn.commit()
    return {**material, "observationHash": observation_hash, "recordedAt": recorded_at}


def retrieval_trace(retrieval_receipt_hash: str) -> Dict[str, Any]:
    ensure_tables()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM rag_retrieval_observations
            WHERE retrieval_receipt_hash = ?
            ORDER BY recorded_at DESC, observation_hash DESC LIMIT 1
            """,
            (str(retrieval_receipt_hash),),
        ).fetchone()
    if not row:
        return {}
    return {
        "schema": OBSERVATION_SCHEMA,
        "version": VERSION,
        "observationHash": str(row["observation_hash"]),
        "queryFingerprint": str(row["query_fingerprint"]),
        "indexVersion": str(row["index_version"]),
        "indexManifestHash": str(row["index_manifest_hash"]),
        "knowledgeSnapshotHash": str(row["knowledge_snapshot_hash"]),
        "retrievalPolicyVersion": str(row["retrieval_policy_version"]),
        "candidateCount": int(row["candidate_count"]),
        "eligibleCount": int(row["eligible_count"]),
        "matchedCount": int(row["matched_count"]),
        "filteredLifecycleCount": int(row["filtered_lifecycle_count"]),
        "latencyMs": float(row["latency_ms"]),
        "matchedRevisionIds": json.loads(str(row["matched_revision_ids_json"])),
        "retrievalReceiptHash": str(row["retrieval_receipt_hash"]),
        "recordedAt": str(row["recorded_at"]),
    }


def knowledge_health_snapshot() -> Dict[str, Any]:
    ensure_active_manifest(actor_id="rag_quantification", reason="knowledge_health_snapshot")
    states = {
        "pending_review": 0,
        "active": 0,
        "stale": 0,
        "re_review": 0,
        "superseded": 0,
        "deprecated": 0,
        "archived": 0,
        "rejected": 0,
    }
    with connect() as conn:
        rows = conn.execute(
            "SELECT lifecycle_state, COUNT(*) AS c FROM rag_knowledge_revision_state GROUP BY lifecycle_state"
        ).fetchall()
    for row in rows:
        state = str(row["lifecycle_state"])
        states[state] = int(row["c"])
    manifest = current_manifest()
    material = {
        "schema": KNOWLEDGE_HEALTH_SCHEMA,
        "version": VERSION,
        "indexVersion": manifest.get("indexVersion"),
        "indexManifestHash": manifest.get("manifestHash"),
        "knowledgeSnapshotHash": manifest.get("knowledgeSnapshotHash"),
        "states": states,
        "totalRevisionStateCount": sum(states.values()),
        "retrievalEligibleState": "active",
    }
    material["knowledgeHealthHash"] = hash_value(material)
    return material


def retrieval_metric_snapshot(*, index_manifest_hash: str | None = None) -> Dict[str, Any]:
    """Materialize immutable operational metrics for one exact knowledge manifest."""
    ensure_tables()
    manifest = ensure_active_manifest(actor_id="rag_quantification", reason="metric_snapshot")
    target_manifest_hash = str(index_manifest_hash or manifest.get("manifestHash") or "")
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT candidate_count, eligible_count, matched_count,
                   filtered_lifecycle_count, latency_ms
            FROM rag_retrieval_observations
            WHERE index_manifest_hash = ?
            ORDER BY recorded_at ASC, observation_hash ASC
            """,
            (target_manifest_hash,),
        ).fetchall()
    count = len(rows)
    latency = [float(row["latency_ms"]) for row in rows]
    zero_hits = sum(1 for row in rows if int(row["matched_count"]) == 0)
    metrics = {
        "observationCount": count,
        "zeroHitRate": round(zero_hits / count, 6) if count else None,
        "averageCandidateCount": round(sum(int(row["candidate_count"]) for row in rows) / count, 4) if count else None,
        "averageEligibleCount": round(sum(int(row["eligible_count"]) for row in rows) / count, 4) if count else None,
        "averageMatchedCount": round(sum(int(row["matched_count"]) for row in rows) / count, 4) if count else None,
        "averageFilteredLifecycleCount": round(sum(int(row["filtered_lifecycle_count"]) for row in rows) / count, 4) if count else None,
        "latencyMsP50": _percentile(latency, 0.50),
        "latencyMsP95": _percentile(latency, 0.95),
        "hitAt1": None,
        "hitAt3": None,
        "hitAt5": None,
        "mrr": None,
        "groundTruthMetricsRequireEvalSet": True,
    }
    health = knowledge_health_snapshot()
    material = {
        "schema": METRIC_SNAPSHOT_SCHEMA,
        "version": VERSION,
        "indexVersion": manifest.get("indexVersion") if target_manifest_hash == manifest.get("manifestHash") else None,
        "indexManifestHash": target_manifest_hash,
        "knowledgeSnapshotHash": manifest.get("knowledgeSnapshotHash") if target_manifest_hash == manifest.get("manifestHash") else None,
        "metrics": metrics,
        "knowledgeHealth": health,
    }
    snapshot_hash = hash_value(material)
    created_at = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO rag_metric_snapshots(
                metric_snapshot_hash, index_manifest_hash, metric_version,
                observation_count, metrics_json, knowledge_health_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_hash,
                target_manifest_hash,
                VERSION,
                count,
                _canonical(metrics),
                _canonical(health),
                created_at,
            ),
        )
        conn.commit()
    return {**material, "metricSnapshotHash": snapshot_hash, "createdAt": created_at}


def recent_observations(*, limit: int = 50) -> list[Dict[str, Any]]:
    ensure_tables()
    resolved_limit = max(1, min(int(limit), 500))
    with connect() as conn:
        rows = conn.execute(
            "SELECT retrieval_receipt_hash FROM rag_retrieval_observations ORDER BY recorded_at DESC, observation_hash DESC LIMIT ?",
            (resolved_limit,),
        ).fetchall()
    return [retrieval_trace(str(row["retrieval_receipt_hash"])) for row in rows]
