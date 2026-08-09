"""V22.2.5 artifact-aware full-bundle and quality-gate stations.

V22.2.5 keeps the historical station contract while binding every full product
bundle to the canonical product snapshot that supplied its facts. The station is
the compatibility boundary: first-report baseline evidence is validated as canonical
product evidence, not as a Signal contract, and therefore never requires Signal
itemization or Agent1 execution.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.services import product_signal_snapshot_service as signal_snapshot_service
from src.services.artifact_transport_service import resolve_artifact, validate_artifact
from src.services.canonical_product_snapshot_service import get_product_snapshot
from src.services.metric_trigger_expansion_v171_service import is_first_report_baseline
from src.services.operating_evidence_contract_service import (
    OPERATING_EVIDENCE_CONTRACT_VERSION,
    normalize_signal_snapshot,
    validate_signal_snapshot,
)

STATION_ALIGNMENT_V225_VERSION = "22.2.6"
CANONICAL_LINEAGE_CONTRACT = "canonicalProductSnapshot.lineage.v1"
BASELINE_EVIDENCE_CONTRACT = "canonicalProductBaselineEvidence.v1"


def _packages(snapshot: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    values = snapshot.get("productSignalPackages") or snapshot.get("signals") or []
    return [item for item in values if isinstance(item, dict)]


def _product_key(item: Dict[str, Any]) -> str:
    return str(item.get("objectId") or item.get("entityId") or item.get("productId") or "").strip()


def _canonical_products(data_version: str | None) -> List[Dict[str, Any]]:
    snapshot = get_product_snapshot(data_version)
    return [
        dict(item)
        for item in (snapshot or {}).get("products") or []
        if isinstance(item, dict)
    ]


def _canonical_index(data_version: str | None) -> Dict[str, Dict[str, Any]]:
    index: Dict[str, Dict[str, Any]] = {}
    for product in _canonical_products(data_version):
        for value in [
            product.get("objectId"),
            product.get("productId"),
            (product.get("profileSnapshot") or {}).get("skuId")
            if isinstance(product.get("profileSnapshot"), dict)
            else None,
        ]:
            key = str(value or "").strip()
            if key:
                index[key] = product
    return index


def _bind_canonical_lineage(data_version: str | None, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Attach immutable parent hashes without rebuilding any business facts."""
    canonical = _canonical_index(data_version)
    packages = _packages(snapshot)
    bound: List[Dict[str, Any]] = []
    matched = 0
    for package in packages:
        product = None
        candidates = [
            package.get("entityId"),
            package.get("productId"),
            (package.get("profileLayer") or {}).get("skuId")
            if isinstance(package.get("profileLayer"), dict)
            else None,
            (package.get("productProfileSnapshot") or {}).get("skuId")
            if isinstance(package.get("productProfileSnapshot"), dict)
            else None,
        ]
        for candidate in candidates:
            key = str(candidate or "").strip()
            if key and key in canonical:
                product = canonical[key]
                break
        next_package = dict(package)
        if product:
            parent_hash = product.get("productSnapshotHash") or product.get("snapshotHash")
            lineage = {
                "contract": CANONICAL_LINEAGE_CONTRACT,
                "dataVersion": data_version,
                "objectId": product.get("objectId"),
                "productId": product.get("productId"),
                "productSnapshotHash": parent_hash,
                "parentSnapshotHash": parent_hash,
                "factRefs": list(product.get("factRefs") or []),
                "factHashRefs": list(product.get("factHashRefs") or []),
                "sourceArtifactRefs": list(product.get("sourceArtifactRefs") or []),
            }
            next_package.update(lineage)
            agent_package = dict(next_package.get("agentProductSnapshotPackage") or {})
            agent_package.update(lineage)
            next_package["agentProductSnapshotPackage"] = agent_package
            matched += 1
        else:
            next_package["canonicalLineageMissing"] = True
        bound.append(next_package)

    next_snapshot = dict(snapshot)
    next_snapshot["productSignalPackages"] = bound
    next_snapshot["signals"] = bound
    next_snapshot["canonicalLineage"] = {
        "contract": CANONICAL_LINEAGE_CONTRACT,
        "dataVersion": data_version,
        "packageCount": len(bound),
        "matchedPackageCount": matched,
        "missingPackageCount": len(bound) - matched,
        "complete": bool(bound) and matched == len(bound),
        "rule": "Agent-facing bundle lineage is inherited from canonical product snapshot; no station-side fact rebuild is allowed.",
    }
    return next_snapshot


