"""V22 public station registry for the single governed runtime."""
from __future__ import annotations

from typing import Any, Dict, List

from src.runtime_version import VERSION

STATION_REGISTRY_VERSION = VERSION
PIPELINE_INTERFACE = "pipelineItemEnvelope.v22"
RUNTIME_UNIT = "businessDataVersion_then_pipelineItem"


def station(
    station_id: str,
    stage: str,
    title: str,
    backend: str,
    prefix: str,
    next_station: str | None,
    line: str,
    domain: str,
    *,
    replayable: bool = True,
    acceptance: str = "count",
    runtime_unit: str | None = None,
) -> Dict[str, Any]:
    return {
        "stationId": station_id,
        "stage": stage,
        "title": title,
        "backendModule": backend,
        "frontendModule": f"web_demo/stations/{station_id.replace('_', '-')}",
        "outputRefPrefix": prefix,
        "nextStation": next_station,
        "stationLine": line,
        "stationDomain": domain,
        "replayable": replayable,
        "diagnosticSupported": True,
        "acceptance": acceptance,
        "version": VERSION,
        "pipelineInterface": PIPELINE_INTERFACE,
        "runtimeUnit": runtime_unit or RUNTIME_UNIT,
    }


STATIONS: List[Dict[str, Any]] = [
    station("report_receive_station", "report_received", "数据中台接入", "src.services.station_alignment_v165_service", "raw_report", "report_schema_station", "real_report_fact_line", "report_receive", replayable=False, acceptance="one file = one businessDataVersion"),
    station("report_schema_station", "report_schema_mapped", "结构解析", "src.services.station_alignment_v165_service", "report_schema_mapping", "report_fact_station", "real_report_fact_line", "report_schema", acceptance="header/date mapping"),
    station("report_fact_station", "report_facts_ready", "事实引擎", "src.services.station_alignment_v165_service", "report_fact_namespace", "product_master_station", "real_report_fact_line", "report_fact", acceptance="product/store/traffic fact namespaces"),
    station("product_master_station", "product_master_ready", "商品主档", "src.services.station_alignment_v165_service", "product_master", "product_metric_snapshot_station", "real_report_fact_line", "product_master", acceptance="distinct platform+store+product+sku"),
    station("product_metric_snapshot_station", "product_metric_snapshot_ready", "指标快照", "src.services.station_alignment_v165_service", "product_metric_snapshot", "full_product_bundle_station", "snapshot_bundle_line", "product_metric_snapshot", acceptance="current facts plus dated observations"),
    station("full_product_bundle_station", "full_product_bundle_ready", "经营上下文", "src.services.station_alignment_v165_service", "full_product_bundle", "bundle_validation_station", "snapshot_bundle_line", "full_product_bundle", acceptance="one complete product context"),
    station("bundle_validation_station", "bundle_validation_ready", "质量门禁", "src.services.station_alignment_v165_service", "validated_full_product_bundle", "product_signal_admission_station", "snapshot_bundle_line", "bundle_validation", acceptance="fact-layer validation"),
    station("product_signal_admission_station", "product_signal_admitted", "信号引擎", "src.services.product_signal_admission_v197_service", "product_signal_admission", "product_judgment_agent_station", "agent_mainline", "product_signal_admission", acceptance="qualified signals seed agent1_pending; observations remain outside tasks", runtime_unit="businessDataVersion_to_pipelineItem"),
    station("product_judgment_agent_station", "agent1_completed", "Agent1上下文经营诊断", "src.services.pipeline_agent1_microbatch_v20101_service", "agent1_contextual_decision", "action_parameter_enrichment_station", "agent_mainline", "product_judgment_agent", acceptance="facts + diagnostic RAG -> causal diagnosis -> native act/observe -> one lock", runtime_unit="pipelineItem"),
    station("action_parameter_enrichment_station", "action_pack_ready", "动作能力编译", "src.services.action_pack_core_v20_service", "action_capability_pack", "action_plan_judgment_agent_station", "agent_mainline", "action_parameter_enrichment", acceptance="facts, objects, permissions and numeric limits only", runtime_unit="pipelineItem"),
    station("action_plan_judgment_agent_station", "agent2_completed", "Agent2自主执行方案", "src.services.agent2_action_plan_core_v20_service", "agent2_autonomous_action_plan", "task_mapping_agent_station", "agent_mainline", "action_plan_judgment_agent", acceptance="one locked family + capability/RAG -> smallest complete path + proof", runtime_unit="pipelineItem"),
    station("task_mapping_agent_station", "sop_mapped", "SOP确定性编译", "src.services.sop_builder_core_v20_service", "task_generation_decision", "task_pool_admission_station", "agent_mainline", "task_mapping_agent", acceptance="format Agent2 only; compilerAddedStepCount=0", runtime_unit="pipelineItem"),
    station("task_pool_admission_station", "task_admitted", "任务池", "src.services.task_pool_admission_core_v20_service", "task_pool", "frontend_read_model_station", "task_delivery_line", "task_pool_admission", acceptance="one SOP item = one idempotent admission attempt", runtime_unit="pipelineItem"),
    station("frontend_read_model_station", "frontend_read_model_ready", "读模型", "src.services.station_alignment_v165_service", "frontend_read_model", "task_pool_acceptance_station", "task_delivery_line", "frontend_read_model", acceptance="materialized task list/detail projections"),
    station("task_pool_acceptance_station", "task_pool_acceptance_ready", "任务闭环", "src.services.station_alignment_v165_service", "task_pool_acceptance", None, "task_delivery_line", "task_pool_acceptance", acceptance="data-line = task pool = frontend views"),
    station("task_acceptance_station", "task_accepted", "任务接收", "src.services.task_acceptance_assignment_station_service", "task_acceptance", "task_submission_station", "task_lifecycle_line", "task_acceptance", replayable=False, acceptance="lifecycle transition"),
    station("task_assignment_station", "task_assigned", "任务派发", "src.services.task_acceptance_assignment_station_service", "task_assignment", "task_acceptance_station", "task_lifecycle_line", "task_assignment", acceptance="assignee permission"),
    station("task_submission_station", "operator_evidence_submitted", "运营提交", "src.services.task_submission_review_station_service", "submission", "task_review_station", "task_lifecycle_line", "task_submission", replayable=False, acceptance="evidence submitted"),
    station("task_review_station", "manager_reviewed", "总管复核", "src.services.task_submission_review_station_service", "review", "recap_schedule_station", "task_lifecycle_line", "task_review", replayable=False, acceptance="manager decision"),
    station("recap_schedule_station", "recap_scheduled", "复盘排期", "src.services.task_recap_rag_station_service", "recap_schedule", "recap_complete_station", "task_lifecycle_line", "recap_schedule", acceptance="recap scheduled"),
    station("recap_complete_station", "system_auto_recap_completed", "系统复盘", "src.services.task_recap_rag_station_service", "recap", "rag_feedback_station", "task_lifecycle_line", "recap_complete", acceptance="before/after metrics collected"),
    station("rag_feedback_station", "rag_candidate_ready", "经验候选", "src.services.task_recap_rag_station_service", "rag_candidate", None, "task_lifecycle_line", "rag_feedback", acceptance="manager approval before retrieval"),
]


