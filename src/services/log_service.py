"""Lightweight product log service.

The MVP uses JSONL files for readable audit trail and SQLite for queryable
product state. This is a transitional persistence layer before a full database
schema is introduced.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from src.repositories.sqlite_repository import (
    insert_execution_log,
    list_execution_logs as list_sqlite_execution_logs,
    list_execution_logs_by_run as list_sqlite_execution_logs_by_run,
    list_workflow_runs as list_sqlite_workflow_runs,
    upsert_workflow_run,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT_DIR / "logs"
WORKFLOW_RUN_LOG_PATH = LOG_DIR / "workflow_runs.jsonl"
EXECUTION_LOG_PATH = LOG_DIR / "execution_logs.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def read_jsonl(path: Path, limit: int = 50) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines if line.strip()]
    return records[-limit:][::-1]


def create_workflow_run(
    workflow_type: str,
    status: str = "running",
    input_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run = {
        "workflow_run_id": f"RUN_{uuid4().hex[:10]}",
        "workflow_type": workflow_type,
        "status": status,
        "input_snapshot": input_snapshot or {},
        "started_at": now_iso(),
        "finished_at": None,
        "error_message": None,
    }
    append_jsonl(WORKFLOW_RUN_LOG_PATH, run)
    upsert_workflow_run(run)
    return run


def finish_workflow_run(
    workflow_run_id: str,
    workflow_type: str,
    status: str,
    output_snapshot: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    run = {
        "workflow_run_id": workflow_run_id,
        "workflow_type": workflow_type,
        "status": status,
        "output_snapshot": output_snapshot or {},
        "started_at": None,
        "finished_at": now_iso(),
        "error_message": error_message,
    }
    append_jsonl(WORKFLOW_RUN_LOG_PATH, run)
    upsert_workflow_run(run)
    return run


def create_execution_log(
    workflow_run_id: str,
    node_name: str,
    status: str,
    input_snapshot: Optional[Dict[str, Any]] = None,
    output_snapshot: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    log = {
        "log_id": f"LOG_{uuid4().hex[:10]}",
        "workflow_run_id": workflow_run_id,
        "node_name": node_name,
        "status": status,
        "input_snapshot": input_snapshot or {},
        "output_snapshot": output_snapshot or {},
        "error_message": error_message,
        "created_at": now_iso(),
    }
    append_jsonl(EXECUTION_LOG_PATH, log)
    insert_execution_log(log)
    return log


def list_workflow_runs(limit: int = 50) -> List[Dict[str, Any]]:
    sqlite_rows = list_sqlite_workflow_runs(limit=limit)
    if sqlite_rows:
        return sqlite_rows
    return read_jsonl(WORKFLOW_RUN_LOG_PATH, limit=limit)


def list_execution_logs(limit: int = 100) -> List[Dict[str, Any]]:
    sqlite_rows = list_sqlite_execution_logs(limit=limit)
    if sqlite_rows:
        return sqlite_rows
    return read_jsonl(EXECUTION_LOG_PATH, limit=limit)


def list_execution_logs_by_run(workflow_run_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    sqlite_rows = list_sqlite_execution_logs_by_run(workflow_run_id=workflow_run_id, limit=limit)
    if sqlite_rows:
        return sqlite_rows
    logs = read_jsonl(EXECUTION_LOG_PATH, limit=limit * 5)
    return [item for item in logs if item.get("workflow_run_id") == workflow_run_id][:limit]
