"""Repeatability fingerprint for the competition task set.

The fingerprint intentionally ignores run/execution identity. It is used only to prove
that the same business inputs and contracts produce the same business task set after a
clean Runtime Generation reset.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect, loads

REPEATABILITY_CONTRACT_VERSION = "1.0.0"
REPEATABILITY_SCHEMA = "competition.task_set_semantic_hash.v1"

_EXCLUDED_KEYS = {
    "id",
    "taskId",
    "task_id",
    "dataVersion",
    "data_version",
    "executionHash",
    "ExecutionHash",
    "itemExecutionId",
    "inputContentHash",
    "outputContentHash",
    "artifactRefs",
    "taskRef",
    "createdAt",
    "updatedAt",
    "created_at",
    "updated_at",
    "admittedAt",
    "submittedAt",
    "reviewedAt",
    "correlationId",
    "signalId",
    "signal_id",
    "packageId",
    "package_id",
}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def semantic_projection(value: Any) -> Any:
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            if name in _EXCLUDED_KEYS:
                continue
            projected = semantic_projection(child)
            if projected in (None, "", [], {}) and child not in (0, False):
                continue
            result[name] = projected
        return result
    if isinstance(value, list):
        projected = [semantic_projection(item) for item in value]
        return sorted(projected, key=_stable_json)
    return value


def _table_exists(conn: Any, table_name: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table_name,),
        ).fetchone()
    )


def _task_pool_payloads(data_version: str | None = None) -> List[Dict[str, Any]]:
    with connect() as conn:
        if not _table_exists(conn, "task_pool_entries"):
            return []
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(task_pool_entries)").fetchall()
        }
        where = ""
        params: List[Any] = []
        if data_version and "data_version" in columns:
            where = " WHERE data_version=?"
            params.append(data_version)
        order_col = "updated_at" if "updated_at" in columns else "rowid"
        rows = conn.execute(
            f"SELECT * FROM task_pool_entries{where} ORDER BY {order_col} ASC",
            tuple(params),
        ).fetchall()

    result: List[Dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        payload: Any = row.get("payload")
        if isinstance(payload, str):
            try:
                payload = loads(payload)
            except Exception:
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = {}
        if not isinstance(payload, dict):
            payload = {}
        merged = {**row, **payload}
        result.append(semantic_projection(merged))
    return result


def task_set_semantic_hash(
    *,
    data_version: str | None = None,
    tasks: Iterable[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    items = (
        [semantic_projection(item) for item in tasks if isinstance(item, dict)]
        if tasks is not None
        else _task_pool_payloads(data_version=data_version)
    )
    items = sorted(items, key=_stable_json)
    digest = _sha256(
        {
            "schema": REPEATABILITY_SCHEMA,
            "version": REPEATABILITY_CONTRACT_VERSION,
            "tasks": items,
        }
    )
    return {
        "schema": REPEATABILITY_SCHEMA,
        "version": REPEATABILITY_CONTRACT_VERSION,
        "dataVersion": data_version,
        "taskCount": len(items),
        "taskSetSemanticHash": digest,
        "identityExcluded": sorted(_EXCLUDED_KEYS),
        "taskSemantics": items,
        "rule": "Task count and semantic hash must both match across clean runs of the same three reports.",
    }


def compare_repeatability(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    equal_count = int(left.get("taskCount") or 0) == int(right.get("taskCount") or 0)
    equal_hash = str(left.get("taskSetSemanticHash") or "") == str(
        right.get("taskSetSemanticHash") or ""
    )
    return {
        "version": REPEATABILITY_CONTRACT_VERSION,
        "passed": bool(equal_count and equal_hash),
        "taskCountMatch": equal_count,
        "taskSetSemanticHashMatch": equal_hash,
        "left": {
            "taskCount": left.get("taskCount"),
            "taskSetSemanticHash": left.get("taskSetSemanticHash"),
        },
        "right": {
            "taskCount": right.get("taskCount"),
            "taskSetSemanticHash": right.get("taskSetSemanticHash"),
        },
    }


__all__ = [
    "REPEATABILITY_CONTRACT_VERSION",
    "REPEATABILITY_SCHEMA",
    "semantic_projection",
    "task_set_semantic_hash",
    "compare_repeatability",
]
