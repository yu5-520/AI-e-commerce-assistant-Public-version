"""V12.2.7 risk task wrapper with strict scoped evidence gate and approval lifecycle sync."""

from __future__ import annotations

from typing import Any, Dict, List

from src.services import module_task_service
from src.services.approval_lifecycle_service import create_approval_flow_for_task, ensure_approval_lifecycle_tables
from src.services.risk_task_v65_service import ensure_risk_task_tables, risk_task_summary as _risk_task_summary
from src.services.risk_task_v65_service import generate_risk_tasks_for_signals as _generate_v65
from src.services.task_evidence_gate_service import TASK_EVIDENCE_GATE_VERSION, apply_evidence_gate_to_created_task, task_evidence_gate_summary

RISK_TASK_VERSION = "12.2.7"


def _gate_created_tasks(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    gated_tasks: List[Dict[str, Any]] = []
    blocked_count = 0
    passed_count = 0
    degraded_count = 0
    for task in tasks:
        gated = apply_evidence_gate_to_created_task(dict(task))
        gate = gated.get("evidenceGate") or {}
        if gate.get("gateStatus") == "blocked":
            blocked_count += 1
        elif gate.get("gateStatus") == "passed":
            passed_count += 1
        else:
            degraded_count += 1
        updated = module_task_service.update_task(
            str(task.get("id")),
            gated,
            log_type="任务证据闸门",
            action="证据校验",
            result="证据不足时已降级为补证任务。" if gate.get("gateStatus") == "blocked" else "证据闸门已记录。",
        )
        gated_tasks.append(updated or gated)
    return {
        "version": TASK_EVIDENCE_GATE_VERSION,
        "gatedTaskCount": len(gated_tasks),
        "blockedTaskCount": blocked_count,
        "passedTaskCount": passed_count,
        "degradedTaskCount": degraded_count,
        "tasks": gated_tasks,
        "rule": "任务必须先来自经营判断；证据按 metric_scope 严格校验；关键证据不足时降级为补证任务。",
    }


def generate_risk_tasks_for_signals(data_version: str | None = None, limit: int = 200, requester_role_id: str = "operator") -> Dict[str, Any]:
    """Generate SOP task packages, gate them by evidence, then attach approvals."""
    ensure_risk_task_tables()
    ensure_approval_lifecycle_tables()
    result = _generate_v65(data_version=data_version, limit=limit, requester_role_id=requester_role_id)
    evidence_gate_sync = _gate_created_tasks(result.get("tasks") or [])
    result["tasks"] = evidence_gate_sync["tasks"]
    flows = []
    for task in result.get("tasks") or []:
        budget = task.get("permissionBudgetGate") or {}
        if task.get("queueType") in {"urgent_execution", "today_execution"} and (task.get("riskGrade") == "高" or budget.get("needsApproval") or task.get("approvalChain")):
            flows.append(create_approval_flow_for_task(task, requester_role_id=requester_role_id))
    result["version"] = RISK_TASK_VERSION
    result["mode"] = "v12_2_7_strict_scoped_evidence_gated_sop_task_generation"
    result["createdTaskCount"] = len(result.get("tasks") or [])
    result["evidenceGateSync"] = evidence_gate_sync
    result["approvalLifecycleSync"] = {
        "version": RISK_TASK_VERSION,
        "createdFlowCount": len(flows),
        "flows": flows,
        "rule": "V12.2.7 只有证据闸门通过的高风险结构化任务包进入审批生命周期；补证任务不走高风险审批。",
    }
    result["rule"] = "任务生成从经营判断开始；缺字段只在阻塞当前判断时升级为补证任务；ROI按 product/traffic/store 口径隔离取证。"
    return result


def risk_task_summary(limit: int = 30) -> Dict[str, Any]:
    summary = _risk_task_summary(limit=limit)
    summary["version"] = RISK_TASK_VERSION
    summary["evidenceGateSummary"] = task_evidence_gate_summary()
    return summary
