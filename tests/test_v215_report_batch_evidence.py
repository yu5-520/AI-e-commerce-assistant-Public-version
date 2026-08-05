from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from src.repositories import sqlite_repository
from src.repositories.sqlite_repository import connect, dumps
from src.services import product_signal_snapshot_service, signal_pool_service
from src.services.product_signal_admission_v197_service import score_signal
from src.services.v215_report_batch_evidence_service import (
    V215_VERSION,
    build_cross_validation,
    build_time_series_features,
    score_cross_validated_signal,
)


def _product(date: str, **metrics: float) -> dict:
    metric = {"metricDate": date, **metrics}
    return {
        "objectId": "pdd::S1::P10001::NO-SKU",
        "productId": "P10001",
        "storeId": "S1",
        "metricDate": date,
        "metricSnapshot": metric,
    }


def test_multi_window_features_are_deterministic() -> None:
    history = [
        _product("2026-07-04", roi=1.45, adSpend=110, paidVisitors=900, conversionRate=0.020, paymentAmount=2200),
        _product("2026-07-03", roi=1.60, adSpend=100, paidVisitors=820, conversionRate=0.023, paymentAmount=2400),
        _product("2026-07-02", roi=1.75, adSpend=90, paidVisitors=760, conversionRate=0.026, paymentAmount=2600),
        _product("2026-07-01", roi=1.90, adSpend=80, paidVisitors=700, conversionRate=0.029, paymentAmount=2800),
    ]
    current = _product(
        "2026-07-05",
        roi=1.10,
        adSpend=130,
        paidVisitors=1050,
        conversionRate=0.014,
        paymentAmount=1700,
    )

    first = build_time_series_features(current, history)
    second = build_time_series_features(current, history)

    assert first == second
    assert first["roi"]["previousDelta"] < 0
    assert first["roi"]["slope5"] < 0
    assert first["adSpend"]["slope5"] > 0
    assert first["conversionRate"]["streakDirection"] == "down"
    assert first["conversionRate"]["streakLength"] >= 4


def test_linked_evidence_confirms_paid_efficiency_decline() -> None:
    history = [
        _product("2026-07-04", roi=1.45, adSpend=110, paidVisitors=900, conversionRate=0.020, paymentAmount=2200, grossMargin=0.28),
        _product("2026-07-03", roi=1.60, adSpend=100, paidVisitors=820, conversionRate=0.023, paymentAmount=2400, grossMargin=0.30),
        _product("2026-07-02", roi=1.75, adSpend=90, paidVisitors=760, conversionRate=0.026, paymentAmount=2600, grossMargin=0.32),
        _product("2026-07-01", roi=1.90, adSpend=80, paidVisitors=700, conversionRate=0.029, paymentAmount=2800, grossMargin=0.34),
    ]
    current = _product(
        "2026-07-05",
        roi=1.10,
        adSpend=130,
        paidVisitors=1050,
        conversionRate=0.014,
        paymentAmount=1700,
        grossMargin=0.24,
    )

    cross = build_cross_validation(current, history)
    paid = next(
        item
        for item in cross["hypotheses"]
        if item["hypothesisCode"] == "paid_efficiency_decline"
    )
    decision = cross["decision"]

    assert cross["version"] == V215_VERSION
    assert cross["sourceVersionScoreContribution"] == 0
    assert paid["status"] == "confirmed"
    assert "efficiency" in paid["independentEvidenceGroups"]
    assert len(set(paid["independentEvidenceGroups"])) >= 3
    assert paid["confidence"] >= 55
    assert decision["status"] == "confirmed"
    assert decision["hypothesisCode"] in {
        "paid_efficiency_decline",
        "conversion_decline",
    }


