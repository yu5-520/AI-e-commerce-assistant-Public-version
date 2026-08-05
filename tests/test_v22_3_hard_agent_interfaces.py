from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.services.agent_input_contract_v230_service import (
    AGENT_INPUT_CONTRACT_VERSION,
    AGENT1_INPUT_SCHEMA,
    AGENT2_INPUT_SCHEMA,
    AgentInputContractError,
    build_projection_envelope,
    validate_agent_input_envelope,
)
from src.services.agent_input_transport_v230_service import (
    compile_agent1_envelope,
    compile_agent2_envelope,
)
from src.services.agent_runtime_hard_interface_v230_service import (
    agent_runtime_hard_interface_status,
)
from src.services.pipeline_artifact_contract_service import input_artifact_id


def _isolate_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.repositories import sqlite_repository
    from src.services import artifact_storage_service

    monkeypatch.setattr(sqlite_repository, "DB_PATH", tmp_path / "runtime.sqlite3")
    monkeypatch.setattr(sqlite_repository, "LOG_DIR", tmp_path)
    monkeypatch.setattr(sqlite_repository, "_WAL_INITIALIZED", False)
    monkeypatch.setattr(artifact_storage_service, "ARTIFACT_ROOT", tmp_path / "artifacts")


def test_agent1_transport_creates_small_whitelisted_projection() -> None:
    source = {
        "productId": "P-1",
        "storeId": "S-1",
        "signalId": "SIG-1",
        "payload": {
            "productIdentity": {
                "productId": "P-1",
                "storeId": "S-1",
                "productTitle": "测试商品",
                "platform": "抖音",
            },
            "snapshotLayer": {
                "roi": 2.1,
                "payment": 1200,
                "fieldSignals": [
                    {
                        "metricCode": "roi",
                        "previous": 2.8,
                        "current": 2.1,
                        "changeRatio": -0.25,
                    }
                ],
                "providerTrace": {"huge": "x" * 50_000},
            },
            "dynamicMetrics": {"clickRate": 0.08},
            "strongRelations": [
                {"metricCode": "paidVisitors", "changeRate": 0.31}
            ],
            "crossValidation": {"status": "passed", "changedMetricCount": 2},
            "systemFacts": {"huge": "must_not_enter_projection"},
        },
    }
    envelope = compile_agent1_envelope(
        source,
        source_ref="ART-SIGNAL",
        source_content_hash="HASH-SIGNAL",
        policy_context={"principles": ["趋势交叉判断"]},
    )
    result = validate_agent_input_envelope(
        envelope,
        expected_schema=AGENT1_INPUT_SCHEMA,
    )
    assert result["ok"] is True
    assert envelope["hardInterface"]["fallbackAllowed"] is False
    contract = envelope["payload"]["inputContract"]
    assert contract["fullSignalReadAllowed"] is False
    assert contract["sourceContentHash"] == "HASH-SIGNAL"
    assert contract["policyContextHash"]
    text = json.dumps(envelope, ensure_ascii=False)
    assert "providerTrace" not in text
    assert "must_not_enter_projection" not in text
    assert len(text) < 14_000


def test_agent2_transport_keeps_contract_fields_without_full_capability_payload() -> None:
    source = {
        "packageId": "PKG-1",
        "productId": "P-1",
        "storeId": "S-1",
        "productTitle": "测试商品",
        "decisionType": "act",
        "agent1DecisionIR": {
            "decisionType": "act",
            "coreProblem": "低效流量增长",
            "selectedActionFamily": "roas_guard",
        },
        "agent1OperatingJudgment": {
            "decisionType": "act",
            "routeLock": {
                "locked": True,
                "selectedOperatingRoute": "paid_efficiency_route",
            },
            "actionFamilyLock": {
                "locked": True,
                "forbiddenOverride": True,
                "selectedActionFamily": "roas_guard",
            },
        },
        "matrixDispatch": {
            "lockedByAgent1": True,
            "selectedActionFamily": "roas_guard",
        },
        "actionParameterPack": {
            "status": "valid",
            "actionFamily": "roas_guard",
            "compilerRole": "facts_permissions_and_numeric_limits_only",
            "currentROI": 2.1,
            "permissionBounds": {"budgetChangeCeiling": 0.2},
            "providerTrace": {"huge": "x" * 50_000},
        },
        "systemFacts": {"huge": "must_not_enter_projection"},
    }
    envelope = compile_agent2_envelope(
        source,
        source_ref="ART-CAPABILITY",
        source_content_hash="HASH-CAPABILITY",
    )
    result = validate_agent_input_envelope(
        envelope,
        expected_schema=AGENT2_INPUT_SCHEMA,
    )
    assert result["ok"] is True
    payload = envelope["payload"]
    assert payload["lockedActionFamily"] == "roas_guard"
    assert payload["actionParameterPack"]["compilerRole"] == (
        "facts_permissions_and_numeric_limits_only"
    )
    assert payload["inputContract"]["fullCapabilityReadAllowed"] is False
    assert payload["inputContract"]["sourceContentHash"] == "HASH-CAPABILITY"
    text = json.dumps(envelope, ensure_ascii=False)
    assert "providerTrace" not in text
    assert "must_not_enter_projection" not in text


