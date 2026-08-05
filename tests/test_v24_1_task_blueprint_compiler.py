from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from tools.registry_compiler.compile_registry import verify_committed_manifest
from tools.registry_compiler.v24_task_blueprint_compiler import (
    BlueprintCompatibilityError,
    canonical_blueprint_bytes,
    compile_single_stage_blueprint,
    task_blueprint_compiler_identity,
)


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REGISTRY_ROOT = (
    "sha256:e1bbee3ef7b78805ed32a917f304c5585e2cca3195e59856e973a051e2a713b0"
)
DEDICATED_RUNNER = (
    "tools.registry_compiler.v24_task_blueprint_compiler:"
    "task_blueprint_compiler_identity"
)


def _source() -> dict:
    sop = {
        "schema": "agent3.sop.v1",
        "version": "23.2.15",
        "packageId": "PKG-001",
        "productId": "P-10001",
        "storeId": "S-001",
        "actionFamily": "title_image_test",
        "lockedActionFamily": "title_image_test",
        "sopStatus": "sop_ready",
        "finalTaskTitle": "完成标题与主图单变量测试",
        "executionObjective": "在不改变商品价格的前提下验证素材点击率提升。",
        "executionObject": {
            "targetType": "product",
            "targetId": "P-10001",
        },
        "executionSteps": [
            {
                "stepId": "STEP-1",
                "actionFamily": "title_image_test",
                "actionType": "prepare_variant",
                "executionObject": {
                    "targetType": "creative",
                    "targetId": "IMG-A",
                },
                "executorRole": "operator",
                "instruction": "建立一组仅修改主图的测试版本。",
                "deadline": "6h",
                "completionCriteria": "测试版本已发布并记录基线。",
            }
        ],
        "decisionBranches": [
            {"branchId": "BR-1", "condition": "ctr_up"}
        ],
        "submissionEvidence": [
            {"type": "screenshot", "ref": "artifact://evidence/1"}
        ],
        "approvalFlow": {"required": False},
        "reviewMetrics": [{"metric": "ctr", "target": ">=3.2%"}],
        "verificationPeriod": "24h",
        "stopConditions": [
            {
                "conditionId": "STOP-1",
                "actionFamily": "title_image_test",
                "conditionType": "compliance",
                "condition": "平台提示素材违规",
                "responseAction": "立即停止测试版本",
                "evidenceRequired": ["platform_notice"],
            }
        ],
        "rollbackConditions": [
            {
                "conditionId": "ROLLBACK-1",
                "actionFamily": "title_image_test",
                "conditionType": "metric_guard",
                "condition": "转化率连续两个观察窗下降",
                "rollbackAction": "恢复原主图",
                "evidenceRequired": ["conversion_snapshot"],
            }
        ],
        "reviewCycle": ["3天", "7天"],
        "agent2DraftRef": "artifact://agent2/draft/1",
        "agent3ExecutionProof": {
            "passed": True,
            "semanticCallId": "CALL-1",
        },
        "contractValidation": {"passed": True, "missing": []},
    }
    return {
        "sourceTaskId": "LT-202608010001-P10001",
        "sourcePackageId": "PKG-001",
        "productId": "P-10001",
        "storeId": "S-001",
        "dataVersion": "DV-2026-08-01",
        "lockedActionFamily": "title_image_test",
        "agent3SopRef": "artifact://agent3/sop/1",
        "agent3OutputContentHash": "sha256:" + "a" * 64,
        "evidenceRefs": ["artifact://facts/1"],
        "agent3Sop": sop,
    }


def test_identity_runner_remains_registered_only_and_side_effect_free() -> None:
    identity = task_blueprint_compiler_identity()

    assert identity["moduleId"] == "task_blueprint_compiler"
    assert identity["activationState"] == "REGISTERED_ONLY"
    assert identity["runtimeBindingEnabled"] is False
    assert identity["compilerVersion"] == "24.1.0"
    assert identity["compilerRunner"] == DEDICATED_RUNNER
    assert identity["compatibilityModes"] == ["single_stage_v23_projection"]
    assert identity["sideEffectFree"] is True
    assert identity["businessRuntimeMutated"] is False
    assert identity["databaseMutated"] is False
    assert identity["providerCallsExecuted"] == 0


