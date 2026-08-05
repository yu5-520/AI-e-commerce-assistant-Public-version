"""V21.6.1 Agent-entry and station-interface synchronization.

The report/station queue ends after signal admission. Admitted representatives
are persisted as ``agent1_pending`` pipeline items and the unified pipeline-item
worker is the only automatic Agent entry. Public Agent station routes remain
available for manual replay and diagnostics.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict

from src.repositories.sqlite_repository import connect

AGENT_ENTRY_INTERFACE_VERSION = "21.6.1"
AGENT_ENTRY_OWNER = "agent_pipeline_item_worker"
PRE_AGENT_QUEUE_TERMINAL_STATION = "product_signal_admission_station"
PUBLIC_AGENT1_STATION = "product_judgment_agent_station"

_INSTALLED = False

_ADMISSION_OUTPUT_FIELDS = [
    "governanceVersion",
    "interfaceVersion",
    "fullSignalCount",
    "qualifiedSignalCount",
    "selectedRepresentativeCount",
    "candidateProductCount",
    "admittedSignalCount",
    "observedSignalCount",
    "agent1PendingItemCount",
    "observedItemCount",
    "admissionLimits",
    "agentBudget",
    "byAdmissionLevel",
    "byEvidenceMaturity",
    "byExperimentMode",
    "aggregationPolicy",
    "artificialMinimumApplied",
    "fixedEightItemCapApplied",
    "hardBusinessCapApplied",
    "signalsDiscarded",
    "admissionRef",
    "outputRef",
]

_COMPACT_ADMISSION_FIELDS = {
    "qualifiedSignalCount",
    "selectedRepresentativeCount",
    "agent1PendingItemCount",
    "observedItemCount",
    "admissionLimits",
    "agentBudget",
    "byAdmissionLevel",
    "byEvidenceMaturity",
    "byExperimentMode",
    "aggregationPolicy",
    "artificialMinimumApplied",
    "fixedEightItemCapApplied",
    "hardBusinessCapApplied",
    "signalsDiscarded",
    "interfaceVersion",
    "automaticEntryOwner",
    "automaticNextRuntime",
    "stationQueueContinuesToAgent1",
}

_COUNT_FIELDS = {
    "fullSignalCount",
    "qualifiedSignalCount",
    "selectedRepresentativeCount",
    "candidateProductCount",
    "admittedSignalCount",
    "observedSignalCount",
    "agent1PendingItemCount",
    "observedItemCount",
    "agentJudgmentCount",
    "formalJudgmentCount",
    "observeOnlyJudgmentCount",
    "pendingItemCount",
}

_BOOL_FIELDS = {
    "artificialMinimumApplied",
    "eightItemBusinessCapApplied",
    "fixedEightItemCapApplied",
    "hardBusinessCapApplied",
    "signalsDiscarded",
}

_DICT_FIELDS = {
    "admissionLimits",
    "agentBudget",
    "byAdmissionLevel",
    "byEvidenceMaturity",
    "byExperimentMode",
    "aggregationPolicy",
    "provider",
}


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _typed_default(key: str) -> Any:
    if key in _COUNT_FIELDS or key.endswith("Count"):
        return 0
    if key in _BOOL_FIELDS or key.endswith("Applied"):
        return False
    if key in _DICT_FIELDS:
        return {}
    return None


def _latest_admission_projection(data_version: str | None) -> Dict[str, Any]:
    if not data_version:
        return {}
    try:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT payload
                FROM pipeline_stage_gates
                WHERE data_version=?
                  AND stage='product_signal_admitted'
                  AND COALESCE(is_diagnostic,0)=0
                  AND COALESCE(run_type,'business')!='diagnostic'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (data_version,),
            ).fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        payload = {}
    output = payload.get("output") if isinstance(payload, dict) else {}
    output = output if isinstance(output, dict) else {}
    budget = output.get("agentBudget") if isinstance(output.get("agentBudget"), dict) else {}
    selected = _safe_int(
        output.get("selectedRepresentativeCount")
        or budget.get("selectedRepresentativeCount")
        or output.get("candidateProductCount")
        or output.get("admittedSignalCount")
    )
    return {
        "version": AGENT_ENTRY_INTERFACE_VERSION,
        "fullSignalCount": _safe_int(output.get("fullSignalCount")),
        "qualifiedSignalCount": _safe_int(output.get("qualifiedSignalCount")),
        "selectedRepresentativeCount": selected,
        "candidateProductCount": _safe_int(
            output.get("candidateProductCount") or selected
        ),
        "admittedSignalCount": _safe_int(output.get("admittedSignalCount") or selected),
        "observedSignalCount": _safe_int(output.get("observedSignalCount")),
        "agent1PendingItemCount": _safe_int(
            output.get("agent1PendingItemCount") or selected
        ),
        "agentBudget": budget,
        "byAdmissionLevel": output.get("byAdmissionLevel") or {},
        "byEvidenceMaturity": output.get("byEvidenceMaturity") or {},
        "byExperimentMode": output.get("byExperimentMode") or {},
        "aggregationPolicy": output.get("aggregationPolicy") or {},
        "fixedEightItemCapApplied": False,
        "hardBusinessCapApplied": bool(
            output.get("hardBusinessCapApplied")
            or budget.get("hardBusinessCap")
        ),
        "signalsDiscarded": False,
        "automaticEntryOwner": AGENT_ENTRY_OWNER,
        "automaticNextRuntime": "pipeline_items.agent1_pending",
    }


def install_v2161_agent_entry_interface() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from src.services import agent_pipeline_governance_v213_service as governance
    from src.services import agent_pipeline_item_worker_v2010_service as agent_worker
    from src.services import pipeline_agent1_microbatch_v20101_service as agent1
    from src.services import pipeline_live_read_model_v208_service as live_model
    from src.services import station_adapter_service as adapter
    from src.services import station_contract_service as contract
    from src.services import station_queue_service as station_queue
    from src.services import station_queue_worker_service as background_worker
    from src.services import station_registry_service as registry

    if getattr(station_queue, "_V2161_AGENT_ENTRY_INSTALLED", False):
        _INSTALLED = True
        return

    # ------------------------------------------------------------------
    # 1. The report/station queue ends at signal admission.
    # ------------------------------------------------------------------
    station_queue.TASK_GENERATION_SEQUENCE = [
        item
        for item in station_queue.TASK_GENERATION_SEQUENCE
        if item[0] != PUBLIC_AGENT1_STATION
    ]
    if PUBLIC_AGENT1_STATION not in station_queue.REMOVED_DOWNSTREAM_STATIONS:
        station_queue.REMOVED_DOWNSTREAM_STATIONS.append(PUBLIC_AGENT1_STATION)
    station_queue.STATION_INDEX = {
        station: index
        for index, (station, _stage) in enumerate(
            station_queue.TASK_GENERATION_SEQUENCE
        )
    }
    station_queue.STATION_PRIORITY = {
        station: index * 10 + 10
        for index, (station, _stage) in enumerate(
            station_queue.TASK_GENERATION_SEQUENCE
        )
    }
    station_queue.STATION_QUEUE_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    station_queue.PRE_AGENT_QUEUE_TERMINAL_STATION = (
        PRE_AGENT_QUEUE_TERMINAL_STATION
    )
    station_queue.AUTOMATIC_AGENT_ENTRY_OWNER = AGENT_ENTRY_OWNER

    original_ensure_queue_tables = station_queue.ensure_queue_tables

    def ensure_queue_tables_v2161() -> None:
        original_ensure_queue_tables()
        try:
            with connect() as conn:
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
                    (PRE_AGENT_QUEUE_TERMINAL_STATION, PUBLIC_AGENT1_STATION),
                )
                conn.commit()
        except Exception:
            pass

    original_compact_output = station_queue._compact_output

    def compact_output_v2161(output: Dict[str, Any]) -> Dict[str, Any]:
        compact = original_compact_output(output)
        for key in _COMPACT_ADMISSION_FIELDS:
            if key in output:
                compact[key] = output.get(key)
        compact["interfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        return compact

    original_queue_summary = station_queue.queue_summary

    def queue_summary_v2161(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        result = original_queue_summary(*args, **kwargs)
        result.update(
            version=AGENT_ENTRY_INTERFACE_VERSION,
            mode="v21_6_1_report_queue_ends_at_signal_admission",
            preAgentQueueTerminalStation=PRE_AGENT_QUEUE_TERMINAL_STATION,
            automaticAgentEntryOwner=AGENT_ENTRY_OWNER,
            automaticAgentEntryStage="agent1_pending",
            rule=(
                "Report/station queue stops after representative admission; "
                "the pipeline-item worker owns every automatic Agent stage."
            ),
        )
        return result

    station_queue.ensure_queue_tables = ensure_queue_tables_v2161
    station_queue._compact_output = compact_output_v2161
    station_queue.queue_summary = queue_summary_v2161

    # ------------------------------------------------------------------
    # 2. Public station contracts expose V21.6 typed admission outputs.
    # ------------------------------------------------------------------
    contract.STATION_CONTRACT_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    contract.STATION_REGISTRY_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    contract.STATION_ADAPTER_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    contract.DEFAULT_INPUTS[PUBLIC_AGENT1_STATION] = ["dataVersion"]
    contract.DEFAULT_OUTPUTS[PRE_AGENT_QUEUE_TERMINAL_STATION] = list(
        _ADMISSION_OUTPUT_FIELDS
    )
    contract.DEFAULT_OUTPUTS[PUBLIC_AGENT1_STATION] = [
        "agentJudgmentCount",
        "formalJudgmentCount",
        "observeOnlyJudgmentCount",
        "pendingItemCount",
        "provider",
        "automaticEntryOwner",
        "publicStationRunMode",
        "outputRef",
    ]

    original_complete_output = contract._complete_output_for_contract

    def complete_output_v2161(
        station_id: str,
        output: Dict[str, Any],
    ) -> Dict[str, Any]:
        seeded = dict(output or {})
        for key in contract.DEFAULT_OUTPUTS.get(station_id, []):
            if key in seeded and seeded.get(key) is not None:
                continue
            default = _typed_default(key)
            if default is not None:
                seeded[key] = copy.deepcopy(default)
        completed = original_complete_output(station_id, seeded)
        completed["interfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        return completed

    contract._complete_output_for_contract = complete_output_v2161

    # ------------------------------------------------------------------
    # 3. Normalize station-adapter responses without re-running admission.
    # ------------------------------------------------------------------
    adapter.STATION_ADAPTER_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    adapter.AGENT_PIPELINE_GOVERNANCE_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    original_run_station_adapter = adapter.run_station_adapter

    def run_station_adapter_v2161(
        station: Dict[str, Any],
        body: Dict[str, Any] | None = None,
        *,
        diagnostic: bool = False,
    ) -> Dict[str, Any]:
        result = original_run_station_adapter(
            station,
            body,
            diagnostic=diagnostic,
        )
        station_id = str(station.get("stationId") or "")
        result["interfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        if station_id == PRE_AGENT_QUEUE_TERMINAL_STATION:
            budget = (
                result.get("agentBudget")
                if isinstance(result.get("agentBudget"), dict)
                else {}
            )
            selected = _safe_int(
                result.get("selectedRepresentativeCount")
                or budget.get("selectedRepresentativeCount")
                or result.get("candidateProductCount")
                or result.get("admittedSignalCount")
            )
            result.setdefault("selectedRepresentativeCount", selected)
            result.setdefault("candidateProductCount", selected)
            result.setdefault("agent1PendingItemCount", selected)
            result.setdefault("admissionLimits", {})
            result.setdefault("byAdmissionLevel", {})
            result.setdefault("byEvidenceMaturity", {})
            result.setdefault("byExperimentMode", {})
            result.setdefault("aggregationPolicy", {})
            result["artificialMinimumApplied"] = False
            result["eightItemBusinessCapApplied"] = False
            result["fixedEightItemCapApplied"] = False
            result["hardBusinessCapApplied"] = bool(
                budget.get("hardBusinessCap")
            )
            result["signalsDiscarded"] = False
            result["automaticEntryOwner"] = AGENT_ENTRY_OWNER
            result["automaticNextRuntime"] = "pipeline_items.agent1_pending"
            result["stationQueueContinuesToAgent1"] = False
        elif station_id == PUBLIC_AGENT1_STATION:
            result["automaticEntryOwner"] = AGENT_ENTRY_OWNER
            result["publicStationRunMode"] = "manual_batch_or_replay"
            result["automaticStationQueueEntry"] = False
        return result

    adapter.run_station_adapter = run_station_adapter_v2161
    # station_contract imported the function by value, so refresh its binding.
    contract.run_station_adapter = run_station_adapter_v2161

    # ------------------------------------------------------------------
    # 4. The unified pipeline-item worker now consumes agent1_pending.
    # ------------------------------------------------------------------
    original_agent_tick = agent_worker.run_agent_pipeline_tick
    original_agent_status = agent_worker.agent_pipeline_status

    def run_agent_pipeline_tick_v2161(
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
            result["version"] = AGENT_ENTRY_INTERFACE_VERSION
            result["agentEntryInterfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
            result["automaticAgentEntryOwner"] = AGENT_ENTRY_OWNER
            return result

        pending = agent1.pending_agent1_item_count(resolved)
        if pending <= 0:
            result["version"] = AGENT_ENTRY_INTERFACE_VERSION
            result["agentEntryInterfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
            result["automaticAgentEntryOwner"] = AGENT_ENTRY_OWNER
            return result

        agent1_result = agent1.run_agent1_microbatch_v20101(
            data_version=resolved,
            user_id=user_id,
            batch_size=max(1, min(20, int(agent1_batch_size or 8))),
        )
        return {
            "version": AGENT_ENTRY_INTERFACE_VERSION,
            "contractVersion": result.get("contractVersion"),
            "agentEntryInterfaceVersion": AGENT_ENTRY_INTERFACE_VERSION,
            "automaticAgentEntryOwner": AGENT_ENTRY_OWNER,
            "ran": bool(
                int(agent1_result.get("claimedItemCount") or 0)
                or int(agent1_result.get("agentJudgmentCount") or 0)
            ),
            "workerId": worker_id,
            "selectedStage": "agent1_pending_to_agent1_completed",
            "dataVersion": resolved,
            "contractRecovery": result.get("contractRecovery") or {},
            "result": agent1_result,
        }

    def agent_pipeline_status_v2161(
        data_version: str | None = None,
    ) -> Dict[str, Any]:
        result = original_agent_status(data_version)
        resolved = result.get("dataVersion") or data_version
        pending = result.setdefault("pending", {})
        pending["agent1PendingForAgent1"] = (
            agent1.pending_agent1_item_count(resolved) if resolved else 0
        )
        result["version"] = AGENT_ENTRY_INTERFACE_VERSION
        result["agentEntryInterfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["automaticAgentEntryOwner"] = AGENT_ENTRY_OWNER
        result["preAgentQueueTerminalStation"] = PRE_AGENT_QUEUE_TERMINAL_STATION
        return result

    agent_worker.run_agent_pipeline_tick = run_agent_pipeline_tick_v2161
    agent_worker.agent_pipeline_status = agent_pipeline_status_v2161
    agent_worker.AGENT_PIPELINE_ITEM_WORKER_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    agent_worker.AUTOMATIC_AGENT_ENTRY_OWNER = AGENT_ENTRY_OWNER

    # ------------------------------------------------------------------
    # 5. Runtime governance, registry and worker status expose one owner.
    # ------------------------------------------------------------------
    governance.AGENT_PIPELINE_GOVERNANCE_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    original_governance_summary = governance.runtime_governance_summary

    def governance_summary_v2161() -> Dict[str, Any]:
        result = original_governance_summary()
        result.update(
            version=AGENT_ENTRY_INTERFACE_VERSION,
            agentEntryInterfaceVersion=AGENT_ENTRY_INTERFACE_VERSION,
            automaticAgentEntryOwner=AGENT_ENTRY_OWNER,
            preAgentQueueTerminalStation=PRE_AGENT_QUEUE_TERMINAL_STATION,
            agent1PendingRunnable=True,
            rule=(
                "Fair dataVersion scheduling selects pipeline_items from "
                "agent1_pending through task_admitted; station_queue owns no "
                "automatic Agent execution."
            ),
        )
        return result

    governance.runtime_governance_summary = governance_summary_v2161

    registry.STATION_REGISTRY_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    registry.PIPELINE_INTERFACE = "pipelineItemEnvelope_v21_6_1"
    registry.RUNTIME_UNIT = "dataVersion_fact_queue_to_pipelineItem_agent_runtime"
    for station in registry.STATIONS:
        station["version"] = AGENT_ENTRY_INTERFACE_VERSION
        station["pipelineInterface"] = registry.PIPELINE_INTERFACE
        if station.get("stationId") == PRE_AGENT_QUEUE_TERMINAL_STATION:
            station.update(
                acceptance=(
                    "observation maturity + representative budget seed "
                    "agent1_pending; deferred signals remain observed"
                ),
                runtimeUnit="pipelineItem_seed_agent1_pending_v21_6_1",
                automaticNextStation=None,
                automaticNextRuntime="pipeline_items.agent1_pending",
                automaticExecutionOwner=AGENT_ENTRY_OWNER,
            )
        elif station.get("stationId") == PUBLIC_AGENT1_STATION:
            station.update(
                acceptance=(
                    "manual/replay entry reads agent1_pending items carrying "
                    "observationMaturity and experimentPolicy"
                ),
                runtimeUnit="pipelineItem_microbatch_agent1_v21_6_1",
                automaticExecutionOwner=AGENT_ENTRY_OWNER,
                publicRunMode="manual_batch_or_replay",
            )
        elif station.get("stationId") == "action_plan_judgment_agent_station":
            station.update(
                acceptance=(
                    "real provider or exact replay + experiment permission "
                    "traffic/budget/duration/mainline validation"
                ),
                runtimeUnit="pipelineItem_microbatch_agent2_v21_6_1",
            )

    original_registry_summary = registry.registry_summary

    def registry_summary_v2161() -> Dict[str, Any]:
        result = original_registry_summary()
        result.update(
            version=AGENT_ENTRY_INTERFACE_VERSION,
            pipelineInterface=registry.PIPELINE_INTERFACE,
            runtimeUnit=registry.RUNTIME_UNIT,
            automaticAgentEntryOwner=AGENT_ENTRY_OWNER,
            preAgentQueueTerminalStation=PRE_AGENT_QUEUE_TERMINAL_STATION,
            mainlinePurity="v21_6_1_single_agent_entry_owner",
            rule=(
                "Public Agent stations remain replayable interfaces; automatic "
                "execution is owned only by the pipeline-item worker."
            ),
        )
        governance_meta = result.setdefault("runtimeGovernance", {})
        governance_meta.update(
            version=AGENT_ENTRY_INTERFACE_VERSION,
            signalAdmissionPolicy=(
                "observation_maturity_dynamic_representative_budget"
            ),
            automaticAgentEntryOwner=AGENT_ENTRY_OWNER,
        )
        return result

    registry.registry_summary = registry_summary_v2161

    background_worker.STATION_QUEUE_WORKER_VERSION = AGENT_ENTRY_INTERFACE_VERSION
    background_worker.AGENT_PIPELINE_GOVERNANCE_VERSION = (
        AGENT_ENTRY_INTERFACE_VERSION
    )

    # ------------------------------------------------------------------
    # 6. Pipeline live API exposes why N products entered Agent.
    # ------------------------------------------------------------------
    original_live_reader = live_model.read_pipeline_live_model

    def read_pipeline_live_model_v2161(
        data_version: str | None = None,
        *,
        limit: int = 80,
    ) -> Dict[str, Any]:
        result = original_live_reader(data_version=data_version, limit=limit)
        resolved = result.get("dataVersion") or data_version
        admission = _latest_admission_projection(resolved)
        result["version"] = AGENT_ENTRY_INTERFACE_VERSION
        result["agentEntryInterfaceVersion"] = AGENT_ENTRY_INTERFACE_VERSION
        result["automaticAgentEntryOwner"] = AGENT_ENTRY_OWNER
        result["preAgentQueueTerminalStation"] = PRE_AGENT_QUEUE_TERMINAL_STATION
        result["admission"] = admission
        if admission:
            summary = result.setdefault("summary", {})
            summary["qualifiedSignalCount"] = admission.get(
                "qualifiedSignalCount", 0
            )
            summary["selectedRepresentativeCount"] = admission.get(
                "selectedRepresentativeCount", 0
            )
            summary["agent1PendingItemCount"] = admission.get(
                "agent1PendingItemCount", 0
            )
            summary["deferredQualifiedCount"] = _safe_int(
                admission.get("agentBudget", {}).get("deferredQualifiedCount")
            )
            result["admissionHeadline"] = (
                f"信号{admission.get('fullSignalCount', 0)} · "
                f"可行动{admission.get('qualifiedSignalCount', 0)} · "
                f"进入Agent{admission.get('selectedRepresentativeCount', 0)} · "
                f"观察{admission.get('observedSignalCount', 0)}"
            )
        return result

    live_model.read_pipeline_live_model = read_pipeline_live_model_v2161
    live_model.PIPELINE_LIVE_READ_MODEL_VERSION = AGENT_ENTRY_INTERFACE_VERSION

    station_queue._V2161_AGENT_ENTRY_INSTALLED = True
    agent_worker._V2161_AGENT_ENTRY_INSTALLED = True
    contract._V2161_AGENT_ENTRY_INSTALLED = True
    adapter._V2161_AGENT_ENTRY_INSTALLED = True
    registry._V2161_AGENT_ENTRY_INSTALLED = True
    live_model._V2161_AGENT_ENTRY_INSTALLED = True
    _INSTALLED = True


__all__ = [
    "AGENT_ENTRY_INTERFACE_VERSION",
    "AGENT_ENTRY_OWNER",
    "PRE_AGENT_QUEUE_TERMINAL_STATION",
    "PUBLIC_AGENT1_STATION",
    "install_v2161_agent_entry_interface",
]
