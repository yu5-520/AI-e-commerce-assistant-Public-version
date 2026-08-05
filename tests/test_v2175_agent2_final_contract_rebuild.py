from __future__ import annotations

from pathlib import Path

import src  # noqa: F401
import src.repositories.sqlite_repository as repository
from src.services import agent2_action_plan_core_v20_service as agent2
from src.services import agent_pipeline_item_worker_v2010_service as pipeline_worker
from src.services.agent2_runtime_resilience_v2143_service import (
    ensure_agent2_runtime_columns,
)
from src.services.agent_runtime_contract_v2141_service import payload_from_row
from src.services.pipeline_item_service import build_item_envelope, upsert_pipeline_item
from src.services.v2175_agent2_final_contract_rebuild_service import (
    AGENT2_FINAL_CONTRACT_REBUILD_VERSION,
    rebuild_final_agent2_plan,
    recover_final_budget_contract_failures,
)


def _proof() -> dict:
    return {
        "version": "21.4.1",
        "stage": "action_plan_judgment_agent",
        "packageId": "PKG-2175",
        "semanticCallId": "A2CALL-2175",
        "provider": "bailian",
        "model": "qwen-plus",
        "providerRequestId": "REQ-2175",
        "providerCallExecuted": True,
        "exactReplayValidated": False,
        "itemCorrelationId": "PKG-2175",
        "resultMatched": True,
        "fallbackUsed": False,
    }


def _provider() -> dict:
    proof = _proof()
    return {
        "providerStatus": "ok",
        "actualCalls": 1,
        "cacheHits": 0,
        "fallbackUsed": False,
        "itemProvenance": {"PKG-2175": proof},
    }


