"""V22 station interfaces for the single governed runtime."""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query, Request

from src.runtime_version import VERSION
from src.services.competition_operator_context_service import user_id_from_headers
from src.services.agent_pipeline_governance_v213_service import runtime_governance_summary
from src.services.pipeline_item_service import pipeline_item_summary
from src.services.station_contract_service import (
    list_station_contracts,
    run_station_contract,
    station_contract,
    station_gates,
    station_health,
)
from src.services.station_queue_service import queue_summary
from src.services.station_registry_service import get_station, registry_summary

router = APIRouter(prefix="/api/stations", tags=["stations"])


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


def _meta(result: Dict[str, Any]) -> Dict[str, Any]:
    result["version"] = VERSION
    result["contractVersion"] = VERSION
    result["interfaceVersion"] = VERSION
    result["runtimeMode"] = "single_v22_runtime"
    return result


@router.get("")
def list_station_interfaces() -> Dict[str, Any]:
    contracts = list_station_contracts()
    return {
        "version": VERSION,
        "contractVersion": VERSION,
        "interfaceVersion": VERSION,
        "runtimeMode": "single_v22_runtime",
        "runtimeGovernance": runtime_governance_summary(),
        "registry": registry_summary(),
        "contracts": contracts.get("contracts"),
        "canonicalActionField": "activeActionContract",
        "fallbackAllowed": False,
        "rule": "V22 exposes one strict Station registry and one pipeline-item runtime.",
    }


@router.get("/queue-summary")
def station_queue_summary_endpoint(
    data_version: str | None = Query(default=None, alias="dataVersion"),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    return _meta(queue_summary(data_version=data_version, limit=limit))


@router.get("/pipeline-items")
def pipeline_items_endpoint(
    data_version: str | None = Query(default=None, alias="dataVersion"),
    limit: int = Query(default=50, ge=1, le=500),
) -> Dict[str, Any]:
    return _meta(pipeline_item_summary(data_version=data_version, limit=limit))


@router.get("/{station_id}")
def station_detail(station_id: str) -> Dict[str, Any]:
    station = get_station(station_id)
    if not station:
        raise HTTPException(status_code=404, detail="station not found")
    return _meta(
        {
            "station": station,
            "contract": station_contract(station_id),
            "health": station_health(station_id),
        }
    )


@router.get("/{station_id}/contract")
def station_contract_endpoint(station_id: str) -> Dict[str, Any]:
    contract = station_contract(station_id)
    if not contract.get("ok"):
        raise HTTPException(status_code=404, detail="station not found")
    return _meta(contract)


@router.get("/{station_id}/health")
def station_health_endpoint(station_id: str) -> Dict[str, Any]:
    result = station_health(station_id)
    if result.get("status") == "failed":
        raise HTTPException(status_code=404, detail="station not found")
    return _meta(result)


@router.get("/{station_id}/gates")
def station_gates_endpoint(
    station_id: str,
    data_version: str | None = Query(default=None, alias="dataVersion"),
    limit: int = Query(default=40, ge=1, le=200),
    include_diagnostic: bool = Query(default=False, alias="includeDiagnostic"),
) -> Dict[str, Any]:
    return _meta(
        station_gates(
            station_id,
            data_version=data_version,
            limit=limit,
            include_diagnostic=include_diagnostic,
        )
    )


@router.get("/{station_id}/latest")
def station_latest_endpoint(
    station_id: str,
    data_version: str | None = Query(default=None, alias="dataVersion"),
    include_diagnostic: bool = Query(default=False, alias="includeDiagnostic"),
) -> Dict[str, Any]:
    gates = station_gates(
        station_id,
        data_version=data_version,
        limit=1,
        include_diagnostic=include_diagnostic,
    )
    return _meta(
        {
            "stationId": station_id,
            "latest": (gates.get("gates") or [None])[0],
            "gateCount": gates.get("gateCount", 0),
            "includeDiagnostic": include_diagnostic,
        }
    )


@router.post("/{station_id}/run")
def run_station_endpoint(
    request: Request,
    station_id: str,
    body: Dict[str, Any] | None = Body(default=None),
) -> Dict[str, Any]:
    payload = body or {}
    payload.setdefault("userId", request_user_id(request))
    result = run_station_contract(
        station_id,
        payload,
        diagnostic=bool(payload.get("isDiagnostic")),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    return _meta(result)


@router.post("/{station_id}/replay")
def replay_station_endpoint(
    request: Request,
    station_id: str,
    body: Dict[str, Any] | None = Body(default=None),
) -> Dict[str, Any]:
    payload = body or {}
    payload.setdefault("userId", request_user_id(request))
    payload.setdefault("replay", True)
    result = run_station_contract(
        station_id,
        payload,
        diagnostic=bool(payload.get("isDiagnostic")),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    result["replayMode"] = True
    result["rule"] = "Replay reruns the same V22 contract and cannot change the locked action family."
    return _meta(result)
