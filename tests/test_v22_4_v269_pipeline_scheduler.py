from copy import deepcopy
from unittest.mock import patch

from src.services import station_agent_worker_v22515_service as worker
from src.services import v269_pipeline_downstream_service as downstream
from src.services import v269_semantic_graph_service as graphs
from tests.test_v22_4_v269_semantic_graph import decision_raw, plan_node


def graph_package():
    decision = graphs.compile_graph("DecisionGraph", decision_raw(), evidence_refs=["fact:1"])
    admission = graphs.admit_actions(decision, ["DA1", "DA2"])
    parts = graphs.partition_actions(decision, admission)
    facts = {"fact:1": {"value": 0, "unit": "ratio"}}
    results = [
        {
            "partitionHash": part["receiptHash"],
            "plan": {"nodes": [plan_node("P" + key, key) for key in part["actionKeys"]]},
        }
        for part in parts
    ]
    plan = graphs.merge_plans(
        decision,
        admission,
        parts,
        results,
        evidence_refs=["fact:1"],
        fact_values=facts,
    )
    operation = graphs.compile_graph(
        "OperationGraph",
        {
            "nodes": [
                {
                    "kind": "OperationStage",
                    "nodeKey": "O1",
                    "planActionRefs": [node["nodeKey"] for node in plan["nodes"]],
                    "instruction": "按冻结方案执行",
                    "owner": "运营",
                    "executionObject": "商品p",
                    "sequence": 0,
                    "rollback": "恢复原设置",
                    "stopConditionRefs": [],
                    "acceptanceActions": ["核验执行记录"],
                }
            ]
        },
        upstream=plan,
    )
    knowledge = {"retrievalPolicyHash": "policy", "records": []}
    knowledge = {**knowledge, "headHash": graphs.digest(knowledge)}
    return {
        "semanticContractVersion": "26.9.0",
        "packageId": "pkg",
        "productId": "p",
        "storeId": "s",
        "dataVersion": "d",
        "knowledgeContext": knowledge,
        "evidenceRefs": ["fact:1"],
        "factValues": facts,
        "DecisionGraph": decision,
        "actionAdmission": admission,
        "PlanGraph": plan,
        "OperationGraph": operation,
        "graphExecutionRefs": {
            "agent1": "exec-a1",
            "agent2": ["exec-a2-1", "exec-a2-2"],
            "agent3": "exec-a3",
        },
        "fallbackAllowed": False,
    }


def test_station_worker_drains_v269_downstream_before_legacy_tick():
    stage_result = {"ran": True, "createdTaskCount": 1}
    with patch.object(worker, "select_runnable_data_version_v225", return_value="d"), \
         patch.object(worker, "pending_graph_task_pool_count", return_value=1), \
         patch.object(worker, "pending_graph_task_mapping_count", return_value=1), \
         patch.object(worker, "pending_graph_agent3_count", return_value=1), \
         patch.object(worker, "pending_graph_agent1_count", return_value=1), \
         patch.object(worker, "run_graph_task_pool_microbatch", return_value=stage_result) as pool, \
         patch.object(worker, "run_graph_task_mapping_microbatch") as mapping, \
         patch.object(worker, "run_agent3_graph_microbatch") as agent3, \
         patch.object(worker, "run_agent2_graph_partition_microbatch") as agent2, \
         patch.object(worker, "_base_run_agent_pipeline_tick_hard") as legacy:
        result = worker.run_agent_pipeline_tick_hard(worker_id="w")
    assert result["selectedStage"] == "v269_task_mapped_to_atomic_task_pool"
    assert result["secondWorkerCreated"] is False
    assert result["fallbackAllowed"] is False
    pool.assert_called_once()
    mapping.assert_not_called()
    agent3.assert_not_called()
    agent2.assert_not_called()
    legacy.assert_not_called()


