from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Query

from src.repositories.sqlite_repository import connect, loads
from src.runtime_version import THREE_AGENT_PIPELINE_VERSION, VERSION
from src.services.frontend_read_model_service import (
    read_dashboard_view,
    read_product_detail,
    read_product_views,
    read_system_status_view,
    refresh_all_read_models,
)
from src.services.frontend_view_artifact_v2259_service import (
    DEFAULT_VIEW_KEY,
    get_frontend_view_head_v2259,
    materialize_frontend_views_v2259,
    read_frontend_view_artifact_v2259,
)
from src.services.pipeline_live_read_model_v225_service import read_pipeline_live_model
from src.services.product_trend_read_model_v217_service import read_product_trend
from src.services.public_task_dto_service import (
    PUBLIC_TASK_DTO_VERSION,
    project_task_detail,
    project_task_list_response,
)
from src.services.task_detail_snapshot_v2024_service import (
    backfill_task_detail_snapshots,
    read_task_detail_snapshot,
)
from src.services.task_fast_read_model_v2021_service import read_task_fast_views_v2021
from src.services.task_generation_run_service import read_data_line_status
from src.services.task_pool_acceptance_v163_service import read_task_pool_acceptance
from src.services.task_pool_lifecycle_sync_v2020_service import sync_task_pool_entries_to_task_status
from src.services.task_read_model_v2082_service import pipeline_diagnostics

router = APIRouter(prefix="/api/view", tags=["frontend-read-model"])
COMPETITION_OPERATOR_ID = "competition_operator"


