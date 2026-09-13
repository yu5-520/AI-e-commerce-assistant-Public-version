"""V26.9.C reviewed promotion / withdrawal / Head feedback acceptance tests."""
from __future__ import annotations

import pytest

from src.repositories import sqlite_repository as repo
from src.services import v269_evaluation_plane_service as evaluation
from src.services import v269_experience_retrieval_service as retrieval
from src.services import v269_experience_store_service as store
from src.services import v269_promotion_gate_service as promotion
from tests.test_v22_4_v269_production_admission import graph_case


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, "LOG_DIR", tmp_path)
    monkeypatch.setattr(repo, "DB_PATH", tmp_path / "v269c.sqlite3")
    monkeypatch.setattr(repo, "_WAL_INITIALIZED", False)
    monkeypatch.setenv("V269C_CANDIDATE_RUNTIME", "1")
    promotion.ensure_promotion_tables()
    return tmp_path


def source(task: str = "TASK-C-1", *, source_type: str = "runtime"):
    if source_type == "seed":
        return {
            "sourceTaskId": "seed:" + task,
            "decisionGraphHash": None,
            "planGraphHash": None,
            "operationGraphHash": None,
            "graphContractVersion": "26.9.0",
            "evaluationVersion": store.VERSION,
            "evidenceRefs": ["seed:one", "seed:two"],
            "businessScope": {"storeId": "store-1", "productId": "product-1"},
            "sourceType": "seed",
            "sourceVersion": "synthetic_documentation_seed_not_business_result",
        }
    return {
        "sourceTaskId": task,
        "decisionGraphHash": "sha256:" + "1" * 64,
        "planGraphHash": "sha256:" + "2" * 64,
        "operationGraphHash": "sha256:" + "3" * 64,
        "graphContractVersion": "26.9.0",
        "evaluationVersion": store.VERSION,
        "evidenceRefs": ["artifact:baseline", "artifact:target", "artifact:review"],
        "businessScope": {"storeId": "store-1", "productId": "product-1"},
        "sourceType": "runtime",
        "sourceVersion": "v269.system_review:sha256:" + "4" * 64,
    }


def strategy(task: str = "TASK-C-1", expected: float = 2.4):
    return store.record_experience(
        source=source(task),
        domain="strategy_outcomes",
        applicability={"category": "traffic", "reviewStatus": "SETTLED"},
        payload={
            "decisionActionKey": "DA-1",
            "decisionAction": {"actionType": "guard", "actionFamily": "roas_guard"},
            "planActionKey": "PA-1",
            "planAction": {"actionFamily": "roas_guard"},
            "category": "traffic",
            "strategyType": "roas_guard",
            "baseline": {"roas": {"value": 2.0, "unit": "ratio", "sourceRef": "artifact:baseline"}},
            "expected": {"roas": {"expectedValue": expected, "expectedRange": [2.2, 2.6], "expectedDelta": expected - 2.0}},
            "actual": {"roas": {"value": 2.3, "unit": "ratio", "sourceRef": "artifact:target"}},
            "predictionError": abs(expected - 2.3),
            "sampleCount": 1,
        },
    )


def add_evidence_evaluation(subject_id: str, task: str = "TASK-C-1"):
    return store.record_experience(
        source=source(task),
        domain="evaluation_results",
        applicability={"metricId": "system.evidence_completeness", "metricVersion": "1.0.0"},
        payload={
            "subjectExperienceId": subject_id,
            "evaluationId": "EVAL-" + subject_id,
            "metricId": "system.evidence_completeness",
            "metricVersion": "1.0.0",
            "numerator": 3.0,
            "denominator": 3.0,
            "value": 1.0,
            "unit": "ratio",
            "observationWindow": "task_lifecycle",
            "sampleCount": 3,
            "missingReason": None,
            "formulaInputs": {"presentRequiredEvidence": 3, "requiredEvidence": 3},
            "interpretationLimits": ["evidence truth is not implied by count alone"],
        },
    )


def query_strategy():
    return {
        "decisionAction": {"actionType": "guard", "actionFamily": "roas_guard"},
        "strategyType": "roas_guard",
    }


def test_candidate_never_auto_promotes_and_gate_requires_evaluation(isolated_db):
    candidate = strategy()
    assert candidate["lifecycleStatus"] == "candidate"
    official = retrieval.retrieve_experience("agent2", query_strategy())
    assert official["emptyResult"] is True
    gate = promotion.evaluate_promotion_gate(candidate["experienceId"])
    assert gate["approvedForPromotion"] is False
    assert "EVALUATION_INSUFFICIENT" in gate["failures"]
    with pytest.raises(promotion.PromotionGateError, match="gate_rejected"):
        promotion.review_candidate(
            candidate["experienceId"], reviewer_id="reviewer-1", decision="approve", rationale="not enough evidence"
        )
    assert store.experience_view(experience_id=candidate["experienceId"])["lifecycleStatus"] == "candidate"


