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


def _old_package(product_id: str = "P10001") -> dict:
    return {
        "signalId": f"SIG-{product_id}",
        "packageId": f"PKG-{product_id}",
        "productId": product_id,
        "storeId": "S1",
        "snapshotLayer": {
            "fieldSignals": [
                {
                    "metricCode": "roi",
                    "signalStrength": "medium",
                    "meaningfulChange": True,
                    "changeVsPrevious": -0.2,
                }
            ]
        },
        "crossValidation": {
            "sourceDataVersions": ["DV-OLD", "DV-NEW"],
            "sourceDatasets": ["products"],
            "sourceVersionCount": 2,
            "sourceDatasetCount": 1,
            "changedMetricCount": 1,
            "abnormalMetricCount": 1,
        },
    }


def test_old_product_signal_contract_is_upgraded_by_one_builder() -> None:
    from src.services.operating_evidence_contract_service import (
        normalize_product_signal_package,
        validate_product_signal_package,
    )

    package = normalize_product_signal_package(_old_package(), baseline_only=False)
    cross = package["crossValidation"]
    assert cross["version"] == "21.5.0"
    assert cross["decision"]["status"] == "passed"
    assert cross["decision"]["taskTriggerAllowed"] is True
    assert validate_product_signal_package(package, baseline_only=False)["ok"] is True


def test_full_bundle_station_accepts_legacy_shape_after_canonical_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import station_alignment_v225_service as alignment

    monkeypatch.setattr(
        alignment.signal_snapshot_service,
        "materialize_product_signal_snapshot",
        lambda **_: {
            "dataVersion": "DV-NEW",
            "baselineNoPrevious": False,
            "productSignalPackages": [_old_package("P1"), _old_package("P2")],
        },
    )
    monkeypatch.setattr(
        alignment,
        "is_first_report_baseline",
        lambda _dv: {"isFirstReportBaseline": False},
    )
    result = alignment.full_product_bundle_station("DV-NEW")
    assert result["productSignalPackageCount"] == 2
    assert result["contractValidation"]["invalidCount"] == 0
    assert all(
        item["crossValidation"]["decision"]["status"] == "passed"
        for item in result["productSignalPackages"]
    )


