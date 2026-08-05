"""Log and recap candidate module routes."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Query

from src.services.module_task_service import list_logs
from src.services.task_recap_service import list_recap_candidates, recap_summary

router = APIRouter()


@router.get("/log")
def log() -> List[Dict[str, Any]]:
    return list_logs()


@router.get("/recap-candidates")
def recap_candidates(target: str | None = Query(default=None), limit: int = Query(default=50)) -> Dict[str, Any]:
    return {"summary": recap_summary(), "items": list_recap_candidates(target=target, limit=limit)}
