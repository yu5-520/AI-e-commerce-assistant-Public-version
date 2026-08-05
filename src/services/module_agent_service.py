"""V5 module Agent service.

Agent inputs now come from imported-data module projections instead of the old
runtime fallback arrays. The Agent layer remains advisory-only: it creates
summaries, task drafts, and handling packages, but never executes business actions.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List

from src.services.account_service import current_user
from src.services.action_plan_service import action_plan_for_problem, infer_action_problem_type
from src.services.module_projection_service import projected_products, projected_report_details, projected_report_groups, projected_traffic
from src.services.module_task_service import create_task, list_tasks

AGENT_VERSION = "5.0.2"
FORBIDDEN_ACTIONS = ["不直接改价", "不直接投放", "不直接退款", "不直接修改真实店铺", "不直接回写 ERP / CRM"]
AGENT_BOUNDARY = "Agent 只生成建议、草案、摘要和任务拆解，不直接执行经营动作。"

MODULE_META: Dict[str, Dict[str, str]] = {
    "product": {"label": "商品经营列表", "route": "business-products", "entityType": "商品"},
    "competitor": {"label": "竞品观察列表", "route": "business-competitors", "entityType": "竞品"},
    "listing": {"label": "上新测试台", "route": "business-listing", "entityType": "上新"},
    "traffic": {"label": "流量测试台", "route": "business-traffic", "entityType": "流量"},
    "report": {"label": "ERP / CRM 报表管理", "route": "data-check", "entityType": "报表"},
    "task": {"label": "统一任务池", "route": "business-actions", "entityType": "任务"},
}


def _now() -> str:
    return datetime.now().isoformat()


def _viewer(user_id: str | None) -> Dict[str, Any]:
    user = current_user(user_id)
    return {"userId": user.get("id"), "roleName": user.get("roleName"), "insightDepth": user.get("insightDepth")}


def _meta(module: str) -> Dict[str, str]:
    return MODULE_META.get(module, {"label": "经营模块", "route": "dashboard", "entityType": "经营对象"})


def _agent_id(module: str, entity_id: str, mode: str) -> str:
    return f"AGENT-V502-{module.upper()}-{entity_id}-{mode.replace(' ', '-') or 'analysis'}"


def _problem(module: str, item: Dict[str, Any]) -> str:
    return infer_action_problem_type(item, source_module=module)


def _level_from_text(text: str) -> str:
    if any(word in text for word in ["暂停", "退款", "售后", "告急", "danger", "风险", "低 ROI", "ROI 低"]):
        return "高"
    if any(word in text for word in ["偏高", "偏低", "warning", "复查", "谨慎", "待补货"]):
        return "中"
    return "低"


def _common_result(*, module: str, entity_id: str, mode: str, agent_name: str, summary: str, evidence: List[Dict[str, Any]], suggestions: List[str], task_drafts: List[Dict[str, Any]], human_decision: List[str], next_step: str, risk_level: str | None = None, input_snapshot: Dict[str, Any] | None = None, extra: Dict[str, Any] | None = None, user_id: str | None = None) -> Dict[str, Any]:
    meta = _meta(module)
    text = " ".join([summary, next_step, *(suggestions or [])])
    result = {
        "agentId": _agent_id(module, entity_id, mode),
        "agentName": agent_name,
        "agentVersion": AGENT_VERSION,
        "mode": mode,
        "sourceModule": meta["label"],
        "sourceRoute": meta["route"],
        "module": module,
        "entityType": meta["entityType"],
        "entityId": entity_id,
        "generatedAt": _now(),
        "viewer": _viewer(user_id),
        "inputSnapshot": input_snapshot or {},
        "riskLevel": risk_level or _level_from_text(text),
        "summary": summary,
        "evidence": evidence,
        "suggestions": suggestions,
        "taskDrafts": task_drafts,
        "humanDecision": human_decision,
        "forbiddenActions": FORBIDDEN_ACTIONS,
        "boundary": AGENT_BOUNDARY,
        "nextStep": next_step,
    }
    if extra:
        result.update(extra)
    return result


def _enrich_with_plan(draft: Dict[str, Any], plan: Dict[str, Any]) -> Dict[str, Any]:
    package = plan.get("recommendedPackage") or {}
    draft.update({
        "problemType": plan.get("problemType"),
        "actionPlan": plan,
        "selectedPackage": package,
        "executionPackages": plan.get("executionPackages") or [],
        "executionSteps": plan.get("executionSteps") or [],
        "evidenceRequired": plan.get("evidenceRequired") or [],
        "submitMetrics": plan.get("submitMetrics") or [],
        "acceptanceCriteria": plan.get("acceptanceCriteria") or [],
        "failureThreshold": plan.get("failureThreshold") or [],
        "reviewFocus": plan.get("reviewFocus") or [],
        "actionType": plan.get("actionPlanType") or draft.get("actionType"),
        "taskType": "V5 问题类型处理包",
        "taskSignal": "projection + problemType + ActionPlan + 人工确认",
        "task": f"执行“{package.get('packageName') or plan.get('actionPlanType')}”，按问题类型处理，不套通用模板。",
        "agentJudgment": {**(draft.get("agentJudgment") or {}), "version": AGENT_VERSION, "problemType": plan.get("problemType"), "actionPlanType": plan.get("actionPlanType"), "summary": plan.get("diagnosis"), "boundary": AGENT_BOUNDARY, "forbiddenActions": FORBIDDEN_ACTIONS},
    })
    return draft


def _draft_base(module: str, item: Dict[str, Any], title: str, task: str, reason: str, risk_domain: str, priority: str = "中") -> Dict[str, Any]:
    meta = _meta(module)
    product_id = item.get("productId") or item.get("id")
    store_ids = [item.get("storeId")] if item.get("storeId") else []
    return {
        "title": title,
        "task": task,
        "reason": reason,
        "priority": priority,
        "priorityLevel": "danger" if priority == "高" else "warning" if priority == "中" else "good",
        "deadline": "今天内" if priority == "高" else "明天前",
        "riskDomain": risk_domain,
        "actionType": "处理",
        "entityType": meta["entityType"],
        "entityId": item.get("id") or product_id,
        "source": "V5 ModuleProjection Agent",
        "sourceModule": meta["label"],
        "sourceRoute": meta["route"],
        "productRoute": meta["route"],
        "storeIds": store_ids,
        "visibleStoreIds": store_ids,
        "productId": product_id,
        "productTitle": item.get("title") or item.get("name") or title,
        "productShort": item.get("shortName") or item.get("targetProduct") or item.get("sourceName") or item.get("name") or item.get("id") or "对象",
        "platform": item.get("platform") or item.get("source") or "经营单元",
        "store": item.get("store") or "经营单元",
        "judgmentTags": [value for value in [risk_domain, item.get("status"), item.get("inventoryStatus"), item.get("afterSales"), item.get("channel")] if value],
        "createdByRole": "agent",
        "agentJudgment": {"status": "advisory", "version": AGENT_VERSION, "summary": reason, "boundary": AGENT_BOUNDARY, "forbiddenActions": FORBIDDEN_ACTIONS},
    }


def get_agent_plan() -> Dict[str, Any]:
    return {
        "version": AGENT_VERSION,
        "mode": "v5_module_projection_agent_layer",
        "principle": "模块内容来自报表导入后的 ModuleProjection，Agent 在投影数据上生成处理包和任务草案。",
        "boundary": AGENT_BOUNDARY,
        "forbiddenActions": FORBIDDEN_ACTIONS,
        "agents": [
            {"id": "product-projection-agent", "name": "商品投影处理 Agent", "module": "product", "output": "商品 / 库存 / 售后 / 毛利处理包"},
            {"id": "traffic-projection-agent", "name": "流量投影复盘 Agent", "module": "traffic", "output": "订单承接、库存承接、ROI 复核包"},
            {"id": "report-projection-agent", "name": "报表投影摘要 Agent", "module": "report", "output": "导入行、数据版本、字段影响和经营任务包"},
            {"id": "task-playbook", "name": "任务处理包 Agent", "module": "task", "output": "执行步骤、证据、复核标准"},
        ],
    }


def _find_projected_product(entity_id: str, user_id: str | None) -> Dict[str, Any] | None:
    return next((item for item in projected_products(user_id) if item.get("id") == entity_id or item.get("productId") == entity_id), None)


def _find_projected_traffic(entity_id: str, user_id: str | None) -> Dict[str, Any] | None:
    return next((item for item in projected_traffic(user_id) if item.get("id") == entity_id or item.get("productId") == entity_id), None)


def _flatten_reports(user_id: str | None) -> List[Dict[str, Any]]:
    return [report for group in projected_report_groups(user_id) for report in group.get("reports", [])]


def _find_projected_report(entity_id: str, user_id: str | None) -> Dict[str, Any] | None:
    detail = projected_report_details(user_id).get(entity_id)
    list_item = next((item for item in _flatten_reports(user_id) if item.get("id") == entity_id), None)
    if detail or list_item:
        return {**(list_item or {"id": entity_id, "name": entity_id}), **(detail or {})}
    return None


def _generic_agent(module: str, item: Dict[str, Any], mode: str, user_id: str | None) -> Dict[str, Any]:
    problem = _problem(module, item)
    plan = action_plan_for_problem(problem, item=item, source_module=module)
    package = plan.get("recommendedPackage") or {}
    title = item.get("title") or item.get("name") or item.get("shortName") or item.get("id") or "经营对象"
    risk_domain = plan.get("actionPlanType") or item.get("riskDomain") or _meta(module)["entityType"]
    priority = "高" if item.get("statusLevel") == "danger" or item.get("inventoryLevel") == "danger" or item.get("afterSalesLevel") in {"warning", "danger"} else "中"
    draft = _draft_base(module, item, f"处理{title}的{plan.get('problemLabel') or risk_domain}问题", "按问题类型处理包执行。", item.get("suggestion") or item.get("nextStep") or plan.get("diagnosis") or "导入数据形成经营信号，需要人工确认。", risk_domain, priority)
    draft = _enrich_with_plan(draft, plan)
    evidence = [
        {"label": "数据版本", "value": ", ".join(item.get("sourceDataVersions") or [str(item.get("dataVersion") or item.get("latestDataVersion") or "—")])},
        {"label": "店铺", "value": item.get("store") or item.get("storeId") or "按权限切片"},
        {"label": "状态", "value": item.get("status") or item.get("inventoryStatus") or item.get("afterSales") or item.get("count") or "已生成"},
        {"label": "处理包", "value": package.get("packageName") or plan.get("actionPlanType")},
    ]
    return _common_result(module=module, entity_id=str(item.get("id") or item.get("productId")), mode=mode, agent_name=f"{_meta(module)['label']} Agent", summary=plan.get("diagnosis") or f"{title} 已形成可处理信号。", evidence=evidence, suggestions=plan.get("executionSteps") or [], task_drafts=[draft], human_decision=["选择处理包", "确认是否进入任务池", "确认复核指标"], next_step="人工确认处理包后加入任务池。", risk_level=priority, input_snapshot=deepcopy(item), extra={"problemType": problem, "actionPlan": plan, "executionPackages": plan.get("executionPackages") or []}, user_id=user_id)


def run_module_agent(module: str, entity_id: str, mode: str = "analysis", user_id: str | None = None) -> Dict[str, Any] | None:
    module = module.strip().lower()
    if module == "product":
        item = _find_projected_product(entity_id, user_id)
    elif module == "traffic":
        item = _find_projected_traffic(entity_id, user_id)
    elif module == "report":
        item = _find_projected_report(entity_id, user_id)
    elif module == "task":
        item = next((task for task in list_tasks(active_only=False, viewer_id=user_id) if task.get("id") == entity_id), None)
    else:
        item = None
    return _generic_agent(module, item, mode, user_id) if item else None


def create_agent_task(module: str, entity_id: str, draft_index: int = 0, mode: str = "analysis", user_id: str | None = None) -> Dict[str, Any] | None:
    agent_result = run_module_agent(module, entity_id, mode=mode, user_id=user_id)
    if not agent_result:
        return None
    drafts = agent_result.get("taskDrafts") or []
    if draft_index < 0 or draft_index >= len(drafts):
        return None
    draft = deepcopy(drafts[draft_index])
    draft["agentJudgment"] = {**(draft.get("agentJudgment") or {}), "status": "advisory_confirmed", "version": AGENT_VERSION, "confirmedBy": _viewer(user_id)}
    task = create_task(draft)
    return {"agent": agent_result, "task": task, "message": "Agent 任务草案已进入统一任务池。"}


def run_cycle_agent(target: str = "日报", user_id: str | None = None) -> Dict[str, Any]:
    tasks = list_tasks(active_only=False, viewer_id=user_id)
    active = [task for task in tasks if task.get("status") not in {"已完成", "已归档", "已写入复盘"}]
    completed = [task for task in tasks if task.get("status") in {"已完成", "已归档", "已写入复盘"}]
    summary = f"{target}范围内共有 {len(tasks)} 条任务，未完成 {len(active)} 条，已完成 {len(completed)} 条。"
    return _common_result(module="task", entity_id=target, mode="cycle", agent_name="周期复盘 Agent", summary=summary, evidence=[{"label": "全部任务", "value": len(tasks)}, {"label": "未完成", "value": len(active)}, {"label": "已完成", "value": len(completed)}], suggestions=["先处理高优先级未完成任务", "复核已完成任务是否沉淀经验"], task_drafts=[], human_decision=["是否写入日报", "是否生成周报候选"], next_step="确认复盘口径后写入日志或经验回流。", input_snapshot={"target": target, "taskCount": len(tasks)}, user_id=user_id)
