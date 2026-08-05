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


def test_artifact_registry_deduplicates_within_tenant_and_isolates_tenants(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)

    from src.services.artifact_transport_service import (
        resolve_artifact,
        store_artifact,
        validate_artifact,
    )

    payload = {"decisionCore": {"decisionType": "act", "coreProblem": "测试"}}
    first = store_artifact(
        artifact_type="agent1_normalized_judgment",
        value=payload,
        tenant_id="TENANT-A",
        product_id="P-1",
        data_version="DV-1",
        created_by="agent1",
    )
    replay = store_artifact(
        artifact_type="agent1_normalized_judgment",
        value=payload,
        tenant_id="TENANT-A",
        product_id="P-1",
        data_version="DV-2",
        created_by="agent1",
    )
    other_tenant = store_artifact(
        artifact_type="agent1_normalized_judgment",
        value=payload,
        tenant_id="TENANT-B",
        product_id="P-1",
        data_version="DV-1",
        created_by="agent1",
    )

    assert first["artifactId"] == replay["artifactId"]
    assert replay["idempotentHit"] is True
    assert other_tenant["artifactId"] != first["artifactId"]
    assert resolve_artifact(first["artifactId"]) == payload
    assert validate_artifact(first["artifactId"])["status"] == "valid"


def test_artifact_lineage_records_parent_child_relationship(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)

    from src.services.artifact_lineage_service import lineage_graph
    from src.services.artifact_transport_service import store_artifact

    signal = store_artifact(
        artifact_type="product_signal",
        value={"signalId": "SIG-1", "roi": 2.1},
        tenant_id="TENANT-A",
        product_id="P-1",
        created_by="signal_engine",
    )
    judgment = store_artifact(
        artifact_type="agent1_normalized_judgment",
        value={"decisionCore": {"decisionType": "act"}},
        tenant_id="TENANT-A",
        product_id="P-1",
        created_by="agent1",
        parent_refs=[signal["artifactId"]],
    )

    graph = lineage_graph(judgment["artifactId"])
    assert graph["edgeCount"] == 1
    assert graph["edges"][0]["from"] == signal["artifactId"]
    assert graph["edges"][0]["to"] == judgment["artifactId"]


def test_pipeline_dual_write_keeps_compatibility_payload_but_events_use_refs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)

    from src.repositories.sqlite_repository import connect, loads
    from src.services.artifact_transport_service import resolve_artifact
    from src.services.pipeline_item_service import (
        build_item_envelope,
        record_pipeline_item_event,
        upsert_pipeline_item,
    )

    envelope = build_item_envelope(
        data_version="DV-1",
        product_id="P-1",
        store_id="S-1",
        signal_id="SIG-1",
        stage="agent1_completed",
        output_ref="agent1:P-1",
    )
    payload = {
        "dataVersion": "DV-1",
        "productId": "P-1",
        "storeId": "S-1",
        "signalId": "SIG-1",
        "decisionType": "act",
        "agent1DecisionCore": {
            "decisionType": "act",
            "selectedActionFamily": "roas_guard",
        },
        "largeInternalValue": "must_live_in_artifact_not_event",
    }
    completed = upsert_pipeline_item(
        envelope,
        stage="agent1_completed",
        status="ready",
        payload=payload,
        output_ref="agent1:P-1",
    )
    record_pipeline_item_event(
        completed,
        station_id="product_judgment_agent_station",
        stage="agent1_completed",
        status="completed",
        payload=payload,
        output_ref="agent1:P-1",
    )

    with connect() as conn:
        item = conn.execute("SELECT * FROM pipeline_items").fetchone()
        event = conn.execute("SELECT * FROM pipeline_item_events").fetchone()

    refs = loads(item["artifact_refs_json"])
    compatibility = loads(item["payload"])
    event_payload = loads(event["payload"])

    assert refs["agent1Ref"].startswith("ART-")
    assert item["payload_artifact_ref"] == refs["agent1Ref"]
    assert compatibility["payload"]["largeInternalValue"] == "must_live_in_artifact_not_event"
    assert resolve_artifact(refs["agent1Ref"])["largeInternalValue"] == "must_live_in_artifact_not_event"

    assert event_payload["fullPayloadStoredInArtifactHub"] is True
    assert event_payload["artifactRef"] == refs["agent1Ref"]
    assert "largeInternalValue" not in event_payload
    assert event["payload_artifact_ref"] == refs["agent1Ref"]


def test_artifact_ops_routes_do_not_expose_raw_content() -> None:
    from src.api.main import app

    schema = app.openapi()
    paths = schema.get("paths") or {}
    assert "/api/ops/artifacts" in paths
    assert "/api/ops/artifacts/{artifact_id}" in paths
    assert "/api/ops/artifacts/{artifact_id}/lineage" in paths
    detail = paths["/api/ops/artifacts/{artifact_id}"]["get"]
    serialized = str(detail)
    assert "includeContent" not in serialized
