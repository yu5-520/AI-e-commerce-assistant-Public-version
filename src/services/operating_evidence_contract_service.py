"""Canonical operating-evidence contract for product signal packages.

The signal producer and the station validator must use the same builder and the
same validator. This removes the V18.6-producer/V21.5-validator split that made
all product bundles invalid even when their business data was present.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

OPERATING_EVIDENCE_CONTRACT_VERSION = "21.5.0"
ALLOWED_EVIDENCE_STATUSES = {"passed", "attention", "baseline"}


def _values(values: Iterable[Any] | None) -> List[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in (values or [])
            if value not in (None, "", "—", "未识别")
        )
    )


def build_cross_validation_contract(
    *,
    source_data_versions: Iterable[Any] | None = None,
    source_datasets: Iterable[Any] | None = None,
    field_signals: Iterable[Dict[str, Any]] | None = None,
    baseline_only: bool = False,
    reason: str | None = None,
) -> Dict[str, Any]:
    signals = [item for item in (field_signals or []) if isinstance(item, dict)]
    changed = [item for item in signals if bool(item.get("meaningfulChange"))]
    abnormal = [
        item
        for item in changed
        if str(item.get("signalStrength") or "").lower() in {"high", "medium"}
    ]
    versions = _values(source_data_versions)
    datasets = _values(source_datasets)

    if baseline_only:
        status = "baseline"
        default_reason = "No comparable previous business report; evidence is stored as baseline only."
    elif versions or datasets:
        status = "passed"
        default_reason = "Evidence source identity and metric deltas are structurally available."
    else:
        status = "attention"
        default_reason = "Metric evidence exists but source identity is incomplete; downstream may observe but must not invent evidence."

    decision = {
        "status": status,
        "reason": reason or default_reason,
        "baselineOnly": bool(baseline_only),
        "taskTriggerAllowed": bool(not baseline_only and changed),
        "changedMetricCount": len(changed),
        "abnormalMetricCount": len(abnormal),
        "sourceVersionCount": len(versions),
        "sourceDatasetCount": len(datasets),
    }
    return {
        "version": OPERATING_EVIDENCE_CONTRACT_VERSION,
        "contract": "operatingEvidenceGraph.v1",
        "sourceDataVersions": versions,
        "sourceDatasets": datasets,
        "sourceVersionCount": len(versions),
        "sourceDatasetCount": len(datasets),
        "changedMetricCount": len(changed),
        "abnormalMetricCount": len(abnormal),
        "topAbnormalMetrics": abnormal[:5],
        "decision": decision,
        "rule": "One evidence builder owns both product-signal production and station validation.",
    }


def normalize_cross_validation(
    value: Any,
    *,
    field_signals: Iterable[Dict[str, Any]] | None = None,
    baseline_only: bool = False,
) -> Dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    normalized = build_cross_validation_contract(
        source_data_versions=source.get("sourceDataVersions") or [],
        source_datasets=source.get("sourceDatasets") or [],
        field_signals=field_signals,
        baseline_only=baseline_only,
        reason=(source.get("decision") or {}).get("reason")
        if isinstance(source.get("decision"), dict)
        else None,
    )
    for key in ("sourceVersionCount", "sourceDatasetCount", "changedMetricCount", "abnormalMetricCount"):
        if source.get(key) is not None:
            try:
                normalized[key] = int(source.get(key))
            except Exception:
                pass
    normalized["topAbnormalMetrics"] = (
        source.get("topAbnormalMetrics")
        if isinstance(source.get("topAbnormalMetrics"), list)
        else normalized.get("topAbnormalMetrics")
    )
    decision = dict(normalized.get("decision") or {})
    existing_decision = source.get("decision") if isinstance(source.get("decision"), dict) else {}
    if existing_decision.get("status") in ALLOWED_EVIDENCE_STATUSES:
        decision.update(existing_decision)
    decision["status"] = (
        "baseline"
        if baseline_only
        else decision.get("status")
        if decision.get("status") in ALLOWED_EVIDENCE_STATUSES
        else "attention"
    )
    decision["baselineOnly"] = bool(baseline_only)
    normalized["decision"] = decision
    return normalized


def normalize_product_signal_package(
    package: Dict[str, Any],
    *,
    baseline_only: bool = False,
) -> Dict[str, Any]:
    result = dict(package or {})
    snapshot = result.get("snapshotLayer") if isinstance(result.get("snapshotLayer"), dict) else {}
    field_signals = snapshot.get("fieldSignals") or result.get("fieldSignals") or []
    result["crossValidation"] = normalize_cross_validation(
        result.get("crossValidation"),
        field_signals=field_signals if isinstance(field_signals, list) else [],
        baseline_only=baseline_only,
    )
    agent_package = (
        dict(result.get("agentProductSnapshotPackage"))
        if isinstance(result.get("agentProductSnapshotPackage"), dict)
        else {}
    )
    agent_package["crossValidation"] = result["crossValidation"]
    result["agentProductSnapshotPackage"] = agent_package
    result["evidenceContract"] = "operatingEvidenceGraph.v1"
    result["evidenceVersion"] = OPERATING_EVIDENCE_CONTRACT_VERSION
    return result


def validate_product_signal_package(
    package: Dict[str, Any],
    *,
    baseline_only: bool = False,
) -> Dict[str, Any]:
    missing: List[str] = []
    if not isinstance(package, dict):
        return {"ok": False, "missing": ["package"], "status": "failed"}
    if not (package.get("productId") or package.get("entityId")):
        missing.append("productId")
    if not package.get("signalId"):
        missing.append("signalId")
    cross = package.get("crossValidation") if isinstance(package.get("crossValidation"), dict) else {}
    decision = cross.get("decision") if isinstance(cross.get("decision"), dict) else {}
    if cross.get("version") != OPERATING_EVIDENCE_CONTRACT_VERSION:
        missing.append("crossValidation.version")
    if decision.get("status") not in ALLOWED_EVIDENCE_STATUSES:
        missing.append("crossValidation.decision.status")
    if baseline_only and decision.get("status") != "baseline":
        missing.append("crossValidation.decision.status=baseline")
    if not baseline_only and decision.get("status") == "baseline":
        missing.append("crossValidation.decision.status!=baseline")
    return {
        "ok": not missing,
        "status": "passed" if not missing else "failed",
        "missing": missing,
        "productId": package.get("productId") or package.get("entityId"),
        "signalId": package.get("signalId"),
        "evidenceStatus": decision.get("status"),
        "version": OPERATING_EVIDENCE_CONTRACT_VERSION,
    }


def normalize_signal_snapshot(snapshot: Dict[str, Any], *, baseline_only: bool) -> Dict[str, Any]:
    result = dict(snapshot or {})
    packages = result.get("productSignalPackages") or result.get("signals") or []
    normalized = [
        normalize_product_signal_package(item, baseline_only=baseline_only)
        for item in packages
        if isinstance(item, dict)
    ]
    result["version"] = "22.2.5"
    result["evidenceContract"] = "operatingEvidenceGraph.v1"
    result["evidenceVersion"] = OPERATING_EVIDENCE_CONTRACT_VERSION
    result["productSignalPackages"] = normalized
    result["signals"] = normalized
    result["productSignalPackageCount"] = len(normalized)
    result["productSignalCount"] = len(normalized)
    return result


def validate_signal_snapshot(snapshot: Dict[str, Any], *, baseline_only: bool) -> Dict[str, Any]:
    packages = snapshot.get("productSignalPackages") or snapshot.get("signals") or []
    checks = [
        validate_product_signal_package(item, baseline_only=baseline_only)
        for item in packages
        if isinstance(item, dict)
    ]
    invalid = [item for item in checks if item.get("ok") is not True]
    return {
        "ok": bool(packages) and not invalid,
        "status": "passed" if packages and not invalid else "failed",
        "packageCount": len(packages),
        "invalidCount": len(invalid),
        "invalid": invalid[:20],
        "sample": [item.get("productId") for item in invalid[:5]],
        "version": OPERATING_EVIDENCE_CONTRACT_VERSION,
    }


__all__ = [
    "OPERATING_EVIDENCE_CONTRACT_VERSION",
    "build_cross_validation_contract",
    "normalize_cross_validation",
    "normalize_product_signal_package",
    "normalize_signal_snapshot",
    "validate_product_signal_package",
    "validate_signal_snapshot",
]
