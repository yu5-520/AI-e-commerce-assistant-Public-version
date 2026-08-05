"""Task evidence repository service.

V12.11.3 hotfix: keep this module dependency-free except for Python stdlib
and existing uid helpers. V12.11.2 imported `src.services.json_store`, but that
module does not exist in this repository, so FastAPI import failed and ECS kept
returning 502.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.services.uid import make_id

EVIDENCE_REPOSITORY_VERSION = "12.11.3"
DATA_DIR = Path("data/runtime_task_evidence")
DATA_FILE = DATA_DIR / "submissions.json"


def _read_records() -> list[Dict[str, Any]]:
    if not DATA_FILE.exists():
        return []
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_records(records: list[Dict[str, Any]]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def persist_evidence_submission(task: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    """Persist an evidence submission audit copy without changing task status."""
    audit = {
        "version": EVIDENCE_REPOSITORY_VERSION,
        "auditId": make_id("EAUDIT"),
        "taskId": task.get("id") or task.get("taskId") or record.get("taskId"),
        "taskTitle": task.get("title") or task.get("productTitle"),
        "status": task.get("status"),
        "workflowStatus": task.get("workflowStatus"),
        "recordId": record.get("id"),
        "submittedById": record.get("submittedById"),
        "submittedByName": record.get("submittedByName"),
        "submittedAt": record.get("submittedAt"),
        "summary": record.get("summary"),
        "operatorManualRecapRequired": False,
        "systemAutoRecap": True,
    }
    records = _read_records()
    records.insert(0, audit)
    _write_records(records[:500])
    return audit


def list_evidence_submissions(limit: int = 50) -> Dict[str, Any]:
    records = _read_records()[:limit]
    return {"version": EVIDENCE_REPOSITORY_VERSION, "count": len(records), "items": records}
