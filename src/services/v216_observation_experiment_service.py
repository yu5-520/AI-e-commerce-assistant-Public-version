"""V21.6 per-product observation maturity and experiment admission.

Reports are transport containers. V21.6 derives maturity from real, comparable
observations for each product/metric/hypothesis, then maps immature evidence to
small isolated experiments instead of verification chores. Agent1 receives a
bounded, diverse set of representative operating events; the remaining signals
stay persisted as observations and are not discarded.
"""

from __future__ import annotations

from collections import defaultdict
from math import ceil
from typing import Any, Dict, Iterable, List, Sequence

V216_VERSION = "21.6.0"
MATURITY_CONTRACT = "productMetricObservationMaturity.v1"
EXPERIMENT_CONTRACT = "operatingExperimentPermission.v1"

MATURITY_RANK = {
    "M0_baseline": 0,
    "M1_pair_delta": 1,
    "M2_direction_confirmed": 2,
    "M3_short_trend": 3,
    "M4_stable_trend": 4,
    "M5_structural": 5,
}

HYPOTHESIS_METRICS: Dict[str, List[str]] = {
    "paid_efficiency_decline": [
        "roi",
        "adSpend",
        "paidVisitors",
        "conversionRate",
        "paymentAmount",
    ],
    "click_acceptance_decline": [
        "clickRate",
        "organicVisitors",
        "visitorCount",
        "conversionRate",
        "paymentAmount",
    ],
    "conversion_decline": [
        "conversionRate",
        "clickRate",
        "visitorCount",
        "paymentAmount",
        "roi",
    ],
    "service_risk": [
        "refundRate",
        "afterSalesRate",
        "conversionRate",
        "paymentAmount",
        "roi",
    ],
    "growth_opportunity": [
        "paymentAmount",
        "gmv",
        "organicVisitors",
        "paidVisitors",
        "clickRate",
        "conversionRate",
        "roi",
    ],
}

ACTION_FAMILY_BY_HYPOTHESIS = {
    "paid_efficiency_decline": "roas_plan_test",
    "click_acceptance_decline": "title_image_test",
    "conversion_decline": "conversion_page_test",
    "service_risk": "service_process_test",
    "growth_opportunity": "growth_scale_test",
    "no_operating_event": "observation_only",
}


def _metric_meta(item: Dict[str, Any], code: str) -> Dict[str, Any]:
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    metric_meta = metric.get("metricMeta") if isinstance(metric.get("metricMeta"), dict) else {}
    code_meta = metric_meta.get(code) if isinstance(metric_meta.get(code), dict) else {}
    return {
        "periodType": code_meta.get("periodType") or metric.get("periodType") or item.get("periodType") or "daily",
        "definitionVersion": (
            code_meta.get("metricDefinitionVersion")
            or metric.get("metricDefinitionVersion")
            or item.get("metricDefinitionVersion")
            or "default"
        ),
        "carriedForward": bool(
            code_meta.get("carriedForward")
            or metric.get("carriedForward")
            or item.get("carriedForward")
        ),
        "observationStatus": str(
            code_meta.get("observationStatus")
            or metric.get("observationStatus")
            or item.get("observationStatus")
            or "observed"
        ),
    }


def _observation_key(v215: Any, item: Dict[str, Any]) -> tuple[str, str] | None:
    date = v215._parse_date(item)
    if date is not None:
        return "effective_date", date.date().isoformat()
    metric = item.get("metricSnapshot") if isinstance(item.get("metricSnapshot"), dict) else {}
    version = (
        item.get("dataVersion")
        or item.get("businessDataVersion")
        or metric.get("dataVersion")
        or metric.get("businessDataVersion")
    )
    if version:
        return "snapshot_fallback", str(version)
    return None