def test_failed_station_cannot_be_completed_or_advance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.services import station_queue_service as queue

    queued = queue.enqueue_task_generation("DV-FAIL")
    monkeypatch.setattr(
        queue,
        "run_station_contract",
        lambda *_args, **_kwargs: {
            "ok": False,
            "status": "failed",
            "adapterError": "real_adapter_failed",
            "output": {
                "dataVersion": "DV-FAIL",
                "stationId": "report_receive_station",
                "pipelineItemEnvelope": {},
            },
            "outputContract": {"status": "failed", "missing": ["rowCount"]},
        },
    )
    result = queue.run_next_station_job(worker_id="test")
    assert result["status"] == "retry"
    assert result["businessArtifactWritten"] is False
    assert result["nextStationCreated"] is False

    from src.repositories.sqlite_repository import connect

    with connect() as conn:
        rows = conn.execute(
            "SELECT station_id,status,output_ref,error_message FROM station_queue ORDER BY created_at"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "retry"
    assert rows[0]["output_ref"] is None
    assert "real_adapter_failed" in rows[0]["error_message"]
    assert queued["job"]["dataVersion"] == "DV-FAIL"


def test_successful_station_passes_real_artifact_ref_to_next_station(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.services import station_queue_service as queue
    from src.services.artifact_transport_service import resolve_artifact
    from src.repositories.sqlite_repository import connect, loads

    queue.enqueue_task_generation("DV-OK")
    monkeypatch.setattr(
        queue,
        "run_station_contract",
        lambda *_args, **_kwargs: {
            "ok": True,
            "status": "completed",
            "output": {
                "version": "22.2.5",
                "stationId": "report_receive_station",
                "dataVersion": "DV-OK",
                "rowCount": 30,
                "rawReportRef": "logical-only",
                "outputRef": "logical-only",
                "pipelineItemEnvelope": {"runtime": True},
            },
            "outputContract": {"status": "passed", "missing": []},
        },
    )
    result = queue.run_next_station_job(worker_id="test")
    assert result["status"] == "completed"
    assert result["outputRef"].startswith("ART-")
    assert result["runtimeReceiptStoredAsBusinessArtifact"] is False
    assert result["duplicateCompletedGateWritten"] is False

    artifact = resolve_artifact(result["outputRef"])
    assert artifact["rowCount"] == 30
    assert "output" not in artifact
    assert "pipelineItemEnvelope" not in artifact

    with connect() as conn:
        next_row = conn.execute(
            "SELECT * FROM station_queue WHERE station_id='report_schema_station' LIMIT 1"
        ).fetchone()
    assert next_row["input_ref"] == result["outputRef"]
    payload = loads(next_row["payload"])
    refs = payload["pipelineItemEnvelope"]["artifactRefs"]
    assert result["outputRef"] in refs.values()


def test_validated_artifact_fans_out_to_product_signal_refs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.services import artifact_signal_admission_v225_service as admission
    from src.services.artifact_transport_service import store_artifact
    from src.repositories.sqlite_repository import connect, loads

    validated = {
        "businessOutputType": "validated_product_signal_snapshot",
        "dataVersion": "DV-SIGNAL",
        "baselineNoPrevious": False,
        "validatedSignals": [_old_package("P1"), _old_package("P2")],
    }
    stored = store_artifact(
        artifact_type="pipeline_stage.quality_gate_ready",
        value=validated,
        data_version="DV-SIGNAL",
        created_by="test",
    )
    monkeypatch.setattr(
        admission,
        "score_signal",
        lambda _signal: {
            "score": 60,
            "level": "medium_candidate",
            "changedMetricCount": 1,
            "abnormalMetricCount": 1,
            "sourceVersionCount": 2,
            "reasons": ["test"],
        },
    )
    result = admission.product_signal_admission_station_v225(
        "DV-SIGNAL",
        validated_bundle_ref=stored["artifactId"],
        max_signals=10,
        max_admitted=10,
    )
    assert result["admittedSignalCount"] == 2
    assert result["legacySignalPoolRead"] is False

    with connect() as conn:
        rows = conn.execute(
            "SELECT current_stage,product_id,signal_id,artifact_refs_json FROM pipeline_items ORDER BY product_id"
        ).fetchall()
        signal_pool = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_pool_v14'"
        ).fetchone()
    assert signal_pool is None
    assert len(rows) == 2
    assert all(row["current_stage"] == "agent1_pending" for row in rows)
    assert all(str(loads(row["artifact_refs_json"])["signalRef"]).startswith("ART-") for row in rows)


def test_historical_outer_completed_inner_failed_is_replayed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.services import station_queue_service as queue
    from src.services.pipeline_gate_service import record_stage_gate
    from src.services.station_truth_repair_v225_service import repair_fake_completed_station_runs
    from src.repositories.sqlite_repository import connect, dumps

    queued = queue.enqueue_task_generation("DV-REPAIR")
    parent = queued["job"]["jobId"]
    with connect() as conn:
        first = conn.execute(
            "SELECT * FROM station_queue WHERE parent_job_id=? LIMIT 1", (parent,)
        ).fetchone()
        conn.execute(
            "UPDATE station_queue SET station_id='full_product_bundle_station',stage='full_product_bundle_ready',status='completed',payload=?,output_ref=? WHERE station_job_id=?",
            (
                dumps({"stationRun": {"status": "failed", "error": "contract mismatch"}}),
                "full_product_bundle_station:DV-REPAIR",
                first["station_job_id"],
            ),
        )
        queue._insert_station_job(
            conn,
            parent_job_id=parent,
            system_type="task_generation",
            station_id="bundle_validation_station",
            stage="bundle_validation_ready",
            data_version="DV-REPAIR",
            actor_user_id=None,
            input_ref="full_product_bundle_station:DV-REPAIR",
            payload={"dataVersion": "DV-REPAIR"},
        )
        conn.execute(
            "UPDATE station_queue SET status='completed',payload=? WHERE parent_job_id=? AND station_id='bundle_validation_station'",
            (dumps({"stationRun": {"status": "failed"}}), parent),
        )
        conn.commit()
    for stage, ref in (
        ("full_product_bundle_ready", "full_product_bundle_station:DV-REPAIR"),
        ("bundle_validation_ready", "bundle_validation_station:DV-REPAIR"),
    ):
        record_stage_gate(
            data_version="DV-REPAIR",
            stage=stage,
            status="completed",
            input_payload={},
            output_payload={"stationId": stage},
            output_ref=ref,
        )

    result = repair_fake_completed_station_runs()
    assert result["repairedJobCount"] == 1
    assert result["deletedFalseCompletedGateCount"] == 2
    assert result["allDownstreamFalseGatesRemoved"] is True
    with connect() as conn:
        first_status = conn.execute(
            "SELECT status FROM station_queue WHERE parent_job_id=? AND station_id='full_product_bundle_station'",
            (parent,),
        ).fetchone()["status"]
        downstream_status = conn.execute(
            "SELECT status FROM station_queue WHERE parent_job_id=? AND station_id='bundle_validation_station'",
            (parent,),
        ).fetchone()["status"]
    assert first_status == "retry"
    assert downstream_status == "disabled"


def test_pipeline_live_reports_batch_retry_instead_of_interface_loading(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.services import station_queue_service as queue
    from src.services.pipeline_live_read_model_v208_service import read_pipeline_live_model
    from src.repositories.sqlite_repository import connect

    queued = queue.enqueue_task_generation("DV-LIVE")
    parent = queued["job"]["jobId"]
    with connect() as conn:
        conn.execute(
            """
            UPDATE station_queue
            SET station_id='full_product_bundle_station', stage='full_product_bundle_ready',
                status='retry', error_message='证据合同重试', attempt_count=1
            WHERE parent_job_id=?
            """,
            (parent,),
        )
        conn.execute(
            """
            UPDATE pipeline_jobs
            SET status='running',current_station='full_product_bundle_station',
                error_message='从真实断点重试'
            WHERE job_id=?
            """,
            (parent,),
        )
        conn.commit()

    result = read_pipeline_live_model("DV-LIVE")
    assert result["ready"] is True
    assert result["interfaceStatus"] == "ok"
    assert result["snapshotStatus"] == "replaying"
    assert result["batchState"]["status"] == "retry"
    assert "重试" in result["headline"]
    assert result["payloadRead"] is False
    assert result["summary"]["totalItems"] == 0
    assert result["summary"]["batchCount"] == 1


def test_pipeline_live_counts_task_admission_without_payload_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_runtime(monkeypatch, tmp_path)
    from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item
    from src.services.pipeline_live_read_model_v208_service import read_pipeline_live_model

    envelope = build_item_envelope(
        data_version="DV-TASK",
        product_id="P1",
        store_id="S1",
        task_id="T1",
        stage="task_admitted",
    )
    upsert_pipeline_item(
        envelope,
        stage="task_admitted",
        status="completed",
        payload={"taskAdmission": {"ok": False, "status": "legacy_payload_must_be_ignored"}},
    )
    result = read_pipeline_live_model("DV-TASK")
    assert result["ready"] is True
    assert result["summary"]["taskAdmitted"] == 1
    assert result["summary"]["totalItems"] == 1
    assert result["payloadRead"] is False
