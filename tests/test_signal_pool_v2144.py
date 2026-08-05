from __future__ import annotations

from pathlib import Path

from src.repositories import sqlite_repository
from src.repositories.sqlite_repository import connect, dumps
from src.services.product_signal_admission_v197_service import score_signal
from src.services import product_signal_snapshot_service, signal_pool_service


def _signal(data_version: str) -> dict:
    return {
        "signalId": "PSIG-CONTENT-STABLE",
        "dataVersion": data_version,
        "entityType": "product",
        "entityId": "PRODUCT-1",
        "productId": "PRODUCT-1",
        "storeId": "STORE-1",
        "signalType": "full_product_bundle",
        "signalStrength": "medium",
        "snapshotLayer": {
            "fieldSignals": [
                {
                    "metricCode": "clickRate",
                    "signalStrength": "medium",
                    "previous": 2.0,
                    "latest": 1.5,
                    "changeVsPrevious": -0.25,
                    "meaningfulChange": True,
                }
            ]
        },
        "crossValidation": {
            "changedMetricCount": 1,
            "abnormalMetricCount": 1,
            "sourceVersionCount": 2,
        },
        "status": "pending_agent_judgment",
    }


def _v215_signal(data_version: str) -> dict:
    signal = _signal(data_version)
    signal["signalStrength"] = "high"
    signal["crossValidation"] = {
        "version": "21.5.0",
        "contract": "operatingEvidenceGraph.v1",
        "changedMetricCount": 4,
        "abnormalMetricCount": 3,
        "sourceVersionCount": 2,
        "changedMetrics": [
            "roi",
            "adSpend",
            "conversionRate",
            "paymentAmount",
        ],
        "decision": {
            "hypothesisCode": "paid_efficiency_decline",
            "hypothesisLabel": "投放效率恶化",
            "status": "confirmed",
            "severity": 82,
            "confidence": 80,
            "businessImpact": 82,
            "urgency": 82,
            "actionIntensity": "L4",
            "independentEvidenceGroups": [
                "efficiency",
                "spend",
                "conversion",
            ],
            "conflictEvidenceGroups": [],
        },
        "timeSeriesFeatures": {
            "roi": {
                "previousDelta": -0.25,
                "slope5": -0.15,
                "sampleCount": 2,
            }
        },
    }
    signal["operatingDecision"] = signal["crossValidation"]["decision"]
    signal["timeSeriesFeatures"] = signal["crossValidation"][
        "timeSeriesFeatures"
    ]
    return signal


def _snapshot(data_version: str) -> dict:
    signal = _v215_signal(data_version)
    return {
        "version": "21.5.0",
        "signalSnapshotId": f"PRODUCT-SIGNAL-SNAPSHOT-{data_version}",
        "dataVersion": data_version,
        "productSnapshotCount": 1,
        "productSignalPackageCount": 1,
        "productSignalCount": 1,
        "baselineNoPrevious": False,
        "signals": [signal],
        "productSignalPackages": [signal],
        "productSignalSnapshotRef": (
            f"product_signal_snapshot:PRODUCT-SIGNAL-SNAPSHOT-{data_version}"
        ),
    }


def _use_temp_db(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        sqlite_repository,
        "DB_PATH",
        tmp_path / "signal-pool.sqlite3",
    )


def test_signal_pool_preserves_payload_for_admission_scoring(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    signal_pool_service.ensure_signal_pool_tables()
    saved = signal_pool_service._save_signal(_signal("DV-A"))
    listed = signal_pool_service.list_signals(data_version="DV-A")

    assert saved["signalId"].startswith("PSIGV-")
    assert listed["signalCount"] == 1

    hydrated = listed["signals"][0]
    assert hydrated["payload"]["snapshotLayer"]["fieldSignals"]
    assert hydrated["payload"]["crossValidation"]["changedMetricCount"] == 1

    score = score_signal(hydrated)
    assert score["changedMetricCount"] == 1
    assert score["sourceVersionCount"] == 2
    assert score["score"] >= 45
    assert score["level"] in {"medium_candidate", "strong_candidate"}


def test_same_content_signal_id_is_isolated_by_data_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_temp_db(tmp_path, monkeypatch)

    signal_pool_service.ensure_signal_pool_tables()
    first = signal_pool_service._save_signal(_signal("DV-A"))
    second = signal_pool_service._save_signal(_signal("DV-B"))

    assert first["sourceSignalId"] == second["sourceSignalId"]
    assert first["signalId"] != second["signalId"]
    assert signal_pool_service.list_signals(data_version="DV-A")["signalCount"] == 1
    assert signal_pool_service.list_signals(data_version="DV-B")["signalCount"] == 1


def test_generate_signal_pool_consumes_persisted_v215_snapshot_without_rematerializing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    data_version = "DV-V215-PERSISTED"
    snapshot = _snapshot(data_version)

    product_signal_snapshot_service.ensure_product_signal_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO product_signal_snapshots_v14 (
                signal_snapshot_id,
                data_version,
                product_snapshot_id,
                previous_snapshot_id,
                signal_count,
                payload,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["signalSnapshotId"],
                data_version,
                "PRODUCT-SNAPSHOT-CURRENT",
                "PRODUCT-SNAPSHOT-PREVIOUS",
                1,
                dumps(snapshot),
                "2026-07-14T12:00:00",
                "2026-07-14T12:00:00",
            ),
        )
        conn.commit()

    def fail_if_rematerialized(*_args, **_kwargs):
        raise AssertionError(
            "Signal Pool must consume the persisted enriched snapshot"
        )

    monkeypatch.setattr(
        product_signal_snapshot_service,
        "materialize_product_signal_snapshot",
        fail_if_rematerialized,
    )

    generated = signal_pool_service.generate_signal_pool(
        data_version=data_version,
        max_signals=10,
    )

    assert generated["signalSnapshotSource"] == "persisted_enriched_snapshot"
    assert generated["snapshotRematerialized"] is False
    assert generated["signalCount"] == 1

    listed = signal_pool_service.list_signals(data_version=data_version)
    assert listed["signalCount"] == 1
    hydrated = listed["signals"][0]

    assert hydrated["signalId"].startswith("PSIGV-")
    assert hydrated["payload"]["crossValidation"]["version"] == "21.5.0"
    assert hydrated["payload"]["operatingDecision"]["status"] == "confirmed"
    assert hydrated["payload"]["timeSeriesFeatures"]["roi"]["slope5"] < 0

    score = score_signal(hydrated)
    assert score["validationStatus"] == "confirmed"
    assert score["confidence"] == 80
    assert score["level"] in {"medium_candidate", "strong_candidate"}


def test_generate_signal_pool_accepts_admission_snapshot_as_single_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _use_temp_db(tmp_path, monkeypatch)
    data_version = "DV-V215-PROVIDED"
    snapshot = _snapshot(data_version)

    def fail_if_materialized(*_args, **_kwargs):
        raise AssertionError(
            "A provided admission snapshot must never be rematerialized"
        )

    monkeypatch.setattr(
        product_signal_snapshot_service,
        "materialize_product_signal_snapshot",
        fail_if_materialized,
    )

    generated = signal_pool_service.generate_signal_pool(
        data_version=data_version,
        max_signals=10,
        signal_snapshot=snapshot,
    )

    assert generated["signalSnapshotSource"] == "provided_by_admission"
    assert generated["snapshotRematerialized"] is False
    assert generated["signalCount"] == 1
    assert (
        generated["productSignalSnapshot"]["productSignalPackages"][0]
        ["crossValidation"]["version"]
        == "21.5.0"
    )