def metric_observation_state(
    v215: Any,
    current_item: Dict[str, Any],
    history_items: Sequence[Dict[str, Any]],
    metric_code: str,
) -> Dict[str, Any]:
    points: Dict[tuple[str, str, str, str], Dict[str, Any]] = {}
    ignored_carried = 0
    ignored_missing = 0
    for item in [*reversed(list(history_items)), current_item]:
        value = v215._metric(item, metric_code)
        if value is None:
            continue
        meta = _metric_meta(item, metric_code)
        if meta["carriedForward"] or meta["observationStatus"] in {
            "not_observed",
            "missing",
            "carried_forward",
        }:
            ignored_carried += 1
            continue
        key = _observation_key(v215, item)
        if key is None:
            ignored_missing += 1
            continue
        point_key = (
            key[0],
            key[1],
            str(meta["periodType"]),
            str(meta["definitionVersion"]),
        )
        points[point_key] = {
            "timeBasis": key[0],
            "effectiveKey": key[1],
            "periodType": meta["periodType"],
            "metricDefinitionVersion": meta["definitionVersion"],
            "value": value,
        }

    period_versions = {
        (point["periodType"], point["metricDefinitionVersion"])
        for point in points.values()
    }
    comparable = len(period_versions) <= 1
    effective_points = len(points) if comparable else 0
    business_dated = sum(
        1 for point in points.values() if point["timeBasis"] == "effective_date"
    )
    fallback_points = effective_points - business_dated
    return {
        "metricCode": metric_code,
        "comparableObservationCount": effective_points,
        "uniqueEffectiveDateCount": business_dated,
        "snapshotFallbackCount": fallback_points,
        "freshObservationCount": effective_points,
        "carriedForwardExcludedCount": ignored_carried,
        "undatedExcludedCount": ignored_missing,
        "periodDefinitionConsistent": comparable,
        "timeBasis": (
            "effective_date"
            if effective_points and fallback_points == 0
            else "mixed_with_snapshot_fallback"
            if effective_points
            else "unavailable"
        ),
        "points": list(points.values())[-10:],
    }


def maturity_from_count(count: int) -> str:
    if count <= 1:
        return "M0_baseline"
    if count == 2:
        return "M1_pair_delta"
    if count <= 4:
        return "M2_direction_confirmed"
    if count <= 7:
        return "M3_short_trend"
    if count <= 14:
        return "M4_stable_trend"
    return "M5_structural"


def _evidence_metric_codes(decision: Dict[str, Any]) -> List[str]:
    values: List[str] = []
    primary = decision.get("primaryEvidence") if isinstance(decision.get("primaryEvidence"), dict) else {}
    if primary.get("metricCode"):
        values.append(str(primary["metricCode"]))
    for item in decision.get("relatedEvidence") or []:
        if isinstance(item, dict) and item.get("metricCode"):
            values.append(str(item["metricCode"]))
    return list(dict.fromkeys(values))


def build_observation_maturity(
    v215: Any,
    current_item: Dict[str, Any],
    history_items: Sequence[Dict[str, Any]],
    cross: Dict[str, Any],
) -> Dict[str, Any]:
    decision = cross.get("decision") if isinstance(cross.get("decision"), dict) else {}
    hypothesis = str(decision.get("hypothesisCode") or "no_operating_event")
    relevant = _evidence_metric_codes(decision) or HYPOTHESIS_METRICS.get(hypothesis, [])
    states = {
        code: metric_observation_state(v215, current_item, history_items, code)
        for code in relevant
    }
    primary_code = None
    primary = decision.get("primaryEvidence") if isinstance(decision.get("primaryEvidence"), dict) else {}
    if primary.get("metricCode"):
        primary_code = str(primary["metricCode"])

    aligned_codes: List[str] = []
    if primary_code and primary_code in states:
        aligned_codes.append(primary_code)
    for code in relevant:
        if code == primary_code:
            continue
        if states.get(code, {}).get("comparableObservationCount", 0) >= 2:
            aligned_codes.append(code)
        if len(aligned_codes) >= 3:
            break

    counts = [
        int(states[code]["comparableObservationCount"])
        for code in aligned_codes
        if code in states
    ]
    aligned_count = min(counts) if counts else 0
    maturity = maturity_from_count(aligned_count)
    observed_metric_count = sum(
        1 for state in states.values() if state["comparableObservationCount"] > 0
    )
    coverage = (
        observed_metric_count / len(states)
        if states
        else 0.0
    )
    return {
        "version": V216_VERSION,
        "contract": MATURITY_CONTRACT,
        "hypothesisCode": hypothesis,
        "maturity": maturity,
        "maturityRank": MATURITY_RANK[maturity],
        "alignedObservationCount": aligned_count,
        "alignedMetricCodes": aligned_codes,
        "relevantMetricCount": len(states),
        "observedMetricCount": observed_metric_count,
        "coverageRatio": round(coverage, 4),
        "metricObservationState": states,
        "reportCountUsed": False,
        "sourceVersionCountUsedForMaturity": False,
        "rule": (
            "Maturity uses unique comparable product-metric observations. Upload "
            "count, repeated business dates and carried-forward values do not mature evidence."
        ),
    }


