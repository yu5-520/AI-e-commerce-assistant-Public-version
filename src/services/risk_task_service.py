"""V12.13 task generation station facade."""

from __future__ import annotations

from typing import Any, Dict

from src.services.action_authorization_gate_service import ACTION_AUTHORIZATION_VERSION
from src.services.action_impact_estimation_service import ACTION_IMPACT_ESTIMATION_VERSION
from src.services.operating_cadence_task_service import OPERATING_CADENCE_VERSION, generate_operating_cadence_tasks, operating_cadence_summary
from src.services.operating_weight_policy_service import OPERATING_WEIGHT_POLICY_VERSION
from src.services.rag_business_memory_service import RAG_BUSINESS_MEMORY_VERSION
from src.services.rag_feedback_loop_service import RAG_FEEDBACK_LOOP_VERSION
from src.services.risk_task_v66_service import RISK_TASK_VERSION as STRICT_RISK_TASK_VERSION
from src.services.risk_task_v66_service import ensure_risk_task_tables, generate_risk_tasks_for_signals as _generate_scoped_risk_tasks
from src.services.risk_task_v66_service import risk_task_summary as _scoped_risk_task_summary
from src.services.task_cluster_service import TASK_CLUSTER_VERSION, cluster_open_tasks
from src.services.task_lifecycle_orchestrator_service import TASK_LIFECYCLE_VERSION, lifecycle_summary
from src.services.task_recap_scheduler_service import RECAP_SCHEDULER_VERSION, recap_schedule_summary

RISK_TASK_VERSION = "12.13.0"


def _action_gate_counts(tasks: list[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for task in tasks:
        decision = ((task.get("actionAuthorization") or {}).get("decision") or "not_applied")
        counts[decision] = counts.get(decision, 0) + 1
    return counts


def _weight_gate_counts(tasks: list[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for task in tasks:
        weight = ((task.get("actionAuthorization") or {}).get("objectWeight") or {})
        key = f"{weight.get('weightLevel', 'unknown')}:{weight.get('weightConfidence', 'unknown')}:{weight.get('weightSource', 'unknown')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def generate_risk_tasks_for_signals(data_version: str | None = None, limit: int = 200, requester_role_id: str = "operator") -> Dict[str, Any]:
    risk_result = _generate_scoped_risk_tasks(data_version=data_version, limit=limit, requester_role_id=requester_role_id)
    cadence_result = generate_operating_cadence_tasks(data_version=data_version, max_tasks=16)
    cluster_result = cluster_open_tasks()
    risk_tasks = risk_result.get("tasks") or []
    cadence_tasks = cadence_result.get("tasks") or []
    all_tasks = [*risk_tasks, *cadence_tasks]
    return {
        **risk_result,
        "version": RISK_TASK_VERSION,
        "mode": "v12_13_task_signal_station_no_page_trigger",
        "dataVersion": data_version,
        "strictRiskTaskVersion": STRICT_RISK_TASK_VERSION,
        "operatingCadenceVersion": OPERATING_CADENCE_VERSION,
        "actionAuthorizationVersion": ACTION_AUTHORIZATION_VERSION,
        "actionImpactEstimationVersion": ACTION_IMPACT_ESTIMATION_VERSION,
        "ragBusinessMemoryVersion": RAG_BUSINESS_MEMORY_VERSION,
        "ragFeedbackLoopVersion": RAG_FEEDBACK_LOOP_VERSION,
        "operatingWeightPolicyVersion": OPERATING_WEIGHT_POLICY_VERSION,
        "taskClusterVersion": TASK_CLUSTER_VERSION,
        "taskLifecycleVersion": TASK_LIFECYCLE_VERSION,
        "recapSchedulerVersion": RECAP_SCHEDULER_VERSION,
        "taskClusterSync": cluster_result,
        "taskLifecycleSync": lifecycle_summary(limit=80),
        "recapScheduleSync": recap_schedule_summary(),
        "primaryAxis": "ROI_GMV",
        "baselineMode": bool(cadence_result.get("baselineMode")),
        "comparisonReady": bool(cadence_result.get("comparisonReady")),
        "trendReady": bool(cadence_result.get("trendReady")),
        "createdTaskCount": len(all_tasks),
        "strictRiskCreatedTaskCount": len(risk_tasks),
        "operatingCadenceCreatedTaskCount": len(cadence_tasks),
        "clusteredTaskCount": cluster_result.get("clusterCount", 0),
        "mergedDuplicateTaskCount": cluster_result.get("mergedDuplicateCount", 0),
        "blockedByBaselineCount": cadence_result.get("blockedByBaselineCount", 0),
        "actionGateDecisionCounts": _action_gate_counts(all_tasks),
        "weightGateCounts": _weight_gate_counts(all_tasks),
        "tasks": all_tasks,
        "strictRiskSync": risk_result,
        "operatingCadenceSync": cadence_result,
        "dailyReportSeedCount": len(cadence_result.get("topSignals") or []),
        "rule": "V12.13：任务生成是pipeline独立站点；经营页刷新不能触发任务生成、RAG检索或LLM增强。",
    }


def risk_task_summary(limit: int = 30) -> Dict[str, Any]:
    summary = _scoped_risk_task_summary(limit=limit)
    cadence = operating_cadence_summary(limit=limit)
    summary["version"] = RISK_TASK_VERSION
    summary["primaryAxis"] = "ROI_GMV"
    summary["baselineFirst"] = True
    summary["actionGateVersion"] = ACTION_AUTHORIZATION_VERSION
    summary["actionImpactEstimationVersion"] = ACTION_IMPACT_ESTIMATION_VERSION
    summary["ragBusinessMemoryVersion"] = RAG_BUSINESS_MEMORY_VERSION
    summary["ragFeedbackLoopVersion"] = RAG_FEEDBACK_LOOP_VERSION
    summary["operatingWeightPolicyVersion"] = OPERATING_WEIGHT_POLICY_VERSION
    summary["taskClusterVersion"] = TASK_CLUSTER_VERSION
    summary["taskLifecycleVersion"] = TASK_LIFECYCLE_VERSION
    summary["recapSchedulerVersion"] = RECAP_SCHEDULER_VERSION
    summary["operatingCadenceSummary"] = cadence
    summary["lifecycleSummary"] = lifecycle_summary(limit=limit)
    summary["recapScheduleSummary"] = recap_schedule_summary()
    summary["rule"] = "V12.13 keeps RAG/LLM task enhancement, but exposes task generation as a station instead of a page side effect."
    return summary
