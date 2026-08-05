from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def concrete_route_paths(app: object) -> set[str]:
    openapi = getattr(app, "openapi", None)
    if callable(openapi):
        schema = openapi()
        if isinstance(schema, dict) and isinstance(schema.get("paths"), dict):
            return {str(path) for path in schema["paths"]}
    return {
        str(path)
        for route in getattr(app, "routes", [])
        if (path := getattr(route, "path", None))
    }


def test_one_public_version_and_separate_preserved_contracts() -> None:
    from src.runtime_version import VERSION, runtime_versions

    assert VERSION == "22.4.0"
    versions = runtime_versions()
    assert versions["api"] == VERSION
    assert versions["product"] == VERSION
    assert versions["contract"] == VERSION
    assert versions["runtimeMode"] == "single_release_sealed_runtime"
    assert versions["releaseIdentity"] == "22.4.0"
    assert versions["releaseManifest"] == "release.manifest.v1"
    assert versions["artifactTransport"] == "22.3.0"
    assert versions["pipelineArtifactContract"] == "22.3.0"
    assert versions["agentInputContract"] == "22.3.0"
    assert versions["agentInputTransport"] == "22.3.0"
    assert versions["agentTokenRuntime"] == "22.3.0"
    assert versions["agentRuntimeHardInterface"] == "22.3.0"
    assert versions["stationAgentWorker"] == "22.4.0"
    assert versions["publicTaskDto"] == "22.2.3"
    assert versions["operatingEvidenceContract"] == "21.5.0"
    assert versions["stationTruthContract"] == "22.2.5"
    preserved = versions["preservedContracts"]
    assert preserved["llmInputProjection"] == "22.2.0"
    assert preserved["pipelinePayloadRetirement"] == "22.2.4"
    assert preserved["endToEndAgentFlow"] == "22.2.6"
    assert "legacyLlmInputProjection" not in versions
    assert "signalPool" not in versions
    assert "contextualAgentRuntime" not in versions


def test_package_startup_has_one_agent_authority() -> None:
    text = (ROOT / "src/__init__.py").read_text(encoding="utf-8")
    assert text.count("install_v22_runtime") == 2
    assert text.count("bind_pipeline_reference_runtime") == 2
    assert text.count("bind_station_truth_contract") == 2
    assert text.count("bind_hard_interface_bridge_v2301") == 2
    assert "\nbind_end_to_end_agent_flow()\n" not in text
    assert "bind_end_to_end_agent_flow_hardening" not in text
    assert "bind_agent_runtime_recovery" not in text
    assert "install_v219" not in text
    assert "install_v217" not in text
    assert "install_v216_runtime" not in text


def test_pre_agent_queue_ends_before_agent1_and_has_no_string_ref_fallback() -> None:
    text = (ROOT / "src/services/station_queue_service.py").read_text(encoding="utf-8")
    sequence = text[text.index("TASK_GENERATION_SEQUENCE"):text.index("REMOVED_DOWNSTREAM_STATIONS")]
    assert "product_signal_admission_station" in sequence
    assert "product_judgment_agent_station" not in sequence
    assert "record_business_station_output" in text
    assert "record_station_output_as_item_state" not in text
    assert 'f"{station_id}:{data_version' not in text
    assert "run.get(\"ok\") is not True" in text
    assert "duplicateCompletedGateAllowed" in text


def test_observe_terminal_transition_is_monotonic() -> None:
    from src.services.pipeline_item_service import STAGE_ORDER

    assert STAGE_ORDER["agent1_pending"] < STAGE_ORDER["agent1_running"]
    assert STAGE_ORDER["agent1_running"] < STAGE_ORDER["observed_soft_gate"]
    assert STAGE_ORDER["observed_soft_gate"] < STAGE_ORDER["agent1_completed"]


def test_missing_action_lock_fails_closed() -> None:
    from src.services.route_action_department_matrix_v1915_service import attach_matrix_dispatch

    with pytest.raises(ValueError, match="agent1_action_family_lock_missing"):
        attach_matrix_dispatch({"productId": "P1", "storeId": "S1"})