def list_stations() -> List[Dict[str, Any]]:
    return [{**item, "interface": f"/api/stations/{item['stationId']}"} for item in STATIONS]


def get_station(station_id: str) -> Dict[str, Any] | None:
    for item in STATIONS:
        if item["stationId"] == station_id or item["stage"] == station_id:
            return {**item, "interface": f"/api/stations/{item['stationId']}"}
    return None


def station_by_stage(stage: str) -> Dict[str, Any] | None:
    return get_station(stage)


def station_order() -> List[str]:
    return [item["stationId"] for item in STATIONS]


def registry_summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "pipelineItemVersion": VERSION,
        "runtimeUnit": RUNTIME_UNIT,
        "runtimeMode": "single_v22_runtime",
        "stations": list_stations(),
        "ragMode": {
            "agent1DiagnosticRetrieval": "before_action_family_lock",
            "agent2ExecutionRetrieval": "after_action_family_lock",
            "taskGate": False,
            "historicalSopCopyAllowed": False,
        },
        "lines": {
            "realReportFactLine": [item["stationId"] for item in STATIONS if item.get("stationLine") == "real_report_fact_line"],
            "snapshotBundleLine": [item["stationId"] for item in STATIONS if item.get("stationLine") == "snapshot_bundle_line"],
            "agentMainline": [item["stationId"] for item in STATIONS if item.get("stationLine") == "agent_mainline"],
            "taskDeliveryLine": [item["stationId"] for item in STATIONS if item.get("stationLine") == "task_delivery_line"],
            "taskLifecycleLine": [item["stationId"] for item in STATIONS if item.get("stationLine") == "task_lifecycle_line"],
        },
        "mainlinePurity": "v22_single_runtime",
        "rule": "Facts and permissions are deterministic; Agent1 diagnoses; Agent2 plans; SOP only formats.",
    }


__all__ = [
    "STATION_REGISTRY_VERSION",
    "list_stations",
    "get_station",
    "station_by_stage",
    "station_order",
    "registry_summary",
]
