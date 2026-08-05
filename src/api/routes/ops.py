"""V21.3.1 Ops Diagnostic Train routes."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Query, Request

from src.runtime_version import API_VERSION
from src.services.account_service import user_id_from_headers
from src.services.ops_diagnostic_train_service import (
    OPS_DIAGNOSTIC_TRAIN_VERSION,
    check_single_station,
    get_ops_run,
    latest_ops_train,
    list_ops_runs,
    run_ops_train,
    station_health_summary,
)

router = APIRouter(prefix="/api/ops", tags=["ops"])
OPS_ROUTE_VERSION = API_VERSION


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


@router.post("/train/run")
def run_train(request: Request, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    payload = body or {}
    result = run_ops_train(mode=payload.get("mode") or "contract", created_by=request_user_id(request))
    result["routeVersion"] = OPS_ROUTE_VERSION
    result["diagnosticTrainVersion"] = OPS_DIAGNOSTIC_TRAIN_VERSION
    return result


@router.get("/train/latest")
def latest_train() -> Dict[str, Any]:
    result = latest_ops_train()
    result["routeVersion"] = OPS_ROUTE_VERSION
    result["diagnosticTrainVersion"] = OPS_DIAGNOSTIC_TRAIN_VERSION
    return result


@router.get("/train/runs")
def train_runs(limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    result = list_ops_runs(limit=limit)
    result["routeVersion"] = OPS_ROUTE_VERSION
    result["diagnosticTrainVersion"] = OPS_DIAGNOSTIC_TRAIN_VERSION
    return result


@router.get("/train/runs/{run_id}")
def train_run_detail(run_id: str) -> Dict[str, Any]:
    result = get_ops_run(run_id)
    result["routeVersion"] = OPS_ROUTE_VERSION
    result["diagnosticTrainVersion"] = OPS_DIAGNOSTIC_TRAIN_VERSION
    return result


@router.get("/stations/health")
def ops_station_health() -> Dict[str, Any]:
    result = station_health_summary()
    result["routeVersion"] = OPS_ROUTE_VERSION
    result["diagnosticTrainVersion"] = OPS_DIAGNOSTIC_TRAIN_VERSION
    return result


@router.post("/stations/{station_id}/check")
def ops_station_check(request: Request, station_id: str) -> Dict[str, Any]:
    result = check_single_station(station_id, created_by=request_user_id(request))
    result["routeVersion"] = OPS_ROUTE_VERSION
    result["diagnosticTrainVersion"] = OPS_DIAGNOSTIC_TRAIN_VERSION
    return result
