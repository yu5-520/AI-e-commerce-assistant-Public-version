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


def _changed_signal(index: int) -> dict:
    # This is intentionally worth only 38 points in the old score:
    # changed + abnormal + previous source. grossMargin is not one of the old
    # special metric families, so the former >=45 gate incorrectly observed it.
    return {
        "signalId": f"SIG-{index:03d}",
        "packageId": f"PKG-{index:03d}",
        "productId": f"P{index:03d}",
        "storeId": "S1",
        "primarySignalType": "product_margin_changed",
        "previousProductMetricSnapshot": {"grossMargin": 40},
        "snapshotLayer": {
            "fieldSignals": [
                {
                    "metricCode": "grossMargin",
                    "signalStrength": "medium",
                    "meaningfulChange": True,
                    "changeVsPrevious": -0.04,
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
            "changedMetricCount": 1,
            "abnormalMetricCount": 1,
            "decision": {
                "status": "passed",
                "baselineOnly": False,
                "taskTriggerAllowed": True,
                "changedMetricCount": 1,
                "abnormalMetricCount": 1,
                "sourceVersionCount": 2,
                "sourceDatasetCount": 1,
            },
        },
    }


def _validated_bundle(signals: list[dict]) -> dict:
    return {
        "businessOutputType": "validated_product_signal_snapshot",
        "dataVersion": "DV-NEW",
        "baselineNoPrevious": False,
        "validatedSignals": signals,
    }


def test_30_meaningful_products_enter_agent1_even_when_old_score_is_below_45(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect, loads
    from src.services.artifact_transport_service import store_artifact
    from src.services.end_to_end_agent_flow_v226_service import (
        product_signal_admission_station_v226,
    )
    from src.services.product_signal_admission_v197_service import score_signal

    signals = [_changed_signal(index) for index in range(1, 31)]
    assert all(score_signal(signal)["score"] < 45 for signal in signals)
    stored = store_artifact(
        artifact_type="pipeline_stage.quality_gate_ready",
        value=_validated_bundle(signals),
        data_version="DV-NEW",
        created_by="test",
    )
    result = product_signal_admission_station_v226(
        "DV-NEW",
        validated_bundle_ref=stored["artifactId"],
        max_signals=160,
        max_admitted=160,
    )
    assert result["fullSignalCount"] == 30
    assert result["qualifiedSignalCount"] == 30
    assert result["admittedSignalCount"] == 30
    assert result["observedSignalCount"] == 0
    assert result["agent1PendingItemCount"] == 30
    assert result["scoreCanBlockAgent1"] is False
    assert result["scoreRole"] == "priority_only"

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT current_stage,status,artifact_refs_json
            FROM pipeline_items
            WHERE data_version='DV-NEW'
            ORDER BY product_id
            """
        ).fetchall()
    assert len(rows) == 30
    assert all(row["current_stage"] == "agent1_pending" for row in rows)
    assert all(row["status"] == "queued" for row in rows)
    assert all(str(loads(row["artifact_refs_json"])["signalRef"]).startswith("ART-") for row in rows)


def test_zero_change_product_remains_observation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.services.artifact_transport_service import store_artifact
    from src.services.end_to_end_agent_flow_v226_service import (
        product_signal_admission_station_v226,
    )

    signal = _changed_signal(1)
    signal["snapshotLayer"]["fieldSignals"][0]["meaningfulChange"] = False
    signal["crossValidation"]["changedMetricCount"] = 0
    signal["crossValidation"]["abnormalMetricCount"] = 0
    signal["crossValidation"]["decision"]["taskTriggerAllowed"] = False
    signal["crossValidation"]["decision"]["changedMetricCount"] = 0
    signal["crossValidation"]["decision"]["abnormalMetricCount"] = 0
    stored = store_artifact(
        artifact_type="pipeline_stage.quality_gate_ready",
        value=_validated_bundle([signal]),
        data_version="DV-NEW",
        created_by="test",
    )
    result = product_signal_admission_station_v226(
        "DV-NEW",
        validated_bundle_ref=stored["artifactId"],
    )
    assert result["admittedSignalCount"] == 0
    assert result["observedSignalCount"] == 1
    assert result["admissionReasonCounts"] == {"zero_meaningful_change": 1}


def test_agent1_reads_signal_artifact_without_signal_pool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect
    from src.services import pipeline_agent1_microbatch_v20101_service as agent1_core
    from src.services.artifact_transport_service import store_artifact
    from src.services.end_to_end_agent_flow_v226_service import run_agent1_microbatch_v226
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item

    signal = _changed_signal(1)
    stored = store_artifact(
        artifact_type="product_signal",
        value=signal,
        data_version="DV-NEW",
        product_id="P001",
        store_id="S1",
        created_by="test",
    )
    envelope = build_item_envelope(
        data_version="DV-NEW",
        product_id="P001",
        store_id="S1",
        signal_id="SIG-001",
        package_id="PKG-001",
        input_ref=stored["artifactId"],
        stage="agent1_pending",
        artifact_refs={"signalRef": stored["artifactId"]},
    )
    upsert_pipeline_item(
        envelope,
        stage="agent1_pending",
        status="queued",
        payload={"source": "test_signal_ref"},
    )
    monkeypatch.setattr(
        agent1_core,
        "_real_agent_judgments",
        lambda signals, data_version, policy: (
            [
                {
                    "signalId": "SIG-001",
                    "productId": "P001",
                    "decisionType": "observe",
                    "decisionCore": {"decisionType": "observe"},
                    "decisionHint": "observe_only",
                }
            ],
            {"providerStatus": "completed", "actualCalls": 1},
        ),
    )
    result = run_agent1_microbatch_v226("DV-NEW", batch_size=8)
    assert result["claimedItemCount"] == 1
    assert result["validSignalArtifactCount"] == 1
    assert result["observedItemCount"] == 1
    assert result["runtimeSource"] == "artifactRefs.signalRef"
    assert result["legacySignalPoolRead"] is False
    assert result["legacySignalPoolWrite"] is False
    with connect() as conn:
        row = conn.execute(
            "SELECT current_stage,status FROM pipeline_items WHERE data_version='DV-NEW' LIMIT 1"
        ).fetchone()
        signal_pool = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_pool_v14'"
        ).fetchone()
    assert row["current_stage"] == "observed_soft_gate"
    assert row["status"] == "observed"
    assert signal_pool is None


def test_unified_worker_actually_selects_agent1_pending(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.services import end_to_end_agent_flow_v226_service as flow
    from src.services.artifact_transport_service import store_artifact
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item

    stored = store_artifact(
        artifact_type="product_signal",
        value=_changed_signal(1),
        data_version="DV-NEW",
        product_id="P001",
        store_id="S1",
        created_by="test",
    )
    envelope = build_item_envelope(
        data_version="DV-NEW",
        product_id="P001",
        store_id="S1",
        signal_id="SIG-001",
        package_id="PKG-001",
        input_ref=stored["artifactId"],
        stage="agent1_pending",
        artifact_refs={"signalRef": stored["artifactId"]},
    )
    upsert_pipeline_item(envelope, stage="agent1_pending", status="queued", payload={"source": "test"})
    monkeypatch.setattr(
        flow,
        "run_agent1_microbatch_v226",
        lambda **kwargs: {
            "claimedItemCount": 1,
            "completedItemCount": 1,
            "pendingItemCount": 0,
            "provider": {"actualCalls": 1},
        },
    )
    result = flow.run_agent_pipeline_tick_v226("DV-NEW", worker_id="test-worker")
    assert result["ran"] is True
    assert result["selectedStage"] == "agent1_pending_to_agent1_completed_or_observed"
    assert result["agent1PendingHandled"] is True


def test_pipeline_live_separates_one_batch_from_30_product_items(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.repositories.sqlite_repository import connect
    from src.services import end_to_end_agent_flow_v226_service as flow
    from src.services import pipeline_live_read_model_v208_service as live
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item
    from src.services.station_queue_service import enqueue_task_generation

    queued = enqueue_task_generation("DV-LIVE")
    parent = queued["job"]["jobId"]
    with connect() as conn:
        conn.execute(
            """
            UPDATE station_queue
            SET station_id='product_signal_admission_station',stage='product_signal_admitted',
                status='completed',output_ref='ART-BATCH'
            WHERE parent_job_id=?
            """,
            (parent,),
        )
        conn.execute(
            """
            UPDATE pipeline_jobs
            SET status='completed',current_station='product_signal_admission_station',
                output_ref='ART-BATCH'
            WHERE job_id=?
            """,
            (parent,),
        )
        conn.commit()
    for index in range(1, 31):
        envelope = build_item_envelope(
            data_version="DV-LIVE",
            product_id=f"P{index:03d}",
            store_id="S1",
            signal_id=f"SIG-{index:03d}",
            stage="observed_soft_gate",
        )
        upsert_pipeline_item(
            envelope,
            stage="observed_soft_gate",
            status="observed",
            payload={"source": "test"},
        )
    flow._ORIGINAL_PIPELINE_LIVE_READER = live.read_pipeline_live_model
    result = flow.read_pipeline_live_model_v226("DV-LIVE")
    signal_stage = next(item for item in result["productStages"] if item["node"] == "信号引擎")
    assert result["summary"]["totalItems"] == 30
    assert signal_stage["total"] == 30
    assert signal_stage["observed"] == 30
    assert len(result["batchStages"]) == 1
    assert result["mixedBatchAndProductCount"] is False
    assert result["batchTokenAddedToProductCount"] is False
    assert result["headline"] == "商品准入完成：30个观察，0个进入Agent1"
