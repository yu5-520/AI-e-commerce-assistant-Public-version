"""Task report service for V12.9.1 lifecycle-state-machine reports."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from src.services.competition_operator_context_service import get_user
from src.services.module_task_service import find_task, list_tasks
from src.services.task_lifecycle_orchestrator_service import TASK_LIFECYCLE_VERSION, lifecycle_snapshot
from src.services.task_lifecycle_state_machine_service import TASK_LIFECYCLE_STATE_MACHINE_VERSION, get_lifecycle_task_projection

REPORT_VERSION = "12.9.1"

ROLE_INSIGHTS = {"operator": {"title": "运营工作台", "summary": "查看执行材料、任务状态和系统复盘。", "focus": ["提交材料", "执行状态", "复盘周期"], "hidden": ["企业组织审批", "部门角色视图"]}}



def _now() -> str:
    return datetime.now().isoformat()


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, default: str = "待确认") -> str:
    text = str(value or "").strip()
    return text or default


def _apply_role_insight(report: Dict[str, Any], user_id: str | None = None) -> Dict[str, Any]:
    user = get_user(user_id) or get_user(None) or {}
    report["viewer"] = {"userId": user.get("id") or "competition_operator", "name": user.get("name") or "赛事运营工作台", "roleName": "运营", "roleId": "operator", "permissionNames": user.get("permissionNames", [])}
    report["roleInsight"] = ROLE_INSIGHTS["operator"]
    report["enterpriseOrganizationCapabilities"] = "暂未开放"
    return report



def _task_lookup(task_id: str, user_id: str | None = None, ctx: Any | None = None) -> Dict[str, Any] | None:
    try:
        projected = get_lifecycle_task_projection(task_id, user_id, ctx=ctx)
        if projected:
            return projected
    except Exception:
        pass
    for viewer in (user_id, None):
        try:
            task = next((item for item in list_tasks(active_only=False, viewer_id=viewer) if item.get("id") == task_id), None)
            if task:
                return task
        except Exception:
            pass
    try:
        return find_task(task_id)
    except Exception:
        return None


def _safe_lifecycle(task: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(task.get("taskLifecycle"), dict):
        return task["taskLifecycle"]
    try:
        return lifecycle_snapshot(task)
    except Exception as exc:
        return {"version": TASK_LIFECYCLE_VERSION, "stateMachineVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "stage": task.get("lifecycleStage") or "generated", "stageLabel": "生成任务", "taskId": task.get("id"), "nextExpected": "返回任务列表继续处理", "error": str(exc), "recapCycles": []}


def _task_title(task: Dict[str, Any], task_id: str) -> str:
    card = task.get("taskCard") or {}
    return _text(card.get("title") or task.get("title") or task.get("productTitle") or task_id, "经营任务")


def _reason(task: Dict[str, Any]) -> str:
    detail = task.get("taskDetailReport") or {}
    return _text(task.get("reason") or detail.get("warningSummary") or task.get("riskDomain") or task.get("actionType") or "系统根据报表事实和经营规则生成该任务。", "系统生成经营任务。")


def _evidence_items(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in _as_list(task.get("evidencePack") or task.get("evidence")):
        if isinstance(item, dict):
            items.append({"label": _text(item.get("label") or item.get("metric") or item.get("title"), "证据"), "value": _text(item.get("value") or item.get("summary") or item.get("text"), "待确认")})
        else:
            items.append({"label": "证据", "value": _text(item)})
    gate = task.get("actionAuthorization") or task.get("v1282ActionGate") or {}
    budget = gate.get("budgetGate") if isinstance(gate.get("budgetGate"), dict) else {}
    if budget:
        items.append({"label": "预算权限", "value": f"{budget.get('operatorBudgetMin', '—')} - {budget.get('operatorBudgetMax', '—')}"})
        if budget.get("requestedBudget") is not None:
            items.append({"label": "申请预算", "value": budget.get("requestedBudget")})
    return items[:12]


def _affected_products(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    products = _as_list(task.get("affectedProducts"))
    if products:
        return products
    product_id = task.get("productId") or task.get("entityId")
    if product_id:
        return [{"productId": product_id, "title": task.get("productTitle") or task.get("title") or product_id, "storeName": task.get("storeName") or task.get("store"), "platform": task.get("platform")}]
    return []


def _steps(task: Dict[str, Any]) -> List[str]:
    gate = task.get("actionAuthorization") or task.get("v1282ActionGate") or {}
    fields = _as_list(gate.get("operatorFactFields"))
    steps = _as_list(task.get("sopSteps")) or _as_list((task.get("taskDetailReport") or {}).get("sopSteps"))
    if steps:
        return [str(item) for item in steps]
    if fields:
        return [f"提交{field}" for field in fields[:5]]
    return ["按任务要求提交处理材料", "等待系统自动复盘"]


def _source_trace(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [{"label": "任务ID", "value": task.get("id")}, {"label": "来源模块", "value": task.get("sourceModule") or task.get("module") or "任务系统"}, {"label": "任务类型", "value": task.get("taskType") or task.get("queueType") or "经营任务"}, {"label": "生命周期状态机", "value": TASK_LIFECYCLE_STATE_MACHINE_VERSION}]


def _structure_missing_report(task_id: str, user_id: str | None = None, task: Dict[str, Any] | None = None) -> Dict[str, Any]:
    task = task or {"id": task_id, "status": "待处理"}
    lifecycle = _safe_lifecycle(task)
    products = _affected_products(task)
    gate = task.get("actionAuthorization") or task.get("v1282ActionGate") or {}
    report = {
        "reportId": f"RPT-TASK-{task_id}", "reportType": "aggregate_task" if task.get("batchTask") or products else "task", "version": REPORT_VERSION,
        "lifecycleVersion": TASK_LIFECYCLE_VERSION, "lifecycleStateMachineVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "module": "task", "sourceModule": task.get("sourceModule") or "任务系统", "sourceRoute": "business-actions",
        "entityId": task.get("entityId") or task.get("productId") or task_id, "taskId": task.get("id") or task_id, "taskStatus": task.get("status") or "待处理", "generatedAt": _now(),
        "title": _task_title(task, task_id), "warningSummary": _reason(task), "riskLevel": task.get("priority") or "中", "evidence": _evidence_items(task),
        "evidenceChain": [{"title": "触发原因", "summary": _reason(task)}, {"title": "当前动作", "summary": gate.get("actionLabel") or task.get("actionType") or "运营处理"}],
        "suggestedActions": _steps(task), "operationChecklist": _steps(task), "dataNeeded": [{"title": "运营提交材料", "summary": item} for item in _as_list(gate.get("operatorFactFields"))],
        "affectedProducts": products, "affectedProductCount": task.get("affectedProductCount") or len(products), "actionAuthorization": gate, "actionImpactEstimate": task.get("actionImpactEstimate") or task.get("v126ImpactEstimate"),
        "ragBusinessMemory": task.get("ragBusinessMemory") or task.get("v126RagMemory"), "taskLifecycle": lifecycle, "recapCycles": lifecycle.get("recapCycles") or [], "ragCandidate": task.get("ragCandidate") or lifecycle.get("ragCandidate"),
        "autoRecapResult": task.get("autoRecapResult"), "nextStep": lifecycle.get("nextExpected") or "按任务卡当前动作继续处理。", "sourceTrace": _source_trace(task),
        "responsibility": {"store": {"storeName": task.get("storeName") or task.get("store"), "platform": task.get("platform")}, "operatorName": task.get("operatorName") or task.get("assigneeName") or "运营账号", "reviewerName": task.get("reviewerName") or "企业组织协同版暂未开放"},
        "triggerRule": {"name": task.get("riskDomain") or "经营任务触发", "status": task.get("status") or "已生成", "rule": _reason(task)}, "fallbackDetail": bool(not task.get("taskDetailReport")), "structureMissing": bool(not task.get("taskDetailReport")), "failClosed": False,
        "relatedTask": task, "rule": "V12.9.1：详情报告读取Repository-aware生命周期投影，同一个task_id贯穿自动接收、提交、复核、自动复盘和RAG候选。",
    }
    return _apply_role_insight(report, user_id)


def _report_from_structured_task(task: Dict[str, Any], task_id: str, user_id: str | None = None) -> Dict[str, Any]:
    detail = dict(task.get("taskDetailReport") or {})
    base = _structure_missing_report(task_id, user_id, task)
    for key, value in base.items():
        detail.setdefault(key, value)
    detail.update({"version": REPORT_VERSION, "lifecycleStateMachineVersion": TASK_LIFECYCLE_STATE_MACHINE_VERSION, "taskStatus": task.get("status"), "evidence": _evidence_items(task) or detail.get("evidence") or [], "sopSteps": _as_list(task.get("sopSteps")) or _as_list(detail.get("sopSteps")) or _steps(task), "affectedProducts": _affected_products(task), "taskLifecycle": _safe_lifecycle(task), "relatedTask": task, "fallbackDetail": False, "structureMissing": False, "failClosed": False})
    detail["affectedProductCount"] = task.get("affectedProductCount") or len(detail["affectedProducts"])
    detail["recapCycles"] = detail["taskLifecycle"].get("recapCycles") or []
    return _apply_role_insight(detail, user_id)


def get_candidate_report(module: str, entity_id: str, user_id: str | None = None) -> Dict[str, Any] | None:
    return _structure_missing_report(entity_id, user_id, {"id": entity_id, "module": module, "sourceModule": module, "status": "候选预警"})


def get_task_report(task_id: str, user_id: str | None = None, ctx: Any | None = None) -> Dict[str, Any] | None:
    try:
        task = _task_lookup(task_id, user_id, ctx=ctx)
        if not task:
            return _structure_missing_report(task_id, user_id)
        if not task.get("taskDetailReport"):
            return _structure_missing_report(task_id, user_id, task)
        return _report_from_structured_task(task, task_id, user_id)
    except Exception as exc:
        return _structure_missing_report(task_id, user_id, {"id": task_id, "status": "详情生成异常", "reason": f"详情生成异常：{exc}"})
