"""V25.12 knowledge Index Manifest, Head and exact revision-set retrieval authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import json
from typing import Any, Dict, Iterable, List, Mapping

from src.repositories.sqlite_repository import connect
from src.services.v25_knowledge_lifecycle_v2511_service import mark_expired_active_stale
from src.services.v25_knowledge_revision_v2510_service import hash_value, revision_record

VERSION = "25.12.0"
INDEX_ID = "competition-knowledge-index"
INDEX_ENGINE = "sqlite_structured_v1"
MANIFEST_SCHEMA = "rag.knowledge_index_manifest.v1"
RETRIEVAL_RECEIPT_SCHEMA = "rag.knowledge_retrieval_receipt.v1"
RETRIEVAL_POLICY_VERSION = "25.12.0"


def _now() -> str:
    return datetime.now().isoformat()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def ensure_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_knowledge_index_manifests(
                manifest_hash TEXT PRIMARY KEY,
                index_version TEXT NOT NULL UNIQUE,
                knowledge_snapshot_hash TEXT NOT NULL,
                source_revision_set_hash TEXT NOT NULL,
                active_revision_ids_json TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                parent_manifest_hash TEXT,
                built_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_knowledge_index_head(
                head_key TEXT PRIMARY KEY,
                current_manifest_hash TEXT,
                previous_manifest_hash TEXT,
                rollback_pinned INTEGER NOT NULL DEFAULT 0,
                changed_by TEXT,
                reason TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO rag_knowledge_index_head(
                head_key, current_manifest_hash, previous_manifest_hash,
                rollback_pinned, changed_by, reason, updated_at
            ) VALUES ('knowledge', NULL, NULL, 0, 'bootstrap', 'phase4_schema_bootstrap', ?)
            """,
            (_now(),),
        )
        conn.commit()


def _head() -> Dict[str, Any]:
    ensure_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM rag_knowledge_index_head WHERE head_key = 'knowledge'"
        ).fetchone()
    return dict(row) if row else {}


def _manifest(manifest_hash: str | None) -> Dict[str, Any] | None:
    if not manifest_hash:
        return None
    ensure_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT manifest_json FROM rag_knowledge_index_manifests WHERE manifest_hash = ?",
            (manifest_hash,),
        ).fetchone()
    if not row:
        return None
    value = json.loads(str(row["manifest_json"]))
    return value if isinstance(value, dict) else None


def current_manifest() -> Dict[str, Any]:
    head = _head()
    result = _manifest(str(head.get("current_manifest_hash") or "")) or {}
    if not result:
        return {}
    result = deepcopy(result)
    result["head"] = {
        "currentManifestHash": head.get("current_manifest_hash"),
        "previousManifestHash": head.get("previous_manifest_hash"),
        "rollbackPinned": bool(head.get("rollback_pinned")),
    }
    return result


def _active_revisions() -> List[Dict[str, str]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.revision_id, r.case_id, r.content_hash, r.source_task_id
            FROM rag_knowledge_revisions r
            JOIN rag_knowledge_revision_state s ON s.revision_id = r.revision_id
            WHERE s.lifecycle_state = 'active'
            ORDER BY r.case_id ASC, r.revision_id ASC
            """
        ).fetchall()
    return [
        {
            "revisionId": str(row["revision_id"]),
            "caseId": str(row["case_id"]),
            "contentHash": str(row["content_hash"]),
            "sourceTaskId": str(row["source_task_id"]),
        }
        for row in rows
    ]


def release_rollback_pin(*, actor_id: str, reason: str) -> None:
    ensure_tables()
    with connect() as conn:
        conn.execute(
            """
            UPDATE rag_knowledge_index_head
            SET rollback_pinned = 0, changed_by = ?, reason = ?, updated_at = ?
            WHERE head_key = 'knowledge'
            """,
            (actor_id, reason, _now()),
        )
        conn.commit()


def ensure_active_manifest(
    *,
    actor_id: str = "knowledge_index_builder",
    reason: str = "knowledge_snapshot_refresh",
) -> Dict[str, Any]:
    """Build/switch only when the active immutable revision set changes."""
    ensure_tables()
    head = _head()
    if bool(head.get("rollback_pinned")) and head.get("current_manifest_hash"):
        pinned = current_manifest()
        if pinned:
            return pinned

    mark_expired_active_stale()
    active = _active_revisions()
    revision_ids = [item["revisionId"] for item in active]
    snapshot_hash = hash_value(active)
    revision_set_hash = hash_value(revision_ids)
    current = _manifest(str(head.get("current_manifest_hash") or "")) or {}
    if current.get("knowledgeSnapshotHash") == snapshot_hash:
        return current_manifest()

    with connect() as conn:
        count = int(conn.execute(
            "SELECT COUNT(*) AS c FROM rag_knowledge_index_manifests"
        ).fetchone()["c"])
        index_version = f"knowledge-index-{count + 1:06d}"
        identity = {
            "knowledgeIndexId": INDEX_ID,
            "indexVersion": index_version,
            "knowledgeSnapshotHash": snapshot_hash,
            "sourceRevisionSetHash": revision_set_hash,
            "retrievalContractVersion": RETRIEVAL_POLICY_VERSION,
            "indexEngine": INDEX_ENGINE,
            "activeRevisions": active,
        }
        manifest_hash = hash_value(identity)
        built_at = _now()
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "version": VERSION,
            **identity,
            "cardCount": len(active),
            "activeCardCount": len(active),
            "builtAt": built_at,
            "builtFrom": reason,
            "parentManifestHash": head.get("current_manifest_hash"),
            "manifestHash": manifest_hash,
        }
        conn.execute(
            """
            INSERT INTO rag_knowledge_index_manifests(
                manifest_hash, index_version, knowledge_snapshot_hash,
                source_revision_set_hash, active_revision_ids_json, manifest_json,
                parent_manifest_hash, built_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest_hash, index_version, snapshot_hash, revision_set_hash,
                _canonical(revision_ids), _canonical(manifest),
                head.get("current_manifest_hash"), built_at,
            ),
        )
        conn.execute(
            """
            UPDATE rag_knowledge_index_head
            SET previous_manifest_hash = current_manifest_hash,
                current_manifest_hash = ?, rollback_pinned = 0,
                changed_by = ?, reason = ?, updated_at = ?
            WHERE head_key = 'knowledge'
            """,
            (manifest_hash, actor_id, reason, built_at),
        )
        conn.commit()
    return current_manifest()


