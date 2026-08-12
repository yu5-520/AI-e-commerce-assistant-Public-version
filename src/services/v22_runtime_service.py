"""V22 single runtime installer.

V22 has one startup authority. Deterministic report, observation and frozen-task
facts are activated once, then the canonical Agent, capability, SOP and task
workers are bound directly. No V21 interface overlay, alternate Agent entry,
default action family or compatibility task runtime is installed.
"""
from __future__ import annotations

from typing import Any, Dict

from src.runtime_version import VERSION
from src.services import competition_evidence_v215_runtime_service as competition_evidence_v215

_INSTALLED = False


def _set_version(module: Any, *names: str) -> None:
    for name in names:
        if hasattr(module, name):
            setattr(module, name, VERSION)


def install_v22_runtime() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from src.services import v215_report_batch_evidence_service as report_evidence
    from src.services import v216_runtime_install_service as observation_install
    from src.services import v216_observation_experiment_service as observation
    from src.services import v2178_task_metric_evidence_projection_install_service as task_evidence_install
    from src.services import task_metric_evidence_projection_v2178_service as task_evidence

    report_evidence.install_v215_runtime()
    competition_evidence_v215.install_competition_evidence_v215_runtime()
    observation_install.install_v216_runtime()
    task_evidence_install.install_v2178_task_metric_evidence_projection()

    observation.ACTION_FAMILY_BY_HYPOTHESIS = {}
    original_policy = observation.experiment_policy

    def capability_policy(_hypothesis: str, maturity: str) -> Dict[str, Any]:
        base = dict(original_policy("no_preselected_family", maturity) or {})
        for key in (
            "actionFamily",
            "requiredActionFamily",
            "selectedActionFamily",
            "selectedActionFamilyHint",
            "operatingRoute",
        ):
            base.pop(key, None)
        base.update(
            version=VERSION,
            actionFamily=None,
            strategyOwner="Agent1/Agent2",
            compilerRole="permissions_and_numeric_limits_only",
            rule=(
                "Evidence maturity controls execution strength and permission "
                "bounds; it never selects an action family."
            ),
        )
        return base

    observation.experiment_policy = capability_policy

    from src.repositories.sqlite_repository import connect
    from src.services import action_pack_core_v20_service as capability
    from src.services import agent2_action_plan_core_v20_service as agent2
    from src.services import agent_pipeline_governance_v213_service as governance
    from src.services import agent_pipeline_item_worker_v2010_service as pipeline
    from src.services import agent_runtime_contract_v2010_service as contract_core
    from src.services import agent_runtime_contract_v2141_service as contract
    from src.services import import_row_store_service as import_rows
    from src.services import module_projection_service as projection
    from src.services import pipeline_action_microbatch_v205_service as agent2_worker
    from src.services import pipeline_agent1_microbatch_v20101_service as agent1_worker
    from src.services import pipeline_live_read_model_v208_service as live_model
    from src.services import pipeline_sop_task_pool_v2010_service as sop_worker
    from src.services import product_signal_admission_v197_service as admission
    from src.services import product_signal_snapshot_service as signal_snapshot
    from src.services import real_product_judgment_agent_v196_service as agent1
    from src.services import report_alert_service as report_alert
    from src.services import report_schema_service as report_schema
    from src.services import sop_builder_core_v20_service as sop
    from src.services import station_adapter_service as station_adapter
    from src.services import station_contract_service as station_contract
    from src.services import station_queue_service as station_queue
    from src.services import system_product_snapshot_service as product_snapshot
    from src.services import task_detail_snapshot_v2024_service as task_detail
    from src.services import task_pool_admission_core_v20_service as task_pool

    public_agent1_station = "product_judgment_agent_station"
    terminal_station = "product_signal_admission_station"
    station_queue.TASK_GENERATION_SEQUENCE = [
        item
        for item in station_queue.TASK_GENERATION_SEQUENCE
        if item[0] != public_agent1_station
    ]
    if public_agent1_station not in station_queue.REMOVED_DOWNSTREAM_STATIONS:
        station_queue.REMOVED_DOWNSTREAM_STATIONS.append(public_agent1_station)
    station_queue.STATION_INDEX = {
        station: index
        for index, (station, _stage) in enumerate(station_queue.TASK_GENERATION_SEQUENCE)
    }
    station_queue.STATION_PRIORITY = {
        station: index * 10 + 10
        for index, (station, _stage) in enumerate(station_queue.TASK_GENERATION_SEQUENCE)
    }
    station_queue.PRE_AGENT_QUEUE_TERMINAL_STATION = terminal_station
    station_queue.AUTOMATIC_AGENT_ENTRY_OWNER = "agent_pipeline_item_worker"

    original_ensure_queue_tables = station_queue.ensure_queue_tables

    def ensure_queue_tables_v22() -> None:
        original_ensure_queue_tables()
        try:
            with connect() as conn:
                conn.execute(
                    """
                    UPDATE station_queue
                    SET status='disabled',
                        error_message='V22 Agent execution is owned by pipeline_items',
                        updated_at=datetime('now')
                    WHERE station_id=?
                      AND status IN ('queued','running','retry')
                    """,
                    (public_agent1_station,),
                )
                conn.execute(
                    """
                    UPDATE pipeline_jobs
                    SET status='completed',
                        current_station=?,
                        error_message=NULL,
                        updated_at=datetime('now')
                    WHERE current_station=?
                      AND status IN ('queued','running','retry')
                    """,
                    (terminal_station, public_agent1_station),
                )
                conn.commit()
        except Exception:
            pass

    station_queue.ensure_queue_tables = ensure_queue_tables_v22

    agent1_worker._real_agent_judgments = agent1._real_agent_judgments
    agent1_worker.build_operating_policy_context = agent1.build_agent1_rag_context
    agent1_worker.normalize_agent1_completed_contract = (
        contract_core.normalize_agent1_completed_contract
    )
    agent1_worker.missing_agent1_contract = contract_core.missing_agent1_contract

    agent2_worker.call_agent2_action_plans = agent2.call_agent2_action_plans
    agent2_worker.attach_agent2_action_plans = agent2.attach_agent2_action_plans
    agent2_worker.missing_agent2_contract = contract.missing_agent2_contract
    agent2_worker.normalize_agent2_completed_contract = (
        contract.normalize_agent2_completed_contract
    )

    pipeline.enrich_package_with_action_parameters = (
        capability.enrich_package_with_action_parameters
    )
    pipeline.select_action_parameter_pack = capability.select_action_parameter_pack
    pipeline.normalize_action_pack_ready_contract = (
        contract_core.normalize_action_pack_ready_contract
    )
    pipeline.missing_agent1_contract = contract_core.missing_agent1_contract
    pipeline.missing_action_pack_contract = contract_core.missing_action_pack_contract

    sop_worker.build_sop_decision_from_package = sop.build_sop_decision_from_package
    sop_worker.missing_agent2_contract = contract.missing_agent2_contract
    sop_worker.missing_sop_contract = contract.missing_sop_contract
    sop_worker.normalize_sop_mapped_contract = contract.normalize_sop_mapped_contract
    sop_worker.normalize_task_admitted_contract = contract.normalize_task_admitted_contract
    sop_worker.admit_decision_to_task_pool = task_pool.admit_decision_to_task_pool
    sop_worker.refresh_task_pool_views = task_pool.refresh_task_pool_views

    original_agent_tick = pipeline.run_agent_pipeline_tick
    original_agent_status = pipeline.agent_pipeline_status

    def run_agent_pipeline_tick_v22(
        data_version: str | None = None,
        *,
        user_id: str | None = None,
        worker_id: str | None = None,
        agent1_batch_size: int = 8,
        action_pack_batch_size: int = 8,
        agent2_batch_size: int = 5,
        sop_batch_size: int = 8,
        pool_batch_size: int = 8,
        force_new_snapshot: bool = False,
    ) -> Dict[str, Any]:
        result = original_agent_tick(
            data_version=data_version,
            user_id=user_id,
            worker_id=worker_id,
            action_pack_batch_size=action_pack_batch_size,
            agent2_batch_size=agent2_batch_size,
            sop_batch_size=sop_batch_size,
            pool_batch_size=pool_batch_size,
            force_new_snapshot=force_new_snapshot,
        )
        resolved = result.get("dataVersion") or data_version
        if result.get("ran") or not resolved:
            result.update(
                version=VERSION,
                contractVersion=VERSION,
                automaticAgentEntryOwner="agent_pipeline_item_worker",
            )
            return result
        pending = agent1_worker.pending_agent1_item_count(resolved)
        if pending <= 0:
            result.update(
                version=VERSION,
                contractVersion=VERSION,
                automaticAgentEntryOwner="agent_pipeline_item_worker",
            )
            return result
        agent1_result = agent1_worker.run_agent1_microbatch_v20101(
            data_version=resolved,
            user_id=user_id,
            batch_size=max(1, min(20, int(agent1_batch_size or 8))),
        )
        return {
            "version": VERSION,
            "contractVersion": VERSION,
            "automaticAgentEntryOwner": "agent_pipeline_item_worker",
            "ran": bool(
                int(agent1_result.get("claimedItemCount") or 0)
                or int(agent1_result.get("agentJudgmentCount") or 0)
            ),
            "workerId": worker_id,
            "selectedStage": "agent1_pending_to_agent1_completed_or_observed",
            "dataVersion": resolved,
            "contractRecovery": result.get("contractRecovery") or {},
            "result": agent1_result,
        }

    def agent_pipeline_status_v22(
        data_version: str | None = None,
    ) -> Dict[str, Any]:
        result = original_agent_status(data_version)
        resolved = result.get("dataVersion") or data_version
        pending = result.setdefault("pending", {})
        pending["agent1PendingForDiagnosis"] = (
            agent1_worker.pending_agent1_item_count(resolved) if resolved else 0
        )
        result.update(
            version=VERSION,
            contractVersion=VERSION,
            automaticAgentEntryOwner="agent_pipeline_item_worker",
            preAgentQueueTerminalStation=terminal_station,
            runtimeMode="single_v22_runtime",
        )
        return result

    pipeline.run_agent_pipeline_tick = run_agent_pipeline_tick_v22
    pipeline.agent_pipeline_status = agent_pipeline_status_v22

    version_fields = {
        observation_install: ("V216_VERSION",),
        observation: ("OBSERVATION_EXPERIMENT_VERSION", "V216_VERSION"),
        task_evidence_install: ("TASK_METRIC_EVIDENCE_PROJECTION_VERSION",),
        task_evidence: ("TASK_METRIC_EVIDENCE_PROJECTION_VERSION",),
        report_alert: ("REPORT_ALERT_SERVICE_VERSION",),
        report_schema: ("SCHEMA_VERSION",),
        import_rows: ("IMPORT_ROW_STORE_VERSION",),
        projection: ("PROJECTION_VERSION",),
        product_snapshot: ("SYSTEM_PRODUCT_SNAPSHOT_VERSION",),
        signal_snapshot: ("PRODUCT_SIGNAL_SNAPSHOT_VERSION",),
        admission: ("PRODUCT_SIGNAL_ADMISSION_VERSION",),
        live_model: ("PIPELINE_LIVE_READ_MODEL_VERSION",),
        agent1: ("REAL_PRODUCT_AGENT_V196_VERSION",),
        agent1_worker: (
            "PIPELINE_AGENT1_MICROBATCH_VERSION",
            "OPERATING_POLICY_CONTEXT_VERSION",
        ),
        capability: (
            "ACTION_PACK_CORE_VERSION",
            "ACTION_PARAMETER_ENRICHMENT_VERSION",
            "AGENT_RAG_CONTEXT_VERSION",
        ),
        agent2: (
            "AGENT2_ACTION_PLAN_CORE_VERSION",
            "AGENT2_PROVENANCE_VERSION",
            "AGENT_RAG_CONTEXT_VERSION",
        ),
        agent2_worker: ("PIPELINE_ACTION_MICROBATCH_VERSION",),
        contract_core: (
            "AGENT_RUNTIME_CONTRACT_VERSION",
            "AGENT1_JUDGMENT_CONTRACT_VERSION",
            "MATRIX_DISPATCH_CONTRACT_VERSION",
            "ACTION_PACK_CONTRACT_VERSION",
            "AGENT2_PLAN_CONTRACT_VERSION",
            "SOP_DECISION_CONTRACT_VERSION",
        ),
        contract: (
            "AGENT_RUNTIME_CONTRACT_VERSION",
            "AGENT1_JUDGMENT_CONTRACT_VERSION",
            "MATRIX_DISPATCH_CONTRACT_VERSION",
            "ACTION_PACK_CONTRACT_VERSION",
            "AGENT2_PLAN_CONTRACT_VERSION",
            "SOP_DECISION_CONTRACT_VERSION",
        ),
        sop: (
            "SOP_BUILDER_CORE_VERSION",
            "ACTION_PLAN_IR_VERSION",
            "AGENT2_PROVENANCE_VERSION",
            "AGENT_RAG_CONTEXT_VERSION",
        ),
        sop_worker: (
            "PIPELINE_SOP_TASK_POOL_VERSION",
            "SOP_MAPPING_RUNTIME_FIX_VERSION",
        ),
        task_pool: (
            "TASK_POOL_ADMISSION_CORE_VERSION",
            "ACTION_AUTHORITY_VERSION",
            "AGENT_RUNTIME_CONTRACT_VERSION",
        ),
        task_detail: ("TASK_DETAIL_SNAPSHOT_VERSION",),
        station_adapter: ("STATION_ADAPTER_VERSION",),
        station_contract: (
            "STATION_CONTRACT_VERSION",
            "STATION_ADAPTER_VERSION",
            "STATION_REGISTRY_VERSION",
            "PIPELINE_ITEM_VERSION",
        ),
        station_queue: ("STATION_QUEUE_VERSION",),
        governance: ("AGENT_PIPELINE_GOVERNANCE_VERSION",),
        pipeline: ("AGENT_PIPELINE_ITEM_WORKER_VERSION",),
    }
    for module, names in version_fields.items():
        _set_version(module, *names)
        module.V22_RUNTIME_VERSION = VERSION
        module.V22_SINGLE_RUNTIME = True

    from src.services import station_queue_worker_service as station_worker

    _set_version(
        station_worker,
        "STATION_QUEUE_WORKER_VERSION",
        "AGENT_PIPELINE_GOVERNANCE_VERSION",
        "STATION_QUEUE_VERSION",
    )
    station_worker.V22_RUNTIME_VERSION = VERSION
    station_worker.V22_SINGLE_RUNTIME = True

    _INSTALLED = True


__all__ = ["install_v22_runtime"]