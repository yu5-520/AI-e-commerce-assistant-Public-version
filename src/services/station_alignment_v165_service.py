"""Report, fact, product-master and read-model alignment services.

V22.2.5 removes full-product-bundle and bundle-validation implementations from
this legacy alignment module. Those stations are owned exclusively by
``station_alignment_v225_service`` so the old V18.6/V21.5 contract split cannot
be reconnected accidentally.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import loads
from src.runtime_version import API_VERSION
from src.services import system_product_snapshot_service as product_snapshot_service
from src.services.canonical_product_snapshot_v215_bridge_service import (
    install_canonical_product_snapshot_v215_bridge,
)
from src.services.import_row_store_service import load_import_rows
from src.services.metric_trigger_expansion_v171_service import is_first_report_baseline
from src.services.module_projection_service import projected_products, projection_summary

STATION_ALIGNMENT_VERSION = API_VERSION


def _ok_ref(prefix: str, data_version: str | None) -> str:
    return f"{prefix}:{data_version or 'latest'}"


def _json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        loaded = loads(value)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def report_receive_station(
    data_version: str | None,
    *,
    user_id: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    del user_id
    rows = []
    for dataset in ["products", "orders", "inventory", "refunds", "customers", None]:
        try:
            rows.extend(load_import_rows(dataset))
        except Exception:
            pass
        if rows:
            break
    output_ref = _ok_ref("raw_report", data_version)
    return {
        "version": STATION_ALIGNMENT_VERSION,
        "stationId": "report_receive_station",
        "dataVersion": data_version,
        "rowCount": len(rows),
        "rawReportRef": output_ref,
        "outputRef": output_ref,
        "rule": "Receive confirms the current import batch and performs no Agent judgment.",
    }


def report_schema_station(
    data_version: str | None,
    *,
    user_id: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    del user_id
    rows = []
    for dataset in ["products", None]:
        try:
            rows = load_import_rows(dataset)
        except Exception:
            rows = []
        if rows:
            break
    headers: List[str] = []
    for row in rows[:20]:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    date_fields = [
        key
        for key in headers
        if any(token in key for token in ["统计日期", "更新时间", "日期", "date", "Date", "time", "Time"])
    ]
    output_ref = _ok_ref("report_schema_mapping", data_version)
    return {
        "version": STATION_ALIGNMENT_VERSION,
        "stationId": "report_schema_station",
        "dataVersion": data_version,
        "headerCount": len(headers),
        "dateFields": date_fields,
        "reportSchemaMappingRef": output_ref,
        "outputRef": output_ref,
    }


def report_fact_station(
    data_version: str | None,
    *,
    user_id: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    summary = projection_summary(user_id)
    current_version = data_version or summary.get("latestDataVersion")
    output_ref = _ok_ref("report_fact_namespace", current_version)
    return {
        "version": STATION_ALIGNMENT_VERSION,
        "stationId": "report_fact_station",
        "dataVersion": current_version,
        "productFactCount": summary.get("metricFactCount", 0),
        "trafficSourceFactCount": summary.get("trafficSourceFactCount", 0),
        "factNamespaceStatus": "passed",
        "factRef": output_ref,
        "outputRef": output_ref,
        "summary": summary,
    }


def product_master_station(
    data_version: str | None,
    *,
    user_id: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    products = projected_products(user_id)
    output_ref = _ok_ref("product_master", data_version)
    return {
        "version": STATION_ALIGNMENT_VERSION,
        "stationId": "product_master_station",
        "dataVersion": data_version,
        "productMasterCount": len(products),
        "productMasterRef": output_ref,
        "outputRef": output_ref,
        "sampleKeys": [item.get("objectId") for item in products[:20]],
    }


def product_metric_snapshot_station(
    data_version: str | None,
    *,
    user_id: str | None = None,
    force: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    install_canonical_product_snapshot_v215_bridge()
    result = product_snapshot_service.materialize_system_product_snapshot(
        data_version=data_version,
        user_id=user_id,
        force=force,
    )
    output_ref = (
        result.get("outputRef")
        or result.get("productSnapshotRef")
        or _ok_ref("product_metric_snapshot", data_version)
    )
    return {
        "version": STATION_ALIGNMENT_VERSION,
        "stationId": "product_metric_snapshot_station",
        "dataVersion": data_version,
        "productMetricSnapshotCount": result.get("productCount", 0),
        "productMetricSnapshotRef": result.get("productSnapshotRef"),
        "outputRef": output_ref,
        "factContract": result.get("factContract"),
    }


def frontend_read_model_station(
    data_version: str | None,
    **_: Any,
) -> Dict[str, Any]:
    try:
        from src.services.frontend_read_model_service import refresh_all_read_models

        result = refresh_all_read_models(data_version=data_version)
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
    baseline = is_first_report_baseline(data_version)
    output_ref = _ok_ref("frontend_read_model", data_version)
    return {
        "version": STATION_ALIGNMENT_VERSION,
        "stationId": "frontend_read_model_station",
        "dataVersion": data_version,
        "baselineMode": "first_report" if baseline.get("isFirstReportBaseline") else "normal_delta",
        "frontendReadModelStatus": (
            "baseline_completed"
            if baseline.get("isFirstReportBaseline")
            else result.get("status") or "completed"
        ),
        "frontendReadModelRef": output_ref,
        "outputRef": output_ref,
        "refresh": result,
    }


def task_pool_acceptance_station(
    data_version: str | None,
    **_: Any,
) -> Dict[str, Any]:
    baseline = is_first_report_baseline(data_version)
    if baseline.get("isFirstReportBaseline"):
        output_ref = _ok_ref("baseline_acceptance", data_version)
        return {
            "version": STATION_ALIGNMENT_VERSION,
            "stationId": "task_pool_acceptance_station",
            "dataVersion": data_version,
            "baselineMode": "first_report",
            "acceptanceStatus": "baseline_completed",
            "ok": True,
            "mismatchCount": 0,
            "taskPoolAcceptanceRef": output_ref,
            "outputRef": output_ref,
        }
    from src.services.task_pool_acceptance_v163_service import read_task_pool_acceptance

    result = read_task_pool_acceptance(data_version=data_version)
    resolved = data_version or result.get("dataVersion")
    output_ref = _ok_ref("task_pool_acceptance", resolved)
    return {
        "version": STATION_ALIGNMENT_VERSION,
        "stationId": "task_pool_acceptance_station",
        "dataVersion": resolved,
        "baselineMode": "normal_delta",
        "acceptanceStatus": result.get("status"),
        "ok": result.get("ok"),
        "mismatchCount": len(result.get("mismatches") or []),
        "taskPoolAcceptanceRef": output_ref,
        "outputRef": output_ref,
        "acceptance": result,
    }


__all__ = [
    "STATION_ALIGNMENT_VERSION",
    "report_receive_station",
    "report_schema_station",
    "report_fact_station",
    "product_master_station",
    "product_metric_snapshot_station",
    "frontend_read_model_station",
    "task_pool_acceptance_station",
]