def _align(result: Dict[str, Any], route_rule: str) -> Dict[str, Any]:
    """Compatibility metadata for non-task read models.

    Task endpoints use the V22.2.3 public DTO boundary and intentionally skip
    duplicate runtime/version aliases.
    """
    result["version"] = VERSION
    result["runtimeVersion"] = VERSION
    result["routeVersion"] = VERSION
    result["contractVersion"] = VERSION
    result["runtimeMode"] = "single_release_sealed_runtime"
    result["threeAgentPipelineVersion"] = THREE_AGENT_PIPELINE_VERSION
    result["routeRule"] = route_rule
    return result


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _overlay_current_task_status(result: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    """Overlay mutable lifecycle state without rebuilding Agent/SOP detail."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT status,workflow_status,assignee_id,reviewer_id,payload,updated_at FROM task_status WHERE task_id=? LIMIT 1",
                (task_id,),
            ).fetchone()
    except Exception:
        row = None
    if not row:
        return result
    status = row["status"] or row["workflow_status"] or result.get("taskStatus") or "待接收"
    payload = loads(row["payload"]) if row["payload"] else {}
    payload = payload if isinstance(payload, dict) else {}
    authorization = _dict(
        payload.get("authorizationDecision")
        or payload.get("actionAuthorization")
        or _dict(payload.get("taskPlan")).get("authorizationDecision")
    )
    result["taskStatus"] = status
    result["lifecycleUpdatedAt"] = row["updated_at"]
    if authorization:
        result["authorizationDecision"] = authorization
        active = _dict(result.get("activeActionContract"))
        if active:
            active["activeAuthority"] = authorization
            result["activeActionContract"] = active
    related = _dict(result.get("relatedTask"))
    related.update(
        status=status,
        workflowStatus=row["workflow_status"] or status,
        displayStatus=status,
        assigneeId=row["assignee_id"] or related.get("assigneeId"),
        reviewerId=row["reviewer_id"] or related.get("reviewerId"),
        updatedAt=row["updated_at"] or related.get("updatedAt"),
    )
    if authorization:
        related["authorizationDecision"] = authorization
    result["relatedTask"] = related
    return result


@router.get("/dashboard")
def dashboard_view() -> Dict[str, Any]:
    return _align(read_dashboard_view(), "V22 read-only dashboard projection; no Agent or task recompute")


@router.get("/products")
def product_view(
    storeId: str | None = None,
    dataVersion: str | None = None,
    limit: int = Query(default=100, ge=1, le=300),
) -> Dict[str, Any]:
    return _align(
        read_product_views(store_id=storeId, data_version=dataVersion, limit=limit),
        "V22 read-only product projection",
    )


@router.get("/products/{product_id}/trend")
def product_trend_view(product_id: str, storeId: str | None = None) -> Dict[str, Any]:
    result = read_product_trend(product_id, store_id=storeId)
    result["productTrendReadModelVersion"] = VERSION
    return _align(
        result,
        "V22 product trend: latest five direct observations plus derived historical trends; missing never becomes zero",
    )


@router.get("/products/{product_id}")
def product_detail_view(product_id: str, storeId: str | None = None) -> Dict[str, Any]:
    return _align(
        read_product_detail(product_id, store_id=storeId),
        "V22 read-only product detail projection",
    )


@router.get("/tasks")
def task_view(
    status: str | None = None,
    dataVersion: str | None = None,
    limit: int = Query(default=80, ge=1, le=200),
) -> Dict[str, Any]:
    internal = read_task_fast_views_v2021(
        status=status,
        data_version=dataVersion,
        limit=limit,
    )
    return project_task_list_response(internal)


@router.get("/tasks/{task_id}")
def task_detail_view(task_id: str, dataVersion: str | None = None) -> Dict[str, Any]:
    internal = _overlay_current_task_status(
        read_task_detail_snapshot(task_id, data_version=dataVersion),
        task_id,
    )
    result = project_task_detail(internal)
    result["version"] = PUBLIC_TASK_DTO_VERSION
    return result


@router.get("/system-status")
def system_status_view() -> Dict[str, Any]:
    return _align(read_system_status_view(), "V22 read-only system projection")


@router.get("/data-line")
def data_line_view() -> Dict[str, Any]:
    result = read_data_line_status()
    result["taskPoolAcceptanceLoaded"] = False
    return _align(result, "V22 lightweight data-line projection")


@router.get("/pipeline-live")
def pipeline_live_view(
    dataVersion: str | None = None,
    limit: int = Query(default=40, ge=1, le=100),
) -> Dict[str, Any]:
    result = read_pipeline_live_model(data_version=dataVersion, limit=limit)
    result["pipelineLiveReadModelVersion"] = THREE_AGENT_PIPELINE_VERSION
    result["pipelineDiagnosticsLoaded"] = False
    return _align(
        result,
        "V22.5 lightweight three-Agent pipeline-live projection",
    )


@router.get("/pipeline-diagnostics")
def pipeline_diagnostics_view(dataVersion: str | None = None) -> Dict[str, Any]:
    return _align(
        pipeline_diagnostics(dataVersion),
        "V22 diagnostics loaded only when explicitly opened",
    )


@router.get("/data-line-detail")
def data_line_detail_view(dataVersion: str | None = None) -> Dict[str, Any]:
    result = read_data_line_status()
    current_version = dataVersion or result.get("dataVersion") or result.get("currentDataVersion")
    acceptance = (
        read_task_pool_acceptance(data_version=current_version)
        if current_version
        else read_task_pool_acceptance()
    )
    acceptance["routeVersion"] = VERSION
    result["taskPoolAcceptance"] = acceptance
    result["taskPoolAcceptanceLoaded"] = True
    if acceptance.get("status") == "failed" and result.get("lineStatus") == "completed":
        result["lineStatus"] = "attention"
        result["headline"] = "Task pool acceptance failed. Open data-line detail."
    return _align(result, "V22 data-line detail with explicit acceptance diagnostics")


@router.get("/task-pool-acceptance")
def task_pool_acceptance_view(dataVersion: str | None = None) -> Dict[str, Any]:
    return _align(
        read_task_pool_acceptance(data_version=dataVersion),
        "V22 read-only task-pool acceptance projection",
    )


@router.get("/stores")
def store_view() -> Dict[str, Any]:
    products = read_product_views(limit=300)
    stores: Dict[str, Dict[str, Any]] = {}
    for item in products.get("items") or []:
        store_id = item.get("storeId")
        if not store_id:
            continue
        store = stores.setdefault(
            store_id,
            {
                "storeId": store_id,
                "storeName": item.get("storeName") or "Operating Unit",
                "platform": item.get("platform"),
                "productCount": 0,
                "highRiskProductCount": 0,
                "updatedAt": item.get("updatedAt"),
            },
        )
        store["productCount"] += 1
    return _align(
        {"ready": bool(stores), "items": list(stores.values())},
        "V22 store read model",
    )


@router.get("/head/{view_key}")
def hash_view_head(
    view_key: str,
    dataVersion: str | None = None,
) -> Dict[str, Any]:
    try:
        return get_frontend_view_head_v2259(
            view_key=view_key or DEFAULT_VIEW_KEY,
            user_id=COMPETITION_OPERATOR_ID,
            data_version=dataVersion,
            materialize_if_missing=True,
        )
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"frontend_view_head_unavailable:{str(exc)[:500]}") from exc


@router.get("/artifacts/{artifact_ref}")
def hash_view_artifact(
    artifact_ref: str,
    viewKey: str = DEFAULT_VIEW_KEY,
) -> Dict[str, Any]:
    try:
        return read_frontend_view_artifact_v2259(
            artifact_ref,
            view_key=viewKey or DEFAULT_VIEW_KEY,
            user_id=COMPETITION_OPERATOR_ID,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"frontend_view_artifact_unavailable:{str(exc)[:500]}") from exc


@router.post("/refresh")
def refresh_view(
    dataVersion: str | None = None,
) -> Dict[str, Any]:
    sync = sync_task_pool_entries_to_task_status(data_version=dataVersion)
    snapshots = backfill_task_detail_snapshots(data_version=dataVersion)
    result = refresh_all_read_models(data_version=dataVersion)
    result["taskPoolLifecycleSync"] = sync
    result["taskPoolLifecycleSyncVersion"] = VERSION
    result["taskDetailSnapshots"] = snapshots
    result["taskDetailSnapshotVersion"] = VERSION
    result["hashView"] = materialize_frontend_views_v2259(
        data_version=dataVersion,
        view_key=DEFAULT_VIEW_KEY,
        user_id=COMPETITION_OPERATOR_ID,
    )
    return _align(
        result,
        "V22.5.9 explicit refresh atomically publishes immutable module Artifacts and one manifest head; hot GET paths remain read-only",
    )
