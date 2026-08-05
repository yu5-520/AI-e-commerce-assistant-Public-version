"""V12.2.7 task evidence gate with strict metric-scope isolation.

任务生成不再从“字段缺失”出发，而从“经营判断是否被关键证据阻塞”出发。

This service is deliberately conservative: it does not create tasks by itself.
It evaluates task candidates that already came from trend/business signals.  If a
high-risk or time-sensitive task lacks critical evidence, it downgrades the task
into an evidence-completion task and marks matching data_gap_events as decision
blocking.

V12.2.7 hard rule:
    product ROI / traffic_source ROI / store ROI cannot replace each other.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.repositories.sqlite_repository import connect
from src.services.data_gap_event_service import ensure_data_gap_tables
from src.services.metric_fact_store_service import ensure_metric_fact_tables
from src.services.report_alert_service import now_iso

TASK_EVIDENCE_GATE_VERSION = "12.2.7"

EVIDENCE_REQUIREMENTS = {
    "流量": {
        "high": ["roi", "ad_spend", "click_rate", "payment_conversion_rate"],
        "medium": ["click_rate", "payment_conversion_rate"],
    },
    "投产": {
        "high": ["roi", "ad_spend", "payment_amount", "gross_margin_rate"],
        "medium": ["roi", "ad_spend"],
    },
    "库存": {
        "high": ["inventory_qty", "payment_amount", "payment_conversion_rate"],
        "medium": ["inventory_qty"],
    },
    "利润": {
        "high": ["gross_margin_rate", "product_cost_amount", "payment_amount"],
        "medium": ["gross_margin_rate"],
    },
    "售后": {
        "high": ["refund_rate", "refund_amount", "refund_order_count"],
        "medium": ["refund_rate"],
    },
    "趋势": {
        "high": ["roi", "payment_amount", "gross_margin_rate", "inventory_qty"],
        "medium": ["payment_amount"],
    },
}

SCOPE_TABLES = {
    "product": ("product_metric_facts",),
    "traffic_source": ("traffic_source_facts",),
    "store": ("store_metric_facts",),
}

SCOPE_LABELS = {
    "product": "商品整体口径",
    "traffic_source": "流量来源口径",
    "store": "店铺汇总口径",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _domain(task: Dict[str, Any]) -> str:
    domain = _text(task.get("riskDomain") or task.get("taskType") or "趋势")
    if "售后" in domain or "退款" in domain:
        return "售后"
    if "库存" in domain:
        return "库存"
    if "利润" in domain or "毛利" in domain or "成本" in domain:
        return "利润"
    if "投产" in domain or "预算" in domain or "ROI" in domain:
        return "投产"
    if "流量" in domain or "点击" in domain or "转化" in domain:
        return "流量"
    return "趋势"


def _requirement_level(task: Dict[str, Any]) -> str:
    high = task.get("riskGrade") == "高" or task.get("priority") == "高" or task.get("queueType") in {"urgent_execution", "today_execution"}
    return "high" if high else "medium"


def _metric_scope(task: Dict[str, Any]) -> str:
    explicit = _text(task.get("metricScope") or task.get("evidenceMetricScope"))
    if explicit in SCOPE_TABLES:
        return explicit
    domain_text = _text(task.get("riskDomain") or task.get("taskType") or task.get("title") or task.get("task"))
    if task.get("trafficSource") or "流量来源" in domain_text or "渠道来源" in domain_text:
        return "traffic_source"
    product_id = _text(task.get("productId") or task.get("entityId"))
    store_id = _text(task.get("storeId") or next(iter(task.get("storeIds") or []), ""))
    if store_id and not product_id:
        return "store"
    return "product"


def required_metrics_for_task(task: Dict[str, Any]) -> List[str]:
    domain = _domain(task)
    level = _requirement_level(task)
    required = EVIDENCE_REQUIREMENTS.get(domain, EVIDENCE_REQUIREMENTS["趋势"]).get(level, [])
    return list(dict.fromkeys(required))


def _where_clause(task: Dict[str, Any], table: str) -> tuple[str, List[Any]]:
    product_id = _text(task.get("productId") or task.get("entityId"))
    store_ids = [str(item) for item in (task.get("storeIds") or task.get("visibleStoreIds") or []) if item]
    store_id = store_ids[0] if store_ids else _text(task.get("storeId"))
    clauses: List[str] = []
    params: List[Any] = []
    if table != "store_metric_facts" and product_id:
        clauses.append("(product_id = ? OR sku_id = ? OR erp_product_code = ? OR link_code = ? OR spu_code = ? OR sku_code = ?)")
        params.extend([product_id, product_id, product_id, product_id, product_id, product_id])
    if store_id:
        clauses.append("(store_id = ? OR store_code = ? OR store_name = ?)")
        params.extend([store_id, store_id, store_id])
    if not clauses:
        return "1 = 0", []
    return " AND ".join(clauses), params


def _present_metrics(task: Dict[str, Any], required: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    ensure_metric_fact_tables()
    required_set = set(required)
    if not required_set:
        return {}
    scope = _metric_scope(task)
    tables = SCOPE_TABLES.get(scope, ("product_metric_facts",))
    present: Dict[str, Dict[str, Any]] = {}
    with connect() as conn:
        for table in tables:
            where, params = _where_clause(task, table)
            if where == "1 = 0":
                continue
            placeholders = ",".join("?" for _ in required_set)
            rows = conn.execute(
                f"""
                SELECT *, ? AS fact_table
                FROM {table}
                WHERE {where}
                  AND metric_code IN ({placeholders})
                  AND (metric_scope IS NULL OR metric_scope = '' OR metric_scope = ?)
                ORDER BY COALESCE(stat_date, updated_at) DESC, updated_at DESC
                LIMIT 80
                """,
                [table, *params, *required_set, scope],
            ).fetchall()
            for row in rows:
                code = row["metric_code"]
                if code not in present:
                    present[code] = {
                        "metricCode": code,
                        "displayValue": row["display_value"] or row["metric_value"],
                        "factId": row["fact_id"],
                        "factTable": row["fact_table"],
                        "metricScope": scope,
                        "scopeLabel": SCOPE_LABELS.get(scope),
                        "sourceSheet": row["source_sheet"],
                        "sourceBlockId": row["source_block_id"] if "source_block_id" in row.keys() else None,
                        "dataVersion": row["data_version"],
                        "statDate": row["stat_date"],
                    }
    return present


def _mark_blocking_gaps(task: Dict[str, Any], missing_metrics: List[str]) -> int:
    if not missing_metrics:
        return 0
    ensure_data_gap_tables()
    task_id = task.get("id")
    data_version = task.get("dataVersion") or (task.get("taskDetailReport") or {}).get("dataVersion")
    updated = 0
    with connect() as conn:
        for metric_code in missing_metrics:
            if data_version:
                row_ids = conn.execute(
                    """
                    SELECT gap_id FROM data_gap_events
                    WHERE metric_code = ? AND data_version = ? AND is_decision_blocking = 0
                    ORDER BY updated_at DESC LIMIT 10
                    """,
                    (metric_code, data_version),
                ).fetchall()
            else:
                row_ids = conn.execute(
                    """
                    SELECT gap_id FROM data_gap_events
                    WHERE metric_code = ? AND is_decision_blocking = 0
                    ORDER BY updated_at DESC LIMIT 10
                    """,
                    (metric_code,),
                ).fetchall()
            for row in row_ids:
                conn.execute(
                    """
                    UPDATE data_gap_events
                    SET is_decision_blocking = 1,
                        related_task_id = ?,
                        status = 'decision_blocking',
                        severity = 'warning',
                        reason = COALESCE(reason, '') || '；该缺口已被任务证据闸门判定为当前经营判断的关键证据。',
                        updated_at = ?
                    WHERE gap_id = ?
                    """,
                    (task_id, now_iso(), row["gap_id"]),
                )
                updated += 1
        conn.commit()
    return updated


def evaluate_task_evidence(task: Dict[str, Any]) -> Dict[str, Any]:
    required = required_metrics_for_task(task)
    scope = _metric_scope(task)
    present = _present_metrics(task, required)
    missing = [code for code in required if code not in present]
    gate_status = "blocked" if missing and _requirement_level(task) == "high" else "passed" if not missing else "degraded"
    blocking_count = _mark_blocking_gaps(task, missing) if gate_status == "blocked" else 0
    return {
        "version": TASK_EVIDENCE_GATE_VERSION,
        "gateStatus": gate_status,
        "domain": _domain(task),
        "requirementLevel": _requirement_level(task),
        "metricScope": scope,
        "scopeLabel": SCOPE_LABELS.get(scope),
        "requiredFactTables": list(SCOPE_TABLES.get(scope, ("product_metric_facts",))),
        "forbiddenCrossScope": [table for key, tables in SCOPE_TABLES.items() if key != scope for table in tables],
        "requiredMetrics": required,
        "presentMetrics": list(present.values()),
        "missingMetrics": missing,
        "decisionBlockingGapCount": blocking_count,
        "rule": "先有经营判断，再按 metric_scope 检查关键证据；product/traffic/store ROI 不可互相替代。",
    }


def evidence_completion_patch(task: Dict[str, Any], gate: Dict[str, Any]) -> Dict[str, Any]:
    missing = gate.get("missingMetrics") or []
    missing_text = "、".join(missing) if missing else "关键指标"
    original_type = task.get("taskType") or "经营任务"
    scope = gate.get("metricScope") or "product"
    scope_label = gate.get("scopeLabel") or SCOPE_LABELS.get(scope) or scope
    actions = [
        f"24小时内补充当前经营对象近7日 {missing_text} 数据，并确认口径为 {scope_label}，且与本次报表时间区间一致。",
        "补齐前只允许做低风险排查，不允许自动降投、下架、加预算、调库存或调整价格。",
        "补齐后重新触发任务证据闸门，由系统判断是否恢复为经营执行任务。",
    ]
    report = dict(task.get("taskDetailReport") or {})
    report.update({
        "version": TASK_EVIDENCE_GATE_VERSION,
        "title": f"补证任务｜{task.get('title') or task.get('productId') or '经营对象'}",
        "warningSummary": f"原候选任务为「{original_type}」，但缺少 {scope_label} 下的 {missing_text}，暂不进入高风险执行。",
        "evidenceGate": gate,
        "sopSteps": actions,
        "completionGate": ["补充缺失指标截图或数据文件", "确认指标时间区间和口径", "重新生成/复核经营任务"],
    })
    tags = list(dict.fromkeys([*(task.get("judgmentTags") or []), "V12.2.7证据闸门", "补证任务", "ROI口径隔离", "禁止高风险自动执行"]))
    return {
        "taskGenerationMode": task.get("taskGenerationMode") or "v11_8_sop_package",
        "taskType": "经营证据补齐任务",
        "task": actions[0],
        "sopSteps": actions,
        "executionRequirements": actions,
        "taskDetailReport": report,
        "evidenceGate": gate,
        "priority": "中",
        "riskGrade": "中",
        "priorityLevel": "warning",
        "deadline": "24小时内",
        "timeBucket": "24小时内",
        "queueType": "evidence_gap",
        "urgencyLevel": "today",
        "displayState": "expanded",
        "actionType": "补证",
        "investmentApplicationAllowed": False,
        "executionAllowed": False,
        "approvalChain": [],
        "visibleRoleIds": list(dict.fromkeys([*(task.get("visibleRoleIds") or []), "owner", "manager", "operator"])),
        "judgmentTags": tags,
        "reason": report["warningSummary"],
        "agentJudgment": {**(task.get("agentJudgment") or {}), "status": "v12_2_7_evidence_gate_blocked", "evidenceGate": gate, "boundary": "缺证时只生成补证任务，不生成高风险经营动作。"},
        "sourceTrail": list(dict.fromkeys([*(task.get("sourceTrail") or []), "V12.2.7任务证据闸门"])),
    }


def apply_evidence_gate_to_created_task(task: Dict[str, Any]) -> Dict[str, Any]:
    gate = evaluate_task_evidence(task)
    task["evidenceGate"] = gate
    if gate.get("gateStatus") == "blocked":
        task.update(evidence_completion_patch(task, gate))
    else:
        report = dict(task.get("taskDetailReport") or {})
        report["evidenceGate"] = gate
        task["taskDetailReport"] = report
        task["agentJudgment"] = {**(task.get("agentJudgment") or {}), "evidenceGate": gate}
        task["judgmentTags"] = list(dict.fromkeys([*(task.get("judgmentTags") or []), "V12.2.7证据闸门通过" if gate.get("gateStatus") == "passed" else "V12.2.7证据不足降级观察"]))
    task["taskEvidenceGateVersion"] = TASK_EVIDENCE_GATE_VERSION
    return task


def task_evidence_gate_summary() -> Dict[str, Any]:
    ensure_data_gap_tables()
    with connect() as conn:
        blocking = conn.execute("SELECT COUNT(*) AS count FROM data_gap_events WHERE is_decision_blocking = 1").fetchone()["count"]
        by_metric = [dict(row) for row in conn.execute("SELECT metric_code AS metricCode, COUNT(*) AS count FROM data_gap_events WHERE is_decision_blocking = 1 GROUP BY metric_code ORDER BY count DESC LIMIT 20").fetchall()]
    return {
        "version": TASK_EVIDENCE_GATE_VERSION,
        "decisionBlockingGapCount": blocking,
        "byMetric": by_metric,
        "roiScopeIsolation": True,
        "rule": "经营判断触发后才允许把普通缺口升级为决策缺口；ROI必须按product/traffic/store口径取证。",
    }
