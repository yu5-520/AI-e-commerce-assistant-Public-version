from __future__ import annotations

from src.services import agent2_action_plan_core_v20_service as agent2
from src.services.v2171_agent2_contract_alignment_service import (
    AGENT2_CONTRACT_ALIGNMENT_VERSION,
    alignment_only_failure,
    canonical_missing_fields,
    canonicalize_agent2_raw,
    clean_agent2_failure_payload,
)


def _package() -> dict:
    return {
        "packageId": "PKG-1",
        "productId": "P1",
        "storeId": "S1",
        "actionFamily": "roas_guard",
        "experimentPolicy": {
            "experimentMode": "isolated_test",
            "targetObject": "new_ad_plan",
            "trafficShareCeiling": 0.10,
            "budgetChangeCeiling": 0.10,
            "durationHours": 72,
            "mainlineMutationAllowed": False,
        },
        "actionParameterPack": {
            "currentTargetROAS": 2.1,
            "targetROAS": 2.4,
            "minimumSafeROAS": 2.0,
        },
    }


def test_agent2_prompt_requires_machine_addressable_targets_and_complete_ops() -> None:
    messages, payload = agent2._build_messages("DV-1", [_package()])
    prompt = messages[0]["content"]

    assert "executionObject必须包含targetId或targetSelector" in prompt
    assert "只有targetName无效" in prompt
    assert "只输出有事实支撑且字段完整的操作" in prompt
    assert "budget_update必须含currentValue.budget" in prompt
    assert "至少4条operatorActionSteps" in prompt
    assert "title_image_test必须输出2-5组" in prompt
    assert "库存只能生成仓储协同" in prompt
    assert payload["agent2ContractAlignmentVersion"] == AGENT2_CONTRACT_ALIGNMENT_VERSION


def test_alignment_builds_selector_and_reuses_it_for_operation_target() -> None:
    aligned = canonicalize_agent2_raw(
        {
            "packageId": "PKG-1",
            "productId": "P1",
            "actionFamily": "roas_guard",
            "executionObject": {"targetName": "男士透气跑鞋独立投放计划"},
            "operationPlan": {
                "operations": [
                    {
                        "operationType": "target_roas_update",
                        "targetValue": {"roas": 2.4},
                    }
                ]
            },
        },
        _package(),
    )

    selector = aligned["executionObject"]["targetSelector"]
    assert selector == "男士透气跑鞋独立投放计划"
    assert aligned["operationPlan"]["operations"][0]["target"]["selector"] == selector


def test_alignment_fills_roas_facts_from_action_pack_without_inventing_budget() -> None:
    aligned = canonicalize_agent2_raw(
        {
            "packageId": "PKG-1",
            "productId": "P1",
            "actionFamily": "roas_guard",
            "executionObject": {"targetSelector": "productId=P1;create=new_ad_plan"},
            "operationPlan": {
                "operations": [
                    {"operationType": "target_roas_update"},
                    {"operationType": "stop_rule_update"},
                ]
            },
        },
        _package(),
    )

    operations = aligned["operationPlan"]["operations"]
    assert operations[0]["currentValue"]["roas"] == 2.1
    assert operations[0]["targetValue"]["roas"] == 2.4
    assert operations[1]["threshold"] == 2.0
    assert "currentValue" not in operations[1]
    assert all(item["target"]["selector"] for item in operations)


def test_runtime_missing_list_keeps_root_causes_and_deletes_derivative_duplicates() -> None:
    plan = {
        "semanticContractMissing": [
            "executionObject.targetId_or_targetSelector",
            "operations[0].target.id_or_selector",
        ],
        "experimentPermissionViolations": [],
    }
    result = canonical_missing_fields(
        [
            "agent2ActionPlan.actionPlanStatus_ready",
            "agent2ActionPlan.semanticContractMissing_empty",
            "agent2ActionPlan.operationPlan.operations[0].target.id_or_selector",
        ],
        plan,
    )

    assert result == [
        "executionObject.targetId_or_targetSelector",
        "operations[0].target.id_or_selector",
    ]


def test_alignment_only_failure_gets_one_clean_replay() -> None:
    payload = {
        "productId": "P1",
        "actionParameterPack": {"status": "ready"},
        "agent2ActionPlan": {
            "actionPlanStatus": "action_plan_missing_data",
            "semanticContractMissing": [
                "executionObject.targetId_or_targetSelector",
                "operations[0].target.id_or_selector",
                "executionSteps_min_3",
            ],
        },
        "missing": [
            "agent2ActionPlan.actionPlanStatus_ready",
            "agent2ActionPlan.semanticContractMissing_empty",
            "executionObject.targetId_or_targetSelector",
        ],
        "failureOwner": "agent2_action_plan_station",
    }

    assert alignment_only_failure(payload) is True
    cleaned = clean_agent2_failure_payload(payload)
    assert "agent2ActionPlan" not in cleaned
    assert "missing" not in cleaned
    assert cleaned["actionParameterPack"]["status"] == "ready"
    assert cleaned["agent2ContractRecovery"]["singleReplay"] is True
    assert alignment_only_failure(cleaned) is False


def test_business_or_permission_failure_is_not_auto_replayed() -> None:
    payload = {
        "agent2ActionPlan": {
            "semanticContractMissing": ["activityPlan"],
            "experimentPermissionViolations": [],
        },
        "missing": ["activityPlan"],
    }
    permission = {
        "agent2ActionPlan": {
            "semanticContractMissing": [],
            "experimentPermissionViolations": ["budget_change_exceeds_ceiling"],
        },
        "missing": ["agent2ActionPlan.actionPlanStatus_ready"],
    }

    assert alignment_only_failure(payload) is False
    assert alignment_only_failure(permission) is False
