from __future__ import annotations

from pathlib import Path

import pytest

from src.services import product_signal_snapshot_service
from src.services import signal_pool_service
from src.services import station_alignment_v165_service as alignment

ROOT = Path(__file__).resolve().parents[1]


def _valid_package() -> dict:
    return {
        "signalId": "PSIG-SOURCE-1",
        "dataVersion": "DV-V2152",
        "entityType": "product",
        "entityId": "PRODUCT-1",
        "productId": "PRODUCT-1",
        "storeId": "STORE-1",
        "signalType": "full_product_bundle",
        "signalStrength": "high",
        "crossValidation": {
            "version": "21.5.0",
            "contract": "operatingEvidenceGraph.v1",
            "changedMetricCount": 4,
            "decision": {
                "hypothesisCode": "paid_efficiency_decline",
                "status": "confirmed",
                "confidence": 80,
                "severity": 82,
            },
        },
        "operatingDecision": {
            "hypothesisCode": "paid_efficiency_decline",
            "status": "confirmed",
            "confidence": 80,
        },
    }


def _valid_snapshot() -> dict:
    package = _valid_package()
    return {
        "version": "21.5.0",
        "dataVersion": "DV-V2152",
        "baselineNoPrevious": False,
        "productSignalCount": 1,
        "productSignalPackageCount": 1,
        "productSignalSnapshotRef": "product_signal_snapshot:DV-V2152",
        "signals": [package],
        "productSignalPackages": [package],
    }


def test_legacy_v186_runtime_wrapper_is_deleted() -> None:
    assert not (
        ROOT / "src/services/product_signal_snapshot_v164_service.py"
    ).exists()
    source = (
        ROOT / "src/services/station_alignment_v165_service.py"
    ).read_text(encoding="utf-8")
    assert "product_signal_snapshot_v164_service" not in source
    assert "signal_snapshot_service.materialize_product_signal_snapshot" in source


def test_station_alignment_uses_active_signal_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _valid_snapshot()
    calls: list[dict] = []

    def materialize(**kwargs):
        calls.append(kwargs)
        return snapshot

    monkeypatch.setattr(
        product_signal_snapshot_service,
        "materialize_product_signal_snapshot",
        materialize,
    )
    monkeypatch.setattr(
        alignment,
        "is_first_report_baseline",
        lambda _data_version: {"isFirstReportBaseline": False},
    )

    result = alignment.full_product_bundle_station(
        "DV-V2152",
        user_id="U1",
        force=True,
    )

    assert calls == [
        {"data_version": "DV-V2152", "user_id": "U1", "force": True}
    ]
    assert result["evidenceVersion"] == "21.5.0"
    assert result["result"]["productSignalPackages"][0]["crossValidation"][
        "version"
    ] == "21.5.0"


def test_station_alignment_rejects_legacy_non_baseline_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy = _valid_snapshot()
    legacy["version"] = "18.6"
    legacy["productSignalPackages"][0].pop("crossValidation")
    legacy["signals"] = legacy["productSignalPackages"]

    monkeypatch.setattr(
        product_signal_snapshot_service,
        "materialize_product_signal_snapshot",
        lambda **_kwargs: legacy,
    )
    monkeypatch.setattr(
        alignment,
        "is_first_report_baseline",
        lambda _data_version: {"isFirstReportBaseline": False},
    )

    with pytest.raises(RuntimeError, match="full_product_bundle_contract_invalid_v21_5"):
        alignment.full_product_bundle_station("DV-V2152")


def test_signal_pool_accepts_only_cross_validated_snapshot() -> None:
    snapshot = _valid_snapshot()
    result = signal_pool_service._validate_snapshot_contract(
        snapshot,
        snapshot["productSignalPackages"],
        data_version="DV-V2152",
        snapshot_source="provided_by_admission",
    )
    assert result == {
        "ok": True,
        "contract": "operatingEvidenceGraph.v1",
        "version": "21.5.0",
        "validatedSignalCount": 1,
    }


def test_signal_pool_fails_closed_on_missing_cross_validation() -> None:
    snapshot = _valid_snapshot()
    snapshot["productSignalPackages"][0].pop("crossValidation")
    snapshot["signals"] = snapshot["productSignalPackages"]

    with pytest.raises(RuntimeError, match="signal_snapshot_contract_invalid_v21_5"):
        signal_pool_service._validate_snapshot_contract(
            snapshot,
            snapshot["productSignalPackages"],
            data_version="DV-V2152",
            snapshot_source="persisted_enriched_snapshot",
        )


def test_signal_pool_never_materializes_a_missing_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        product_signal_snapshot_service,
        "get_product_signal_snapshot",
        lambda _data_version: {},
    )

    with pytest.raises(RuntimeError, match="signal_snapshot_missing_before_signal_pool"):
        signal_pool_service._resolve_signal_snapshot(
            "DV-MISSING",
            signal_snapshot=None,
        )