def test_existing_agent1_projection_is_rebuilt_when_source_or_policy_changes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect, dumps, loads
    from src.services.agent_input_transport_v230_service import ensure_agent1_input_ref
    from src.services.artifact_transport_service import store_artifact
    from src.services.pipeline_item_service import ensure_pipeline_item_tables

    ensure_pipeline_item_tables()
    first_signal = store_artifact(
        artifact_type="product_signal",
        value={
            "productId": "P-1",
            "storeId": "S-1",
            "signalId": "SIG-1",
            "productIdentity": {
                "productId": "P-1",
                "storeId": "S-1",
                "productTitle": "测试商品",
            },
            "snapshotLayer": {
                "fieldSignals": [
                    {"metricCode": "roi", "previous": 2.8, "current": 2.1}
                ]
            },
        },
        data_version="DV-1",
        product_id="P-1",
        store_id="S-1",
        created_by="test",
    )
    second_signal = store_artifact(
        artifact_type="product_signal",
        value={
            "productId": "P-1",
            "storeId": "S-1",
            "signalId": "SIG-2",
            "productIdentity": {
                "productId": "P-1",
                "storeId": "S-1",
                "productTitle": "测试商品",
            },
            "snapshotLayer": {
                "fieldSignals": [
                    {"metricCode": "roi", "previous": 2.1, "current": 2.6}
                ]
            },
        },
        data_version="DV-2",
        product_id="P-1",
        store_id="S-1",
        created_by="test",
    )
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO pipeline_items(
                item_id,data_version,product_id,store_id,signal_id,current_stage,status,
                artifact_refs_json,payload_artifact_ref,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
            """,
            (
                "PI-HARD-A1",
                "DV-1",
                "P-1",
                "S-1",
                "SIG-1",
                "agent1_pending",
                "queued",
                dumps(
                    {
                        "signalRef": first_signal["artifactId"],
                        "currentStageRef": first_signal["artifactId"],
                    }
                ),
                first_signal["artifactId"],
            ),
        )
        conn.commit()
        row = dict(
            conn.execute(
                "SELECT * FROM pipeline_items WHERE item_id='PI-HARD-A1'"
            ).fetchone()
        )

    first_input = ensure_agent1_input_ref(
        row,
        policy_context={"principles": ["原则A"]},
    )
    with connect() as conn:
        stored = conn.execute(
            "SELECT artifact_refs_json FROM pipeline_items WHERE item_id='PI-HARD-A1'"
        ).fetchone()
        refs = loads(stored["artifact_refs_json"])
        refs["signalRef"] = second_signal["artifactId"]
        refs["currentStageRef"] = first_input
        conn.execute(
            """
            UPDATE pipeline_items
            SET data_version='DV-2',signal_id='SIG-2',artifact_refs_json=?,
                payload_artifact_ref=?
            WHERE item_id='PI-HARD-A1'
            """,
            (dumps(refs), second_signal["artifactId"]),
        )
        conn.commit()
        changed_source_row = dict(
            conn.execute(
                "SELECT * FROM pipeline_items WHERE item_id='PI-HARD-A1'"
            ).fetchone()
        )

    second_input = ensure_agent1_input_ref(
        changed_source_row,
        policy_context={"principles": ["原则A"]},
    )
    assert second_input != first_input

    with connect() as conn:
        policy_row = dict(
            conn.execute(
                "SELECT * FROM pipeline_items WHERE item_id='PI-HARD-A1'"
            ).fetchone()
        )
    third_input = ensure_agent1_input_ref(
        policy_row,
        policy_context={"principles": ["原则B"]},
    )
    assert third_input != second_input


def test_hard_pipeline_stages_never_fall_back_to_full_upstream_refs() -> None:
    agent1_row = {
        "current_stage": "agent1_pending",
        "payload_artifact_ref": "ART-SIGNAL",
        "artifact_refs_json": json.dumps(
            {"signalRef": "ART-SIGNAL", "currentStageRef": "ART-SIGNAL"}
        ),
    }
    agent2_row = {
        "current_stage": "action_pack_ready",
        "payload_artifact_ref": "ART-CAPABILITY",
        "artifact_refs_json": json.dumps(
            {
                "capabilityRef": "ART-CAPABILITY",
                "currentStageRef": "ART-CAPABILITY",
            }
        ),
    }
    assert input_artifact_id(agent1_row) is None
    assert input_artifact_id(agent2_row) is None

    agent1_row["artifact_refs_json"] = json.dumps(
        {
            "signalRef": "ART-SIGNAL",
            "agent1InputRef": "ART-A1-INPUT",
            "currentStageRef": "ART-A1-INPUT",
        }
    )
    agent2_row["artifact_refs_json"] = json.dumps(
        {
            "capabilityRef": "ART-CAPABILITY",
            "agent2InputRef": "ART-A2-INPUT",
            "currentStageRef": "ART-A2-INPUT",
        }
    )
    assert input_artifact_id(agent1_row) == "ART-A1-INPUT"
    assert input_artifact_id(agent2_row) == "ART-A2-INPUT"


def test_unknown_fields_and_oversized_projection_fail_closed() -> None:
    payload = {
        "productId": "P-1",
        "storeId": "S-1",
        "productIdentity": {"productId": "P-1", "storeId": "S-1"},
        "inputContract": {
            "schema": AGENT1_INPUT_SCHEMA,
            "version": AGENT_INPUT_CONTRACT_VERSION,
        },
        "forbiddenField": "not allowed",
    }
    envelope = {
        "schema": AGENT1_INPUT_SCHEMA,
        "projectionVersion": AGENT_INPUT_CONTRACT_VERSION,
        "sourceArtifactRefs": ["ART-1"],
        "sourceContentHash": "HASH",
        "projectedContentHash": "wrong",
        "payload": payload,
        "projectionAudit": {},
        "hardInterface": {"enabled": True, "fallbackAllowed": False},
    }
    result = validate_agent_input_envelope(
        envelope,
        expected_schema=AGENT1_INPUT_SCHEMA,
    )
    assert result["ok"] is False
    assert any("unknown_payload_fields" in error for error in result["errors"])

    with pytest.raises(AgentInputContractError):
        build_projection_envelope(
            schema=AGENT1_INPUT_SCHEMA,
            payload={
                "productId": "P-1",
                "storeId": "S-1",
                "productIdentity": {"productId": "P-1", "storeId": "S-1"},
                "inputContract": {
                    "schema": AGENT1_INPUT_SCHEMA,
                    "version": AGENT_INPUT_CONTRACT_VERSION,
                },
                "metricLayer": {"huge": "x" * 20_000},
            },
            source_artifact_refs=["ART-1"],
            source_content_hash="HASH",
        )


def test_runtime_status_declares_one_hard_interface_owner() -> None:
    status = agent_runtime_hard_interface_status()
    assert status["version"] == "22.3.0"
    assert status["hardInterface"] is True
    assert status["agent1RuntimeSource"] == "artifactRefs.agent1InputRef"
    assert status["agent2RuntimeSource"] == "artifactRefs.agent2InputRef"
    assert status["unprojectedProviderInputAllowed"] is False
    assert status["fullSignalReadByAgentAllowed"] is False
    assert status["fullCapabilityReadByAgentAllowed"] is False
    assert status["fallbackAllowed"] is False
