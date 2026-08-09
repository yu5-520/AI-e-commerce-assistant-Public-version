"""Persist validated station business output as the pipeline's authoritative artifact.

V22.2.6 closes the last first-report artifact gap by validating *semantic mode*
rather than assuming every evidence bundle is already a Signal bundle. The field
semantics are governed by the hash-anchored registry overlay used by the competition
lineage gate:

- first report: canonical baseline evidence is valid while Signal/Agent counts stay 0;
- comparable report: Signal eligibility may open, but no positive minimum Signal
  count is invented;
- immutable ART references are still written only after this business validation.
"""
from __future__ import annotations

from typing import Any, Dict, List

from src.services.pipeline_item_service import (
    build_item_envelope,
    record_pipeline_item_event,
    upsert_pipeline_item,
)

STATION_BUSINESS_ARTIFACT_VERSION = "22.2.6"

STATION_TO_STAGE = {
    "report_receive_station": "data_received",
    "report_schema_station": "schema_ready",
    "report_fact_station": "fact_ready",
    "product_master_station": "product_master_ready",
    "product_metric_snapshot_station": "metric_snapshot_ready",
    "full_product_bundle_station": "context_bundle_ready",
    "bundle_validation_station": "quality_gate_ready",
    "product_signal_admission_station": "signal_admission_completed",
}

_RUNTIME_ONLY_FIELDS = {
    "pipelineItemEnvelope",
    "pipelineItemState",
    "pipelineItemSummary",
    "readModelRefresh",
    "isDiagnostic",
    "adapterMode",
    "pipelineInterfaceMode",
}

BASELINE_FULL_BUNDLE_TYPE = "baseline_product_bundle"
DELTA_FULL_BUNDLE_TYPE = "full_product_signal_snapshot"
BASELINE_VALIDATED_BUNDLE_TYPE = "validated_baseline_product_bundle"
DELTA_VALIDATED_BUNDLE_TYPE = "validated_product_signal_snapshot"
BASELINE_ADMISSION_TYPE = "baseline_history_gate_closed"
DELTA_ADMISSION_TYPE = "artifact_signal_admission"
BASELINE_GATE_CLOSED = "closed_before_signal_engine"
DELTA_GATE_OPEN = "open_after_previous_snapshot"


def business_output_payload(output: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in dict(output or {}).items()
        if key not in _RUNTIME_ONLY_FIELDS
    }


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _require_list(output: Dict[str, Any], key: str, missing: List[str]) -> List[Any]:
    value = output.get(key)
    if not isinstance(value, list):
        missing.append(key)
        return []
    return value


def _require_contract_validation(output: Dict[str, Any], missing: List[str]) -> Dict[str, Any]:
    validation = output.get("contractValidation") if isinstance(output.get("contractValidation"), dict) else {}
    if validation.get("ok") is not True:
        missing.append("contractValidation.ok")
    return validation


def _require_signal_gate(
    output: Dict[str, Any],
    *,
    eligible: bool,
    gate: str,
    missing: List[str],
) -> None:
    if output.get("signalEligibility") is not eligible:
        missing.append(f"signalEligibility={str(eligible).lower()}")
    if output.get("baselineGate") != gate:
        missing.append(f"baselineGate={gate}")


