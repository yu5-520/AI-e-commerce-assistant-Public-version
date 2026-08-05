from __future__ import annotations

from typing import Any, Dict

from src.repositories.sqlite_repository import connect, loads

BASELINE_OVERRIDE_VERSION = "17.3"


def _load(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        data = loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _present(value: Any) -> bool:
    return value not in {None, "", "null", "None", "UNKNOWN", "—"}


def latest_bundle_baseline_state() -> Dict[str, Any]:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT data_version, payload FROM product_signal_snapshots_v14 ORDER BY updated_at DESC, created_at DESC LIMIT 1"
            ).fetchone()
    except Exception:
        row = None
    if not row:
        return {"bundleCount": 0, "dataVersion": None, "baselineNoPrevious": False}
    payload = _load(row["payload"])
    bundles = payload.get("productSignalPackages") or payload.get("signals") or payload.get("products") or []
    count = len(bundles) if isinstance(bundles, list) else int(payload.get("productSignalPackageCount") or payload.get("productSignalCount") or 0)
    previous_snapshot_id = payload.get("previousSnapshotId")
    previous_data_version = payload.get("previousDataVersion")
    return {
        "version": BASELINE_OVERRIDE_VERSION,
        "bundleCount": int(count or 0),
        "dataVersion": row["data_version"],
        "previousSnapshotId": previous_snapshot_id,
        "previousDataVersion": previous_data_version,
        "baselineNoPrevious": bool(count and not _present(previous_snapshot_id) and not _present(previous_data_version)),
    }


def apply_first_report_baseline(result: Dict[str, Any]) -> Dict[str, Any]:
    baseline = latest_bundle_baseline_state()
    if not baseline.get("baselineNoPrevious"):
        return result
    bundle_count = int(baseline.get("bundleCount") or result.get("inputBundleCount") or 0)
    data_version = baseline.get("dataVersion") or result.get("currentDataVersion")
    result.update({
        "version": BASELINE_OVERRIDE_VERSION,
        "runtimeVersion": BASELINE_OVERRIDE_VERSION,
        "lineStatus": "baseline_completed",
        "headline": "首份报表已建立基线，商品 %d，正式任务 0，等待下一份报表形成变化判断" % bundle_count,
        "baselineMode": "first_report",
        "baselineNoPrevious": True,
        "bundlePreviousSnapshotId": baseline.get("previousSnapshotId"),
        "bundlePreviousDataVersion": baseline.get("previousDataVersion"),
        "dataVersion": data_version,
        "currentDataVersion": data_version,
        "latestBundleDataVersion": data_version,
        "newReportPendingJudgment": False,
        "agentJudgmentCount": 0,
        "rawJudgmentCount": 0,
        "productJudgmentPackageCount": 0,
        "taskDecisionCount": 0,
        "formalTaskCount": 0,
        "taskPoolTotalCount": 0,
        "inputBundleCount": bundle_count,
        "productJudgmentCoverageStatus": "baseline",
        "productJudgmentCoverageRate": 1.0 if bundle_count else 0,
        "stationAlignment": "v17_3_service_direct_bundle_baseline",
    })
    for station in result.get("stations") or []:
        sid = station.get("id")
        if sid in {"product_judgment_agent_station", "product_judgment_package_station", "rag_permission_context_station", "task_mapping_agent_station"}:
            station["status"] = "skipped"
            station["note"] = "首份跳过"
        elif sid == "task_pool_admission_station":
            station["status"] = "empty"
            station["note"] = "首份无任务"
        elif sid in {"frontend_read_model_station", "task_pool_acceptance_station"}:
            station["status"] = "passed"
            station["note"] = "基线完成"
    return result
