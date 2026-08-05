from __future__ import annotations

from src.services import v215_report_batch_evidence_service as v215
from src.services import v216_observation_experiment_service as v216


def _product(date: str, product_id: str = "P1", **metrics: float) -> dict:
    return {
        "objectId": f"STORE::{product_id}::NO-SKU",
        "productId": product_id,
        "storeId": "STORE",
        "metricDate": date,
        "dataVersion": f"DV-{date}",
        "metricSnapshot": {
            "metricDate": date,
            **metrics,
        },
    }


def test_two_real_dates_create_isolated_experiment_not_verification_task() -> None:
    previous = _product(
        "2026-07-01",
        clickRate=0.050,
        organicVisitors=1000,
        conversionRate=0.040,
        paymentAmount=10000,
    )
    current = _product(
        "2026-07-02",
        clickRate=0.040,
        organicVisitors=820,
        conversionRate=0.034,
        paymentAmount=8800,
    )

    cross = v215.build_cross_validation(current, [previous])
    maturity = cross["observationMaturity"]
    policy = cross["experimentPolicy"]

    assert maturity["maturity"] == "M1_pair_delta"
    assert maturity["alignedObservationCount"] == 2
    assert policy["experimentMode"] == "isolated_test"
    assert policy["mainlineMutationAllowed"] is False
    assert policy["trafficShareCeiling"] <= 0.10
    assert policy["budgetChangeCeiling"] <= 0.10
    assert "verify" not in str(policy).lower()
    assert "review" not in str(policy).lower()
    assert "核查" not in str(policy)
    assert "确认信息" not in str(policy)


def test_duplicate_business_date_does_not_increase_maturity() -> None:
    first = _product("2026-07-01", clickRate=0.050)
    repeated = _product("2026-07-01", clickRate=0.048)
    current = _product("2026-07-02", clickRate=0.040)

    state = v216.metric_observation_state(
        v215,
        current,
        [first, repeated],
        "clickRate",
    )

    assert state["comparableObservationCount"] == 2
    assert state["uniqueEffectiveDateCount"] == 2


def test_metric_maturity_is_independent_for_async_sources() -> None:
    history = [
        _product("2026-07-01", roi=2.0, adSpend=100),
        _product("2026-07-02", roi=1.8, adSpend=110),
        _product("2026-07-03", roi=1.6, adSpend=120),
    ]
    current = _product(
        "2026-07-04",
        roi=1.4,
        adSpend=130,
        refundRate=0.05,
    )

    roi_state = v216.metric_observation_state(v215, current, history, "roi")
    refund_state = v216.metric_observation_state(
        v215,
        current,
        history,
        "refundRate",
    )

    assert roi_state["comparableObservationCount"] == 4
    assert v216.maturity_from_count(roi_state["comparableObservationCount"]) == "M2_direction_confirmed"
    assert refund_state["comparableObservationCount"] == 1
    assert v216.maturity_from_count(refund_state["comparableObservationCount"]) == "M0_baseline"


def _qualified(index: int, group: str = "same") -> dict:
    return {
        "signal": {
            "signalId": f"S{index}",
            "entityId": f"P{index}",
            "storeId": "STORE",
        },
        "score": {
            "score": 60 + index % 10,
            "level": "medium_candidate",
            "maturityRank": 1,
            "evidenceMaturity": "M1_pair_delta",
            "hypothesisCode": (
                "click_acceptance_decline"
                if group == "same"
                else f"hypothesis_{index % 4}"
            ),
            "admissionPriority": 70 + index,
            "extremeRiskBypass": False,
            "experimentPolicy": {
                "actionFamily": (
                    "title_image_test"
                    if group == "same"
                    else f"action_{index % 4}"
                )
            },
        },
    }


def test_thirty_pair_delta_signals_use_dynamic_representative_budget() -> None:
    qualified = [_qualified(index) for index in range(30)]
    selected, budget = v216.select_agent_candidates(
        qualified,
        total_signal_count=30,
    )

    assert budget["baseRatio"] == 0.20
    assert budget["baseBudget"] == 6
    assert budget["hardBusinessCap"] is False
    assert budget["aggregationGroupCount"] == 1
    assert len(selected) == 2
    assert budget["deferredQualifiedCount"] == 28


def test_diverse_groups_fill_budget_without_discarding_signals() -> None:
    qualified = [_qualified(index, group="diverse") for index in range(30)]
    selected, budget = v216.select_agent_candidates(
        qualified,
        total_signal_count=30,
    )

    assert 4 <= len(selected) <= 6
    assert budget["effectiveBudget"] == 6
    assert budget["aggregationGroupCount"] == 4
    assert budget["deferredQualifiedCount"] == 30 - len(selected)
