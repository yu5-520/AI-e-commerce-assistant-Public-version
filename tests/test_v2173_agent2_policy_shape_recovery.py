from __future__ import annotations

from pathlib import Path

import src  # noqa: F401
import src.repositories.sqlite_repository as repository
from src.services import agent2_action_plan_core_v20_service as agent2
from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
from src.services import pipeline_action_microbatch_v205_service as action_worker
from src.services.agent2_runtime_resilience_v2143_service import (
    ensure_agent2_runtime_columns,
)
from src.services.agent_runtime_contract_v2141_service import payload_from_row
from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item
from src.services.v2173_agent2_policy_shape_recovery_service import (
    AGENT2_POLICY_SHAPE_RECOVERY_VERSION,
    align_agent2_policy_and_shape,
    align_experiment_policy_to_locked_family,
    classify_policy_shape_failure,
    clean_stale_agent2_failure_fields,
    recover_policy_shape_failures,
)


def _package() -> dict:
    return {
        "dataVersion": "DV-2173",
        "itemId": "PI-2173",
        "packageId": "PKG-2173",
        "productId": "P-2173",
        "storeId": "S-2173",
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
                "budgetChangeCeiling": 0.10,
                "durationHours": 168,
                "mainlineMutationAllowed": False,
                "allowed": True,
            }
        },
        "actionParameterPack": {
            "status": "ready",
            "actionFamily": "roas_scale",
            "currentBudget": 845.43,
            "recommendedBudgetUpperBound": 938.43,
            "currentROI": 2.85,
            "safetyROI": 1.84,
        },
    }


def _raw() -> dict:
    return {
        "packageId": "PKG-2173",
        "productId": "P-2173",
        "actionFamily": "roas_scale",
        "actionPlanStatus": "ready",
        "operationMode": "isolated_test",
        "executionObject": {
            "targetSelector": "productId=P-2173;create=secondary_link_small_traffic_activity",
            "targetType": "secondary_link",
        },
        "operationPlan": {
            "operations": [
                {
                    "operationType": "budget_update",
                    "target": {
                        "type": "secondary_link",
                        "selector": "productId=P-2173;create=secondary_link_small_traffic_activity",
                    },
                    "direction": "increase",
                    "currentValue": {"budget": 845.43},
                    "targetValue": {"budget": 938.43},
                    "adjustmentAmount": 93.0,
                    "budgetChangeRate": 0.11,
                }
            ]
        },
        "executionSteps": ["新建隔离广告计划", "复制原计划定向", "启动10%预算测试"],
        "decisionBranches": ["ROI达到目标则保留", "ROI低于安全线则回滚"],
        "submissionEvidence": ["提交新计划截图", "提交ROI对比数据"],
    }


def test_roas_policy_is_rebound_to_locked_family() -> None:
    aligned = align_experiment_policy_to_locked_family(_package())
    policy = aligned["crossValidation"]["experimentPolicy"]
    assert policy["actionFamily"] == "roas_scale"
    assert policy["targetObject"] == "new_ad_plan"
    assert policy["operationScope"] == "isolated_ad_plan_test"
    assert policy["familyAlignment"]["previousActionFamily"] == "platform_activity"
    assert policy["familyAlignment"]["changed"] is True


def test_provider_strings_are_objectized_and_budget_is_capped() -> None:
    package = align_experiment_policy_to_locked_family(_package())
    aligned = align_agent2_policy_and_shape(_raw(), package)
    assert all(isinstance(item, dict) for item in aligned["executionSteps"])
    assert all(isinstance(item, dict) for item in aligned["decisionBranches"])
    assert all(isinstance(item, dict) for item in aligned["submissionEvidence"])
    assert "create=new_ad_plan" in aligned["executionObject"]["targetSelector"]
    operation = aligned["operationPlan"]["operations"][0]
    assert operation["target"]["type"] == "ad_plan"
    assert "create=new_ad_plan" in operation["target"]["selector"]
    assert round(operation["targetValue"]["budget"], 3) == round(845.43 * 1.10, 3)
    assert operation["changeRate"] == 0.10


