"""V13.3 Task Snapshot Station routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.services.account_service import user_id_from_headers
from src.services.task_snapshot_station_service import create_task_snapshot, get_task_snapshot, list_task_snapshots, task_snapshot_summary

router = APIRouter(prefix="/api/task-snapshots", tags=["task-snapshots"])
TASK_SNAPSHOT_ROUTE_VERSION = "13.3.0"


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


@router.get("")
def task_snapshots(dataVersion: str | None = Query(default=None), handoffId: str | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200)) -> Dict[str, Any]:
    result = list_task_snapshots(data_version=dataVersion, handoff_id=handoffId, limit=limit)
    result["routeVersion"] = TASK_SNAPSHOT_ROUTE_VERSION
    return result


@router.get("/summary")
def snapshots_summary(limit: int = Query(default=50, ge=1, le=200)) -> Dict[str, Any]:
    result = task_snapshot_summary(limit=limit)
    result["routeVersion"] = TASK_SNAPSHOT_ROUTE_VERSION
    return result


@router.post("")
def create_snapshot(request: Request, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    result = create_task_snapshot(body or {}, created_by=request_user_id(request))
    result["routeVersion"] = TASK_SNAPSHOT_ROUTE_VERSION
    return result


@router.get("/{task_snapshot_id}")
def snapshot_detail(task_snapshot_id: str) -> Dict[str, Any]:
    item = get_task_snapshot(task_snapshot_id)
    if not item:
        raise HTTPException(status_code=404, detail="task snapshot not found")
    return {"version": TASK_SNAPSHOT_ROUTE_VERSION, "snapshot": item}
