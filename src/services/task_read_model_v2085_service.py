"""V20.15 Strict Task Read Model Gate.

List endpoint returns lightweight task cards.  Detail endpoint keeps the full V19
product logic contract.  This prevents the frontend from downloading and parsing
large Agent payloads on every page switch.
"""

from __future__ import annotations

from typing import Any, Dict, List

from src.repositories.sqlite_repository import connect
from src.services.legacy_task_chain_cleanup_v2085_service import chain_integrity_for_item, latest_data_version, legacy_task_chain_status
from src.services.task_read_model_v2082_service import _payload, _row_get, _task_from_pipeline_row, pipeline_diagnostics
from src.services.v19_product_logic_contract_v2014_service import V19_PRODUCT_LOGIC_CONTRACT_VERSION, apply_v19_product_logic_contract

TASK_READ_MODEL_V2085_VERSION = "20.15"
DONE_STATUS = {"已完成", "已确认", "已归档", "已通过", "已写入复盘"}
HEAVY_LIST_FIELDS = {
    "metricEvidence", "systemFacts", "agent1OperatingJudgment", "agentOperatingJudgment", "agentJudgment",
    "agent2ActionPlan", "sopDecision", "actionParameterPack", "taskDetailReport", "systemChangePack",
    "dynamicMetricChanges", "v19ProductLogicContract", "chainIntegrity",
}


