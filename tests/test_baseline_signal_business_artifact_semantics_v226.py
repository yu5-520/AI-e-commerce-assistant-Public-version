from __future__ import annotations

from src.services.station_business_artifact_service import validate_business_output


def _contract_validation(count: int) -> dict:
    return {
        "ok": True,
        "status": "passed",
        "packageCount": count,
        "invalidCount": 0,
    }


def test_first_report_full_bundle_is_valid_evidence_with_zero_signal() -> None:
    bundles = [{"productId": f"P{i}", "productSnapshotHash": f"sha256:{i:064x}"} for i in range(30)]
    result = validate_business_output(
        "full_product_bundle_station",
        {
            "businessOutputType": "baseline_product_bundle",
            "baselineProductBundleCount": 30,
            "baselineProductBundles": bundles,
            "productSignalPackageCount": 0,
            "productSignalPackages": [],
            "signalEligibility": False,
            "baselineGate": "closed_before_signal_engine",
            "contractValidation": _contract_validation(30),
        },
    )
    assert result["ok"] is True
    assert result["missing"] == []


def test_first_report_validation_is_valid_with_zero_validated_signal() -> None:
    bundles = [{"productId": f"P{i}", "productSnapshotHash": f"sha256:{i:064x}"} for i in range(30)]
    result = validate_business_output(
        "bundle_validation_station",
        {
            "businessOutputType": "validated_baseline_product_bundle",
            "bundleCount": 30,
            "baselineProductBundleCount": 30,
            "baselineProductBundles": bundles,
            "validatedSignalCount": 0,
            "validatedSignals": [],
            "validationStatus": "passed",
            "signalEligibility": False,
            "baselineGate": "closed_before_signal_engine",
            "contractValidation": _contract_validation(30),
        },
    )
    assert result["ok"] is True
    assert result["missing"] == []


def test_first_report_admission_closes_before_signal_and_agent1() -> None:
    result = validate_business_output(
        "product_signal_admission_station",
        {
            "businessOutputType": "baseline_history_gate_closed",
            "baselineProductBundleCount": 30,
            "signalEligibility": False,
            "baselineGate": "closed_before_signal_engine",
            "fullSignalCount": 0,
            "generatedSignalCount": 0,
            "qualifiedSignalCount": 0,
            "candidateProductCount": 0,
            "admittedSignalCount": 0,
            "observedSignalCount": 0,
            "agent1PendingItemCount": 0,
        },
    )
    assert result["ok"] is True
    assert result["missing"] == []


def test_comparable_delta_does_not_invent_positive_signal_minimum() -> None:
    full = validate_business_output(
        "full_product_bundle_station",
        {
            "businessOutputType": "full_product_signal_snapshot",
            "baselineProductBundleCount": 0,
            "baselineProductBundles": [],
            "productSignalPackageCount": 0,
            "productSignalPackages": [],
            "signalEligibility": True,
            "baselineGate": "open_after_previous_snapshot",
            "contractValidation": _contract_validation(0),
        },
    )
    validated = validate_business_output(
        "bundle_validation_station",
        {
            "businessOutputType": "validated_product_signal_snapshot",
            "bundleCount": 0,
            "baselineProductBundleCount": 0,
            "baselineProductBundles": [],
            "validatedSignalCount": 0,
            "validatedSignals": [],
            "validationStatus": "waiting",
            "signalEligibility": True,
            "baselineGate": "open_after_previous_snapshot",
            "contractValidation": _contract_validation(0),
        },
    )
    admission = validate_business_output(
        "product_signal_admission_station",
        {
            "businessOutputType": "artifact_signal_admission",
            "signalEligibility": True,
            "baselineGate": "open_after_previous_snapshot",
            "fullSignalCount": 0,
            "generatedSignalCount": 0,
            "qualifiedSignalCount": 0,
            "candidateProductCount": 0,
            "admittedSignalCount": 0,
            "observedSignalCount": 0,
            "agent1PendingItemCount": 0,
        },
    )
    assert full["ok"] is True
    assert validated["ok"] is True
    assert admission["ok"] is True


def test_baseline_semantics_fail_closed_if_signal_leaks_through_gate() -> None:
    result = validate_business_output(
        "product_signal_admission_station",
        {
            "businessOutputType": "baseline_history_gate_closed",
            "baselineProductBundleCount": 30,
            "signalEligibility": False,
            "baselineGate": "closed_before_signal_engine",
            "fullSignalCount": 1,
            "generatedSignalCount": 1,
            "qualifiedSignalCount": 0,
            "candidateProductCount": 0,
            "admittedSignalCount": 0,
            "observedSignalCount": 0,
            "agent1PendingItemCount": 0,
        },
    )
    assert result["ok"] is False
    assert "baseline.fullSignalCount=0" in result["missing"]
    assert "baseline.generatedSignalCount=0" in result["missing"]


def test_wrong_business_output_mode_cannot_cross_artifact_boundary() -> None:
    result = validate_business_output(
        "full_product_bundle_station",
        {
            "businessOutputType": "legacy_signal_only_bundle",
            "productSignalPackageCount": 30,
            "productSignalPackages": [{}] * 30,
            "signalEligibility": True,
            "baselineGate": "open_after_previous_snapshot",
            "contractValidation": _contract_validation(30),
        },
    )
    assert result["ok"] is False
    assert any("businessOutputType in" in item for item in result["missing"])
