from __future__ import annotations

import json
from typing import Any

import src  # noqa: F401
from src.services import agent2_action_plan_core_v20_service as agent2
from src.services import pipeline_action_microbatch_v205_service as action_worker
from src.services import pipeline_sop_task_pool_v2010_service as sop_worker
from src.services.v2177_agent2_single_action_contract_service import (
    AGENT2_SINGLE_ACTION_CONTRACT_VERSION,
    active_action_contract,
    compact_sop_decision,
    metric_digest_for_family,
    sanitize_plan,
)


def _contains_exact_key(value: Any, target: str) -> bool:
    if isinstance(value, dict):
        return target in value or any(
            _contains_exact_key(child, target) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_exact_key(child, target) for child in value)
    return False


def _package(family: str = "roas_scale") -> dict:
    facts = []
    for index in range(40):
        facts.append(
            {
                "metricCode": "roi" if index % 2 == 0 else "gmv",
                "metricName": "ROI" if index % 2 == 0 else "支付金额",
                "currentValue": 2.8 + index / 100 if index % 2 == 0 else 1000 + index,
                "previousValue": 2.7 + index / 100 if index % 2 == 0 else 980 + index,
                "changeRate": 0.03,
                "identity": {"productId": "P-2177", "storeId": "S-2177"},
                "systemCodes": {"platform": "天猫", "sku": "SKU-2177"},
            }
        )
    return {
        "dataVersion": "DV-2177",
        "itemId": "PI-2177",
        "packageId": "PKG-2177",
        "productId": "P-2177",
        "storeId": "S-2177",
        "productTitle": "通勤防泼水背包",
        "selectedOperatingRoute": "paid_traffic_efficiency",
        "actionFamily": family,
        "agent1OperatingJudgment": {
            "selectedOperatingRoute": "paid_traffic_efficiency",
            "routeLock": {
                "locked": True,
                "selectedOperatingRoute": "paid_traffic_efficiency",
            },
            "actionFamilyLock": {
                "locked": True,
                "selectedActionFamily": family,
                "forbiddenOverride": True,
            },
        },
        "matrixDispatch": {
            "selectedActionFamily": family,
            "lockedByAgent1": True,
            "routeActionConsistency": "passed",
            "agent1LockMissing": False,
        },
        "actionParameterPack": {
            "status": "ready",
            "actionFamily": family,
            "currentBudget": 1172.93,
            "recommendedBudget": 1290.22,
            "recommendedBudgetUpperBound": 1301.95,
            "currentROI": 2.85,
            "safetyROI": 1.6,
            "inventoryCoordination": {
                "required": True,
                "reason": "确认补货承接",
            },
        },
        "crossValidation": {
            "experimentPolicy": {
                "actionFamily": family,
                "budgetChangeCeiling": 0.10,
                "trafficShareCeiling": 0.10,
                "durationHours": 72,
                "mainlineMutationAllowed": False,
                "operationScope": "isolated_ad_plan_test",
            }
        },
        "ragContextSnapshot": {
            "version": "20.28",
            "status": "ready",
            "actionFamily": family,
            "approvedCaseIds": [],
            "positiveExperienceCards": [],
            "negativeCases": [],
            "taskGate": False,
        },
        "metricEvidence": {
            "metricFacts": facts,
            "productMetricFacts": facts,
            "previousProductMetricSnapshot": {
                "metricFacts": facts,
                "productMetricFacts": facts,
            },
            "fieldSignals": facts,
        },
    }


def _roas_plan() -> dict:
    return {
        "packageId": "PKG-2177",
        "actionFamily": "roas_scale",
        "actionPlanStatus": "ready",
        "operationPlan": {
            "operations": [
                {
                    "operationType": "budget_update",
                    "currentValue": {"budget": 1172.93},
                    "targetValue": {"budget": 1290.22},
                }
            ]
        },
        "budgetPlan": {"currentBudget": 1172.93, "targetBudget": 1290.22},
        "creativeTestPlan": {
            "groups": [
                {"fullTitle": "错误跨动作族标题A"},
                {"fullTitle": "错误跨动作族标题B"},
            ]
        },
        "activityPlan": {"status": "unused"},
        "conversionRepairPlan": {"status": "unused"},
        "similarProductPlan": {"status": "unused"},
        "operatorActionSteps": ["一", "二", "三", "四"],
        "executionSteps": [{"step": 1}, {"step": 2}, {"step": 3}],
        "decisionBranches": [{"branch": 1}, {"branch": 2}],
        "submissionEvidence": [{"evidence": 1}, {"evidence": 2}],
        "reviewMetrics": ["ROI", "消耗"],
        "crossDepartmentActions": [{"department": "仓储"}],
    }


