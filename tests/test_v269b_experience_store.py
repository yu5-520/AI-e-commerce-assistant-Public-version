"""V26.9.B normalized Experience Store tests."""
from pathlib import Path

import pytest

from src.repositories import sqlite_repository as repo
from src.services import v269_experience_store_service as store


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repo, "LOG_DIR", tmp_path)
    monkeypatch.setattr(repo, "DB_PATH", tmp_path / "v269b.sqlite3")
    monkeypatch.setattr(repo, "_WAL_INITIALIZED", False)
    store.ensure_experience_store()
    return tmp_path


def runtime_source():
    return {
        "sourceTaskId": "TASK-V269B-1",
        "decisionGraphHash": "sha256:" + "1" * 64,
        "planGraphHash": "sha256:" + "2" * 64,
        "operationGraphHash": "sha256:" + "3" * 64,
        "graphContractVersion": "26.9.0",
        "evaluationVersion": store.VERSION,
        "evidenceRefs": ["artifact:actual", "artifact:baseline"],
        "businessScope": {"storeId": "store-1", "productId": "product-1"},
        "sourceType": "runtime",
        "sourceVersion": "task-evidence-v1",
    }


def decision_payload():
    return {
        "decisionActionKey": "DA1",
        "conditionKey": "roas_below_guard",
        "metric": "roas",
        "direction": "down",
        "category": "traffic",
        "decisionPattern": "ROAS lower than frozen guard with verified evidence.",
        "graphValueScore": None,
        "graphValueMetricVersion": None,
        "sampleCount": 1,
    }


def test_source_is_normalized_once_and_record_is_idempotent(isolated_db):
    first = store.record_experience(
        source=runtime_source(),
        domain="decision_patterns",
        applicability={"condition": "roas_below_guard", "metric": "roas"},
        payload=decision_payload(),
    )
    second = store.record_experience(
        source=runtime_source(),
        domain="decision_patterns",
        applicability={"condition": "roas_below_guard", "metric": "roas"},
        payload=decision_payload(),
    )
    assert first["experienceId"] == second["experienceId"]
    assert first["lifecycleStatus"] == "candidate"
    assert second["idempotentHit"] is True
    with repo.connect() as conn:
        sources = conn.execute("SELECT COUNT(*) AS value FROM v269b_experience_sources").fetchone()["value"]
        items = conn.execute("SELECT COUNT(*) AS value FROM v269b_experience_items").fetchone()["value"]
    assert sources == 1
    assert items == 1


def test_graph_value_cannot_be_agent_self_score(isolated_db):
    payload = decision_payload()
    payload["graphValueScore"] = 0.9
    payload["graphValueMetricVersion"] = "1.0.0"
    with pytest.raises(store.ExperienceStoreError, match="graph_value_self_score_forbidden"):
        store.record_experience(
            source=runtime_source(),
            domain="decision_patterns",
            applicability={},
            payload=payload,
        )
    payload["graphValueSource"] = "evaluation_plane"
    result = store.record_experience(
        source=runtime_source(),
        domain="decision_patterns",
        applicability={},
        payload=payload,
    )
    assert result["payload"]["graphValueScore"] == 0.9


def test_seed_is_explicit_idempotent_and_does_not_change_official_head(isolated_db):
    before = store.knowledge_head(["decision_patterns"])
    first = store.import_seed()
    second = store.import_seed()
    after = store.knowledge_head(["decision_patterns"])
    assert first["officialRetrievalEligible"] is False
    assert second["idempotentHits"] == len(second["experienceIds"])
    assert before == after
    item = store.experience_view(experience_id=first["experienceIds"][0])
    assert item["sourceType"] == "seed"
    assert item["lifecycleStatus"] == "seed"


def test_backup_and_restore_require_explicit_operator_intent(isolated_db, tmp_path):
    store.record_experience(
        source=runtime_source(),
        domain="decision_patterns",
        applicability={},
        payload=decision_payload(),
    )
    backup = store.backup_experience_store(tmp_path / "backup.sqlite3")
    assert Path(backup["path"]).is_file()
    with pytest.raises(store.ExperienceStoreError, match="restore_requires_explicit_operator_intent"):
        store.restore_experience_store(Path(backup["path"]))
    receipt = store.restore_experience_store(Path(backup["path"]), explicit_operator_intent=True)
    assert receipt["explicitOperatorIntent"] is True
