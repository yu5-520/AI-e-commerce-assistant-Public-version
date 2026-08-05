"""V13.4 Task Pool Station routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Query, Request

from src.services.account_service import user_id_from_headers
from src.services.task_pool_station_service import enter_task_pool_from_snapshot, list_task_pool_entries, sync_ready_task_snapshots, task_pool_summary

router = APIRouter(prefix="/api/task-pool", tags=["task-pool"])
TASK_POOL_ROUTE_VERSION = "13.4.0"


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


@router.get("")
def task_pool_entries(limit: int = Query(default=80, ge=1, le=200)) -> Dict[str, Any]:
    result = list_task_pool_entries(limit=limit)
    result["routeVersion"] = TASK_POOL_ROUTE_VERSION
    return result


@router.get("/summary")
def pool_summary(limit: int = Query(default=80, ge=1, le=200)) -> Dict[str, Any]:
    result = task_pool_summary(limit=limit)
    result["routeVersion"] = TASK_POOL_ROUTE_VERSION
    return result


@router.post("/from-snapshot/{task_snapshot_id}")
def enter_from_snapshot(request: Request, task_snapshot_id: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    result = enter_task_pool_from_snapshot(task_snapshot_id, created_by=request_user_id(request), force=bool(body.get("force")))
    result["routeVersion"] = TASK_POOL_ROUTE_VERSION
    return result


@router.post("/sync")
def sync_pool(request: Request, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    result = sync_ready_task_snapshots(data_version=body.get("dataVersion") or body.get("data_version"), limit=int(body.get("limit") or 50), created_by=request_user_id(request))
    result["routeVersion"] = TASK_POOL_ROUTE_VERSION
    return result
