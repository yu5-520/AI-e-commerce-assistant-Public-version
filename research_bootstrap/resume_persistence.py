from __future__ import annotations

from typing import Any, Callable, Dict

from append_store import AppendOnlyJsonlStore
from core import sha256_json
from run_identity import assert_resume_compatible


class ResumePersistenceError(RuntimeError):
    pass


def _identity_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    identity = record.get("run_identity")
    if not isinstance(identity, dict):
        raise ResumePersistenceError("stored_run_identity_missing")
    return identity


def execute_or_resume(
    *,
    store: AppendOnlyJsonlStore,
    requested_identity: Dict[str, Any],
    execute_fn: Callable[[], Dict[str, Any]],
    resume_from_run_id: str | None = None,
) -> Dict[str, Any]:
    """Execute once, persist immutably, or resume only from an identical run identity.

    A resume attempt names the prior run explicitly. The stored identity must match the
    requested identity component-for-component. When compatible, the stored result is
    returned without invoking execute_fn again. Identity drift therefore cannot silently
    turn a resume into a new empirical run.
    """
    requested_run_id = str(requested_identity.get("run_id") or "").strip()
    if not requested_run_id:
        raise ResumePersistenceError("requested_run_id_missing")

    if resume_from_run_id is not None:
        existing_event = store.get(resume_from_run_id)
        if existing_event is None:
            raise ResumePersistenceError("resume_source_not_found")
        existing_record = existing_event.get("record") or {}
        existing_identity = _identity_from_record(existing_record)
        try:
            assert_resume_compatible(existing_identity, requested_identity)
        except RuntimeError as exc:
            raise ResumePersistenceError(str(exc)) from exc
        if existing_record.get("run_id") != requested_run_id:
            raise ResumePersistenceError("stored_run_id_identity_mismatch")
        return {
            "status": "RESUMED",
            "run_id": requested_run_id,
            "result": existing_record.get("result"),
            "record_hash": existing_event.get("record_hash"),
            "event_hash": existing_event.get("event_hash"),
            "executed": False,
        }

    result = execute_fn()
    if not isinstance(result, dict):
        raise ResumePersistenceError("execution_result_must_be_object")
    record = {
        "run_id": requested_run_id,
        "run_identity": requested_identity,
        "result": result,
        "result_hash": sha256_json(result),
    }
    event = store.append(record)
    return {
        "status": "EXECUTED",
        "run_id": requested_run_id,
        "result": result,
        "record_hash": event.get("record_hash"),
        "event_hash": event.get("event_hash"),
        "executed": True,
    }