def _table_exists(conn: Any, table: str) -> bool:
    return bool(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _chain_stamp_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    stamp = payload.get("chainIntegrity") or payload.get("agentChainIntegrity") or payload.get("v20ChainIntegrity")
    if isinstance(stamp, dict):
        return {"passed": stamp.get("passed") is True or stamp.get("status") == "passed", "source": "payload", "stamp": stamp}
    if stamp == "passed":
        return {"passed": True, "source": "payload", "stamp": stamp}
    return {"passed": False, "source": "payload", "stamp": stamp}


def _task_row_integrity(conn: Any, row: Any) -> Dict[str, Any]:
    payload_stamp = _chain_stamp_from_payload(_payload(row))
    if payload_stamp.get("passed"):
        return payload_stamp
    event_stamp = chain_integrity_for_item(conn, row["item_id"])
    event_stamp["source"] = "pipeline_item_events"
    return event_stamp


def _task_rows(data_version: str | None = None, limit: int = 200) -> List[Any]:
    resolved = data_version or latest_data_version()
    with connect() as conn:
        if not _table_exists(conn, "pipeline_items"):
            return []
        params: List[Any] = []
        where = "WHERE current_stage = 'task_admitted' AND task_id IS NOT NULL AND task_id != ''"
        if resolved:
            where += " AND data_version = ?"
            params.append(resolved)
        rows = conn.execute(f"SELECT * FROM pipeline_items {where} ORDER BY updated_at DESC LIMIT ?", (*params, int(limit) * 5)).fetchall()
        passed = []
        for row in rows:
            if _task_row_integrity(conn, row).get("passed"):
                passed.append(row)
            if len(passed) >= int(limit):
                break
        return passed


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value not in [None, "", [], {}, "—", "UNKNOWN", "未识别"]:
            return value
    return None


def _nested_plan(payload: Dict[str, Any]) -> Dict[str, Any]:
    sop = _dict(payload.get("sopDecision"))
    plan = _dict(sop.get("taskPlan"))
    if plan:
        return plan
    action = _dict(payload.get("agent2ActionPlan"))
    return _dict(action.get("taskPlan"))


def _merge_nested_task_payload(task: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    task = dict(task or {})
    plan = _nested_plan(payload)
    for key in [
        "metricEvidence", "systemFacts", "systemChangePack", "dynamicMetricChanges",
        "agent1OperatingJudgment", "agentOperatingJudgment", "operatorJudgmentView", "agentJudgment",
        "agent2ActionPlan", "sopDecision", "actionParameterPack", "taskCard", "v19ProductLogicContract",
    ]:
        if key in payload and key not in task:
            task[key] = payload[key]
    if plan:
        task.setdefault("taskPlan", plan)
        if not task.get("operatorExecutionSop"):
            task["operatorExecutionSop"] = plan.get("operatorExecutionSop") or plan.get("operatorActionSteps")
        identity = _dict(task.get("productIdentity")) or _dict(plan.get("productIdentity")) or _dict(payload.get("productIdentity"))
        if identity:
            task["productIdentity"] = identity
            task["productId"] = task.get("productId") or identity.get("productId") or plan.get("productId") or payload.get("productId")
            task["storeId"] = task.get("storeId") or identity.get("storeId") or plan.get("storeId") or payload.get("storeId")
            task["storeName"] = task.get("storeName") or identity.get("storeName") or plan.get("storeName") or payload.get("storeName")
            task["platform"] = task.get("platform") or identity.get("platform") or plan.get("platform") or payload.get("platform")
        if not task.get("reviewMetrics") and isinstance(plan.get("reviewMetrics"), list):
            task["reviewMetrics"] = plan.get("reviewMetrics")
    task, _ = apply_v19_product_logic_contract(task, payload, _dict(task.get("taskDetailReport")))
    return task


def _detail_report(task: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    report = dict(_dict(task.get("taskDetailReport")) or _dict(payload.get("taskDetailReport")))
    plan = _nested_plan(payload)
    if plan:
        report.setdefault("taskPlan", plan)
    for key in [
        "sopDecision", "agent2ActionPlan", "agent1OperatingJudgment", "agentOperatingJudgment",
        "operatorJudgmentView", "agentJudgment", "metricEvidence", "systemFacts", "systemChangePack",
        "dynamicMetricChanges", "productIdentity", "productActionCards", "actionParameterPack", "taskCard",
        "v19ProductLogicContract",
    ]:
        if key in payload and key not in report:
            report[key] = payload[key]
    if task.get("operatorExecutionSop") and "operatorExecutionSop" not in report:
        report["operatorExecutionSop"] = task.get("operatorExecutionSop")
    _, report = apply_v19_product_logic_contract(task, payload, report)
    return report


def _light_task(task: Dict[str, Any]) -> Dict[str, Any]:
    plan = _dict(task.get("taskPlan"))
    card = _dict(task.get("taskCard"))
    product = _dict(task.get("productIdentity")) or (_dict(task.get("productActionCards", [{}])[0]) if isinstance(task.get("productActionCards"), list) and task.get("productActionCards") else {})
    lifecycle = _dict(task.get("taskLifecycle"))
    title = _first(task.get("title"), card.get("title"), plan.get("title"), product.get("productTitle"), product.get("title"), task.get("productTitle"), "经营任务")
    out = {
        "id": _first(task.get("id"), task.get("taskId")),
        "taskId": _first(task.get("taskId"), task.get("id")),
        "title": title,
        "status": task.get("status") or task.get("workflowStatus") or "待接收",
        "workflowStatus": task.get("workflowStatus") or task.get("status") or "待接收",
        "displayStatus": task.get("displayStatus"),
        "productId": task.get("productId") or product.get("productId"),
        "productTitle": product.get("productTitle") or product.get("title") or task.get("productTitle"),
        "productIdentity": product,
        "storeId": task.get("storeId") or product.get("storeId"),
        "storeName": task.get("storeName") or product.get("storeName"),
        "store": task.get("store") or task.get("storeName") or product.get("storeName"),
        "platform": task.get("platform") or product.get("platform"),
        "actionFamily": task.get("actionFamily") or plan.get("actionFamily") or plan.get("selectedActionFamily"),
        "riskDomain": task.get("riskDomain") or plan.get("selectedActionFamilyLabel") or plan.get("testFocus"),
        "reason": task.get("reason") or _dict(task.get("operatorJudgmentView")).get("displayReason"),
        "priority": task.get("priority") or card.get("priority") or "中",
        "riskLevel": task.get("riskLevel") or card.get("riskLevel"),
        "executionDeadline": task.get("executionDeadline") or card.get("executionDeadline") or plan.get("executionDeadline"),
        "deadline": task.get("deadline") or card.get("deadline") or plan.get("deadline"),
        "deadlineMinutes": task.get("deadlineMinutes") or card.get("deadlineMinutes") or plan.get("deadlineMinutes"),
        "deadlineAt": task.get("deadlineAt") or card.get("deadlineAt"),
        "createdAt": task.get("createdAt"),
        "updatedAt": task.get("updatedAt"),
        "taskResponsibility": task.get("taskResponsibility") or plan.get("taskResponsibility") or "operator_growth",
        "taskType": task.get("taskType") or plan.get("taskType"),
        "queueType": task.get("queueType"),
        "decision": task.get("decision"),
        "displayState": task.get("displayState"),
        "taskLayer": task.get("taskLayer"),
        "taskLifecycle": lifecycle,
        "visibleTaskActions": task.get("visibleTaskActions") or [],
        "primaryTaskAction": task.get("primaryTaskAction"),
        "taskPlan": {k: v for k, v in plan.items() if k in {"selectedActionFamily", "actionFamily", "selectedActionFamilyLabel", "taskResponsibility", "taskType", "executionDeadline", "deadline", "deadlineMinutes", "testFocus", "businessHypothesis"}},
        "taskReadModelSource": task.get("taskReadModelSource"),
        "viewVersion": TASK_READ_MODEL_V2085_VERSION,
        "v19ProductLogicContractVersion": V19_PRODUCT_LOGIC_CONTRACT_VERSION,
    }
    return {k: v for k, v in out.items() if v not in [None, "", [], {}]}


def read_task_views_v2085(status: str | None = None, limit: int = 200, data_version: str | None = None) -> Dict[str, Any]:
    resolved = data_version or latest_data_version()
    items: List[Dict[str, Any]] = []
    with connect() as conn:
        for row in _task_rows(resolved, limit=limit):
            payload = _payload(row)
            task = _task_from_pipeline_row(conn, row)
            if not task:
                continue
            task = _merge_nested_task_payload(task, payload)
            integrity = _task_row_integrity(conn, row)
            task["chainIntegrity"] = integrity
            task["taskReadModelSource"] = "pipeline_items.task_admitted.chain_integrity_passed"
            task["viewVersion"] = TASK_READ_MODEL_V2085_VERSION
            task["v19ProductLogicContractVersion"] = V19_PRODUCT_LOGIC_CONTRACT_VERSION
            items.append(_light_task(task))
    if status:
        items = [item for item in items if item.get("status") == status or item.get("workflowStatus") == status]
    items = [item for item in items if item.get("status") not in DONE_STATUS and item.get("taskId")]
    return {
        "version": TASK_READ_MODEL_V2085_VERSION,
        "ready": bool(items),
        "count": len(items),
        "currentDataVersion": resolved,
        "items": items[: int(limit)],
        "lightweight": True,
        "pipelineDiagnostics": {},
        "legacyTaskChainStatus": {"skipped": True, "reason": "list_endpoint_lightweight"},
        "gate": {"name": "AgentChainIntegrityGate", "acceptedSource": "pipeline_items.task_admitted only"},
        "v19ProductLogicContractVersion": V19_PRODUCT_LOGIC_CONTRACT_VERSION,
        "rule": "V20.15: task list returns lightweight cards; full Agent payload is available only from /api/view/tasks/{task_id}.",
    }


def read_task_detail_v2085(task_id: str, data_version: str | None = None) -> Dict[str, Any]:
    resolved = data_version or latest_data_version()
    with connect() as conn:
        if not _table_exists(conn, "pipeline_items"):
            return {"version": TASK_READ_MODEL_V2085_VERSION, "ready": False, "taskId": task_id, "reason": "no_pipeline_items"}
        params: List[Any] = [task_id]
        where = "WHERE task_id = ? AND current_stage = 'task_admitted'"
        if resolved:
            where += " AND data_version = ?"
            params.append(resolved)
        row = conn.execute(f"SELECT * FROM pipeline_items {where} ORDER BY updated_at DESC LIMIT 1", tuple(params)).fetchone()
        if not row:
            return {"version": TASK_READ_MODEL_V2085_VERSION, "ready": False, "taskId": task_id, "currentDataVersion": resolved, "reason": "task_not_found_in_strict_v20_task_admitted_items", "legacyTaskChainStatus": legacy_task_chain_status(resolved)}
        integrity = _task_row_integrity(conn, row)
        if not integrity.get("passed"):
            return {"version": TASK_READ_MODEL_V2085_VERSION, "ready": False, "taskId": task_id, "currentDataVersion": resolved, "reason": "chain_integrity_not_passed", "chainIntegrity": integrity, "legacyTaskChainStatus": legacy_task_chain_status(resolved)}
        payload = _payload(row)
        task = _task_from_pipeline_row(conn, row)
        if not task:
            return {"version": TASK_READ_MODEL_V2085_VERSION, "ready": False, "taskId": task_id, "reason": "task_payload_missing"}
        task = _merge_nested_task_payload(task, payload)
        report = _detail_report(task, payload)
        task, report = apply_v19_product_logic_contract(task, payload, report)
        task["taskDetailReport"] = report
        task["chainIntegrity"] = integrity
        task["taskReadModelSource"] = "pipeline_items.task_admitted.chain_integrity_passed"
        task["viewVersion"] = TASK_READ_MODEL_V2085_VERSION
        task["v19ProductLogicContractVersion"] = V19_PRODUCT_LOGIC_CONTRACT_VERSION
        detail = {
            "viewVersion": TASK_READ_MODEL_V2085_VERSION,
            "id": task_id,
            "taskId": task_id,
            "dataVersion": _row_get(row, "data_version"),
            "relatedTask": task,
            "taskCard": task.get("taskCard") or report.get("taskCard"),
            "taskDetailReport": report,
            "productIdentity": task.get("productIdentity") or report.get("productIdentity"),
            "systemChangePack": report.get("systemChangePack") or task.get("systemChangePack"),
            "dynamicMetricChanges": report.get("dynamicMetricChanges") or task.get("dynamicMetricChanges"),
            "operatorJudgmentView": report.get("operatorJudgmentView") or task.get("operatorJudgmentView"),
            "operatorExecutionSop": report.get("operatorExecutionSop") or task.get("operatorExecutionSop"),
            "sopSteps": task.get("sopSteps") or report.get("sopSteps") or task.get("operatorExecutionSop"),
            "reviewMetrics": task.get("reviewMetrics") or report.get("reviewMetrics"),
            "agentJudgment": task.get("agentJudgment") or report.get("agentJudgment"),
            "agentOperatingJudgment": task.get("agentOperatingJudgment") or report.get("agentOperatingJudgment"),
            "v19ProductLogicContract": report.get("v19ProductLogicContract") or task.get("v19ProductLogicContract"),
            "chainIntegrity": integrity,
            "updatedAt": _row_get(row, "updated_at"),
        }
        return {
            "version": TASK_READ_MODEL_V2085_VERSION,
            "ready": True,
            "currentDataVersion": resolved,
            "item": detail,
            "cachedAt": _row_get(row, "updated_at"),
            "source": "pipeline_items_task_admitted_chain_integrity_passed",
            "v19ProductLogicContractVersion": V19_PRODUCT_LOGIC_CONTRACT_VERSION,
            "rule": "V20.15 task detail returns full V19 product logic contract from V20 payload.",
        }
