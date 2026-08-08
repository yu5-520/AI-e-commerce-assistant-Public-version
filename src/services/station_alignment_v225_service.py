"""V22.2.5 artifact-aware full-bundle and quality-gate stations.

V22.2.5 keeps the historical station contract while binding every full product
bundle to the canonical product snapshot that supplied its facts. The station is
the compatibility boundary: legacy signal construction may remain, but no Agent
package leaves this station without a canonical parent hash when a matching
product snapshot exists.
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

STATION_ALIGNMENT_V225_VERSION = "22.2.5"
CANONICAL_LINEAGE_CONTRACT = "canonicalProductSnapshot.lineage.v1"


def _packages(snapshot: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    values = snapshot.get("productSignalPackages") or snapshot.get("signals") or []
    return [item for item in values if isinstance(item, dict)]


def _product_key(item: Dict[str, Any]) -> str:
    return str(item.get("objectId") or item.get("entityId") or item.get("productId") or "").strip()


def _canonical_index(data_version: str | None) -> Dict[str, Dict[str, Any]]:
    snapshot = get_product_snapshot(data_version)
    index: Dict[str, Dict[str, Any]] = {}
    for product in (snapshot or {}).get("products") or []:
        if not isinstance(product, dict):
            continue
        for value in [
            product.get("objectId"),
            product.get("productId"),
            (product.get("profileSnapshot") or {}).get("skuId") if isinstance(product.get("profileSnapshot"), dict) else None,
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
            (package.get("profileLayer") or {}).get("skuId") if isinstance(package.get("profileLayer"), dict) else None,
            (package.get("productProfileSnapshot") or {}).get("skuId") if isinstance(package.get("productProfileSnapshot"), dict) else None,
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
    snapshot = normalize_signal_snapshot(raw, baseline_only=baseline_only)
    snapshot = _bind_canonical_lineage(data_version, snapshot)
    validation = validate_signal_snapshot(snapshot, baseline_only=baseline_only)
    if validation.get("ok") is not True:
        raise RuntimeError(
            "full_product_bundle_contract_invalid_v22_2_5:"
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
        "baselineMode": "first_report" if baseline_only else "normal_delta",
        "baselineNoPrevious": baseline_only,
        "baseline": baseline,
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceVersion": OPERATING_EVIDENCE_CONTRACT_VERSION,
        "canonicalLineage": lineage,
        "contractValidation": validation,
        "productSignalPackages": packages,
        "signals": packages,
        "result": snapshot,
        "outputRef": f"business_output_pending_artifact:full_product_bundle:{data_version or 'latest'}",
        "rule": "The station returns one canonical business snapshot and preserves canonical productSnapshotHash into Agent-facing bundles.",
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
    snapshot = (
        upstream.get("result")
        if isinstance(upstream.get("result"), dict)
        else upstream
    )
    snapshot = _bind_canonical_lineage(data_version, snapshot)
    baseline_only, baseline = _baseline_only(data_version, snapshot)
    snapshot = normalize_signal_snapshot(snapshot, baseline_only=baseline_only)
    snapshot = _bind_canonical_lineage(data_version, snapshot)
    validation = validate_signal_snapshot(snapshot, baseline_only=baseline_only)
    if validation.get("ok") is not True:
        raise RuntimeError(
            "bundle_validation_contract_invalid_v22_2_5:"
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
    status = "passed" if packages else "waiting"
    return {
        "version": STATION_ALIGNMENT_V225_VERSION,
        "stationId": "bundle_validation_station",
        "businessOutputType": "validated_product_signal_snapshot",
        "dataVersion": data_version,
        "sourceArtifactRef": source_ref,
        "bundleCount": len(packages),
        "attentionBundleCount": attention,
        "validationStatus": status,
        "baselineMode": "first_report" if baseline_only else "normal_delta",
        "baselineNoPrevious": baseline_only,
        "baseline": baseline,
        "evidenceContract": "operatingEvidenceGraph.v1",
        "evidenceVersion": OPERATING_EVIDENCE_CONTRACT_VERSION,
        "canonicalLineage": snapshot.get("canonicalLineage") or {},
        "contractValidation": validation,
        "validatedSignals": packages,
        "productSignalPackages": packages,
        "outputRef": f"business_output_pending_artifact:bundle_validation:{data_version or 'latest'}",
        "rule": "Quality gate validates the Artifact content and keeps canonical productSnapshotHash attached to every Agent-facing bundle.",
    }


__all__ = [
    "STATION_ALIGNMENT_V225_VERSION",
    "CANONICAL_LINEAGE_CONTRACT",
    "full_product_bundle_station",
    "bundle_validation_station",
]