def test_review_enable_retrieval_withdraw_history(isolated_db):
    candidate = strategy()
    add_evidence_evaluation(candidate["experienceId"])
    gate = promotion.evaluate_promotion_gate(candidate["experienceId"])
    assert gate["approvedForPromotion"] is True

    reviewed = promotion.review_candidate(
        candidate["experienceId"], reviewer_id="reviewer-1", decision="approve", rationale="verified evidence and applicability"
    )
    assert reviewed["lifecycleStatus"] == "approved"
    assert reviewed["enabled"] is False
    assert retrieval.retrieve_experience("agent2", query_strategy())["emptyResult"] is True

    enabled = promotion.enable_experience(
        candidate["experienceId"], operator_id="operator-1", explicit_operator_intent=True
    )
    assert enabled["lifecycleStatus"] == "enabled"
    assert enabled["knowledgeHeadMutated"] is True
    hit = retrieval.retrieve_experience("agent2", query_strategy())
    assert hit["emptyResult"] is False
    assert hit["results"][0]["experienceId"] == candidate["experienceId"]

    withdrawn = promotion.disable_experience(
        candidate["experienceId"], operator_id="operator-1", reason="business applicability withdrawn", explicit_operator_intent=True
    )
    assert withdrawn["lifecycleStatus"] == "disabled"
    assert withdrawn["historyPreserved"] is True
    assert retrieval.retrieve_experience("agent2", query_strategy())["emptyResult"] is True
    inspection = retrieval.retrieve_experience("agent2", query_strategy(), mode="inspection")
    assert any(item["experienceId"] == candidate["experienceId"] for item in inspection["results"])
    history = promotion.promotion_history(candidate["experienceId"])
    assert [event["to_status"] for event in history["lifecycleEvents"]] == ["approved", "enabled", "disabled"]
    assert len(history["headEvents"]) == 2


def test_supersede_is_explicit_and_atomic(isolated_db):
    first = strategy("TASK-C-OLD", expected=2.4)
    add_evidence_evaluation(first["experienceId"], "TASK-C-OLD")
    promotion.review_candidate(first["experienceId"], reviewer_id="reviewer-1", decision="approve", rationale="first reviewed")
    promotion.enable_experience(first["experienceId"], operator_id="operator-1", explicit_operator_intent=True)

    second = strategy("TASK-C-NEW", expected=2.5)
    add_evidence_evaluation(second["experienceId"], "TASK-C-NEW")
    gate = promotion.evaluate_promotion_gate(second["experienceId"])
    assert "ENABLED_CONFLICT_REQUIRES_SUPERSEDE" in gate["failures"]

    reviewed = promotion.review_candidate(
        second["experienceId"], reviewer_id="reviewer-2", decision="approve", rationale="newer reviewed outcome",
        supersedes_experience_id=first["experienceId"],
    )
    assert reviewed["gate"]["approvedForPromotion"] is True
    switched = promotion.enable_experience(second["experienceId"], operator_id="operator-2", explicit_operator_intent=True)
    assert switched["supersedeLifecycleEventId"]
    assert store.experience_view(experience_id=first["experienceId"])["lifecycleStatus"] == "superseded"
    assert store.experience_view(experience_id=second["experienceId"])["lifecycleStatus"] == "enabled"
    official = retrieval.retrieve_experience("agent2", query_strategy())
    assert [item["experienceId"] for item in official["results"]] == [second["experienceId"]]


def test_seed_and_adjustment_required_are_not_promotable(isolated_db):
    seed = store.record_experience(
        source=source("DOC", source_type="seed"),
        domain="experience_knowledge",
        applicability={"reviewStatus": "SETTLED"},
        payload={"knowledgeType": "documentation", "sampleCount": 1},
    )
    assert promotion.evaluate_promotion_gate(seed["experienceId"])["approvedForPromotion"] is False

    candidate = store.record_experience(
        source=source("TASK-C-ADJ"),
        domain="operation_patterns",
        applicability={"reviewStatus": "ADJUSTMENT_REQUIRED"},
        payload={"planActionKey": "PA-1", "planAction": {"actionFamily": "roas_guard"}, "platform": "tmall", "executionType": "budget_update", "completionStatus": "ADJUSTMENT_REQUIRED", "rollbackOccurred": None, "sampleCount": 1},
    )
    add_evidence_evaluation(candidate["experienceId"], "TASK-C-ADJ")
    gate = promotion.evaluate_promotion_gate(candidate["experienceId"])
    assert gate["approvedForPromotion"] is False
    assert "SOURCE_REVIEW_NOT_SETTLED" in gate["failures"]


def test_system_review_creates_agent1_decision_candidates_and_requires_explicit_enable(isolated_db):
    package, _ = graph_case()
    package["taskId"] = "TASK-C-A1"
    review_hash = "sha256:" + "9" * 64
    target_hash = "sha256:" + "8" * 64
    receipt = evaluation.record_system_review_candidate(
        package,
        target_facts={"roas": {"value": 1.5, "unit": "ratio", "sourceRef": "fact:metric:roas"}},
        target_source_content_hash=target_hash,
        review_receipt={"receiptHash": review_hash},
        review_status="SETTLED",
    )
    assert receipt["decisionExperienceIds"]
    assert receipt["promotionPerformed"] is False
    assert receipt["knowledgeHeadMutated"] is False

    inspection = retrieval.retrieve_experience(
        "agent1", {"metric": "roas", "direction": "up"}, mode="inspection"
    )
    decision_ids = set(receipt["decisionExperienceIds"])
    assert decision_ids <= {item["experienceId"] for item in inspection["results"]}
    assert retrieval.retrieve_experience("agent1", {"metric": "roas", "direction": "up"})["emptyResult"] is True

    candidate_id = receipt["decisionExperienceIds"][0]
    gate = promotion.evaluate_promotion_gate(candidate_id)
    assert gate["approvedForPromotion"] is True
    reviewed = promotion.review_candidate(
        candidate_id,
        reviewer_id="reviewer-a1",
        decision="approve",
        rationale="reviewed graph pattern and later target evidence",
    )
    assert reviewed["lifecycleStatus"] == "approved"
    assert retrieval.retrieve_experience("agent1", {"metric": "roas", "direction": "up"})["emptyResult"] is True

    promotion.enable_experience(
        candidate_id,
        operator_id="operator-a1",
        explicit_operator_intent=True,
    )
    official = retrieval.retrieve_experience("agent1", {"metric": "roas", "direction": "up"})
    assert official["emptyResult"] is False
    assert candidate_id in {item["experienceId"] for item in official["results"]}
