from __future__ import annotations

import unittest
from unittest.mock import patch

from src.services import agent3_runtime_v23215_service as runtime
from src.services import agent3_system_constraint_v23215_service as constraint
from src.services.agent_runtime_contract_v225_service import build_task_mapping_decision


def _package() -> dict:
    return {
        "packageId": "PKG-P10006",
        "itemId": "PI-P10006",
        "dataVersion": "DV-TEST",
        "productId": "P10006",
        "storeId": "JD-SH-002",
        "lockedActionFamily": "roas_scale",
        "agent2ActionDraft": {
            "packageId": "PKG-P10006",
            "productId": "P10006",
            "storeId": "JD-SH-002",
            "actionFamily": "roas_scale",
            "draftStatus": "draft_ready",
            "operationPlan": {"budgetChange": "scale"},
        },
        "inputContract": {
            "schema": "agent_input.agent3_sop.v1",
            "agent3SystemConstraintRequired": True,
        },
    }


def _stop_condition() -> dict:
    return {
        "conditionId": "STOP-1",
        "actionFamily": "roas_scale",
        "conditionType": "roas_guardrail",
        "condition": "ROAS跌破当前安全阈值",
        "responseAction": "暂停继续放量",
        "evidenceRequired": "广告计划结果快照",
    }


def _rollback_condition() -> dict:
    return {
        "conditionId": "ROLLBACK-1",
        "actionFamily": "roas_scale",
        "conditionType": "restore_previous_budget",
        "condition": "放量后效率持续恶化",
        "rollbackAction": "恢复上一预算",
        "evidenceRequired": "调整前后预算与ROAS快照",
    }


def _step(step_id: str, action_type: str, instruction: str) -> dict:
    return {
        "stepId": step_id,
        "actionFamily": "roas_scale",
        "actionType": action_type,
        "executionObject": "product_ad_plan_scope:P10006",
        "executorRole": "运营",
        "instruction": instruction,
        "deadline": "T+4小时",
        "completionCriteria": "完成并留存系统结果",
    }


def _valid_sop() -> dict:
    instructions = [
        "核对当前广告计划基准与系统冻结证据。",
        "在已授权范围内上调广告预算。",
        "复盘调整后的ROAS与消耗变化。",
    ]
    return {
        "packageId": "PKG-P10006",
        "productId": "P10006",
        "storeId": "JD-SH-002",
        "actionFamily": "roas_scale",
        "lockedActionFamily": "roas_scale",
        "sopStatus": "sop_requires_approval",
        "executionSteps": [
            _step("STEP-1", "plan_audit", instructions[0]),
            _step("STEP-2", "budget_adjustment", instructions[1]),
            _step("STEP-3", "result_review", instructions[2]),
        ],
        "operatorActionSteps": instructions,
        "submissionEvidence": ["预算调整前后截图"],
        "crossDepartmentActions": [],
        "stopConditions": [_stop_condition()],
        "rollbackConditions": [_rollback_condition()],
    }


class Agent3CrossDepartmentCoordinationContractTest(unittest.TestCase):
    def test_roas_scale_operator_surface_still_blocks_inventory_language(self) -> None:
        sop = _valid_sop()
        sop["executionSteps"][1]["instruction"] = "上调预算并让仓储立即补货。"
        sop["operatorActionSteps"][1] = sop["executionSteps"][1]["instruction"]
        errors = constraint.validate_agent3_sop_system_contract(sop, _package())
        self.assertTrue(
            any(value.startswith("agent3_sop_cross_family_contamination:") for value in errors),
            errors,
        )

    def test_inventory_language_is_allowed_in_structured_supporting_coordination(self) -> None:
        sop = _valid_sop()
        sop["crossDepartmentActions"] = [
            {
                "department": "仓储",
                "action": "关注库存水位并按仓储流程安排补货",
                "reason": "ROAS放量后流量提升，防止断货",
            }
        ]
        errors = constraint.validate_agent3_sop_system_contract(sop, _package())
        self.assertFalse(
            any(value.startswith("agent3_sop_cross_family_contamination:") for value in errors),
            errors,
        )
        self.assertFalse(
            any(value.startswith("agent3_cross_department_coordination_") for value in errors),
            errors,
        )

    def test_coordination_requires_department_action_and_reason(self) -> None:
        sop = _valid_sop()
        sop["crossDepartmentActions"] = [{"action": "关注库存", "reason": "防断货"}]
        errors = constraint.validate_agent3_sop_system_contract(sop, _package())
        self.assertIn(
            "agent3_cross_department_coordination_1_missing:department",
            errors,
        )

    def test_coordination_cannot_override_locked_action_family(self) -> None:
        sop = _valid_sop()
        sop["crossDepartmentActions"] = [
            {
                "department": "仓储",
                "action": "安排补货",
                "reason": "防断货",
                "actionFamily": "inventory_coordination",
            }
        ]
        errors = constraint.validate_agent3_sop_system_contract(sop, _package())
        self.assertIn(
            "agent3_cross_department_coordination_1_action_family_override",
            errors,
        )

    def test_task_mapping_preserves_supporting_coordination_interface(self) -> None:
        package = _package()
        package["agent3Sop"] = {
            "sopStatus": "sop_requires_approval",
            "finalTaskTitle": "P10006 ROAS放量",
            "actionFamily": "roas_scale",
            "operatorActionSteps": ["调整广告预算"],
            "executionSteps": [_step("STEP-1", "budget_adjustment", "调整广告预算")],
            "crossDepartmentActions": [
                {
                    "department": "仓储",
                    "action": "关注库存并安排补货",
                    "reason": "防止增投后断货",
                }
            ],
        }
        package["agent3ExecutionProof"] = {
            "resultMatched": True,
            "providerCallExecuted": True,
            "providerRequestId": "REQ-1",
            "semanticCallId": "A3CALL-1",
            "fallbackUsed": False,
            "passed": True,
        }
        decision = build_task_mapping_decision(package)
        self.assertEqual(
            decision["taskPlan"]["supportingCoordination"],
            package["agent3Sop"]["crossDepartmentActions"],
        )
        self.assertEqual(
            decision["taskPlan"]["crossDepartmentActions"],
            package["agent3Sop"]["crossDepartmentActions"],
        )

    def test_semantic_identity_changes_when_system_constraint_version_changes(self) -> None:
        package = _package()
        compiled = {
            "productId": "P10006",
            "storeId": "JD-SH-002",
            "lockedActionFamily": "roas_scale",
            "systemConstraintContract": {"version": "23.2.18"},
        }
        descriptor = {
            "stage": "task_mapping_agent",
            "inputSchema": "agent_input.agent3_sop.v1",
            "projectionVersion": "22.5.9",
            "promptVersion": "23.2.15",
            "policyHash": "policy-1",
            "provider": "aliyun_bailian",
            "model": "qwen3.7-plus",
            "generationParametersHash": "generation-1",
        }
        with patch.object(runtime.core, "compile_agent3_provider_package", return_value=compiled):
            current = runtime.build_agent3_semantic_identity({}, descriptor, package)
            with patch.object(runtime.core, "AGENT3_SYSTEM_CONSTRAINT_VERSION", "23.2.15"):
                previous = runtime.build_agent3_semantic_identity({}, descriptor, package)
        self.assertNotEqual(current["semanticHash"], previous["semanticHash"])
        self.assertEqual(constraint.AGENT3_SYSTEM_CONSTRAINT_VERSION, "23.2.18")


if __name__ == "__main__":
    unittest.main()
