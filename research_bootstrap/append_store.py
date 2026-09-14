from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from core import sha256_json


class AppendStoreError(RuntimeError):
    pass


class AppendOnlyJsonlStore:
    """Append-only, hash-chained JSONL evidence store for research runs.

    The implementation never rewrites prior records. External file mutation is detected
    by verify(); OS/filesystem policy is still required for stronger immutability.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_locked(self, handle) -> List[Dict[str, Any]]:
        handle.seek(0)
        records = []
        for line_no, line in enumerate(handle.read().splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AppendStoreError(f"invalid_jsonl:{line_no}") from exc
            if not isinstance(item, dict):
                raise AppendStoreError(f"invalid_event:{line_no}")
            records.append(item)
        return records

    def append(self, record: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(record.get("run_id") or "").strip()
        if not run_id:
            raise AppendStoreError("run_id_required")
        self.path.touch(exist_ok=True)
        with self.path.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            events = self._read_locked(handle)
            self._verify_events(events)
            record_hash = sha256_json(record)
            for event in events:
                if event.get("record", {}).get("run_id") == run_id:
                    if event.get("record_hash") != record_hash:
                        raise AppendStoreError("run_id_conflict")
                    return event
            prev_hash = events[-1]["event_hash"] if events else None
            body = {
                "sequence": len(events) + 1,
                "prev_event_hash": prev_hash,
                "record_hash": record_hash,
                "record": record,
            }
            event = {**body, "event_hash": sha256_json(body)}
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
            return event

    def get(self, run_id: str) -> Dict[str, Any] | None:
        run_id = str(run_id or "").strip()
        if not run_id or not self.path.exists():
            return None
        with self.path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            events = self._read_locked(handle)
        self._verify_events(events)
        for event in events:
            if event.get("record", {}).get("run_id") == run_id:
                return event
        return None

    def verify(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"status": "PASS", "events": 0, "head_event_hash": None}
        with self.path.open("r", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH)
            events = self._read_locked(handle)
        self._verify_events(events)
        return {
            "status": "PASS",
            "events": len(events),
            "head_event_hash": events[-1]["event_hash"] if events else None,
        }

    @staticmethod
    def _verify_events(events: List[Dict[str, Any]]) -> None:
        prev_hash = None
        seen = {}
        for expected_seq, event in enumerate(events, 1):
            if event.get("sequence") != expected_seq:
                raise AppendStoreError(f"sequence_mismatch:{expected_seq}")
            if event.get("prev_event_hash") != prev_hash:
                raise AppendStoreError(f"chain_mismatch:{expected_seq}")
            record = event.get("record")
            if not isinstance(record, dict):
                raise AppendStoreError(f"record_missing:{expected_seq}")
            record_hash = sha256_json(record)
            if event.get("record_hash") != record_hash:
                raise AppendStoreError(f"record_hash_mismatch:{expected_seq}")
            body = {
                "sequence": event.get("sequence"),
                "prev_event_hash": event.get("prev_event_hash"),
                "record_hash": event.get("record_hash"),
                "record": record,
            }
            expected_event_hash = sha256_json(body)
            if event.get("event_hash") != expected_event_hash:
                raise AppendStoreError(f"event_hash_mismatch:{expected_seq}")
            run_id = str(record.get("run_id") or "")
            if run_id in seen and seen[run_id] != record_hash:
                raise AppendStoreError(f"duplicate_run_conflict:{run_id}")
            seen[run_id] = record_hash
            prev_hash = expected_event_hash