def experiment_policy(hypothesis: str, maturity: str) -> Dict[str, Any]:
    family = ACTION_FAMILY_BY_HYPOTHESIS.get(hypothesis, "isolated_operating_test")
    rank = MATURITY_RANK.get(maturity, 0)
    if rank <= 0:
        return {
            "version": V216_VERSION,
            "contract": EXPERIMENT_CONTRACT,
            "experimentMode": "baseline_only",
            "actionFamily": "observation_only",
            "actionIntensity": "L0",
            "targetObject": "none",
            "trafficShareCeiling": 0.0,
            "budgetChangeCeiling": 0.0,
            "durationHours": 0,
            "mainlineMutationAllowed": False,
            "allowed": False,
        }
    if rank == 1:
        target = "new_ad_plan" if family == "roas_plan_test" else "new_test_link"
        return {
            "version": V216_VERSION,
            "contract": EXPERIMENT_CONTRACT,
            "experimentMode": "isolated_test",
            "actionFamily": family,
            "actionIntensity": "L2",
            "targetObject": target,
            "trafficShareCeiling": 0.10,
            "budgetChangeCeiling": 0.10,
            "durationHours": 72 if family != "roas_plan_test" else 48,
            "mainlineMutationAllowed": False,
            "singleVariablePreferred": True,
            "rollbackRequired": True,
            "allowed": True,
        }
    if rank == 2:
        return {
            "version": V216_VERSION,
            "contract": EXPERIMENT_CONTRACT,
            "experimentMode": "directional_test",
            "actionFamily": family,
            "actionIntensity": "L2",
            "targetObject": "isolated_variant_group",
            "trafficShareCeiling": 0.20,
            "budgetChangeCeiling": 0.15,
            "durationHours": 72,
            "mainlineMutationAllowed": False,
            "rollbackRequired": True,
            "allowed": True,
        }
    if rank == 3:
        return {
            "version": V216_VERSION,
            "contract": EXPERIMENT_CONTRACT,
            "experimentMode": "formal_optimization_test",
            "actionFamily": family,
            "actionIntensity": "L3",
            "targetObject": "secondary_link_or_plan",
            "trafficShareCeiling": 0.30,
            "budgetChangeCeiling": 0.20,
            "durationHours": 168,
            "mainlineMutationAllowed": False,
            "promotionConditionRequired": True,
            "rollbackRequired": True,
            "allowed": True,
        }
    if rank == 4:
        return {
            "version": V216_VERSION,
            "contract": EXPERIMENT_CONTRACT,
            "experimentMode": "mainline_optimization",
            "actionFamily": family,
            "actionIntensity": "L3",
            "targetObject": "main_link_or_main_plan",
            "trafficShareCeiling": 0.50,
            "budgetChangeCeiling": 0.30,
            "durationHours": 336,
            "mainlineMutationAllowed": True,
            "rollbackRequired": True,
            "allowed": True,
        }
    return {
        "version": V216_VERSION,
        "contract": EXPERIMENT_CONTRACT,
        "experimentMode": "structural_optimization",
        "actionFamily": family,
        "actionIntensity": "L4",
        "targetObject": "store_or_product_family",
        "trafficShareCeiling": 1.0,
        "budgetChangeCeiling": 0.50,
        "durationHours": 720,
        "mainlineMutationAllowed": True,
        "rollbackRequired": True,
        "allowed": True,
    }


