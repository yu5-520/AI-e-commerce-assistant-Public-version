from __future__ import annotations

from pathlib import Path

import src  # noqa: F401
import src.repositories.sqlite_repository as repository
from src.services import agent2_action_plan_core_v20_service as agent2
from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
from src.services import agent_runtime_contract_v2010_service as runtime_contract
from src.services import v216_agent2_experiment_policy_service as v216_policy
from src.services.agent2_runtime_resilience_v2143_service import (
    ensure_agent2_runtime_columns,
)
from src.services.agent_runtime_contract_v2141_service import payload_from_row
from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item
from src.services.v2174_budget_governance_service import (
    BUDGET_GOVERNANCE_VERSION,
    budget_false_failure,
    executable_permission_violations,
    finalize_governed_plan,
    govern_budget_operations,
    recover_budget_false_failures,
)


def _package(ceiling: float = 0.10) -> dict:
    return {
        "dataVersion": "DV-2174",
        "itemId": "PI-2174",
        "packageId": "PKG-2174",
        "productId": "P-2174",
        "storeId": "S-2174",
        "actionFamily": "roas_scale",
        "selectedActionFamilyHint": "roas_scale",
        "agent1OperatingJudgment": {
            "selectedActionFamily": "roas_scale",
            "actionFamilyLock": {
                "locked": True,
                "selectedActionFamily": "roas_scale",
                "forbiddenOverride": True,
            },
        },
        "matrixDispatch": {
            "selectedActionFamily": "roas_scale",
            "lockedByAgent1": True,
        },
        "crossValidation": {
            "experimentPolicy": {
                "actionFamily": "platform_activity",
                "experimentMode": "isolated_test",
                "targetObject": "secondary_link_small_traffic_activity",
                "operationScope": "isolated_activity_test",
                "trafficShareCeiling": 0.10,
                "budgetChangeCeiling": ceiling,
                "durationHours": 72,
                "mainlineMutationAllowed": False,
                "allowed": True,
            }
        },
        "actionParameterPack": {
            "status": "ready",
            "actionFamily": "roas_scale",
            "currentBudget": 1172.93,
            "recommendedBudgetUpperBound": 1301.9523,
            "currentROI": 2.85,
            "safetyROI": 1.84,
        },
    }


def _raw(target: float, current: float = 1172.93) -> dict:
    return {
        "packageId": "PKG-2174",
        "productId": "P-2174",
        "actionFamily": "roas_scale",
        "actionPlanStatus": "ready",
        "operationMode": "isolated_test",
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
                    "currentValue": {"budget": current},
                    "targetValue": {"budget": target},
                    "adjustmentAmount": target - current,
                }
            ]
        },
        "operatorActionSteps": ["一", "二", "三", "四"],
        "executionSteps": [{"step": 1}, {"step": 2}, {"step": 3}],
        "decisionBranches": [{"branch": 1}, {"branch": 2}],
        "submissionEvidence": [{"evidence": 1}, {"evidence": 2}],
    }


def _budget_operation(value: dict) -> dict:
    return value["operationPlan"]["operations"][0]


def test_budget_inside_ceiling_passes_without_mutating_recommendation() -> None:
    raw = _raw(1172.93 * 1.08)
    governed = govern_budget_operations(raw, _package())
    operation = _budget_operation(governed)
    assert operation["normalizationStatus"] == "passed"
    assert round(operation["targetValue"]["budget"], 6) == round(1172.93 * 1.08, 6)
    assert round(operation["recommendedTargetValue"]["budget"], 6) == round(1172.93 * 1.08, 6)
    assert executable_permission_violations(governed, governed["experimentPolicy"] if "experimentPolicy" in governed else _package()["crossValidation"]["experimentPolicy"]) == []


def test_eleven_percent_is_capped_to_ten_and_passes() -> None:
    current = 1172.93
    recommended = 1301.9523
    governed = govern_budget_operations(_raw(recommended, current), _package())
    operation = _budget_operation(governed)
    assert operation["normalizationStatus"] == "normalized_and_passed"
    assert round(operation["recommendedTargetValue"]["budget"], 4) == round(recommended, 4)
    assert round(operation["targetValue"]["budget"], 3) == round(current * 1.10, 3)
    assert operation["authorizedChangeRate"] == 0.10
    assert governed["budgetGovernance"]["status"] == "normalized_and_passed"
    policy = {
        **_package()["crossValidation"]["experimentPolicy"],
        "actionFamily": "roas_scale",
        "targetObject": "new_ad_plan",
        "operationScope": "isolated_ad_plan_test",
    }
    assert executable_permission_violations(governed, policy) == []


def test_large_recommendation_becomes_staged_execution() -> None:
    current = 100.0
    governed = govern_budget_operations(_raw(130.0, current), _package())
    operation = _budget_operation(governed)
    staged = operation["stagedExecution"]
    assert staged["status"] == "staged_execution"
    assert staged["stageCount"] == 3
    assert operation["targetValue"]["budget"] == 110.0
    assert staged["stages"][-1]["targetBudget"] == 130.0


