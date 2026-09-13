"""V26.9.B deterministic field retrieval and Agent knowledge-context bridge tests."""
import pytest

from src.repositories import sqlite_repository as repo
from src.services import v269_experience_retrieval_service as retrieval
from src.services import v269_experience_store_service as store
from src.services import v269_input_migration_service as migration
from src.services import v269_semantic_graph_service as graphs


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


def base_context():
    body = {"retrievalPolicyHash": graphs.digest({"policy": "a-empty"}), "records": []}
    return {"headHash": graphs.digest(body), **body}


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


def test_agent2_and_agent3_use_their_registered_field_contracts(isolated_db, monkeypatch):
    strategy = store.record_experience(
        source=source("TASK-R-2"),
        domain="strategy_outcomes",
        applicability={"category": "traffic"},
        payload={
            "decisionActionKey": "DA2",
            "decisionAction": {"actionType": "guard", "actionFamily": "roas_guard"},
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
            "planAction": {"actionFamily": "roas_guard"},
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
        "agent2", {"decisionAction": {"actionType": "guard", "actionFamily": "roas_guard"}, "strategyType": "roas_guard"}
    )
    agent3 = retrieval.retrieve_experience(
        "agent3", {"planAction": {"actionFamily": "roas_guard"}, "executionType": "budget_update"}
    )
    assert agent2["results"][0]["experienceId"] == strategy["experienceId"]
    assert agent3["results"][0]["experienceId"] == operation["experienceId"]

    monkeypatch.setenv("V269B_CANDIDATE_RUNTIME", "1")
    context2 = retrieval.attach_official_experience_context(
        "agent2",
        {"DecisionGraph": {"nodes": [{"kind": "DecisionActionNode", "actionType": "guard", "actionFamily": "roas_guard"}]}},
        base_context(),
    )
    context3 = retrieval.attach_official_experience_context(
        "agent3",
        {"PlanGraph": {"nodes": [{"kind": "PlanActionNode", "actionFamily": "roas_guard", "parameters": {"operationPlan": {"operations": [{"operationType": "budget_update"}]}}}]}},
        base_context(),
    )
    assert any(record.get("experienceId") == strategy["experienceId"] for record in context2["records"])
    assert any(record.get("experienceId") == operation["experienceId"] for record in context3["records"])
    assert any(record.get("schema") == "experience.retrieval.context_receipt.v269b.v1" for record in context2["records"])
    assert any(record.get("schema") == "experience.retrieval.context_receipt.v269b.v1" for record in context3["records"])


def test_agent1_project_input_binds_active_head_and_reprojection_is_idempotent(isolated_db, monkeypatch):
    candidate = store.record_experience(
        source=source("TASK-R-4"),
        domain="decision_patterns",
        applicability={"metric": "roas", "direction": "down"},
        payload={
            "decisionActionKey": "DA4",
            "conditionKey": None,
            "metric": "roas",
            "direction": "down",
            "category": "traffic",
            "decisionPattern": "Historical reviewed ROAS decline pattern.",
            "graphValueScore": None,
            "graphValueMetricVersion": None,
            "sampleCount": 2,
        },
    )
    base = base_context()
    current = {
        "semanticContractVersion": "26.9.0",
        "packageId": "PKG-IDENTITY",
        "productId": "product-1",
        "storeId": "store-1",
        "dataVersion": "dv-1",
        "knowledgeContext": base,
        "BusinessFacts": {
            "fieldSignals": [{"metricCode": "roas", "current": 1.5, "previous": 2.0}],
            "metricSnapshot": {"roas": 1.5},
        },
        "evidenceRefs": ["artifact:fact"],
    }
    descriptor = {
        "provider": "test-provider",
        "model": "test-model",
        "generationParametersHash": graphs.digest({"temperature": 0}),
        "promptVersion": "test-prompt-v1",
    }
    monkeypatch.delenv("V269B_CANDIDATE_RUNTIME", raising=False)
    assert retrieval.runtime_enabled() is True

    candidate_only = migration.project_input(
        "agent1",
        current,
        source_ref="ART-TEST-SOURCE",
        source_content_hash="sha256:" + "a" * 64,
    )["payload"]
    candidate_identity = migration.semantic_identity("agent1", candidate_only, descriptor)["semanticHash"]
    candidate_records = candidate_only["knowledgeContext"]["records"]
    assert candidate_only["knowledgeContext"]["headHash"] != base["headHash"]
    assert any(
        record.get("schema") == "experience.retrieval.context_receipt.v269b.v1"
        and record.get("emptyResult") is True
        for record in candidate_records
    )
    assert not any(record.get("experienceId") == candidate["experienceId"] for record in candidate_records)

    enable(candidate["experienceId"])
    enabled = migration.project_input(
        "agent1",
        current,
        source_ref="ART-TEST-SOURCE",
        source_content_hash="sha256:" + "a" * 64,
    )["payload"]
    enabled_identity = migration.semantic_identity("agent1", enabled, descriptor)["semanticHash"]
    assert any(
        record.get("experienceId") == candidate["experienceId"]
        for record in enabled["knowledgeContext"]["records"]
    )
    assert enabled_identity != candidate_identity

    reprojected = migration.project_input(
        "agent1",
        enabled,
        source_ref="ART-TEST-SOURCE-REPLAY",
        source_content_hash="sha256:" + "b" * 64,
    )["payload"]
    replay_identity = migration.semantic_identity("agent1", reprojected, descriptor)["semanticHash"]
    assert reprojected["knowledgeContext"] == enabled["knowledgeContext"]
    assert replay_identity == enabled_identity


def test_no_match_is_explicit_empty_not_seed_fill(isolated_db):
    store.import_seed()
    result = retrieval.retrieve_experience("agent1", {"category": "does-not-exist"})
    assert result["emptyResult"] is True
    assert result["results"] == []
    assert result["matchCount"] == 0
