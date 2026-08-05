from __future__ import annotations

import src  # noqa: F401

from src.services import agent2_action_plan_core_v20_service as agent2
from src.services.v2172_inventory_action_guard_service import (
    INVENTORY_ACTION_GUARD_VERSION,
    align_agent2_raw_to_action_family,
    apply_attention_headline,
    classify_inventory_failure,
    guard_agent1_judgment,
)


def _signal(
    *,
    source_count: int = 2,
    hypothesis: str = "growth_opportunity",
    status: str = "confirmed",
    roi_delta: float = 0.0,
    inventory_delta: float = -0.12,
) -> dict:
    return {
        "payload": {
            "productId": "P10004",
            "storeId": "TB-SH-001",
            "crossValidation": {
                "sourceVersionCount": source_count,
                "decision": {
                    "hypothesisCode": hypothesis,
                    "status": status,
                    "primaryEvidence": {
                        "metricCode": "roi" if hypothesis == "paid_efficiency_decline" else "paymentAmount",
                        "direction": "down" if hypothesis == "paid_efficiency_decline" else "up",
                        "magnitude": abs(roi_delta) if hypothesis == "paid_efficiency_decline" else 0.24,
                    },
                },
                "timeSeriesFeatures": {
                    "roi": {
                        "previousDelta": roi_delta,
                        "slope5": roi_delta,
                    },
                    "inventory": {
                        "previousDelta": inventory_delta,
                        "slope5": inventory_delta,
                    },
                },
                "changedMetrics": ["inventory"],
                "abnormalMetrics": ["inventory"],
                "observationMaturity": {
                    "maturity": "M1_pair_delta",
                },
                "experimentPolicy": {
                    "experimentMode": "isolated_test",
                    "targetObject": "new_ad_plan",
                    "operationScope": "isolated_new_plan",
                    "trafficShareCeiling": 0.10,
                    "budgetChangeCeiling": 0.10,
                    "durationHours": 48,
                    "mainlineMutationAllowed": False,
                    "allowed": True,
                },
            },
        }
    }


def _judgment(signal: dict, family: str = "roas_guard") -> dict:
    return {
        "productId": "P10004",
        "storeId": "TB-SH-001",
        "signalId": "SIG-1",
        "decisionHint": "risk_candidate",
        "finding": "库存下降，建议限制投放",
        "selectedOperatingRoute": "paid_traffic_efficiency",
        "selectedActionFamilyHint": family,
        "capacityConstraints": ["库存下降12%"],
        "companyHooks": ["催仓储补货"],
        "routeLock": {
            "locked": True,
            "selectedOperatingRoute": "paid_traffic_efficiency",
        },
        "actionFamilyLock": {
            "locked": True,
            "selectedActionFamily": family,
            "forbiddenOverride": True,
        },
        "agent1OperatingJudgment": {
            "selectedOperatingRoute": "paid_traffic_efficiency",
            "selectedActionFamily": family,
        },
        "signal": signal,
    }


def _package(signal: dict) -> dict:
    cross = signal["payload"]["crossValidation"]
    return {
        "productId": "P10004",
        "skuId": "SKU10004-A",
        "storeId": "TB-SH-001",
        "actionFamily": "roas_guard",
        "crossValidation": cross,
        "experimentPolicy": cross["experimentPolicy"],
    }


def test_first_report_action_is_forced_back_to_observation() -> None:
    guarded = guard_agent1_judgment(
        _judgment(
            _signal(
                source_count=1,
                hypothesis="paid_efficiency_decline",
                roi_delta=-0.20,
            )
        )
    )

    assert guarded["decisionHint"] == "observe_only"
    assert guarded["selectedActionFamilyHint"] is None
    assert guarded["observationDisposition"] == "observed_soft_gate"
    assert guarded["taskAdmissionAllowed"] is False
    assert guarded["inventoryActionGuard"]["baselineOnly"] is True


def test_inventory_only_stable_roas_never_enters_roas_guard() -> None:
    guarded = guard_agent1_judgment(
        _judgment(
            _signal(
                hypothesis="growth_opportunity",
                roi_delta=0.0,
                inventory_delta=-0.121875,
            )
        )
    )

    assert guarded["decisionHint"] == "observe_only"
    assert guarded["selectedOperatingRoute"] == "observe"
    assert guarded["selectedActionFamilyHint"] is None
    assert guarded["inventoryActionGuard"]["inventoryOnly"] is True
    assert (
        guarded["inventoryActionGuard"]["reason"]
        == "roas_guard_without_paid_efficiency_evidence"
    )


