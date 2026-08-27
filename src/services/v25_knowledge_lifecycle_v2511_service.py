"""V25.11 knowledge lifecycle authority, separate from task lifecycle."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict

from src.repositories.sqlite_repository import connect
from src.services.v25_knowledge_revision_v2510_service import hash_value

VERSION = "25.11.0"
STATES = {
    "pending_review", "active", "stale", "re_review", "superseded",
    "deprecated", "archived", "rejected",
}
TRANSITIONS = {
    "pending_review": {"active", "re_review", "rejected"},
    "active": {"stale", "superseded", "deprecated"},
    "stale": {"re_review", "deprecated", "archived"},
    "re_review": {"active", "rejected", "deprecated"},
    "superseded": {"archived"},
    "deprecated": {"archived"},
    "archived": set(),
    "rejected": set(),
}


def _now() -> str:
    return datetime.now().isoformat()


def ensure_tables() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_knowledge_revision_state(
                revision_id TEXT PRIMARY KEY,
                lifecycle_state TEXT NOT NULL,
                stale_reason TEXT,
                replacement_revision_id TEXT,
                last_event_hash TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_knowledge_lifecycle_events(
                event_hash TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rag_knowledge_reuse_events(
                event_hash TEXT PRIMARY KEY,
                revision_id TEXT NOT NULL,
                retrieval_receipt_hash TEXT NOT NULL,
                outcome TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_state ON rag_knowledge_revision_state(lifecycle_state, updated_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_lifecycle_revision ON rag_knowledge_lifecycle_events(revision_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_reuse_revision ON rag_knowledge_reuse_events(revision_id, created_at)")
        conn.commit()


def state_of(revision_id: str) -> str | None:
    ensure_tables()
    with connect() as conn:
        row = conn.execute(
            "SELECT lifecycle_state FROM rag_knowledge_revision_state WHERE revision_id = ?",
            (revision_id,),
        ).fetchone()
    return str(row["lifecycle_state"]) if row else None


def register_revision(revision_id: str, *, actor_id: str = "knowledge_candidate_writer") -> str:
    ensure_tables()
    existing = state_of(revision_id)
    if existing:
        return existing
    event_hash = hash_value({
        "revisionId": revision_id,
        "from": None,
        "to": "pending_review",
        "actorId": actor_id,
        "reason": "immutable_revision_created",
    })
    now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO rag_knowledge_revision_state(revision_id, lifecycle_state, stale_reason, replacement_revision_id, last_event_hash, updated_at) VALUES (?, 'pending_review', NULL, NULL, ?, ?)",
            (revision_id, event_hash, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO rag_knowledge_lifecycle_events(event_hash, revision_id, from_state, to_state, actor_id, reason, created_at) VALUES (?, ?, NULL, 'pending_review', ?, 'immutable_revision_created', ?)",
            (event_hash, revision_id, actor_id, now),
        )
        conn.commit()
    return "pending_review"


def transition(
    revision_id: str,
    to_state: str,
    *,
    actor_id: str,
    reason: str,
    replacement_revision_id: str | None = None,
) -> str:
    ensure_tables()
    if to_state not in STATES:
        raise ValueError(f"unknown lifecycle state: {to_state}")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM rag_knowledge_revision_state WHERE revision_id = ?", (revision_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"unknown knowledge revision: {revision_id}")
        current = str(row["lifecycle_state"])
        if current == to_state:
            return str(row["last_event_hash"] or "")
        if to_state not in TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid knowledge lifecycle transition: {current} -> {to_state}")
        event_hash = hash_value({
            "revisionId": revision_id,
            "from": current,
            "to": to_state,
            "actorId": actor_id,
            "reason": reason,
            "replacementRevisionId": replacement_revision_id,
        })
        now = _now()
        conn.execute(
            """
            UPDATE rag_knowledge_revision_state
            SET lifecycle_state = ?, stale_reason = ?, replacement_revision_id = ?,
                last_event_hash = ?, updated_at = ?
            WHERE revision_id = ?
            """,
            (to_state, reason if to_state == "stale" else None, replacement_revision_id, event_hash, now, revision_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO rag_knowledge_lifecycle_events(event_hash, revision_id, from_state, to_state, actor_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_hash, revision_id, current, to_state, actor_id, reason, now),
        )
        conn.commit()
    return event_hash


def mark_expired_active_stale(*, actor_id: str = "knowledge_expiry_guard") -> list[str]:
    """Expiry only marks stale; it never deletes or auto-approves anything."""
    ensure_tables()
    today = date.today()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.revision_id, r.valid_until
            FROM rag_knowledge_revisions r
            JOIN rag_knowledge_revision_state s ON s.revision_id = r.revision_id
            WHERE s.lifecycle_state = 'active' AND r.valid_until IS NOT NULL AND TRIM(r.valid_until) != ''
            """
        ).fetchall()
    expired: list[str] = []
    for row in rows:
        try:
            valid_until = date.fromisoformat(str(row["valid_until"])[:10])
        except Exception:
            continue
        if valid_until < today:
            revision_id = str(row["revision_id"])
            transition(
                revision_id,
                "stale",
                actor_id=actor_id,
                reason="validUntil_before_current_date",
            )
            expired.append(revision_id)
    return expired


def record_reuse_outcome(
    revision_id: str,
    *,
    retrieval_receipt_hash: str,
    outcome: str,
    actor_id: str,
    notes: str = "",
) -> Dict[str, Any]:
    if outcome not in {"success", "failure", "neutral"}:
        raise ValueError("outcome must be success, failure or neutral")
    ensure_tables()
    material = {
        "revisionId": revision_id,
        "retrievalReceiptHash": retrieval_receipt_hash,
        "outcome": outcome,
        "actorId": actor_id,
        "notes": notes,
    }
    event_hash = hash_value(material)
    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM rag_knowledge_revision_state WHERE revision_id = ?", (revision_id,)
        ).fetchone()
        if not exists:
            raise ValueError(f"unknown knowledge revision: {revision_id}")
        conn.execute(
            "INSERT OR IGNORE INTO rag_knowledge_reuse_events(event_hash, revision_id, retrieval_receipt_hash, outcome, actor_id, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_hash, revision_id, retrieval_receipt_hash, outcome, actor_id, notes, _now()),
        )
        rows = conn.execute(
            "SELECT outcome FROM rag_knowledge_reuse_events WHERE revision_id = ?", (revision_id,)
        ).fetchall()
        conn.commit()
    total = len(rows)
    success = sum(1 for row in rows if str(row["outcome"]) == "success")
    failure = sum(1 for row in rows if str(row["outcome"]) == "failure")
    return {
        "version": VERSION,
        "revisionId": revision_id,
        "reuseEventHash": event_hash,
        "reuseCount": total,
        "successRateAfterReuse": round(success / total, 4) if total else None,
        "failureRateAfterReuse": round(failure / total, 4) if total else None,
        "lifecycleState": state_of(revision_id),
        "automaticLifecycleChange": False,
        "automaticDelete": False,
    }