def score_cross_validated_signal_v216(
    signal: Dict[str, Any],
    fallback: Any,
    v215_score: Any,
) -> Dict[str, Any]:
    base = v215_score(signal, fallback)
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else signal
    cross = payload.get("crossValidation") if isinstance(payload, dict) and isinstance(payload.get("crossValidation"), dict) else {}
    maturity = cross.get("observationMaturity") if isinstance(cross.get("observationMaturity"), dict) else {}
    decision = cross.get("decision") if isinstance(cross.get("decision"), dict) else {}
    maturity_code = str(maturity.get("maturity") or "M0_baseline")
    rank = MATURITY_RANK.get(maturity_code, 0)
    confidence = int(decision.get("confidence") or 0)
    severity = int(decision.get("severity") or 0)
    impact = int(decision.get("businessImpact") or 0)
    urgency = int(decision.get("urgency") or 0)
    status = str(decision.get("status") or "insufficient_evidence")
    evidence_groups = len(decision.get("independentEvidenceGroups") or [])
    hypothesis = str(decision.get("hypothesisCode") or "no_operating_event")
    policy = cross.get("experimentPolicy") if isinstance(cross.get("experimentPolicy"), dict) else experiment_policy(hypothesis, maturity_code)

    extreme = bool(
        (hypothesis == "service_risk" and severity >= 85 and confidence >= 60)
        or (hypothesis == "paid_efficiency_decline" and severity >= 90 and confidence >= 60)
    )
    admissible = False
    if status == "confirmed" and policy.get("allowed"):
        if rank == 1:
            admissible = confidence >= 65 and severity >= 45 and evidence_groups >= 2
        elif rank == 2:
            admissible = confidence >= 60 and severity >= 40 and evidence_groups >= 2
        elif rank >= 3:
            admissible = confidence >= 55 and evidence_groups >= 2
    admissible = admissible or extreme

    composite = round(
        int(base.get("score") or 0) * 0.45
        + confidence * 0.20
        + severity * 0.15
        + impact * 0.10
        + urgency * 0.10
    )
    if admissible:
        level = "strong_candidate" if composite >= 75 and confidence >= 70 else "medium_candidate"
        score = max(70 if level == "strong_candidate" else 45, min(100, composite))
    else:
        level = "weak_observation" if composite >= 25 else "noise_or_baseline"
        score = min(44, max(0, composite))

    priority = round(
        score * 0.45
        + confidence * 0.20
        + impact * 0.15
        + urgency * 0.10
        + rank * 2
    )
    return {
        **base,
        "score": int(score),
        "level": level,
        "evidenceMaturity": maturity_code,
        "maturityRank": rank,
        "alignedObservationCount": int(maturity.get("alignedObservationCount") or 0),
        "coverageRatio": float(maturity.get("coverageRatio") or 0.0),
        "experimentPolicy": policy,
        "hypothesisCode": hypothesis,
        "admissionPriority": int(priority),
        "extremeRiskBypass": extreme,
        "softGateRule": "v21_6_observation_maturity_and_experiment_permission",
        "reasons": [
            *(base.get("reasons") or []),
            f"evidence_maturity={maturity_code}",
            f"aligned_observations={int(maturity.get('alignedObservationCount') or 0)}",
            f"experiment_mode={policy.get('experimentMode')}",
        ][-12:],
    }


def aggregation_key(item: Dict[str, Any]) -> str:
    signal = item.get("signal") if isinstance(item.get("signal"), dict) else {}
    score = item.get("score") if isinstance(item.get("score"), dict) else {}
    policy = score.get("experimentPolicy") if isinstance(score.get("experimentPolicy"), dict) else {}
    return "::".join(
        [
            str(signal.get("storeId") or "GLOBAL"),
            str(score.get("hypothesisCode") or "none"),
            str(policy.get("actionFamily") or "none"),
            str(score.get("evidenceMaturity") or "M0_baseline"),
        ]
    )