def _package() -> dict:
    return {
        "dataVersion": "DV-2175",
        "itemId": "PI-2175",
        "packageId": "PKG-2175",
        "productId": "P-2175",
        "productTitle": "通勤防泼水背包",
        "title": "通勤防泼水背包",
        "storeId": "S-2175",
        "selectedOperatingRoute": "paid_traffic_efficiency",
        "actionFamily": "roas_scale",
        "selectedActionFamilyHint": "roas_scale",
        "agent1OperatingJudgment": {
            "selectedOperatingRoute": "paid_traffic_efficiency",
            "selectedActionFamily": "roas_scale",
            "routeLock": {
                "locked": True,
                "selectedOperatingRoute": "paid_traffic_efficiency",
            },
            "actionFamilyLock": {
                "locked": True,
                "selectedActionFamily": "roas_scale",
                "forbiddenOverride": True,
            },
        },
        "matrixDispatch": {
            "selectedActionFamily": "roas_scale",
            "lockedByAgent1": True,
            "routeActionConsistency": "passed",
            "agent1LockMissing": False,
        },
        "crossValidation": {
            "experimentPolicy": {
                "actionFamily": "roas_scale",
                "experimentMode": "isolated_test",
                "targetObject": "new_ad_plan",
                "operationScope": "isolated_ad_plan_test",
                "trafficShareCeiling": 0.10,
                "budgetChangeCeiling": 0.10,
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
        "ragContextSnapshot": {"taskGate": False},
    }


def _failed_plan() -> dict:
    proof = _proof()
    return {
        "stage": "agent2_action_plan",
        "packageId": "PKG-2175",
        "productId": "P-2175",
        "storeId": "S-2175",
        "actionFamily": "roas_scale",
        "actionPlanStatus": "conflict_requires_rejudgment",
        "conflictReason": (
            "Agent2 plan exceeds V21.6 experiment permission: "
            "budget_change_exceeds_ceiling"
        ),
        "reason": (
            "Agent2 plan exceeds V21.6 experiment permission: "
            "budget_change_exceeds_ceiling"
        ),
        "finalTaskTitle": "隔离广告计划预算放量",
        "operationMode": "isolated_test",
        "differentiationReason": "该商品ROI稳定且具备可控放量空间",
        "executionObject": {
            "targetSelector": "productId=P-2175;create=new_ad_plan",
            "targetType": "ad_plan",
        },
        "operationPlan": {
            "version": "21.4.0",
            "schema": "operation_plan_ir.v1",
            "actionFamily": "roas_scale",
            "operations": [
                {
                    "operationId": "OP-2175",
                    "operationType": "budget_update",
                    "target": {
                        "type": "ad_plan",
                        "selector": "productId=P-2175;create=new_ad_plan",
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
                }
            ],
            "validation": {"passed": True, "missing": []},
        },
        "operatorActionSteps": ["创建计划", "复制定向", "设置预算", "启动测试"],
        "executionSteps": [{"step": 1}, {"step": 2}, {"step": 3}],
        "decisionBranches": [{"branch": 1}, {"branch": 2}],
        "submissionEvidence": [{"evidence": 1}, {"evidence": 2}],
        "semanticContractMissing": [
            "agent2ActionPlan.actionPlanStatus_ready",
            "agent2ActionPlan.semanticContractMissing_empty",
        ],
        "experimentPermissionViolations": ["budget_change_exceeds_ceiling"],
        "experimentPermissionStatus": "rejected",
        "agent2ExecutionProof": proof,
        "agent2Source": "llm_provider_call",
        "fallbackAllowed": False,
        "taskAdmissionAllowed": False,
    }


def test_final_contract_rebuild_clears_only_budget_derivatives() -> None:
    rebuilt = rebuild_final_agent2_plan(_failed_plan(), _package())
    assert rebuilt["actionPlanStatus"] == "ready"
    assert rebuilt["semanticContractMissing"] == []
    assert rebuilt["experimentPermissionViolations"] == []
    assert rebuilt["experimentPermissionStatus"] == "passed"
    assert rebuilt["taskAdmissionAllowed"] is True
    assert rebuilt["reason"] is None
    assert rebuilt["conflictReason"] is None
    assert rebuilt["agent2FinalContractRebuild"]["recomputedAfterBudgetGovernance"] is True


def test_real_structural_missing_still_blocks() -> None:
    plan = _failed_plan()
    plan["executionSteps"] = []
    rebuilt = rebuild_final_agent2_plan(plan, _package())
    assert rebuilt["actionPlanStatus"] != "ready"
    assert "executionSteps_min_3" in rebuilt["semanticContractMissing"]
    assert rebuilt["taskAdmissionAllowed"] is False


def test_existing_proven_plan_is_promoted_without_new_provider_call(tmp_path: Path) -> None:
    repository.DB_PATH = tmp_path / "v2175.sqlite3"
    repository.LOG_DIR = tmp_path
    repository._WAL_INITIALIZED = False
    ensure_agent2_runtime_columns()

    payload = {
        **_package(),
        "agent2ActionPlan": _failed_plan(),
        "plan": _failed_plan(),
        "operationPlan": _failed_plan()["operationPlan"],
        "agent2ExecutionProof": _proof(),
        "agent2Provider": _provider(),
        "actionPlanStatus": "conflict_requires_rejudgment",
        "reason": "Agent2 plan exceeds V21.6 experiment permission",
        "missing": [
            "agent2ActionPlan.actionPlanStatus_ready",
            "agent2ActionPlan.semanticContractMissing_empty",
            "agent2ActionPlan.experimentPermission.budget_change_exceeds_ceiling",
            "budget_change_exceeds_ceiling",
        ],
        "budgetGovernanceRecovery": {
            "version": "21.7.4",
            "singleReplay": True,
            "previousMissing": ["budget_change_exceeds_ceiling"],
        },
        "agent2RetryPolicy": {"terminal": True},
    }
    envelope = build_item_envelope(
        data_version="DV-2175",
        item_id="PI-2175",
        product_id="P-2175",
        store_id="S-2175",
        package_id="PKG-2175",
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

    result = recover_final_budget_contract_failures("DV-2175")
    assert result["recoveredItemCount"] == 1
    assert result["newProviderCallCount"] == 0
    assert recover_final_budget_contract_failures("DV-2175")["recoveredItemCount"] == 0

    with repository.connect() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_items WHERE item_id='PI-2175'"
        ).fetchone()
    recovered = payload_from_row(row)
    assert row["current_stage"] == "agent2_completed"
    assert row["status"] == "ready"
    assert recovered["agent2ActionPlan"]["actionPlanStatus"] == "ready"
    assert recovered["agent2ActionPlan"]["semanticContractMissing"] == []
    assert recovered["agent2FinalContractRecovery"]["providerCallReused"] is True
    assert recovered["agent2FinalContractRecovery"]["newProviderCallExecuted"] is False
    assert recovered["agent2Provider"]["actualCalls"] == 1


def test_runtime_overlay_is_installed_after_budget_governance() -> None:
    assert AGENT2_FINAL_CONTRACT_REBUILD_VERSION == "21.7.5"
    assert getattr(agent2, "_V2175_AGENT2_FINAL_CONTRACT_REBUILD_INSTALLED", False)
    assert getattr(pipeline_worker, "_V2175_AGENT2_FINAL_CONTRACT_REBUILD_INSTALLED", False)
