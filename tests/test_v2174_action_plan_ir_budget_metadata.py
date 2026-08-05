from src.services.action_plan_ir_v214_service import normalize_action_plan_ir


def test_plan_ir_preserves_budget_recommendation_and_execution_metadata() -> None:
    plan = {
        "actionFamily": "roas_scale",
        "executionObject": {
            "targetSelector": "productId=P-2174;create=new_ad_plan",
            "targetType": "ad_plan",
        },
        "operationPlan": {
            "operations": [
                {
                    "operationType": "budget_update",
                    "target": {
                        "type": "ad_plan",
                        "selector": "productId=P-2174;create=new_ad_plan",
                    },
                    "direction": "increase",
                    "currentValue": {"budget": 1172.93},
                    "targetValue": {"budget": 1290.223},
                    "recommendedTargetValue": {"budget": 1301.9523},
                    "authorizedTargetValue": {"budget": 1290.223},
                    "executedTargetValue": {"budget": 1290.223},
                    "recommendedChangeRate": 0.11,
                    "authorizedChangeRate": 0.10,
                    "executedChangeRate": 0.10,
                    "changeRate": 0.10,
                    "adjustmentAmount": 117.293,
                    "normalizationStatus": "normalized_and_passed",
                    "stagedExecution": {
                        "status": "staged_execution",
                        "stageCount": 2,
                        "finalRecommendedBudget": 1301.9523,
                    },
                }
            ]
        },
    }

    normalized = normalize_action_plan_ir(plan, "roas_scale")
    operation = normalized["operations"][0]
    assert normalized["validation"]["passed"] is True
    assert operation["recommendedTargetValue"]["budget"] == 1301.9523
    assert operation["authorizedTargetValue"]["budget"] == 1290.223
    assert operation["executedTargetValue"]["budget"] == 1290.223
    assert operation["recommendedChangeRate"] == 0.11
    assert operation["authorizedChangeRate"] == 0.10
    assert operation["executedChangeRate"] == 0.10
    assert operation["normalizationStatus"] == "normalized_and_passed"
    assert operation["stagedExecution"]["stageCount"] == 2
