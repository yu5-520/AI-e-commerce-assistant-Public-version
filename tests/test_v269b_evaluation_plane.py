"""V26.9.B Evaluation Plane and downstream System Review bridge tests."""
import pytest

from src.repositories import sqlite_repository as repo
from src.services import v269_evaluation_plane_service as evaluation
from src.services import v269_experience_store_service as store
from src.services import v269_semantic_graph_service as graphs
from src.services import v269_system_review_service as system_review


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, "LOG_DIR", tmp_path)
    monkeypatch.setattr(repo, "DB_PATH", tmp_path / "v269b-eval.sqlite3")
    monkeypatch.setattr(repo, "_WAL_INITIALIZED", False)
    store.ensure_experience_store()
    return tmp_path


def runtime_source():
    return {
        "sourceTaskId": "TASK-EVAL-1",
        "decisionGraphHash": "sha256:" + "4" * 64,
        "planGraphHash": "sha256:" + "5" * 64,
        "operationGraphHash": "sha256:" + "6" * 64,
        "graphContractVersion": "26.9.0",
        "evaluationVersion": store.VERSION,
        "evidenceRefs": ["artifact:review", "artifact:actual"],
        "businessScope": {"storeId": "store-1", "productId": "product-1"},
        "sourceType": "runtime",
        "sourceVersion": "review-window-v1",
    }


def metric_map(vector):
    return {item["metricId"]: item for item in vector["metrics"]}


def compiled_review_package():
    evidence = {"fact:metric:roas"}
    decision = graphs.compile_graph(
        "DecisionGraph",
        {
            "nodes": [
                {
                    "nodeKey": "J1",
                    "kind": "JudgementNode",
                    "reasoning": "ROAS below reviewed operating range",
                    "evidenceRefs": ["fact:metric:roas"],
                    "confidence": 0.9,
                    "metric": "roas",
                    "direction": "down",
                    "category": "traffic",
                },
                {
                    "nodeKey": "A1",
                    "kind": "DecisionActionNode",
                    "judgementRefs": ["J1"],
                    "actionType": "guard",
                    "actionFamily": "roas_guard",
                    "priority": 0.8,
                    "confidence": 0.85,
                    "evidenceRefs": ["fact:metric:roas"],
                },
            ],
            "edges": [{"sourceRef": "J1", "targetRef": "A1", "relation": "supports"}],
        },
        evidence_refs=evidence,
    )
    facts = {"fact:metric:roas": {"value": 2.0, "unit": "ratio"}}
    plan = graphs.compile_graph(
        "PlanGraph",
        {
            "nodes": [
                {
                    "nodeKey": "P1",
                    "kind": "PlanActionNode",
                    "decisionActionRef": "A1",
                    "judgementRefs": ["J1"],
                    "parameters": {
                        "operationPlan": {
                            "operations": [{"operationType": "target_roas_update"}]
                        }
                    },
                    "baseline": {"roas": {"value": 2.0, "unit": "ratio", "sourceRef": "fact:metric:roas"}},
                    "expectedOutcome": {"roas": {"expectedValue": 2.4, "expectedRange": [2.2, 2.6], "expectedDelta": 0.4}},
                    "reviewWindow": {"durationSeconds": 3600},
                    "guard": {"stop": {"metric": "roas", "comparator": "LTE", "value": 1.5}},
                    "riskBoundary": [{"metric": "roas", "comparator": "LTE", "value": 1.2}],
                    "acceptanceCriteria": [{"metric": "roas", "constraint": "expectedRange"}],
                    "affectedMetrics": ["roas"],
                }
            ],
            "edges": [],
        },
        upstream=decision,
        evidence_refs=evidence,
        fact_values=facts,
    )
    operation = graphs.compile_graph(
        "OperationGraph",
        {
            "nodes": [
                {
                    "nodeKey": "O1",
                    "kind": "OperationStage",
                    "planActionRefs": ["P1"],
                    "instruction": "Apply the reviewed ROAS guard plan",
                    "owner": "operator",
                    "executionObject": "ad_plan",
                    "sequence": 0,
                    "rollback": "Restore the previous target when the authorized stop condition is met",
                    "stopConditionRefs": ["P1:stop"],
                    "acceptanceActions": ["verify target and evidence"],
                }
            ],
            "edges": [],
        },
        upstream=plan,
    )
    return {
        "taskId": "TASK-REVIEW-B",
        "storeId": "store-1",
        "productId": "product-1",
        "semanticContractVersion": "26.9.0",
        "DecisionGraph": decision,
        "PlanGraph": plan,
        "OperationGraph": operation,
        "evidenceRefs": ["fact:metric:roas"],
    }


