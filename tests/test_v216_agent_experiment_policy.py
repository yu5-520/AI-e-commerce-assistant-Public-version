from __future__ import annotations

from src.services import real_product_judgment_agent_v196_service as agent1
from src.services import v216_observation_experiment_service as maturity


def _bundle() -> dict:
    return {
        "signalId": "PSIGV-1",
        "entityId": "P1",
        "productId": "P1",
        "storeId": "S1",
        "payload": {
            "productId": "P1",
            "storeId": "S1",
            "profileLayer": {"title": "测试商品"},
            "crossValidation": {
                "version": "21.5.0",
                "decision": {
                    "hypothesisCode": "click_acceptance_decline",
                    "hypothesisLabel": "标题主图承接下降",
                    "status": "confirmed",
                    "severity": 68,
                    "confidence": 72,
                    "businessImpact": 68,
                    "urgency": 68,
                    "independentEvidenceGroups": ["click", "organic_traffic"],
                    "conflictEvidenceGroups": [],
                },
                "observationMaturity": {
                    "version": "21.6.0",
                    "maturity": "M1_pair_delta",
                    "alignedObservationCount": 2,
                },
                "experimentPolicy": {
                    "version": "21.6.0",
                    "experimentMode": "isolated_test",
                    "actionFamily": "title_image_test",
                    "actionIntensity": "L2",
                    "targetObject": "new_test_link",
                    "trafficShareCeiling": 0.10,
                    "budgetChangeCeiling": 0.10,
                    "mainlineMutationAllowed": False,
                    "rollbackRequired": True,
                    "allowed": True,
                },
            },
        },
    }


def test_action_family_mapping_uses_existing_downstream_contract() -> None:
    assert maturity.ACTION_FAMILY_BY_HYPOTHESIS == {
        "paid_efficiency_decline": "roas_guard",
        "click_acceptance_decline": "title_image_test",
        "conversion_decline": "conversion_repair",
        "service_risk": "conversion_repair",
        "growth_opportunity": "platform_activity",
        "no_operating_event": "observe_only",
    }


def test_pair_delta_target_matches_supported_action_family() -> None:
    roas = maturity.experiment_policy(
        "paid_efficiency_decline",
        "M1_pair_delta",
    )
    creative = maturity.experiment_policy(
        "click_acceptance_decline",
        "M1_pair_delta",
    )

    assert roas["actionFamily"] == "roas_guard"
    assert roas["targetObject"] == "new_ad_plan"
    assert roas["operationScope"] == "isolated_new_plan"
    assert roas["durationHours"] == 48
    assert roas["mainlineMutationAllowed"] is False

    assert creative["actionFamily"] == "title_image_test"
    assert creative["targetObject"] == "new_test_link"
    assert creative["operationScope"] == "isolated_new_link"


def test_agent1_fact_card_contains_maturity_and_experiment_policy() -> None:
    card = agent1._fact_card(_bundle())

    assert card["observationMaturity"]["maturity"] == "M1_pair_delta"
    assert card["experimentPolicy"]["experimentMode"] == "isolated_test"
    assert card["requiredActionFamily"] == "title_image_test"
    assert card["actionIntensityCeiling"] == "L2"
    assert card["mainlineMutationAllowed"] is False


def test_agent1_prompt_forbids_verification_chore_and_requires_experiment() -> None:
    messages, payload = agent1._build_messages(
        "DV-1",
        [_bundle()],
        agent1.build_agent1_rag_context(),
    )
    prompt = messages[0]["content"]

    assert "不得输出核查、复查、确认信息" in prompt
    assert "新测试链接" in prompt
    assert "新独立计划" in prompt
    assert "experimentPolicy" in prompt
    assert payload["experimentPolicyVersion"] == "21.6.0"