def rollback_head(
    *,
    actor_id: str,
    reason: str,
    target_manifest_hash: str | None = None,
) -> Dict[str, Any]:
    """Rollback changes only Head; immutable manifests/revisions are untouched."""
    head = _head()
    target = target_manifest_hash or head.get("previous_manifest_hash")
    target_manifest = _manifest(str(target or ""))
    if not target_manifest:
        raise ValueError("rollback target knowledge manifest does not exist")
    with connect() as conn:
        conn.execute(
            """
            UPDATE rag_knowledge_index_head
            SET current_manifest_hash = ?, previous_manifest_hash = ?,
                rollback_pinned = 1, changed_by = ?, reason = ?, updated_at = ?
            WHERE head_key = 'knowledge'
            """,
            (target, head.get("current_manifest_hash"), actor_id, reason, _now()),
        )
        conn.commit()
    return current_manifest()


def resume_current_active_set(*, actor_id: str, reason: str = "rollback_pin_released") -> Dict[str, Any]:
    release_rollback_pin(actor_id=actor_id, reason=reason)
    return ensure_active_manifest(actor_id=actor_id, reason=reason)


def load_head_cases() -> List[Dict[str, Any]]:
    """Materialize exactly the immutable revisions selected by current Head."""
    manifest = ensure_active_manifest(actor_id="agent_rag_retrieval", reason="retrieval_snapshot_guard")
    result: List[Dict[str, Any]] = []
    for item in list(manifest.get("activeRevisions") or []):
        revision_id = str(dict(item).get("revisionId") or "")
        row = revision_record(revision_id)
        if not row:
            continue
        valid_until = str(row.get("validUntil") or "").strip()
        if valid_until:
            try:
                if date.fromisoformat(valid_until[:10]) < date.today():
                    continue
            except Exception:
                pass
        payload = deepcopy(dict(row.get("content") or {}))
        payload.update({
            "caseId": row.get("caseId"),
            "sourceTaskId": row.get("sourceTaskId"),
            "status": "approved",
            "effective": True,
            "knowledgeRevisionId": revision_id,
            "knowledgeContentHash": row.get("contentHash"),
            "knowledgeIndexVersion": manifest.get("indexVersion"),
            "knowledgeIndexManifestHash": manifest.get("manifestHash"),
        })
        result.append(payload)
    return result


def retrieval_receipt(
    *,
    query_fingerprint: str,
    matched_case_ids: Iterable[str],
) -> Dict[str, Any]:
    manifest = ensure_active_manifest(actor_id="agent_rag_retrieval", reason="retrieval_receipt")
    case_to_revision = {
        str(item.get("caseId")): str(item.get("revisionId"))
        for item in list(manifest.get("activeRevisions") or [])
        if isinstance(item, dict)
    }
    matched_revision_ids = sorted({
        case_to_revision[case_id]
        for case_id in matched_case_ids
        if case_id in case_to_revision
    })
    receipt = {
        "schema": RETRIEVAL_RECEIPT_SCHEMA,
        "queryFingerprint": query_fingerprint,
        "knowledgeSnapshotHash": manifest.get("knowledgeSnapshotHash"),
        "indexVersion": manifest.get("indexVersion"),
        "indexManifestHash": manifest.get("manifestHash"),
        "retrievalPolicyVersion": RETRIEVAL_POLICY_VERSION,
        "matchedRevisionIds": matched_revision_ids,
    }
    receipt["retrievalReceiptHash"] = hash_value(receipt)
    return receipt