def test_confirmed_paid_efficiency_decline_keeps_roas_guard() -> None:
    guarded = guard_agent1_judgment(
        _judgment(
            _signal(
                hypothesis="paid_efficiency_decline",
                status="confirmed",
                roi_delta=-0.18,
                inventory_delta=-0.10,
            )
        )
    )

    assert guarded["selectedActionFamilyHint"] == "roas_guard"
    assert guarded["decisionHint"] == "risk_candidate"
    assert (
        guarded["inventoryActionGuard"]["reason"]
        == "paid_efficiency_independently_supported"
    )
    assert guarded["inventoryActionGuard"]["inventoryConstraint"]["present"] is True


def test_agent2_inventory_ticket_is_rebound_to_isolated_ad_plan_and_ceiling() -> None:
    signal = _signal(
        hypothesis="paid_efficiency_decline",
        status="confirmed",
        roi_delta=-0.18,
    )
    aligned = align_agent2_raw_to_action_family(
        {
            "operationMode": "mainline_direct",
            "executionObject": {
                "targetSelector": "productId=P10004;create=inventory_coordination_ticket",
                "targetType": "inventory_ticket",
            },
            "trafficShare": 0.35,
            "budgetChangeRate": 0.25,
            "operatorActionSteps": ["a", "b", "c", "d"],
        },
        _package(signal),
    )

    assert aligned["operationMode"] == "isolated_test"
    assert "create=new_ad_plan" in aligned["executionObject"]["targetSelector"]
    assert aligned["executionObject"]["targetType"] == "ad_plan"
    assert aligned["trafficShare"] == 0.10
    assert aligned["budgetChangeRate"] == 0.10
    assert len(
        aligned["inventoryActionGuardAlignment"]["normalizations"]
    ) >= 4


def test_inventory_failure_classification_observes_without_paid_evidence() -> None:
    signal = _signal(
        hypothesis="growth_opportunity",
        roi_delta=0.0,
        inventory_delta=-0.12,
    )
    payload = {
        **signal["payload"],
        "actionFamily": "roas_guard",
        "agent2ActionPlan": {
            "executionObject": {
                "targetSelector": "productId=P10004;create=inventory_coordination_ticket"
            },
            "semanticContractMissing": [
                "inventory_cannot_directly_cut_operator_traffic"
            ],
            "experimentPermissionViolations": [
                "traffic_share_exceeds_ceiling"
            ],
        },
        "missing": [
            "inventory_cannot_directly_cut_operator_traffic",
            "agent2ActionPlan.experimentPermission.traffic_share_exceeds_ceiling",
        ],
    }

    classification = classify_inventory_failure(payload, "roas_guard")

    assert classification["matched"] is True
    assert classification["paidEfficiency"]["supported"] is False
    assert classification["disposition"] == "observe_inventory_only"


def test_live_headline_never_calls_failed_batch_completed() -> None:
    result = apply_attention_headline(
        {
            "ready": True,
            "headline": "本轮处理完成 · 观察沉淀28",
            "flowStatus": "completed",
            "summary": {
                "failed": 2,
                "running": 0,
                "queued": 0,
                "observedDeposited": 28,
            },
        }
    )

    assert result["flowStatus"] == "attention"
    assert result["headline"] == "本轮处理结束 · 观察沉淀28 · 异常2"
    assert result["processingComplete"] is False
    assert result["inventoryActionGuardVersion"] == INVENTORY_ACTION_GUARD_VERSION


def test_runtime_install_adds_prompt_and_worker_bindings() -> None:
    messages, payload = agent2._build_messages(
        "DV-1",
        [_package(_signal(hypothesis="paid_efficiency_decline", roi_delta=-0.2))],
    )

    assert "库存与投放边界" in messages[0]["content"]
    assert "inventory_coordination_ticket" in messages[0]["content"]
    assert payload["inventoryActionGuardVersion"] == INVENTORY_ACTION_GUARD_VERSION