def _validate_full_product_bundle(output: Dict[str, Any], missing: List[str]) -> None:
    output_type = str(output.get("businessOutputType") or "")
    _require_contract_validation(output, missing)

    if output_type == BASELINE_FULL_BUNDLE_TYPE:
        baseline_bundles = _require_list(output, "baselineProductBundles", missing)
        signal_packages = _require_list(output, "productSignalPackages", missing)
        baseline_count = _int(output.get("baselineProductBundleCount"))
        signal_count = _int(output.get("productSignalPackageCount"))
        if baseline_count <= 0:
            missing.append("baselineProductBundleCount>0")
        if len(baseline_bundles) != baseline_count:
            missing.append("baselineProductBundles.count")
        if signal_count != 0 or signal_packages:
            missing.append("baseline.productSignalPackageCount=0")
        _require_signal_gate(
            output,
            eligible=False,
            gate=BASELINE_GATE_CLOSED,
            missing=missing,
        )
        return

    if output_type == DELTA_FULL_BUNDLE_TYPE:
        signal_packages = _require_list(output, "productSignalPackages", missing)
        signal_count = _int(output.get("productSignalPackageCount"))
        if signal_count < 0:
            missing.append("productSignalPackageCount>=0")
        if len(signal_packages) != signal_count:
            missing.append("productSignalPackages.count")
        _require_signal_gate(
            output,
            eligible=True,
            gate=DELTA_GATE_OPEN,
            missing=missing,
        )
        return

    missing.append(
        "businessOutputType in {baseline_product_bundle,full_product_signal_snapshot}"
    )


def _validate_bundle_validation(output: Dict[str, Any], missing: List[str]) -> None:
    output_type = str(output.get("businessOutputType") or "")
    _require_contract_validation(output, missing)

    if output_type == BASELINE_VALIDATED_BUNDLE_TYPE:
        baseline_bundles = _require_list(output, "baselineProductBundles", missing)
        validated = _require_list(output, "validatedSignals", missing)
        baseline_count = _int(output.get("baselineProductBundleCount"))
        bundle_count = _int(output.get("bundleCount"))
        validated_count = _int(output.get("validatedSignalCount"))
        if baseline_count <= 0:
            missing.append("baselineProductBundleCount>0")
        if bundle_count != baseline_count:
            missing.append("bundleCount=baselineProductBundleCount")
        if len(baseline_bundles) != baseline_count:
            missing.append("baselineProductBundles.count")
        if validated_count != 0 or validated:
            missing.append("baseline.validatedSignalCount=0")
        if output.get("validationStatus") != "passed":
            missing.append("validationStatus=passed")
        _require_signal_gate(
            output,
            eligible=False,
            gate=BASELINE_GATE_CLOSED,
            missing=missing,
        )
        return

    if output_type == DELTA_VALIDATED_BUNDLE_TYPE:
        validated = _require_list(output, "validatedSignals", missing)
        bundle_count = _int(output.get("bundleCount"))
        validated_count = _int(output.get("validatedSignalCount"))
        if bundle_count < 0 or validated_count < 0:
            missing.append("deltaCounts>=0")
        if len(validated) != validated_count:
            missing.append("validatedSignals.count")
        if output.get("validationStatus") not in {"passed", "attention", "waiting"}:
            missing.append("validationStatus")
        _require_signal_gate(
            output,
            eligible=True,
            gate=DELTA_GATE_OPEN,
            missing=missing,
        )
        return

    missing.append(
        "businessOutputType in {validated_baseline_product_bundle,validated_product_signal_snapshot}"
    )


def _validate_signal_admission(output: Dict[str, Any], missing: List[str]) -> None:
    output_type = str(output.get("businessOutputType") or "")
    count_keys = (
        "fullSignalCount",
        "generatedSignalCount",
        "qualifiedSignalCount",
        "candidateProductCount",
        "admittedSignalCount",
        "observedSignalCount",
        "agent1PendingItemCount",
    )
    for key in count_keys:
        if output.get(key) is None:
            missing.append(key)
        elif _int(output.get(key)) < 0:
            missing.append(f"{key}>=0")

    if output_type == BASELINE_ADMISSION_TYPE:
        _require_signal_gate(
            output,
            eligible=False,
            gate=BASELINE_GATE_CLOSED,
            missing=missing,
        )
        if _int(output.get("baselineProductBundleCount")) <= 0:
            missing.append("baselineProductBundleCount>0")
        for key in count_keys:
            if _int(output.get(key)) != 0:
                missing.append(f"baseline.{key}=0")
        return

    if output_type == DELTA_ADMISSION_TYPE:
        _require_signal_gate(
            output,
            eligible=True,
            gate=DELTA_GATE_OPEN,
            missing=missing,
        )
        # Eligibility means permission to emit Signals, not a positive minimum.
        return

    missing.append(
        "businessOutputType in {baseline_history_gate_closed,artifact_signal_admission}"
    )


