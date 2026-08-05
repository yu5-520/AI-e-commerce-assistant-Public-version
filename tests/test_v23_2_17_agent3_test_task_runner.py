from __future__ import annotations

import inspect
import json

import pytest

from src.services import agent3_test_task_runner_v23217_service as runner


def _proof() -> dict:
    return {
        "stage": "action_plan_judgment_agent",
        "passed": True,
        "resultMatched": True,
        "providerCallExecuted": False,
        "exactReplayValidated": True,
        "semanticCallId": "ASC-TEST-1",
        "fallbackUsed": False,
    }


def test_clone_agent2_package_creates_isolated_test_identity() -> None:
    source = {
        "dataVersion": "DV-SOURCE",
        "itemId": "PI-SOURCE",
        "packageId": "PKG-SOURCE",
        "productId": "P10001",
        "storeId": "S001",
        "lockedActionFamily": "title_image_test",
        "agent2ActionDraft": {
            "packageId": "PKG-SOURCE",
            "draftStatus": "draft_ready",
            "actionFamily": "title_image_test",
            "agent2DraftExecutionProof": _proof(),
        },
        "agent2DraftExecutionProof": _proof(),
        "agent3Sop": {"finalTaskTitle": "旧SOP"},
        "agent3ExecutionProof": {"stage": "agent3_sop_agent"},
        "taskId": "LT-OLD",
        "decisionId": "TGD-OLD",
        "taskMappingDecision": {"decisionId": "TGD-OLD"},
    }

    cloned = runner._clone_agent2_package(
        source,
        test_data_version="DV-TEST-A3-1",
        test_item_id="PI-TEST-1",
        source_task_id="LT-OLD",
        source_pipeline_item_id="PI-SOURCE",
        purpose="verify_v23_2_16",
    )

    assert cloned["dataVersion"] == "DV-TEST-A3-1"
    assert cloned["itemId"] == "PI-TEST-1"
    assert cloned["packageId"] == "PKG-SOURCE"
    assert cloned["agent2DraftExecutionProof"]["semanticCallId"] == "ASC-TEST-1"
    assert cloned["agent2ActionDraft"]["agent2DraftExecutionProof"] == cloned[
        "agent2DraftExecutionProof"
    ]
    assert "agent3Sop" not in cloned
    assert "agent3ExecutionProof" not in cloned
    assert "taskId" not in cloned
    assert "decisionId" not in cloned
    assert "taskMappingDecision" not in cloned
    assert cloned["testExecutionContext"]["isTestTask"] is True
    assert cloned["testExecutionContext"]["originalTaskReplacement"] is False
    assert cloned["testExecutionContext"]["rerunAgent1"] is False
    assert cloned["testExecutionContext"]["rerunAgent2"] is False
    assert cloned["testExecutionContext"]["rerunAgent3"] is True
    assert cloned["lineage"]["sourceTaskId"] == "LT-OLD"


def test_upstream_refs_remove_every_downstream_test_output() -> None:
    row = {
        "artifact_refs_json": json.dumps(
            {
                "agent1Ref": "ART-A1",
                "agent2DraftRef": "ART-A2",
                "agent3SopInputRef": "ART-A3I",
                "agent3SopRef": "ART-A3",
                "taskMappingRef": "ART-MAP",
                "taskAdmissionRef": "ART-POOL",
                "currentStageRef": "ART-CURRENT",
            }
        )
    }

    refs = runner._upstream_artifact_refs(row)

    assert refs == {
        "agent1Ref": "ART-A1",
        "agent2DraftRef": "ART-A2",
    }


def test_worker_gate_requires_exactly_one_success() -> None:
    runner._assert_worker_result(
        "agent3",
        {"completedItemCount": 1, "failedItemCount": 0},
        completed_key="completedItemCount",
    )

    with pytest.raises(RuntimeError, match="agent3_failed"):
        runner._assert_worker_result(
            "agent3",
            {"completedItemCount": 0, "failedItemCount": 1},
            completed_key="completedItemCount",
        )


def test_runner_uses_registered_downstream_chain_without_rerunning_upstream_agents() -> None:
    source = inspect.getsource(runner.rerun_agent3_as_test_task)

    assert "run_agent3_sop_microbatch_v225" in source
    assert "run_task_mapping_microbatch_v225" in source
    assert "run_task_pool_admission_microbatch_v225" in source
    assert "force_new_snapshot=True" in source
    assert "run_agent1" not in source
    assert "run_agent2" not in source
    assert runner.AGENT3_TEST_TASK_RUNNER_VERSION == "23.2.17"
