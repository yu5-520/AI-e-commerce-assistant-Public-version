from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest


def _isolate_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.repositories import sqlite_repository
    from src.services import artifact_storage_service

    monkeypatch.setattr(sqlite_repository, "DB_PATH", tmp_path / "runtime.sqlite3")
    monkeypatch.setattr(sqlite_repository, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sqlite_repository, "_WAL_INITIALIZED", False)
    monkeypatch.setattr(artifact_storage_service, "ARTIFACT_ROOT", tmp_path / "artifacts")


def _locked_roas_package(*, package_id: str, data_version: str | None = None) -> dict:
    return {
        "dataVersion": data_version,
        "packageId": package_id,
        "actionFamily": "roas_scale",
        "selectedActionFamily": "roas_scale",
        "route": "roas_scale_test",
        "productId": "P10005",
        "storeId": "DY-SH-003",
        "productTitle": "测试商品",
        "decisionType": "act",
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "selectedOperatingRoute": "roas_scale_test",
            "selectedActionFamily": "roas_scale",
            "routeLock": {
                "locked": True,
                "selectedOperatingRoute": "roas_scale_test",
                "observationOnly": False,
            },
            "actionFamilyLock": {
                "locked": True,
                "selectedActionFamily": "roas_scale",
                "forbiddenOverride": True,
                "observationOnly": False,
            },
        },
        "actionParameterPack": {
            "actionFamily": "roas_scale",
            "adPlanFacts": [],
        },
    }


def test_hard_runtime_is_public_and_native_helpers_require_no_monkey_patch() -> None:
    from src.api.main import api_version
    from src.services.agent_runtime_hard_interface_v230_service import (
        agent_runtime_hard_interface_status,
    )
    from src.services.agent_runtime_native_v2263_service import (
        agent_runtime_integrity_status,
    )
    from src.services.station_agent_worker_v2263_service import worker_config

    native = agent_runtime_integrity_status()
    assert native["version"] == "22.2.6.3"
    assert native["native"] is True
    assert native["monkeyPatchRequired"] is False
    assert native["agent1ClaimMode"] == "finite_sqlite_lease_native"
    assert native["agent2TargetProjection"] == "native_before_final_contract"

    hard = agent_runtime_hard_interface_status()
    assert hard["version"] == "22.3.0"
    assert hard["hardInterface"] is True
    assert hard["agent1RuntimeSource"] == "artifactRefs.agent1InputRef"
    assert hard["agent2RuntimeSource"] == "artifactRefs.agent2InputRef"
    assert hard["unprojectedProviderInputAllowed"] is False

    config = worker_config()
    assert config["version"] == "22.4.0"
    assert config["hardAgentRuntimeVersion"] == "22.3.0"
    assert config["nativeLeaseRuntimeVersion"] == "22.2.6.3"
    assert config["agentExecutionMode"] == "hard_interface_projection_artifact_only"
    assert config["releaseIdentity"]["schema"] == "release.identity.v1"

    version = api_version()
    assert "agentRuntimeRecovery" not in version
    assert version["agentHardInterface"]["hardInterface"] is True
    assert version["agentHardInterface"]["runtimeMonkeyPatchRequired"] is False
    assert version["runtimeVersions"]["agentRuntimeHardInterface"] == "22.3.0"
    assert version["agentFlow"]["hardInterfaceVersion"] == "22.5.5"
    assert version["agentFlow"]["activeBusinessPipelineVersion"] == "22.5.5"
    assert version["agentFlow"]["fullSignalReadByAgentAllowed"] is False
    assert version["agentFlow"]["fullCapabilityReadByAgentAllowed"] is False
    assert version["releaseIdentity"]["schema"] == "release.identity.v1"