def test_amount_like_budget_change_field_is_not_parsed_as_rate() -> None:
    raw = _raw(1290.223)
    raw["budgetChangeAmount"] = 117.293
    raw["audit"] = {
        "recommendedBudgetChange": 129.0223,
        "historicalBudgetChangeAmount": 300.0,
    }
    policy = {
        **_package()["crossValidation"]["experimentPolicy"],
        "actionFamily": "roas_scale",
        "targetObject": "new_ad_plan",
    }
    assert executable_permission_violations(raw, policy) == []


def test_floating_point_ten_percent_is_not_rejected() -> None:
    current = 1172.93
    raw = _raw(current * (1 + 0.1000000000000001), current)
    policy = {
        **_package()["crossValidation"]["experimentPolicy"],
        "actionFamily": "roas_scale",
        "targetObject": "new_ad_plan",
    }
    assert executable_permission_violations(raw, policy) == []


def test_finalizer_turns_budget_permission_conflict_into_ready() -> None:
    raw = govern_budget_operations(_raw(1301.9523), _package())
    raw.update(
        actionPlanStatus="conflict_requires_rejudgment",
        conflictReason=(
            "Agent2 plan exceeds V21.6 experiment permission: "
            "budget_change_exceeds_ceiling"
        ),
        experimentPermissionViolations=["budget_change_exceeds_ceiling"],
        semanticContractMissing=[],
        taskAdmissionAllowed=False,
    )
    finalized = finalize_governed_plan(raw, _package())
    assert finalized["actionPlanStatus"] == "ready"
    assert finalized["experimentPermissionStatus"] == "passed"
    assert finalized["experimentPermissionViolations"] == []
    assert finalized["taskAdmissionAllowed"] is True
    operation = _budget_operation(finalized)
    assert round(operation["recommendedTargetValue"]["budget"], 4) == 1301.9523
    assert round(operation["targetValue"]["budget"], 3) == round(1172.93 * 1.10, 3)


def test_action_pack_persists_locked_family_policy_before_agent2() -> None:
    ready = runtime_contract.normalize_action_pack_ready_contract(
        _package(),
        _package()["actionParameterPack"],
    )
    policy = ready["crossValidation"]["experimentPolicy"]
    assert ready["actionFamily"] == "roas_scale"
    assert policy["actionFamily"] == "roas_scale"
    assert policy["targetObject"] == "new_ad_plan"
    assert policy["operationScope"] == "isolated_ad_plan_test"
    assert ready["actionFamilyPolicyAlignment"]["persistedBeforeAgent2"] is True


def test_budget_false_failure_classifier_is_narrow() -> None:
    payload = {
        "missing": [
            "agent2ActionPlan.experimentPermission.budget_change_exceeds_ceiling",
            "budget_change_exceeds_ceiling",
        ],
        "agent2ActionPlan": {
            "experimentPermissionViolations": ["budget_change_exceeds_ceiling"]
        },
    }
    assert budget_false_failure(payload) is True
    payload["missing"].append("executionObject.targetId_or_targetSelector")
    assert budget_false_failure(payload) is False


def test_existing_budget_false_failure_is_requeued_once(tmp_path: Path) -> None:
    repository.DB_PATH = tmp_path / "v2174.sqlite3"
    repository.LOG_DIR = tmp_path
    repository._WAL_INITIALIZED = False
    ensure_agent2_runtime_columns()

    payload = {
        **_package(),
        "reason": "Agent2 plan exceeds V21.6 experiment permission",
        "missing": [
            "agent2ActionPlan.experimentPermission.budget_change_exceeds_ceiling",
            "budget_change_exceeds_ceiling",
        ],
        "agent2ActionPlan": {
            "experimentPermissionViolations": ["budget_change_exceeds_ceiling"],
            "semanticContractMissing": [],
        },
        "agent2RetryPolicy": {"terminal": True},
    }
    envelope = build_item_envelope(
        data_version="DV-2174",
        item_id="PI-2174",
        product_id="P-2174",
        store_id="S-2174",
        package_id="PKG-2174",
        action_family="roas_scale",
        route="paid_traffic_efficiency",
        stage="agent2_output_invalid",
    )
    upsert_pipeline_item(
        envelope,
        stage="agent2_output_invalid",
        status="failed",
        payload=payload,
    )

    result = recover_budget_false_failures("DV-2174")
    assert result["requeuedCount"] == 1
    assert recover_budget_false_failures("DV-2174")["requeuedCount"] == 0

    with repository.connect() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_items WHERE item_id='PI-2174'"
        ).fetchone()
    recovered = payload_from_row(row)
    assert row["current_stage"] == "action_pack_ready"
    assert row["status"] == "retry"
    assert recovered["budgetGovernanceRecovery"]["singleReplay"] is True
    assert recovered["crossValidation"]["experimentPolicy"]["actionFamily"] == "roas_scale"
    assert "agent2ActionPlan" not in recovered


def test_runtime_overlay_is_installed() -> None:
    assert BUDGET_GOVERNANCE_VERSION == "21.7.4"
    assert getattr(agent2, "_V2174_BUDGET_GOVERNANCE_INSTALLED", False)
    assert getattr(pipeline_worker, "_V2174_BUDGET_GOVERNANCE_INSTALLED", False)
    assert getattr(runtime_contract, "_V2174_BUDGET_GOVERNANCE_INSTALLED", False)
    assert getattr(v216_policy, "_V2174_BUDGET_GOVERNANCE_INSTALLED", False)
    assert v216_policy._permission_violations is executable_permission_violations
