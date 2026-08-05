from __future__ import annotations

import json
from pathlib import Path

import src  # noqa: F401 - installs the same runtime bindings used by FastAPI.
from src.services import agent_runtime_hard_interface_v22515_service as hard
from src.services import station_agent_worker_v22515_service as worker_binding
from src.services import station_agent_worker_v2259_service as active_worker
from src.services.registry_runtime_receipt_v23_service import (
    build_selected_module_contracts,
    load_runtime_projection,
)

ROOT = Path(__file__).resolve().parents[1]


def test_v23_1_4_active_agent1_binding_is_exact_and_read_only() -> None:
    binding = hard.active_agent1_runtime_binding()
    assert binding["matched"] is True
    assert binding["activeFacadeOwner"] == (
        "src.services.agent_runtime_hard_interface_v22515_service"
    )
    assert binding["agent1StageOwner"] == (
        "src.services.agent_runtime_hard_interface_v2257_service"
    )
    assert binding["inputContractOwner"] == (
        "src.services.agent_input_contract_v2258_service"
    )
    assert binding["inputTransportOwner"] == (
        "src.services.agent_input_transport_v2258_service"
    )
    assert binding["inputResolverOwner"] == (
        "src.services.agent_input_transport_v2258_service"
    )
    assert binding["tokenRuntimeOwner"] == (
        "src.services.agent_token_runtime_hash_exact_v2259_service"
    )
    assert binding["stationWorkerFacade"] == (
        "src.services.station_agent_worker_v2259_service"
    )
    assert binding["databaseMutated"] is False
    assert binding["providerCallsExecuted"] == 0
    assert binding["secondWorkerCreated"] is False


def test_v23_1_4_active_worker_keeps_one_thread_and_v22515_tick() -> None:
    assert worker_binding.legacy.run_agent_pipeline_tick_hard.__module__ == (
        "src.services.agent_runtime_hard_interface_v22515_service"
    )
    config = active_worker.worker_config()
    assert config["secondWorkerAllowed"] is False
    assert config["activeAgent1RuntimeBinding"]["matched"] is True
    assert config["agent2EvidenceSliceVersion"] == "22.5.14"
    assert config["agent2HashProofBridgeVersion"] == "22.5.15"


def test_v23_1_4_runtime_projection_requires_active_probe() -> None:
    config = load_runtime_projection(ROOT)
    assert "agent1_input_projection" in config["requiredModules"]
    assert config["rules"]["activeBindingProbeRequired"] is True
    probe = config["modules"]["agent1_runtime"]["activeBindingProbe"]
    assert probe["module"] == (
        "src.services.agent_runtime_hard_interface_v22515_service"
    )
    assert probe["symbol"] == "active_agent1_runtime_binding"
    assert probe["expectedOwners"]["inputTransportOwner"].endswith(
        "agent_input_transport_v2258_service"
    )
    assert probe["expectedOwners"]["tokenRuntimeOwner"].endswith(
        "agent_token_runtime_hash_exact_v2259_service"
    )


def test_v23_1_4_registry_contract_executes_and_hashes_active_probe() -> None:
    contracts = build_selected_module_contracts(ROOT)
    assert contracts["verified"] is True, contracts["errors"]
    agent1 = contracts["moduleContracts"]["agent1_runtime"]
    probe = agent1["activeBindingProbe"]
    assert probe["result"]["matched"] is True
    assert probe["result"]["databaseMutated"] is False
    assert probe["result"]["providerCallsExecuted"] == 0
    assert str(probe["activeBindingHash"]).startswith("sha256:")


def test_v23_1_4_projection_files_cover_real_worker_chain() -> None:
    raw = json.loads(
        (ROOT / "config/v23_registry_runtime.json").read_text(encoding="utf-8")
    )
    paths = set(raw["modules"]["agent1_runtime"]["implementationPaths"])
    assert {
        "src/services/agent_runtime_hard_interface_v2257_service.py",
        "src/services/agent_runtime_hard_interface_v22514_service.py",
        "src/services/agent_runtime_hard_interface_v22515_service.py",
        "src/services/station_agent_worker_v22515_service.py",
        "src/services/station_agent_worker_v2259_service.py",
        "src/services/agent_token_runtime_hash_exact_v2259_service.py",
    }.issubset(paths)
