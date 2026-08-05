from __future__ import annotations

import pytest

from src.services import agent_input_transport_v2258_service as transport
from src.services import agent_token_runtime_hash_exact_v2259_service as runtime
from src.services import pipeline_item_service as pipeline
from tools.registry_compiler.post_codegen_gate import build_test_plan

# This file intentionally matches every direct/downstream module test pattern compiled
# by the approved V23.1.3 Change Program.


def test_v23_1_3_current_agent1_input_is_reused(monkeypatch) -> None:
    row = {"item_id": "PI-1", "product_id": "P10007", "store_id": "TB-SH-001"}
    monkeypatch.setattr(transport, "artifact_refs_from_row", lambda value: {"signalRef": "ART-SIGNAL", "agent1InputRef": "ART-INPUT"})
    monkeypatch.setattr(transport, "_source_hash", lambda value: "source-hash")
    monkeypatch.setattr(transport, "_policy", lambda value: {})
    monkeypatch.setattr(transport, "content_hash", lambda value: "policy-hash")
    monkeypatch.setattr(transport, "validate_artifact", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(transport, "resolve_artifact", lambda value: {"schema": transport.AGENT1_INPUT_SCHEMA})
    monkeypatch.setattr(transport, "assert_agent_input_envelope", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(transport, "_existing_input", lambda *args, **kwargs: "ART-INPUT")
    plan = transport.inspect_agent1_input_ref(row)
    assert plan["decision"] == "REUSE"
    assert plan["validation"]["ok"] is True
    assert plan["providerCallsExecuted"] == 0
    assert plan["databaseMutated"] is False


def test_v23_1_3_stale_agent1_input_is_rebuilt(monkeypatch) -> None:
    row = {"item_id": "PI-1", "product_id": "P10007", "store_id": "TB-SH-001"}
    monkeypatch.setattr(transport, "artifact_refs_from_row", lambda value: {"signalRef": "ART-SIGNAL", "agent1InputRef": "ART-OLD"})
    monkeypatch.setattr(transport, "_source_hash", lambda value: "source-hash")
    monkeypatch.setattr(transport, "_policy", lambda value: {})
    monkeypatch.setattr(transport, "content_hash", lambda value: "policy-hash")
    monkeypatch.setattr(transport, "validate_artifact", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(transport, "resolve_artifact", lambda value: {"schema": transport.AGENT1_INPUT_SCHEMA})
    monkeypatch.setattr(transport, "assert_agent_input_envelope", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(transport, "_existing_input", lambda *args, **kwargs: None)
    plan = transport.inspect_agent1_input_ref(row)
    assert plan["decision"] == "REBUILD"
    assert "agent1_input_source_policy_or_lineage_mismatch" in plan["validation"]["errors"]


def test_v23_1_3_dry_run_is_selector_bound_and_zero_write(monkeypatch) -> None:
    rows = [
        {"item_id": f"PI-{index}", "data_version": "DV-1", "product_id": "P10007", "store_id": store, "signal_id": f"SIG-{index}", "current_stage": "agent1_failed", "status": "failed", "updated_at": str(index)}
        for index, store in enumerate(("TB-SH-001", "JD-SH-002", "DY-SH-003"), start=1)
    ]
    monkeypatch.setattr(pipeline, "_load_agent1_recovery_rows", lambda data_version, product_id, store_id=None: rows if store_id is None else [row for row in rows if row["store_id"] == store_id])
    monkeypatch.setattr(transport, "inspect_agent1_input_ref", lambda row, policy_context=None: {"signalRef": "ART-SIGNAL", "currentAgent1InputRef": "ART-INPUT", "decision": "REUSE", "validation": {"ok": True}})
    plan = pipeline.run_agent1_recovery("DV-1", "P10007")
    assert plan["targetCount"] == 3
    assert {item["storeId"] for item in plan["items"]} == {"TB-SH-001", "JD-SH-002", "DY-SH-003"}
    assert plan["dryRun"] is True
    assert plan["databaseMutated"] is False
    assert plan["providerCallsExecuted"] == 0
    one = pipeline.run_agent1_recovery("DV-1", "P10007", store_id="TB-SH-001")
    assert one["targetCount"] == 1


def test_v23_1_3_apply_requires_exact_plan_hash(monkeypatch) -> None:
    plan = {"schema": "pipeline.agent1_recovery_plan.v1", "planHash": "sha256:plan", "targetCount": 1}
    monkeypatch.setattr(pipeline, "agent1_recovery_plan", lambda *args, **kwargs: plan)
    with pytest.raises(RuntimeError, match="plan_hash_required_or_stale"):
        pipeline.run_agent1_recovery("DV-1", "P10007", apply=True)
    monkeypatch.setattr(pipeline, "apply_agent1_recovery_plan", lambda value, policy_context=None: {"appliedCount": 1, "planHash": value["planHash"]})
    result = pipeline.run_agent1_recovery("DV-1", "P10007", apply=True, expected_plan_hash="sha256:plan")
    assert result["appliedCount"] == 1


def test_v23_1_3_apply_rechecks_state_and_moves_only_selected_item(monkeypatch) -> None:
    plan = {
        "schema": "pipeline.agent1_recovery_plan.v1",
        "selector": {"dataVersion": "DV-1", "productId": "P10007", "storeId": "TB-SH-001"},
        "planHash": "sha256:plan",
        "items": [{"itemId": "PI-1"}],
    }
    monkeypatch.setattr(pipeline, "agent1_recovery_plan", lambda *args, **kwargs: plan)
    row = {"item_id": "PI-1", "data_version": "DV-1", "product_id": "P10007", "store_id": "TB-SH-001", "current_stage": "agent1_failed", "status": "failed"}
    monkeypatch.setattr(pipeline, "_current_agent1_recovery_row", lambda item_id: row)
    monkeypatch.setattr(transport, "ensure_agent1_input_ref_with_receipt", lambda value, policy_context=None: {"inputAction": "REUSE", "currentAgent1InputRef": "ART-1", "activeAgent1InputRef": "ART-1"})
    moved = []
    monkeypatch.setattr(pipeline, "_set_agent1_retry_pending", lambda value, receipt, plan_hash: moved.append((value["item_id"], receipt["inputAction"], plan_hash)))
    result = pipeline.apply_agent1_recovery_plan(plan)
    assert moved == [("PI-1", "REUSE", "sha256:plan")]
    assert result["items"][0]["stage"] == "agent1_pending"
    assert result["items"][0]["status"] == "retry"


def test_v23_1_3_rejection_artifact_is_structured_and_non_retryable(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(runtime, "store_artifact", lambda **kwargs: captured.update(kwargs) or {"artifactId": "ART-REJECT"})
    attached = []
    monkeypatch.setattr(runtime, "attach_pipeline_artifact_ref", lambda item_id, key, artifact_id, make_current=False: attached.append((item_id, key, artifact_id, make_current)))
    descriptor = {"itemExecutionId": "EXE-1", "pipelineItemId": "PI-1", "inputArtifactRef": "ART-IN", "inputContentHash": "hash", "productId": "P10007", "storeId": "TB-SH-001", "dataVersion": "DV-1"}
    product = {"correlationId": "PI-1", "productId": "P10007", "storeId": "TB-SH-001", "signalId": "SIG-1", "dataVersion": "DV-1"}
    ref = runtime._store_rejection_artifact(descriptor=descriptor, product=product, raw_batch_output_ref="ART-RAW", returned_identity=[{"itemExecutionId": "EXE-1", "inputContentHash": "wrong"}], reasons=["input_content_hash_mismatch"])
    assert ref == "ART-REJECT"
    value = captured["value"]
    assert value["schema"] == "agent1.normalization_rejection.v1"
    assert value["retryAllowed"] is False
    assert value["rawBatchOutputRef"] == "ART-RAW"
    assert value["rejectionReasons"] == ["input_content_hash_mismatch"]
    assert attached == [("PI-1", "agent1RejectionRef", "ART-REJECT", False)]


def test_v23_1_3_rejection_reason_distinguishes_contract_invalid_from_missing() -> None:
    diagnostics = {
        "rawReturnedItemExecutionIds": ["EXE-1"],
        "exactReturnedItemExecutionIds": [],
        "duplicateItemExecutionIds": ["EXE-1"],
        "inputContentHashMismatches": [{"itemExecutionId": "EXE-1"}],
    }
    reasons = runtime._rejection_reasons("EXE-1", diagnostics, "agent1_exact_hash_output_contract_invalid")
    assert "duplicate_item_execution_id" in reasons
    assert "input_content_hash_mismatch" in reasons
    assert "exact_hash_output_contract_invalid" in reasons
    assert "agent1_exact_hash_output_contract_invalid" in reasons


def test_v23_1_3_post_codegen_prefers_changed_targeted_tests(tmp_path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    targeted = tests_dir / "test_v23_1_3_agent1_pipeline_action_pack_agent2_input_agent3_input_frontend_view_task_mapping_task_pool.py"
    historical = tests_dir / "test_agent2_historical.py"
    targeted.write_text("def test_ok(): assert True\n", encoding="utf-8")
    historical.write_text("def test_old(): assert False\n", encoding="utf-8")
    program = {
        "programHash": "sha256:" + "1" * 64,
        "codegenRequests": [
            {
                "requestId": "EDIT-001",
                "moduleId": "agent1_runtime",
                "allowedTestPatterns": ["tests/test_*agent1*.py", "tests/test_v23_1_*.py"],
            }
        ],
        "verificationRequests": [
            {
                "requestId": "VERIFY-001",
                "moduleId": "agent2_runtime",
                "editAllowed": False,
                "allowedTestPatterns": ["tests/test_*agent2*.py", "tests/test_v23_1_*.py"],
            }
        ],
    }
    changed_path = targeted.relative_to(tmp_path).as_posix()
    plan = build_test_plan(program, tmp_path, changed_paths=[changed_path])
    assert plan["tests"] == [changed_path]
    assert all(item["selectionMode"] == "changed_targeted" for item in plan["modules"])
    assert all(item["matchedTests"] == [changed_path] for item in plan["modules"])