def test_observe_is_native_terminal_result() -> None:
    from src.services.agent_runtime_contract_v2010_service import (
        missing_agent1_contract,
        normalize_agent1_completed_contract,
    )

    observed = normalize_agent1_completed_contract(
        item={"item_id": "I1", "product_id": "P1", "store_id": "S1"},
        signal={"productId": "P1", "storeId": "S1", "productTitle": "测试商品"},
        judgment={
            "decisionType": "observe",
            "selectedOperatingRoute": "observe",
            "selectedActionFamily": None,
            "routeLock": {"locked": True, "selectedOperatingRoute": "observe"},
            "actionFamilyLock": {
                "locked": True,
                "selectedActionFamily": None,
                "forbiddenOverride": True,
                "observationOnly": True,
            },
        },
        provider={"providerStatus": "ok"},
        data_version="DV1",
    )
    assert missing_agent1_contract(observed) == []
    assert observed["actionFamily"] is None
    assert observed["selectedActionFamily"] is None
    assert observed["route"] == "observe"
    assert observed["taskAdmissionAllowed"] is False
    assert "matrixDispatch" not in observed


def test_act_uses_only_canonical_lock() -> None:
    from src.services.route_action_department_matrix_v1915_service import attach_matrix_dispatch

    package = {
        "productId": "P1",
        "storeId": "S1",
        "productTitle": "测试商品",
        "selectedActionFamilyHint": "title_image_test",
        "matrixDispatch": {"selectedActionFamily": "title_image_test"},
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "routeLock": {
                "locked": True,
                "selectedOperatingRoute": "paid_efficiency_route",
            },
            "actionFamilyLock": {
                "locked": True,
                "selectedActionFamily": "roas_guard",
                "forbiddenOverride": True,
            },
        },
    }
    result = attach_matrix_dispatch(package)
    assert result["actionFamily"] == "roas_guard"
    assert result["route"] == "paid_efficiency_route"
    assert "selectedActionFamilyHint" not in result
    assert result["matrixDispatch"]["fallbackAllowed"] is False


def test_agent2_has_no_fixed_step_minimums() -> None:
    text = (ROOT / "src/services/agent2_action_plan_core_v20_service.py").read_text(encoding="utf-8")
    for marker in (
        "operatorActionSteps_min_4",
        "executionSteps_min_3",
        "decisionBranches_min_2",
        "submissionEvidence_min_2",
    ):
        assert marker not in text
    assert "executable_action_required" in text


def test_sop_compiler_adds_no_business_steps() -> None:
    text = (ROOT / "src/services/sop_builder_core_v20_service.py").read_text(encoding="utf-8")
    assert '"compilerAddedStepCount": 0' in text
    assert "_coordination_steps(plan)" not in text


def test_station_input_contract_is_strict() -> None:
    from src.services.station_contract_service import run_station_contract

    result = run_station_contract(
        "action_plan_judgment_agent_station",
        {"dataVersion": "DV1"},
    )
    assert result["ok"] is False
    assert result["status"] == "contract_invalid"
    assert "pipelineItemEnvelope" in result["inputContract"]["missing"]


def test_bundle_station_output_contracts_are_business_dtos() -> None:
    from src.services.station_contract_service import station_contract

    full = station_contract("full_product_bundle_station")["output"]["required"]
    validation = station_contract("bundle_validation_station")["output"]["required"]
    assert "businessOutputType" in full
    assert "productSignalPackages" in full
    assert "fullProductBundleRef" not in full
    assert "validatedSignals" in validation
    assert "validatedBundleRef" not in validation


def test_legacy_system_routes_are_absent_and_release_route_exists() -> None:
    from src.api.main import app

    paths = concrete_route_paths(app)
    for path in (
        "/api/system/legacy-task-chain-status",
        "/api/system/clear-legacy-task-chain",
        "/api/system/reset-legacy-runtime-once",
        "/api/system/runtime-diagnostics",
        "/api/system/backfill-operating-objects",
        "/api/system/postgres-cutover-check",
    ):
        assert path not in paths
    assert "/api/system/release-identity" in paths
    assert "/api/ops/artifacts" in paths
    assert "/api/ops/artifacts/retirement-status" in paths
    assert "/api/view/tasks" in paths
    assert "/api/view/tasks/{task_id}" in paths
