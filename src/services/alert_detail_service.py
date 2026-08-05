"""V3.0.7 alert evidence detail report service.

Every report-triggered alert should explain why it exists, which imported rows
created it, which store owns it, which operator should handle it, and how it
moves into tasks / logs / retrospectives.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect, loads
from src.services.account_service import list_stores
from src.services.report_alert_service import _alert_visible_for_user, _row_to_alert, ensure_v3_tables

ALERT_REPORT_VERSION = "3.0.7"
ACTIVE_STATUSES = {"new", "task_created", "task_merged", "task_linked"}

TRIGGER_RULES = {
    "库存不足预警": "当前库存小于或等于安全库存时触发。",
    "商品库存预警": "商品库存低于低位阈值时触发。",
    "退款异常预警": "同一商品退款记录集中出现，或退款金额超过风险线时触发。",
    "订单激增预警": "同一商品订单数、购买件数或实付金额短期放大时触发。",
    "毛利异常预警": "商品毛利率低于安全线时触发。",
    "客户售后敏感预警": "客户退款次数达到售后敏感阈值时触发。",
}

RISK_ACTIONS = {
    "库存": ["确认可售库存", "确认补货周期", "判断是否暂停活动流量", "同步总管复核"],
    "售后": ["复查退款原因", "核对商品承诺", "核对客服话术", "确认是否暂停放量"],
    "流量": ["核对订单来源", "确认库存承接", "检查退款率", "判断是否继续放量"],
    "价格": ["核对成本", "核对售价", "核对活动价", "确认利润安全线"],
    "报表": ["确认数据可信度", "检查异常字段", "决定是否重新导入", "写入复盘记录"],
}


def _find_alert(alert_id: str) -> Dict[str, Any] | None:
    ensure_v3_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM alert_events WHERE alert_id = ?", (alert_id,)).fetchone()
    return _row_to_alert(row) if row else None


def _find_snapshot(snapshot_id: str | None) -> Dict[str, Any] | None:
    if not snapshot_id:
        return None
    ensure_v3_tables()
    with connect() as conn:
        row = conn.execute("SELECT * FROM data_snapshots WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
    if not row:
        return None
    payload = loads(row["payload"])
    return payload or {"snapshotId": row["snapshot_id"], "datasetName": row["dataset_name"], "dataVersion": row["data_version"], "rowCount": row["row_count"], "createdAt": row["created_at"], "sampleRows": []}


def _row_value(row: Dict[str, Any], *fields: str) -> str:
    for field in fields:
        if row.get(field) not in {None, ""}:
            return str(row.get(field))
    return ""


def _matching_rows(alert: Dict[str, Any], snapshot: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    rows = (snapshot or {}).get("sampleRows") or []
    entity_id = str(alert.get("entityId") or "")
    store_id = str(alert.get("storeId") or "")
    matches: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        product_id = _row_value(row, "product_id", "商品ID", "sku", "SKU", "商品编号")
        customer_id = _row_value(row, "customer_id", "客户ID", "买家ID", "用户ID")
        row_store = _row_value(row, "store_id", "storeId", "店铺ID", "店铺编号")
        if entity_id and entity_id in {product_id, customer_id}:
            if not store_id or not row_store or row_store == store_id:
                matches.append(row)
    return matches[:5]


def _store_context(store_id: str | None) -> Dict[str, Any]:
    store = next((item for item in list_stores() if item.get("id") == store_id), None)
    if not store:
        return {"storeId": store_id, "storeName": "未绑定店铺", "operatorName": "待确认", "reviewerName": "店群总管"}
    return {"storeId": store.get("id"), "storeName": store.get("name"), "platform": store.get("platform"), "operatorName": store.get("primaryOperatorName") or "未分配", "reviewerName": store.get("reviewerName") or "店群总管"}


def _evidence_cards(alert: Dict[str, Any], snapshot: Dict[str, Any] | None, store: Dict[str, Any]) -> List[Dict[str, Any]]:
    cards = [
        {"label": "预警类型", "value": alert.get("alertType") or "预警"},
        {"label": "风险等级", "value": alert.get("priority") or "中"},
        {"label": "来源版本", "value": alert.get("dataVersion") or "未记录"},
        {"label": "责任店铺", "value": store.get("storeName") or "未绑定店铺"},
    ]
    cards.extend(alert.get("evidence") or [])
    if snapshot:
        cards.append({"label": "导入行数", "value": str(snapshot.get("rowCount") or 0)})
    return cards


def get_alert_detail_report(alert_id: str, user_id: str | None = None) -> Dict[str, Any] | None:
    alert = _find_alert(alert_id)
    if not alert or not _alert_visible_for_user(alert, user_id):
        return None
    snapshot = _find_snapshot(alert.get("snapshotId"))
    store = _store_context(alert.get("storeId"))
    raw_rows = _matching_rows(alert, snapshot)
    task_id = alert.get("taskId")
    risk_domain = alert.get("riskDomain") or "报表"
    source_dataset = alert.get("sourceDataset") or (snapshot or {}).get("datasetName") or "report"
    trigger_rule = TRIGGER_RULES.get(alert.get("alertType"), "命中当前报表规则阈值后触发。")
    next_step = alert.get("suggestion") or "查看证据链，确认处理动作，并在待办中提交处理结果。"
    return {
        "version": ALERT_REPORT_VERSION,
        "reportId": f"ALERT-RPT-{alert_id}",
        "reportType": "alert",
        "module": "report-alert",
        "sourceModule": "报表预警中心",
        "sourceRoute": "data-check",
        "entityId": alert.get("entityId"),
        "alertId": alert_id,
        "taskId": task_id,
        "taskStatus": alert.get("status"),
        "generatedAt": alert.get("updatedAt") or alert.get("createdAt"),
        "title": f"预警证据报告｜{alert.get('alertType') or alert_id}",
        "warningSummary": next_step,
        "riskLevel": alert.get("priority") or "中",
        "evidence": _evidence_cards(alert, snapshot, store),
        "alertEvidence": alert.get("evidence") or [],
        "relatedAlerts": [alert],
        "sourceTrace": [
            {"label": "来源报表", "value": source_dataset},
            {"label": "数据版本", "value": alert.get("dataVersion")},
            {"label": "导入批次", "value": alert.get("importId")},
            {"label": "快照", "value": alert.get("snapshotId")},
        ],
        "triggerRule": {"name": alert.get("alertType"), "rule": trigger_rule, "status": "已触发" if alert.get("status") in ACTIVE_STATUSES else alert.get("status")},
        "responsibility": {"store": store, "operatorName": store.get("operatorName"), "reviewerName": store.get("reviewerName"), "visibleStoreIds": alert.get("visibleStoreIds") or []},
        "rawRows": raw_rows,
        "evidenceChain": [
            "报表导入生成数据版本",
            "字段映射识别预警对象和店铺归属",
            "规则判断生成预警事件",
            "预警事件继承店铺权限",
            "任务进入对应负责人待办",
        ],
        "aiAssessment": "该报告只解释预警来源、证据链和处理边界，不自动改库存、价格、预算、上新状态或客户触达。",
        "suggestedActions": RISK_ACTIONS.get(risk_domain, RISK_ACTIONS["报表"]),
        "operationChecklist": ["核对来源报表", "核对触发字段", "核对责任店铺", "确认处理动作", "提交证据给复核人"],
        "dataNeeded": ["原始报表文件", "关联商品数据", "近 7 日订单/售后", "处理后的复核说明"],
        "humanDecision": ["是否确认预警有效", "是否进入待办处理", "是否需要重新导入", "是否写入复盘"],
        "nextStep": next_step,
        "agentBoundary": "Agent 可以补充分析报告和检查清单，但不能直接执行店铺动作。",
        "archiveRule": "预警处理完成后，证据链进入日志和周期复盘。",
        "relatedTask": {"id": task_id, "status": alert.get("taskStatus") or alert.get("taskWorkflowStatus")} if task_id else None,
    }
