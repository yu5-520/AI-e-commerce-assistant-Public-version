"""V10.10 data source connection contract.

ERP/CRM/platform/API connections are the primary data path. Manual report upload
is kept as a backup path for demo, missing interfaces, historical backfill and
exception repair.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List

V1010_DATA_SOURCE_CONNECTION_VERSION = "10.10.0"
PRIMARY_SOURCE_IDS = ["erp", "crm", "platform", "ads"]
BACKUP_SOURCE_IDS = ["manual_upload"]

DATA_SOURCE_CONNECTIONS: Dict[str, Dict[str, Any]] = {
    "erp": {
        "sourceId": "erp",
        "label": "ERP 接口",
        "sourceType": "api_connector",
        "priority": "primary",
        "displayStatus": "演示接入",
        "syncMode": "scheduled_api_sync",
        "cadence": "15分钟 / 1小时",
        "dataScope": ["商品", "订单", "库存", "成本", "利润"],
        "datasetNames": ["products", "orders", "inventory"],
        "targetModules": ["总览", "经营", "任务", "数据", "日志"],
        "credentialStatus": "real_credentials_required",
        "actionLabel": "同步 ERP",
    },
    "crm": {
        "sourceId": "crm",
        "label": "CRM 接口",
        "sourceType": "api_connector",
        "priority": "primary",
        "displayStatus": "演示接入",
        "syncMode": "scheduled_api_sync",
        "cadence": "1小时 / 每日",
        "dataScope": ["客户", "售后", "退款", "标签"],
        "datasetNames": ["customers", "refunds"],
        "targetModules": ["总览", "经营", "任务", "数据", "日志"],
        "credentialStatus": "real_credentials_required",
        "actionLabel": "同步 CRM",
    },
    "platform": {
        "sourceId": "platform",
        "label": "平台后台 API",
        "sourceType": "api_connector",
        "priority": "primary",
        "displayStatus": "演示接入",
        "syncMode": "scheduled_api_sync",
        "cadence": "15分钟 / 1小时",
        "dataScope": ["商品", "订单", "评价", "售后"],
        "datasetNames": ["products", "orders", "refunds"],
        "targetModules": ["总览", "经营", "任务", "数据", "日志"],
        "credentialStatus": "real_credentials_required",
        "actionLabel": "同步平台",
    },
    "ads": {
        "sourceId": "ads",
        "label": "广告后台 API",
        "sourceType": "api_connector",
        "priority": "primary",
        "displayStatus": "演示接入",
        "syncMode": "scheduled_api_sync",
        "cadence": "15分钟 / 每日",
        "dataScope": ["投放", "ROI", "点击", "转化"],
        "datasetNames": ["products"],
        "targetModules": ["总览", "经营", "任务", "数据", "日志"],
        "credentialStatus": "real_credentials_required",
        "actionLabel": "同步广告",
    },
    "manual_upload": {
        "sourceId": "manual_upload",
        "label": "手动上传",
        "sourceType": "manual_file",
        "priority": "backup",
        "displayStatus": "备用入口",
        "syncMode": "manual_upload_backup",
        "cadence": "临时补录",
        "dataScope": ["历史数据", "接口未开通数据", "异常补数"],
        "datasetNames": ["auto"],
        "targetModules": ["总览", "经营", "任务", "数据", "日志"],
        "credentialStatus": "not_required",
        "actionLabel": "上传文件",
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _source_order(item: Dict[str, Any]) -> int:
    source_id = str(item.get("sourceId") or "")
    if source_id in PRIMARY_SOURCE_IDS:
        return PRIMARY_SOURCE_IDS.index(source_id)
    if source_id in BACKUP_SOURCE_IDS:
        return 100 + BACKUP_SOURCE_IDS.index(source_id)
    return 999


def list_data_source_connections() -> Dict[str, Any]:
    sources = sorted((deepcopy(item) for item in DATA_SOURCE_CONNECTIONS.values()), key=_source_order)
    return {
        "version": V1010_DATA_SOURCE_CONNECTION_VERSION,
        "mode": "api_sources_primary_manual_upload_backup",
        "primarySourceIds": PRIMARY_SOURCE_IDS,
        "backupSourceIds": BACKUP_SOURCE_IDS,
        "sources": sources,
        "rule": "ERP、CRM、平台后台和广告后台接口是主链路；手动上传只作为接口未开通、历史补数和异常补录的备用入口。",
        "flow": ["接口接入", "自动同步", "字段识别", "模块更新", "任务生成", "日志留痕"],
    }


def get_data_source_connection(source_id: str) -> Dict[str, Any]:
    key = (source_id or "").strip().lower().replace("-", "_")
    if key not in DATA_SOURCE_CONNECTIONS:
        raise ValueError(f"Unsupported data source: {source_id}")
    return deepcopy(DATA_SOURCE_CONNECTIONS[key])


def build_source_sync_summary(source_id: str, import_result: Dict[str, Any]) -> Dict[str, Any]:
    source = get_data_source_connection(source_id)
    results = import_result.get("results") if isinstance(import_result.get("results"), list) else [import_result]
    row_count = sum(int(item.get("rowCount") or 0) for item in results if isinstance(item, dict))
    alert_count = int(import_result.get("alertCount") or 0)
    created_task_count = int(import_result.get("createdTaskCount") or 0)
    return {
        "version": V1010_DATA_SOURCE_CONNECTION_VERSION,
        "mode": "primary_api_source_sync" if source.get("priority") == "primary" else "manual_upload_backup",
        "sourceId": source["sourceId"],
        "label": source["label"],
        "priority": source["priority"],
        "sourceType": source["sourceType"],
        "syncedAt": now_iso(),
        "datasetNames": source.get("datasetNames", []),
        "rowCount": row_count,
        "alertCount": alert_count,
        "createdTaskCount": created_task_count,
        "updatedModules": source.get("targetModules", []),
        "message": f"{source['label']} 已完成同步，生成 {created_task_count} 个任务。",
    }
