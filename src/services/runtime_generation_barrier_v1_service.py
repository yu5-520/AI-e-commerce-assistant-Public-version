"""Runtime generation barrier for repeatable competition resets.

The competition runtime keeps immutable Artifact/semantic-cache/audit history across
resets, but only one runtime generation may own mutable pipeline/task/view state.
A process-wide re-entrant barrier serializes complete worker iterations against reset.
The generation identity is persisted in ``runtime_meta`` so an application restart does
not silently reopen an older generation.

This is intentionally a sibling identity to Evidence/ExecutionHash. It does not change
any existing strict hash definition.
"""
from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List

from src.repositories.sqlite_repository import connect

RUNTIME_GENERATION_VERSION = "1.0.0"
RUNTIME_GENERATION_SCHEMA = "runtime.generation.barrier.v1"
ROOT_DIR = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT_DIR / "config" / "runtime_generation_lineage_registry_v1.json"

_EXECUTION_BARRIER = threading.RLock()

_META_SEQ = "runtime_generation_seq"
_META_HASH = "runtime_generation_hash"
_META_STATE = "runtime_generation_state"
_META_REASON = "runtime_generation_reason"
_META_STARTED_AT = "runtime_generation_started_at"
_META_ACTIVE_DATA_VERSION = "runtime_generation_active_data_version"
_META_REGISTRY_VERSION = "runtime_generation_registry_version"

_ALLOWED_EXECUTION_STATES = {"active", "empty"}


def _now() -> str:
    return datetime.now().isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _ensure_runtime_meta(conn: Any) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS runtime_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )


