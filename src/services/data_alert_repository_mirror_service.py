"""DataVersion and AlertEvent PostgreSQL mirror service."""

from __future__ import annotations

from typing import Any, Dict, List
from uuid import uuid4

from src.core.context import UserContext
from src.db.projection_repositories import ProductionAlertEventRepository, ProductionDataVersionRepository
from src.db.session import get_session_factory
from src.services.repository_mirror_base_service import mirror_enabled, mirror_failed, mirror_skipped, mirror_summary, repository_mode, run_mirror

DATA_ALERT_MIRROR_VERSION = "5.3.8"


def _alert_id() -> str:
    return f"ALERTMIRROR_{uuid4().hex[:10]}".upper()


def _source_results(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    nested = result.get("results")
    if isinstance(nested, list) and nested:
        return [item for item in nested if isinstance(item, dict)]
    return [result]


def _collect_data_versions(result: Dict[str, Any], *, trace_id: str, import_job_id: str | None, source_type: str | None) -> List[Dict[str, Any]]:
    versions: List[Dict[str, Any]] = []
    for item in _source_results(result):
        data_version = item.get("dataVersion") or item.get("data_version") or item.get("version")
        if not data_version:
            continue
        versions.append({"dataVersion": data_version, "traceId": item.get("traceId") or trace_id, "importJobId": import_job_id, "datasetName": item.get("datasetName") or item.get("dataset_name") or item.get("latestDatasetName"), "sourceType": source_type or item.get("sourceType") or item.get("source_type"), "status": "active", "rowCount": item.get("rowCount") or item.get("totalRows") or 0, "checksum": item.get("checksum"), "payload": item})
    return versions


def _priority_to_severity(priority: str | None) -> str:
    return "high" if priority == "高" else "medium" if priority == "中" else "low"


def _collect_alerts(result: Dict[str, Any], *, trace_id: str) -> List[Dict[str, Any]]:
    alerts: List[Dict[str, Any]] = []
    for item in _source_results(result):
        for alert in item.get("alerts") or []:
            if not isinstance(alert, dict):
                continue
            alerts.append({"alertId": alert.get("alertId") or alert.get("alert_id") or _alert_id(), "traceId": alert.get("traceId") or trace_id, "dataVersion": alert.get("dataVersion") or alert.get("data_version") or item.get("dataVersion"), "sourceModule": alert.get("sourceModule") or alert.get("sourceDataset") or item.get("datasetName") or "report_alert", "sourceEntityId": alert.get("sourceEntityId") or alert.get("entityId") or alert.get("productId") or alert.get("taskId"), "alertType": alert.get("alertType") or alert.get("taskType") or "report_alert", "severity": alert.get("severity") or _priority_to_severity(alert.get("priority")), "status": alert.get("status") or "open", "title": alert.get("title") or alert.get("alertType") or alert.get("taskSignal") or "报表预警", "payload": alert})
    return alerts


async def _mirror_data_alert_async(ctx: UserContext, data_versions: List[Dict[str, Any]], alerts: List[Dict[str, Any]], action: str) -> Dict[str, Any]:
    mirrored_versions: List[Dict[str, Any]] = []
    mirrored_alerts: List[Dict[str, Any]] = []
    async with get_session_factory()() as session:
        version_repo = ProductionDataVersionRepository(session, ctx)
        alert_repo = ProductionAlertEventRepository(session, ctx)
        for item in data_versions:
            mirrored_versions.append(await version_repo.upsert(item))
        for item in alerts:
            mirrored_alerts.append(await alert_repo.upsert(item))
        await session.commit()
    return {"version": DATA_ALERT_MIRROR_VERSION, "action": action, "mode": repository_mode(), "mirrored": True, "status": "mirrored", "dataVersions": mirrored_versions, "alertEvents": mirrored_alerts, "counts": {"dataVersions": len(mirrored_versions), "alertEvents": len(mirrored_alerts)}}


def mirror_data_alerts_to_production(ctx: UserContext, result: Dict[str, Any] | None, *, trace_id: str, import_job_id: str | None = None, source_type: str | None = None, action: str = "data_alert.write") -> Dict[str, Any]:
    if not mirror_enabled():
        return mirror_skipped(action, version=DATA_ALERT_MIRROR_VERSION)
    if not result:
        return mirror_skipped(action, reason="result is empty", version=DATA_ALERT_MIRROR_VERSION)
    data_versions = _collect_data_versions(result, trace_id=trace_id, import_job_id=import_job_id, source_type=source_type)
    alerts = _collect_alerts(result, trace_id=trace_id)
    if not data_versions and not alerts:
        return mirror_skipped(action, reason="no data versions or alerts in result", version=DATA_ALERT_MIRROR_VERSION)
    try:
        return run_mirror(_mirror_data_alert_async(ctx, data_versions, alerts, action), action, version=DATA_ALERT_MIRROR_VERSION)
    except Exception as exc:  # noqa: BLE001
        return mirror_failed(action, exc, version=DATA_ALERT_MIRROR_VERSION, extra={"counts": {"dataVersions": len(data_versions), "alertEvents": len(alerts)}})


def data_alert_mirror_summary() -> Dict[str, Any]:
    return mirror_summary(name="dataAlertWriteMirror", resources=["DataVersion", "AlertEvent"], version=DATA_ALERT_MIRROR_VERSION, extra={"mirroredResources": ["DataVersion", "AlertEvent"], "rule": "DataVersion and AlertEvent are collected from report import results and mirrored after SQLite import succeeds."})
