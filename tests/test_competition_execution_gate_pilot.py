from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "config/deployment/competition_execution_gate.py"
CONFIG_PATH = ROOT / "config/deployment/runtime_verification_pilot_v1.json"


def _load_gate():
    spec = importlib.util.spec_from_file_location("competition_execution_gate", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_repository_gate_is_deterministic_for_same_source_identity():
    gate = _load_gate()
    first_findings = []
    second_findings = []
    first = gate.repository_identity(ROOT, _config(), "TARGET-SHA", first_findings)
    second = gate.repository_identity(ROOT, _config(), "TARGET-SHA", second_findings)

    assert first_findings == []
    assert second_findings == []
    assert first["repositoryGateHash"] == second["repositoryGateHash"]
    assert first["sourceIdentityHash"] == second["sourceIdentityHash"]
    assert first["runtimeCallableAuthority"]["verified"] is True


def test_database_schema_hash_is_separate_from_mutable_state_hash(tmp_path):
    gate = _load_gate()
    db = tmp_path / "pilot.sqlite3"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "CREATE TABLE pipeline_jobs (id INTEGER PRIMARY KEY, status TEXT, updated_at TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    findings = []
    before = gate.database_identity(db, ["pipeline_jobs"], findings)
    assert findings == []
    assert before["quickCheck"] == "ok"

    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO pipeline_jobs(status,updated_at) VALUES (?,?)",
            ("queued", "2026-08-10T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    after = gate.database_identity(db, ["pipeline_jobs"], findings)
    assert before["databaseSchemaHash"] == after["databaseSchemaHash"]
    assert before["databaseStateHash"] != after["databaseStateHash"]


def test_secret_values_are_not_in_environment_identity():
    gate = _load_gate()
    env = {
        "AI_ECOMMERCE_ROOT": "/opt/ai-ecommerce-assistant",
        "QWEN_API_KEY": "secret-value-must-never-enter-report",
    }
    values, presence = gate.env_identity(_config(), env)

    assert "QWEN_API_KEY" not in values
    assert "secret-value-must-never-enter-report" not in json.dumps(values)
    assert presence["QWEN_API_KEY"] is True


def test_runtime_authority_pilot_does_not_import_application_runtime():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "import src" not in source
    assert "from src" not in source
    assert "locate_live" in source
    assert "databaseSchemaHash" in source
    assert "databaseStateHash" in source
    assert "repositoryGateHash" in source


def test_source_identity_contains_gate_and_deployment_controller():
    config = _config()
    paths = set(config["sourceIdentityFiles"])
    assert "config/deployment/competition_execution_gate.py" in paths
    assert "scripts/deploy_release.sh" in paths
    assert "config/deployment/runtime_callable_authority_v1.json" in paths
    assert "contracts/registry/fields.json" not in paths
