"""V14.1 deprecated station registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from src.services.station_registry_service import list_stations

DEPRECATED_STATION_VERSION = "14.1.0"
ROOT_DIR = Path(__file__).resolve().parents[2]

DEPRECATED_ITEMS: List[Dict[str, Any]] = [
    {"legacyId": "v112_task_chain_fix_service", "filePath": "src/deprecated_stations/archive_services/v112_task_chain_fix_service.py", "originalPath": "src/services/v112_task_chain_fix_service.py", "currentStatus": "archive_only", "replacementStation": "task_snapshot_station", "allowedUsage": "archive_only", "canImport": False, "canRoute": False, "canFrontendLoad": False, "riskLevel": "medium"},
    {"legacyId": "v1211_agent_sop_enhancement_service", "filePath": "src/deprecated_stations/archive_services/v1211_agent_sop_enhancement_service.py", "originalPath": "src/services/v1211_agent_sop_enhancement_service.py", "currentStatus": "archive_only", "replacementStation": "agent_judgment_station", "allowedUsage": "archive_only", "canImport": False, "canRoute": False, "canFrontendLoad": False, "riskLevel": "high"},
    {"legacyId": "v1212_rag_llm_agent_service", "filePath": "src/deprecated_stations/archive_services/v1212_rag_llm_agent_service.py", "originalPath": "src/services/v1212_rag_llm_agent_service.py", "currentStatus": "archive_only", "replacementStation": "rag_context_station+agent_judgment_station", "allowedUsage": "archive_only", "canImport": False, "canRoute": False, "canFrontendLoad": False, "riskLevel": "high"},
    {"legacyId": "v1211_manual_task_package_service", "filePath": "src/services/v1211_manual_task_package_service.py", "currentStatus": "deprecated_not_mainline", "replacementStation": "task_snapshot_station", "allowedUsage": "blocked_from_module_routes", "canImport": False, "canRoute": False, "canFrontendLoad": False, "riskLevel": "high"},
    {"legacyId": "report_task_sync_route", "filePath": "src/api/routes/report_task_sync.py", "currentStatus": "disabled_noop_compat_route", "replacementStation": "task_pool_station", "allowedUsage": "noop_only", "canImport": True, "canRoute": True, "canFrontendLoad": False, "riskLevel": "low"},
    {"legacyId": "pipeline_compat_route", "filePath": "src/api/routes/pipeline.py", "currentStatus": "v14_station_wrapper", "replacementStation": "station_interface", "allowedUsage": "station_wrapper_only", "canImport": True, "canRoute": True, "canFrontendLoad": False, "riskLevel": "low"},
]

BLOCKED_MAINLINE_IDS = {item["legacyId"] for item in DEPRECATED_ITEMS if not item.get("canImport") or item.get("allowedUsage") in {"archive_only", "blocked_from_module_routes"}}


def list_deprecated_items() -> List[Dict[str, Any]]:
    return [{**item, "version": DEPRECATED_STATION_VERSION} for item in DEPRECATED_ITEMS]


def get_deprecated_item(legacy_id: str) -> Dict[str, Any] | None:
    for item in DEPRECATED_ITEMS:
        if item["legacyId"] == legacy_id:
            return {**item, "version": DEPRECATED_STATION_VERSION}
    return None


def deprecated_summary() -> Dict[str, Any]:
    items = list_deprecated_items()
    high_risk = [item for item in items if item.get("riskLevel") == "high"]
    archived = [item for item in items if item.get("allowedUsage") == "archive_only"]
    return {"version": DEPRECATED_STATION_VERSION, "stationId": "deprecated_station_archive", "itemCount": len(items), "highRiskCount": len(high_risk), "adapterWhitelistCount": 0, "archivedReferenceCount": len(archived), "items": items, "rule": "V14.1 deprecated registry tracks old task routes as blocked or noop; mainline is signal_pool -> rag_context -> agent_judgment -> task_snapshot -> task_pool."}


def _read_repo_file(path: str) -> str:
    target = ROOT_DIR / path
    try:
        return target.read_text(encoding="utf-8")
    except Exception:
        return ""


def _module_path(file_path: str) -> str:
    return file_path.replace("/", ".").removesuffix(".py")


def mainline_purity_check() -> Dict[str, Any]:
    violations: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    station_backend_modules = {station.get("stationId"): station.get("backendModule") for station in list_stations()}
    deprecated_modules = {_module_path(item["filePath"]): item for item in DEPRECATED_ITEMS}
    deprecated_modules.update({_module_path(item.get("originalPath", "")): item for item in DEPRECATED_ITEMS if item.get("originalPath")})
    for station_id, backend_module in station_backend_modules.items():
        item = deprecated_modules.get(str(backend_module or ""))
        if item and item["legacyId"] in BLOCKED_MAINLINE_IDS:
            violations.append({"type": "station_registry_deprecated_backend", "stationId": station_id, "legacyId": item["legacyId"], "filePath": item["filePath"], "replacementStation": item["replacementStation"], "message": f"{station_id} points to blocked backend {backend_module}."})
    main_text = _read_repo_file("src/api/main.py")
    for pattern in ["apply_v112_task_chain_fix", "apply_v1211_agent_sop_enhancement", "apply_v1212_rag_llm_agent"]:
        if pattern in main_text and "legacyStartupHooks" not in main_text:
            violations.append({"type": "main_startup_hook", "pattern": pattern, "filePath": "src/api/main.py", "message": "main.py must not execute legacy startup hook."})
    route_files = ["src/api/routes/modules/product.py", "src/api/routes/modules/report_v5.py", "src/api/routes/modules/competitor.py", "src/api/routes/modules/listing.py", "src/api/routes/modules/traffic.py", "src/api/routes/modules/agents.py"]
    for route_file in route_files:
        text = _read_repo_file(route_file)
        if "wrap_manual_task_payload" in text:
            violations.append({"type": "manual_wrapper_leak", "filePath": route_file, "message": "module route still imports the old manual task wrapper."})
        if "create_task(wrap_manual_task_payload" in text:
            violations.append({"type": "direct_legacy_task_write", "filePath": route_file, "message": "module route still writes legacy task payload directly."})
    for item in DEPRECATED_ITEMS:
        if item.get("allowedUsage") == "archive_only" and item.get("originalPath") and (ROOT_DIR / item["originalPath"]).exists():
            violations.append({"type": "archive_original_path_still_exists", "legacyId": item["legacyId"], "filePath": item["originalPath"], "message": "archive-only original path still exists."})
    status = "clean" if not violations else "blocked"
    return {"version": DEPRECATED_STATION_VERSION, "status": status, "failedStage": "deprecated_mainline_leak" if violations else None, "violationCount": len(violations), "warningCount": len(warnings), "violations": violations, "warnings": warnings, "rule": "V14.1 purity check blocks old manual task wrappers and archived station backends from the mainline."}
