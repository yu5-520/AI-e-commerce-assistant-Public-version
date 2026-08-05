from __future__ import annotations

import src  # noqa: F401

from src.runtime_version import runtime_versions
from src.services import agent_pipeline_governance_v213_service as governance
from src.services import agent_pipeline_item_worker_v2010_service as agent_worker
from src.services import pipeline_agent1_microbatch_v20101_service as agent1
from src.services import product_signal_admission_v197_service as admission
from src.services import station_adapter_service as adapter
from src.services import station_contract_service as contract
from src.services import station_queue_service as station_queue
from src.services import station_queue_worker_service as background_worker
from src.services import station_registry_service as registry
from src.services.v215_report_batch_evidence_service import V215_VERSION
from src.services.v216_runtime_install_service import V216_VERSION


def test_v213_component_versions_survive_all_runtime_overlays() -> None:
    config = background_worker.worker_config()
    summary = governance.runtime_governance_summary()

    assert governance.AGENT_PIPELINE_GOVERNANCE_VERSION == "21.3"
    assert adapter.STATION_ADAPTER_VERSION == "21.3"
    assert adapter.AGENT_PIPELINE_GOVERNANCE_VERSION == "21.3"
    assert background_worker.STATION_QUEUE_WORKER_VERSION == "21.3"
    assert background_worker.AGENT_PIPELINE_GOVERNANCE_VERSION == "21.3"

    assert governance.AGENT_ENTRY_INTERFACE_VERSION == "21.6.1"
    assert adapter.AGENT_ENTRY_INTERFACE_VERSION == "21.6.1"
    assert background_worker.AGENT_ENTRY_INTERFACE_VERSION == "21.6.1"
    assert governance.COMPONENT_VERSION_LAYERING_VERSION == "21.7.7"

    assert config["version"] == "21.3"
    assert config["governanceVersion"] == "21.3"
    assert config["agentEntryInterfaceVersion"] == "21.6.1"
    assert summary["version"] == "21.6.1"
    assert summary["governanceCoreVersion"] == "21.3"
    assert summary["componentVersionLayeringVersion"] == "21.7.7"


def test_signal_admission_versions_have_distinct_owners() -> None:
    # Source component, evidence layer and effective runtime are intentionally
    # different versions. V21.6 owns the active admission implementation.
    assert V215_VERSION == "21.5.0"
    assert V216_VERSION == "21.6.0"
    assert admission.PRODUCT_SIGNAL_ADMISSION_VERSION == "21.6.0"
    assert admission.product_signal_admission_station_v197.__name__ == (
        "admission_station_v216"
    )
    assert admission.MIN_ADMITTED == 0


def test_report_queue_stops_after_signal_admission() -> None:
    station_ids = [station_id for station_id, _stage in station_queue.TASK_GENERATION_SEQUENCE]

    assert station_ids[-1] == "product_signal_admission_station"
    assert "product_judgment_agent_station" not in station_ids
    assert "product_judgment_agent_station" in station_queue.REMOVED_DOWNSTREAM_STATIONS
    assert station_queue.AUTOMATIC_AGENT_ENTRY_OWNER == "agent_pipeline_item_worker"


def test_agent1_public_contract_is_manual_batch_interface() -> None:
    public_contract = contract.station_contract("product_judgment_agent_station")

    assert public_contract["version"] == "21.6.1"
    assert public_contract["adapterVersion"] == "21.3"
    assert public_contract["input"]["required"] == ["dataVersion"]
    assert public_contract["stationId"] == "product_judgment_agent_station"
    assert public_contract["runtimeUnit"] == "pipelineItem_microbatch_agent1_v21_6_1"


def test_admission_contract_uses_typed_defaults_not_output_ref_truthiness() -> None:
    output = contract._complete_output_for_contract(
        "product_signal_admission_station",
        {"outputRef": "product_signal_admission:DV-TEST"},
    )

    assert output["qualifiedSignalCount"] == 0
    assert output["selectedRepresentativeCount"] == 0
    assert output["agent1PendingItemCount"] == 0
    assert output["agentBudget"] == {}
    assert output["byEvidenceMaturity"] == {}
    assert output["artificialMinimumApplied"] is False
    assert output["fixedEightItemCapApplied"] is False
    assert output["hardBusinessCapApplied"] is False
    assert output["signalsDiscarded"] is False