def test_single_stage_blueprint_is_deterministic_and_exactly_one_to_one() -> None:
    first = compile_single_stage_blueprint(_source())
    second = task_blueprint_compiler_identity(copy.deepcopy(_source()))

    assert first == second
    assert canonical_blueprint_bytes(first) == canonical_blueprint_bytes(second)
    assert first["schema"] == "task.execution_blueprint.v24"
    assert first["compatibilityMode"] == "single_stage_v23_projection"
    assert first["blueprintStatus"] == "ready"
    assert first["stageCount"] == 1
    assert first["actionNodeCount"] == 1
    assert first["planId"].startswith("PLAN-")
    assert first["parentTaskId"].startswith("TASK-")
    assert first["taskExecutionBlueprintRef"].startswith(
        "artifact://task-execution-blueprint/"
    )
    assert first["blueprintContentHash"].startswith("sha256:")

    graph = first["stageGraph"]
    assert graph["schema"] == "plan.stage_graph.v24"
    assert len(graph["stages"]) == 1
    stage = graph["stages"][0]
    assert stage["stageId"] == first["currentStageId"]
    assert len(stage["actionNodeIds"]) == 1
    assert len(stage["actionNodes"]) == 1
    assert stage["actionNodes"][0]["actionNodeId"] == stage["actionNodeIds"][0]


def test_projection_preserves_agent3_business_content_without_rewrite() -> None:
    source = _source()
    source_before = copy.deepcopy(source)
    blueprint = compile_single_stage_blueprint(source)
    node = blueprint["stageGraph"]["stages"][0]["actionNodes"][0]

    assert source == source_before
    assert blueprint["operatorExecutionSop"] == source_before["agent3Sop"]
    assert node["executionSteps"] == source_before["agent3Sop"]["executionSteps"]
    assert node["stopConditions"] == source_before["agent3Sop"]["stopConditions"]
    assert node["rollbackConditions"] == source_before["agent3Sop"]["rollbackConditions"]
    assert node["decisionBranches"] == source_before["agent3Sop"]["decisionBranches"]
    assert node["evidenceReferences"]["agent3SopRef"] == source_before["agent3SopRef"]
    assert node["evidenceReferences"]["sourceEvidenceRefs"] == source_before["evidenceRefs"]
    assert blueprint["preservationContract"]["businessContentRewritten"] is False


def test_semantically_identical_key_order_produces_byte_identical_output() -> None:
    source = _source()
    reordered = json.loads(
        json.dumps(source, ensure_ascii=False, sort_keys=True)
    )

    assert canonical_blueprint_bytes(
        compile_single_stage_blueprint(source)
    ) == canonical_blueprint_bytes(
        compile_single_stage_blueprint(reordered)
    )


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (
            lambda value: value.pop("agent3Sop"),
            "V24_BLUEPRINT_AGENT3_SOP_REQUIRED",
        ),
        (
            lambda value: value["agent3Sop"].update(
                {"sopStatus": "sop_missing_data"}
            ),
            "V24_BLUEPRINT_AGENT3_SOP_NOT_READY",
        ),
        (
            lambda value: value["agent3Sop"]["contractValidation"].update(
                {"passed": False}
            ),
            "V24_BLUEPRINT_AGENT3_CONTRACT_NOT_VALIDATED",
        ),
        (
            lambda value: value["agent3Sop"].update(
                {"productId": "P-OTHER"}
            ),
            "V24_BLUEPRINT_SOURCE_IDENTITY_MISMATCH:productId",
        ),
        (
            lambda value: value["agent3Sop"]["executionSteps"][0].update(
                {"actionFamily": "roas_adjustment"}
            ),
            "V24_BLUEPRINT_MULTIPLE_ACTION_FAMILIES",
        ),
        (
            lambda value: value["agent3Sop"].update({"executionSteps": []}),
            "V24_BLUEPRINT_EXECUTION_STEPS_REQUIRED",
        ),
        (
            lambda value: value["agent3Sop"]["stopConditions"][0].pop(
                "conditionType"
            ),
            "V24_BLUEPRINT_STOP_CONDITION_INVALID:0:conditionType",
        ),
        (
            lambda value: value["agent3Sop"]["rollbackConditions"][0].pop(
                "rollbackAction"
            ),
            "V24_BLUEPRINT_ROLLBACK_CONDITION_INVALID:0:rollbackAction",
        ),
    ],
)
def test_compiler_fails_closed(mutator, code: str) -> None:
    source = _source()
    mutator(source)

    with pytest.raises(BlueprintCompatibilityError, match=re.escape(code)):
        compile_single_stage_blueprint(source)


def test_registry_module_remains_dedicated_and_disconnected() -> None:
    registry_path = ROOT / "contracts" / "registry" / "modules.json"
    modules = {
        item["moduleId"]: item
        for item in json.loads(registry_path.read_text(encoding="utf-8"))["modules"]
    }
    module = modules["task_blueprint_compiler"]

    assert module["status"] == "REGISTERED_ONLY"
    assert module["activationState"] == "REGISTERED_ONLY"
    assert module["runtimeBindingEnabled"] is False
    assert module["upstream"] == []
    assert module["downstream"] == []
    assert module["runner"] == DEDICATED_RUNNER

    verification = verify_committed_manifest(ROOT)
    assert verification["verified"] is True
    assert verification["committedRegistryRootHash"] == EXPECTED_REGISTRY_ROOT
    assert verification["expectedRegistryRootHash"] == EXPECTED_REGISTRY_ROOT
