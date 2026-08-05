"""Bind V22.2.5 business-output contracts into the single station runtime."""
from __future__ import annotations

from typing import Any, Dict

STATION_TRUTH_CONTRACT_VERSION = "22.2.5"
_BOUND = False


def bind_station_truth_contract() -> Dict[str, Any]:
    global _BOUND
    if _BOUND:
        return {
            "version": STATION_TRUTH_CONTRACT_VERSION,
            "bound": True,
            "idempotent": True,
        }

    from src.services import station_contract_service as contracts

    contracts.DEFAULT_OUTPUTS["full_product_bundle_station"] = [
        "businessOutputType",
        "productSignalPackageCount",
        "productSignalPackages",
        "contractValidation",
    ]
    contracts.DEFAULT_OUTPUTS["bundle_validation_station"] = [
        "businessOutputType",
        "bundleCount",
        "validationStatus",
        "validatedSignals",
        "contractValidation",
    ]
    contracts.DEFAULT_OUTPUTS["product_signal_admission_station"] = [
        "businessOutputType",
        "fullSignalCount",
        "qualifiedSignalCount",
        "admittedSignalCount",
        "observedSignalCount",
        "agent1PendingItemCount",
    ]
    contracts.STATION_CONTRACT_VERSION = STATION_TRUTH_CONTRACT_VERSION
    contracts.STATION_ADAPTER_VERSION = STATION_TRUTH_CONTRACT_VERSION
    _BOUND = True
    return {
        "version": STATION_TRUTH_CONTRACT_VERSION,
        "bound": True,
        "idempotent": False,
        "businessOutputContracts": [
            "full_product_bundle_station",
            "bundle_validation_station",
            "product_signal_admission_station",
        ],
        "runtimeReceiptAcceptedAsBusinessOutput": False,
        "missingBusinessArtifactFallbackAllowed": False,
    }


__all__ = ["STATION_TRUTH_CONTRACT_VERSION", "bind_station_truth_contract"]