def test_source_version_count_does_not_change_admission_score() -> None:
    cross = {
        "version": V215_VERSION,
        "changedMetricCount": 4,
        "abnormalMetricCount": 3,
        "sourceVersionCount": 2,
        "decision": {
            "hypothesisCode": "paid_efficiency_decline",
            "status": "confirmed",
            "severity": 78,
            "confidence": 82,
            "businessImpact": 82,
            "urgency": 80,
            "actionIntensity": "L3",
            "independentEvidenceGroups": ["efficiency", "spend", "conversion"],
            "conflictEvidenceGroups": [],
        },
    }
    signal = {"payload": {"crossValidation": cross}}
    fallback = lambda _signal: {"score": 1, "level": "noise_or_baseline"}

    first = score_cross_validated_signal(signal, fallback)
    mutated = deepcopy(signal)
    mutated["payload"]["crossValidation"]["sourceVersionCount"] = 30
    second = score_cross_validated_signal(mutated, fallback)

    assert first["score"] == second["score"]
    assert first["level"] == second["level"]
    assert first["level"] in {"medium_candidate", "strong_candidate"}


def test_single_unlinked_delta_stays_observation() -> None:
    history = [_product("2026-07-01", roi=1.50, adSpend=100, paymentAmount=2000)]
    current = _product("2026-07-02", roi=1.20, adSpend=100, paymentAmount=2000)

    cross = build_cross_validation(current, history)
    decision = cross["decision"]
    scored = score_cross_validated_signal(
        {"payload": {"crossValidation": cross}},
        lambda _signal: {"score": 99, "level": "strong_candidate"},
    )

    assert decision["status"] == "insufficient_evidence"
    assert scored["level"] in {"weak_observation", "noise_or_baseline"}
    assert scored["score"] < 45


def test_signal_pool_does_not_overwrite_enriched_v215_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        sqlite_repository,
        "DB_PATH",
        tmp_path / "v215-snapshot-handoff.sqlite3",
    )
    data_version = "DV-V215-HANDOFF"
    cross = {
        "version": V215_VERSION,
        "contract": "operatingEvidenceGraph.v1",
        "changedMetricCount": 4,
        "abnormalMetricCount": 3,
        "sourceVersionCount": 2,
        "changedMetrics": ["roi", "adSpend", "conversionRate", "paymentAmount"],
        "decision": {
            "hypothesisCode": "paid_efficiency_decline",
            "hypothesisLabel": "投放效率恶化",
            "status": "confirmed",
            "severity": 82,
            "confidence": 80,
            "businessImpact": 82,
            "urgency": 82,
            "actionIntensity": "L4",
            "independentEvidenceGroups": ["efficiency", "spend", "conversion"],
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
    package = {
        "signalId": "PSIG-V215-HANDOFF",
        "dataVersion": data_version,
        "entityType": "product",
        "entityId": "PRODUCT-1",
        "productId": "PRODUCT-1",
        "storeId": "STORE-1",
        "signalType": "full_product_bundle",
        "signalStrength": "high",
        "crossValidation": cross,
        "operatingDecision": cross["decision"],
        "timeSeriesFeatures": cross["timeSeriesFeatures"],
        "status": "pending_agent_judgment",
    }
    snapshot = {
        "version": V215_VERSION,
        "signalSnapshotId": f"PRODUCT-SIGNAL-SNAPSHOT-{data_version}",
        "dataVersion": data_version,
        "productSnapshotCount": 1,
        "productSignalPackageCount": 1,
        "productSignalCount": 1,
        "baselineNoPrevious": False,
        "signals": [package],
        "productSignalPackages": [package],
    }

    product_signal_snapshot_service.ensure_product_signal_tables()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO product_signal_snapshots_v14 (
                signal_snapshot_id,data_version,product_snapshot_id,
                previous_snapshot_id,signal_count,payload,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?)
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
            "Signal Pool must consume the persisted V21.5 snapshot"
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

    hydrated = signal_pool_service.list_signals(
        data_version=data_version
    )["signals"][0]
    assert hydrated["signalId"].startswith("PSIGV-")
    assert hydrated["payload"]["crossValidation"]["version"] == V215_VERSION
    assert hydrated["payload"]["operatingDecision"]["status"] == "confirmed"

    scored = score_signal(hydrated)
    assert scored["validationStatus"] == "confirmed"
    assert scored["confidence"] == 80
    assert scored["level"] in {"medium_candidate", "strong_candidate"}
