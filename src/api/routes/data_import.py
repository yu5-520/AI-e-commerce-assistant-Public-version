"""V16.15 Data Hub routes.

Import APIs run the import system only, then enqueue the task generation system.
Agent/RAG/task snapshot/task pool stations are pulled by the station queue worker.

The route no longer imports the deleted src.core.context module. Request context
is resolved through the current V16 account service.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, Request, UploadFile

from src.services.account_service import current_user, user_id_from_headers
from src.services.data_gap_event_service import data_gap_summary, ingest_data_gaps_from_import
from src.services.data_import_service import DATASET_CONFIGS, import_mock_data, list_import_records, list_import_sources, read_csv, validate_all_imports
from src.services.data_source_connection_service import build_source_sync_summary, get_data_source_connection, list_data_source_connections
from src.services.data_version_service import delete_data_version, get_data_version_detail
from src.services.data_version_service import list_import_records as list_version_import_records
from src.services.data_version_service import rollback_data_version
from src.services.import_adapter_service import compact_upload_meta, parse_upload_file
from src.services.import_diagnostics_service import import_diagnostics
from src.services.metric_fact_store_service import ingest_metric_facts_from_import, ingest_metric_facts_from_sheet_rows, metric_fact_summary
from src.services.operating_object_store_service import upsert_operating_objects_from_import
from src.services.operating_unit_snapshot_service import materialize_operating_unit_snapshot
from src.services.pipeline_gate_service import record_stage_gate
from src.services.report_alert_service import get_v3_dashboard_summary, import_report_dataset, list_alert_events, list_alerts_for_entity
from src.services.report_schema_service import confirm_report_import, get_report_templates, preview_report_dataset
from src.services.station_queue_service import enqueue_task_generation

router = APIRouter(prefix="/api/data", tags=["data-import"])
ROLLBACK_ROLE_IDS = {"owner", "manager", "finance"}
DEFAULT_SYNC_DATASETS = ["inventory", "refunds", "orders", "products"]
DATA_IMPORT_ROUTE_VERSION = "16.15"
HEAVY_KEYS = {"rows", "sampleRows", "stationRuns", "outputs", "products", "signals", "productSignalPackages", "agentProductSnapshotPackages", "judgments", "snapshots", "taskPackage", "snapshot", "payload"}


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


def request_context(request: Request) -> SimpleNamespace:
    user_id = request_user_id(request)
    user = current_user(user_id)
    return SimpleNamespace(
        user_id=user_id,
        role_id=user.get("roleId") or user.get("role_id") or "operator",
        tenant_id=user.get("tenantId") or user.get("tenant_id") or "demo_tenant",
    )


def can_rollback(user_id: str) -> bool:
    return current_user(user_id).get("roleId") in ROLLBACK_ROLE_IDS


def require_rollback_permission(user_id: str) -> None:
    if not can_rollback(user_id):
        raise HTTPException(status_code=403, detail="当前账号无权回滚全局数据版本")


def _dataset_rows(dataset_name: str | None) -> List[Dict[str, Any]]:
    config = DATASET_CONFIGS.get(str(dataset_name or "").strip())
    if not config:
        return []
    try:
        return [{str(key): value for key, value in row.items()} for row in read_csv(str(config["filename"]))]
    except Exception:
        return []


def _normalize_result_rows(item: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    dataset_name = item.get("datasetName")
    data_version = item.get("dataVersion")
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        next_row = {str(key): value for key, value in row.items()}
        if dataset_name:
            next_row.setdefault("datasetName", dataset_name)
        if data_version:
            next_row.setdefault("dataVersion", data_version)
        normalized.append(next_row)
    return normalized


def _materialize_import_rows(result: Dict[str, Any], rows: Any = None) -> List[Dict[str, Any]]:
    if isinstance(rows, list) and rows:
        return _normalize_result_rows(result, rows)
    results = result.get("results") if isinstance(result.get("results"), list) else [result]
    all_rows: List[Dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        item_rows = item.get("rows") if isinstance(item.get("rows"), list) else None
        if not item_rows:
            item_rows = item.get("sampleRows") if isinstance(item.get("sampleRows"), list) else None
        if not item_rows:
            item_rows = _dataset_rows(item.get("datasetName"))
        all_rows.extend(_normalize_result_rows(item, item_rows or []))
    return all_rows


def _data_versions_from_result(result: Dict[str, Any]) -> List[str]:
    versions: List[str] = []
    if result.get("dataVersion"):
        versions.append(str(result["dataVersion"]))
    for item in (result.get("results") if isinstance(result.get("results"), list) else []):
        if isinstance(item, dict) and item.get("dataVersion"):
            versions.append(str(item["dataVersion"]))
    return list(dict.fromkeys([item for item in versions if item]))


def _compact_value(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<compact>"
    if isinstance(value, list):
        if len(value) > 20:
            return {"count": len(value), "items": [_compact_value(item, depth + 1) for item in value[:5]], "truncated": True}
        return [_compact_value(item, depth + 1) for item in value]
    if isinstance(value, dict):
        compact: Dict[str, Any] = {}
        for key, item in value.items():
            if key in HEAVY_KEYS:
                compact[f"{key}Count"] = len(item) if isinstance(item, list) else (1 if item else 0)
                continue
            compact[key] = _compact_value(item, depth + 1)
        return compact
    return value


def _compact_import_response(result: Dict[str, Any], *, source: str, rows: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    rows = rows or []
    versions = _data_versions_from_result(result)
    queue_sync = result.get("taskMainlineSync") or {}
    upload_meta = result.get("uploadMeta") if isinstance(result.get("uploadMeta"), dict) else {}
    return {
        "ok": True,
        "version": DATA_IMPORT_ROUTE_VERSION,
        "mode": "import_system_completed_task_generation_queued",
        "source": source,
        "dataVersion": result.get("dataVersion"),
        "dataVersions": versions,
        "datasetName": result.get("datasetName"),
        "rowCount": len(rows) or result.get("rowCount") or result.get("rowsCount") or 0,
        "alertCount": result.get("alertCount", 0),
        "taggedAlertCount": result.get("taggedAlertCount", 0),
        "createdTaskCount": 0,
        "taskGenerationStatus": "queued",
        "uploadMeta": _compact_value(upload_meta),
        "operatingObjectSync": _compact_value(result.get("operatingObjectSync") or {}),
        "metricFactSync": _compact_value(result.get("metricFactSync") or {}),
        "dataGapSync": _compact_value(result.get("dataGapSync") or {}),
        "pipelineSync": _compact_value(result.get("pipelineSync") or {}),
        "taskMainlineSync": _compact_value(queue_sync),
        "importDiagnostics": _compact_value(result.get("importDiagnostics") or {}),
        "summary": _compact_value(result.get("summary") or {}),
        "responseBoundary": {"heavyPayloadReturned": False, "syncMainlineExecuted": False, "strippedKeys": sorted(HEAVY_KEYS), "rule": "Import request only completes import system; task generation runs from station queue."},
    }


def _run_dataset_imports_without_legacy_tasks(dataset_names: Iterable[str] | None = None) -> Dict[str, Any]:
    selected = [str(name) for name in (dataset_names or DEFAULT_SYNC_DATASETS)]
    results: List[Dict[str, Any]] = []
    all_rows: List[Dict[str, Any]] = []
    for name in selected:
        rows = _dataset_rows(name)
        result = import_report_dataset(name, rows=rows, auto_create_tasks=False)
        result["legacyTaskCreationDisabled"] = True
        results.append(result)
        all_rows.extend(_normalize_result_rows(result, rows))
    return {"version": DATA_IMPORT_ROUTE_VERSION, "mode": "dataset_sync_without_legacy_task_triggers", "datasetCount": len(results), "rowCount": len(all_rows), "alertCount": sum(item.get("alertCount", 0) for item in results), "createdTaskCount": 0, "taggedAlertCount": sum(item.get("taggedAlertCount", 0) for item in results), "results": results, "summary": get_v3_dashboard_summary(), "_rows": all_rows}


def _attach_operating_object_sync(request: Request, result: Dict[str, Any], rows: Any, *, source: str) -> Dict[str, Any]:
    materialized_rows = _materialize_import_rows(result, rows)
    ctx = request_context(request)
    result["operatingObjectSync"] = upsert_operating_objects_from_import(result, materialized_rows, source=source, uploader_user_id=ctx.user_id, uploader_role_id=ctx.role_id)
    result["_rows"] = materialized_rows
    return result


def _report_profile_from_result(result: Dict[str, Any]) -> Dict[str, Any] | None:
    upload_meta = result.get("uploadMeta") if isinstance(result.get("uploadMeta"), dict) else {}
    profile = upload_meta.get("reportProfile") if isinstance(upload_meta, dict) else None
    return profile if isinstance(profile, dict) else result.get("reportProfile") if isinstance(result.get("reportProfile"), dict) else None


def _attach_v121_metric_fact_sync(result: Dict[str, Any], rows: Any, *, source: str, source_system: str | None = None, parsed: Dict[str, Any] | None = None) -> Dict[str, Any]:
    materialized_rows = _materialize_import_rows(result, rows) or result.get("_rows") or []
    if parsed and isinstance(parsed.get("sheetRows"), dict) and parsed.get("sheetRows"):
        result["metricFactSync"] = ingest_metric_facts_from_sheet_rows(result, parsed, report_profile=_report_profile_from_result(result), source_system=source_system or result.get("sourceSystem"), source_report_id=source)
    elif materialized_rows:
        result["metricFactSync"] = ingest_metric_facts_from_import(result, materialized_rows, report_profile=_report_profile_from_result(result), source_system=source_system or result.get("sourceSystem"), source_report_id=source)
    else:
        result["metricFactSync"] = {"version": DATA_IMPORT_ROUTE_VERSION, "skipped": True, "reason": "rows is not a list"}
    result["_rows"] = materialized_rows
    return result


def _attach_v1213_data_gap_sync(result: Dict[str, Any], rows: Any, *, source: str, source_system: str | None = None, parsed: Dict[str, Any] | None = None) -> Dict[str, Any]:
    materialized_rows = _materialize_import_rows(result, rows) or result.get("_rows") or []
    parsed_payload = parsed if isinstance(parsed, dict) else {"rows": materialized_rows}
    result["dataGapSync"] = ingest_data_gaps_from_import(result, parsed_payload, report_profile=_report_profile_from_result(result), source_system=source_system or result.get("sourceSystem"), source_report_id=source)
    result["_rows"] = materialized_rows
    return result


def _attach_pipeline_station_sync(request: Request, result: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    ctx = request_context(request)
    versions = _data_versions_from_result(result) or [None]  # type: ignore[list-item]
    rows = result.get("_rows") or []
    gates = []
    for version in versions:
        gate_input = {"source": source, "rowCount": len(rows), "sourceSystem": result.get("sourceSystem")}
        gates.append(record_stage_gate(data_version=version, stage="import_uploaded", status="completed", input_payload=gate_input, output_payload={"dataVersion": version}, user_id=ctx.user_id, output_ref=f"import:{version or 'latest'}"))
        gates.append(record_stage_gate(data_version=version, stage="report_parsed", status="completed", input_payload=gate_input, output_payload={"rowCount": len(rows)}, user_id=ctx.user_id, upstream_stage="import_uploaded", output_ref=f"rows:{version or 'latest'}"))
        gates.append(record_stage_gate(data_version=version, stage="metric_facts_ready", status="completed", input_payload={"metricFactSync": result.get("metricFactSync")}, output_payload={"summary": result.get("metricFactSync")}, user_id=ctx.user_id, upstream_stage="report_parsed", output_ref=f"metric_facts:{version or 'latest'}"))
        gates.append(record_stage_gate(data_version=version, stage="operating_objects_ready", status="completed", input_payload={"operatingObjectSync": result.get("operatingObjectSync")}, output_payload={"summary": result.get("operatingObjectSync")}, user_id=ctx.user_id, upstream_stage="metric_facts_ready", output_ref=f"operating_objects:{version or 'latest'}"))
        snapshot = materialize_operating_unit_snapshot(user_id=ctx.user_id, data_version=version, force=True)
        result["operatingUnitSnapshotSync"] = {"snapshotKey": snapshot.get("snapshotKey"), "storeCount": len(snapshot.get("storeRows") or []), "productCount": len(snapshot.get("productRows") or [])}
        gates.append(record_stage_gate(data_version=version, stage="operating_unit_snapshot_ready", status="completed", input_payload={"source": source}, output_payload=result["operatingUnitSnapshotSync"], user_id=ctx.user_id, upstream_stage="operating_objects_ready", output_ref=snapshot.get("snapshotKey")))
    result["pipelineSync"] = {"version": DATA_IMPORT_ROUTE_VERSION, "mode": "import_system_station_gates", "dataVersions": [item for item in versions if item], "gateCount": len(gates), "taskGeneration": "queued_after_import"}
    return result


def _attach_import_diagnostics(result: Dict[str, Any]) -> Dict[str, Any]:
    version = result.get("dataVersion")
    if not version and isinstance(result.get("results"), list) and result["results"]:
        version = next((item.get("dataVersion") for item in result["results"] if isinstance(item, dict) and item.get("dataVersion")), None)
    result["importDiagnostics"] = import_diagnostics(str(version) if version else None, report_profile=_report_profile_from_result(result), metric_fact_sync=result.get("metricFactSync") if isinstance(result.get("metricFactSync"), dict) else None, data_gap_sync=result.get("dataGapSync") if isinstance(result.get("dataGapSync"), dict) else None, risk_task_sync=None)
    return result


def _attach_import_product_contracts(request: Request, result: Dict[str, Any], rows: Any, *, source: str, upsert_objects: bool = False) -> Dict[str, Any]:
    if upsert_objects:
        result = _attach_operating_object_sync(request, result, rows, source=source)
        result = _attach_v121_metric_fact_sync(result, result.get("_rows"), source=source)
        result = _attach_v1213_data_gap_sync(result, result.get("_rows"), source=source)
    result["legacyImportTaskHooksDisabled"] = True
    result.setdefault("createdTaskCount", 0)
    result.setdefault("riskTaskSync", {"version": DATA_IMPORT_ROUTE_VERSION, "skipped": True, "reason": "task_generation_moved_to_station_queue"})
    return _attach_import_diagnostics(result)


def _attach_task_mainline(request: Request, result: Dict[str, Any], *, source: str) -> Dict[str, Any]:
    ctx = request_context(request)
    versions = _data_versions_from_result(result)
    if not versions and result.get("dataVersion"):
        versions = [str(result["dataVersion"])]
    queued = []
    for version in versions:
        queued.append(enqueue_task_generation(version, actor_user_id=ctx.user_id, input_ref=f"operating_unit_snapshot:{version or 'latest'}", source=source, force=True))
    result["taskMainlineSync"] = {"version": DATA_IMPORT_ROUTE_VERSION, "mode": "queued_async_task_generation", "runCount": len(queued), "queuedCount": sum(1 for item in queued if item.get("queued") or item.get("idempotentHit")), "jobs": queued, "createdTaskCount": 0, "rule": "V16.15：上传只投递任务生成队列，不同步执行RAG/Agent/任务快照/任务池。"}
    result["v142TaskMainlineSync"] = result["taskMainlineSync"]
    result["createdTaskCount"] = 0
    return result


def _finalize_import(request: Request, result: Dict[str, Any], rows: Any, *, source: str, upsert_objects: bool = False) -> Dict[str, Any]:
    final = _attach_import_product_contracts(request, result, rows, source=source, upsert_objects=upsert_objects)
    final = _attach_task_mainline(request, final, source=source)
    rows_for_count = final.get("_rows") or _materialize_import_rows(final, rows)
    return _compact_import_response(final, source=source, rows=rows_for_count)


async def _rows_from_uploaded_file(file: UploadFile) -> Dict[str, Any]:
    content = await file.read()
    try:
        return parse_upload_file(file.filename or "upload", content, content_type=file.content_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/sources")
def data_sources() -> List[Dict[str, Any]]:
    return list_import_sources()


@router.get("/source-connections")
def data_source_connections() -> Dict[str, Any]:
    return {"version": DATA_IMPORT_ROUTE_VERSION, "sources": list_data_source_connections(), "rule": "数据源配置只读，不触发同步。"}


@router.post("/source-connections/{source_id}/sync")
def sync_source_connection(request: Request, source_id: str) -> Dict[str, Any]:
    try:
        source = get_data_source_connection(source_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if source.get("priority") == "backup":
        raise HTTPException(status_code=400, detail="手动上传是备用入口，请使用 /api/data/import/confirm 或前端文件上传。")
    result = _run_dataset_imports_without_legacy_tasks(source.get("datasetNames") or None)
    result["sourceConnection"] = build_source_sync_summary(source_id, result)
    result["dataSourceSync"] = result["sourceConnection"]
    objected = _attach_operating_object_sync(request, result, result.get("_rows"), source=f"{source_id}_api_sync")
    facted = _attach_v121_metric_fact_sync(objected, objected.get("_rows"), source=f"{source_id}_api_sync", source_system=source_id)
    gapped = _attach_v1213_data_gap_sync(facted, facted.get("_rows"), source_system=source_id, source=f"{source_id}_api_sync")
    staged = _attach_pipeline_station_sync(request, gapped, source=f"{source_id}_api_sync")
    return _finalize_import(request, staged, staged.get("_rows"), source=f"{source_id}_api_sync", upsert_objects=False)


@router.post("/validate")
def validate_imports() -> Dict[str, Any]:
    return validate_all_imports()


@router.post("/import/mock")
def import_mock() -> Dict[str, Any]:
    return import_mock_data()


@router.get("/imports")
def imports() -> List[Dict[str, Any]]:
    return list_import_records()


@router.get("/import-records")
def import_records(limit: int = Query(default=50, ge=1, le=200)) -> Dict[str, Any]:
    return list_version_import_records(limit=limit)


@router.get("/metric-facts/summary")
def metric_facts_summary() -> Dict[str, Any]:
    return metric_fact_summary()


@router.get("/data-gaps/summary")
def data_gaps_summary() -> Dict[str, Any]:
    return data_gap_summary()


@router.get("/import-diagnostics")
def import_diagnostics_endpoint(data_version: str | None = Query(default=None, alias="dataVersion")) -> Dict[str, Any]:
    return import_diagnostics(data_version)


@router.get("/versions/{data_version}/detail")
def version_detail(request: Request, data_version: str) -> Dict[str, Any]:
    user_id = request_user_id(request)
    try:
        detail = get_data_version_detail(data_version, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    detail["permissions"] = {"canRollback": can_rollback(user_id) and bool(detail.get("record", {}).get("canRollback")), "canDelete": True, "rollbackRoleIds": sorted(ROLLBACK_ROLE_IDS)}
    return detail


@router.post("/versions/{data_version}/rollback")
def rollback_version(request: Request, data_version: str, body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    body = body or {}
    user_id = request_user_id(request)
    require_rollback_permission(user_id)
    try:
        return rollback_data_version(data_version, operator_id=user_id, reason=body.get("reason") or body.get("note"), task_strategy=body.get("task_strategy") or body.get("taskStrategy") or "review")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/versions/{data_version}")
def delete_version(request: Request, data_version: str, confirm: bool = Query(default=False), body: Dict[str, Any] | None = Body(default=None)) -> Dict[str, Any]:
    if not confirm:
        raise HTTPException(status_code=400, detail="删除导入记录需要 confirm=true")
    body = body or {}
    user_id = request_user_id(request)
    try:
        return delete_data_version(data_version, operator_id=user_id, reason=body.get("reason") or body.get("note") or "Demo 阶段删除导入记录。")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/templates")
def report_templates() -> Dict[str, Any]:
    return get_report_templates()


@router.post("/upload/preview")
async def preview_upload(file: UploadFile = File(...), dataset_name: str = Form(default="auto"), source_system: str = Form(default="manual_upload")) -> Dict[str, Any]:
    parsed = await _rows_from_uploaded_file(file)
    try:
        preview = preview_report_dataset(str(dataset_name), rows=parsed.get("rows"), source_system=source_system)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    preview["uploadMeta"] = compact_upload_meta(parsed)
    return _compact_value(preview)


@router.post("/upload/confirm")
async def confirm_upload(request: Request, file: UploadFile = File(...), dataset_name: str = Form(default="auto"), source_system: str = Form(default="manual_upload"), auto_create_tasks: bool = Form(default=False)) -> Dict[str, Any]:
    parsed = await _rows_from_uploaded_file(file)
    rows = parsed.get("rows")
    try:
        result = confirm_report_import(str(dataset_name), rows=rows, field_mapping={}, auto_create_tasks=False, source_system=source_system)
        result["uploadMeta"] = compact_upload_meta(parsed)
        objected = _attach_operating_object_sync(request, result, rows, source="upload_file_import")
        facted = _attach_v121_metric_fact_sync(objected, rows, source="upload_file_import", source_system=source_system, parsed=parsed)
        gapped = _attach_v1213_data_gap_sync(facted, rows, source="upload_file_import", source_system=source_system, parsed=parsed)
        staged = _attach_pipeline_station_sync(request, gapped, source="upload_file_import")
        return _finalize_import(request, staged, rows, source="upload_file_import", upsert_objects=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/preview")
def preview_report(body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    dataset_name = body.get("dataset_name") or body.get("datasetName")
    if not dataset_name:
        raise HTTPException(status_code=400, detail="dataset_name is required")
    try:
        preview = preview_report_dataset(str(dataset_name), rows=body.get("rows"), field_mapping=body.get("field_mapping") or body.get("fieldMapping"), source_system=body.get("source_system") or body.get("sourceSystem"))
        return _compact_value(preview)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import/confirm")
def confirm_import(request: Request, body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    dataset_name = body.get("dataset_name") or body.get("datasetName")
    if not dataset_name:
        raise HTTPException(status_code=400, detail="dataset_name is required")
    source_system = body.get("source_system") or body.get("sourceSystem")
    try:
        result = confirm_report_import(str(dataset_name), rows=body.get("rows"), field_mapping=body.get("field_mapping") or body.get("fieldMapping"), auto_create_tasks=False, source_system=source_system)
        if isinstance(body.get("reportProfile"), dict):
            result["reportProfile"] = body.get("reportProfile")
        objected = _attach_operating_object_sync(request, result, body.get("rows"), source="confirm_report_import")
        facted = _attach_v121_metric_fact_sync(objected, body.get("rows"), source="confirm_report_import", source_system=source_system)
        gapped = _attach_v1213_data_gap_sync(facted, body.get("rows"), source="confirm_report_import", source_system=source_system)
        staged = _attach_pipeline_station_sync(request, gapped, source="confirm_report_import")
        return _finalize_import(request, staged, body.get("rows"), source="confirm_report_import", upsert_objects=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import/report")
def import_report(request: Request, body: Dict[str, Any] = Body(default_factory=dict)) -> Dict[str, Any]:
    dataset_name = body.get("dataset_name") or body.get("datasetName")
    if not dataset_name:
        raise HTTPException(status_code=400, detail="dataset_name is required")
    source_system = body.get("source_system") or body.get("sourceSystem")
    try:
        result = import_report_dataset(str(dataset_name), rows=body.get("rows"), auto_create_tasks=False)
        objected = _attach_operating_object_sync(request, result, body.get("rows"), source="report_import")
        facted = _attach_v121_metric_fact_sync(objected, body.get("rows"), source="report_import", source_system=source_system)
        gapped = _attach_v1213_data_gap_sync(facted, body.get("rows"), source="report_import", source_system=source_system)
        staged = _attach_pipeline_station_sync(request, gapped, source="report_import")
        return _finalize_import(request, staged, body.get("rows"), source="report_import", upsert_objects=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import/mock-alerts")
def import_mock_alerts(request: Request) -> Dict[str, Any]:
    result = _run_dataset_imports_without_legacy_tasks()
    result["v3Summary"] = get_v3_dashboard_summary()
    objected = _attach_operating_object_sync(request, result, result.get("_rows"), source="mock_alerts_import")
    facted = _attach_v121_metric_fact_sync(objected, objected.get("_rows"), source="mock_alerts_import", source_system="mock_alerts")
    gapped = _attach_v1213_data_gap_sync(facted, facted.get("_rows"), source="mock_alerts_import", source_system="mock_alerts")
    staged = _attach_pipeline_station_sync(request, gapped, source="mock_alerts_import")
    return _finalize_import(request, staged, staged.get("_rows"), source="mock_alerts_import", upsert_objects=False)


@router.get("/v3-summary")
def v3_summary() -> Dict[str, Any]:
    return get_v3_dashboard_summary()


@router.get("/alerts")
def alerts(active_only: bool = Query(default=True)) -> List[Dict[str, Any]]:
    return list_alerts_for_entity(active_only=active_only)


@router.get("/alerts/events")
def alert_events(active_only: bool = Query(default=True)) -> List[Dict[str, Any]]:
    return list_alert_events(active_only)


@router.get("/versions")
def versions() -> List[Dict[str, Any]]:
    return list_version_import_records(limit=200).get("records", [])
