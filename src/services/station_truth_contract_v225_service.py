"""V22.5.11 Station truth-contract compatibility verifier.

Older revisions of this module rewrote ``station_contract_service.DEFAULT_OUTPUTS``
at import time. That created a split runtime authority: the static TARGET contract
could require the current baseline/delta-neutral fields while the imported runtime
silently restored an older V22.2.5 shape. The binder is now verification-only.

The single governed contract authority is ``station_contract_service``. This module
may confirm that the required business-output fields are present, but it must never
mutate public Station contracts at import time.
"""
from __future__ import annotations

from typing import Any, Dict

STATION_TRUTH_CONTRACT_VERSION = "22.5.11"
_BOUND = False

_REQUIRED_OUTPUTS = {
    "full_product_bundle_station": {
        "productSignalPackageCount",
        "baselineProductBundleCount",
        "signalEligibility",
        "baselineGate",
        "contractValidation",
        "outputRef",
    },
    "bundle_validation_station": {
        "bundleCount",
        "baselineProductBundleCount",
        "validatedSignalCount",
        "validationStatus",
        "signalEligibility",
        "baselineGate",
        "contractValidation",
        "outputRef",
    },
    "product_signal_admission_station": {
        "businessOutputType",
        "validatedBundleArtifactRef",
        "signalEligibility",
        "baselineGate",
        "fullSignalCount",
        "generatedSignalCount",
        "qualifiedSignalCount",
        "candidateProductCount",
        "admittedSignalCount",
        "observedSignalCount",
        "agent1PendingItemCount",
        "outputRef",
    },
}


def _contract_status() -> Dict[str, Any]:
    from src.services import station_contract_service as contracts

    missing: Dict[str, list[str]] = {}
    for station_id, required in _REQUIRED_OUTPUTS.items():
        actual = set(contracts.DEFAULT_OUTPUTS.get(station_id) or [])
        absent = sorted(required - actual)
        if absent:
            missing[station_id] = absent
    return {
        "contractAuthority": "src.services.station_contract_service.DEFAULT_OUTPUTS",
        "importTimeMutationAllowed": False,
        "verified": not missing,
        "missing": missing,
        "businessOutputContracts": sorted(_REQUIRED_OUTPUTS),
    }


def bind_station_truth_contract() -> Dict[str, Any]:
    global _BOUND
    status = _contract_status()
    if not status["verified"]:
        details = ";".join(
            f"{station}:{','.join(fields)}"
            for station, fields in sorted(status["missing"].items())
        )
        raise RuntimeError(f"station_truth_contract_authority_mismatch:{details}")

    idempotent = _BOUND
    _BOUND = True
    return {
        "version": STATION_TRUTH_CONTRACT_VERSION,
        "bound": True,
        "idempotent": idempotent,
        **status,
        "runtimeReceiptAcceptedAsBusinessOutput": False,
        "missingBusinessArtifactFallbackAllowed": False,
    }


__all__ = ["STATION_TRUTH_CONTRACT_VERSION", "bind_station_truth_contract"]