def test_stale_failure_fields_are_removed_before_new_failure_write() -> None:
    cleaned = clean_stale_agent2_failure_fields(
        {
            **_package(),
            "blockedReason": "agent2_provider_call_or_exact_replay_missing",
            "reason": "old reason",
            "missing": ["old"],
            "agent2RetryPolicy": {"terminal": True},
        }
    )
    assert "blockedReason" not in cleaned
    assert "reason" not in cleaned
    assert "missing" not in cleaned
    assert "agent2RetryPolicy" not in cleaned
    assert cleaned["actionParameterPack"]["currentBudget"] == 845.43


def test_policy_or_shape_failure_is_classified_for_single_replay() -> None:
    payload = {
        **_package(),
        "agent2ActionPlan": {
            "semanticContractMissing": [
                "executionSteps_min_3",
                "decisionBranches_min_2",
                "submissionEvidence_min_2",
            ],
            "experimentPermissionViolations": [
                "budget_change_exceeds_ceiling"
            ],
        },
        "missing": [
            "agent2ActionPlan.experimentPermission.budget_change_exceeds_ceiling"
        ],
    }
    result = classify_policy_shape_failure(payload, "roas_scale")
    assert result["matched"] is True
    assert result["familyMismatch"] is True
    assert result["targetMismatch"] is True
    assert result["shapeFailure"] is True


def test_failed_item_is_cleaned_rebound_and_requeued_once(tmp_path: Path) -> None:
    repository.DB_PATH = tmp_path / "v2173.sqlite3"
    repository.LOG_DIR = tmp_path
    repository._WAL_INITIALIZED = False
    ensure_agent2_runtime_columns()

    payload = {
        **_package(),
        "blockedReason": "agent2_provider_call_or_exact_replay_missing",
        "reason": "Agent2 output did not satisfy contract",
        "missing": [
            "executionSteps_min_3",
            "decisionBranches_min_2",
            "submissionEvidence_min_2",
            "agent2ActionPlan.experimentPermission.budget_change_exceeds_ceiling",
        ],
        "agent2ActionPlan": {
            "semanticContractMissing": [
                "executionSteps_min_3",
                "decisionBranches_min_2",
                "submissionEvidence_min_2",
            ],
            "experimentPermissionViolations": [
                "budget_change_exceeds_ceiling"
            ],
        },
        "agent2RetryPolicy": {"terminal": True},
    }
    envelope = build_item_envelope(
        data_version="DV-2173",
        item_id="PI-2173",
        product_id="P-2173",
        store_id="S-2173",
        package_id="PKG-2173",
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

    result = recover_policy_shape_failures("DV-2173")
    assert result["requeuedCount"] == 1
    assert recover_policy_shape_failures("DV-2173")["requeuedCount"] == 0

    with repository.connect() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_items WHERE item_id='PI-2173'"
        ).fetchone()
    recovered = payload_from_row(row)
    assert row["current_stage"] == "action_pack_ready"
    assert row["status"] == "retry"
    assert recovered["crossValidation"]["experimentPolicy"]["actionFamily"] == "roas_scale"
    assert recovered["crossValidation"]["experimentPolicy"]["targetObject"] == "new_ad_plan"
    assert "blockedReason" not in recovered
    assert recovered["agent2PolicyShapeRecovery"]["singleReplay"] is True


def test_runtime_overlay_is_installed_after_v2172() -> None:
    assert AGENT2_POLICY_SHAPE_RECOVERY_VERSION == "21.7.3"
    assert getattr(agent2, "_V2173_AGENT2_POLICY_SHAPE_RECOVERY_INSTALLED", False)
    assert getattr(action_worker, "_V2173_AGENT2_POLICY_SHAPE_RECOVERY_INSTALLED", False)
    assert getattr(pipeline_worker, "_V2173_AGENT2_POLICY_SHAPE_RECOVERY_INSTALLED", False)
    messages, payload = agent2._build_messages("DV-2173", [_package()])
    assert "experimentPolicy.actionFamily必须与Agent1锁定动作族完全一致" in messages[0]["content"]
    assert payload["agent2PolicyShapeRecoveryVersion"] == "21.7.3"
    compact_package = payload["packages"][0]
    assert compact_package["experimentPolicy"]["actionFamily"] == "roas_scale"
