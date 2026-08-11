"""Current-run history boundary for competition/demo product evidence.

Canonical product snapshots are an archive and are intentionally not destroyed by a
demo reset. Product trend/evidence reads, however, must never mix snapshots from a
previous evaluator run into the current run. This service owns that boundary.

The boundary is stored in ``runtime_meta`` and automatically rotates when the existing
system reset contract updates ``latest_demo_reset_scope``. For legacy databases that
predate this contract, the first read fails closed by treating only the newest canonical
snapshot as the beginning of the current epoch. Subsequent uploads accumulate inside
that same epoch until the next reset.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict

from src.repositories.sqlite_repository import connect

COMPETITION_HISTORY_EPOCH_VERSION = "1.0.1"
EPOCH_ID_KEY = "competition_history_epoch_id"
EPOCH_STARTED_AT_KEY = "competition_history_epoch_started_at"
EPOCH_SOURCE_RESET_TOKEN_KEY = "competition_history_epoch_source_reset_token"
EPOCH_BOOTSTRAP_MODE_KEY = "competition_history_epoch_bootstrap_mode"
EPOCH_BOOTSTRAP_SNAPSHOT_KEY = "competition_history_epoch_bootstrap_snapshot_id"
SYSTEM_RESET_SCOPE_KEY = "latest_demo_reset_scope"


def _ensure_runtime_meta(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
    )


def _meta_value(conn: Any, key: str) -> str | None:
    row = conn.execute("SELECT value FROM runtime_meta WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    value = row["value"]
    return str(value) if value not in {None, ""} else None


def _set_meta(conn: Any, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO runtime_meta(key,value,updated_at) VALUES (?,?,CURRENT_TIMESTAMP)",
        (key, value),
    )


def _system_reset_state(conn: Any) -> Dict[str, str | None]:
    row = conn.execute(
        "SELECT rowid,value,updated_at FROM runtime_meta WHERE key=? LIMIT 1",
        (SYSTEM_RESET_SCOPE_KEY,),
    ).fetchone()
    if not row:
        return {"scope": None, "updatedAt": None, "token": None}
    scope = str(row["value"] or "demo")
    updated_at = str(row["updated_at"] or "") or None
    token_seed = f"{row['rowid']}|{scope}|{updated_at or ''}"
    token = "sha256:" + hashlib.sha256(token_seed.encode("utf-8")).hexdigest()
    return {"scope": scope, "updatedAt": updated_at, "token": token}


def _latest_canonical_snapshot(conn: Any) -> Dict[str, str | None] | None:
    if not _table_exists(conn, "canonical_product_snapshot_sets_v1"):
        return None
    row = conn.execute(
        """
        SELECT snapshot_id,created_at
        FROM canonical_product_snapshot_sets_v1
        ORDER BY julianday(created_at) DESC, rowid DESC
        LIMIT 1
        """
    ).fetchone()
    if not row:
        return None
    return {
        "snapshotId": str(row["snapshot_id"] or "") or None,
        "createdAt": str(row["created_at"] or "") or None,
    }


def _is_at_or_after(conn: Any, left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    row = conn.execute(
        "SELECT CASE WHEN julianday(?) >= julianday(?) THEN 1 ELSE 0 END AS matched",
        (left, right),
    ).fetchone()
    return bool(row and row["matched"])


def _epoch_id(*, started_at: str, reset_token: str | None, snapshot_id: str | None) -> str:
    seed = f"{started_at}|{reset_token or 'no-reset'}|{snapshot_id or 'no-snapshot'}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return f"HIST-EPOCH-{digest}"


def _persist_epoch(
    conn: Any,
    *,
    started_at: str,
    reset_token: str | None,
    bootstrap_mode: str,
    snapshot_id: str | None,
) -> Dict[str, Any]:
    epoch_id = _epoch_id(
        started_at=started_at,
        reset_token=reset_token,
        snapshot_id=snapshot_id,
    )
    _set_meta(conn, EPOCH_ID_KEY, epoch_id)
    _set_meta(conn, EPOCH_STARTED_AT_KEY, started_at)
    _set_meta(conn, EPOCH_SOURCE_RESET_TOKEN_KEY, reset_token or "")
    _set_meta(conn, EPOCH_BOOTSTRAP_MODE_KEY, bootstrap_mode)
    _set_meta(conn, EPOCH_BOOTSTRAP_SNAPSHOT_KEY, snapshot_id or "")
    conn.commit()
    return {
        "version": COMPETITION_HISTORY_EPOCH_VERSION,
        "epochId": epoch_id,
        "startedAt": started_at,
        "bootstrapMode": bootstrap_mode,
        "bootstrapSnapshotId": snapshot_id,
        "sourceResetToken": reset_token,
        "crossEpochHistoryAllowed": False,
        "archivePreserved": True,
    }


def current_competition_history_epoch() -> Dict[str, Any]:
    """Return the active evaluator-history epoch, rotating after a demo reset.

    Existing canonical snapshots remain archived. The returned ``startedAt`` is the
    lower time bound that current-run trend/evidence queries must enforce.
    """
    with connect() as conn:
        _ensure_runtime_meta(conn)
        reset = _system_reset_state(conn)
        stored_epoch = _meta_value(conn, EPOCH_ID_KEY)
        stored_started_at = _meta_value(conn, EPOCH_STARTED_AT_KEY)
        stored_reset_token = _meta_value(conn, EPOCH_SOURCE_RESET_TOKEN_KEY)
        stored_mode = _meta_value(conn, EPOCH_BOOTSTRAP_MODE_KEY)
        stored_snapshot = _meta_value(conn, EPOCH_BOOTSTRAP_SNAPSHOT_KEY)

        reset_token = reset.get("token")
        reset_at = reset.get("updatedAt")
        reset_changed = bool(stored_epoch and reset_token and reset_token != stored_reset_token)

        if stored_epoch and stored_started_at and not reset_changed:
            return {
                "version": COMPETITION_HISTORY_EPOCH_VERSION,
                "epochId": stored_epoch,
                "startedAt": stored_started_at,
                "bootstrapMode": stored_mode or "persisted",
                "bootstrapSnapshotId": stored_snapshot,
                "sourceResetToken": stored_reset_token,
                "crossEpochHistoryAllowed": False,
                "archivePreserved": True,
            }

        if reset_changed and reset_at:
            return _persist_epoch(
                conn,
                started_at=str(reset_at),
                reset_token=str(reset_token) if reset_token else None,
                bootstrap_mode="system_demo_reset_boundary",
                snapshot_id=None,
            )

        latest = _latest_canonical_snapshot(conn)
        latest_at = latest.get("createdAt") if latest else None

        # Migration safety: an old reset marker does not prove that every canonical
        # snapshot created after it belongs to one evaluator run. If canonical history
        # already exists when this contract is first installed, begin at the newest
        # snapshot (fail closed) and acknowledge the current reset token. A future reset
        # rotates the epoch normally.
        if latest and latest_at and not _is_at_or_after(conn, reset_at, latest_at):
            return _persist_epoch(
                conn,
                started_at=str(latest_at),
                reset_token=str(reset_token) if reset_token else None,
                bootstrap_mode="legacy_latest_snapshot_fail_closed",
                snapshot_id=str(latest.get("snapshotId") or "") or None,
            )

        if reset_at:
            return _persist_epoch(
                conn,
                started_at=str(reset_at),
                reset_token=str(reset_token) if reset_token else None,
                bootstrap_mode="system_demo_reset_boundary",
                snapshot_id=None,
            )

        if latest and latest_at:
            return _persist_epoch(
                conn,
                started_at=str(latest_at),
                reset_token=None,
                bootstrap_mode="legacy_latest_snapshot_fail_closed",
                snapshot_id=str(latest.get("snapshotId") or "") or None,
            )

        return _persist_epoch(
            conn,
            started_at=datetime.now().isoformat(),
            reset_token=None,
            bootstrap_mode="empty_runtime_start",
            snapshot_id=None,
        )


__all__ = [
    "COMPETITION_HISTORY_EPOCH_VERSION",
    "current_competition_history_epoch",
]
