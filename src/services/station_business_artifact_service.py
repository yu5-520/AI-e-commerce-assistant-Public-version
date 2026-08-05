"""Persist validated station business output as the pipeline's authoritative artifact."""
from __future__ import annotations

from typing import Any, Dict, List

from src.services.pipeline_item_service import (
    build_item_envelope,
    record_pipeline_item_event,
    upsert_pipeline_item,
)

STATION_BUSINESS_ARTIFACT_VERSION = "22.2.5"

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


def business_output_payload(output: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in dict(output or {}).items()
        if key not in _RUNTIME_ONLY_FIELDS
    }


def validate_business_output(station_id: str, output: Dict[str, Any]) -> Dict[str, Any]:
    missing: List[str] = []
    if not isinstance(output, dict) or not output:
        missing.append("businessOutput")
    if station_id == "full_product_bundle_station":
        if output.get("businessOutputType") != "full_product_signal_snapshot":
            missing.append("businessOutputType=full_product_signal_snapshot")
        if int(output.get("productSignalPackageCount") or 0) <= 0:
            missing.append("productSignalPackageCount>0")
        if not isinstance(output.get("productSignalPackages"), list):
            missing.append("productSignalPackages")
        validation = output.get("contractValidation") if isinstance(output.get("contractValidation"), dict) else {}
        if validation.get("ok") is not True:
            missing.append("contractValidation.ok")
    elif station_id == "bundle_validation_station":
        if output.get("businessOutputType") != "validated_product_signal_snapshot":
            missing.append("businessOutputType=validated_product_signal_snapshot")
        if int(output.get("bundleCount") or 0) <= 0:
            missing.append("bundleCount>0")
        if output.get("validationStatus") not in {"passed", "attention"}:
            missing.append("validationStatus")
        if not isinstance(output.get("validatedSignals"), list):
            missing.append("validatedSignals")
        validation = output.get("contractValidation") if isinstance(output.get("contractValidation"), dict) else {}
        if validation.get("ok") is not True:
            missing.append("contractValidation.ok")
    elif station_id == "product_signal_admission_station":
        for key in ("fullSignalCount", "admittedSignalCount", "observedSignalCount"):
            if output.get(key) is None:
                missing.append(key)
    return {
        "version": STATION_BUSINESS_ARTIFACT_VERSION,
        "ok": not missing,
        "status": "passed" if not missing else "failed",
        "stationId": station_id,
        "missing": missing,
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
