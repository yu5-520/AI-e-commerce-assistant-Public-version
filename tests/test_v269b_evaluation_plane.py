"""V26.9.B Evaluation Plane tests."""
import pytest

from src.repositories import sqlite_repository as repo
from src.services import v269_evaluation_plane_service as evaluation
from src.services import v269_experience_store_service as store


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