def _canonical_baseline_bundle(product: Dict[str, Any], data_version: str | None) -> Dict[str, Any]:
    profile = dict(product.get("profileSnapshot") or {})
    metric = dict(product.get("metricSnapshot") or {})
    parent_hash = product.get("productSnapshotHash") or product.get("snapshotHash")
    object_id = product.get("objectId") or profile.get("objectId")
    product_id = product.get("productId") or profile.get("productId")
    store_id = product.get("storeId") or profile.get("storeId")
    return {
        "baselineEvidenceId": f"BASELINE::{object_id or store_id or 'GLOBAL'}::{product_id or 'PRODUCT'}",
        "evidenceContract": BASELINE_EVIDENCE_CONTRACT,
        "evidenceStatus": "baseline",
        "baselineOnly": True,
        "dataVersion": data_version,
        "entityType": "product",
        "entityId": object_id,
        "objectId": object_id,
        "productId": product_id,
        "storeId": store_id,
        "platform": product.get("platform") or profile.get("platform"),
        "profileLayer": profile,
        "metricLayer": metric,
        "productSnapshotHash": parent_hash,
        "parentSnapshotHash": parent_hash,
        "factRefs": list(product.get("factRefs") or []),
        "factHashRefs": list(product.get("factHashRefs") or []),
        "sourceArtifactRefs": list(product.get("sourceArtifactRefs") or []),
        "canonicalLineageContract": CANONICAL_LINEAGE_CONTRACT,
        "rule": "First-report evidence is a canonical product baseline, not a Signal item.",
    }


def _baseline_evidence_bundles(
    data_version: str | None,
    snapshot: Dict[str, Any] | None,
) -> List[Dict[str, Any]]:
    """Return baseline evidence without requiring the Signal contract.

    Existing full-product bundles are retained when available because they already
    contain richer profile/metric evidence. If the compatibility Signal snapshot is
    empty, canonical product snapshots are the authoritative fallback. In neither
    case are these bundles exposed as Signals.
    """
    packages = _packages(snapshot)
    if packages:
        return packages
    return [
        _canonical_baseline_bundle(product, data_version)
        for product in _canonical_products(data_version)
    ]


def _validate_baseline_evidence(
    bundles: List[Dict[str, Any]],
    data_version: str | None,
) -> Dict[str, Any]:
    invalid: List[Dict[str, Any]] = []
    for item in bundles:
        identity = _product_key(item)
        parent_hash = item.get("productSnapshotHash") or item.get("parentSnapshotHash")
        missing: List[str] = []
        if not identity:
            missing.append("productIdentity")
        if not parent_hash:
            missing.append("productSnapshotHash")
        if missing:
            invalid.append(
                {
                    "productId": item.get("productId") or item.get("entityId"),
                    "missing": missing,
                }
            )
    return {
        "ok": bool(bundles) and not invalid,
        "status": "passed" if bundles and not invalid else "failed",
        "contract": BASELINE_EVIDENCE_CONTRACT,
        "dataVersion": data_version,
        "packageCount": len(bundles),
        "invalidCount": len(invalid),
        "invalid": invalid[:20],
        "sample": [item.get("productId") for item in invalid[:5]],
        "signalContractRequired": False,
    }


def _baseline_only(data_version: str | None, snapshot: Dict[str, Any] | None) -> tuple[bool, Dict[str, Any]]:
    baseline = is_first_report_baseline(data_version)
    value = bool(
        (snapshot or {}).get("baselineNoPrevious")
        or baseline.get("isFirstReportBaseline")
    )
    return value, baseline


def _resolve_business_artifact(artifact_id: str | None) -> Dict[str, Any]:
    ref = str(artifact_id or "").strip()
    if not ref.startswith("ART-"):
        raise RuntimeError("required_business_artifact_ref_missing")
    validation = validate_artifact(ref)
    if validation.get("ok") is not True:
        raise RuntimeError(
            f"business_artifact_invalid:{ref}:{validation.get('status') or 'invalid'}"
        )
    payload = resolve_artifact(ref)
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(f"business_artifact_payload_invalid:{ref}")
    return payload


