from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest


def _isolate_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.repositories import sqlite_repository
    from src.services import artifact_storage_service

    monkeypatch.setattr(sqlite_repository, "DB_PATH", tmp_path / "runtime.sqlite3")
    monkeypatch.setattr(sqlite_repository, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sqlite_repository, "_WAL_INITIALIZED", False)
    monkeypatch.setattr(artifact_storage_service, "ARTIFACT_ROOT", tmp_path / "artifacts")


def test_stale_agent1_running_is_requeued_from_signal_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect, loads
    from src.services.agent_runtime_recovery_v2261_service import (
        agent1_lease_seconds,
        ensure_agent1_runtime_columns,
        recover_stale_agent1_items,
    )
    from src.services.artifact_transport_service import store_artifact
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item

    ensure_agent1_runtime_columns()
    signal = {
        "signalId": "SIG-1",
        "productId": "P1",
        "storeId": "S1",
        "crossValidation": {"decision": {"status": "passed"}},
    }
    stored = store_artifact(
        artifact_type="product_signal",
        value=signal,
        data_version="DV-1",
        product_id="P1",
        store_id="S1",
        created_by="test",
    )
    envelope = build_item_envelope(
        data_version="DV-1",
        product_id="P1",
        store_id="S1",
        signal_id="SIG-1",
        stage="agent1_running",
        artifact_refs={"signalRef": stored["artifactId"]},
    )
    upsert_pipeline_item(
        envelope,
        stage="agent1_running",
        status="running",
        payload={"source": "test"},
    )
    stale = (datetime.now() - timedelta(seconds=agent1_lease_seconds() + 60)).isoformat()
    with connect() as conn:
        conn.execute(
            "UPDATE pipeline_items SET updated_at=?,lease_expires_at=NULL WHERE item_id=?",
            (stale, envelope["itemId"]),
        )
        conn.commit()

    result = recover_stale_agent1_items("DV-1")
    assert result["requeuedItemCount"] == 1
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_items WHERE item_id=?",
            (envelope["itemId"],),
        ).fetchone()
    refs = loads(row["artifact_refs_json"])
    assert row["current_stage"] == "agent1_pending"
    assert row["status"] == "retry"
    assert row["retry_count"] == 1
    assert row["payload_artifact_ref"] == stored["artifactId"]
    assert refs["signalRef"] == stored["artifactId"]
    assert refs["currentStageRef"] == stored["artifactId"]


def test_agent1_claim_receives_finite_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect
    from src.services.agent_runtime_recovery_v2261_service import claim_agent1_items
    from src.services.artifact_transport_service import store_artifact
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item

    stored = store_artifact(
        artifact_type="product_signal",
        value={"signalId": "SIG-2", "productId": "P2", "storeId": "S1"},
        data_version="DV-2",
        product_id="P2",
        store_id="S1",
        created_by="test",
    )
    envelope = build_item_envelope(
        data_version="DV-2",
        product_id="P2",
        store_id="S1",
        signal_id="SIG-2",
        stage="agent1_pending",
        artifact_refs={"signalRef": stored["artifactId"]},
    )
    upsert_pipeline_item(envelope, stage="agent1_pending", status="queued", payload={"source": "test"})
    items = [{"item_id": envelope["itemId"]}]
    claim_agent1_items(items)
    assert len(items) == 1
    with connect() as conn:
        row = conn.execute("SELECT * FROM pipeline_items WHERE item_id=?", (envelope["itemId"],)).fetchone()
    assert row["current_stage"] == "agent1_running"
    assert row["status"] == "running"
    assert str(row["claim_id"]).startswith("A1L-")
    assert row["lease_expires_at"]
    assert row["agent1_claim_owner"]


def test_roas_missing_targets_are_filled_from_canonical_selector() -> None:
    from src.services.agent_runtime_recovery_v2261_service import apply_roas_execution_target

    package = {
        "actionFamily": "roas_scale",
        "productId": "P10005",
        "storeId": "DY-SH-003",
        "productTitle": "测试商品",
        "actionParameterPack": {
            "actionFamily": "roas_scale",
            "adPlanFacts": [],
        },
    }
    raw = {
        "actionFamily": "roas_scale",
        "executionObject": {},
        "operationPlan": {
            "operations": [
                {"operationType": "budget_update", "target": {}},
                {"operationType": "target_roas_update"},
            ]
        },
    }
    result = apply_roas_execution_target(raw, package)
    execution = result["executionObject"]
    assert execution["targetSelector"]["storeId"] == "DY-SH-003"
    assert execution["targetSelector"]["productId"] == "P10005"
    for operation in result["operationPlan"]["operations"]:
        assert operation["target"]["selector"]["storeId"] == "DY-SH-003"
        assert operation["target"]["selector"]["productId"] == "P10005"


def test_target_only_agent2_failure_replays_once_from_capability_ref(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect, dumps, loads
    from src.services.agent_runtime_recovery_v2261_service import (
        recover_target_only_agent2_failures,
    )
    from src.services.artifact_transport_service import store_artifact
    from src.services.pipeline_item_service import ensure_pipeline_item_tables

    capability = store_artifact(
        artifact_type="pipeline_stage.action_pack_ready",
        value={
            "dataVersion": "DV-3",
            "productId": "P10005",
            "storeId": "DY-SH-003",
            "packageId": "PKG-1",
            "actionFamily": "roas_scale",
            "actionParameterPack": {"actionFamily": "roas_scale", "adPlanFacts": []},
        },
        data_version="DV-3",
        product_id="P10005",
        store_id="DY-SH-003",
        created_by="test",
    )
    failure = store_artifact(
        artifact_type="pipeline_stage.agent2_output_invalid",
        value={"reason": "missing target"},
        data_version="DV-3",
        product_id="P10005",
        store_id="DY-SH-003",
        created_by="test",
    )
    ensure_pipeline_item_tables()
    now = datetime.now().isoformat()
    refs = {
        "capabilityRef": capability["artifactId"],
        "agent2FailureRef": failure["artifactId"],
        "currentStageRef": failure["artifactId"],
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_items
            (item_id,data_version,product_id,store_id,package_id,current_stage,status,
             action_family,last_error_code,artifact_refs_json,payload_artifact_ref,
             created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "PI-TARGET-FAIL",
                "DV-3",
                "P10005",
                "DY-SH-003",
                "PKG-1",
                "agent2_output_invalid",
                "failed",
                "roas_scale",
                "Agent2 output did not satisfy V22 contract: executionObject.targetId_or_targetSelector,operations[0].target.id_or_selector",
                dumps(refs),
                failure["artifactId"],
                now,
                now,
            ),
        )
        conn.commit()

    first = recover_target_only_agent2_failures("DV-3")
    assert first["recoveredItemCount"] == 1
    with connect() as conn:
        row = conn.execute("SELECT * FROM pipeline_items WHERE item_id='PI-TARGET-FAIL'").fetchone()
    updated_refs = loads(row["artifact_refs_json"])
    assert row["current_stage"] == "action_pack_ready"
    assert row["status"] == "retry"
    assert row["agent2_target_repair_count"] == 1
    assert row["payload_artifact_ref"] == capability["artifactId"]
    assert updated_refs["currentStageRef"] == capability["artifactId"]

    with connect() as conn:
        conn.execute(
            "UPDATE pipeline_items SET current_stage='agent2_output_invalid',status='failed' WHERE item_id='PI-TARGET-FAIL'"
        )
        conn.commit()
    second = recover_target_only_agent2_failures("DV-3")
    assert second["recoveredItemCount"] == 0