def test_v269_task_mapping_is_only_graph_identity_projection():
    package = graph_package()
    item = {"item_id": "i", "data_version": "d", "priority": 1}
    finished = []
    with patch.object(downstream, "_rows", return_value=[item]), \
         patch.object(downstream, "payload_from_row", return_value=deepcopy(package)), \
         patch.object(downstream, "_finish", side_effect=lambda *a, **kw: finished.append(kw) or {}):
        result = downstream.run_graph_task_mapping_microbatch("d")
    assert result["taskMappedCount"] == 1
    assert result["failedItemCount"] == 0
    payload = finished[0]["payload"]
    expected = graphs.map_task(
        package["DecisionGraph"],
        package["actionAdmission"],
        package["PlanGraph"],
        package["OperationGraph"],
    )
    assert payload["taskGraphMapping"] == expected
    assert payload["taskMappingMode"] == "deterministic_v269_graph_identity"
    assert payload["compilerAddedStepCount"] == 0
    assert payload["legacyBusinessSemanticsUsed"] is False
    assert "taskPlan" not in payload
    assert "agent2ActionPlan" not in payload
    assert "selectedActionFamily" not in payload


def test_v269_task_pool_calls_graph_authority_directly():
    package = {
        **graph_package(),
        "decisionId": "TGD-V269-X",
        "taskMappingMode": "deterministic_v269_graph_identity",
        "taskAdmissionAllowed": True,
    }
    item = {"item_id": "i", "data_version": "d", "priority": 1}
    finished = []
    admission = {
        "ok": True,
        "status": "entered_task_pool",
        "taskId": "LT269-X",
        "createdTaskCount": 1,
    }
    with patch.object(downstream, "_rows", return_value=[item]), \
         patch.object(downstream, "payload_from_row", return_value=deepcopy(package)), \
         patch.object(downstream, "admit_decision_to_task_pool", return_value=admission) as admit, \
         patch.object(downstream, "_finish", side_effect=lambda *a, **kw: finished.append(kw) or {}), \
         patch.object(downstream, "sync_task_pool_entries_to_task_status", return_value={}), \
         patch.object(downstream, "refresh_task_pool_views", return_value={}):
        result = downstream.run_graph_task_pool_microbatch("d", user_id="u")
    assert result["createdTaskCount"] == 1
    assert result["failedItemCount"] == 0
    admit.assert_called_once()
    called = admit.call_args.args[0]
    assert called["DecisionGraph"] == package["DecisionGraph"]
    assert called["PlanGraph"] == package["PlanGraph"]
    assert called["OperationGraph"] == package["OperationGraph"]
    assert finished[0]["stage"] == "task_admitted"
    assert finished[0]["payload"]["taskId"] == "LT269-X"
    assert finished[0]["payload"]["legacyBusinessSemanticsUsed"] is False


def test_v269_agent3_handoff_requires_exact_execution_identity():
    package = graph_package()
    package.pop("OperationGraph")
    package["graphExecutionRefs"] = {
        "agent1": "exec-a1",
        "agent2": ["exec-a2-1", "exec-a2-2"],
    }
    item = {"item_id": "i", "data_version": "d", "priority": 1}
    operation = graph_package()["OperationGraph"]
    output = {
        "packageId": "pkg",
        "sopStatus": "sop_ready",
        "OperationGraph": operation,
        "executionHash": "exec-a3",
    }
    persisted = {"payload": {}}
    finished = []
    with patch.object(downstream, "_rows", return_value=[item]), \
         patch.object(downstream, "payload_from_row", return_value=deepcopy(package)), \
         patch.object(downstream, "_artifact_source", return_value=("ART-plan", "hash")), \
         patch.object(downstream.migration, "project_input", return_value=persisted), \
         patch.object(downstream, "_store_agent3_input", return_value="ART-agent3-input"), \
         patch.object(downstream, "resolve_artifact", return_value=persisted), \
         patch("src.services.agent3_runtime_v23215_service.run_agent3_sop_projected_inputs", return_value=({"pkg": output}, {"providerStatus": "ok"})), \
         patch.object(downstream, "_finish", side_effect=lambda *a, **kw: finished.append(kw) or {}):
        result = downstream.run_agent3_graph_microbatch("d")
    assert result["operationGraphReadyCount"] == 1
    assert result["failedItemCount"] == 0
    payload = finished[0]["payload"]
    assert payload["graphExecutionRefs"] == {
        "agent1": "exec-a1",
        "agent2": ["exec-a2-1", "exec-a2-2"],
        "agent3": "exec-a3",
    }
    assert payload["OperationGraph"] == operation
    assert payload["legacyBusinessSemanticsUsed"] is False
