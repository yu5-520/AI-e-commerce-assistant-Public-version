"""V14.8 report module routes.

Report page is read-only. It reads persisted table counts and read-model status,
not projected_report_groups/projected_report_details.
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Request

from src.services.account_service import user_id_from_headers
from src.services.frontend_read_model_service import read_dashboard_view, read_system_status_view
from src.services.system_service import get_db_status
from src.services.task_pool_station_service import enter_task_pool_from_snapshot
from src.services.task_snapshot_station_service import create_task_snapshot

router = APIRouter()
REPORT_MODULE_VERSION = "14.8.0"

REPORT_DATASETS = [
    ("imported_report_rows", "报表行"),
    ("operating_products", "商品对象"),
    ("operating_stores", "店铺对象"),
    ("product_metric_facts", "商品指标事实"),
    ("store_metric_facts", "店铺指标事实"),
    ("system_product_snapshots_v14", "商品分层快照"),
    ("product_signal_snapshots_v14", "商品全量包"),
    ("signal_pool_v14", "全量包队列"),
    ("agent_judgments_v14", "Agent判断"),
    ("task_snapshots", "任务快照"),
    ("task_pool_entries", "任务池"),
    ("frontend_dashboard_view", "前端总览读模型"),
    ("frontend_product_view", "前端商品读模型"),
    ("frontend_task_view", "前端任务读模型"),
]


def request_user_id(request: Request) -> str:
    return user_id_from_headers(request.headers)


def _table_map() -> Dict[str, Dict[str, Any]]:
    status = get_db_status()
    return {item.get("table_name"): item for item in status.get("tables") or []}


def _report_groups() -> List[Dict[str, Any]]:
    table_map = _table_map()
    reports = []
    for table_name, label in REPORT_DATASETS:
        row = table_map.get(table_name) or {}
        count = int(row.get("record_count") or 0)
        reports.append({"id": table_name, "name": label, "label": label, "status": "已同步" if count else "暂无数据", "count": f"{count} 条", "source": "runtime_table_status", "latestDataVersion": None, "latestSnapshotAt": row.get("latest_at"), "createdTaskCount": 0, "desc": f"{label}：{count} 条"})
    return [{"id": "runtime", "name": "运行态数据", "reports": reports}]


def _flatten(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [report for group in groups for report in group.get("reports", [])]


def report_task_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    return {"entityType": "报表", "entityId": item["id"], "riskDomain": "报表", "actionType": "复盘", "sourceModule": "报表模块", "source": "frontend_read_model", "sourceRoute": "data-check", "productId": f"R-{item['id']}", "imageLabel": "表", "productShort": item.get("name") or item["id"], "productTitle": f"{item.get('name') or item['id']}同步状态复盘", "title": f"{item.get('name') or item['id']}同步状态复盘", "platform": item.get("source") or "报表", "store": "按账号权限切片", "priority": "中", "priorityLevel": "warning", "deadline": "本周内", "taskType": "报表复盘", "taskSignal": "模块页面人工请求", "task": f"复盘{item.get('name') or item['id']}，检查读模型、队列和任务归属。", "reason": f"当前状态：{item.get('status', '待导入')}，数据量：{item.get('count', '0 条')}。", "judgmentTags": [item.get("source", "报表"), item.get("status", "待导入"), item.get("count", "0 条")], "sourceDataVersion": item.get("latestDataVersion")}


def _enter_snapshot_pool(payload: Dict[str, Any], user_id: str | None = None) -> Dict[str, Any]:
    entity_id = payload.get("entityId") or payload.get("productId")
    data_version = payload.get("sourceDataVersion") or payload.get("dataVersion")
    task_plan = {"title": payload.get("title") or entity_id, "subtitle": "manual_report_read_model_request_v14_8", "entityType": payload.get("entityType") or "报表", "entityId": entity_id, "taskType": payload.get("taskType") or "报表复核", "actionType": payload.get("actionType") or "manual_report_request", "priority": payload.get("priority") or "中", "deadline": payload.get("deadline") or "24小时内", "riskDomain": payload.get("riskDomain") or "报表", "sopSteps": [payload.get("task") or "复核报表同步状态。", "提交异常字段、数据口径和影响范围。", "后续由系统复盘相关指标。"], "evidenceRequirements": ["报表来源", "异常字段", "处理说明"], "reviewMetrics": ["入库行数", "识别字段数", "任务信号数", "数据缺口数"], "needManagerReview": False, "reason": payload.get("reason") or payload.get("taskSignal")}
    snapshot = create_task_snapshot({"dataVersion": data_version, "decision": "create_task_snapshot", "confidence": 0.7, "entityType": payload.get("entityType") or "报表", "entityId": entity_id, "signalRef": f"manual:report-read-model:{entity_id}:{data_version or 'latest'}", "ragContext": {"source": "report_read_model_manual_request", "version": REPORT_MODULE_VERSION}, "agentJudgment": {"decision": "create_task_snapshot", "confidence": 0.7, "reason": task_plan["reason"], "status": "manual_read_model_snapshot_bridge"}, "taskPlan": task_plan, "evidenceRequirements": task_plan["evidenceRequirements"], "systemFacts": {"modulePayload": payload}, "source": "report_module_read_model_manual_request"}, created_by=user_id)
    pool = enter_task_pool_from_snapshot(snapshot.get("taskSnapshotId"), created_by=user_id)
    return {"version": REPORT_MODULE_VERSION, "mode": "manual_request_via_task_snapshot", "snapshot": snapshot, "pool": pool, "task": pool.get("task") if isinstance(pool, dict) else None}


@router.get("/report")
def report(request: Request) -> Dict[str, Any]:
    request_user_id(request)
    groups = _report_groups()
    sync_records = _flatten(groups)
    dashboard = read_dashboard_view()
    system_status = read_system_status_view()
    has_data = any(int(str(item.get("count") or "0").split()[0]) for item in sync_records if str(item.get("count") or "0").split()[0].isdigit())
    return {"version": REPORT_MODULE_VERSION, "hasData": has_data, "reportGroups": groups if has_data else [], "reportDetails": {item["id"]: item for item in sync_records} if has_data else {}, "syncRecords": sync_records, "v3": {"version": REPORT_MODULE_VERSION, "activeAlertCount": 0, "highPriorityAlertCount": 0, "latestAlerts": []}, "recentAlerts": [], "dashboardReadModel": dashboard, "systemStatusReadModel": system_status, "readMode": "runtime_status_and_frontend_read_model_only", "rule": "V14.8：报表页不调用projected_report_groups/projected_report_details，只读运行态状态和read model。"}


@router.get("/report/{report_id}")
def report_detail(request: Request, report_id: str) -> Dict[str, Any]:
    request_user_id(request)
    details = {item["id"]: item for item in _flatten(_report_groups())}
    if report_id not in details:
        raise HTTPException(status_code=404, detail="report not found")
    return {**details[report_id], "version": REPORT_MODULE_VERSION, "readMode": "runtime_status_only"}


@router.post("/report/{report_id}/tasks")
def report_task(request: Request, report_id: str) -> Dict[str, Any]:
    user_id = request_user_id(request)
    item = next((report for report in _flatten(_report_groups()) if report.get("id") == report_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="report not found")
    return _enter_snapshot_pool(report_task_payload(item), user_id=user_id)