def test_diagnostic_admission_adapter_exposes_agent_entry_owner() -> None:
    station = registry.get_station("product_signal_admission_station")
    assert station is not None

    result = adapter.run_station_adapter(
        station,
        {"dataVersion": "DIAG-V2161"},
        diagnostic=True,
    )

    assert result["interfaceVersion"] == "21.6.1"
    assert result["automaticEntryOwner"] == "agent_pipeline_item_worker"
    assert result["automaticNextRuntime"] == "pipeline_items.agent1_pending"
    assert result["stationQueueContinuesToAgent1"] is False
    assert result["selectedRepresentativeCount"] == 0


def test_unified_worker_consumes_agent1_pending(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_worker,
        "recover_version_only_action_pack_invalid",
        lambda _data_version: {"ran": False},
    )
    monkeypatch.setattr(agent_worker, "pending_task_pool_item_count", lambda _dv: 0)
    monkeypatch.setattr(agent_worker, "pending_sop_item_count", lambda _dv: 0)
    monkeypatch.setattr(agent_worker, "pending_agent2_item_count", lambda _dv: 0)
    monkeypatch.setattr(
        agent_worker,
        "seed_action_pack_from_agent1_items",
        lambda *_args, **_kwargs: {
            "ran": False,
            "claimedItemCount": 0,
            "createdItemCount": 0,
        },
    )
    monkeypatch.setattr(agent1, "pending_agent1_item_count", lambda _dv: 1)
    monkeypatch.setattr(
        agent1,
        "run_agent1_microbatch_v20101",
        lambda **_kwargs: {
            "claimedItemCount": 1,
            "agentJudgmentCount": 1,
            "pendingItemCount": 0,
        },
    )

    result = agent_worker.run_agent_pipeline_tick(
        "DV-V2161-ENTRY",
        worker_id="test-worker",
    )

    assert result["ran"] is True
    assert result["selectedStage"] == "agent1_pending_to_agent1_completed"
    assert result["agentEntryInterfaceVersion"] == "21.6.1"
    assert result["automaticAgentEntryOwner"] == "agent_pipeline_item_worker"
    assert result["agent2FinalContractRebuildVersion"] == "21.7.5"
    assert result["result"]["claimedItemCount"] == 1


def test_unified_worker_and_runtime_versions_publish_layered_contracts() -> None:
    versions = runtime_versions()
    config = background_worker.worker_config()

    assert versions["api"] == "21.6.2"
    assert versions["agent1ObservationContract"] == "21.6.2"
    assert versions["agentEntryInterface"] == "21.6.1"
    assert versions["stationInterface"] == "21.6.1"
    assert versions["pipelineLiveInterface"] == "21.6.1"
    assert versions["runtimeGovernance"] == "21.6.1"
    assert versions["agent2FinalContractRebuild"] == "21.7.5"
    assert agent_worker.AGENT_PIPELINE_ITEM_WORKER_VERSION == "21.6.1"
    assert agent_worker.AUTOMATIC_AGENT_ENTRY_OWNER == "agent_pipeline_item_worker"
    assert agent_worker.AGENT2_FINAL_CONTRACT_REBUILD_VERSION == "21.7.5"
    assert getattr(agent_worker, "_V2175_AGENT2_FINAL_CONTRACT_REBUILD_INSTALLED", False)
    assert config["automaticAgentEntryOwner"] == "agent_pipeline_item_worker"
    assert config["preAgentQueueTerminalStation"] == "product_signal_admission_station"


def test_registry_declares_one_automatic_agent_owner() -> None:
    summary = registry.registry_summary()
    admission_station = registry.get_station("product_signal_admission_station")
    agent1_station = registry.get_station("product_judgment_agent_station")

    assert summary["automaticAgentEntryOwner"] == "agent_pipeline_item_worker"
    assert summary["preAgentQueueTerminalStation"] == "product_signal_admission_station"
    assert summary["mainlinePurity"] == "v21_6_1_single_agent_entry_owner"
    assert admission_station is not None
    assert admission_station["automaticNextRuntime"] == "pipeline_items.agent1_pending"
    assert agent1_station is not None
    assert agent1_station["publicRunMode"] == "manual_batch_or_replay"