def test_compact_package_replaces_full_metric_evidence_with_digest() -> None:
    package = _package()
    compact = agent2._compact_package(package)
    assert "metricEvidence" not in compact
    assert compact["metricDigest"]["fullMetricEvidenceExcluded"] is True
    assert compact["metricDigest"]["current"]["currentBudget"] == 1172.93
    assert compact["metricDigest"]["current"]["currentROI"] == 2.85
    assert len(json.dumps(compact, ensure_ascii=False)) < len(
        json.dumps(package["metricEvidence"], ensure_ascii=False)
    ) / 4


def test_roas_prompt_exposes_only_roas_plan_schema() -> None:
    messages, payload = agent2._build_messages("DV-2177", [_package()])
    system = messages[0]["content"]
    user_payload = json.loads(messages[1]["content"])
    assert "budgetPlan" in system
    assert "creativeTestPlan" not in system
    assert "activityPlan" not in system
    assert not _contains_exact_key(user_payload, "metricEvidence")
    assert payload["lockedActionFamily"] == "roas_scale"
    assert payload["packages"][0]["metricDigest"]["version"] == "21.7.7"
    assert payload["packages"][0]["metricDigest"]["fullMetricEvidenceExcluded"] is True
    assert payload["packages"][0]["metricEvidenceRef"]["source"] == (
        "fact_layer_not_in_llm_context"
    )


def test_title_image_prompt_does_not_expose_budget_plan() -> None:
    package = _package("title_image_test")
    messages, payload = agent2._build_messages("DV-2177", [package])
    system = messages[0]["content"]
    assert "creativeTestPlan" in system
    assert "budgetPlan" not in system
    assert payload["lockedActionFamily"] == "title_image_test"


def test_cross_family_plans_are_discarded_and_active_contract_is_single() -> None:
    clean = sanitize_plan(_roas_plan(), _roas_plan())
    assert clean["budgetPlan"]["targetBudget"] == 1290.22
    assert clean["creativeTestPlan"] is None
    assert clean["activityPlan"] is None
    assert clean["conversionRepairPlan"] is None
    assert clean["similarProductPlan"] is None
    assert set(clean["discardedCrossFamilyFields"]) == {
        "creativeTestPlan",
        "activityPlan",
        "conversionRepairPlan",
        "similarProductPlan",
    }
    contract = clean["activeActionContract"]
    assert contract["activeActionFamily"] == "roas_scale"
    assert contract["activeFamilyPlan"]["targetBudget"] == 1290.22
    assert contract["activeSopPlan"]["operatorActionSteps"] == ["一", "二", "三", "四"]


def test_sop_decision_keeps_one_agent2_plan_copy_and_active_contract() -> None:
    plan = sanitize_plan(_roas_plan(), _roas_plan())
    decision = compact_sop_decision(
        {
            "packageId": "PKG-2177",
            "agent2ActionPlan": plan,
            "taskPlan": {
                "selectedActionFamily": "roas_scale",
                "agent2ActionPlan": plan,
                "creativeTestPlan": _roas_plan()["creativeTestPlan"],
                "budgetPlan": plan["budgetPlan"],
                "operatorExecutionSop": ["一", "二", "三", "四"],
            },
            "productJudgmentPackage": {"agent2ActionPlan": plan},
        }
    )
    assert "agent2ActionPlan" in decision
    assert "agent2ActionPlan" not in decision["taskPlan"]
    assert "agent2ActionPlan" not in decision["productJudgmentPackage"]
    assert "creativeTestPlan" not in decision["taskPlan"]
    assert decision["taskPlan"]["budgetPlan"]["targetBudget"] == 1290.22
    assert decision["activeActionContract"]["activeActionFamily"] == "roas_scale"


def test_runtime_overlay_and_worker_aliases_are_installed() -> None:
    assert AGENT2_SINGLE_ACTION_CONTRACT_VERSION == "21.7.7"
    assert getattr(agent2, "_V2177_SINGLE_ACTION_CONTRACT_INSTALLED", False)
    assert getattr(action_worker, "_V2177_SINGLE_ACTION_CONTRACT_INSTALLED", False)
    assert getattr(sop_worker, "_V2177_SINGLE_ACTION_CONTRACT_INSTALLED", False)
    assert action_worker.call_agent2_action_plans is agent2.call_agent2_action_plans
    assert action_worker.attach_agent2_action_plans is agent2.attach_agent2_action_plans


def test_active_contract_helper_keeps_only_selected_family_plan() -> None:
    plan = sanitize_plan(_roas_plan(), _roas_plan())
    contract = active_action_contract(plan)
    assert contract["version"] == "21.7.7"
    assert contract["activeActionFamily"] == "roas_scale"
    assert contract["activeOperationPlan"]["operations"][0]["operationType"] == "budget_update"
    assert contract["activeFamilyPlan"]["targetBudget"] == 1290.22