def test_unlabelled_agent1_accuracy_stays_explicitly_missing(isolated_db):
    vector = evaluation.evaluate_execution_result(runtime_source(), {})
    metrics = metric_map(vector)
    assert metrics["agent1.judgement_accuracy"]["value"] is None
    assert metrics["agent1.judgement_accuracy"]["missingReason"] == "NO_VERIFIABLE_LABEL"
    assert vector["compositeScore"] is None
    assert vector["causalAttribution"] is None


def test_delta_realization_handles_negative_and_zero_expected_delta(isolated_db):
    vector = evaluation.evaluate_execution_result(
        runtime_source(),
        {
            "baselineValue": 100,
            "expectedValue": 80,
            "actualValue": 85,
            "metricUnit": "CNY",
            "reviewWindow": {"durationSeconds": 86400},
        },
    )
    metrics = metric_map(vector)
    assert metrics["agent2.expected_delta"]["value"] == -20
    assert metrics["agent2.actual_delta"]["value"] == -15
    assert metrics["agent2.delta_realization_rate"]["value"] == pytest.approx(0.75)

    zero = evaluation.evaluate_execution_result(
        runtime_source(),
        {
            "baselineValue": 100,
            "expectedValue": 100,
            "actualValue": 101,
            "metricUnit": "CNY",
        },
    )
    assert metric_map(zero)["agent2.delta_realization_rate"]["missingReason"] == "EXPECTED_DELTA_NEAR_ZERO"


def test_graph_value_rejects_self_evaluation_source(isolated_db):
    with pytest.raises(evaluation.EvaluationPlaneError, match="self_evaluation_forbidden"):
        evaluation.evaluate_execution_result(
            runtime_source(),
            {
                "validatedValueEvents": 1,
                "evaluatedValueEvents": 1,
                "evaluationSource": "agent1_self",
            },
        )


def test_rollback_rate_is_not_interpreted_as_quality_failure(isolated_db):
    vector = evaluation.evaluate_execution_result(
        runtime_source(),
        {"rollbackEvents": 1, "eligibleExecutions": 2, "executionWindow": "2026-09-13T00:00Z/2026-09-14T00:00Z"},
    )
    rollback = metric_map(vector)["agent3.rollback_rate"]
    assert rollback["value"] == 0.5
    assert any("quality" in text.lower() for text in rollback["interpretationLimits"])


def test_persisted_vector_writes_candidate_evaluation_records_only(isolated_db):
    vector = evaluation.evaluate_execution_result(
        runtime_source(),
        {
            "baselineValue": 100,
            "expectedValue": 120,
            "actualValue": 115,
            "metricUnit": "CNY",
            "presentRequiredEvidence": 3,
            "requiredEvidence": 4,
        },
        persist=True,
    )
    assert len(vector["persistenceReceipts"]) == len(vector["metrics"])
    with repo.connect() as conn:
        statuses = {
            row["lifecycle_status"]
            for row in conn.execute(
                "SELECT lifecycle_status FROM v269b_experience_items WHERE domain='evaluation_results'"
            ).fetchall()
        }
    assert statuses == {"candidate"}