def full_product_bundle_station(
    data_version: str | None,
    *,
    user_id: str | None = None,
    force: bool = True,
    **_: Any,
) -> Dict[str, Any]:
    raw = signal_snapshot_service.materialize_product_signal_snapshot(
        data_version=data_version,
        user_id=user_id,
        force=force,
    )
    raw = _bind_canonical_lineage(data_version, raw)
    baseline_only, baseline = _baseline_only(data_version, raw)

    if baseline_only:
        packages = _baseline_evidence_bundles(data_version, raw)
        validation = _validate_baseline_evidence(packages, data_version)
        if validation.get("ok") is not True:
            raise RuntimeError(
                "baseline_product_bundle_contract_invalid_v22_2_6:"
                f"dataVersion={data_version or 'latest'};"
                f"packageCount={validation.get('packageCount')};"
                f"invalidCount={validation.get('invalidCount')};"
                f"sample={','.join(str(value) for value in validation.get('sample') or [])}"
            )
        lineage = raw.get("canonicalLineage") or {}
        result = {
            "baselineNoPrevious": True,
            "baselineMode": "first_report",
            "baselineProductBundleCount": len(packages),
            "baselineProductBundles": packages,
            "productSignalPackages": [],
            "signals": [],
            "canonicalLineage": lineage,
            "signalEligibility": False,
        }
        return {
            "version": STATION_ALIGNMENT_V225_VERSION,
            "stationId": "full_product_bundle_station",
            "businessOutputType": "baseline_product_bundle",
            "dataVersion": data_version,
            "productSignalPackageCount": 0,
            "productSignalCount": 0,
            "generatedSignalCount": 0,
            "baselineProductBundleCount": len(packages),
            "signalEligibility": False,
            "baselineGate": "closed_before_signal_engine",
            "baselineMode": "first_report",
            "baselineNoPrevious": True,
            "baseline": baseline,
            "evidenceContract": BASELINE_EVIDENCE_CONTRACT,
            "evidenceVersion": OPERATING_EVIDENCE_CONTRACT_VERSION,
            "canonicalLineage": lineage,
            "contractValidation": validation,
            "baselineProductBundles": packages,
            "productSignalPackages": [],
            "signals": [],
            "result": result,
            "outputRef": f"business_output_pending_artifact:full_product_bundle:{data_version or 'latest'}",
            "rule": (
                "First report validates canonical product baseline evidence directly; "
                "Signal output stays zero until a strictly earlier active report exists."
            ),
        }

    snapshot = normalize_signal_snapshot(raw, baseline_only=False)
    snapshot = _bind_canonical_lineage(data_version, snapshot)
    validation = validate_signal_snapshot(snapshot, baseline_only=False)
    if validation.get("ok") is not True:
        raise RuntimeError(
            "full_product_bundle_contract_invalid_v22_2_6:"
            f"dataVersion={data_version or 'latest'};"
            f"packageCount={validation.get('packageCount')};"
            f"invalidCount={validation.get('invalidCount')};"
            f"sample={','.join(str(value) for value in validation.get('sample') or [])}"
        )
    packages = _packages(snapshot)
    lineage = snapshot.get("canonicalLineage") or {}
    return {
        "version": STATION_ALIGNMENT_V225_VERSION,
        "stationId": "full_product_bundle_station",
        "businessOutputType": "full_product_signal_snapshot",
        "dataVersion": data_version,
        "productSignalPackageCount": len(packages),
        "productSignalCount": len(packages),
        "generatedSignalCount": len(packages),
        "baselineProductBundleCount": 0,
        "signalEligibility": True,
        "baselineGate": "open_after_previous_snapshot",
        "baselineMode": "normal_delta",
        "baselineNoPrevious": False,
        "baseline": baseline,
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceVersion": OPERATING_EVIDENCE_CONTRACT_VERSION,
        "canonicalLineage": lineage,
        "contractValidation": validation,
        "baselineProductBundles": [],
        "productSignalPackages": packages,
        "signals": packages,
        "result": snapshot,
        "outputRef": f"business_output_pending_artifact:full_product_bundle:{data_version or 'latest'}",
        "rule": "The station returns canonical delta evidence with productSnapshotHash lineage.",
    }


