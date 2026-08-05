from __future__ import annotations

from pathlib import Path

import pytest


def _isolate_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.repositories import sqlite_repository
    from src.services import artifact_storage_service

    monkeypatch.setattr(sqlite_repository, "DB_PATH", tmp_path / "runtime.sqlite3")
    monkeypatch.setattr(sqlite_repository, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sqlite_repository, "_WAL_INITIALIZED", False)
    monkeypatch.setattr(artifact_storage_service, "ARTIFACT_ROOT", tmp_path / "artifacts")


def test_runtime_recovery_exposes_explicit_helpers_only() -> None:
    from src.services import agent_runtime_recovery_v2261_service as recovery

    status = recovery.runtime_recovery_helper_status()
    assert status["mode"] == "explicit_helpers_only"
    assert status["runtimeBindingInstalled"] is False
    assert status["monkeyPatchAvailable"] is False
    assert status["activeRuntimeOwner"] == "agent_runtime_hard_interface_v230"
    assert status["fallbackAllowed"] is False
    assert not hasattr(recovery, "bind_agent_runtime_recovery")


def test_package_bootstrap_does_not_bind_the_legacy_agent_runtime() -> None:
    package_bootstrap = Path("src/__init__.py").read_text(encoding="utf-8")
    recovery_source = Path(
        "src/services/agent_runtime_recovery_v2261_service.py"
    ).read_text(encoding="utf-8")

    assert "\nbind_end_to_end_agent_flow()\n" not in package_bootstrap
    assert "bind_end_to_end_agent_flow_hardening" not in package_bootstrap
    assert "bind_hard_interface_bridge_v2301()" in package_bootstrap
    assert not Path(
        "src/services/end_to_end_agent_flow_v226_hardening_service.py"
    ).exists()
    assert "bind_agent_runtime_recovery" not in package_bootstrap
    assert "bind_agent_runtime_recovery" not in recovery_source
    assert "sys.modules" not in recovery_source
    assert "_ORIGINAL_AGENT1_MICROBATCH" not in recovery_source
    assert "_ORIGINAL_PIPELINE_TICK" not in recovery_source


def test_bridge_declares_non_agent_scope_and_immediate_input_handoffs() -> None:
    from src.services.hard_interface_bridge_v2301_service import (
        hard_interface_bridge_status,
    )

    status = hard_interface_bridge_status()
    assert status["bound"] is True
    assert status["agentRuntimeReplaced"] is False
    assert status["agentExecutionOwner"] == "agent_runtime_hard_interface_v230"
    assert status["agent1InputProducerHandoff"].startswith("signalRef_to_agent1InputRef")
    assert status["agent1InputTransportOwner"] == (
        "src.services.agent_input_transport_v2258_service"
    )
    assert status["agent2InputProducerHandoff"].startswith("capabilityRef_to_agent2InputRef")
    assert status["fallbackAllowed"] is False


def test_operating_policy_projection_keeps_family_permissions_and_rag_boundaries() -> None:
    from src.services.operating_policy_context_v2028_service import (
        build_operating_policy_context,
    )

    policy = build_operating_policy_context()
    guardrails = policy["guardrails"]
    assert guardrails["familyGuidance"] == policy["familyGuidance"]
    assert guardrails["permissionBoundary"] == policy["permissionBoundary"]
    assert guardrails["ragBoundary"] == policy["ragBoundary"]
    assert policy["projectionContract"]["experienceMayOverridePolicy"] is False


def test_signal_admission_compiles_agent1_input_before_return(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect, loads
    from src.services import artifact_signal_admission_v225_service as admission
    from src.services.agent_input_contract_v2258_service import AGENT1_INPUT_SCHEMA
    from src.services.agent_input_transport_v2258_service import resolve_agent_input_ref
    from src.services.artifact_transport_service import store_artifact

    signal = {
        "signalId": "SIG-P1",
        "packageId": "PKG-P1",
        "dataVersion": "DV-NEW",
        "productId": "P1",
        "storeId": "S1",
        "productIdentity": {
            "productId": "P1",
            "storeId": "S1",
            "productTitle": "测试商品",
            "platform": "抖音",
        },
        "primarySignalType": "product_margin_changed",
        "previousProductMetricSnapshot": {"grossMargin": 40},
        "snapshotLayer": {
            "fieldSignals": [
                {
                    "metricCode": "grossMargin",
                    "previous": 40,
                    "current": 32,
                    "changeRatio": -0.2,
                    "meaningfulChange": True,
                    "signalStrength": "high",
                }
            ]
        },
        "crossValidation": {
            "version": "21.5.0",
            "sourceDataVersions": ["DV-OLD", "DV-NEW"],
            "sourceDatasets": ["products"],
            "changedMetricCount": 1,
            "abnormalMetricCount": 1,
            "decision": {
                "status": "passed",
                "baselineOnly": False,
                "taskTriggerAllowed": True,
            },
        },
    }
    bundle = store_artifact(
        artifact_type="pipeline_stage.quality_gate_ready",
        value={
            "dataVersion": "DV-NEW",
            "baselineNoPrevious": False,
            "validatedSignals": [signal],
        },
        data_version="DV-NEW",
        created_by="test",
    )

    result = admission.product_signal_admission_station_v225(
        "DV-NEW",
        validated_bundle_ref=bundle["artifactId"],
        max_admitted=10,
    )
    assert result["admittedSignalCount"] == 1
    assert result["admitted"][0]["agent1InputCompiled"] is True
    assert result["admitted"][0]["transportReady"] is True
    assert result["admitted"][0]["agent1InputProjectionVersion"] == "22.5.8"

    with connect() as conn:
        row = conn.execute(
            "SELECT current_stage,status,artifact_refs_json FROM pipeline_items WHERE signal_id='SIG-P1'"
        ).fetchone()
    refs = loads(row["artifact_refs_json"])
    assert row["current_stage"] == "agent1_pending"
    assert row["status"] == "queued"
    assert str(refs["signalRef"]).startswith("ART-")
    assert str(refs["agent1InputRef"]).startswith("ART-")

    envelope = resolve_agent_input_ref(
        refs["agent1InputRef"],
        expected_schema=AGENT1_INPUT_SCHEMA,
    )
    payload = envelope["payload"]
    guardrails = payload["diagnosticRag"]["guardrails"]
    lineage = payload["sourceLineageValidation"]
    assert "familyGuidance" in guardrails
    assert "permissionBoundary" in guardrails
    assert lineage["status"] == "complete"
    assert lineage["sourceVersionCount"] >= 1
    assert lineage["sourceDatasetCount"] >= 1
    assert lineage["sourceArtifactCount"] == 1
    assert lineage["contentHashVerified"] is True
    assert lineage["sourceIdentityComplete"] is True
    assert lineage["blockingFactors"] == []
    assert payload["crossValidation"]["lineageOwner"] == "sourceLineageValidation"
    assert payload["crossValidation"]["lineageFieldsRemoved"] is True
    assert envelope["hardInterface"]["fallbackAllowed"] is False