def _meta_get(conn: Any, key: str) -> str | None:
    _ensure_runtime_meta(conn)
    row = conn.execute("SELECT value FROM runtime_meta WHERE key=?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def _meta_set(conn: Any, key: str, value: Any) -> None:
    _ensure_runtime_meta(conn)
    conn.execute(
        """INSERT OR REPLACE INTO runtime_meta(key,value,updated_at)
           VALUES (?,?,CURRENT_TIMESTAMP)""",
        (key, "" if value is None else str(value)),
    )


def load_runtime_generation_registry() -> Dict[str, Any]:
    value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if value.get("schema") != "runtime.generation_lineage.registry.v1":
        raise RuntimeError("runtime_generation_registry_schema_invalid")
    if value.get("mode") != "fail_closed":
        raise RuntimeError("runtime_generation_registry_must_fail_closed")
    if value.get("rootRegistry") != "config/runtime_contract_lineage_registry_v1.json":
        raise RuntimeError("runtime_generation_registry_root_mismatch")
    fields = value.get("fields") or {}
    required = {
        "runtime.generation_seq",
        "runtime.generation_hash",
        "runtime.reset_state",
        "runtime.active_data_version",
        "runtime.claim_generation_hash",
        "runtime.commit_generation_hash",
        "runtime.stale_generation_reason",
        "verification.task_set_semantic_hash",
    }
    missing = sorted(required.difference(fields))
    if missing:
        raise RuntimeError(f"runtime_generation_registry_fields_missing:{','.join(missing)}")
    return value


def reset_policy_summary() -> Dict[str, Any]:
    registry = load_runtime_generation_registry()
    policies = registry.get("resetPolicies") or {}
    return {
        "schema": registry.get("schema"),
        "version": registry.get("version"),
        "mode": registry.get("mode"),
        "categories": {
            key: {
                "action": value.get("action"),
                "tableCount": len(value.get("tables") or []),
                "tables": list(value.get("tables") or []),
            }
            for key, value in policies.items()
            if isinstance(value, dict)
        },
    }


def registered_reset_table_names() -> List[str]:
    registry = load_runtime_generation_registry()
    policy = (registry.get("resetPolicies") or {}).get("current_runtime_ephemeral") or {}
    if policy.get("action") not in {"delete", "invalidate"}:
        raise RuntimeError("runtime_generation_ephemeral_reset_policy_invalid")
    tables = [str(item).strip() for item in policy.get("tables") or [] if str(item).strip()]
    if not tables:
        raise RuntimeError("runtime_generation_reset_tables_missing")
    if len(tables) != len(set(tables)):
        raise RuntimeError("runtime_generation_reset_tables_duplicate")
    return tables


def _release_identity_material() -> Dict[str, Any]:
    try:
        from src.services.release_identity_service import release_identity

        identity = release_identity(verify_content=False)
    except Exception:
        identity = {}
    return {
        "sourceCommit": identity.get("sourceCommit"),
        "releaseHash": identity.get("releaseHash"),
    }


def _generation_hash(seq: int) -> str:
    registry = load_runtime_generation_registry()
    return _sha256(
        {
            "schema": RUNTIME_GENERATION_SCHEMA,
            "version": RUNTIME_GENERATION_VERSION,
            "registryVersion": registry.get("version"),
            "sequence": int(seq),
            "release": _release_identity_material(),
        }
    )


def ensure_runtime_generation_state() -> Dict[str, Any]:
    registry = load_runtime_generation_registry()
    with connect() as conn:
        _ensure_runtime_meta(conn)
        raw_seq = _meta_get(conn, _META_SEQ)
        try:
            seq = max(1, int(raw_seq or "1"))
        except Exception:
            seq = 1
        generation_hash = _meta_get(conn, _META_HASH)
        state = (_meta_get(conn, _META_STATE) or "active").strip().lower()
        started_at = _meta_get(conn, _META_STARTED_AT) or _now()
        if not generation_hash:
            generation_hash = _generation_hash(seq)
        _meta_set(conn, _META_SEQ, seq)
        _meta_set(conn, _META_HASH, generation_hash)
        _meta_set(conn, _META_STATE, state)
        _meta_set(conn, _META_STARTED_AT, started_at)
        _meta_set(conn, _META_REGISTRY_VERSION, registry.get("version"))
        conn.commit()
    return current_runtime_generation(ensure=False)


def current_runtime_generation(*, ensure: bool = True) -> Dict[str, Any]:
    if ensure:
        ensure_runtime_generation_state()
    with connect() as conn:
        _ensure_runtime_meta(conn)
        raw_seq = _meta_get(conn, _META_SEQ) or "1"
        try:
            seq = int(raw_seq)
        except Exception:
            seq = 1
        return {
            "schema": RUNTIME_GENERATION_SCHEMA,
            "version": RUNTIME_GENERATION_VERSION,
            "generationSeq": seq,
            "generationHash": _meta_get(conn, _META_HASH),
            "state": (_meta_get(conn, _META_STATE) or "active").strip().lower(),
            "reason": _meta_get(conn, _META_REASON),
            "startedAt": _meta_get(conn, _META_STARTED_AT),
            "activeDataVersion": _meta_get(conn, _META_ACTIVE_DATA_VERSION) or None,
            "registryVersion": _meta_get(conn, _META_REGISTRY_VERSION),
            "writeAuthority": "active_generation_only",
        }


def _set_generation_state(
    *,
    seq: int,
    generation_hash: str,
    state: str,
    reason: str,
    active_data_version: str | None,
    started_at: str | None = None,
) -> Dict[str, Any]:
    with connect() as conn:
        _meta_set(conn, _META_SEQ, int(seq))
        _meta_set(conn, _META_HASH, generation_hash)
        _meta_set(conn, _META_STATE, state)
        _meta_set(conn, _META_REASON, reason)
        _meta_set(conn, _META_STARTED_AT, started_at or _now())
        _meta_set(conn, _META_ACTIVE_DATA_VERSION, active_data_version or "")
        _meta_set(conn, _META_REGISTRY_VERSION, load_runtime_generation_registry().get("version"))
        conn.commit()
    return current_runtime_generation(ensure=False)


def begin_runtime_reset(*, reason: str, scope: str) -> Dict[str, Any]:
    previous = ensure_runtime_generation_state()
    next_seq = int(previous.get("generationSeq") or 0) + 1
    next_hash = _generation_hash(next_seq)
    current = _set_generation_state(
        seq=next_seq,
        generation_hash=next_hash,
        state="resetting",
        reason=f"{reason}|scope={scope}",
        active_data_version=None,
    )
    return {
        "previousGeneration": previous,
        "currentGeneration": current,
        "barrier": "exclusive_process_generation_reset",
    }


def finalize_runtime_reset(*, reason: str = "reset_complete") -> Dict[str, Any]:
    current = current_runtime_generation()
    return _set_generation_state(
        seq=int(current.get("generationSeq") or 1),
        generation_hash=str(current.get("generationHash") or _generation_hash(1)),
        state="empty",
        reason=reason,
        active_data_version=None,
        started_at=str(current.get("startedAt") or _now()),
    )


def fail_runtime_reset(reason: str) -> Dict[str, Any]:
    current = current_runtime_generation()
    return _set_generation_state(
        seq=int(current.get("generationSeq") or 1),
        generation_hash=str(current.get("generationHash") or _generation_hash(1)),
        state="failed",
        reason=reason,
        active_data_version=None,
        started_at=str(current.get("startedAt") or _now()),
    )


def mark_runtime_generation_active(data_version: str | None) -> Dict[str, Any]:
    current = current_runtime_generation()
    if current.get("state") == "failed":
        raise RuntimeError("runtime_generation_failed_closed")
    if not data_version:
        return current
    return _set_generation_state(
        seq=int(current.get("generationSeq") or 1),
        generation_hash=str(current.get("generationHash") or _generation_hash(1)),
        state="active",
        reason="active_data_version_observed",
        active_data_version=str(data_version),
        started_at=str(current.get("startedAt") or _now()),
    )


def assert_generation_current(expected_hash: str) -> Dict[str, Any]:
    current = current_runtime_generation()
    if current.get("state") not in _ALLOWED_EXECUTION_STATES:
        raise RuntimeError(f"runtime_generation_not_writable:{current.get('state')}")
    if str(current.get("generationHash") or "") != str(expected_hash or ""):
        raise RuntimeError("stale_runtime_generation")
    return current


@contextmanager
def runtime_execution_guard(owner: str) -> Iterator[Dict[str, Any]]:
    """Serialize one complete Worker iteration against Reset.

    The worker holds this guard across Provider calls and persistence. Reset waits for
    the iteration to finish before rotating the generation and deleting mutable state.
    """
    with _EXECUTION_BARRIER:
        claim = ensure_runtime_generation_state()
        if claim.get("state") not in _ALLOWED_EXECUTION_STATES:
            raise RuntimeError(f"runtime_generation_not_writable:{claim.get('state')}")
        claim_hash = str(claim.get("generationHash") or "")
        envelope = {
            **claim,
            "owner": owner,
            "claimGenerationHash": claim_hash,
        }
        yield envelope
        commit = assert_generation_current(claim_hash)
        envelope["commitGenerationHash"] = commit.get("generationHash")
        envelope["generationMatch"] = True


@contextmanager
def runtime_reset_barrier(*, reason: str, scope: str) -> Iterator[Dict[str, Any]]:
    """Acquire exclusive runtime ownership, rotate generation, then reset atomically.

    Mutable-table deletion is performed by the caller while this lock is held.
    Historical Artifact/semantic-cache/canonical archives are intentionally outside
    the reset policy.
    """
    with _EXECUTION_BARRIER:
        reset = begin_runtime_reset(reason=reason, scope=scope)
        try:
            yield reset
        except Exception as exc:
            fail_runtime_reset(f"reset_failed:{type(exc).__name__}:{str(exc)[:300]}")
            raise
        else:
            reset["currentGeneration"] = finalize_runtime_reset(
                reason=f"{reason}|scope={scope}|empty"
            )


__all__ = [
    "RUNTIME_GENERATION_VERSION",
    "RUNTIME_GENERATION_SCHEMA",
    "load_runtime_generation_registry",
    "reset_policy_summary",
    "registered_reset_table_names",
    "ensure_runtime_generation_state",
    "current_runtime_generation",
    "begin_runtime_reset",
    "finalize_runtime_reset",
    "fail_runtime_reset",
    "mark_runtime_generation_active",
    "assert_generation_current",
    "runtime_execution_guard",
    "runtime_reset_barrier",
]
