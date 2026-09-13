"""V26.9.B deterministic field retrieval tests."""
import pytest

from src.repositories import sqlite_repository as repo
from src.services import v269_experience_retrieval_service as retrieval
from src.services import v269_experience_store_service as store


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, "LOG_DIR", tmp_path)
    monkeypatch.setattr(repo, "DB_PATH", tmp_path / "v269b-retrieval.sqlite3")
    monkeypatch.setattr(repo, "_WAL_INITIALIZED", False)
    store.ensure_experience_store()
    return tmp_path


def source(task="TASK-R-1"):
    return {
        "sourceTaskId": task,
        "decisionGraphHash": "sha256:" + "7" * 64,
        "planGraphHash": "sha256:" + "8" * 64,
        "operationGraphHash": "sha256:" + "9" * 64,
        "graphContractVersion": "26.9.0",
        "evaluationVersion": store.VERSION,
        "evidenceRefs": ["artifact:baseline", "artifact:result"],
        "businessScope": {"storeId": "store-1", "productId": "product-1"},
        "sourceType": "runtime",
        "sourceVersion": "execution-result-v1",
    }


def enable(experience_id):
    # B intentionally exposes no promotion API. Tests simulate the future C-owned
    # lifecycle transition directly to prove official retrieval semantics.
    with repo.connect() as conn:
        conn.execute(
            "UPDATE v269b_experience_items SET lifecycle_status='enabled' WHERE experience_id=?",
            (experience_id,),
        )
        conn.commit()


def test_candidate_and_seed_never_enter_official_retrieval(isolated_db):
    candidate = store.record_experience(
        source=source(),
        domain="decision_patterns",
        applicability={"condition": "roas_below_guard"},
        payload={
            "decisionActionKey": "DA1",
            "conditionKey": "roas_below_guard",
            "metric": "roas",
            "direction": "down",
            "category": "traffic",
            "decisionPattern": "Verified low-ROAS pattern",
            "graphValueScore": None,
            "graphValueMetricVersion": None,
            "sampleCount": 4,
        },
    )
    store.import_seed()
    official = retrieval.retrieve_experience("agent1", {"metric": "roas", "direction": "down"})
    assert official["emptyResult"] is True
    assert official["matchCount"] == 0

    inspection = retrieval.retrieve_experience(
        "agent1", {"metric": "roas", "direction": "down"}, mode="inspection"
    )
    assert [item["experienceId"] for item in inspection["results"]] == [candidate["experienceId"]]
    assert inspection["results"][0]["officialEligible"] is False

    with_seed = retrieval.retrieve_experience(
        "agent1", {"metric": "roas", "direction": "down"}, mode="inspection", include_seed=True
    )
    assert any(item["sourceType"] == "seed" for item in with_seed["results"])


def test_future_enabled_decision_pattern_changes_head_and_is_receipt_bound(isolated_db):
    candidate = store.record_experience(
        source=source(),
        domain="decision_patterns",
        applicability={"condition": "roas_below_guard"},
        payload={
            "decisionActionKey": "DA1",
            "conditionKey": "roas_below_guard",
            "metric": "roas",
            "direction": "down",
            "category": "traffic",
            "decisionPattern": "Use the registered guard action candidate.",
            "graphValueScore": 0.75,
            "graphValueMetricVersion": "1.0.0",
            "graphValueSource": "evaluation_plane",
            "sampleCount": 8,
        },
    )
    before = store.knowledge_head(["decision_patterns", "experience_knowledge"])
    enable(candidate["experienceId"])
    result = retrieval.retrieve_experience(
        "agent1", {"condition": "roas_below_guard", "category": "traffic"}
    )
    after = result["knowledgeHead"]
    assert before != after
    assert result["emptyResult"] is False
    assert result["results"][0]["payload"]["graphValueScore"] == 0.75
    assert result["results"][0]["evidenceRefs"] == ["artifact:baseline", "artifact:result"]
    assert result["receiptHash"].startswith("sha256:")


def test_agent2_and_agent3_use_their_registered_field_contracts(isolated_db):
    strategy = store.record_experience(
        source=source("TASK-R-2"),
        domain="strategy_outcomes",
        applicability={"category": "traffic"},
        payload={
            "decisionActionKey": "DA2",
            "planActionKey": "PA2",
            "category": "traffic",
            "strategyType": "roas_guard",
            "baseline": {"value": 2.0, "unit": "ratio"},
            "expected": {"value": 2.4, "unit": "ratio"},
            "actual": {"value": 2.3, "unit": "ratio"},
            "predictionError": 0.1,
            "sampleCount": 5,
        },
    )
    operation = store.record_experience(
        source=source("TASK-R-3"),
        domain="operation_patterns",
        applicability={"platform": "tmall"},
        payload={
            "planActionKey": "PA3",
            "platform": "tmall",
            "executionType": "budget_update",
            "completionStatus": "completed",
            "rollbackOccurred": False,
            "sampleCount": 3,
        },
    )
    enable(strategy["experienceId"])
    enable(operation["experienceId"])

    agent2 = retrieval.retrieve_experience(
        "agent2", {"decisionAction": "DA2", "category": "traffic", "strategyType": "roas_guard"}
    )
    agent3 = retrieval.retrieve_experience(
        "agent3", {"planAction": "PA3", "platform": "tmall", "executionType": "budget_update"}
    )
    assert agent2["results"][0]["experienceId"] == strategy["experienceId"]
    assert agent3["results"][0]["experienceId"] == operation["experienceId"]


def test_no_match_is_explicit_empty_not_seed_fill(isolated_db):
    store.import_seed()
    result = retrieval.retrieve_experience("agent1", {"category": "does-not-exist"})
    assert result["emptyResult"] is True
    assert result["results"] == []
    assert result["matchCount"] == 0