def bundle_validation_station(
    data_version: str | None,
    *,
    full_product_bundle_ref: str | None = None,
    fullProductBundleRef: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    source_ref = full_product_bundle_ref or fullProductBundleRef
    upstream = _resolve_business_artifact(source_ref)
    baseline_only, baseline = _baseline_only(data_version, upstream)

    if baseline_only:
        result = upstream.get("result") if isinstance(upstream.get("result"), dict) else {}
        raw_bundles = (
            upstream.get("baselineProductBundles")
            or result.get("baselineProductBundles")
            or []
        )
        packages = [item for item in raw_bundles if isinstance(item, dict)]
        if not packages:
            packages = _baseline_evidence_bundles(data_version, result)
        validation = _validate_baseline_evidence(packages, data_version)
        if validation.get("ok") is not True:
            raise RuntimeError(
                "baseline_bundle_validation_contract_invalid_v22_2_6:"
                f"dataVersion={data_version or 'latest'};"
                f"packageCount={validation.get('packageCount')};"
                f"invalidCount={validation.get('invalidCount')};"
                f"sample={','.join(str(value) for value in validation.get('sample') or [])}"
            )
        return {
            "version": STATION_ALIGNMENT_V225_VERSION,
            "stationId": "bundle_validation_station",
            "businessOutputType": "validated_baseline_product_bundle",
            "dataVersion": data_version,
            "sourceArtifactRef": source_ref,
            "bundleCount": len(packages),
            "baselineProductBundleCount": len(packages),
            "validatedSignalCount": 0,
            "attentionBundleCount": 0,
            "validationStatus": "passed",
            "signalEligibility": False,
            "baselineGate": "closed_before_signal_engine",
            "baselineMode": "first_report",
            "baselineNoPrevious": True,
            "baseline": baseline,
            "evidenceContract": BASELINE_EVIDENCE_CONTRACT,
            "evidenceVersion": OPERATING_EVIDENCE_CONTRACT_VERSION,
            "canonicalLineage": upstream.get("canonicalLineage") or result.get("canonicalLineage") or {},
            "contractValidation": validation,
            "baselineProductBundles": packages,
            "validatedSignals": [],
            "productSignalPackages": [],
            "outputRef": f"business_output_pending_artifact:bundle_validation:{data_version or 'latest'}",
            "rule": (
                "Baseline bundles are canonical evidence, not Signals; validation passes "
                "without Signal itemization and Agent1 remains closed."
            ),
        }

    snapshot = (
        upstream.get("result")
        if isinstance(upstream.get("result"), dict)
        else upstream
    )
    snapshot = _bind_canonical_lineage(data_version, snapshot)
    snapshot = normalize_signal_snapshot(snapshot, baseline_only=False)
    snapshot = _bind_canonical_lineage(data_version, snapshot)
    validation = validate_signal_snapshot(snapshot, baseline_only=False)
    if validation.get("ok") is not True:
        raise RuntimeError(
            "bundle_validation_contract_invalid_v22_2_6:"
            f"dataVersion={data_version or 'latest'};"
            f"packageCount={validation.get('packageCount')};"
            f"invalidCount={validation.get('invalidCount')};"
            f"sample={','.join(str(value) for value in validation.get('sample') or [])}"
        )
    packages = _packages(snapshot)
    attention = sum(
        1
        for item in packages
        if ((item.get("crossValidation") or {}).get("decision") or {}).get("status")
        == "attention"
    )
    return {
        "version": STATION_ALIGNMENT_V225_VERSION,
        "stationId": "bundle_validation_station",
        "businessOutputType": "validated_product_signal_snapshot",
        "dataVersion": data_version,
        "sourceArtifactRef": source_ref,
        "bundleCount": len(packages),
        "baselineProductBundleCount": 0,
        "validatedSignalCount": len(packages),
        "attentionBundleCount": attention,
        "validationStatus": "passed" if packages else "waiting",
        "signalEligibility": True,
        "baselineGate": "open_after_previous_snapshot",
        "baselineMode": "normal_delta",
        "baselineNoPrevious": False,
        "baseline": baseline,
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceVersion": OPERATING_EVIDENCE_CONTRACT_VERSION,
        "canonicalLineage": snapshot.get("canonicalLineage") or {},
        "contractValidation": validation,
        "baselineProductBundles": [],
        "validatedSignals": packages,
        "productSignalPackages": packages,
        "outputRef": f"business_output_pending_artifact:bundle_validation:{data_version or 'latest'}",
        "rule": "Quality gate validates delta Signals and keeps canonical productSnapshotHash lineage.",
    }


__all__ = [
    "STATION_ALIGNMENT_V225_VERSION",
    "CANONICAL_LINEAGE_CONTRACT",
    "BASELINE_EVIDENCE_CONTRACT",
    "full_product_bundle_station",
    "bundle_validation_station",
]
