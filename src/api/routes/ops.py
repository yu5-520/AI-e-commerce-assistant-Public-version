"""V21.3.1 Ops Diagnostic Train routes plus V26.9.C explicit Experience governance."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Body, Query, Request, HTTPException

from src.runtime_version import API_VERSION
from src.services.competition_operator_context_service import user_id_from_headers
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


@router.get("/experience/{experience_id}/promotion-gate")
def experience_promotion_gate(experience_id: str) -> Dict[str, Any]:
    """Read-only deterministic C gate; this endpoint never changes lifecycle state."""
    from src.services.v269_promotion_gate_service import evaluate_promotion_gate
    result = evaluate_promotion_gate(experience_id)
    result["routeVersion"] = OPS_ROUTE_VERSION
    return result


@router.get("/experience/{experience_id}/promotion-history")
def experience_promotion_history(experience_id: str) -> Dict[str, Any]:
    from src.services.v269_promotion_gate_service import promotion_history
    result = promotion_history(experience_id)
    result["routeVersion"] = OPS_ROUTE_VERSION
    return result


@router.post("/experience/{experience_id}/promotion-review")
def experience_promotion_review(
    request: Request,
    experience_id: str,
    body: Dict[str, Any] | None = Body(default=None),
) -> Dict[str, Any]:
    """Explicit reviewer action. Approve means candidate->approved, never enabled."""
    from src.services.v269_promotion_gate_service import review_candidate
    payload = body or {}
    result = review_candidate(
        experience_id,
        reviewer_id=request_user_id(request),
        decision=str(payload.get("decision") or ""),
        rationale=str(payload.get("rationale") or ""),
        supersedes_experience_id=(str(payload.get("supersedesExperienceId")) if payload.get("supersedesExperienceId") else None),
    )
    result["routeVersion"] = OPS_ROUTE_VERSION
    return result


@router.post("/experience/{experience_id}/enable")
def experience_enable(
    request: Request,
    experience_id: str,
    body: Dict[str, Any] | None = Body(default=None),
) -> Dict[str, Any]:
    """Second explicit action after review. Intent must be asserted in the request body."""
    from src.services.v269_promotion_gate_service import enable_experience
    payload = body or {}
    result = enable_experience(
        experience_id,
        operator_id=request_user_id(request),
        explicit_operator_intent=payload.get("explicitOperatorIntent") is True,
    )
    result["routeVersion"] = OPS_ROUTE_VERSION
    return result


@router.post("/experience/{experience_id}/disable")
def experience_disable(
    request: Request,
    experience_id: str,
    body: Dict[str, Any] | None = Body(default=None),
) -> Dict[str, Any]:
    """Explicit withdrawal; history remains inspectable but formal retrieval stops immediately."""
    from src.services.v269_promotion_gate_service import disable_experience
    payload = body or {}
    result = disable_experience(
        experience_id,
        operator_id=request_user_id(request),
        reason=str(payload.get("reason") or ""),
        explicit_operator_intent=payload.get("explicitOperatorIntent") is True,
    )
    result["routeVersion"] = OPS_ROUTE_VERSION
    return result


@router.get("/initialization")
def initialization_preview() -> Dict[str, Any]:
    from src.services.v2610_initialization_service import bundle
    value = bundle()
    return {"bundleHash": value["bundleHash"], "profile": value["profile"],
            "operatingUnits": value["operatingUnits"], "methodCount": len(value["methods"]),
            "supplementaryBaselines": value["supplementaryBaselines"],
            "evaluationStandards": value["evaluationStandards"],
            "evaluationStandardsHash": value["evaluationStandardsHash"],
            "automaticEnable": False, "recomputedOnRead": False}


@router.post("/initialization")
def initialize_business(request: Request, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    from fastapi import HTTPException
    from src.services.v2610_initialization_service import initialize_bundle, bundle
    payload = body or {}
    if payload.get("explicitOperatorIntent") is not True or payload.get("bundleHash") != bundle()["bundleHash"]:
        raise HTTPException(status_code=409, detail="initialization_requires_explicit_intent_and_exact_bundle")
    result = initialize_bundle()
    result["requestedBy"] = request_user_id(request)
    return result


@router.get('/tasks/{task_id}/steps')
def step_workspace(task_id: str, request: Request):
    from src.services.v2611_step_workspace_service import read_workspace
    from fastapi.responses import JSONResponse, Response
    try:
        view=read_workspace(task_id)
        etag='"'+view["headHash"]+'"'
        headers={"ETag":etag,"Cache-Control":"private, no-cache"}
        if request.headers.get("if-none-match")==etag:return Response(status_code=304,headers=headers)
        return JSONResponse(view,headers=headers)
    except ValueError as exc:raise HTTPException(status_code=404,detail=str(exc))


@router.post('/tasks/{task_id}/steps')
def step_submit(task_id: str, request: Request, body: Dict[str, Any] = Body(...)):
    from src.services.v2611_step_workspace_service import submit_step
    try:return submit_step(task_id,body,request_user_id(request))
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc))


@router.get('/tasks/{task_id}/steps/attachment')
def step_attachment(task_id: str, recordHash: str, contentHash: str):
    import base64
    from fastapi.responses import Response
    from src.services.v2611_step_workspace_service import attachment
    try:a=attachment(task_id,recordHash,contentHash)
    except ValueError as exc:raise HTTPException(status_code=404,detail=str(exc))
    return Response(base64.b64decode(a['base64']),media_type='application/octet-stream',headers={'Content-Disposition':'attachment','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.post('/tasks/{task_id}/steps/review')
def step_review(task_id: str, request: Request, body: Dict[str, Any] = Body(...)):
    from src.services.v2611_step_workspace_service import review_step
    try:return review_step(task_id,body,request_user_id(request))
    except ValueError as exc:raise HTTPException(status_code=409,detail=str(exc))


@router.get('/tasks/{task_id}/steps/content/{content_hash}')
def step_content(task_id: str, content_hash: str):
    from fastapi.responses import JSONResponse
    from src.services.v2611_step_workspace_service import read_content
    try:payload=read_content(task_id,content_hash)
    except ValueError as exc:raise HTTPException(status_code=404,detail=str(exc))
    return JSONResponse(payload,headers={'Cache-Control':'private, max-age=31536000, immutable','ETag':'"'+content_hash+'"'})


@router.get('/experience-overview')
def experience_overview(request: Request):
    from src.services.v269_experience_store_service import read_experience_overview
    from fastapi.responses import JSONResponse, Response
    body = read_experience_overview()
    etag = '"'+body['contentHash']+'"'
    headers = {'ETag':etag,'Cache-Control':'private, no-cache'}
    if request.headers.get('if-none-match') == etag:
        return Response(status_code=304,headers=headers)
    return JSONResponse(body,headers=headers)
