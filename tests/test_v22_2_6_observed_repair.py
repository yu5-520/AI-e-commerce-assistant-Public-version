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


def _signal(product_id: str, changed: bool) -> dict:
    return {
        "signalId": f"SIG-{product_id}",
        "packageId": f"PKG-{product_id}",
        "productId": product_id,
        "storeId": "S1",
        "primarySignalType": "product_margin_changed" if changed else "normal_state",
        "previousProductMetricSnapshot": {"grossMargin": 40},
        "snapshotLayer": {
            "fieldSignals": [
                {
                    "metricCode": "grossMargin",
                    "signalStrength": "medium" if changed else "normal",
                    "meaningfulChange": changed,
                    "changeVsPrevious": -0.04 if changed else 0,
                }
            ]
        },
        "crossValidation": {
            "version": "21.5.0",
            "contract": "operatingEvidenceGraph.v1",
            "sourceDataVersions": ["DV-OLD", "DV-NEW"],
            "sourceDatasets": ["products"],
            "sourceVersionCount": 2,
            "sourceDatasetCount": 1,
            "changedMetricCount": 1 if changed else 0,
            "abnormalMetricCount": 1 if changed else 0,
            "decision": {
                "status": "passed",
                "baselineOnly": False,
                "taskTriggerAllowed": changed,
            },
        },
    }


def _observed_item(signal: dict) -> None:
    from src.services.artifact_transport_service import store_artifact
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item

    stored = store_artifact(
        artifact_type="product_signal",
        value=signal,
        data_version="DV-NEW",
        product_id=signal["productId"],
        store_id="S1",
        created_by="test",
    )
    envelope = build_item_envelope(
        data_version="DV-NEW",
        product_id=signal["productId"],
        store_id="S1",
        signal_id=signal["signalId"],
        package_id=signal["packageId"],
        input_ref=stored["artifactId"],
        output_ref=f"observed_signal:{signal['signalId']}",
        stage="observed_soft_gate",
        artifact_refs={"signalRef": stored["artifactId"]},
    )
    upsert_pipeline_item(
        envelope,
        stage="observed_soft_gate",
        status="observed",
        payload={"source": "legacy_score_gate", "admissionDecision": "observed"},
    )


def test_deploy_repair_requeues_only_meaningful_legacy_observations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect
    from src.services.observed_signal_repair_v226_service import (
        repair_misclassified_observations_v226,
    )

    _observed_item(_signal("P1", changed=True))
    _observed_item(_signal("P2", changed=False))
    result = repair_misclassified_observations_v226("DV-NEW")
    assert result["inspectedObservedItemCount"] == 2
    assert result["requeuedAgent1PendingCount"] == 1
    assert result["preservedTrueObservationCount"] == 1
    assert result["legacySignalPoolRead"] is False

    with connect() as conn:
        rows = conn.execute(
            "SELECT product_id,current_stage,status FROM pipeline_items ORDER BY product_id"
        ).fetchall()
    assert dict(rows[0]) == {
        "product_id": "P1",
        "current_stage": "agent1_pending",
        "status": "queued",
    }
    assert dict(rows[1]) == {
        "product_id": "P2",
        "current_stage": "observed_soft_gate",
        "status": "observed",
    }

    second = repair_misclassified_observations_v226("DV-NEW")
    assert second["requeuedAgent1PendingCount"] == 0
    assert second["preservedTrueObservationCount"] == 1
