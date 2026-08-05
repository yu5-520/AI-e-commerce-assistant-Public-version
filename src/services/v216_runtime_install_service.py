"""Install V21.6 observation maturity and experiment admission at runtime."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Sequence

from src.services import v216_observation_experiment_service as core

V216_VERSION = core.V216_VERSION


def _safe_observation_fallback(_signal: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "score": 0,
        "level": "noise_or_baseline",
        "reasons": ["missing_v21_5_cross_validation"],
        "changedMetricCount": 0,
        "abnormalMetricCount": 0,
        "sourceVersionCount": 0,
        "softGateRule": "fail_closed_missing_operating_evidence",
    }


def _cross_validation(signal: Dict[str, Any]) -> Dict[str, Any]:
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else signal
    if not isinstance(payload, dict):
        return {}
    value = payload.get("crossValidation")
    return value if isinstance(value, dict) else {}


def install_v216_runtime() -> None:
    from src.services import product_signal_admission_v197_service as admission
    from src.services import v215_report_batch_evidence_service as v215

    if getattr(admission, "_V216_INSTALLED", False):
        return

    original_build_cross_validation = v215.build_cross_validation
    original_v215_score = v215.score_cross_validated_signal

    def build_cross_validation_v216(
        current_item: Dict[str, Any],
        history_items: Sequence[Dict[str, Any]],
    ) -> Dict[str, Any]:
        cross = original_build_cross_validation(current_item, history_items)
        maturity = core.build_observation_maturity(
            v215,
            current_item,
            history_items,
            cross,
        )
        decision = cross.get("decision") if isinstance(cross.get("decision"), dict) else {}
        hypothesis = str(decision.get("hypothesisCode") or "no_operating_event")
        policy = core.experiment_policy(hypothesis, maturity["maturity"])
        cross["observationMaturity"] = maturity
        cross["experimentPolicy"] = policy
        cross["maturityVersion"] = V216_VERSION
        cross["reportCountAdmissionRemoved"] = True
        cross["verificationTaskFamilyRemoved"] = True
        return cross

    def score_v216(signal: Dict[str, Any]) -> Dict[str, Any]:
        cross = _cross_validation(signal)
        maturity = cross.get("observationMaturity") if isinstance(cross.get("observationMaturity"), dict) else {}
        if not maturity:
            legacy = original_v215_score(signal, _safe_observation_fallback)
            return {
                **legacy,
                "evidenceMaturity": "legacy_missing",
                "maturityRank": 0,
                "alignedObservationCount": 0,
                "coverageRatio": 0.0,
                "experimentPolicy": core.experiment_policy(
                    str((cross.get("decision") or {}).get("hypothesisCode") or "no_operating_event"),
                    "M0_baseline",
                ),
                "admissionPriority": 0,
                "admissionEligibleV216": False,
                "softGateRule": "legacy_score_visible_but_not_agent_eligible_v21_6",
                "reasons": [*(legacy.get("reasons") or []), "observation_maturity_missing"][-12:],
            }
        scored = core.score_cross_validated_signal_v216(
            signal,
            _safe_observation_fallback,
            original_v215_score,
        )
        scored["admissionEligibleV216"] = True
        return scored

    def score_cross_validated_compat(
        signal: Dict[str, Any],
        fallback: Any,
    ) -> Dict[str, Any]:
        cross = _cross_validation(signal)
        maturity = cross.get("observationMaturity") if isinstance(cross.get("observationMaturity"), dict) else {}
        if not maturity:
            return original_v215_score(signal, fallback)
        return core.score_cross_validated_signal_v216(
            signal,
            fallback,
            original_v215_score,
        )

    def admission_station_v216(
        data_version: str | None,
        *,
        user_id: str | None = None,
        max_signals: int = 160,
        min_admitted: int = 0,
        max_admitted: int | None = None,
        force: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        del min_admitted, force, kwargs
        limits = admission.normalize_admission_limits(
            max_signals=max_signals,
            min_admitted=0,
            max_admitted=max_admitted,
        )
        signal_snapshot = admission.materialize_product_signal_snapshot(
            data_version=data_version,
            user_id=user_id,
            force=True,
        )
        if admission._baseline_only(signal_snapshot):
            baseline_seed = admission._seed_baseline_items(data_version, signal_snapshot)
            return {
                "version": V216_VERSION,
                "governanceVersion": admission.AGENT_PIPELINE_GOVERNANCE_VERSION,
                "stationId": "product_signal_admission_station",
                "dataVersion": data_version,
                "baselineOnly": True,
                "baselineGate": "closed_before_signal_engine",
                "baselineReason": admission._baseline_reason(signal_snapshot),
                "productSnapshotCount": signal_snapshot.get("productSnapshotCount", 0),
                "fullSignalCount": 0,
                "generatedSignalCount": 0,
                "candidateProductCount": 0,
                "admittedSignalCount": 0,
                "observedSignalCount": 0,
                "agent1PendingItemCount": 0,
                "observedItemCount": 0,
                "baselineItemSeed": baseline_seed,
                "admissionLimits": limits,
                "outputRef": f"baseline_only_product_signal_admission:{data_version or 'latest'}",
                "rule": "A product with no comparable observation remains baseline only.",
            }

        admission._reset_current_version_admissions(data_version)
        generated = admission.generate_signal_pool(
            data_version=data_version,
            max_signals=limits["maxSignals"],
            user_id=user_id,
            signal_snapshot=signal_snapshot,
        )
        signals = (
            admission.list_signals(
                data_version=data_version,
                limit=limits["maxSignals"],
            ).get("signals")
            or []
        )
        scored = [
            {"signal": signal, "score": admission.score_signal(signal)}
            for signal in signals
        ]
        qualified = [
            item
            for item in scored
            if item["score"].get("level") in {"strong_candidate", "medium_candidate"}
            and item["score"].get("admissionEligibleV216") is not False
        ]
        explicit_max = max_admitted if max_admitted not in {None, 0} else None
        selected, budget_meta = core.select_agent_candidates(
            qualified,
            total_signal_count=len(signals),
            external_max=explicit_max,
        )
        selected_ids = {
            item["signal"].get("signalId")
            for item in selected
        }

        admitted_rows: List[Dict[str, Any]] = []
        observed_rows: List[Dict[str, Any]] = []
        for item in scored:
            signal = item["signal"]
            score = item["score"]
            key = core.aggregation_key(item)
            patch = {
                "admissionVersion": V216_VERSION,
                "governanceVersion": admission.AGENT_PIPELINE_GOVERNANCE_VERSION,
                "admissionScore": score,
                "previousStatusBeforeAdmission": signal.get("status"),
                "softGateOutputRef": f"product_signal_admission:{data_version or 'latest'}",
                "aggregationKey": key,
                "agentBudget": budget_meta,
                "experimentPolicy": score.get("experimentPolicy"),
                "evidenceMaturity": score.get("evidenceMaturity"),
            }
            summary = {
                "signalId": signal.get("signalId"),
                "productId": signal.get("entityId") or signal.get("productId"),
                "storeId": signal.get("storeId"),
                "aggregationKey": key,
                **score,
            }
            if signal.get("signalId") in selected_ids:
                admission.update_signal_status(
                    signal.get("signalId"),
                    admission.ADMITTED_STATUS,
                    patch,
                )
                admitted_rows.append(summary)
            else:
                admission.update_signal_status(
                    signal.get("signalId"),
                    admission.OBSERVED_STATUS,
                    patch,
                )
                observed_rows.append(summary)

        item_seed = admission.seed_agent1_pipeline_items_from_admission(
            data_version,
            admitted=admitted_rows,
            observed=observed_rows,
            source="product_signal_admission_v21_6",
        )
        by_level: Dict[str, int] = defaultdict(int)
        by_maturity: Dict[str, int] = defaultdict(int)
        by_experiment: Dict[str, int] = defaultdict(int)
        for item in scored:
            score = item["score"]
            by_level[str(score.get("level"))] += 1
            by_maturity[str(score.get("evidenceMaturity"))] += 1
            policy = score.get("experimentPolicy") if isinstance(score.get("experimentPolicy"), dict) else {}
            by_experiment[str(policy.get("experimentMode") or "none")] += 1

        return {
            "version": V216_VERSION,
            "governanceVersion": admission.AGENT_PIPELINE_GOVERNANCE_VERSION,
            "stationId": "product_signal_admission_station",
            "dataVersion": data_version,
            "baselineOnly": False,
            "baselineGate": "open_has_comparable_observations",
            "baselineReason": admission._baseline_reason(signal_snapshot),
            "fullSignalCount": len(signals),
            "generatedSignalCount": generated.get("signalCount"),
            "qualifiedSignalCount": len(qualified),
            "candidateProductCount": len(admitted_rows),
            "admittedSignalCount": len(admitted_rows),
            "observedSignalCount": len(observed_rows),
            "pipelineItemSeed": item_seed,
            "agent1PendingItemCount": item_seed.get("seededAgent1PendingCount"),
            "observedItemCount": item_seed.get("observedItemCount"),
            "byAdmissionLevel": dict(by_level),
            "byEvidenceMaturity": dict(by_maturity),
            "byExperimentMode": dict(by_experiment),
            "agentBudget": budget_meta,
            "aggregationPolicy": {
                "key": "store+hypothesis+actionFamily+maturity",
                "representativeFirst": True,
                "maxRepresentativesPerGroup": 2,
                "signalsDiscarded": False,
            },
            "artificialMinimumApplied": False,
            "fixedEightItemCapApplied": False,
            "admitted": admitted_rows,
            "observedTop": observed_rows[:20],
            "admissionRef": f"product_signal_admission:{data_version or 'latest'}",
            "outputRef": f"product_signal_admission:{data_version or 'latest'}",
            "rule": (
                "V21.6 admits representative, directly actionable experiments based "
                "on real product-metric observation maturity; deferred signals remain observed."
            ),
        }

    v215.build_cross_validation = build_cross_validation_v216
    v215.score_cross_validated_signal = score_cross_validated_compat
    admission.score_signal = score_v216
    admission.product_signal_admission_station_v197 = admission_station_v216
    admission.PRODUCT_SIGNAL_ADMISSION_VERSION = V216_VERSION
    admission._V216_INSTALLED = True


__all__ = ["V216_VERSION", "install_v216_runtime"]