def test_system_review_candidate_persists_strategy_operation_and_evaluation_without_promotion(isolated_db):
    package = compiled_review_package()
    receipt = evaluation.record_system_review_candidate(
        package,
        target_facts={"roas": {"value": 2.35, "unit": "ratio", "sourceRef": "fact:target:roas"}},
        target_source_content_hash="sha256:" + "a" * 64,
        review_receipt={"receiptHash": "sha256:" + "b" * 64},
        review_status="SETTLED",
    )
    assert receipt["strategyExperienceIds"]
    assert receipt["operationExperienceIds"]
    assert receipt["evaluationVectorHashes"]
    assert receipt["evaluationPersistenceReceipts"]
    assert receipt["promotionPerformed"] is False
    assert receipt["knowledgeHeadMutated"] is False
    with repo.connect() as conn:
        domains = {
            row["domain"]
            for row in conn.execute(
                "SELECT domain FROM v269b_experience_items WHERE lifecycle_status='candidate'"
            ).fetchall()
        }
    assert {"strategy_outcomes", "operation_patterns", "evaluation_results"} <= domains


def test_sop_evaluation_evidence_reads_persisted_vector_without_recompute_or_write(isolated_db):
    vector = evaluation.evaluate_execution_result(
        runtime_source(),
        {
            "baselineValue": 100,
            "expectedValue": 120,
            "actualValue": 115,
            "metricUnit": "CNY",
            "reviewWindow": {"durationSeconds": 3600},
            "presentRequiredEvidence": 3,
            "requiredEvidence": 4,
        },
        persist=True,
    )
    with repo.connect() as conn:
        before = conn.execute("SELECT COUNT(*) AS c FROM v269b_experience_items").fetchone()["c"]
    evidence = evaluation.read_task_evaluation_evidence("TASK-EVAL-1")
    with repo.connect() as conn:
        after = conn.execute("SELECT COUNT(*) AS c FROM v269b_experience_items").fetchone()["c"]
    assert before == after
    assert evidence["status"] == "RECORDED"
    assert evidence["recomputedOnRead"] is False
    assert evidence["compositeScore"] is None
    assert evidence["causalAttribution"] is None
    assert evidence["receiptHash"].startswith("sha256:")
    assert len(evidence["items"]) == len(vector["metrics"])
    expected_delta = next(item for item in evidence["items"] if item["metricId"] == "agent2.expected_delta")
    assert expected_delta["formula"]
    assert expected_delta["formulaInputs"]["baselineValue"] == 100
    assert expected_delta["formulaInputs"]["expectedValue"] == 120
    assert expected_delta["observationWindow"]
    assert "interpretationLimits" in expected_delta


def test_system_review_b_evaluation_failure_is_fail_isolated(monkeypatch, isolated_db):
    captured = {}
    monkeypatch.setattr(system_review, "_v269b_runtime_enabled", lambda: True)
    monkeypatch.setattr(
        system_review,
        "_write_v269b_evaluation_status",
        lambda task_id, evidence: captured.update(taskId=task_id, evidence=evidence),
    )
    monkeypatch.setattr(
        evaluation,
        "record_system_review_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("forced-b-failure")),
    )
    result = system_review._record_v269b_evaluation(
        payload={
            "taskId": "TASK-B-FAIL",
            "storeId": "store-1",
            "productId": "product-1",
            "DecisionGraph": {},
            "PlanGraph": {},
            "OperationGraph": {},
            "baselineSourceContentHash": "sha256:" + "1" * 64,
        },
        target={"roas": {"value": 2.1, "unit": "ratio", "sourceRef": "fact:metric:roas"}},
        source_content_hash="sha256:" + "2" * 64,
        receipt={"receiptHash": "sha256:" + "3" * 64},
        review_status="SETTLED",
    )
    assert result["status"] == "FAILED"
    assert "forced-b-failure" in result["reason"]
    assert result["promotionPerformed"] is False
    assert result["knowledgeHeadMutated"] is False
    assert captured["taskId"] == "TASK-B-FAIL"
    assert captured["evidence"]["status"] == "FAILED"
