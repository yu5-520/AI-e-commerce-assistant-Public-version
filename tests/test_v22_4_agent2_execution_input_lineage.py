import json
import importlib.util
from pathlib import Path
import sqlite3

import pytest

from src.services import agent2_runtime_v22515_service as runtime
from src.services import agent_token_runtime_v22520_service as token
from src.services import agent2_runtime_resilience_v2143_service as resilience
from src.services import pipeline_item_service as pipeline


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.sqlite3"
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    with connect() as conn:
        conn.execute("CREATE TABLE pipeline_items(item_id TEXT PRIMARY KEY, claim_id TEXT, status TEXT, artifact_refs_json TEXT)")
        conn.execute("INSERT INTO pipeline_items VALUES('PI-1','CLAIM-1','running',?)",
                     (json.dumps({'agent2DraftInputRef': 'ART-CANONICAL'}),))
    monkeypatch.setattr(runtime, "connect", connect)
    return connect


def test_compiled_input_binding_is_persisted_without_replacing_canonical(ledger):
    item = dict(item_id="PI-1", claim_id="CLAIM-1")
    runtime.bind_agent2_execution_input(item, "ART-COMPILED")
    with ledger() as conn:
        refs = json.loads(conn.execute("SELECT artifact_refs_json FROM pipeline_items").fetchone()[0])
    assert refs == {"agent2DraftInputRef": "ART-CANONICAL", "agent2ExecutionInputRef": "ART-COMPILED"}
    assert json.loads(item["artifact_refs_json"]) == refs


def test_stale_claim_cannot_change_execution_binding(ledger):
    with pytest.raises(runtime.Agent2HashProofError, match="claim_changed"):
        runtime.bind_agent2_execution_input(dict(item_id="PI-1", claim_id="STALE"), "ART-OTHER")
    with ledger() as conn:
        refs = json.loads(conn.execute("SELECT artifact_refs_json FROM pipeline_items").fetchone()[0])
    assert "agent2ExecutionInputRef" not in refs


def test_missing_draft_looks_up_exact_compiled_input(monkeypatch):
    seen = []
    def bridge(**kwargs):
        seen.append(kwargs["input_ref"])
        raise runtime.Agent2HashProofError("probe_stop")
    monkeypatch.setattr(runtime, "bridge_agent2_hash_proof", bridge)
    item = {"artifact_refs_json": json.dumps({"agent2DraftInputRef": "ART-CANONICAL",
                                              "agent2ExecutionInputRef": "ART-COMPILED"})}
    with pytest.raises(runtime.Agent2HashProofError, match="probe_stop"):
        runtime._bridge_candidate(item=item, envelope={}, package={"packageId": "PKG"},
                                  runtime_draft=None, provider={})
    assert seen == ["ART-COMPILED"]


def test_returned_input_cannot_override_bound_execution(monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("must reject wrong input before proof resolution")
    monkeypatch.setattr(runtime, "bridge_agent2_hash_proof", forbidden)
    with pytest.raises(runtime.Agent2HashProofError, match="input_ref_mismatch"):
        runtime._bridge_candidate(item={"artifact_refs_json": json.dumps({"agent2ExecutionInputRef": "ART-A"})},
            envelope={}, package={}, runtime_draft={"inputArtifactRef": "ART-B"}, provider={})


def test_prepare_failure_is_attached_to_its_package_and_survives_bridge_error(monkeypatch):
    monkeypatch.setattr(token, "assert_agent_input_envelope", lambda *a, **k: None)
    monkeypatch.setattr(token, "provider_runtime_config", lambda *a: {"provider": "qwen", "model": "test"})
    def entry(envelope, **kwargs):
        raise ValueError("input_binding_missing")
    monkeypatch.setattr(token, "_entry", entry)
    outputs, summary = token.run_agent2_draft_projected_inputs(
        [{"payload": {"packageId": "PKG-1"}}], data_version="DV")
    assert outputs == {}
    assert summary["itemFailures"]["PKG-1"]["providerCallExecuted"] is False
    reason = runtime.agent2_missing_result_reason(summary, "PKG-1", "agent2_hash_accepted_execution_missing")
    assert reason == "agent2_execution_prepare_failed:input_binding_missing"


def test_provider_failure_classification_uses_underlying_reason():
    reason = runtime.agent2_missing_result_reason({"inputCount": 1, "errors": ["HTTP 401 unauthorized"]}, "PKG", "missing")
    classification = resilience.classify_agent2_failure({}, reason)
    assert classification["failureClass"] == "permanent_provider_configuration"
    assert classification["retryEligible"] is False
    assert runtime.agent2_missing_result_reason(
        {"inputCount": 2, "errors": ["other-package:HTTP 401"]}, "PKG", "missing"
    ) == "agent2_hash_proof_bridge_missing:missing"


def test_diagnosis_follows_compiler_parent_edge_read_only(tmp_path):
    path = tmp_path / "diagnostic.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE pipeline_items(item_id TEXT,artifact_refs_json TEXT,payload TEXT)")
        conn.execute("INSERT INTO pipeline_items VALUES('PI-1',?, '{}')",
                     (json.dumps({'agent2DraftInputRef': 'ART-SOURCE'}),))
        conn.execute("CREATE TABLE artifact_edges(parent_artifact_id TEXT,child_artifact_id TEXT)")
        conn.execute("INSERT INTO artifact_edges VALUES('ART-SOURCE','ART-COMPILED')")
        conn.execute("CREATE TABLE artifact_execution_index_v2259(execution_hash TEXT,input_artifact_ref TEXT,input_content_hash TEXT,status TEXT,attempt_count INTEGER,accepted_output_ref TEXT,last_error TEXT,updated_at TEXT)")
        conn.execute("INSERT INTO artifact_execution_index_v2259 VALUES('EXE','ART-COMPILED','hash','failed',3,NULL,'HTTP 401','now')")
    module_path = Path(__file__).resolve().parents[1] / "config/deployment/agent2_lineage_status.py"
    spec = importlib.util.spec_from_file_location("a2_diagnosis", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        result = module.inspect(conn, "PI-1")
        assert len(result["执行记录"]) == 1
        assert result["执行记录"][0]["input_artifact_ref"] == "ART-COMPILED"
        assert result["执行记录"][0]["last_error"] == "HTTP 401"


def test_failure_scheduler_persists_diagnostic_code(tmp_path, monkeypatch):
    path = tmp_path / "failure.sqlite"
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(resilience, "connect", connect)
    monkeypatch.setattr(pipeline, "connect", connect)
    resilience.ensure_agent2_runtime_columns()
    with connect() as conn:
        conn.execute("INSERT INTO pipeline_items(item_id,current_stage,status,retry_count,created_at,updated_at) VALUES('PI-X','agent2_running','running',0,'now','now')")
    result = resilience.schedule_agent2_failure({"item_id": "PI-X"}, {}, {}, "HTTP 401 unauthorized")
    assert result["terminal"] is True
    with connect() as conn:
        row = conn.execute("SELECT last_error_code,failure_code,error_reason FROM pipeline_items WHERE item_id='PI-X'").fetchone()
    assert row["last_error_code"] == row["failure_code"] == "agent2_provider_configuration_error"
    assert row["error_reason"] == "HTTP 401 unauthorized"
