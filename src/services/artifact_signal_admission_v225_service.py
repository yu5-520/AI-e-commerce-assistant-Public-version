"""V22.5.11 Signal admission from the validated business artifact.

This is the formal bridge from one validated batch artifact to product-level
``signalRef`` items. It does not reload the legacy Signal Pool and does not use a
payload fallback. A first-report baseline artifact is consumed only to record the
closed historical gate: it creates zero Signal items and zero Agent1 work.

For comparable reports, operating evidence decides whether Agent1 may inspect a
product. Numeric admission score only orders eligible throughput; it may not turn
meaningful/structural evidence into an observation. This behavior used to live in a
V22.2.6 import-time replacement. It now belongs to the registered v225 Station owner
so static Hash Lineage and runtime callable identity describe the same execution.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from src.services.agent_pipeline_governance_v213_service import normalize_admission_limits
from src.services.artifact_transport_service import resolve_artifact, store_artifact, validate_artifact
from src.services.pipeline_agent1_microbatch_v20101_service import (
    AGENT1_PENDING_STAGE,
    OBSERVED_STAGE,
)
from src.services.pipeline_item_service import (
    build_item_envelope,
    record_pipeline_item_event,
    upsert_pipeline_item,
)
from src.services.product_signal_admission_v197_service import score_signal

ARTIFACT_SIGNAL_ADMISSION_VERSION = "22.5.11"


def _validated_payload(artifact_id: str | None) -> Dict[str, Any]:
    ref = str(artifact_id or "").strip()
    if not ref.startswith("ART-"):
        raise RuntimeError("validated_bundle_artifact_ref_missing")
    validation = validate_artifact(ref)
    if validation.get("ok") is not True:
        raise RuntimeError(
            f"validated_bundle_artifact_invalid:{ref}:{validation.get('status') or 'invalid'}"
        )
    payload = resolve_artifact(ref)
    if not isinstance(payload, dict) or not payload:
        raise RuntimeError(f"validated_bundle_payload_invalid:{ref}")
    return payload


def _signals(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    values = (
        payload.get("validatedSignals")
        or payload.get("productSignalPackages")
        or payload.get("signals")
        or []
    )
    return [item for item in values if isinstance(item, dict)]


def _baseline_bundle_count(payload: Dict[str, Any]) -> int:
    for key in ("baselineProductBundleCount", "bundleCount"):
        try:
            value = int(payload.get(key) or 0)
        except Exception:
            value = 0
        if value > 0:
            return value
    bundles = payload.get("baselineProductBundles")
    return len(bundles) if isinstance(bundles, list) else 0


def _cross_validation(signal: Dict[str, Any]) -> Dict[str, Any]:
    value = signal.get("crossValidation")
    return value if isinstance(value, dict) else {}


def _decision(signal: Dict[str, Any]) -> Dict[str, Any]:
    value = _cross_validation(signal).get("decision")
    return value if isinstance(value, dict) else {}


def _field_signals(signal: Dict[str, Any]) -> List[Dict[str, Any]]:
    snapshot = signal.get("snapshotLayer") if isinstance(signal.get("snapshotLayer"), dict) else {}
    values = snapshot.get("fieldSignals") or signal.get("fieldSignals") or []
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _meaningful_change(signal: Dict[str, Any]) -> bool:
    decision = _decision(signal)
    if decision.get("taskTriggerAllowed") is True:
        return True
    cross = _cross_validation(signal)
    try:
        if int(cross.get("changedMetricCount") or 0) > 0:
            return True
    except Exception:
        pass
    return any(bool(item.get("meaningfulChange")) for item in _field_signals(signal))


def _structural_change(signal: Dict[str, Any]) -> bool:
    primary = str(signal.get("primarySignalType") or "").strip().lower()
    if primary in {
        "product_missing_from_latest",
        "product_new_in_latest",
        "new_product",
        "new_link",
        "test_link",
    }:
        return True
    return bool(
        primary == "product_baseline"
        and signal.get("previousProductMetricSnapshot") is None
    )


def _agent1_eligibility(signal: Dict[str, Any], *, baseline_only: bool) -> Dict[str, Any]:
    if baseline_only or _decision(signal).get("baselineOnly") is True:
        return {
            "eligible": False,
            "reason": "batch_or_product_baseline",
            "source": "operatingEvidenceGraph.v1",
        }
    decision = _decision(signal)
    status = str(decision.get("status") or "").strip().lower()
    if status not in {"passed", "attention", ""}:
        return {
            "eligible": False,
            "reason": f"evidence_decision_{status or 'invalid'}",
            "source": "operatingEvidenceGraph.v1",
        }
    if _structural_change(signal):
        return {
            "eligible": True,
            "reason": "structural_product_or_link_change",
            "source": "operatingEvidenceGraph.v1",
        }
    if _meaningful_change(signal):
        return {
            "eligible": True,
            "reason": "meaningful_metric_change",
            "source": "operatingEvidenceGraph.v1",
        }
    return {
        "eligible": False,
        "reason": "zero_meaningful_change",
        "source": "operatingEvidenceGraph.v1",
    }


def _signal_identity(signal: Dict[str, Any]) -> tuple[str, str, str]:
    signal_id = str(signal.get("signalId") or "").strip()
    product_id = str(signal.get("productId") or signal.get("entityId") or "").strip()
    store_id = str(signal.get("storeId") or "GLOBAL").strip()
    if not signal_id or not product_id:
        raise RuntimeError("validated_signal_identity_missing")
    return signal_id, product_id, store_id


def _seed_signal_item(
    *,
    data_version: str | None,
    signal: Dict[str, Any],
    score: Dict[str, Any],
    source_artifact_ref: str,
    admitted: bool,
) -> Dict[str, Any]:
    signal_id, product_id, store_id = _signal_identity(signal)
    signal_artifact = store_artifact(
        artifact_type="product_signal",
        value=signal,
        store_id=store_id,
        product_id=product_id,
        data_version=data_version,
        created_by="product_signal_admission_station",
        parent_refs=[source_artifact_ref],
        metadata={
            "signalId": signal_id,
            "admission": "admitted" if admitted else "observed",
            "score": score.get("score"),
            "level": score.get("level"),
            "agent1Eligible": bool(score.get("agent1Eligible")),
            "eligibilityReason": score.get("eligibilityReason"),
        },
    )
    signal_ref = str(signal_artifact.get("artifactId") or "")
    if not signal_ref.startswith("ART-"):
        raise RuntimeError("signal_artifact_store_failed")
    stage = AGENT1_PENDING_STAGE if admitted else OBSERVED_STAGE
    status = "queued" if admitted else "observed"
    output_ref = f"agent1_pending:{signal_id}" if admitted else f"observed_signal:{signal_id}"
    envelope = build_item_envelope(
        data_version=data_version,
        product_id=product_id,
        store_id=store_id,
        signal_id=signal_id,
        package_id=signal.get("packageId"),
        input_ref=signal_ref,
        output_ref=output_ref,
        stage=stage,
        artifact_refs={"signalRef": signal_ref},
    )
    handle = {
        "version": ARTIFACT_SIGNAL_ADMISSION_VERSION,
        "source": "validated_signal_artifact",
        "signalId": signal_id,
        "productId": product_id,
        "storeId": store_id,
        "signalRef": signal_ref,
        "admissionScore": score,
        "admissionDecision": "admitted" if admitted else "observed",
        "fullSignalPayloadStoredInArtifactHub": True,
    }
    stored = upsert_pipeline_item(
        envelope,
        stage=stage,
        status=status,
        priority=max(1, min(100, 100 - int(score.get("score") or 0))) if admitted else 100,
        output_ref=output_ref,
        payload=handle,
    )
    record_pipeline_item_event(
        stored,
        station_id="product_signal_admission_station",
        stage=stage,
        status=status,
        input_ref=source_artifact_ref,
        output_ref=signal_ref,
        payload=handle,
    )
    return {
        "signalId": signal_id,
        "productId": product_id,
        "storeId": store_id,
        "signalRef": signal_ref,
        **score,
    }


def product_signal_admission_station_v225(
    data_version: str | None,
    *,
    validated_bundle_ref: str | None,
    max_signals: int = 160,
    min_admitted: int = 0,
    max_admitted: int | None = None,
    **_: Any,
) -> Dict[str, Any]:
    limits = normalize_admission_limits(
        max_signals=max_signals,
        min_admitted=min_admitted,
        max_admitted=max_admitted,
    )
    payload = _validated_payload(validated_bundle_ref)
    baseline = payload.get("baseline") if isinstance(payload.get("baseline"), dict) else {}
    baseline_only = bool(payload.get("baselineNoPrevious") or baseline.get("baselineNoPrevious"))
    signals = _signals(payload)
    if baseline_only:
        baseline_bundle_count = _baseline_bundle_count(payload)
        return {
            "version": ARTIFACT_SIGNAL_ADMISSION_VERSION,
            "stationId": "product_signal_admission_station",
            "businessOutputType": "baseline_history_gate_closed",
            "dataVersion": data_version,
            "validatedBundleArtifactRef": validated_bundle_ref,
            "baselineOnly": True,
            "signalEligibility": False,
            "baselineGate": "closed_before_signal_engine",
            "baselineProductBundleCount": baseline_bundle_count,
            "fullSignalCount": 0,
            "generatedSignalCount": 0,
            "qualifiedSignalCount": 0,
            "candidateProductCount": 0,
            "admittedSignalCount": 0,
            "observedSignalCount": 0,
            "agent1PendingItemCount": 0,
            "priorityScoredSignalCount": 0,
            "legacySignalPoolRead": False,
            "signalArtifactsCreated": 0,
            "scoreCanBlockAgent1": False,
            "scoreRole": "priority_only",
            "admissionPolicy": "baseline_never_enters_agent1",
            "outputRef": f"business_output_pending_artifact:baseline_admission:{data_version or 'latest'}",
            "rule": "First active report is canonical baseline only: no Signal Pool, no signalRef itemization and no Agent1 execution.",
        }

    candidates: List[Dict[str, Any]] = []
    for signal in signals:
        score = score_signal(signal)
        eligibility = _agent1_eligibility(signal, baseline_only=False)
        candidates.append({"signal": signal, "score": score, "eligibility": eligibility})
    candidates.sort(
        key=lambda item: (
            int(item["score"].get("score") or 0),
            str(item["signal"].get("productId") or item["signal"].get("entityId") or ""),
        ),
        reverse=True,
    )
    eligible = [item for item in candidates if item["eligibility"]["eligible"]]
    selected_ids = {
        str(item["signal"].get("signalId") or "")
        for item in eligible[: limits["maxAdmitted"]]
    }
    admitted: List[Dict[str, Any]] = []
    observed: List[Dict[str, Any]] = []
    reason_counts: Counter[str] = Counter()
    for item in candidates[: limits["maxSignals"]]:
        signal_id = str(item["signal"].get("signalId") or "")
        is_admitted = signal_id in selected_ids
        reason = str(item["eligibility"].get("reason") or "unknown")
        reason_counts[reason] += 1
        summary = _seed_signal_item(
            data_version=data_version,
            signal=item["signal"],
            score={
                **item["score"],
                "agent1Eligible": bool(item["eligibility"]["eligible"]),
                "eligibilityReason": reason,
                "scoreRole": "priority_only",
            },
            source_artifact_ref=str(validated_bundle_ref),
            admitted=is_admitted,
        )
        summary["agent1Eligibility"] = item["eligibility"]
        (admitted if is_admitted else observed).append(summary)

    return {
        "version": ARTIFACT_SIGNAL_ADMISSION_VERSION,
        "stationId": "product_signal_admission_station",
        "businessOutputType": "artifact_signal_admission",
        "dataVersion": data_version,
        "validatedBundleArtifactRef": validated_bundle_ref,
        "baselineOnly": False,
        "signalEligibility": True,
        "baselineGate": "open_after_previous_snapshot",
        "fullSignalCount": len(signals),
        "generatedSignalCount": len(signals),
        "qualifiedSignalCount": len(eligible),
        "candidateProductCount": len(admitted),
        "admittedSignalCount": len(admitted),
        "observedSignalCount": len(observed),
        "agent1PendingItemCount": len(admitted),
        "observedItemCount": len(observed),
        "admitted": admitted,
        "observedTop": observed[:12],
        "admissionLimits": limits,
        "admissionReasonCounts": dict(reason_counts),
        "artificialMinimumApplied": False,
        "legacySignalPoolRead": False,
        "scoreCanBlockAgent1": False,
        "scoreRole": "priority_only",
        "admissionPolicy": "evidence_trigger_for_agent1_score_for_priority_only",
        "outputRef": f"business_output_pending_artifact:signal_admission:{data_version or 'latest'}",
        "rule": "Meaningful or structural evidence enters Agent1; numeric score only orders throughput.",
    }


__all__ = [
    "ARTIFACT_SIGNAL_ADMISSION_VERSION",
    "product_signal_admission_station_v225",
]