def validate_business_output(station_id: str, output: Dict[str, Any]) -> Dict[str, Any]:
    missing: List[str] = []
    if not isinstance(output, dict) or not output:
        missing.append("businessOutput")
    elif station_id == "full_product_bundle_station":
        _validate_full_product_bundle(output, missing)
    elif station_id == "bundle_validation_station":
        _validate_bundle_validation(output, missing)
    elif station_id == "product_signal_admission_station":
        _validate_signal_admission(output, missing)
    return {
        "version": STATION_BUSINESS_ARTIFACT_VERSION,
        "ok": not missing,
        "status": "passed" if not missing else "failed",
        "stationId": station_id,
        "businessOutputType": output.get("businessOutputType") if isinstance(output, dict) else None,
        "missing": missing,
        "rule": (
            "Baseline and comparable-delta are distinct registered semantic modes; "
            "zero Signal/Agent counts are valid when the historical gate is closed "
            "or when an eligible comparable report produces no admissible Signal."
        ),
    }


def record_business_station_output(
    *,
    station_id: str,
    data_version: str | None,
    output: Dict[str, Any],
    upstream_envelope: Dict[str, Any] | None,
    input_ref: str | None,
) -> Dict[str, Any]:
    business_output = business_output_payload(output)
    validation = validate_business_output(station_id, business_output)
    if validation.get("ok") is not True:
        raise RuntimeError(
            f"station_business_output_invalid:{station_id}:"
            + ",".join(validation.get("missing") or [])
        )
    upstream = upstream_envelope if isinstance(upstream_envelope, dict) else {}
    stage = STATION_TO_STAGE.get(station_id, station_id)
    envelope = build_item_envelope(
        data_version=data_version,
        item_id=upstream.get("itemId"),
        product_id=upstream.get("productId"),
        store_id=upstream.get("storeId"),
        signal_id=upstream.get("signalId"),
        package_id=upstream.get("packageId"),
        decision_id=upstream.get("decisionId"),
        task_id=upstream.get("taskId"),
        action_family=upstream.get("actionFamily"),
        route=upstream.get("route"),
        input_ref=input_ref,
        output_ref=None,
        stage=stage,
        artifact_refs=upstream.get("artifactRefs")
        if isinstance(upstream.get("artifactRefs"), dict)
        else {},
    )
    stored = upsert_pipeline_item(
        envelope,
        stage=stage,
        status="completed",
        priority=100,
        output_ref=None,
        payload=business_output,
    )
    artifact_ref = stored.get("payloadArtifactRef")
    if not str(artifact_ref or "").startswith("ART-"):
        raise RuntimeError(f"station_business_artifact_missing:{station_id}")
    record_pipeline_item_event(
        stored,
        station_id=station_id,
        stage=stage,
        status="completed",
        input_ref=input_ref,
        output_ref=artifact_ref,
        payload={
            "businessOutputType": business_output.get("businessOutputType"),
            "dataVersion": data_version,
            "artifactRef": artifact_ref,
            "validation": validation,
        },
    )
    return {
        "version": STATION_BUSINESS_ARTIFACT_VERSION,
        "stage": stage,
        "itemId": stored.get("itemId"),
        "payloadArtifactRef": artifact_ref,
        "artifactRefs": stored.get("artifactRefs") or {},
        "businessOutputValidation": validation,
        "runtimeReceiptStoredAsBusinessArtifact": False,
    }


__all__ = [
    "STATION_BUSINESS_ARTIFACT_VERSION",
    "business_output_payload",
    "validate_business_output",
    "record_business_station_output",
]