def test_native_agent2_projection_repairs_normalized_target_contract() -> None:
    from src.services.agent_runtime_native_v2263_service import repair_agent2_plan_native

    package = _locked_roas_package(package_id="PKG-1")
    plan = {
        "packageId": "PKG-1",
        "actionFamily": "roas_scale",
        "actionPlanStatus": "action_plan_missing_data",
        "conflictReason": (
            "Agent2 output did not satisfy V22 contract: "
            "executionObject.targetId_or_targetSelector,"
            "operations[0].target.id_or_selector"
        ),
        "reason": (
            "Agent2 output did not satisfy V22 contract: "
            "executionObject.targetId_or_targetSelector,"
            "operations[0].target.id_or_selector"
        ),
        "finalTaskTitle": "提升测试商品投放预算",
        "operationMode": "single_product_ad_plan_adjustment",
        "differentiationReason": "ROI高于安全线，执行小步放量。",
        "executionObject": {},
        "operatorActionSteps": ["在广告后台核对商品绑定计划后调整预算。"],
        "executionSteps": [],
        "operationPlan": {
            "version": "21.4.0",
            "schema": "operation_plan_ir.v1",
            "actionFamily": "roas_scale",
            "operations": [
                {
                    "operationId": "OP-1",
                    "operationType": "budget_update",
                    "target": {},
                    "direction": "increase",
                    "currentValue": {"budget": 100},
                    "targetValue": {"budget": 120},
                    "adjustmentAmount": 20,
                }
            ],
        },
        "reviewMetrics": ["ROI", "广告消耗"],
        "missingData": [],
    }

    repaired = repair_agent2_plan_native(plan, package)
    selector = repaired["executionObject"]["targetSelector"]
    assert selector["storeId"] == "DY-SH-003"
    assert selector["productId"] == "P10005"
    operation_target = repaired["operationPlan"]["operations"][0]["target"]
    assert operation_target["selector"]["storeId"] == "DY-SH-003"
    assert operation_target["selector"]["productId"] == "P10005"
    assert repaired["semanticContractMissing"] == []
    assert repaired["actionPlanStatus"] == "ready"
    assert repaired["conflictReason"] is None
    assert repaired["executionTargetProjection"]["nativeRuntime"] is True


def test_native_migration_allows_exactly_one_second_target_replay(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect, dumps
    from src.services.agent_runtime_native_v2263_service import (
        recover_target_only_agent2_failures_native,
    )
    from src.services.agent_runtime_recovery_v2261_service import ensure_agent1_runtime_columns
    from src.services.artifact_transport_service import store_artifact

    ensure_agent1_runtime_columns()
    capability = store_artifact(
        artifact_type="pipeline_stage.action_pack_ready",
        value=_locked_roas_package(package_id="PKG-NATIVE", data_version="DV-NATIVE"),
        data_version="DV-NATIVE",
        product_id="P10005",
        store_id="DY-SH-003",
        created_by="test",
    )
    failed = store_artifact(
        artifact_type="pipeline_stage.agent2_output_invalid",
        value={"reason": "target contract"},
        data_version="DV-NATIVE",
        product_id="P10005",
        store_id="DY-SH-003",
        created_by="test",
    )
    refs = {
        "capabilityRef": capability["artifactId"],
        "agent2FailureRef": failed["artifactId"],
        "currentStageRef": failed["artifactId"],
    }
    now = datetime.now().isoformat()
    error = (
        "Agent2 output did not satisfy V22 contract: "
        "executionObject.targetId_or_targetSelector,"
        "operations[0].target.id_or_selector"
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_items
            (item_id,data_version,product_id,store_id,package_id,current_stage,status,
             action_family,last_error_code,artifact_refs_json,payload_artifact_ref,
             agent2_target_repair_count,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "PI-NATIVE-TARGET",
                "DV-NATIVE",
                "P10005",
                "DY-SH-003",
                "PKG-NATIVE",
                "agent2_output_invalid",
                "failed",
                "roas_scale",
                error,
                dumps(refs),
                failed["artifactId"],
                1,
                now,
                now,
            ),
        )
        conn.commit()

    first = recover_target_only_agent2_failures_native("DV-NATIVE")
    assert first["recoveredItemCount"] == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_items WHERE item_id='PI-NATIVE-TARGET'"
        ).fetchone()
    assert row["current_stage"] == "action_pack_ready"
    assert row["status"] == "retry"
    assert row["agent2_target_repair_count"] == 2
    assert row["payload_artifact_ref"] == capability["artifactId"]

    with connect() as conn:
        conn.execute(
            """
            UPDATE pipeline_items
            SET current_stage='agent2_output_invalid',status='failed',last_error_code=?
            WHERE item_id='PI-NATIVE-TARGET'
            """,
            (error,),
        )
        conn.commit()
    second = recover_target_only_agent2_failures_native("DV-NATIVE")
    assert second["recoveredItemCount"] == 0