def agent_budget(total_signal_count: int, qualified: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ranks = [int(item.get("score", {}).get("maturityRank") or 0) for item in qualified]
    average_rank = sum(ranks) / len(ranks) if ranks else 0.0
    ratio = 0.20 if average_rank <= 1.5 else 0.25 if average_rank < 3 else 0.30
    budget = max(1, ceil(max(1, total_signal_count) * ratio)) if qualified else 0
    extreme_count = sum(1 for item in qualified if item.get("score", {}).get("extremeRiskBypass"))
    return {
        "version": V216_VERSION,
        "policy": "dynamic_representative_budget",
        "totalSignalCount": total_signal_count,
        "qualifiedSignalCount": len(qualified),
        "averageMaturityRank": round(average_rank, 3),
        "baseRatio": ratio,
        "baseBudget": budget,
        "extremeRiskCount": extreme_count,
        "effectiveBudget": max(budget, extreme_count),
        "hardBusinessCap": False,
    }


def select_agent_candidates(
    qualified: Sequence[Dict[str, Any]],
    *,
    total_signal_count: int,
    external_max: int | None = None,
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    budget_meta = agent_budget(total_signal_count, qualified)
    budget = int(budget_meta["effectiveBudget"])
    if external_max is not None and external_max > 0:
        budget = min(budget, int(external_max))
    ordered = sorted(
        qualified,
        key=lambda item: (
            int(item.get("score", {}).get("extremeRiskBypass") or 0),
            int(item.get("score", {}).get("admissionPriority") or 0),
            int(item.get("score", {}).get("score") or 0),
            str(item.get("signal", {}).get("entityId") or ""),
        ),
        reverse=True,
    )
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in ordered:
        groups[aggregation_key(item)].append(item)

    selected: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()
    # First pass keeps one representative from every event/action group.
    representatives = sorted(
        [values[0] for values in groups.values()],
        key=lambda item: int(item.get("score", {}).get("admissionPriority") or 0),
        reverse=True,
    )
    for item in representatives:
        if len(selected) >= budget and not item.get("score", {}).get("extremeRiskBypass"):
            continue
        signal_id = str(item.get("signal", {}).get("signalId") or "")
        if signal_id and signal_id not in selected_ids:
            selected.append(item)
            selected_ids.add(signal_id)

    # Second pass fills remaining budget, with at most two products per group.
    group_selected: Dict[str, int] = defaultdict(int)
    for item in selected:
        group_selected[aggregation_key(item)] += 1
    for item in ordered:
        if len(selected) >= budget:
            break
        signal_id = str(item.get("signal", {}).get("signalId") or "")
        key = aggregation_key(item)
        if not signal_id or signal_id in selected_ids or group_selected[key] >= 2:
            continue
        selected.append(item)
        selected_ids.add(signal_id)
        group_selected[key] += 1

    budget_meta.update(
        {
            "effectiveBudget": budget,
            "aggregationGroupCount": len(groups),
            "selectedRepresentativeCount": len(selected),
            "deferredQualifiedCount": max(0, len(qualified) - len(selected)),
            "maxRepresentativesPerGroup": 2,
        }
    )
    return selected, budget_meta


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
        maturity = build_observation_maturity(
            v215,
            current_item,
            history_items,
            cross,
        )
        decision = cross.get("decision") if isinstance(cross.get("decision"), dict) else {}
        hypothesis = str(decision.get("hypothesisCode") or "no_operating_event")
        policy = experiment_policy(hypothesis, maturity["maturity"])
        cross["observationMaturity"] = maturity
        cross["experimentPolicy"] = policy
        cross["maturityVersion"] = V216_VERSION
        cross["reportCountAdmissionRemoved"] = True
        cross["verificationTaskFamilyRemoved"] = True
        return cross

    def score_v216(signal: Dict[str, Any]) -> Dict[str, Any]:
        return score_cross_validated_signal_v216(
            signal,
            admission.score_signal_original_v216,
            original_v215_score,
        )

    original_admission_station = admission.product_signal_admission_station_v197
    admission.score_signal_original_v216 = admission.score_signal

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
        del min_admitted, force, kwargs, original_admission_station
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
            result = admission._seed_baseline_items(data_version, signal_snapshot)
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
                "baselineItemSeed": result,
                "admissionLimits": limits,
                "outputRef": f"baseline_only_product_signal_admission:{data_version or 'latest'}",
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
        ]
        explicit_max = max_admitted if max_admitted not in {None, 0} else None
        selected, budget_meta = select_agent_candidates(
            qualified,
            total_signal_count=len(signals),
            external_max=explicit_max,
        )
        selected_ids = {
            item["signal"].get("signalId") for item in selected
        }

        admitted_rows: List[Dict[str, Any]] = []
        observed_rows: List[Dict[str, Any]] = []
        for item in scored:
            signal = item["signal"]
            score = item["score"]
            key = aggregation_key(item)
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
                "on product-metric observation maturity; deferred signals remain observed."
            ),
        }

    v215.build_cross_validation = build_cross_validation_v216
    v215.score_cross_validated_signal = lambda signal, fallback: score_cross_validated_signal_v216(
        signal,
        fallback,
        original_v215_score,
    )
    admission.score_signal = score_v216
    admission.product_signal_admission_station_v197 = admission_station_v216
    admission.PRODUCT_SIGNAL_ADMISSION_VERSION = V216_VERSION
    admission._V216_INSTALLED = True


__all__ = [
    "V216_VERSION",
    "MATURITY_CONTRACT",
    "EXPERIMENT_CONTRACT",
    "metric_observation_state",
    "build_observation_maturity",
    "experiment_policy",
    "score_cross_validated_signal_v216",
    "agent_budget",
    "select_agent_candidates",
    "install_v216_runtime",
]
