"""V25.10 immutable knowledge revision and human-review audit authority."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from typing import Any, Dict, Mapping

from src.repositories.sqlite_repository import connect

VERSION = "25.10.0"

_VOLATILE = {
    "caseId", "level", "status", "effective", "reviewStatus", "reviewerId",
    "reviewerName", "reviewReason", "reviewedAt", "createdAt", "updatedAt",
    "lastUsedAt", "reuseCount", "successRateAfterReuse", "failureRateAfterReuse",
    "staleReason", "replacementRevision", "_allowStatusOverwrite",
    "protectedApprovedCase", "protectionRule", "latestFeedbackDraft",
    "feedbackDraftHistory",
}


def _now() -> str:
    return datetime.now().isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def hash_value(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def is_real_task_experience(card: Mapping[str, Any]) -> bool:
    source_task_id = str(card.get("sourceTaskId") or "").strip()
    if not source_task_id or card.get("seedVersion"):
        return False
    if str(card.get("status") or "") == "seed_approved":
        return False
    return str(card.get("caseType") or "operation_solution") not in {
        "cross_validation_rule", "acceptance_rule", "category_profile"
    }


def immutable_content(card: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        str(key): deepcopy(value)
        for key, value in dict(card).items()
        if str(key) not in _VOLATILE
    }


def ensure_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_knowledge_revisions (
                revision_id TEXT PRIMARY KEY,
                case_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                content_json TEXT NOT NULL,
                source_task_id TEXT NOT NULL,
                source_recap_hash TEXT NOT NULL,
                previous_revision_id TEXT,
                valid_until TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(case_id, content_hash, source_task_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_knowledge_review_events (
                event_hash TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reviewer_id TEXT NOT NULL,
                reason TEXT,
                before_hash TEXT NOT NULL,
                after_hash TEXT NOT NULL,
                migration INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_revision_case ON rag_knowledge_revisions(case_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_review_revision ON rag_knowledge_review_events(revision_id, created_at)")
        conn.commit()


def ensure_revision(card: Mapping[str, Any]) -> str | None:
    """Create one immutable revision for real task-derived knowledge content."""
    if not is_real_task_experience(card):
        return None
    ensure_tables()
    case_id = str(card.get("caseId") or "").strip()
    source_task_id = str(card.get("sourceTaskId") or "").strip()
    if not case_id or not source_task_id:
        return None
    content = immutable_content(card)
    content_hash = hash_value(content)
    revision_id = "kr-" + hash_value({
        "caseId": case_id,
        "contentHash": content_hash,
        "sourceTaskId": source_task_id,
    })[:24]
    recap_hash = hash_value({
        "sourceTaskId": source_task_id,
        "resultSummary": content.get("resultSummary"),
        "beforeMetrics": content.get("beforeMetrics"),
        "afterMetrics": content.get("afterMetrics"),
        "sourceReportIds": content.get("sourceReportIds") or [],
    })
    with connect() as conn:
        existing = conn.execute(
            "SELECT revision_id FROM rag_knowledge_revisions WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
        if existing:
            return str(existing["revision_id"])
        previous = conn.execute(
            "SELECT revision_id FROM rag_knowledge_revisions WHERE case_id = ? ORDER BY created_at DESC, revision_id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO rag_knowledge_revisions(
                revision_id, case_id, content_hash, content_json, source_task_id,
                source_recap_hash, previous_revision_id, valid_until, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id, case_id, content_hash, canonical(content), source_task_id,
                recap_hash, str(previous["revision_id"]) if previous else None,
                str(card.get("validUntil") or "").strip() or None, _now(),
            ),
        )
        conn.commit()
    return revision_id


def latest_revision(case_id: str, *, states: tuple[str, ...] | None = None) -> str | None:
    ensure_tables()
    if states:
        marks = ",".join("?" for _ in states)
        query = f"""
            SELECT r.revision_id
            FROM rag_knowledge_revisions r
            JOIN rag_knowledge_revision_state s ON s.revision_id = r.revision_id
            WHERE r.case_id = ? AND s.lifecycle_state IN ({marks})
            ORDER BY r.created_at DESC, r.revision_id DESC LIMIT 1
        """
        params = (case_id, *states)
    else:
        query = "SELECT revision_id FROM rag_knowledge_revisions WHERE case_id = ? ORDER BY created_at DESC, revision_id DESC LIMIT 1"
        params = (case_id,)
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
    return str(row["revision_id"]) if row else None


def record_review(
    revision_id: str,
    *,
    decision: str,
    reviewer_id: str,
    before_state: str,
    after_state: str,
    reason: str = "",
    migration: bool = False,
) -> str:
    """Append an immutable review event; caller owns lifecycle transition."""
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    reviewer = str(reviewer_id or "").strip()
    if not reviewer:
        raise ValueError("human reviewer identity is required")
    ensure_tables()
    before_hash = hash_value({"revisionId": revision_id, "lifecycleState": before_state})
    after_hash = hash_value({"revisionId": revision_id, "lifecycleState": after_state})
    material = {
        "revisionId": revision_id,
        "decision": decision,
        "reviewerId": reviewer,
        "reason": reason,
        "beforeHash": before_hash,
        "afterHash": after_hash,
        "migration": bool(migration),
    }
    event_hash = hash_value(material)
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM rag_knowledge_revisions WHERE revision_id = ?", (revision_id,)
        ).fetchone()
        if not exists:
            raise ValueError(f"unknown knowledge revision: {revision_id}")
        conn.execute(
            """
            INSERT OR IGNORE INTO rag_knowledge_review_events(
                event_hash, revision_id, decision, reviewer_id, reason,
                before_hash, after_hash, migration, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_hash, revision_id, decision, reviewer, reason,
                before_hash, after_hash, int(bool(migration)), _now(),
            ),
        )
        conn.commit()
    return event_hash


def revision_record(revision_id: str) -> Dict[str, Any] | None:
    ensure_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM rag_knowledge_revisions WHERE revision_id = ?", (revision_id,)).fetchone()
    if not row:
        return None
    payload = json.loads(str(row["content_json"]))
    return {
        "revisionId": str(row["revision_id"]),
        "caseId": str(row["case_id"]),
        "contentHash": str(row["content_hash"]),
        "sourceTaskId": str(row["source_task_id"]),
        "sourceRecapHash": str(row["source_recap_hash"]),
        "previousRevisionId": row["previous_revision_id"],
        "validUntil": row["valid_until"],
        "content": payload if isinstance(payload, dict) else {},
    }
